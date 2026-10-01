"""Install a separate mon-sensors-plugin and a small opt-in 0730 bridge."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile

from .collector import _script_argument
from .adoption import OriginalAdoption
from sckocp_api.security import trusted_executable

SCRIPTS = ("mon-sensors", "oct", "ocb", "py/mon-analyse-log.py")
ENTRY = "mon-sensors-plugin"
HELPER = "mon-sensors-plugin.d"
FILES = (ENTRY, "bits_layout.py", "bits_core/__init__.py", "bits_core/layout.py",
         "bits_core/collector/__init__.py", "bits_core/collector/collector.py",
         "bits_core/collector/runtime.py",
         "sckocp_api/__init__.py", "sckocp_api/interface.py", "sckocp_api/provider.py",
         "sckocp_api/security.py")
COMPATIBILITY_FILES = {
    "mon_sensors_plugin/runtime.py": "bits_core/collector/legacy_runtime.py",
}
PREVIOUS_FILES = frozenset(FILES) | frozenset((
    "mon_sensors_plugin/__init__.py", "mon_sensors_plugin/collector.py",
    "mon_sensors_plugin/runtime.py"))
MARKER = ".mon-sensors-plugin"
BACKEND_FILE = ".mon-sensors-backend"
PREVIOUS_BEGIN = "# BEGIN OCRUN SCKOCP MON-SENSORS BRIDGE v1"
PREVIOUS_END = "# END OCRUN SCKOCP MON-SENSORS BRIDGE v1"
PREVIOUS_DISPATCH = r'''# BEGIN OCRUN SCKOCP MON-SENSORS BRIDGE v1
_MON_APP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
_MON_BACKEND=${MON_SENSORS_BACKEND:-auto}
_MON_BINARY=${SCKOCP_BINARY:-$(command -v sckocp || true)}
case "$_MON_BACKEND" in
    auto|sckocp|legacy) ;;
    *) echo 'mon-sensors: invalid MON_SENSORS_BACKEND (auto/sckocp/legacy)' >&2; exit 2 ;;
esac
if [[ $_MON_BACKEND == sckocp || ( $_MON_BACKEND == auto && -n $_MON_BINARY ) ]]; then
    if [[ -n $_MON_BINARY ]]; then
        exec python3 "$_MON_APP/sckocp-collector/mon-sensors" "$@" --binary "$_MON_BINARY"
    fi
    exec python3 "$_MON_APP/sckocp-collector/mon-sensors" "$@"
fi
# END OCRUN SCKOCP MON-SENSORS BRIDGE v1
'''
V2_BEGIN = "# BEGIN OCRUN SCKOCP MON-SENSORS BRIDGE v2"
V2_END = "# END OCRUN SCKOCP MON-SENSORS BRIDGE v2"
V2_DISPATCH = r'''# BEGIN OCRUN SCKOCP MON-SENSORS BRIDGE v2
_MON_BACKEND=${MON_SENSORS_BACKEND:-legacy}
case "$_MON_BACKEND" in
    auto|sckocp|legacy) ;;
    *) echo 'mon-sensors: invalid MON_SENSORS_BACKEND (legacy/sckocp/auto)' >&2; exit 2 ;;
esac
if [[ $_MON_BACKEND != legacy ]]; then
    _MON_APP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
    _MON_BINARY=${SCKOCP_BINARY:-$(command -v sckocp || true)}
    if [[ $_MON_BACKEND == sckocp || -n $_MON_BINARY ]]; then
        if [[ -n $_MON_BINARY ]]; then
            exec python3 "$_MON_APP/sckocp-collector/mon-sensors" "$@" --binary "$_MON_BINARY"
        fi
        exec python3 "$_MON_APP/sckocp-collector/mon-sensors" "$@"
    fi
fi
# END OCRUN SCKOCP MON-SENSORS BRIDGE v2
'''
V3_BEGIN = "# BEGIN OCRUN SCKOCP MON-SENSORS BRIDGE v3"
V3_END = "# END OCRUN SCKOCP MON-SENSORS BRIDGE v3"
V3_DISPATCH = r'''# BEGIN OCRUN SCKOCP MON-SENSORS BRIDGE v3
_MON_APP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${MON_SENSORS_BACKEND+x} ]]; then
    _MON_BACKEND=${MON_SENSORS_BACKEND:-legacy}
elif [[ -e $_MON_APP/.mon-sensors-backend || -L $_MON_APP/.mon-sensors-backend ]]; then
    if [[ ! -f $_MON_APP/.mon-sensors-backend || -L $_MON_APP/.mon-sensors-backend ]]; then
        echo 'mon-sensors: unsafe device backend configuration' >&2; exit 2
    fi
    IFS= read -r _MON_BACKEND < "$_MON_APP/.mon-sensors-backend" || {
        echo 'mon-sensors: cannot read device backend configuration' >&2; exit 2;
    }
else
    _MON_BACKEND=legacy
fi
case "$_MON_BACKEND" in
    auto|sckocp|legacy) ;;
    *) echo 'mon-sensors: invalid backend (legacy/sckocp/auto)' >&2; exit 2 ;;
esac
if [[ $_MON_BACKEND != legacy ]]; then
    _MON_BINARY=${SCKOCP_BINARY:-$(command -v sckocp || true)}
    if [[ $_MON_BACKEND == sckocp || -n $_MON_BINARY ]]; then
        if [[ -n $_MON_BINARY ]]; then
            exec python3 "$_MON_APP/sckocp-collector/mon-sensors" "$@" --binary "$_MON_BINARY"
        fi
        exec python3 "$_MON_APP/sckocp-collector/mon-sensors" "$@"
    fi
fi
# END OCRUN SCKOCP MON-SENSORS BRIDGE v3
'''
V4_BEGIN = "# BEGIN MON-SENSORS-PLUGIN BRIDGE v1"
V4_END = "# END MON-SENSORS-PLUGIN BRIDGE v1"
V4_DISPATCH = (V3_DISPATCH.replace(V3_BEGIN, V4_BEGIN).replace(V3_END, V4_END)
            .replace("$_MON_APP/sckocp-collector/mon-sensors", "$_MON_APP/mon-sensors-plugin"))
BEGIN = "# BEGIN MON-SENSORS-PLUGIN BRIDGE v2"
END = "# END MON-SENSORS-PLUGIN BRIDGE v2"
GUARD = r'''# Guard background file monitoring before either backend starts.
if [[ ${2:-view} != view && ${2:-} != --* && " $* " != *" --once "* ]]; then
    "$_MON_APP/mon-sensors-plugin" --guard-check "$_MON_APP"
    _MON_GUARD_RC=$?
    if [[ $_MON_GUARD_RC == 3 ]]; then
        exec "$_MON_APP/mon-sensors-plugin" --guard-run "$_MON_APP" -- /bin/bash "$0" "$@"
    elif [[ $_MON_GUARD_RC != 0 ]]; then
        exit "$_MON_GUARD_RC"
    fi
fi
'''
DISPATCH = (V4_DISPATCH.replace(V4_BEGIN, BEGIN).replace(V4_END, END)
            .replace('exec python3 "$_MON_APP/mon-sensors-plugin"', 'exec "$_MON_APP/mon-sensors-plugin"')
            .replace('if [[ ${MON_SENSORS_BACKEND+x} ]]; then',
                     GUARD + 'if [[ ${MON_SENSORS_BACKEND+x} ]]; then'))


def _launcher(app, helper=HELPER):
    interpreter = os.path.realpath(sys.executable)
    with trusted_executable(interpreter):
        pass
    return ('#!/bin/sh\n# Managed mon-sensors-plugin launcher.\nexec ' +
            shlex.quote(interpreter) + ' -I -S -B ' + shlex.quote(str(app / helper / ENTRY)) +
            ' "$@"\n').encode('utf-8')


def _regular(path):
    _trusted_directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _safe_file(os.fstat(fd), path)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("Installation file is too large: " + str(path))
        return data
    finally:
        os.close(fd)


def _safe_file(details, path):
    if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or
            details.st_uid not in (0, os.geteuid()) or details.st_mode & 0o7022):
        raise ValueError("Unsafe installation file (owner, permissions or links): " + str(path))


def _trusted_directory(path):
    """Only trusted owners may replace installation paths; do not follow aliases."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Installation paths must be absolute without '..'")
    for item in list(reversed(path.parents)) + [path]:
        details = item.lstat()
        sticky_root = details.st_uid == 0 and details.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(details.st_mode) or details.st_uid not in (0, os.geteuid()) or
                (details.st_mode & 0o022 and not sticky_root)):
            raise ValueError("Unsafe installation directory (owner, permissions or link): " + str(item))
    return path


