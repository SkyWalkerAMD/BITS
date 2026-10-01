"""System-wide opt-in enhancement of the original OCRUN controller/node."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import socket
import sys
from types import SimpleNamespace

from . import VERSION, common, tasks
from bits_core.center.wire import Redis


def host(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', value):
        raise ValueError('Invalid server name or IPv4 address')
    return value


def configure(args):
    common.root()
    spec = common.verify(args.role)
    app = os.path.abspath(args.app or ('/home/ocuser/ocrun' if args.role == 'control' else '/root/ocrun'))
    if args.role == 'node' and app != '/root/ocrun':
        raise ValueError('The verified original scheduler requires /root/ocrun')
    value = {'schema': 'ocrun-plugin-config-v1', 'role': args.role, 'app': app,
             'rdb_server': host(args.rdb_server), 'rdb_port': args.rdb_port,
             'source_commit': spec['source_commit']}
    if not 1 <= args.rdb_port <= 65535:
        raise ValueError('Invalid Redis port')
    from bits_core.results.data import Reader
    with Reader(app) as reader:
        value['environment_sha256'] = reader.digest('oc.env')['sha256']
        env = reader.read('oc.env').decode('utf-8')
    # Primary endpoints must agree with the original program, whose failover
    # and shutdown logic remain unchanged. Never execute/source its env here.
    def original(key):
        found = re.findall(r'^' + key + r'=([A-Za-z0-9.-]+)\s*$', env, re.M)
        if len(found) != 1:
            raise ValueError('Cannot safely read {} from original oc.env'.format(key))
        return found[0]
    if original('RDBSVR1') != args.rdb_server or args.rdb_port != 6379:
        raise ValueError('Redis endpoint must match original RDBSVR1 and port 6379')
    if args.role == 'control':
        from bits_core.results.control import check
        result = check(app, args.results_root)
        if not result['original_files_match']:
            common.display(result, True)
            raise ValueError('Original control files differ from the reviewed 215 copy; retained for review')
        account = pwd.getpwnam(args.operator)
        if not args.allow_node:
            raise ValueError('Specify at least one explicitly enabled --allow-node')
        value.update(results_root=os.path.abspath(args.results_root), operator=args.operator,
                     operator_uid=account.pw_uid, allowed_nodes=sorted(set(tasks.name(n) for n in args.allow_node)))
    else:
        common.directory(Path(app))
        if not args.serial:
            raise ValueError('Specify the verified motherboard serial with --serial')
        selected = tasks.name(args.node or socket.gethostname())
        if selected != socket.gethostname():
            raise ValueError('Node name must match the original scheduler hostname')
        log_server = host(args.log_server or original('LOGSVR1'))
        if log_server != original('LOGSVR1'):
            raise ValueError('Log server must match original LOGSVR1')
        value.update(node=selected, serial=tasks.name(args.serial), log_server=log_server,
                     log_dir='/root/log')
    if args.check:
        return dict(value, check=True, tasks_started=False)
    if common.CONFIG.exists():
        old = json.loads(common.read(common.CONFIG).decode())
        if old != value:
            # Reconfiguration is explicit, but never change an active attachment
            # underneath an install/rollback journal or its scheduler.
            if old['role'] == 'node':
                journal = Path(old['app']) / '.ocrun-plugin/attachment.json'
                if journal.exists() and json.loads(common.read(journal, limit=64 * 1024 ** 2).decode())['status'] != 'rolled_back':
                    raise ValueError('Roll back the node attachment before changing its configuration')
            if not args.replace:
                raise ValueError('Configuration exists; review --check then explicitly use --replace')
            backup = common.CONFIG.parent / ('config.' + common.digest(common.read(common.CONFIG))[:16] + '.json')
            if not backup.exists():
                common.write(backup, common.read(common.CONFIG), 0o600)
    if not common.CONFIG.parent.exists():
        common.directory(common.CONFIG.parent.parent)
        common.CONFIG.parent.mkdir(mode=0o755)
    common.write(common.CONFIG, (json.dumps(value, sort_keys=True, indent=2) + '\n').encode(), 0o644)
    return {'status': 'configured', 'role': args.role, 'tasks_started': False, 'config': str(common.CONFIG)}


def control_tasks(cfg, args):
    if os.geteuid() not in (0, cfg['operator_uid']):
        raise ValueError('Use the configured control account: ' + cfg['operator'])
    if args.node not in cfg['allowed_nodes']:
        raise ValueError('Node is not explicitly enabled in this plugin configuration')
    from bits_core.results.data import Reader
    with Reader(cfg['app']) as reader:
        if reader.digest('oc.env')['sha256'] != cfg['environment_sha256']:
            raise ValueError('Original control configuration changed; review configure --check')
    redis = Redis(cfg['rdb_server'], cfg['rdb_port'], username=None)
    if args.operation == 'status':
        return tasks.status(redis, args.node)
    if args.operation == 'add':
        return tasks.submit(redis, args.node, args.id, args.task, args.time, args.check)
    if args.operation == 'archive':
        from bits_core.results.control import inspect
        with Reader(cfg['results_root']) as reader:
            result = inspect(reader, args.receipt, True, None)
            receipt = json.loads(reader.read(args.receipt).decode())
        if not result['verified_here'] or receipt.get('task_id') != args.id or receipt.get('task_time') != args.time:
            raise ValueError('Matching delivered receipt and all artifact hashes are required')
        if not Path(args.receipt).name.startswith(args.node + '_'):
            raise ValueError('Receipt machine differs')
    return tasks.remove(redis, args.node, args.id, args.time, args.operation == 'archive', args.check)


def setup(args):
    """Configure the installed role from bounded data, then attach idempotently."""
    common.root()
    spec = common.verify()
    role = spec['role']
    app = args.app or ('/home/ocuser/ocrun' if role == 'control' else '/root/ocrun')
    from bits_core.results.data import Reader
    with Reader(app) as reader:
        env = reader.read('oc.env').decode('utf-8')
    endpoints = re.findall(r'^RDBSVR1=([A-Za-z0-9.-]+)\s*$', env, re.M)
    if len(endpoints) != 1:
        raise ValueError('Cannot safely read original RDBSVR1; use configure to inspect the endpoint')
    serial = args.serial
    if role == 'node' and not serial:
        # Fixed sysfs device path, no shell, DMI probing, PATH or caller-supplied file.
        serial = common.read(Path('/sys/devices/virtual/dmi/id/board_serial'), limit=1024).decode('ascii').strip()
        if serial.lower() in ('', 'none', 'unknown', 'default string', 'to be filled by o.e.m.', 'not specified', 'system serial number'):
            raise ValueError('Board serial is unavailable; rerun setup --serial VERIFIED_SERIAL')
        tasks.name(serial)
    values = SimpleNamespace(role=role, app=app, rdb_server=endpoints[0], rdb_port=6379,
        log_server=None, node=None, serial=serial, results_root=args.results_root,
        operator=args.operator, allow_node=args.allow_node, replace=args.replace, check=True)
    cfg = configure(values)
    cfg.pop('check'); cfg.pop('tasks_started')
    if role == 'node':
        from . import attach
        attach.check(cfg)
    if args.check:
        return {'status': 'checked', 'role': role, 'configuration': cfg, 'tasks_started': False,
                'next': 'bits-o setup with the same options and without --check'}
    values.check = False
    configure(values)
    if role == 'node':
        attached = attach.apply(cfg, args.resume)
        return dict(attached, role=role, tasks_started=False,
                    next='bits-o preflight; bits-o start explicitly starts the queued batch')
    return {'status': 'ready', 'role': role, 'allowed_nodes': cfg['allowed_nodes'],
            'tasks_started': False, 'next': 'bits-o tasks status --node NODE'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='bits-o ' + VERSION)
    sub = parser.add_subparsers(dest='command')
    quick = sub.add_parser('setup', help='read original endpoints and board serial; configure and attach without starting tasks')
    quick.add_argument('--app')
    quick.add_argument('--serial')
    quick.add_argument('--allow-node', action='append')
    quick.add_argument('--results-root', default='/data/cds/result')
    quick.add_argument('--operator', default='ocuser')
    quick.add_argument('--replace', action='store_true')
    quick.add_argument('--resume', action='store_true')
    quick.add_argument('--check', action='store_true')
    configuration = sub.add_parser('configure', help='explicit role and endpoints; does not start anything')
    configuration.add_argument('--role', choices=('control', 'node'), required=True)
    configuration.add_argument('--app')
    configuration.add_argument('--rdb-server', required=True)
    configuration.add_argument('--rdb-port', type=int, default=6379)
    configuration.add_argument('--log-server')
    configuration.add_argument('--node')
    configuration.add_argument('--serial')
    configuration.add_argument('--results-root', default='/data/cds/result')
    configuration.add_argument('--operator', default='ocuser')
    configuration.add_argument('--allow-node', action='append')
    configuration.add_argument('--replace', action='store_true')
    configuration.add_argument('--check', action='store_true')
    for name in ('check', 'catalog', 'start', 'preflight', 'stop-scheduler', 'status'):
        command = sub.add_parser(name)
        command.add_argument('--json', action='store_true')
        if name == 'status':
            command.add_argument('--case')
    sub.add_parser('unconfigure', help='after rollback, retain a configuration backup and allow package removal')
    maintenance = sub.add_parser('maintenance-stop', help='before first package install: stop an idle, already enhanced old scheduler')
    maintenance.add_argument('--app', default='/root/ocrun')
    task = sub.add_parser('tasks').add_subparsers(dest='operation')
    for op in ('add', 'status', 'cancel', 'archive'):
        command = task.add_parser(op)
        command.add_argument('--node', required=True)
        command.add_argument('--json', action='store_true')
        if op != 'status':
            command.add_argument('--id', required=True)
            command.add_argument('--time', required=op != 'add')
            command.add_argument('--check', action='store_true')
        if op == 'add':
            command.add_argument('--task', action='append', required=True, help='name=seconds; repeat to build a sequence')
        if op == 'archive':
            command.add_argument('--receipt', required=True)
    results = sub.add_parser('results', help='list/show/verify/sample; original result directory')
    results.add_argument('args', nargs=argparse.REMAINDER)
    tools = sub.add_parser('tools', help='list/check/import-spec; uses the selected packaged tools')
    tools.add_argument('args', nargs=argparse.REMAINDER)
    for action in ('attach', 'rollback'):
        command = sub.add_parser(action)
        command.add_argument('--check', action='store_true')
        if action == 'attach':
            command.add_argument('--resume', action='store_true')
    for action in ('stop', 'retry', 'recover', 'close-incomplete'):
        command = sub.add_parser(action)
        command.add_argument('--case', required=True)
        if action == 'recover':
            command.add_argument('--interrupted', action='store_true', required=True)
        if action == 'close-incomplete':
            command.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(); return 2
    if args.command == 'configure':
        common.display(configure(args), True); return 0
    if args.command == 'setup':
        common.display(setup(args), True); return 0
    spec = common.verify()
    if args.command == 'maintenance-stop':
        if spec['role'] != 'node' or args.app != '/root/ocrun':
            raise ValueError('Use the node payload for the verified /root/ocrun installation')
        from . import node
        common.display(node.stop_scheduler({'app': args.app}), True)
        return 0
    if args.command == 'catalog':
        common.display({'tasks': tasks.CATALOG, 'syntax': '--task NAME=SECONDS',
                        'repeated_names': 'same duration required by original protocol',
                        'cpu2017': 'requires the licensed original archive import on each node',
                        'node_checks': 'CPU instructions, executables, memory, space, activation and report dependencies checked before claim'}, args.json)
        return 0
    cfg = common.config()
    if args.command == 'unconfigure':
        common.root()
        if cfg['role'] == 'node':
            from . import node
            node.idle(cfg)
            journal = Path(cfg['app']) / '.ocrun-plugin/attachment.json'
            if journal.exists() and json.loads(common.read(journal, limit=64 * 1024 ** 2).decode())['status'] != 'rolled_back':
                raise ValueError('Run rollback before unconfigure; active components retained')
        data = common.read(common.CONFIG)
        backup = common.CONFIG.parent / ('config.' + common.digest(data)[:16] + '.json')
        if not backup.exists():
            common.write(backup, data)
        common.CONFIG.unlink()
        common.sync_directory(common.CONFIG.parent)
        common.display({'status': 'unconfigured', 'backup': str(backup), 'tasks_or_results_deleted': False}, True)
        return 0
    if args.command == 'check' and cfg['role'] == 'control':
        from bits_core.results.control import check
        value = check(cfg['app'], cfg['results_root'])
        value.update(plugin_version=VERSION, allowed_nodes=cfg['allowed_nodes'],
                     task_database='existing protocol; no server changes')
        common.display(value, args.json)
        return 0 if value['original_files_match'] else 2
    if args.command == 'status' and cfg['role'] == 'control':
        if args.case:
            raise ValueError('Control batches use tasks status --node; --case is a node operation')
        if os.geteuid() not in (0, cfg['operator_uid']):
            raise ValueError('Use the configured control account: ' + cfg['operator'])
        redis = Redis(cfg['rdb_server'], cfg['rdb_port'], username=None)
        rows = [tasks.status(redis, host) for host in cfg['allowed_nodes']]
        if args.json:
            common.display({'role': 'control', 'batches': rows, 'source': 'database_snapshot_not_live_hardware'}, True)
        else:
            for row in rows:
                common.display('{}  批次={}  时间={}  队列状态={}  当前={}  待执行={}'.format(
                    row['host'], row.get('id') or '-', row.get('time') or '-', row.get('status') or 'unknown',
                    row.get('current') or '-', len(row.get('tasks', []))))
            print('数据库快照；不表示节点在线或硬件合格。')
        return 0
    if args.command == 'results':
        if cfg['role'] != 'control':
            raise ValueError('Use status --case on the node; results is a control command')
        from bits_core.results.control import main as reader
        if not args.args or args.args[0] not in ('list', 'show', 'verify', 'sample'):
            raise ValueError('Use results list/show/verify/sample')
        return reader(args.args + ['--results-root', cfg['results_root']])
    if args.command == 'tasks':
        if cfg['role'] != 'control' or not args.operation:
            raise ValueError('Use control role tasks add/status/cancel/archive')
        common.display(control_tasks(cfg, args), args.json); return 0
    if cfg['role'] != 'node':
        raise ValueError('This command is for the node role')
    common.root()
    from . import node, attach
    if args.command == 'attach':
        common.display(attach.check(cfg) if args.check else attach.apply(cfg, args.resume), True)
    elif args.command == 'rollback':
        common.display(attach.rollback(cfg, args.check), True)
    elif args.command == 'tools':
        if not args.args or args.args[0] not in ('list', 'check', 'import-spec'):
            raise ValueError('Use tools list/check/import-spec; attach manages binding')
        common.run(['/usr/bin/bits-o-workloads'] + args.args, timeout=1800, capture=False)
    elif args.command == 'stop-scheduler':
        common.display(node.stop_scheduler(cfg), args.json)
    elif args.command in ('start', 'preflight'):
        result = node.preflight(cfg, capture=True)
        if args.command == 'start':
            value = json.loads(result.decode())
            if value.get('empty'):
                raise ValueError('No current batch; scheduler was not started')
            node.finish(cfg, ['start'])
        else:
            common.display(json.loads(result.decode()), args.json)
    else:
        forwarded = [args.command]
        if args.command == 'status' and not args.json:
            forwarded += ['--human']
        if getattr(args, 'case', None):
            forwarded += ['--case', args.case]
        if args.command == 'recover':
            forwarded += ['--interrupted']
        if args.command == 'close-incomplete':
            forwarded += ['--reason', args.reason]
        node.finish(cfg, forwarded)
    return 0


def entry():
    try:
        return main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        from bits_core.results.control import clean
        print('bits-o: ' + clean(exc), file=sys.stderr)
        return 2
