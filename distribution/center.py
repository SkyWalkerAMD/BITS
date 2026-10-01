"""Native management package entry; network configuration stays explicit."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
import common


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('tasks', 'menu', 'node-config'):
        common.verify('center')
        arguments = sys.argv[1:]
        if arguments[0] == 'tasks':
            arguments[0] = 'task'
        return subprocess.run(['/usr/local/bin/ocrun-server'] + arguments, check=True).returncode
    if len(sys.argv) > 1 and sys.argv[1] == 'results':
        common.verify('center', require_root=False)
        from control_addon.control import main as results
        # The default matches the center's published results directory. No
        # service or task mutation is involved; normal file permissions apply.
        args = sys.argv[2:]
        if args and '--results-root' not in args and '--help' not in args:
            args += ['--results-root', '/srv/ocrun/logs']
        raise SystemExit(results(args))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='bits-center ' + common.VERSION)
    sub = parser.add_subparsers(dest='action')
    setup = sub.add_parser('setup')
    setup.add_argument('--address')
    setup.add_argument('--network')
    setup.add_argument('--auto', action='store_true', help='Detect an existing private IPv4 and its subnet; never edit NICs')
    setup.add_argument('--interface', help='Select an existing interface when using --auto')
    choice = setup.add_mutually_exclusive_group(required=True)
    choice.add_argument('--check', action='store_true')
    choice.add_argument('--apply', action='store_true')
    setup.add_argument('--skip-deps', action='store_true')
    network_parser = sub.add_parser('network', help='Read existing network candidates without configuring services')
    network_parser.add_argument('--json', action='store_true')
    sub.add_parser('rollback')
    sub.add_parser('check')
    sub.add_parser('publish-node')
    sub.add_parser('results', help='list/show/verify/sample local batch results (use results --help)')
    sub.add_parser('tasks', help='add/status/delete tasks through the existing protocol')
    sub.add_parser('menu', help='interactive task menu')
    sub.add_parser('node-config', help='export a private connection file for a node')
    args = parser.parse_args()
    common.verify('center')
    if args.action == 'network':
        import network
        value = network.inventory()
        print(json.dumps(value, ensure_ascii=False, sort_keys=True) if args.json else network.display(value))
        return
    installer = ['bash', str(ROOT / 'server/server_deploy/install.sh')]
    if args.action == 'setup':
        if args.auto:
            import network
            options = (args.interface, args.address, args.network)
            selected = network.plan(*options)
            print(network.display(selected), file=sys.stderr, flush=True)
            network.unchanged(selected, *options)
            args.address, args.network = selected['address'], selected['network']
        elif args.interface or not args.address or not args.network:
            parser.error('Use --auto [--interface NAME], or supply both --address IP and --network CIDR')
        command = installer + ['--address', args.address, '--network', args.network,
                               '--check' if args.check else '--apply']
        if args.skip_deps:
            command.append('--skip-deps')
        subprocess.run(command, check=True)
        if args.apply:
            publish()
    elif args.action == 'publish-node':
        publish()
    elif args.action == 'rollback':
        subprocess.run(installer + ['--rollback'], check=True)
    elif args.action == 'check':
        if Path('/etc/ocrun-server/manifest.json').exists():
            subprocess.run(['/usr/local/bin/ocrun-server', 'check'], check=True)
        else:
            print(json.dumps({'status': 'not_configured', 'version': common.VERSION,
                              'next': 'bits-center setup --address IP --network CIDR --check'}))
    else:
        parser.error('Choose network, setup, check, publish-node or rollback')


def publish():
    # The server's existing publication routine verifies filename, contents and
    # existing releases, then writes public data. Credentials never enter here.
    sys.path.insert(0, str(ROOT / 'server'))
    from server_deploy import safe
    from server_deploy.publish import publish as put
    config = safe.load('/etc/ocrun-server/server.json')
    packages = common.load(ROOT / 'NODE-PACKAGES.json')
    for filename, checksum in sorted(packages.items()):
        if Path(filename).name != filename:
            raise ValueError('Invalid node package name')
        result = put(config, ROOT / 'releases' / filename, checksum)
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('bits-center: ' + str(error), file=sys.stderr)
        sys.exit(1)