def _paths(app, source):
    app, source = Path(app), Path(source)
    if not app.is_absolute() or app == Path("/"):
        raise ValueError("--app must be an existing absolute application directory")
    _trusted_directory(app)
    _trusted_directory(source)
    if not os.access(str(app), os.W_OK | os.X_OK):
        raise PermissionError("Application directory is not writable by this user: " + str(app))
    if app == source or app in source.parents or source in app.parents:
        raise ValueError("Source package and OCRUN application must be separate directories")
    return app, source


def discover_app(candidates=None):
    """Inspect known locations and command links without invoking OCRUN or oc.env."""
    if candidates is None:
        candidates = [Path.home() / "ocrun", Path("/root/ocrun"), Path("/opt/ocrun"),
                      Path("/usr/local/ocrun")]
        for name in ("oct", "ocb", "mon-sensors", "mon-sensors-plugin"):
            command = shutil.which(name)
            if command and os.path.isabs(command):
                try:
                    candidates.append(Path(command).resolve().parent)
                except (OSError, RuntimeError):
                    continue  # Broken command links are not application candidates.
    compatible = []
    visited = set()
    for candidate in candidates:
        candidate = Path(candidate)
        key = str(candidate)
        if key in visited:
            continue
        visited.add(key)
        try:
            _trusted_directory(candidate)
            patched({name: _regular(candidate / name) for name in SCRIPTS})
        except (OSError, ValueError):
            continue
        compatible.append(candidate)
    if not compatible:
        raise ValueError("No compatible 0730 device installation found; specify --app /absolute/ocrun")
    if len(compatible) > 1:
        raise ValueError("Multiple compatible OCRUN installations found; specify --app: " +
                         ", ".join(str(path) for path in compatible))
    return compatible[0]


