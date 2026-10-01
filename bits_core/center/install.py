"""Fresh-server installation, persistent retry journal, and non-destructive detach."""
import argparse
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request

from . import VERSION, platforms, render, safe
from .wire import Redis

STATE = Path("/var/lib/bits/center-install")
MANIFEST = Path("/etc/bits/center/manifest.json")
CURRENT = Path("/opt/bits/center-runtime/current")


def command(argv, timeout=45, check=True):
    executable = Path(shutil.which(str(argv[0]), path="/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin") or str(argv[0]))
    safe.directory(executable.parent.resolve(strict=True))
    safe.read(executable.resolve(strict=True), limit=128 * 1024 * 1024, executable=True)
    # Distribution multi-call executables select behavior from argv[0]. Verify
    # the trusted link target, but retain redis-server rather than redis-check-rdb.
    result = subprocess.run([str(executable)] + list(map(str, argv[1:])), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    if check and result.returncode:
        # Do not echo argv: it may contain credentials when called by a future
        # adapter. Current commands never put database passwords in argv.
        raise ValueError("{} failed ({}): {}".format(executable.name, result.returncode, result.stderr.decode("utf-8", "replace")[-1000:]))
    return result


def parents(path):
    path = Path(path)
    for part in list(reversed(path.parents)) + [path]:
        if not part.exists():
            safe.mkdir(part)
        else:
            safe.directory(part)


def package(source):
    source = Path(source).resolve(strict=True)
    safe.directory(source)
    value = safe.load(source / "PACKAGE.json")
    if value["version"] != VERSION:
        raise ValueError("Installer and package version differ")
    for relative, metadata in value["files"].items():
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("Unsafe package manifest path")
        data = safe.read(source / relative, 128 * 1024 * 1024)
        if safe.digest(data) != metadata["sha256"] or len(data) != metadata["bytes"]:
            raise ValueError("Package file differs from manifest: " + relative)
    return value


def verify_managed():
    value = safe.load(MANIFEST)
    if not CURRENT.is_symlink() or str(CURRENT.resolve()) != value["release"]:
        raise ValueError("Managed runtime link changed; preserve it for inspection")
    for filename, metadata in value["files"].items():
        data = safe.read(filename, 128 * 1024 * 1024)
        if safe.digest(data) != metadata["sha256"] or (Path(filename).stat().st_mode & 0o777) != metadata["mode"]:
            raise ValueError("Managed file modified; refusing replacement: " + filename)
    return value


def dependencies(platform):
    for name in (platform["database_binary"], "nginx", "rsync", "nft", "systemctl", "useradd", "groupadd"):
        path = shutil.which(name)
        if not path:
            raise ValueError("Missing system dependency: " + name + "; run the dependency installer or prepare internal repositories")
        safe.read(Path(path).resolve(), 128 * 1024 * 1024, executable=True)
    text = command([platform["database_binary"], "--version"]).stdout.decode()
    match = re.search(r"v=(\d+)\.", text)
    if not match or int(match.group(1)) < 6:
        raise ValueError("Redis/Valkey 6+ is required; no automatic replacement of an existing database")
    if not Path("/run/systemd/system").is_dir():
        raise ValueError("A running systemd system is required for service deployment")
    pwd.getpwnam(platform["database_user"])
    return text.strip()


def fresh_check(config, outputs):
    for path in list(outputs) + [str(MANIFEST), str(CURRENT), "/data/cds/result"]:
        p = Path(path)
        if p.exists() or p.is_symlink():
            raise ValueError("Existing path is not managed by this installer; refusing to overwrite: " + path)
        while not p.parent.exists():
            p = p.parent
        safe.directory(p.parent)
    if Path("/etc/ocrun/server.json").exists() or Path("/etc/ocrun/agent.json").exists():
        raise ValueError("Another OCRUN architecture is installed; use a separate fresh server")
    if Path('/etc/ocrun-server/manifest.json').exists():
        raise ValueError('A pre-0.3 BITS center is deployed; export and roll back that deployment before migration. Existing data was preserved.')
    for service in render.SERVICES:
        if command(["systemctl", "is-active", "--quiet", service], check=False).returncode == 0:
            raise ValueError("Existing unowned service: " + service)
    if command(["nft", "list", "table", "inet", "bits_center"], check=False).returncode == 0:
        raise ValueError("Existing unowned nft table: bits_center")
    for port in (80, 873, 6379):
        with socket.socket() as probe:
            try:
                # Closed HTTP/rsync connections can remain in TIME_WAIT after
                # a clean rollback; active listeners must still cause failure.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((config["address"], port))
                probe.listen(1)
            except OSError:
                raise ValueError("Server address is absent or TCP port {} is occupied".format(port))
    return {"status": "checked", "read_only": True, "version": VERSION,
            "protocol": config["protocol"], "platform": config["platform"],
            "address": config["address"], "network": config["network"], "automatic_task_polling": False}


def health(config):
    for service in render.SERVICES:
        if command(["systemctl", "is-active", "--quiet", service], check=False).returncode:
            raise ValueError('Service not active: {}; inspect journalctl -u {}'.format(service, service))
    from .security_setup import service_contexts
    contexts = service_contexts()
    guard = safe.read('/run/bits-center-guard.sha256', 1024).decode('ascii').split()[0]
    if safe.digest(command(['nft', '-s', 'list', 'table', 'inet', 'bits_center']).stdout) != guard:
        raise ValueError('Running management-network guard differs from its verified copy')
    if Redis(password=config["password"]).call("PING") != "PONG":
        raise ValueError("Task database did not answer PING")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://" + config["address"] + "/config/ocrun-version.txt", timeout=5) as response:
        if b"MAIN_VERSION=0.9.24a\n" not in response.read(4096):
            raise ValueError("Resource version endpoint does not match the supported node")
    modules = command(["rsync", "--list-only", "--contimeout=5", "--timeout=5", config["address"] + "::"], timeout=12).stdout
    if not all(name in modules.split() for name in (b"logs", b"ocrun")):
        raise ValueError("Required original rsync modules are missing")
    return {"status": "ok", "version": VERSION, "protocol": config["protocol"],
            "services": list(render.SERVICES), "database": config["platform"]["database"],
            "automatic_task_polling": False, "results": config["results"],
            "service_selinux_contexts": contexts,
            "hardware_acceptance": False, "database_authentication": "required",
            "legacy_resource_tree": "not_provisioned_by_this_installer",
            "required_node_baseline": "OCRUN 0.9.24a + sensors plugin + finalizer 0.2.2 for authenticated access"}


def accounts():
    try:
        group = grp.getgrnam("bits")
    except KeyError:
        command(["groupadd", "--system", "bits"])
        group = grp.getgrnam("bits")
    try:
        user = pwd.getpwnam("bits")
        if user.pw_gid != group.gr_gid:
            raise ValueError("Existing bits has a different primary group; refusing to change it")
    except KeyError:
        command(["useradd", "--create-home", "--gid", "bits", "--shell", "/bin/bash", "bits"])
        user = pwd.getpwnam("bits")
    # Existing passwords, SSH keys, shell startup files and sudo policies are untouched.
    return user, group


def failure_diagnostics(config):
    """Retain bounded metadata before rollback removes failed service paths."""
    paths = ['/etc/bits/center', '/etc/bits/center/database.conf',
             '/usr/bin/redis-server', '/usr/sbin/nginx', '/var/log/nginx',
             config['database_dir'], '/run/bits-center-http', '/run/bits-center-http/server.pid',
             '/run/bits-center-rsync', '/srv/bits/results']
    probes = [['ls', '-ldZ'] + paths,
              ['ps', '-C', 'redis-server,valkey-server,nginx,rsync', '-o', 'label,pid,user,comm'],
              ['journalctl', '--no-pager', '-o', 'verbose', '-n', '12', '_COMM=redis-server', '_COMM=nginx'],
              ['journalctl', '--no-pager', '-n', '80', '-u', 'bits-center-db', '-u', 'bits-center-http'],
              ['journalctl', '--no-pager', '-k', '-n', '50']]
    output = []
    for argv in probes:
        try:
            result = command(argv, timeout=10, check=False)
            output.append((' '.join(argv) + '\n').encode() + result.stdout[-16000:] + result.stderr[-2000:])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            output.append(str(error).encode('utf-8'))
    safe.write(STATE / 'failure-diagnostics.txt', b'\n'.join(output))


def apply(config, source, outputs, manifest):
    parents(STATE)
    os.chmod(str(STATE), 0o700)
    prepared = STATE / "prepared.json"
    if prepared.exists():
        previous = safe.load(prepared)
        if any(previous["config"][k] != config[k] for k in ("address", "network", "platform")):
            raise ValueError("Retry must use the prepared address/network/OS")
        if previous["package"] != manifest:
            raise ValueError("Prepared package differs; inspect the installation journal before upgrading")
        config = previous["config"]
        outputs = render.files(config, str(Path(sys.executable).resolve()))
    else:
        safe.save(prepared, {"config": config, "package": manifest})
    # Keep this file and prepared credentials even after a failed activation.
    journal = {"version": VERSION, "stage": "preparing", "files": {}, "release": None,
               "symlinks": {}, "error": None, "data_preserved": True}
    safe.save(STATE / "journal.json", journal)
    try:
        user, group = accounts()
        for path in ("/opt/bits/center-runtime/releases", "/etc/bits/center", "/usr/local/libexec",
                     "/srv/bits", "/srv/bits/resources/ocrun", "/srv/bits/resources/releases",
                     "/srv/bits/resources/config", "/data/cds", "/run/bits-center-http", "/run/bits-center-rsync"):
            parents(path)
        results = Path(config["results"])
        if not results.exists():
            results.mkdir(mode=0o750)
            results.chmod(0o750)
            os.chown(str(results), user.pw_uid, group.gr_gid)
        else:
            info = results.lstat()
            if not results.is_dir() or results.is_symlink() or info.st_uid != user.pw_uid or info.st_mode & 0o022:
                raise ValueError("Existing result directory has an unexpected owner/type/mode")
        dbuser = pwd.getpwnam(config["platform"]["database_user"])
        database = Path(config["database_dir"])
        if not database.exists():
            database.mkdir(mode=0o700)
            os.chown(str(database), dbuser.pw_uid, dbuser.pw_gid)
        elif database.is_symlink() or not database.is_dir() or database.stat().st_uid != dbuser.pw_uid or database.stat().st_mode & 0o077:
            raise ValueError("Untrusted existing database directory")
        release = Path("/opt/bits/center-runtime/releases") / (VERSION + "-" + safe.digest(json.dumps(manifest, sort_keys=True).encode())[:12])
        if release.exists():
            safe.directory(release)
        else:
            release.mkdir(mode=0o755)
            release.chmod(0o755)
        journal["release"] = str(release)
        for relative in manifest["files"]:
            if relative.startswith("bits_core/center/") or relative == 'bits_core/__init__.py':
                target = release / relative
                parents(target.parent)
                data = safe.read(source / relative, 128 * 1024 * 1024)
                if target.exists() and safe.digest(safe.read(target)) != safe.digest(data):
                    raise ValueError("Retained release was modified: " + str(target))
                safe.write(target, data, 0o644)
                journal["files"][str(target)] = {"sha256": safe.digest(data), "mode": 0o644}
        outputs["/usr/local/libexec/bits-center-guard"] = (safe.read(source / "bits_core/center/guard.sh").decode(), 0o755)
        for path, (text, mode) in outputs.items():
            parents(Path(path).parent)
            safe.write(path, text.encode("utf-8"), mode)
            journal["files"][path] = {"sha256": safe.digest(text.encode("utf-8")), "mode": mode}
            safe.save(STATE / "journal.json", journal)
        os.chown("/etc/bits/center/server.json", 0, group.gr_gid)
        os.chown("/etc/bits/center/database.conf", 0, dbuser.pw_gid)
        for link, target in ((CURRENT, release), (Path("/data/cds/result"), results)):
            link.symlink_to(target)
            journal["symlinks"][str(link)] = str(target)
            safe.save(STATE / "journal.json", journal)
        from .security_setup import configure
        configure(config, journal, lambda: safe.save(STATE / "journal.json", journal))
        command(["nginx", "-t", "-c", "/etc/bits/center/nginx.conf"])
        command(["nft", "-c", "-f", "/etc/bits/center/ingress.nft"])
        command(["systemctl", "daemon-reload"])
        journal["stage"] = "activating"
        safe.save(STATE / "journal.json", journal)
        for service in render.SERVICES:
            command(["systemctl", "enable", "--now", service])
        for attempt in range(15):
            try:
                result = health(config)
                break
            except (ValueError, OSError):
                if attempt == 14:
                    raise
                time.sleep(1)
        journal["stage"] = "installed"
        safe.save(MANIFEST, journal)
        os.chmod(str(MANIFEST), 0o644)
        safe.save(STATE / "journal.json", journal)
        result["action"] = "install"
        return result
    except BaseException as error:
        journal["error"] = str(error)[:1000]
        safe.save(STATE / "journal.json", journal)
        try:
            failure_diagnostics(config)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass  # Diagnostics must never prevent the owned-service rollback.
        undo(journal)
        raise


def undo(journal):
    problems = []
    # Stop only units created by this deployment. Never stop default vendor
    # Redis/nginx, another node's scheduler, or unrelated services.
    for service in reversed(render.SERVICES):
        unit = "/etc/systemd/system/" + service + ".service"
        if unit in journal["files"]:
            result = command(["systemctl", "disable", "--now", service], check=False)
            if result.returncode and command(["systemctl", "is-active", "--quiet", service], check=False).returncode == 0:
                problems.append("Unable to stop " + service)
    if problems:
        raise ValueError("Rollback incomplete, retaining files and access guard: " + "; ".join(problems))
    from .security_setup import rollback
    rollback(journal)
    for link, target in journal["symlinks"].items():
        path = Path(link)
        if path.is_symlink() and os.readlink(str(path)) == target:
            path.unlink()
        elif path.exists() or path.is_symlink():
            problems.append("Changed link retained: " + link)
    # Runtime copies and all database/result files stay for audit/retry.
    for filename, metadata in journal["files"].items():
        if filename.startswith("/opt/bits/center-runtime/releases/"):
            continue
        path = Path(filename)
        if not path.exists() and not path.is_symlink():
            continue
        try:
            if safe.digest(safe.read(path)) != metadata["sha256"]:
                raise ValueError("content changed")
            path.unlink()
        except (OSError, ValueError):
            problems.append("Changed file retained: " + filename)
    command(["systemctl", "daemon-reload"])
    if MANIFEST.exists():
        MANIFEST.unlink()
    journal["stage"] = "rollback_incomplete" if problems else "detached"
    journal["rollback_problems"] = problems
    safe.save(STATE / "journal.json", journal)
    if problems:
        raise ValueError("Rollback retained modified files: " + "; ".join(problems))
    return {"action": "rollback", "status": "detached", "data_preserved": True,
            "packages_accounts_preserved": True, "prepared_credentials_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--address")
    parser.add_argument("--network")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    parser.add_argument("--skip-deps", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Server deployment requires root")
    source = Path(args.source).resolve(strict=True)
    manifest = package(source)
    safe.directory(Path("/run"))
    descriptor = os.open("/run/bits-center-install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, "a") as guard:
        info = os.fstat(guard.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError("Untrusted installation lock")
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            value = verify_managed()
            result = undo(value)
        else:
            if not args.address or not args.network:
                parser.error("--address and --network are required")
            platform = platforms.profile(platforms.read_release())
            config = render.configuration(args.address, args.network, platform)
            if MANIFEST.exists():
                verify_managed()
                previous = safe.load("/etc/bits/center/server.json")
                if any(previous[k] != config[k] for k in ("address", "network", "platform")):
                    raise ValueError("Installed address/network/platform differ; no implicit reconfiguration")
                result = health(previous)
                result.update({"action": "check" if args.check else "install", "already_installed": True})
            else:
                dependencies(platform)
                outputs = render.files(config, str(Path(sys.executable).resolve()))
                outputs["/usr/local/libexec/bits-center-guard"] = (safe.read(source / "bits_core/center/guard.sh").decode(), 0o755)
                result = fresh_check(config, outputs)
                if args.apply:
                    result = apply(config, source, outputs, manifest)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("ocrun-server install: " + str(error), file=sys.stderr)
        sys.exit(1)
