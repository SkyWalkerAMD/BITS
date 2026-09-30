"""Offline, transactional installation of the independent local API (Linux)."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import sys
import unicodedata

from .security import UnsafeExecutable, trusted_executable


FILES = ("__init__.py", "__main__.py", "cli.py", "interface.py",
         "provider.py", "security.py", "install.py")
MANIFEST = ".sckocp-api-install.json"
SCHEMA = "sckocp-api-install-v1"
MAX_FILE = 2 * 1024 * 1024
CACHE_NAME = re.compile(r"^(?:" + "|".join(re.escape(name[:-3]) for name in FILES) +
                        r")\.cpython-[0-9]{2,3}(?:\.opt-[12])?\.pyc$")


class InstallError(ValueError):
    """Installation was refused without replacing unrelated files."""


def _path(value):
    if not isinstance(value, str) or not value or any(
            unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value):
        raise InstallError("Paths must not contain control characters.")
    value = os.path.abspath(value)
    if value == "/":
        raise InstallError("The filesystem root cannot be an installation target.")
    return value


def _trusted(info, directory=False):
    if info.st_uid not in (0, os.geteuid()):
        raise InstallError("A path is owned by an untrusted user.")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise InstallError("A directory path contains a symlink or non-directory.")
        if info.st_mode & 0o022 and not (
                info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise InstallError("A directory is writable by another user or group.")
    elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
          info.st_mode & 0o7022):
        raise InstallError("A file is not a private regular file with safe permissions.")


def _directory(path, create=False, created=None):
    """Open every component without following links; optionally create parents."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", flags)
    current = ""
    try:
        _trusted(os.fstat(descriptor), directory=True)
        for component in path.split("/"):
            if not component:
                continue
            current += "/" + component
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    if not os.access("/proc/self/fd/" + str(descriptor),
                                     os.W_OK | os.X_OK, effective_ids=True):
                        raise InstallError("Cannot create a missing installation directory.")
                    return None
                os.mkdir(component, 0o755, dir_fd=descriptor)
                if created is not None:
                    created.append(current)
                child = os.open(component, flags, dir_fd=descriptor)
            try:
                _trusted(os.fstat(child), directory=True)
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        result = descriptor
        descriptor = None
        return result
    except InstallError as error:
        raise InstallError("Directory " + repr(current or "/") + ": " + str(error)) from error
    except OSError as error:
        # Preserve the errno while reporting the full offending component;
        # openat otherwise exposes only its final basename in diagnostics.
        raise OSError(error.errno, error.strerror, current or "/") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read(parent, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                         os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        _trusted(info)
        if info.st_size > MAX_FILE:
            raise InstallError("An installation file exceeds the size limit.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise InstallError("An installation file exceeds the size limit.")
        return data
    except InstallError as error:
        raise InstallError("File " + repr(name) + ": " + str(error)) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _exists(parent, name):
    if parent is None:
        return False
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _source(source):
    descriptor = _directory(source + "/sckocp_api")
    if descriptor is None:
        raise InstallError("The source does not contain the complete sckocp_api package.")
    try:
        data = {name: _read(descriptor, name) for name in FILES}
        # Embedded in the command, not imported from the installed package.
        # The installed seven-file layout stays compatible with 0.3.1 rollback.
        data['bootstrap.py'] = _read(descriptor, 'bootstrap.py')
    finally:
        os.close(descriptor)
    match = re.search(br'^__version__\s*=\s*[\'"]([0-9]+\.[0-9]+\.[0-9]+)[\'"]',
                      data["__init__.py"], re.M)
    if not match:
        raise InstallError("The source package has no supported version identifier.")
    return data, match.group(1).decode("ascii")


def _launcher(prefix, files):
    # Resolve the interpreter's legitimate distro links, then validate its full
    # resolved path. -S also excludes system sitecustomize and .pth hooks.
    # -B alone does NOT stop cached bytecode from being loaded. The embedded
    # bootstrap validates and imports source bytes without using any .pyc.
    interpreter = os.path.realpath(sys.executable)
    try:
        with trusted_executable(interpreter):
            pass
    except UnsafeExecutable as error:
        raise InstallError("The Python interpreter path is not trusted.") from error
    inventory = {name: _digest(files[name]) for name in FILES}
    code = (files['bootstrap.py'].decode('utf-8') + '\nraise SystemExit(launch(' +
            repr(prefix) + ', ' + repr(inventory) + '))\n')
    return ("#!/bin/sh\n# Managed by sckocp-api installer.\numask 077\n"
            "ulimit -c 0 || exit 1\nexec " +
            shlex.quote(interpreter) + " -I -S -B -c " + shlex.quote(code) +
            ' "$@"\n').encode("utf-8")


def _cache(package):
    """Validate interpreter-created caches; never adopt arbitrary extra files."""
    if not _exists(package, "__pycache__"):
        return None, ()
    descriptor = os.open("__pycache__", os.O_RDONLY | os.O_DIRECTORY |
                         os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=package)
    try:
        info = os.fstat(descriptor)
        _trusted(info, directory=True)
        if info.st_mode & 0o7022:
            raise InstallError("The Python cache directory has unsafe permissions.")
        names = os.listdir(descriptor)
        if len(names) > 128 or any(not CACHE_NAME.fullmatch(name) for name in names):
            raise InstallError("The Python cache contains unrecognized files; it was preserved.")
        for name in names:
            _read(descriptor, name)
        return descriptor, names
    except Exception:
        os.close(descriptor)
        raise


def _inspect(prefix_parent, name, bin_parent, prefix, bin_dir):
    has_prefix = _exists(prefix_parent, name)
    has_launcher = _exists(bin_parent, "sckocp-api")
    if not has_prefix:
        if has_launcher:
            raise InstallError("An unmanaged sckocp-api command already exists.")
        return None, False
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                         os.O_CLOEXEC, dir_fd=prefix_parent)
    package = None
    try:
        _trusted(os.fstat(descriptor), directory=True)
        if set(os.listdir(descriptor)) != {MANIFEST, "sckocp_api"}:
            raise InstallError("The target is unmanaged or contains additional files; it was preserved.")
        try:
            manifest = json.loads(_read(descriptor, MANIFEST).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise InstallError("The existing installation manifest is invalid.")
        if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or
                manifest.get("prefix") != prefix or manifest.get("bin_dir") != bin_dir or
                not isinstance(manifest.get("files"), dict) or
                set(manifest["files"]) != set(FILES)):
            raise InstallError("The existing installation is not managed for these paths.")
        package = os.open("sckocp_api", os.O_RDONLY | os.O_DIRECTORY |
                          os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
        _trusted(os.fstat(package), directory=True)
        names = set(os.listdir(package))
        if names - {"__pycache__"} != set(manifest["files"]):
            raise InstallError("The installed package contains additional or missing files; it was preserved.")
        cache_descriptor, _ = _cache(package)
        if cache_descriptor is not None:
            os.close(cache_descriptor)
        for filename in manifest["files"]:
            if _digest(_read(package, filename)) != manifest["files"][filename]:
                raise InstallError("The installed package has local changes; it was preserved.")
        if has_launcher and _digest(_read(bin_parent, "sckocp-api")) != manifest.get("launcher_sha256"):
            raise InstallError("The existing command has local changes; it was preserved.")
        return manifest, has_launcher
    finally:
        if package is not None:
            os.close(package)
        os.close(descriptor)


def _write(parent, name, data, mode):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent)
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            os.unlink(name, dir_fd=parent)
        raise


def _remove_tree(parent, name, expected):
    """Remove only our own fixed files, after validating the entire tree."""
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                         os.O_CLOEXEC, dir_fd=parent)
    package = cache_descriptor = None
    try:
        if set(os.listdir(descriptor)) - {MANIFEST, "sckocp_api"}:
            raise InstallError("Cleanup stopped because the staged directory changed.")
        has_manifest = _exists(descriptor, MANIFEST)
        if has_manifest and _read(descriptor, MANIFEST) != expected[MANIFEST]:
            raise InstallError("Cleanup stopped because the manifest changed.")
        if _exists(descriptor, "sckocp_api"):
            package = os.open("sckocp_api", os.O_RDONLY | os.O_DIRECTORY |
                              os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            names = set(os.listdir(package)) - {"__pycache__"}
            if names - set(FILES):
                raise InstallError("Cleanup stopped because the staged package changed.")
            cache_descriptor, cache_names = _cache(package)
            for filename in names:
                if _read(package, filename) != expected[filename]:
                    raise InstallError("Cleanup stopped because a staged file changed.")
            if cache_descriptor is not None:
                for filename in cache_names:
                    os.unlink(filename, dir_fd=cache_descriptor)
                os.close(cache_descriptor)
                cache_descriptor = None
                os.rmdir("__pycache__", dir_fd=package)
            for filename in names:
                os.unlink(filename, dir_fd=package)
            os.close(package)
            package = None
            os.rmdir("sckocp_api", dir_fd=descriptor)
        if has_manifest:
            os.unlink(MANIFEST, dir_fd=descriptor)
    finally:
        if cache_descriptor is not None:
            os.close(cache_descriptor)
        if package is not None:
            os.close(package)
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def install(source=None, prefix="/opt/sckocp-api", bin_dir="/usr/local/bin", check=False):
    """Return a plan/result; never invoke native sckocp or install dependencies."""
    if not sys.platform.startswith("linux") or sys.version_info < (3, 6):
        raise InstallError("Linux and Python 3.6 or newer are required.")
    source = _path(source or os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    prefix, bin_dir = _path(prefix), _path(bin_dir)
    if (prefix == bin_dir or prefix.startswith(bin_dir + "/") or
            bin_dir.startswith(prefix + "/") or source == prefix or
            source.startswith(prefix + "/")):
        raise InstallError("The package, command and source paths must not overlap.")
    files, version = _source(source)
    launcher = _launcher(prefix, files)
    manifest = {"schema": SCHEMA, "version": version, "prefix": prefix,
                "bin_dir": bin_dir, "files": {k: _digest(files[k]) for k in FILES},
                "launcher_sha256": _digest(launcher)}
    files[MANIFEST] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    parent_path, name = os.path.split(prefix)
    parent = command_parent = None
    created = []
    token = secrets.token_hex(8)
    stage = "." + name + ".stage-" + token
    stage_command = ".sckocp-api.stage-" + token
    backup = name + ".backup-" + token
    backup_command = "sckocp-api.backup-" + token
    staged = staged_command = saved = saved_command = published = published_command = False
    try:
        # Read-only planning first; actual writes repeat inspection while holding
        # a directory lock. No lock files or directories are created by --check.
        parent = _directory(parent_path)
        command_parent = _directory(bin_dir)
        old, has_command = _inspect(parent, name, command_parent, prefix, bin_dir)
        action = "unchanged" if old == manifest and has_command else ("upgrade" if old else "install")
        result = {"action": action, "check": bool(check), "version": version,
                  "prefix": prefix, "command": bin_dir + "/sckocp-api", "source": source}
        if action != "unchanged":
            for descriptor, directory_path in ((parent, parent_path), (command_parent, bin_dir)):
                if descriptor is not None and not os.access(
                        "/proc/self/fd/" + str(descriptor), os.W_OK | os.X_OK,
                        effective_ids=True):
                    raise InstallError("Directory " + repr(directory_path) +
                                       " is not writable by this user.")
        if check or action == "unchanged":
            return result
        if parent is None:
            parent = _directory(parent_path, create=True, created=created)
        if command_parent is None:
            command_parent = _directory(bin_dir, create=True, created=created)
        # Lock shared parents in a stable order to serialize our own installers.
        for descriptor in sorted(set((parent, command_parent)),
                                 key=lambda fd: (os.fstat(fd).st_dev, os.fstat(fd).st_ino)):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old, has_command = _inspect(parent, name, command_parent, prefix, bin_dir)
        if old == manifest and has_command:
            result["action"] = "unchanged"
            return result
        result["action"] = "upgrade" if old else "install"
        os.mkdir(stage, 0o755, dir_fd=parent)
        staged = True
        stage_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(stage_fd, 0o755)
            os.mkdir("sckocp_api", 0o755, dir_fd=stage_fd)
            package_fd = os.open("sckocp_api", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=stage_fd)
            try:
                os.fchmod(package_fd, 0o755)
                for filename in FILES:
                    _write(package_fd, filename, files[filename], 0o644)
                os.fsync(package_fd)
            finally:
                os.close(package_fd)
            _write(stage_fd, MANIFEST, files[MANIFEST], 0o644)
            os.fsync(stage_fd)
        finally:
            os.close(stage_fd)
        _write(command_parent, stage_command, launcher, 0o755)
        staged_command = True
        if old:
            os.rename(name, backup, src_dir_fd=parent, dst_dir_fd=parent)
            saved = True
        if has_command:
            os.rename("sckocp-api", backup_command, src_dir_fd=command_parent,
                      dst_dir_fd=command_parent)
            saved_command = True
        os.rename(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
        staged, published = False, True
        os.rename(stage_command, "sckocp-api", src_dir_fd=command_parent,
                  dst_dir_fd=command_parent)
        staged_command, published_command = False, True
        os.fsync(parent)
        os.fsync(command_parent)
        if saved:
            result["backup"] = parent_path + "/" + backup
        if saved_command:
            result["command_backup"] = bin_dir + "/" + backup_command
        return result
    except Exception as error:
        try:
            if published_command:
                if _read(command_parent, "sckocp-api") != launcher:
                    raise InstallError("The installed command changed during rollback.")
                os.unlink("sckocp-api", dir_fd=command_parent)
            if published:
                _remove_tree(parent, name, files)
            if saved:
                if _exists(parent, name):
                    raise InstallError("The runtime target changed during rollback.")
                os.rename(backup, name, src_dir_fd=parent, dst_dir_fd=parent)
            if saved_command:
                if _exists(command_parent, "sckocp-api"):
                    raise InstallError("The command target changed during rollback.")
                os.rename(backup_command, "sckocp-api", src_dir_fd=command_parent,
                          dst_dir_fd=command_parent)
            if staged:
                _remove_tree(parent, stage, files)
            if staged_command:
                if _read(command_parent, stage_command) != launcher:
                    raise InstallError("The staged command changed during rollback.")
                os.unlink(stage_command, dir_fd=command_parent)
            for directory in reversed(created):
                os.rmdir(directory)  # Empty, exact directories only; never recurse.
        except Exception as rollback_error:
            raise InstallError("Installation failed; automatic rollback could not finish. "
                               "Existing backups were preserved: " + str(rollback_error)) from error
        raise
    finally:
        if command_parent is not None:
            os.close(command_parent)
        if parent is not None:
            os.close(parent)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the independent sckocp local API offline.")
    parser.add_argument("--source", help="Extracted package root (auto-detected by default)")
    parser.add_argument("--prefix", default="/opt/sckocp-api", help="Dedicated managed runtime directory")
    parser.add_argument("--bin-dir", default="/usr/local/bin", help="Directory for the sckocp-api command")
    parser.add_argument("--check", action="store_true", help="Read-only validation and installation plan")
    args = parser.parse_args(argv)
    try:
        result = install(args.source, args.prefix, args.bin_dir, args.check)
    except (InstallError, OSError) as error:
        print("sckocp-api installation refused: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