def active_monitors(app, proc_root="/proc"):
    """Find collectors for this device without signalling or probing hardware."""
    targets = {str(app / name) for name in
               ("mon-sensors", "mon_sensors", ENTRY, HELPER + "/" + ENTRY,
                "sckocp-collector/mon-sensors", ".bits-collector", ".bits-collector.d/mon-sensors-plugin")}
    active = []
    with os.scandir(proc_root) as entries:
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                with open(os.path.join(entry.path, "cmdline"), "rb") as stream:
                    command = stream.read(65537)
                if not command or len(command) > 65536 or not command.endswith(b"\0"):
                    continue
                argv = [os.fsdecode(value) for value in command.split(b"\0")[:-1]]
                if "--stop-app" in argv:
                    continue
                script = _script_argument(argv)
                if script is None:
                    continue
                if not os.path.isabs(script):
                    script = os.path.join(os.readlink(os.path.join(entry.path, "cwd")), script)
                script = os.path.join(os.path.realpath(os.path.dirname(script)), os.path.basename(script))
                if script in targets:
                    active.append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError:
                # A different user's process may be unreadable. Processes owned by
                # this installer (root for normal device deployment) remain visible.
                continue
    return sorted(active)


def _idle(app):
    active = active_monitors(app)
    if active:
        raise ValueError("Monitoring is active for this application (PID {}); stop it before installing".
                         format(", ".join(str(pid) for pid in active)))


def _replace(text, old, new):
    if text.count(old) == 1 and new not in text:
        return text.replace(old, new, 1)
    if text.count(new) == 1 and old not in text:
        return text
    raise ValueError("Unrecognized or ambiguous legacy script; no files changed")


