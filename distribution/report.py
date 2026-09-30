"""Portable front end for the existing 0.2.0 streaming XLSX engine."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
import common as native
sys.path.insert(0, str(ROOT / 'report-engine'))
sys.path.insert(0, str(ROOT / 'vendor'))
# streaming.py intentionally imports the report engine's own file checks.
sys.modules.pop('common', None)
import streaming


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('source', nargs='?')
    parser.add_argument('output', nargs='?')
    parser.add_argument('sheet', nargs='?', default='Monitoring')
    args = parser.parse_args()
    native.verify('node')
    import xlsxwriter
    if args.check:
        print(json.dumps({'status': 'ok', 'version': '0.2.0', 'renderer': 'streaming',
            'distribution_version': native.VERSION, 'dependencies': {'xlsxwriter': xlsxwriter.__version__}}))
        return
    if not args.source or not args.output:
        parser.error('Supply the .mon source and a separate .xlsx output')
    source, output = Path(os.path.abspath(args.source)), Path(os.path.abspath(args.output))
    if source == output or output.suffix != '.xlsx':
        raise ValueError('A separate .xlsx output is required')
    if (not args.sheet or len(args.sheet) > 31 or re.search(r"[\[\]:*?/\\\x00-\x1f]", args.sheet)
            or args.sheet.startswith("'") or args.sheet.endswith("'")):
        raise ValueError('Invalid worksheet name')
    native.directory(source.parent)
    native.directory(output.parent)
    lock = output.parent / ('.' + output.name + '.native-report.lock')
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    work = None
    try:
        native.read(lock, private=True)
        if os.fstat(fd).st_ino != lock.lstat().st_ino:
            raise ValueError('Report lock identity changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = native.fingerprint(output) if output.exists() or output.is_symlink() else None
        tempfd, work = tempfile.mkstemp(prefix='.report-', suffix='.xlsx', dir=str(output.parent))
        os.close(tempfd)
        rows, source_sha = streaming.render(source, Path(work), args.sheet)
        current = native.fingerprint(output) if output.exists() or output.is_symlink() else None
        if current != before:
            raise ValueError('Output changed while rendering; existing file retained')
        if before is not None:
            # Batch names can approach NAME_MAX. Keep backup names bounded too.
            backup = native.mkdir(output.parent / '.mon-sensors-report-backups') / (
                native.digest(os.fsencode(output.name))[:16] + '.' + before['sha256'] + '.xlsx')
            if backup.exists():
                if native.fingerprint(backup)['sha256'] != before['sha256']:
                    raise ValueError('Report backup differs')
            else:
                backup_fd, temporary = tempfile.mkstemp(prefix='.backup-', dir=str(backup.parent))
                try:
                    with os.fdopen(backup_fd, 'wb') as stream:
                        if native.fingerprint(output, target=stream) != before:
                            raise ValueError('Report changed during backup')
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, str(backup))
                    native.sync_directory(backup.parent)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        with open(work, 'rb') as stream:
            os.fsync(stream.fileno())
        os.replace(work, str(output))
        parent = os.open(str(output.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        print(json.dumps({'status': 'ok', 'rows': rows, 'output': str(output),
                          'source_sha256': source_sha, 'sha256': native.fingerprint(output)['sha256'],
                          'renderer': 'streaming'}))
    finally:
        os.close(fd)
        if work and os.path.exists(work):
            os.unlink(work)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print('ocrun-report: ' + str(error), file=sys.stderr)
        sys.exit(1)
