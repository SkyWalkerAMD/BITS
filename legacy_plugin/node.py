"""Unified operations, leaving original task and shutdown policy in ocb."""
import json
import os
from pathlib import Path
import signal
import time

from . import common


def finish(cfg, args, capture=False):
    app = Path(cfg['app'])
    common.read(app / 'mon-sensors-finish')
    return common.run([str(app / 'mon-sensors-finish')] + args, timeout=7200, capture=capture)


def preflight(cfg, capture=False):
    # The old scheduler sources this file. Detect a configuration change since
    # setup instead of silently preflighting against different servers.
    from control_addon.data import Reader
    with Reader(cfg['app']) as reader:
        if reader.digest('oc.env')['sha256'] != cfg['environment_sha256']:
            raise ValueError('oc.env changed since configuration; review it and configure again')
    args = ['preflight']
    for flag in ('rdb_server', 'rdb_port', 'log_server', 'log_dir', 'node', 'serial'):
        args += ['--' + flag.replace('_', '-'), str(cfg[flag])]
    return finish(cfg, args, capture)


def identity(pid):
    try:
        text = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
        return {'pid': int(pid), 'ticks': text[19], 'state': text[0],
                'ppid': int(text[1]), 'pgrp': int(text[2]), 'session': int(text[3]),
                'argv': Path('/proc/{}/cmdline'.format(pid)).read_bytes().split(b'\0')}
    except (FileNotFoundError, ProcessLookupError):
        return None


def idle(cfg):
    app = Path(cfg['app'])
    paths = {os.fsencode(str(app / x)) for x in ('ocb', 'oct', 'mon-sensors', 'mon-sensors-plugin',
             'mon-sensors-plugin.d/mon-sensors-plugin', 'mon-sensors-finish.d/finish.py')}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        data = identity(item.name)
        if data and data['state'] != 'Z' and paths.intersection(data['argv']):
            raise ValueError('Node still active, PID {}. Inspect status; stop the batch or use stop-scheduler when idle.'.format(item.name))


def stop_scheduler(cfg):
    """Stop only the matching old scheduler AFTER its batch has finished."""
    import fcntl
    common.root()
    app = Path(cfg['app'])
    finish(cfg, ['check', '--scheduler'], capture=True)
    locks = []
    try:
        start_lock = app / '.mon-sensors-finish/start.lock'
        paths = [app / '.mon-sensors-runtime.lock', app / '.mon-sensors-finish/scheduler.lock']
        if start_lock.exists():
            paths.insert(0, start_lock)
        for lock in paths:
            common.read(lock, private=True)
            fd = os.open(str(lock), os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            locks.append(fd)
            if (os.fstat(fd).st_ino, os.fstat(fd).st_dev) != (lock.lstat().st_ino, lock.lstat().st_dev):
                raise ValueError('Lock identity changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        found = []
        for item in Path('/proc').iterdir():
            if item.name.isdigit():
                data = identity(item.name)
                if data and os.fsencode(str(app / 'ocb')) in data['argv'] and data['state'] != 'Z':
                    # flock launcher and its child may both contain the path.
                    # Only the interpreted ocb process is signalled, never a name match.
                    if data['argv'][0] in (b'/usr/bin/flock', b'flock'):
                        continue
                    found.append(data)
        if len(found) > 1:
            raise ValueError('Multiple original schedulers found; retained for inspection')
        for old in found:
            # The old auto-shutdown shell sleeps between checks. That child can
            # inherit the launch lock; stopping only its parent leaves a stale
            # lock for five minutes. Pin this scheduler's descendants BEFORE
            # signalling, never signal all sleep processes or a shared session.
            processes = []
            for item in Path('/proc').iterdir():
                if item.name.isdigit():
                    child = identity(item.name)
                    if child:
                        processes.append(child)
            descendants, owned = [], {old['pid']}
            changed = True
            while changed:
                changed = False
                for child in processes:
                    if child['pid'] not in owned and child['ppid'] in owned:
                        if child['session'] != old['session']:
                            raise ValueError('Detached scheduler child retained for inspection')
                        descendants.append(child); owned.add(child['pid']); changed = True
            current = identity(old['pid'])
            if not current or current['ticks'] != old['ticks'] or current['argv'] != old['argv']:
                raise ValueError('Scheduler identity changed')
            os.kill(old['pid'], signal.SIGTERM)
            for child in reversed(descendants):
                current = identity(child['pid'])
                if current and current['ticks'] == child['ticks'] and current['argv'] == child['argv'] and current['session'] == child['session']:
                    try:
                        os.kill(child['pid'], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            for unused in range(100):
                current = identity(old['pid'])
                alive = []
                for child in descendants:
                    seen = identity(child['pid'])
                    if seen and seen['ticks'] == child['ticks'] and seen['state'] != 'Z':
                        alive.append(child['pid'])
                if (not current or current['ticks'] != old['ticks'] or current['state'] == 'Z') and not alive:
                    break
                time.sleep(.1)
            else:
                raise ValueError('Scheduler did not exit; no forced kill used')
        launch = app / '.mon-sensors-finish/launch.lock'
        if launch.exists():
            # util-linux flock creates an empty lock with the caller's umask;
            # older starts can legitimately leave it 0644. It contains no data.
            # Still require root ownership, one link and no group/world writes.
            common.read(launch)
            fd = os.open(str(launch), os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            locks.append(fd)
            if (os.fstat(fd).st_ino, os.fstat(fd).st_dev) != (launch.lstat().st_ino, launch.lstat().st_dev):
                raise ValueError('Launch lock identity changed')
            for unused in range(100):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(.05)
            else:
                raise ValueError('Launch lock is still held; inspect the remaining scheduler, no forced kill used')
        return {'status': 'scheduler_stopped' if found else 'already_stopped',
                'tasks_started': False, 'shutdown_policy_changed': False}
    finally:
        for fd in locks:
            os.close(fd)