def patched(sources, headless=False):
    result = {name: data.decode("utf-8").replace("\r\n", "\n") for name, data in sources.items()}
    mon = result["mon-sensors"]
    for old_begin, old_end, old_dispatch in ((PREVIOUS_BEGIN, PREVIOUS_END, PREVIOUS_DISPATCH),
                                            (V2_BEGIN, V2_END, V2_DISPATCH),
                                            (V3_BEGIN, V3_END, V3_DISPATCH),
                                            (V4_BEGIN, V4_END, V4_DISPATCH)):
        if old_begin in mon or old_end in mon:
            if (mon.count(old_dispatch) != 1 or mon.count(old_begin) != 1 or
                    mon.count(old_end) != 1 or BEGIN in mon or END in mon):
                raise ValueError("Modified previous bridge block; refusing to overwrite")
            mon = mon.replace(old_dispatch, DISPATCH, 1)
    if BEGIN in mon or END in mon:
        if mon.count(DISPATCH) != 1 or mon.count(BEGIN) != 1 or mon.count(END) != 1:
            raise ValueError("Modified bridge block; refusing to overwrite")
    else:
        if not mon.startswith("#!") or mon.count("source ~/ocrun/oc.env") != 1:
            raise ValueError("Unrecognized mon-sensors entry")
        first, rest = mon.split("\n", 1)
        mon = first + "\n" + DISPATCH + rest
    result["mon-sensors"] = mon
    result["ocb"] = _replace(result["ocb"], 'grep -e "mon_sensors"', "grep -E 'mon[-_]sensors'")
    result["oct"] = _replace(result["oct"],
        'nohup ${APPPATH}/mon-sensors 2 ${_MON_LOG} &',
        'nohup "${APPPATH}/mon-sensors" 2 "${_MON_LOG}" &')
    stop = '"${APPPATH}/mon-sensors-plugin" --stop-app "${APPPATH}"'
    private_stop = '"${APPPATH}/.bits-collector" --stop-app "${APPPATH}"'
    if headless and private_stop in result['oct']:
        result['oct'] = _replace(result['oct'], private_stop, stop)
    v4_stop = 'python3 ' + stop
    previous_stop = 'python3 "${APPPATH}/sckocp-collector/mon-sensors" --stop-app "${APPPATH}"'
    old_stop = 'kill -9 $(ps -ef | grep -e "mon-sensors" | grep -v grep | awk \'{print $2}\')'
    intermediate_stop = old_stop.replace("kill -9", "kill -TERM")
    if v4_stop in result["oct"]:
        if result["oct"].count(v4_stop) != 1 or stop in result["oct"].replace(v4_stop, "", 1):
            raise ValueError("Ambiguous previous plugin stop command")
        result["oct"] = result["oct"].replace(v4_stop, stop, 1)
    elif previous_stop in result["oct"]:
        result["oct"] = _replace(result["oct"], previous_stop, stop)
    elif intermediate_stop in result["oct"]:
        result["oct"] = _replace(result["oct"], intermediate_stop, stop)
    else:
        result["oct"] = _replace(result["oct"], old_stop, stop)
    analyzer = "py/mon-analyse-log.py"
    result[analyzer] = _replace(result[analyzer], "aggfunc='mean')", "aggfunc='mean', dropna=False)")
    result[analyzer] = _replace(result[analyzer], "sheet_filename = sys.argv[3]\n",
        'sheet_filename = sys.argv[3] if len(sys.argv) > 3 else "Monitoring"\n')
    if headless:
        # Keep the original monitor command's code; the batch executor owns a
        # private collector instead. Known old bridges are removed exactly.
        result['mon-sensors'] = result['mon-sensors'].replace(DISPATCH, '', 1)
        result['oct'] = result['oct'].replace('${APPPATH}/mon-sensors-plugin', '${APPPATH}/.bits-collector')
    return {name: text.encode("utf-8") for name, text in result.items()}


