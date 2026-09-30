#!/usr/bin/env python3
"""Cloud-only, real offline installer checks on a disposable root Linux VM.

The native executable and original 0730 application are synthetic/credential-free
fixtures. No hardware, activation server, customer license or network is used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".cloud-results" / "automatic-install.json"
BASELINE = ROOT / "integrations" / "mon-sensors" / "upstream"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_sckocp_original_api import original_payload


def write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    path.chmod(mode)


def snapshot(directory):
    """Include modes, directories and symlinks without following links."""
    if not directory.exists():
        return None
    result = {}
    for path in sorted([directory] + list(directory.rglob("*"))):
        info = path.lstat()
        if path.is_symlink():
            content = ("link", os.readlink(str(path)))
        elif path.is_file():
            content = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            content = ("directory",)
        result[str(path.relative_to(directory))] = (stat.S_IMODE(info.st_mode), content)
    return result


def extract(archive, destination):
    destination.mkdir()
    with tarfile.open(str(archive), "r:gz") as bundle:
        for member in bundle.getmembers():
            assert member.isfile() and not Path(member.name).is_absolute()
            assert ".." not in Path(member.name).parts
        bundle.extractall(str(destination))


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Run only as root on the disposable GitHub Actions Linux runner")
    started = time.monotonic()
    results = []
    manifest = json.loads((ROOT / "dist" / "manifest.json").read_text())

    def artifact(key):
        entry = manifest[key]
        path = ROOT / "dist" / entry["file"]
        assert path.stat().st_size == entry["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        return path

    api_run = artifact("public_api_installer")
    plugin_run = artifact("mon_sensors_installer")
    api_tar = artifact("public_api")
    plugin_tar = artifact("mon_sensors_plugin")

    def checked(name, function):
        try:
            function()
            results.append({"name": name, "passed": True})
            print("PASS " + name, flush=True)
        except BaseException as error:
            results.append({"name": name, "passed": False, "error": str(error)})
            raise

    try:
        with tempfile.TemporaryDirectory(prefix="sckocp-automatic-install-", dir="/var/tmp") as temporary:
            directory = Path(temporary)
            home = directory / "customer home"
            home.mkdir()
            work = directory / "arbitrary working directory"
            work.mkdir()
            binary_dir = directory / "fixture-bin"
            binary_dir.mkdir()
            native = binary_dir / "sckocp"
            native_mode = directory / "native-mode"
            native_calls = directory / "native-calls"
            write(native_mode, "ok")
            write(native, "#!" + sys.executable + "\n"
                  "import json, os, sys\n"
                  "from pathlib import Path\n"
                  "assert sys.argv[1:] == ['mon', '--json']\n"
                  "assert os.environ['SCKOCP_MODE'] == 'ro'\n"
                  "assert os.environ['SCKOCP_MODPROBE'] == '0'\n"
                  "with open(" + repr(str(native_calls)) + ", 'a') as out: out.write('called\\n')\n"
                  "if Path(" + repr(str(native_mode)) + ").read_text().strip() == 'denied':\n"
                  "    print('PRIVATE-ACTIVATION-FIXTURE', file=sys.stderr)\n"
                  "    sys.exit(10)\n"
                  "print(" + repr(json.dumps(original_payload())) + ")\n", 0o755)
            native_before = (native.read_bytes(), native.stat().st_mode)
            environment = {"HOME": str(home), "PATH": str(binary_dir) + ":/usr/bin:/bin",
                           "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                           "SCKOCP_BINARY": str(native)}

            def command(arguments, code=0, env=None):
                result = subprocess.run([str(value) for value in arguments], cwd=str(work),
                                        env=environment if env is None else env, timeout=30,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        universal_newlines=True)
                if code is None:
                    assert result.returncode != 0, result.stdout + result.stderr
                else:
                    assert result.returncode == code, result.stdout + result.stderr
                assert "PRIVATE-ACTIVATION-FIXTURE" not in result.stdout + result.stderr
                return result

            api_source = directory / "api source"
            plugin_source = directory / "plugin source"
            extract(api_tar, api_source)
            extract(plugin_tar, plugin_source)
            prefix = directory / "custom prefix" / "sckocp-api"
            bin_dir = directory / "custom bin"
            bin_dir.mkdir()
            api_options = ["--prefix", prefix, "--bin-dir", bin_dir]

            def help_and_tamper():
                for installer in (api_run, plugin_run):
                    header = installer.read_bytes().split(b"# __SCKOCP_ARCHIVE_BELOW__\n", 1)[0]
                    syntax = subprocess.run(["/bin/bash", "-n"], input=header,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                    assert syntax.returncode == 0, syntax.stderr
                    command(["/bin/bash", installer, "--help"])
                    data = bytearray(installer.read_bytes())
                    data[-1] ^= 1
                    damaged = directory / (installer.name + ".damaged")
                    damaged.write_bytes(data)
                    result = command(["/bin/bash", damaged, "--help"], code=None)
                    output = (result.stdout + result.stderr).lower()
                    assert any(word in output for word in ("checksum", "sha256", "sha-256", "integrity", "校验")), output
                assert not native_calls.exists(), "Installer help executed the native program"
                assert not prefix.exists()
            checked("self-extracting installers verify embedded checksums before executing payload", help_and_tamper)

            def api_preview():
                before = snapshot(directory)
                command(["/bin/bash", api_run] + api_options + ["--check"])
                assert snapshot(directory) == before, "API --check wrote into the target/fixtures"
                assert not native_calls.exists()
            checked("API installer preflight is read-only and does not collect or activate", api_preview)

            def api_install():
                command(["/bin/bash", api_run] + api_options)
                entry = bin_dir / "sckocp-api"
                assert entry.exists() and entry.stat().st_mode & 0o111
                command([entry, "--version"])
                before = snapshot(prefix)
                entry_before = entry.read_bytes()
                command(["/bin/bash", api_run] + api_options)
                assert snapshot(prefix) == before and entry.read_bytes() == entry_before
                assert not native_calls.exists(), "Installation called the native program"
                for mode, status, code in (("ok", "ok", 0), ("denied", "license_denied", 1), ("ok", "ok", 0)):
                    write(native_mode, mode)
                    result = command([entry, "--binary", native, "--interval", ".05"], code=code)
                    response = json.loads(result.stdout)
                    assert response["schema"] == "sckocp-api-v1" and response["status"] == status
                    assert (response["data"] is None) == (status != "ok")
            checked("API run installer supports space-containing paths, repeat installs and licensed calls from any cwd", api_install)

            def api_default_install():
                default_prefix = Path("/opt/sckocp-api")
                default_entry = Path("/usr/local/bin/sckocp-api")
                assert not default_prefix.exists() and not default_entry.exists(), "Disposable VM has an unexpected prior install"
                calls = native_calls.read_bytes()
                # GitHub runners expose writable tool directories to the runner
                # user. They cannot stand in for trusted production defaults.
                # First verify refusal, then normalize only these exact fixture
                # directories on this disposable root VM; keep product policy.
                default_directories = (Path("/opt"), Path("/usr/local"), Path("/usr/local/bin"))
                unsafe = False
                for target in default_directories:
                    details = target.lstat()
                    assert stat.S_ISDIR(details.st_mode), "Default fixture directory must not be a symlink"
                    print(json.dumps({"fixture_directory": str(target), "original_uid": details.st_uid,
                                      "original_gid": details.st_gid,
                                      "original_mode": oct(stat.S_IMODE(details.st_mode))}), flush=True)
                    unsafe = unsafe or details.st_uid != 0 or bool(
                        details.st_mode & 0o022 and not details.st_mode & stat.S_ISVTX)
                if unsafe:
                    command(["/bin/bash", api_run, "--check"], code=None)
                    assert not default_prefix.exists() and not default_entry.exists()
                    assert native_calls.read_bytes() == calls
                for target in default_directories:
                    os.chown(str(target), 0, 0)
                    target.chmod(0o755)
                command(["/bin/bash", api_run])
                assert default_prefix.is_dir() and default_entry.exists()
                command([default_entry, "--version"])
                command(["/bin/bash", api_run, "--check"])
                assert native_calls.read_bytes() == calls
            checked("zero-option API installation creates a working system command without native access", api_default_install)

            def api_conflicts():
                for kind in ("prefix", "launcher"):
                    target = directory / ("conflict-" + kind)
                    target.mkdir()
                    target_prefix = target / "api"
                    target_bin = target / "bin"
                    target_bin.mkdir()
                    if kind == "prefix":
                        write(target_prefix / "customer-file", "must survive\n")
                    else:
                        write(target_bin / "sckocp-api", "#!/bin/sh\necho customer\n", 0o755)
                    before = snapshot(target)
                    command(["/bin/bash", api_run, "--prefix", target_prefix,
                             "--bin-dir", target_bin], code=None)
                    assert snapshot(target) == before, "Unmanaged installation conflict changed customer files"
            checked("API installer refuses unmanaged directories and commands without overwriting them", api_conflicts)

            def api_upgrade():
                version_file = api_source / "sckocp_api" / "__init__.py"
                original = version_file.read_text(encoding="utf-8")
                changed, count = re.subn(r'(?m)^__version__\s*=\s*[\'"][^\'"]+[\'"]',
                                         "__version__ = '9.9.9'", original)
                assert count == 1
                version_file.write_text(changed, encoding="utf-8")
                command(["/bin/bash", api_source / "install-sckocp-api.sh"] + api_options)
                assert "9.9.9" in command([bin_dir / "sckocp-api", "--version"]).stdout
                command(["/bin/bash", api_run] + api_options)
                assert "9.9.9" not in command([bin_dir / "sckocp-api", "--version"]).stdout
            checked("managed API payloads upgrade through the archive wrapper and can return to the packaged release", api_upgrade)

            def isolated_python_bootstrap():
                marker = directory / "untrusted-python-executed"
                write(work / "sitecustomize.py", "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
                hostile_bin = directory / "untrusted-bin"
                write(hostile_bin / "python3", "#!/bin/sh\n/usr/bin/touch " + str(marker) + "\nexit 88\n", 0o755)
                hostile = dict(environment)
                hostile["PATH"] = str(hostile_bin) + ":" + environment["PATH"]
                hostile["PYTHONPATH"] = str(work)
                hostile["PYTHONHOME"] = str(work / "nonexistent-python-home")
                command(["/bin/bash", api_run] + api_options + ["--check"], env=hostile)
                command([bin_dir / "sckocp-api", "--version"], env=hostile)
                command(["/bin/bash", plugin_run, "--help"], env=hostile)
                assert not marker.exists(), "Installer/launcher loaded caller-controlled Python code"
            checked("install bootstrap and installed API ignore caller Python paths and shadow interpreters", isolated_python_bootstrap)

            def application(target):
                target.mkdir(parents=True)
                (target / "py").mkdir()
                # The runner's /opt default ACL can make new directories writable
                # even after its own access mode was normalized. Prepare trusted
                # fixture directories explicitly, without relaxing installer rules.
                for fixture_directory in (target, target / "py"):
                    details = fixture_directory.lstat()
                    assert stat.S_ISDIR(details.st_mode) and details.st_uid == 0
                    fixture_directory.chmod(0o755)
                    print(json.dumps({"application_fixture_directory": str(fixture_directory),
                                      "original_mode": oct(stat.S_IMODE(details.st_mode)),
                                      "prepared_mode": oct(stat.S_IMODE(fixture_directory.stat().st_mode))}),
                          flush=True)
                for name in ("mon-sensors", "oct", "ocb", "py/mon-analyse-log.py"):
                    source = BASELINE / ("mon-analyse-log.py" if name.startswith("py/") else name)
                    (target / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(source), str(target / name))
                    (target / name).chmod(0o755)
                write(target / "oc.env", "# customer configuration; installers must not source this\n"
                      "touch " + str(directory / "environment-was-executed") + "\n")
                return target

            app = application(home / "ocrun")
            env_before = (app / "oc.env").read_bytes()

            def plugin_preview():
                before = snapshot(app)
                calls = native_calls.read_bytes()
                command(["/bin/bash", plugin_run, "--check"])
                assert snapshot(app) == before, "Plugin --check created lock/backup files"
                assert native_calls.read_bytes() == calls
                assert not (directory / "environment-was-executed").exists()
            checked("plugin automatically finds the original 0730 tree and checks it without mutations", plugin_preview)

            def plugin_install():
                calls = native_calls.read_bytes()
                command(["/bin/bash", plugin_run])
                assert (app / "mon-sensors-plugin").is_file()
                assert (app / ".mon-sensors-backend").read_bytes() == b"sckocp\n"
                assert (app / "oc.env").read_bytes() == env_before
                command([app / "mon-sensors-plugin", "--help"])
                before = snapshot(app)
                command(["/bin/bash", plugin_run])
                assert snapshot(app) == before, "Repeat plugin installation changed the application"
                command(["/bin/bash", plugin_run, "--backend", "legacy"])
                assert (app / ".mon-sensors-backend").read_bytes() == b"legacy\n"
                command(["/bin/bash", plugin_run])
                assert (app / ".mon-sensors-backend").read_bytes() == b"legacy\n"
                assert native_calls.read_bytes() == calls
                assert not (directory / "environment-was-executed").exists()
            checked("zero-option plugin installation enables the adapter once and preserves later backend choices", plugin_install)

            def explicit_legacy_path():
                explicit = application(directory / "explicit old node")
                command(["/bin/bash", plugin_source / "install-mon-sensors-plugin.sh", explicit])
                assert (explicit / "mon-sensors-plugin").exists()
                setting = explicit / ".mon-sensors-backend"
                assert not setting.exists() or setting.read_bytes() == b"legacy\n"
            checked("existing positional installer syntax retains the original backend default", explicit_legacy_path)

            def plugin_reject_modified():
                modified = application(directory / "customer modified node")
                script = modified / "oct"
                script.write_bytes(script.read_bytes().replace(b"nohup ${APPPATH}/mon-sensors 2 ${_MON_LOG} &", b"echo customer monitoring"))
                before = snapshot(modified)
                command(["/bin/bash", plugin_run, "--app", modified, "--check"], code=None)
                assert snapshot(modified) == before
                command(["/bin/bash", plugin_run, "--app", modified], code=None)
                after = snapshot(modified)
                # An exclusive installation lock is allowed during a real failed
                # install, but all scripts/configuration must remain unchanged.
                after.pop(".mon-sensors-install.lock", None)
                assert after == before
            checked("unsupported customer script changes are refused before replacing original commands", plugin_reject_modified)

            def ambiguous_plugin_discovery():
                other = Path("/opt/ocrun")
                assert not other.exists(), "Disposable VM has an unexpected original OCRUN tree"
                application(other)
                before, other_before = snapshot(app), snapshot(other)
                # Assert both candidates are independently valid before testing
                # ambiguity, so fixture permission failures remain diagnosable.
                command(["/bin/bash", plugin_run, "--app", other, "--check"])
                assert snapshot(app) == before and snapshot(other) == other_before
                result = command(["/bin/bash", plugin_run, "--check"], code=None)
                assert "--app" in result.stdout + result.stderr
                assert snapshot(app) == before and snapshot(other) == other_before
                command(["/bin/bash", plugin_run, "--app", app, "--check"])
                assert snapshot(app) == before and snapshot(other) == other_before
            checked("multiple compatible OCRUN installations require explicit selection without modifying either", ambiguous_plugin_discovery)

            def no_native_mutations():
                assert (native.read_bytes(), native.stat().st_mode) == native_before
                assert not (directory / "environment-was-executed").exists()
                for target in (prefix, app / "mon-sensors-plugin.d"):
                    assert not any(path.suffix == ".service" for path in target.rglob("*"))
                    assert not any(path.name in ("license", "activation", "passwd", "server.json") for path in target.rglob("*"))
            checked("installation leaves native sckocp and customer environment untouched and creates no service payload", no_native_mutations)
    finally:
        report = {"successful": bool(results) and all(item["passed"] for item in results),
                  "checks": len(results), "results": results,
                  "duration_seconds": round(time.monotonic() - started, 3),
                  "python": sys.version,
                  "scope": "real offline run/archive installers, synthetic licensed provider, original credential-free 0730 scripts",
                  "hardware": "not exercised", "production_licenses": "not used"}
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
