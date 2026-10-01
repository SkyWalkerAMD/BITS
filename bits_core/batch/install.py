"""Attach an independently removable finalization hook to a known OCRUN node."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

SOURCE = Path(__file__).absolute().parent
PAYLOAD = SOURCE / "payload"
sys.path.insert(0, str(PAYLOAD))
from common import (ENTRY, HELPER, STATE, VERSION, atomic, digest, directory, json_read,
                    lock, read, run, save, snapshot)
from security import trusted_executable

MARKER = ".mon-sensors-finish-install.json"
BLOCK = ('# BEGIN MON-SENSORS-FINISH v1\n'
         'source "${APPPATH}/mon-sensors-finish.d/hook.sh" || exit 1\n'
         '# END MON-SENSORS-FINISH v1\n\n')


def busy(app):
    paths = {str(app / name) for name in ("ocb", "oct", "mon-sensors", "mon-sensors-plugin")}
    paths.add(str(app / "mon-sensors-plugin.d/mon-sensors-plugin"))
    paths.update(str(app / n) for n in ('.bits-collector', '.bits-collector.d/mon-sensors-plugin'))
    for item in Path("/proc").iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            argv = (item / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        if any(os.fsdecode(arg) in paths for arg in argv):
            raise ValueError("Stop this node's scheduler and monitoring before installation (PID {})".format(item.name))


def checked_package():
    spec = json_read(PAYLOAD / "MANIFEST.json")
    if spec.get("version") != VERSION:
        raise ValueError("Package version mismatch")
    expected = {"common.py", "security.py", "finish.py", "hook.sh", "oct-hook.sh",
                "node.py", "queue.py", "workload.py", "operator_cli.py", "install_guard.py", "tools_adoption.py", "suite.py", "report_sheet.py"}
    if set(spec.get("files", {})) != expected:
        raise ValueError("Invalid package file inventory")
    for name, checksum in spec["files"].items():
        if digest(read(PAYLOAD / name)) != checksum:
            raise ValueError("Package checksum mismatch: " + name)
    return spec


def launch(app):
    interpreter = os.path.realpath(sys.executable)
    with trusted_executable(interpreter):
        pass
    return ("#!/bin/sh\n# Managed mon-sensors-finish launcher v1.\nexec " +
            shlex.quote(interpreter) + " -I -S -B " + shlex.quote(str(app / HELPER / "finish.py")) +
            " --app " + shlex.quote(str(app)) + ' "$@"\n').encode("utf-8")


def patch(data, spec):
    if digest(data) not in spec["compatible_ocb_sha256"]:
        raise ValueError("This OCRUN scheduler is not the verified 0.9.24a plugin bridge")
    text = data.decode("utf-8")
    target = "# 主程序部分\n"
    call = "    run_task\n"
    if text.count(target) != 1 or text.count(call) != 1:
        raise ValueError("Ambiguous scheduler hook location")
    text = text.replace(target, BLOCK + target).replace(call, "    run_task || return $?\n")
    text = text.replace('    app_check\n', '    app_check || return $?\n')
    text = text.replace('    oct push\n', '    "${APPPATH}/oct" push || return $?\n')
    text = text.replace('    ${APPPATH}/oct push\n', '    "${APPPATH}/oct" push || return $?\n')
    return text.encode('utf-8')


def patch_oct(data, spec):
    if digest(data) not in spec['compatible_oct_sha256']:
        raise ValueError('Unrecognized oct bridge; refusing replacement')
    block = 'source "${APPPATH}/mon-sensors-finish.d/oct-hook.sh" || exit 1\n\n'
    text = data.decode('utf-8')
    if text.count('case ${OPS} in\n') != 1:
        raise ValueError('Ambiguous oct dispatcher')
    return text.replace('case ${OPS} in\n', block + 'case ${OPS} in\n').encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument('--rollback', action='store_true', help='Restore the previous managed release')
    args = parser.parse_args()
    if args.detach and args.rollback:
        parser.error('--detach and --rollback are mutually exclusive')
    if (args.detach or args.rollback) and Path('/etc/ocrun-node/connection.json').exists():
        raise ValueError('Roll back the authenticated node connection before detaching or downgrading the finalizer')
    if os.geteuid() != 0 or sys.platform != "linux" or sys.version_info < (3, 6):
        parser.error("Root and Linux Python 3.6+ are required")
    app = directory(Path(args.app))
    if (args.detach or args.rollback) and (app / '.mon-sensors-workload-suite.json').exists():
        raise ValueError('Unbind the selected workload suite before finalizer rollback/detach')
    if app == SOURCE or app in SOURCE.parents or SOURCE in app.parents:
        raise ValueError("Package and application directories must be separate")
    spec = checked_package()
    busy(app)
    before = read(app / "ocb")
    mode = (app / "ocb").stat().st_mode & 0o777
    marker = app / MARKER
    installed = marker.exists() or marker.is_symlink()
    saved = None
    if installed:
        saved = json_read(marker)
        if saved.get("version") not in ('0.1.0', '0.2.0', '0.2.1', '0.2.2', '0.2.3', '0.2.4', '0.2.5', '0.2.6', VERSION) or saved.get("app") != str(app):
            raise ValueError("Unrecognized finalization installation")
        if digest(before) != saved["ocb_after"] or digest(read(app / ENTRY)) != saved["launcher_sha256"]:
            raise ValueError("Managed scheduler or launcher has been modified")
        installed_files = (spec['files'] if saved['version'] == VERSION else
                           spec['previous_releases'][saved['version']])
        for name, checksum in installed_files.items():
            if digest(read(app / HELPER / name)) != checksum:
                raise ValueError("Managed finalizer differs from this package")
        original = read(Path(saved["backup"]) / "ocb")
        if digest(original) != saved["ocb_before"]:
            raise ValueError("Original scheduler backup checksum mismatch")
        after = patch(original, spec)
    else:
        if any(p.exists() or p.is_symlink() for p in (app / HELPER, app / ENTRY)):
            raise ValueError("Unmanaged finalizer paths already exist")
        original = before
        after = patch(before, spec)
    before_oct = read(app / 'oct')
    original_oct = read(Path(saved['backup']) / 'oct') if saved and 'oct_after' in saved else before_oct
    if saved and 'oct_after' in saved and digest(before_oct) != saved['oct_after']:
        raise ValueError('Managed oct has been modified')
    after_oct = patch_oct(original_oct, spec)
    module_spec = importlib.util.spec_from_file_location("finish_preflight", str(PAYLOAD / "finish.py"))
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    module.require_idle()
    if not args.detach and not args.rollback:
        module.prerequisites(app, allow_legacy_report=args.check)
    pending = [s["case"] for s in module.cases(app) if s["stage"] not in module.TERMINAL]
    if pending:
        raise ValueError("Unfinished batches must be resolved before installation/detach")
    if args.rollback:
        if not saved or not saved.get('rollback'):
            raise ValueError('No previous managed release; use --detach for a first installation')
        backup = directory(saved['rollback'])
        inventory = json_read(backup / 'inventory.json')
        for name, checksum in inventory.items():
            if '..' in Path(name).parts or Path(name).is_absolute() or digest(read(backup / name)) != checksum:
                raise ValueError('Rollback backup changed')
    result = {"version": VERSION, "action": "detach" if args.detach else "install",
              "check": args.check, "app": str(app), "already_installed": installed,
              'upgrade': bool(saved and saved['version'] != VERSION), 'rollback': args.rollback,
              "automatic_task_polling": False, "management_server_changes": False}
    if args.check:
        print(json.dumps(result, sort_keys=True))
        return
    with lock(app / ".mon-sensors-install.lock"):
        busy(app)
        if read(app / "ocb") != before:
            raise ValueError("Scheduler changed during installation")
        if args.rollback:
            if not saved or not saved.get('rollback'):
                raise ValueError('No previous managed release; use --detach for a first installation')
            backup = directory(saved['rollback'])
            inventory = json_read(backup / 'inventory.json')
            for name, checksum in inventory.items():
                if '..' in Path(name).parts or Path(name).is_absolute() or digest(read(backup / name)) != checksum:
                    raise ValueError('Rollback backup changed')
            for name in inventory:
                atomic(app / name, read(backup / name))
                if name in ('ocb', 'oct', ENTRY):
                    (app / name).chmod(0o755)
            for name in spec['files']:
                if HELPER + '/' + name not in inventory:
                    target = app / HELPER / name
                    if digest(read(target)) != spec['files'][name]:
                        raise ValueError('New helper changed before rollback: ' + name)
                    target.unlink()
            print(json.dumps(result, sort_keys=True))
            return
        if args.detach:
            if installed:
                atomic(app / "ocb", original)
                (app / "ocb").chmod(saved["ocb_mode"])
                atomic(app / 'oct', original_oct)
                (app / 'oct').chmod(0o755)
                # Keep helper, launcher, histories and backups for status/retry/audit.
                saved["detached"] = True
                saved["ocb_after"] = digest(original)
                saved['oct_after'] = digest(original_oct)
                save(marker, saved)
            print(json.dumps(result, sort_keys=True))
            return
        if installed and saved['version'] == VERSION:
            if saved.get("detached"):
                after = patch(before, spec)
                atomic(app / "ocb", after)
                (app / "ocb").chmod(saved["ocb_mode"])
                atomic(app / 'oct', after_oct)
                (app / 'oct').chmod(0o755)
                saved["detached"] = False
                saved["ocb_after"] = digest(after)
                saved['oct_after'] = digest(after_oct)
                save(marker, saved)
            print(json.dumps(result, sort_keys=True))
            return
        backup_root = app / ".mon-sensors-finish-backups"
        backup_root.mkdir(mode=0o700, exist_ok=True)
        directory(backup_root)
        backup = Path(tempfile.mkdtemp(prefix="before-", dir=str(backup_root)))
        atomic(backup / "ocb", original, replace=False)
        atomic(backup / 'oct', original_oct, replace=False)
        rollback = None
        if installed:
            rollback = backup / 'previous'
            rollback.mkdir(mode=0o700)
            names = ['ocb', 'oct', ENTRY, MARKER] + [HELPER + '/' + n for n in installed_files]
            inventory = {}
            for name in names:
                (rollback / name).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                data = read(app / name)
                atomic(rollback / name, data, replace=False)
                inventory[name] = digest(data)
            save(rollback / 'inventory.json', inventory)
        stage = Path(tempfile.mkdtemp(prefix=".finish-install-", dir=str(app)))
        state_root = app / STATE
        activated = False
        try:
            for name in spec["files"]:
                atomic(stage / name, read(PAYLOAD / name), replace=False)
            if installed:
                os.replace(str(app / HELPER), str(backup / 'previous-helper'))
            os.replace(str(stage), str(app / HELPER))
            activated = True
            atomic(app / ENTRY, launch(app), replace=installed)
            (app / ENTRY).chmod(0o755)
            state_root.mkdir(mode=0o700, exist_ok=True)
            directory(state_root)
            with lock(state_root / "scheduler.lock"):
                pass
            saved = {"version": VERSION, "app": str(app), "backup": str(backup), "ocb_mode": mode,
                     "ocb_before": digest(original), "ocb_after": digest(after),
                     'oct_after': digest(after_oct), 'rollback': str(rollback) if rollback else None,
                     "launcher_sha256": digest(launch(app)), "detached": False,
                     'managed_files': dict({HELPER + '/' + n: h for n, h in spec['files'].items()},
                                          ocb=digest(after), oct=digest(after_oct))}
            save(marker, saved)
            atomic(app / "ocb", after)
            (app / "ocb").chmod(mode)
            atomic(app / 'oct', after_oct)
            (app / 'oct').chmod(0o755)
        except BaseException:
            atomic(app / "ocb", before)
            (app / "ocb").chmod(mode)
            atomic(app / 'oct', before_oct)
            (app / 'oct').chmod(0o755)
            if activated:
                shutil.rmtree(str(app / HELPER))
            if installed:
                if (backup / 'previous-helper').exists():
                    os.replace(str(backup / 'previous-helper'), str(app / HELPER))
                for name in (ENTRY, MARKER):
                    atomic(app / name, read(rollback / name))
                (app / ENTRY).chmod(0o755)
            else:
                for path in (app / ENTRY, marker):
                    if path.exists():
                        path.unlink()
            raise
        finally:
            if stage.exists():
                shutil.rmtree(str(stage))
        result["backup"] = str(backup)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("mon-sensors-finish install: " + str(error), file=sys.stderr)
        sys.exit(1)