def _atomic(path, data, mode):
    fd, temporary = tempfile.mkstemp(prefix=".mon-sensors-write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _lock_file(app, create):
    path = app / ".mon-sensors-install.lock"
    if not create:
        if path.exists() or path.is_symlink():
            _regular(path)
        return None
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        details = os.fstat(fd)
        _safe_file(details, path)
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (details.st_dev, details.st_ino):
            raise ValueError("Installation lock was replaced")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def install(app, source, backend=None, check=False, adopt_original=False, modules_only=False, headless=False):
    if backend not in (None, "legacy", "sckocp", "auto"):
        raise ValueError("Unsupported backend selection")
    if modules_only and (backend is not None or adopt_original):
        raise ValueError('--modules-only preserves the backend and requires an existing managed plugin')
    app, source = _paths(app, source)
    _idle(app)
    fd, adoption = None, None
    try:
        if any(os.path.commonpath([str(app), str((app / name).resolve())]) != str(app)
               for name in SCRIPTS):
            raise ValueError("A legacy script escapes the application directory")
        try:
            originals = {name: _regular(app / name) for name in SCRIPTS}
        except ValueError:
            if not adopt_original:
                raise
            adoption = OriginalAdoption.prepare(app)
            originals = adoption.originals
        fd = _lock_file(app, create=not check)
        if any(not os.access(str((app / name).parent), os.W_OK | os.X_OK) for name in SCRIPTS):
            raise PermissionError("A legacy script directory is not writable by this user")
        configuration = app / BACKEND_FILE
        config_before = _regular(configuration) if configuration.exists() or configuration.is_symlink() else None
        if config_before is not None and config_before not in (b"legacy\n", b"sckocp\n", b"auto\n"):
            raise ValueError("Unrecognized device backend configuration")
        config_after = (backend + "\n").encode("ascii") if backend is not None else config_before
        prepared = originals if modules_only else patched(originals, headless=headless)
        payload = {name: _regular(source / name) for name in FILES}
        payload.update({name: _regular(source / relative)
                        for name, relative in COMPATIBILITY_FILES.items()})
        helper_name = '.bits-collector.d' if headless else HELPER
        entry_name = '.bits-collector' if headless else ENTRY
        launcher = _launcher(app, helper_name)
        digest = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
        helper = app / helper_name
        if modules_only and not helper.exists():
            raise ValueError('--modules-only requires an existing managed plugin')
        entry = app / entry_name
        entry_before = _regular(entry) if entry.exists() or entry.is_symlink() else None
        entry_mode = stat.S_IMODE(entry.stat().st_mode) if entry_before is not None else 0o755
        previous = {}
        if helper.is_symlink() or (helper.exists() and not helper.is_dir()):
            raise ValueError("Unsafe existing plugin module directory")
        if helper.exists():
            _trusted_directory(helper)
            marker = _regular(helper / MARKER)
            previous = json.loads(marker.decode("utf-8"))
            if not isinstance(previous, dict) or previous.get("owner") != "mon-sensors-plugin-v1":
                raise ValueError("Existing helper is not managed by this installer")
            records = previous.get("files")
            if not isinstance(records, dict) or ENTRY not in records:
                raise ValueError("Existing helper has an invalid installation record")
            for name, expected in records.items():
                if name not in PREVIOUS_FILES or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise ValueError("Existing helper has an invalid installation record")
                try:
                    installed = _regular(helper / name)
                except FileNotFoundError:
                    continue  # Restore missing managed dependencies during an upgrade.
                if hashlib.sha256(installed).hexdigest() != expected:
                    raise ValueError("Locally modified plugin module; refusing to overwrite: " + name)
        if entry_before is not None and hashlib.sha256(entry_before).hexdigest() != previous.get(
                "launcher_sha256", previous.get("files", {}).get(ENTRY)):
            raise ValueError("Existing mon-sensors-plugin command is not an unchanged managed entrypoint")
        unchanged = False
        if helper.exists():
            try:
                unchanged = all(_regular(helper / name) == data for name, data in payload.items())
            except FileNotFoundError:
                unchanged = False  # An older managed release may not contain new modules.
        unchanged = (unchanged and entry_before == launcher and originals == prepared and
                     config_before == config_after)
        selection = config_after.decode("ascii").strip() if config_after is not None else "legacy"
        backup_root = app / ".mon-sensors-backups"
        if backup_root.exists() or backup_root.is_symlink():
            _trusted_directory(backup_root)
        if check:
            return {"status": "checked", "action": "already_installed" if unchanged else "install",
                    "app": str(app), "backend": selection, "read_only": True,
                    "entrypoint": str(entry), "backup_directory": str(backup_root),
                    "native_authorization": "not_checked", "monitoring": "stopped",
                    "original_adoption": adoption.plan if adoption else None, "modules_only": modules_only}
        if unchanged:
            return {"status": "already_installed", "app": str(app), "backend": selection}
        _idle(app)  # Catch monitoring started while the installation was prepared.
        backup_root.mkdir(mode=0o700, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="before-", dir=str(backup_root)))
        modes = {}
        metadata = {}
        for name in SCRIPTS:
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            info = (app / name).lstat()
            metadata[name] = (adoption.metadata[name] if adoption else
                              {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid})
            modes[name] = metadata[name]["mode"]
            _atomic(backup / name, originals[name], 0o600)
        _atomic(backup / "original-metadata.json",
                (json.dumps({"files": metadata, "adoption": adoption.plan if adoption else None},
                            indent=2) + "\n").encode("utf-8"), 0o600)
        if config_before is not None:
            shutil.copy2(str(configuration), str(backup / BACKEND_FILE))
        if entry_before is not None:
            shutil.copy2(str(entry), str(backup / ENTRY))
        stage = Path(tempfile.mkdtemp(prefix=".mon-sensors-plugin-", dir=str(app)))
        old_helper, activated = False, False
        changed_scripts = []
        try:
            if adoption:
                adoption.apply()
            for name, data in payload.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o755 if name == ENTRY else 0o644)
            (stage / MARKER).write_text(json.dumps({"owner": "mon-sensors-plugin-v1", "files": digest,
                "launcher_sha256": hashlib.sha256(launcher).hexdigest()}) + "\n")
            if helper.exists():
                os.replace(str(helper), str(backup / HELPER))
                old_helper = True
            os.replace(str(stage), str(helper))
            activated = True
            _atomic(entry, launcher, 0o755)
            for name in SCRIPTS:
                if modules_only:
                    continue
                changed_scripts.append(name)
                _atomic(app / name, prepared[name], modes[name])
            if config_after is not None:
                _atomic(configuration, config_after, 0o600)
        except BaseException:
            # Backups remain available even if a filesystem failure also prevents rollback.
            for name in changed_scripts:
                _atomic(app / name, originals[name], modes[name])
                if adoption:
                    os.chown(str(app / name), metadata[name]["uid"], metadata[name]["gid"], follow_symlinks=False)
            if adoption:
                adoption.rollback()
            if config_before is not None:
                shutil.copy2(str(backup / BACKEND_FILE), str(configuration))
            elif configuration.exists():
                configuration.unlink()
            if entry_before is not None:
                _atomic(entry, entry_before, entry_mode)
            elif entry.exists():
                entry.unlink()
            if activated:
                shutil.rmtree(str(helper))
            if old_helper:
                os.replace(str(backup / HELPER), str(helper))
            raise
        finally:
            if stage.exists():
                shutil.rmtree(str(stage))
        return {"status": "installed", "app": str(app), "backup": str(backup),
                "backend": selection, "original_adoption": adoption.plan if adoption else None,
                "modules_only": modules_only}
    finally:
        if fd is not None:
            os.close(fd)
        if adoption is not None:
            adoption.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", help="0730 device directory; omitted discovers one compatible installation")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--backend", choices=("legacy", "sckocp", "auto"),
                        help="Persist selection; automatic discovery enables sckocp on first installation")
    parser.add_argument("--check", action="store_true", help="Validate and show the plan without changing files")
    parser.add_argument('--modules-only', action='store_true',
                        help='Upgrade only managed plugin modules and launcher; preserve scheduler/report hooks')
    parser.add_argument('--headless', action='store_true', help='Private BITS batch collector; preserve original monitoring command')
    parser.add_argument("--adopt-original", action="store_true",
                        help="Root only: adopt known original UID 201 files after exact release hash validation; requires --app")
    args = parser.parse_args(argv)
    try:
        automatic = args.app is None
        if automatic and args.adopt_original:
            raise ValueError("--adopt-original requires an explicit --app directory")
        app = discover_app() if automatic else Path(args.app)
        backend = args.backend
        if automatic and backend is None and not (app / BACKEND_FILE).exists() and not (app / HELPER).exists():
            existing = _regular(app / "mon-sensors").decode("utf-8")
            if not any(marker in existing for marker in (BEGIN, PREVIOUS_BEGIN, V2_BEGIN, V3_BEGIN, V4_BEGIN)):
                backend = "sckocp"
        result = install(app, args.source, backend, check=args.check, adopt_original=args.adopt_original,
                         modules_only=args.modules_only, headless=args.headless)
        result["discovered"] = automatic
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(1, "mon-sensors-plugin installation failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
