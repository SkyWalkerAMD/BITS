"""Explicit start, preserving original initialization/shutdown policy."""
import os
from pathlib import Path
import subprocess
import time
from common import lock, save, regular
import finish
from bits_layout import LAYOUT


def start(app):
    import install_guard
    install_guard.verify(app)
    finish.prerequisites(app)
    finish.require_idle()
    if any(s['stage'] not in finish.TERMINAL for s in finish.cases(app)):
        raise ValueError('Resolve pending batches before starting; use status --human')
    for item in Path('/proc').iterdir():
        if item.name.isdigit():
            try:
                argv = (item / 'cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError, ProcessLookupError):
                continue
            if os.fsencode(str(app / LAYOUT.scheduler)) in argv:
                raise ValueError('Scheduler already exists (PID {})'.format(item.name))
    state = finish.root_state(app)
    with lock(state / 'start.lock'):
        log = state / 'scheduler.log'
        fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            regular(os.fstat(fd), log)
            child = subprocess.Popen(['/usr/bin/flock', '-n', str(state / 'launch.lock'), str(app / LAYOUT.scheduler), 'run'],
                stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, start_new_session=True,
                env={'PATH': str(app) + ':/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                     'HOME': str(app.parent), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
        finally:
            os.close(fd)
        time.sleep(.5)
        if child.poll() is not None:
            raise ValueError('Scheduler did not start; inspect ' + str(log))
        result = {'status': 'started', 'pid': child.pid, 'log': str(log), 'automatic_task_polling': False}
        save(state / '.scheduler-start', result)
        return result
