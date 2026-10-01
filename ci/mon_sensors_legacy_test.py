#!/usr/bin/env python3
"""Cloud-only checks of the original oct/mon-sensors chain with a fake provider.

Only the three credential-free upstream scripts are used. The environment,
board functions and provider are synthetic files under a temporary home.
Nothing is installed system-wide, and no hardware or production license is read.
"""
import contextlib
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_sckocp import payload
from test_sckocp_original_api import original_payload
from sckocp_api import __version__ as API_VERSION

REPORT = ROOT / ".cloud-results" / "mon-sensors-legacy.json"
ARTIFACTS = ROOT / ".cloud-results" / "mon-sensors"
BASELINE = ROOT / "integrations" / "mon-sensors" / "upstream"


def wait_for(check, label, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError("Timed out: " + label)


def live(pid):
    try:
        state = Path("/proc/{}/stat".format(pid)).read_text().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return False


def write(path, value, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def read_jsonl(path):
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return []


class Fixture:
    def __init__(self, directory, package, native_format="v2", amd=False):
        self.home = Path(directory)
        self.app = self.home / "ocrun"
        self.app.mkdir()
        self.logs = self.home / "logs"
        self.logs.mkdir()
        self.bin = self.home / "fixture-bin"
        self.bin.mkdir()
        self.package = package
        self.mode = self.home / "provider-mode"
        self.provider_pids = self.home / "provider-pids.jsonl"
        self.legacy_marker = self.home / "legacy-called"
        self.board_marker = self.home / "board-probed"
        self.environment_marker = self.home / "environment-loaded"
        self.diagnostics = self.home / "diagnostics.txt"
        for name in ("mon-sensors", "oct", "ocb"):
            shutil.copy2(BASELINE / name, self.app / name)
            (self.app / name).chmod(0o755)
        (self.app / "py").mkdir()
        shutil.copy2(BASELINE / "mon-analyse-log.py", self.app / "py" / "mon-analyse-log.py")
        (self.app / "py" / "mon-analyse-log.py").chmod(0o755)
        (self.app / "mon-analyse-log").symlink_to("py/mon-analyse-log.py")
        self.env_text = (
            "export APPPATH=" + shlex.quote(str(self.app)) + "\n"
            "export LOGPATH=" + shlex.quote(str(self.logs)) + "\n"
            "export MB_SN=FIXTURE MEM_FREE=1024 CPU_NUM=1 APP_DATE=20260101_000000\n"
            "printf 'loaded\\n' >> " + shlex.quote(str(self.environment_marker)) + "\n"
        )
        write(self.app / "oc.env", self.env_text)
        write(self.mode, "ok")
        write(self.bin / "dmidecode", "#!/bin/bash\n"
              "printf 'probed\\n' >> " + shlex.quote(str(self.board_marker)) + "\n"
              "printf 'Product Name: Unknown_Fixture_Board\\n'\n", 0o755)
        self.provider = self.bin / "sckocp"
        program = (
            "#!" + sys.executable + "\n"
            "import json, os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "assert sys.argv[1:] == " + repr(["mon", "--json" if native_format == "v1" else "--json=v2"]) + "\n"
            "assert os.environ['SCKOCP_MODE'] == 'ro'\n"
            "assert os.environ['SCKOCP_MODPROBE'] == '0'\n"
            "mode = Path(" + repr(str(self.mode)) + ").read_text().strip()\n"
            "record = {'provider': os.getpid()}\n"
            "if mode == 'hang':\n"
            "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'], preexec_fn=os.setpgrp)\n"
            "    record['helper'] = child.pid\n"
            "with open(" + repr(str(self.provider_pids)) + ", 'a') as output:\n"
            "    output.write(json.dumps(record) + '\\n')\n"
            "if mode == 'hang':\n"
            "    time.sleep(90)\n"
            "elif mode == 'denied':\n"
            "    print('fixture-private-license-code-DO-NOT-EXPORT', file=sys.stderr)\n"
            "    sys.exit(10)\n"
            "elif mode == 'transient':\n"
            "    Path(" + repr(str(self.mode)) + ").write_text('ok')\n"
            "    print('fixture-private-license-code-DO-NOT-EXPORT', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "elif mode == 'invalid':\n"
            "    print('fixture-private-license-code-DO-NOT-EXPORT')\n"
            "else:\n"
            "    print(" + repr(json.dumps(original_payload(amd) if native_format == "v1" else payload())) + ")\n"
        )
        write(self.provider, program, 0o755)
        self.env = dict(os.environ)
        self.env.update({"HOME": str(self.home), "PATH": str(self.bin) + ":/usr/bin:/bin",
                         "MON_SENSORS_BACKEND": "sckocp", "SCKOCP_BINARY": str(self.provider),
                         "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8"})
        self.env.pop("SCKOCP_FORMAT", None)
        if native_format == "v2":
            self.env["SCKOCP_FORMAT"] = "v2"

    def command(self, arguments, timeout=15, env=None):
        return subprocess.run([str(value) for value in arguments], cwd=str(self.home),
                              env=self.env if env is None else env, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def install(self, backend=None):
        args = ["/bin/bash", self.package / "install-mon-sensors-plugin.sh", self.app]
        if backend is not None:
            args += ["--backend", backend]
        result = self.command(args)
        assert result.returncode == 0, result.stderr + result.stdout
        assert (self.app / "oc.env").read_text(encoding="utf-8") == self.env_text
        return result

    def oct(self, operation, task="cloud", stamp="20260101_120000"):
        # Background nohup inherits pipe descriptors; use a real log descriptor
        # so waiting for oct does not accidentally wait for the monitor itself.
        with self.diagnostics.open("a", encoding="utf-8") as output:
            result = subprocess.run(["/bin/bash", str(self.app / "oct"), operation, task, stamp],
                                    env=self.env, cwd=str(self.home), timeout=10,
                                    stdout=output, stderr=output)
        assert result.returncode == 0, self.diagnostics.read_text(encoding="utf-8")

    def monitor_pids(self):
        from bits_core.collector.collector import _script_argument
        markers = {str(self.app / "mon-sensors-plugin"),
                   str(self.app / "mon-sensors-plugin.d" / "mon-sensors-plugin")}
        result = []
        for item in Path("/proc").iterdir():
            if not item.name.isdigit():
                continue
            try:
                argv = [os.fsdecode(value) for value in (item / "cmdline").read_bytes().split(b"\0") if value]
                if _script_argument(argv) in markers:
                    result.append(int(item.name))
            except OSError:
                pass
        return result

    def rows(self, path):
        return list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))

    def close(self):
        for pid in self.monitor_pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        pids = set()
        for record in read_jsonl(self.provider_pids):
            pids.update(record.values())
        for pid in pids:
            if live(pid):
                try:
                    # Provider is a session leader; its helper uses the same
                    # session and a separate group, matching native run_cmd.
                    if os.getpgid(pid) == pid:
                        os.killpg(pid, signal.SIGKILL)
                    else:
                        os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def legacy_stubs(self):
        for suffix in (201, 202, 301, 302, 303, 304, 401, 901):
            write(self.app / "fbin" / ("mon-sensors-fun" + str(suffix)), "# fixture only\n")
        write(self.app / "fbin" / "mon-sensors-fun301",
              "recoed_sensors_c9x299-pg300f() { printf 'legacy called\\n' >> " +
              shlex.quote(str(self.legacy_marker)) + "; }\n")
        write(self.bin / "dmidecode", "#!/bin/bash\nprintf 'Product Name: C9X299-PG300F\\n'\n", 0o755)


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or not sys.platform.startswith("linux"):
        raise SystemExit("This validation may run only in the cloud Linux workflow.")
    results = []
    started = time.monotonic()
    fixtures = []

    def checked(name, action):
        try:
            action()
            print("PASS " + name, flush=True)
            results.append({"name": name, "passed": True})
        except BaseException as error:
            results.append({"name": name, "passed": False, "error": str(error)})
            raise

    try:
        with contextlib.ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="ocrun-legacy-fixture-"))
            temporary = Path(directory)
            archive = list(ARTIFACTS.glob("mon-sensors-plugin-*.tar.gz"))
            assert len(archive) == 1, "Build exactly one mon-sensors overlay archive first"
            package = temporary / "package"
            package.mkdir()
            with tarfile.open(str(archive[0]), "r:gz") as bundle:
                members = bundle.getmembers()
                for member in members:
                    parts = Path(member.name).parts
                    assert not Path(member.name).is_absolute() and ".." not in parts
                    assert member.isfile() or member.isdir(), "Unexpected archive entry type"
                    assert "oc.env" not in parts and "fbin" not in parts and "keys" not in parts
                    assert "ocrun" not in parts, "Independent plugin must not contain the OCRUN Python package"
                bundle.extractall(str(package))
            installers = list(package.rglob("install-mon-sensors-plugin.sh"))
            assert len(installers) == 1
            package = installers[0].parent

            def fixture(name, native_format="v2", amd=False):
                home = temporary / name
                home.mkdir()
                value = Fixture(home, package, native_format, amd)
                fixtures.append(value)
                cleanup.callback(value.close)
                return value

            chain = fixture("chain")
            def standalone_plugin():
                source = fixture("standalone-plugin", "v1")
                environment = dict(source.env)
                environment.pop("PYTHONPATH", None)
                for mode, code, label in (("ok", 0, "sckocp-v1"), ("denied", 1, "did not authorize")):
                    write(source.mode, mode)
                    result = source.command([package / "mon-sensors-plugin", ".05", "view", "--once"],
                                            env=environment)
                    assert result.returncode == code and label in result.stdout + result.stderr, result.stderr
                result = subprocess.run([sys.executable, "-c",
                    "import importlib.util; assert importlib.util.find_spec('ocrun') is None; "
                    "import bits_core.collector.collector; import bits_core.collector.install"],
                    cwd=str(package), env=environment, timeout=10,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                assert result.returncode == 0, result.stderr
                assert not (package / "ocrun").exists()
                assert not source.environment_marker.exists() and not source.board_marker.exists()
                assert not (source.app / "mon-sensors-plugin").exists(), "Standalone use unexpectedly installed files"
            checked("standalone plugin archive directly collects without OCRUN imports or installation", standalone_plugin)

            def independent_api():
                api_package = temporary / "standalone-api"
                api_package.mkdir()
                api_archives = list((ARTIFACTS / "api").glob("ocrun-sckocp-api-*.tar.gz"))
                assert len(api_archives) == 1
                with tarfile.open(str(api_archives[0]), "r:gz") as bundle:
                    assert set(bundle.getnames()) == {"ocrun/__init__.py", "ocrun/sckocp.py", "SCKOCP.md", "SCKOCP-API.md",
                        "sckocp_api/__init__.py", "sckocp_api/interface.py", "sckocp_api/provider.py",
                        "sckocp_api/security.py"}
                    assert all(member.isfile() for member in bundle.getmembers())
                    bundle.extractall(str(api_package))
                for mode, status, code in (("ok", "ok", 0), ("denied", "license_denied", 1)):
                    write(chain.mode, mode)
                    result = subprocess.run([sys.executable, "-m", "ocrun.sckocp", "--binary", str(chain.provider)],
                                            cwd=str(api_package), env=chain.env, timeout=10,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                    assert result.returncode == code, result.stderr
                    assert len(result.stdout.splitlines()) == 1
                    response = json.loads(result.stdout)
                    assert response["schema"] == "ocrun-sckocp-v1" and response["status"] == status
                    assert (response["data"] is not None) == (status == "ok")
                    assert "fixture-private-license-code" not in result.stdout + result.stderr
                write(chain.mode, "ok")
                assert not chain.environment_marker.exists() and not chain.board_marker.exists()
                assert not (chain.app / "sckocp-collector").exists()
            checked("standalone API package returns licensed JSON without installing or calling mon-sensors", independent_api)

            public_directory = temporary / "public-api"
            public_directory.mkdir()
            public_archives = list((ARTIFACTS / "public-api").glob("sckocp-api-*.tar.gz"))
            assert len(public_archives) == 1
            with tarfile.open(str(public_archives[0]), "r:gz") as bundle:
                expected = {"sckocp-api", "SCKOCP-API.md", "examples/read-sckocp.py",
                            "install-sckocp-api.sh", "installer-python.sh", "sckocp_api/install.py",
                            "sckocp_api/bootstrap.py", "docs/API-SECURITY.md",
                            "sckocp_api/__init__.py", "sckocp_api/__main__.py",
                            "sckocp_api/interface.py", "sckocp_api/cli.py", "sckocp_api/provider.py",
                            "sckocp_api/security.py"}
                assert set(bundle.getnames()) == expected
                assert all(member.isfile() for member in bundle.getmembers())
                assert bundle.getmember("sckocp-api").mode & 0o111
                bundle.extractall(str(public_directory))

            api_source = fixture("public-caller")
            public_environment = dict(api_source.env)
            public_environment.pop("PYTHONPATH", None)

            def public_run(arguments, cwd=None, environment=None):
                return subprocess.run([str(value) for value in arguments],
                    cwd=str(cwd or public_directory), env=environment or public_environment, timeout=15,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

            def public_command():
                for mode, status, code in (("ok", "ok", 0), ("denied", "license_denied", 1),
                                           ("ok", "ok", 0)):
                    write(api_source.mode, mode)
                    result = public_run([public_directory / "sckocp-api", "--format", "v2", "--binary", api_source.provider], cwd=temporary)
                    assert result.returncode == code, result.stderr
                    assert len(result.stdout.splitlines()) == 1
                    response = json.loads(result.stdout)
                    assert response["schema"] == "sckocp-api-v1" and response["status"] == status
                    assert (response["data"] is not None) == (status == "ok")
                assert not (public_directory / "ocrun").exists()
                assert not api_source.environment_marker.exists()
            checked("public CLI works outside its directory without ocrun and rereads authorization each call", public_command)

            def unsafe_provider_paths():
                device = fixture("unsafe-provider-paths", "v1")
                device.install("sckocp")
                write(device.mode, "denied")
                for protected in (device.provider, device.bin):
                    protected.chmod(0o775)
                    try:
                        result = device.command([public_directory / "sckocp-api", "--binary", device.provider])
                        assert result.returncode == 1, result.stderr
                        response = json.loads(result.stdout)
                        assert response["status"] == "unsafe_executable" and response["data"] is None
                        assert "fixture-private-license-code" not in result.stdout + result.stderr
                        for label, command in (
                                ("plugin", [device.app / "mon-sensors-plugin"]),
                                ("bridge", ["/bin/bash", device.app / "mon-sensors"])):
                            logfile = device.logs / (protected.name + "-" + label + ".mon")
                            # No --once: security errors must bypass even the largest retry budget.
                            result = device.command(command + [".05", logfile, "--retries", "5"])
                            assert result.returncode == 1, result.stderr
                            frames = read_jsonl(Path(str(logfile) + ".sckocp.jsonl"))
                            assert len(frames) == 1 and len(device.rows(logfile)) == 3
                            assert frames[0]["provider"]["status"] == "unsafe_executable"
                            assert frames[0]["provider"]["data"] is None
                            assert device.rows(logfile)[2][5:] == [""] * 6
                            assert "fixture-private-license-code" not in result.stdout + result.stderr + json.dumps(frames)
                        assert not device.provider_pids.exists(), "Rejected provider code was executed"
                        assert not device.environment_marker.exists() and not device.board_marker.exists()
                    finally:
                        protected.chmod(0o755)
            checked("public API and installed plugin reject writable executables and parents before execution or retry", unsafe_provider_paths)

            def protected_monitoring_logs():
                device = fixture("protected-monitoring-logs", "v1")
                device.install("sckocp")
                logfile = device.logs / "protected.mon"
                sidecar = Path(str(logfile) + ".sckocp.jsonl")
                command = [device.app / "mon-sensors-plugin", ".05", logfile, "--once"]
                result = device.command(command)
                assert result.returncode == 0, result.stderr
                originals = {path: path.read_bytes() for path in (logfile, sidecar)}
                called = device.provider_pids.read_bytes()
                for path in (logfile, sidecar):
                    assert stat.S_IMODE(path.stat().st_mode) == 0o600
                    path.chmod(0o660)
                    try:
                        result = device.command(command)
                        assert result.returncode == 1, result.stderr
                        assert device.provider_pids.read_bytes() == called
                        assert all(value.read_bytes() == original for value, original in originals.items())
                        assert stat.S_IMODE(path.stat().st_mode) == 0o660, "Rejected file was silently chmodded"
                    finally:
                        path.chmod(0o600)
                alias = device.logs / "sidecar-hardlink"
                os.link(str(sidecar), str(alias))
                try:
                    result = device.command(command)
                    assert result.returncode == 1, result.stderr
                    assert device.provider_pids.read_bytes() == called
                    assert sidecar.read_bytes() == alias.read_bytes() == originals[sidecar]
                finally:
                    alias.unlink()
                write(sidecar, '{"private":"fixture-private-license-code-DO-NOT-EXPORT"}\n', 0o644)
                foreign = sidecar.read_bytes()
                result = device.command(command)
                assert result.returncode == 1, result.stderr
                assert sidecar.read_bytes() == foreign and stat.S_IMODE(sidecar.stat().st_mode) == 0o644
                assert logfile.read_bytes() == originals[logfile]
                assert device.provider_pids.read_bytes() == called
                assert "fixture-private-license-code" not in result.stdout + result.stderr
                sidecar.write_bytes(originals[sidecar])
                logfile.chmod(0o644)
                result = device.command(command)
                assert result.returncode == 0, result.stderr
                assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (logfile, sidecar))
                assert len(device.rows(logfile)) == 4 and len(read_jsonl(sidecar)) == 2
            checked("installed plugin protects existing logs from writable modes hardlinks and foreign sidecars", protected_monitoring_logs)

            def installed_transient_recovery():
                device = fixture("installed-transient-recovery", "v1")
                device.install("sckocp")
                write(device.mode, "transient")
                logfile = device.logs / "recovery.mon"
                sidecar = Path(str(logfile) + ".sckocp.jsonl")
                process = subprocess.Popen([str(device.app / "mon-sensors-plugin"), ".05", str(logfile),
                    "--retries", "1", "--retry-delay", ".1"], cwd=str(device.home), env=device.env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                try:
                    records = wait_for(lambda: read_jsonl(sidecar) if len(read_jsonl(sidecar)) >= 2 else None,
                                       "transient failure followed by recovered reading")
                    wait_for(lambda: len(device.rows(logfile)) >= 4, "failed and recovered CSV rows are flushed")
                    process.terminate()
                    stdout, stderr = process.communicate(timeout=5)
                    assert process.returncode == 0, stderr
                    assert [value["provider"]["status"] for value in records[:2]] == ["collection_failed", "ok"]
                    assert records[0]["provider"]["data"] is None
                    rows = device.rows(logfile)
                    assert rows[2][5:] == [""] * 6 and rows[3][5:7] == ["2500", "65"]
                    assert all(len(row) == 11 for row in rows[1:])
                    assert "fixture-private-license-code" not in stdout + stderr + sidecar.read_text()
                    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (logfile, sidecar))
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
            checked("installed plugin recovers from one transient failure without stale data or diagnostic leakage", installed_transient_recovery)

            def public_python():
                snippet = ("import importlib.util,json; assert importlib.util.find_spec('ocrun') is None; "
                           "from sckocp_api import collect; "
                           "print(json.dumps(collect(binary=" + repr(str(api_source.provider)) + ", native_format='v2')))")
                result = public_run([sys.executable, "-c", snippet])
                assert result.returncode == 0, result.stderr
                assert json.loads(result.stdout)["schema"] == "sckocp-api-v1"
                result = public_run([sys.executable, "-m", "sckocp_api", "--format", "v2", "--binary", api_source.provider])
                assert result.returncode == 0 and json.loads(result.stdout)["status"] == "ok", result.stderr
                environment = dict(public_environment, PYTHONPATH=str(public_directory))
                result = public_run([sys.executable, public_directory / "examples/read-sckocp.py",
                                     "--format", "v2", "--binary", api_source.provider], environment=environment)
                assert result.returncode == 0, result.stderr
                assert json.loads(result.stdout)["temperatures"][0]["temperature"] == 61.5
            checked("public Python SDK, module command and shipped example work with no ocrun installed", public_python)

            def public_import_does_not_collect():
                before = api_source.provider_pids.read_bytes()
                result = public_run([sys.executable, "-c", "import sckocp_api; import sckocp_api.cli"])
                assert result.returncode == 0 and not result.stdout, result.stderr
                result = public_run([public_directory / "sckocp-api", "--help"])
                assert result.returncode == 0
                result = public_run([public_directory / "sckocp-api", "--version"])
                assert result.returncode == 0 and result.stdout.strip() == "sckocp-api " + API_VERSION
                assert api_source.provider_pids.read_bytes() == before
            checked("public import, help and version do not start monitoring", public_import_does_not_collect)

            def public_stop():
                write(api_source.mode, "hang")
                before = len(read_jsonl(api_source.provider_pids))
                process = subprocess.Popen([str(public_directory / "sckocp-api"), "--format", "v2", "--binary", str(api_source.provider)],
                    cwd=str(temporary), env=public_environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    record = wait_for(lambda: next((value for value in read_jsonl(api_source.provider_pids)[before:]
                                                   if "helper" in value), None), "public CLI starts provider")
                    process.terminate()
                    stdout, stderr = process.communicate(timeout=5)
                    assert process.returncode == 143 and not stdout, stderr
                    wait_for(lambda: not live(record["provider"]) and not live(record["helper"]),
                             "public CLI cleans provider and helper")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
                    write(api_source.mode, "ok")
            checked("stopping the public CLI terminates its native provider and auxiliary process group", public_stop)

            checked("archive installs onto pristine scripts without modifying oc.env", chain.install)
            checked("overlay installer is idempotent", chain.install)

            def installed_command():
                device = fixture("installed-command", "v1")
                device.install("legacy")
                device.legacy_stubs()
                environment = dict(device.env, MON_SENSORS_BACKEND="legacy")
                environment.pop("PYTHONPATH", None)
                result = device.command([device.app / "mon-sensors-plugin", ".05", "view", "--once"],
                                        env=environment)
                assert result.returncode == 0 and "sckocp-v1" in result.stdout, result.stderr
                assert not device.environment_marker.exists() and not device.legacy_marker.exists()
                calls = len(read_jsonl(device.provider_pids))
                result = device.command(["/bin/bash", device.app / "mon-sensors", ".05", "view"], env=environment)
                assert result.returncode == 0 and device.legacy_marker.exists(), result.stderr
                assert len(read_jsonl(device.provider_pids)) == calls
                assert not (device.app / "mon-sensors-plugin.d" / "ocrun").exists()
            checked("installed plugin command runs independently while original mon-sensors preserves selected legacy behavior", installed_command)

            def entrypoint_collision():
                from bits_core.collector import install as plugin_install
                device = fixture("entrypoint-collision", "v1")
                before = {name: (device.app / name).read_bytes() for name in plugin_install.SCRIPTS}
                entry = device.app / "mon-sensors-plugin"
                protected = b"#!/bin/sh\nprintf 'customer command\\n'\n"
                entry.write_bytes(protected)
                result = device.command(["/bin/bash", package / "install-mon-sensors-plugin.sh", device.app])
                assert result.returncode != 0 and entry.read_bytes() == protected
                assert not (device.app / "mon-sensors-plugin.d").exists()
                assert all((device.app / name).read_bytes() == data for name, data in before.items())
                entry.unlink()
                target = device.home / "customer-command"
                target.write_bytes(protected)
                entry.symlink_to(target)
                result = device.command(["/bin/bash", package / "install-mon-sensors-plugin.sh", device.app])
                assert result.returncode != 0 and entry.is_symlink() and target.read_bytes() == protected
                assert all((device.app / name).read_bytes() == data for name, data in before.items())
                assert not (device.app / "mon-sensors-plugin.d").exists()
                entry.unlink()
                device.install()
                entry.write_bytes(protected)
                managed_before = {path: path.read_bytes() for path in device.app.rglob("*") if path.is_file()}
                result = device.command(["/bin/bash", package / "install-mon-sensors-plugin.sh", device.app])
                assert result.returncode != 0
                assert all(path.read_bytes() == data for path, data in managed_before.items())
            checked("customer commands, symlinks and locally modified plugin entrypoints are refused without overwrites", entrypoint_collision)

            def upgrade_standalone_provider():
                older = fixture("older-provider")
                older.install()
                helper = older.app / "mon-sensors-plugin.d"
                for path in (helper / "sckocp_api").iterdir():
                    path.unlink()
                (helper / "sckocp_api").rmdir()
                # Simulate an older managed helper without the newly extracted
                # shared package; installation must restore all dependencies.
                assert json.loads(older.install().stdout)["status"] == "installed"
                result = older.command(["/bin/bash", older.app / "mon-sensors", ".05", "view", "--once"])
                assert result.returncode == 0 and "sckocp-v2" in result.stdout, result.stderr
            checked("managed older adapter upgrades when the shared public API package is absent", upgrade_standalone_provider)

            def default_is_legacy():
                disabled = fixture("disabled")
                disabled.install()
                disabled.legacy_stubs()
                helper = disabled.app / "mon-sensors-plugin.d"
                helper.rename(disabled.app / "unused-helper")
                for backend in (None, "", "legacy"):
                    environment = dict(disabled.env)
                    if backend is None:
                        environment.pop("MON_SENSORS_BACKEND")
                    else:
                        environment["MON_SENSORS_BACKEND"] = backend
                    result = disabled.command(["/bin/bash", disabled.app / "mon-sensors", ".05", "view"],
                                              env=environment)
                    assert result.returncode == 0, result.stderr
                    assert not disabled.provider_pids.exists(), "Disabled provider was invoked"
                    assert not disabled.monitor_pids(), "Disabled provider launched a collector"
                assert disabled.legacy_marker.read_text().splitlines() == ["legacy called"] * 3
            checked("default, empty and legacy selections preserve legacy even with sckocp installed and helper absent", default_is_legacy)

            def starts_original_chain():
                chain.oct("mon")
                files = wait_for(lambda: list(chain.logs.glob("*.mon")), "oct creates original .mon filename")
                logfile = files[0]
                sidecar = Path(str(logfile) + ".sckocp.jsonl")
                records = wait_for(lambda: read_jsonl(sidecar), "first native JSON record")
                rows = wait_for(lambda: chain.rows(logfile) if len(chain.rows(logfile)) >= 3 else None,
                                "CSV first sample")
                assert rows[0][0].startswith("#-主机名:") and "provider=sckocp-v2" in rows[0][0]
                assert len(rows[1]) == len(rows[2]) == 11
                assert rows[2][0] == "sckocp-v2"
                assert rows[2][5:11] == ["2400", "61.5", "", "154.3", "1100", ""]
                assert records[0]["schema"] == "mon-sensors-sckocp-v1"
                assert records[0]["provider"]["status"] == "ok"
                assert not chain.board_marker.exists(), "sckocp path must not probe motherboard whitelist"
                assert not (chain.app / "fbin").exists(), "sckocp path must not require legacy functions"
                assert chain.environment_marker.read_text().splitlines() == ["loaded"]
                chain.logfile = logfile
                chain.sidecar = sidecar
            checked("oct mon uses native data on an unknown board and preserves eleven columns", starts_original_chain)

            def duplicate():
                result = chain.command(["/bin/bash", chain.app / "mon-sensors", ".05", chain.logfile, "--once"])
                assert result.returncode != 0, "Concurrent writer was not rejected"
                assert all(len(row) == 11 for row in chain.rows(chain.logfile)[1:])
            checked("duplicate writer is rejected without corrupting logs", duplicate)

            def graceful_stop():
                write(chain.mode, "hang")
                record = wait_for(lambda: next((entry for entry in read_jsonl(chain.provider_pids)
                                               if "helper" in entry), None), "blocking provider starts")
                monitors = wait_for(chain.monitor_pids, "monitor PID is identifiable")
                assert live(record["provider"]) and live(record["helper"])
                decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)",
                                          "unrelated-mon-sensors-log-consumer"], start_new_session=True)
                try:
                    chain.oct("killm")
                    wait_for(lambda: all(not live(pid) for pid in monitors + list(record.values())),
                             "TERM stops collector, native provider and separate helper group", timeout=6)
                    assert decoy.poll() is None, "killm stopped an unrelated command containing mon-sensors"
                finally:
                    if decoy.poll() is None:
                        decoy.terminate()
                    decoy.wait(timeout=5)
                assert all(len(row) == 11 for row in chain.rows(chain.logfile)[1:])
                shutil.copy2(chain.logfile, ARTIFACTS / "synthetic-example.mon")
                shutil.copy2(chain.sidecar, ARTIFACTS / "synthetic-example.mon.sckocp.jsonl")
            checked("oct killm gracefully stops an in-flight provider and its process group", graceful_stop)

            def stop_direct_plugin():
                device = fixture("stop-direct-plugin", "v1")
                device.install()
                write(device.mode, "hang")
                process = subprocess.Popen([str(device.app / "mon-sensors-plugin"), ".05", "view"],
                    cwd=str(device.home), env=device.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)",
                    str(device.app / "mon-sensors-plugin")], start_new_session=True)
                try:
                    record = wait_for(lambda: next((value for value in read_jsonl(device.provider_pids)
                                                   if "helper" in value), None), "direct plugin provider starts")
                    device.oct("killm")
                    stdout, stderr = process.communicate(timeout=6)
                    assert process.returncode == 0, stderr
                    wait_for(lambda: not live(record["provider"]) and not live(record["helper"]),
                             "direct plugin provider and helper stop")
                    assert decoy.poll() is None, "Exact plugin path used as an argument was mistaken for its command"
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
                    if decoy.poll() is None:
                        decoy.terminate()
                    decoy.wait(timeout=5)
            checked("oct killm stops the direct plugin and children while an unrelated process with its path survives", stop_direct_plugin)

            def analyzer(path, name):
                spec = importlib.util.spec_from_file_location(name, str(path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module

            def original_parser():
                module = analyzer(BASELINE / "mon-analyse-log.py", "original_monitor_analyzer")
                frame = module.process_data(module.read_file(str(chain.logfile)))
                assert len(frame) >= 1
                assert frame["主频"].eq(2400).all()
                assert frame["cpu温度"].eq(61.5).all()
                assert frame["cpu功耗"].eq(154.3).all()
                assert frame["整机功耗"].eq(1100).all()
                assert frame["VRM温度"].isna().all()
                assert frame["风扇转速"].isna().all()
            checked("original analyzer parses real monitor logs without converting missing values to zero", original_parser)

            def excel_export():
                from openpyxl import load_workbook
                module = analyzer(chain.app / "py" / "mon-analyse-log.py", "installed_monitor_analyzer")
                workbook_path = ARTIFACTS / "synthetic-example.xlsx"
                module.main(str(chain.logfile), str(workbook_path), "sckocp")
                workbook = load_workbook(str(workbook_path), data_only=True)
                try:
                    sheet = workbook["sckocp"]
                    assert sheet["F2"].value == 2400
                    assert sheet["G2"].value == 61.5
                    assert sheet["I2"].value == 154.3
                    assert sheet["J2"].value == 1100
                    assert sheet["H2"].value is None and sheet["K2"].value is None
                    assert sheet["R3"].value == 61.5 and sheet["T3"].value == 154.3
                    assert sheet["S3"].value is None and sheet["V3"].value is None
                    assert len(sheet._charts) == 9
                finally:
                    workbook.close()
            checked("installed analyzer exports numeric Excel readings, blank unavailable fields and charts", excel_export)

            def oct_analysis():
                from openpyxl import load_workbook
                chain.oct("analyse")
                output = list(chain.logs.glob("*.xlsx"))
                assert len(output) == 1, "oct analyse must produce its default report"
                workbook = load_workbook(str(output[0]), data_only=True)
                try:
                    sheet = workbook["Monitoring"]
                    assert sheet["G2"].value == 61.5 and sheet["I2"].value == 154.3
                    assert sheet["H2"].value is None and sheet["K2"].value is None
                finally:
                    workbook.close()
                shutil.copy2(output[0], ARTIFACTS / "synthetic-oct-report.xlsx")
            checked("original oct analyse exports with its existing two-argument invocation", oct_analysis)

            direct = fixture("direct")
            direct.install()

            def view():
                result = direct.command(["/bin/bash", direct.app / "mon-sensors", ".05", "view", "--once"])
                assert result.returncode == 0, result.stderr
                assert "sckocp-v2" in result.stdout and "61.5" in result.stdout
                assert not direct.environment_marker.exists(), "Direct sckocp dispatch loaded oc.env"
                assert not direct.board_marker.exists()
            checked("direct mon-sensors view works without the original environment or board helpers", view)

            def auto_detect():
                environment = dict(direct.env)
                environment["MON_SENSORS_BACKEND"] = "auto"
                environment.pop("SCKOCP_BINARY")
                result = direct.command(["/bin/bash", direct.app / "mon-sensors", ".05", "view", "--once"],
                                        env=environment)
                assert result.returncode == 0, result.stderr
                assert "sckocp-v2" in result.stdout
                assert not direct.environment_marker.exists()
            checked("explicit auto selection discovers sckocp on PATH", auto_detect)

            def denied():
                direct.legacy_stubs()
                write(direct.mode, "denied")
                output = direct.logs / "denied.mon"
                result = direct.command(["/bin/bash", direct.app / "mon-sensors", ".05", output, "--once"])
                assert result.returncode != 0
                assert direct.rows(output)[2][5:] == [""] * 6
                record = read_jsonl(Path(str(output) + ".sckocp.jsonl"))[0]
                assert record["provider"]["status"] == "license_denied"
                assert record["provider"]["data"] is None
                assert "fixture-private-license-code" not in (result.stdout + result.stderr + json.dumps(record))
                assert not direct.legacy_marker.exists(), "Authorization refusal must not use another backend"
            checked("activation refusal writes unavailable fields and safe status without fallback", denied)

            def malformed():
                write(direct.mode, "invalid")
                output = direct.logs / "invalid.mon"
                result = direct.command(["/bin/bash", direct.app / "mon-sensors", ".05", output, "--once"])
                assert result.returncode != 0
                assert direct.rows(output)[2][5:] == [""] * 6
                record = read_jsonl(Path(str(output) + ".sckocp.jsonl"))[0]
                assert record["provider"]["status"] == "invalid_data"
                assert "fixture-private-license-code" not in (result.stdout + result.stderr + json.dumps(record))
                assert not direct.legacy_marker.exists()
            checked("invalid provider output cannot become a successful or secret-bearing log", malformed)

            def auto_absent():
                environment = dict(direct.env)
                environment["MON_SENSORS_BACKEND"] = "auto"
                environment.pop("SCKOCP_BINARY")
                direct.provider.rename(direct.provider.with_name("disabled-provider"))
                result = direct.command(["/bin/bash", direct.app / "mon-sensors", ".05", "view"], env=environment)
                assert result.returncode == 0, result.stderr
                assert direct.legacy_marker.read_text().strip() == "legacy called"
            checked("auto mode retains original legacy route when sckocp is absent", auto_absent)

            def invalid_selection():
                invalid = fixture("invalid-selection")
                invalid.install()
                invalid.env["MON_SENSORS_BACKEND"] = "unknown"
                result = invalid.command(["/bin/bash", invalid.app / "mon-sensors"])
                assert result.returncode == 2
                assert not invalid.provider_pids.exists() and not invalid.environment_marker.exists()
            checked("invalid provider selection fails without invoking either backend", invalid_selection)

            def upgrade_previous_bridge():
                previous = fixture("upgrade")
                previous.install()
                previous.legacy_stubs()
                from bits_core.collector import install as legacy_install
                path = previous.app / "mon-sensors"
                text = path.read_text(encoding="utf-8")
                assert text.count(legacy_install.DISPATCH) == 1
                write(path, text.replace(legacy_install.DISPATCH, legacy_install.PREVIOUS_DISPATCH), 0o755)
                assert json.loads(previous.install().stdout)["status"] == "installed"
                assert json.loads(previous.install().stdout)["status"] == "already_installed"
                environment = dict(previous.env)
                environment.pop("MON_SENSORS_BACKEND")
                result = previous.command(["/bin/bash", path, ".05", "view"], env=environment)
                assert result.returncode == 0, result.stderr
                assert previous.legacy_marker.exists() and not previous.provider_pids.exists()
                result = previous.command(["/bin/bash", path, ".05", "view", "--once"])
                assert result.returncode == 0 and "sckocp-v2" in result.stdout, result.stderr
                environment["MON_SENSORS_BACKEND"] = "legacy"
                result = previous.command(["/bin/bash", path, ".05", "view"], env=environment)
                assert result.returncode == 0
                assert previous.legacy_marker.read_text().splitlines() == ["legacy called"] * 2
                assert len(read_jsonl(previous.provider_pids)) == 1
            checked("previous automatic bridge upgrades to default legacy and supports explicit switching both ways", upgrade_previous_bridge)

            def refuse_modified_bridge():
                modified = fixture("modified-bridge")
                modified.install()
                from bits_core.collector import install as legacy_install
                path = modified.app / "mon-sensors"
                text = path.read_text(encoding="utf-8").replace(legacy_install.DISPATCH,
                    legacy_install.PREVIOUS_DISPATCH.replace("${MON_SENSORS_BACKEND:-auto}", "custom-provider"))
                write(path, text, 0o755)
                result = modified.command(["/bin/bash", package / "install-mon-sensors-plugin.sh", modified.app])
                assert result.returncode != 0 and path.read_text(encoding="utf-8") == text
            checked("locally modified previous bridge is refused without overwriting it", refuse_modified_bridge)

            altered = fixture("altered")

            def unknown_baseline():
                write(altered.app / "oct", "#!/bin/bash\necho local customized script\n", 0o755)
                before = {name: (altered.app / name).read_bytes()
                          for name in ("oct", "ocb", "mon-sensors", "py/mon-analyse-log.py", "oc.env")}
                result = altered.command(["/bin/bash", package / "install-mon-sensors-plugin.sh", altered.app])
                assert result.returncode != 0, "Unsupported script shape was silently overwritten"
                for name, original in before.items():
                    assert (altered.app / name).read_bytes() == original, name + " changed after refusal"
                assert not (altered.app / "mon-sensors-plugin.d").exists()
                assert not (altered.app / "mon-sensors-plugin").exists()
            checked("unsupported local script layout is refused before any source modification", unknown_baseline)

            recovery = fixture("recovery")

            def installation_rollback():
                before = {name: (recovery.app / name).read_bytes()
                          for name in ("oct", "ocb", "mon-sensors", "py/mon-analyse-log.py", "oc.env")}
                spec = importlib.util.spec_from_file_location("bits_core.collector.packaged_install",
                                                              str(package / "bits_core/collector" / "install.py"))
                installer = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(installer)
                original_replace = os.replace
                failed = []

                def failing_replace(source, target):
                    if Path(target) == recovery.app / "oct" and not failed:
                        failed.append(True)
                        raise OSError("Synthetic single-write failure")
                    return original_replace(source, target)

                with mock.patch.object(installer.os, "replace", side_effect=failing_replace):
                    try:
                        installer.install(str(recovery.app), str(package))
                    except OSError:
                        pass
                    else:
                        raise AssertionError("Expected the injected installation failure")
                assert failed, "Failure injection did not exercise script activation"
                assert not (recovery.app / "mon-sensors-plugin.d").exists()
                assert not (recovery.app / "mon-sensors-plugin").exists()
                for name, original in before.items():
                    assert (recovery.app / name).read_bytes() == original, name + " was not rolled back"
                recovery.install()
            checked("a failed overlay activation restores scripts and permits a clean retry", installation_rollback)

            def entrypoint_write_rollback():
                from bits_core.collector import install as plugin_install
                device = fixture("entrypoint-write-rollback", "v1")
                before = {name: (device.app / name).read_bytes() for name in plugin_install.SCRIPTS}
                actual_atomic = plugin_install._atomic
                failed = []

                def fail_entry(path, data, mode):
                    if path == device.app / "mon-sensors-plugin" and not failed:
                        failed.append(True)
                        raise OSError("synthetic entrypoint write failure")
                    return actual_atomic(path, data, mode)

                with mock.patch.object(plugin_install, "_atomic", side_effect=fail_entry):
                    try:
                        plugin_install.install(str(device.app), str(package), "sckocp")
                    except OSError:
                        pass
                    else:
                        raise AssertionError("Expected entrypoint write to fail")
                assert failed
                assert all((device.app / name).read_bytes() == data for name, data in before.items())
                assert not (device.app / "mon-sensors-plugin").exists()
                assert not (device.app / "mon-sensors-plugin.d").exists()
                assert not (device.app / plugin_install.BACKEND_FILE).exists()
                device.install("sckocp")
                result = device.command([device.app / "mon-sensors-plugin", ".05", "view", "--once"])
                assert result.returncode == 0 and "sckocp-v1" in result.stdout, result.stderr
            checked("failed root plugin entrypoint activation rolls back modules and scripts before a successful retry", entrypoint_write_rollback)

            original = fixture("original-v1", "v1")

            def original_device_chain():
                original.install("sckocp")
                original.env.pop("MON_SENSORS_BACKEND")
                original.env.pop("SCKOCP_BINARY")
                # A fresh invocation has no source-selection environment. The
                # device setting survives and the original oct loads unchanged.
                assert "SCKOCP_FORMAT" not in original.env
                original.oct("mon")
                logfile = wait_for(lambda: next(iter(original.logs.glob("*.mon")), None), "v1 log")
                records = wait_for(lambda: read_jsonl(Path(str(logfile) + ".sckocp.jsonl")), "v1 sample")
                wait_for(lambda: len(original.rows(logfile)) >= 3, "v1 CSV sample")
                original.oct("killm")
                wait_for(lambda: not original.monitor_pids(), "v1 stopped")
                rows = original.rows(logfile)
                assert "quality=reported-validity-and-age-unknown" in rows[0][0]
                assert rows[2][0] == "sckocp-v1" and len(rows[2]) == 11
                assert rows[2][5:] == ["2500", "65", "", "0", "", ""]
                assert records[0]["provider"]["schema"] == "sckocp-api-v1"
                assert records[0]["provider"]["data"] == original_payload()
                assert records[0]["reading_quality"] == "reported-validity-and-age-unknown"
                assert not original.board_marker.exists()
                assert json.loads(original.install().stdout)["status"] == "already_installed"
                original.logfile = logfile
                shutil.copy2(logfile, ARTIFACTS / "synthetic-original-v1.mon")
                shutil.copy2(Path(str(logfile) + ".sckocp.jsonl"), ARTIFACTS / "synthetic-original-v1.mon.sckocp.jsonl")
            checked("0730 device persists opt-in and runs original v1 through public API without server or native changes", original_device_chain)

            def original_device_report():
                from openpyxl import load_workbook
                module = analyzer(BASELINE / "mon-analyse-log.py", "unchanged_server_parser")
                frame = module.process_data(module.read_file(str(original.logfile)))
                assert frame["主频"].eq(2500).all() and frame["cpu温度"].eq(65).all()
                assert frame["VRM温度"].isna().all() and frame["整机功耗"].isna().all()
                original.oct("analyse")
                report = next(iter(original.logs.glob("*.xlsx")))
                workbook = load_workbook(str(report), data_only=True)
                try:
                    sheet = workbook["Monitoring"]
                    assert sheet["F2"].value == 2500 and sheet["G2"].value == 65
                    assert sheet["I2"].value == 0  # Preserved raw report, not certified validity.
                    assert all(sheet[cell].value is None for cell in ("H2", "J2", "K2", "S3", "U3", "V3"))
                    assert len(sheet._charts) == 9
                finally:
                    workbook.close()
                shutil.copy2(report, ARTIFACTS / "synthetic-original-v1-report.xlsx")
            checked("unmodified server parser reads v1 and device-side Excel preserves unavailable columns", original_device_report)

            def original_upload():
                receiver = temporary / "unchanged-server-logs"
                receiver.mkdir()
                config = temporary / "rsync.conf"
                pidfile = temporary / "rsync.pid"
                write(config, "use chroot = false\npid file = " + str(pidfile) +
                      "\n[logs]\npath = " + str(receiver) + "\nread only = false\n" +
                      "uid = " + str(os.getuid()) + "\ngid = " + str(os.getgid()) +
                      "\nhosts allow = 127.0.0.1\n")
                diagnostics = temporary / "receiver.log"
                with diagnostics.open("w") as output:
                    daemon = subprocess.Popen(["sudo", "-n", "rsync", "--daemon", "--no-detach",
                        "--config=" + str(config), "--address=127.0.0.1", "--port=873"],
                        stdout=output, stderr=output)
                    try:
                        def ready():
                            assert daemon.poll() is None, diagnostics.read_text()
                            try:
                                with socket.create_connection(("127.0.0.1", 873), timeout=.2):
                                    return pidfile.exists()
                            except OSError:
                                return False
                        wait_for(ready, "isolated rsync receiver")
                        original.env["LOGSVR"] = "127.0.0.1"
                        for source in (original.logfile, Path(str(original.logfile) + ".sckocp.jsonl")):
                            assert stat.S_IMODE(source.stat().st_mode) == 0o600
                        original.oct("push")
                        for source in original.logs.iterdir():
                            copies = list(receiver.rglob(source.name))
                            assert len(copies) == 1 and copies[0].read_bytes() == source.read_bytes(), source.name
                            if source == original.logfile or source.name.endswith(".sckocp.jsonl"):
                                assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
                        # The sender's upload function remains byte-for-byte original.
                        def push_function(path):
                            return path.read_text().split("push-log() {", 1)[1].split("\n}", 1)[0]
                        assert push_function(original.app / "oct") == push_function(BASELINE / "oct")
                    finally:
                        if pidfile.exists():
                            pid = int(pidfile.read_text().strip())
                            assert pid > 1
                            subprocess.run(["sudo", "-n", "kill", "-TERM", str(pid)], timeout=5, check=False)
                        daemon.wait(timeout=10)
            checked("unchanged oct push uploads v1 log sidecar and device report byte-for-byte to real rsync receiver", original_upload)

            def original_amd_and_failures():
                amd = fixture("original-amd", "v1", amd=True)
                amd.install()
                output = amd.logs / "amd.mon"
                result = amd.command(["/bin/bash", amd.app / "mon-sensors", ".05", output, "--once"])
                assert result.returncode == 0, result.stderr
                assert amd.rows(output)[2][5:] == ["3000", "", "", "", "", ""]
                for mode, status in (("denied", "license_denied"), ("invalid", "invalid_data")):
                    write(amd.mode, mode)
                    failed = amd.logs / (mode + ".mon")
                    result = amd.command(["/bin/bash", amd.app / "mon-sensors", ".05", failed, "--once"])
                    assert result.returncode == 1 and amd.rows(failed)[2][5:] == [""] * 6
                    record = read_jsonl(Path(str(failed) + ".sckocp.jsonl"))[0]
                    assert record["provider"]["status"] == status and record["provider"]["data"] is None
                    assert "fixture-private-license-code" not in json.dumps(record) + result.stdout + result.stderr
                write(amd.mode, "ok")
                result = amd.command(["/bin/bash", amd.app / "mon-sensors", ".05", "view", "--once", "--format", "v2"])
                assert result.returncode == 1, "Explicit v2 must not fall back to original v1"
            checked("original AMD leaves absent sensors empty and denial or format mismatch never reuses readings", original_amd_and_failures)

            def device_switching():
                original.legacy_stubs()
                baseline_calls = len(read_jsonl(original.provider_pids))
                original.install("legacy")
                result = original.command(["/bin/bash", original.app / "mon-sensors", ".05", "view"])
                assert result.returncode == 0 and original.legacy_marker.exists()
                assert len(read_jsonl(original.provider_pids)) == baseline_calls
                original.install("sckocp")
                environment = dict(original.env, MON_SENSORS_BACKEND="legacy")
                result = original.command(["/bin/bash", original.app / "mon-sensors", ".05", "view"], env=environment)
                assert result.returncode == 0 and len(read_jsonl(original.provider_pids)) == baseline_calls
                result = original.command(["/bin/bash", original.app / "mon-sensors", ".05", "view", "--once"])
                assert result.returncode == 0 and "sckocp-v1" in result.stdout
                assert len(read_jsonl(original.provider_pids)) == baseline_calls + 1
            checked("persistent device selection switches both ways and explicit environment override takes precedence", device_switching)

            def upgrade_v2_bridge():
                from bits_core.collector import install as legacy_install
                older = fixture("v2-bridge", "v1")
                older.install()
                path = older.app / "mon-sensors"
                write(path, path.read_text().replace(legacy_install.DISPATCH, legacy_install.V2_DISPATCH), 0o755)
                assert json.loads(older.install("sckocp").stdout)["status"] == "installed"
                assert path.read_text().count(legacy_install.DISPATCH) == 1
                assert legacy_install.V2_BEGIN not in path.read_text()
            checked("previous opt-in v2 bridge upgrades to original-compatible device adapter", upgrade_v2_bridge)

            def upgrade_v3_bridge():
                from bits_core.collector import install as plugin_install
                device = fixture("v3-bridge", "v1")
                device.install("sckocp")
                device.env.pop("MON_SENSORS_BACKEND")
                mon = device.app / "mon-sensors"
                write(mon, mon.read_text().replace(plugin_install.DISPATCH, plugin_install.V3_DISPATCH), 0o755)
                oct_path = device.app / "oct"
                write(oct_path, oct_path.read_text().replace(
                    '"${APPPATH}/mon-sensors-plugin" --stop-app',
                    'python3 "${APPPATH}/sckocp-collector/mon-sensors" --stop-app'), 0o755)
                old_helper = device.app / "sckocp-collector"
                old_helper.mkdir()
                write(old_helper / "mon-sensors", "#!/usr/bin/env python3\nraise SystemExit('obsolete collector invoked')\n", 0o755)
                write(old_helper / ".ocrun-sckocp", '{"owner":"ocrun-sckocp-v1"}\n')
                retained = {path.name: path.read_bytes() for path in old_helper.iterdir()}
                (device.app / "mon-sensors-plugin").unlink()
                shutil.rmtree(str(device.app / "mon-sensors-plugin.d"))
                assert json.loads(device.install().stdout)["status"] == "installed"
                assert mon.read_text().count(plugin_install.DISPATCH) == 1
                assert plugin_install.V3_BEGIN not in mon.read_text()
                assert '${APPPATH}/mon-sensors-plugin" --stop-app' in oct_path.read_text()
                assert (device.app / plugin_install.BACKEND_FILE).read_bytes() == b"sckocp\n"
                assert {path.name: path.read_bytes() for path in old_helper.iterdir()} == retained
                result = device.command(["/bin/bash", mon, ".05", "view", "--once"])
                assert result.returncode == 0 and "sckocp-v1" in result.stdout, result.stderr
                assert json.loads(device.install().stdout)["status"] == "already_installed"
            checked("installed 0.12.5 v3 bridge migrates to the plugin while preserving selection and retired helper files", upgrade_v3_bridge)

            def configuration_rollback():
                from bits_core.collector import install as legacy_install
                device = fixture("config-rollback", "v1")
                device.install("legacy")
                config_path = device.app / legacy_install.BACKEND_FILE
                paths = [device.app / name for name in legacy_install.SCRIPTS]
                paths += [path for path in (device.app / "mon-sensors-plugin.d").rglob("*") if path.is_file()]
                paths.append(device.app / "mon-sensors-plugin")
                paths.append(config_path)
                before = {str(path): path.read_bytes() for path in paths}
                actual_atomic = legacy_install._atomic

                def fail_config(path, data, mode):
                    if path == config_path:
                        raise OSError("synthetic configuration write failure")
                    return actual_atomic(path, data, mode)

                with mock.patch.object(legacy_install, "_atomic", side_effect=fail_config):
                    try:
                        legacy_install.install(str(device.app), str(package), "sckocp")
                    except OSError:
                        pass
                    else:
                        raise AssertionError("Expected backend write to fail")
                assert all(Path(path).read_bytes() == data for path, data in before.items())
                device.install("sckocp")
                assert config_path.read_bytes() == b"sckocp\n"
                write(config_path, "sckocp; echo do-not-execute\n")
                environment = dict(device.env)
                environment.pop("MON_SENSORS_BACKEND")
                result = device.command(["/bin/bash", device.app / "mon-sensors", "--once"], env=environment)
                assert result.returncode == 2 and not device.provider_pids.exists()
            checked("failed device setting update restores modules scripts and selection; malformed settings are not executed", configuration_rollback)
    finally:
        for fixture_value in fixtures:
            fixture_value.close()
        report = {"successful": bool(results) and all(value["passed"] for value in results),
                  "checks": len(results), "duration_seconds": round(time.monotonic() - started, 3),
                  "python": sys.version, "hardware": "synthetic provider only",
                  "scope": "actual archived overlay, original oct mon/killm, CSV and JSON sidecar, backend routing, original parser and installed Excel report export",
                  "results": results}
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
