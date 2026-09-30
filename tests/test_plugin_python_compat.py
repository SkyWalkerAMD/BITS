"""Cloud Linux coverage for minimal EL8 Python and exact collector matching."""
import json
import fcntl
import os
from pathlib import Path
import shutil
import site
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from mon_sensors_plugin import collector
from mon_sensors_plugin import install as installer
from mon_sensors_plugin import runtime


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux launchers and proc semantics")
class PluginPythonCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="plugin-python-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_both_bootstraps_accept_only_platform_python_without_python3(self):
        source = self.root / "package with spaces"
        source.mkdir()
        platform = self.root / "platform-python"
        platform.symlink_to(sys.executable)
        for package in ("mon_sensors_plugin", "sckocp_api"):
            directory = source / package
            directory.mkdir()
            (directory / "__init__.py").write_text("")
            (directory / "install.py").write_text(
                "import json, os, sys\n"
                "print(json.dumps({'isolated': sys.flags.isolated, "
                "'no_bytecode': sys.flags.dont_write_bytecode, 'args': sys.argv[1:], "
                "'environment': [os.environ.get(name) for name in "
                "('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')]}))\n")
        # Override lookup only inside this test shell: never remove or replace
        # a system interpreter on the runner to simulate an EL8 installation.
        script = r'''
command() {
    if [[ $# == 3 && $1 == -v && $2 == -- ]]; then
        printf '%s\n' "$3" >> "$COMPAT_CANDIDATES"
        [[ $3 == /usr/libexec/platform-python ]] || return 1
        printf '%s\n' "$COMPAT_PLATFORM_PYTHON"
    else
        builtin command "$@"
    fi
}
source "$1"
installer_python "$2" "$3" --check
'''
        for package in ("mon_sensors_plugin", "sckocp_api"):
            with self.subTest(package=package):
                candidates = self.root / (package + ".candidates")
                env = dict(os.environ, COMPAT_PLATFORM_PYTHON=str(platform),
                           COMPAT_CANDIDATES=str(candidates), PYTHONPATH="untrusted",
                           PYTHONHOME="untrusted", PYTHONSTARTUP="untrusted")
                output = subprocess.check_output(
                    ["/bin/bash", "-c", script, "compat-check",
                     str(ROOT / "installer-python.sh"), package + ".install", str(source)],
                    env=env, cwd=str(self.root), stderr=subprocess.STDOUT, timeout=15)
                result = json.loads(output.decode("utf-8"))
                self.assertEqual(1, result["isolated"])
                self.assertEqual(1, result["no_bytecode"])
                self.assertEqual([None, None, None], result["environment"])
                self.assertEqual(["--source", str(source), "--check"], result["args"])
                attempts = candidates.read_text().splitlines()
                self.assertEqual("python3", attempts[0])
                self.assertEqual("/usr/libexec/platform-python", attempts[-1])

    def test_installed_launcher_runs_from_any_cwd_without_python_on_path(self):
        app = self.root / "device path" / "ocrun"
        app.mkdir(parents=True)
        (app / "py").mkdir()
        baseline = ROOT / "integrations" / "mon-sensors" / "upstream"
        for relative in installer.SCRIPTS:
            original = baseline / ("mon-analyse-log.py" if relative.startswith("py/") else relative)
            shutil.copyfile(str(original), str(app / relative))
            (app / relative).chmod(0o755)
        (app / "oc.env").write_text("exit 97 # must not be sourced\n")
        # Container CI can mount the checkout with a different host UID. The
        # fixture package must belong to the installing user just like a real
        # safely extracted package; do not relax production ownership checks.
        source = self.root / "source package"
        source.mkdir()
        for relative in installer.FILES:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
            target.chmod(0o755 if relative == installer.ENTRY else 0o644)
        installer.install(app, source, backend="legacy")
        poison = self.root / "poison"
        poison.mkdir()
        for package in ("mon_sensors_plugin", "sckocp_api"):
            (poison / package).mkdir()
            (poison / package / "__init__.py").write_text("raise RuntimeError('UNTRUSTED IMPORT')\n")
        env = dict(os.environ, PATH=str(poison), PYTHONPATH=str(poison),
                   PYTHONHOME=str(poison), PYTHONSTARTUP=str(poison / "startup"))
        hook = None
        marker = self.root / 'site-hook-executed'
        try:
            # Demonstrate a real global .pth hook is not run by the installed
            # launcher. Global fixture changes are confined to disposable CI.
            if os.geteuid() == 0 and os.environ.get('GITHUB_ACTIONS') == 'true':
                directory = next(Path(p) for p in site.getsitepackages() if Path(p).is_dir())
                hook = directory / ('ocrun-fixture-' + self.root.name + '.pth')
                with hook.open('x') as stream:
                    stream.write('import pathlib; pathlib.Path(' + repr(str(marker)) + ').touch()\n')
                subprocess.check_call([sys.executable, '-I', '-B', '-c', 'pass'])
                self.assertTrue(marker.exists(), 'Site-hook fault fixture did not execute')
                marker.unlink()
            output = subprocess.check_output(
                [str(app / installer.ENTRY), "--help"], cwd=str(poison), env=env,
                stderr=subprocess.STDOUT, timeout=15).decode("utf-8")
            self.assertFalse(marker.exists())
            # Background entry re-execs the supervisor; the no-site property
            # must survive that transition too. Occupy its application lock so
            # the actual entry takes that path but cannot start collecting.
            fd = runtime._open_lock(str(app), create=True)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                duplicate = subprocess.run([str(app / installer.ENTRY), '2', str(app / 'probe.mon'),
                    '--binary', '/nonexistent-sckocp'], cwd=str(poison), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                self.assertEqual(runtime.ALREADY_RUNNING, duplicate.returncode, duplicate.stderr)
                self.assertFalse((app / 'probe.mon').exists())
                self.assertFalse(marker.exists(), 'Background re-exec ran the global site hook')
            finally:
                os.close(fd)
        finally:
            if hook is not None:
                hook.unlink()
        self.assertIn("usage:", output)
        self.assertNotIn("UNTRUSTED IMPORT", output)
        self.assertFalse(list((app / installer.HELPER).rglob("__pycache__")))
        # Keep the Python source usable explicitly, with isolated mode too.
        source_output = subprocess.check_output(
            [sys.executable, "-I", "-B", str(ROOT / installer.ENTRY), "--help"],
            cwd=str(poison), env=env, stderr=subprocess.STDOUT, timeout=15).decode("utf-8")
        self.assertIn("usage:", source_output)

    def _process(self, proc, pid, argv):
        folder = proc / str(pid)
        folder.mkdir()
        (folder / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in argv) + b"\0")
        fields = ["S"] + ["0"] * 18 + ["12345"]
        (folder / "stat").write_text("{} (fixture) {}\n".format(pid, " ".join(fields)))

    def test_stop_recognizes_isolated_platform_python_only_for_exact_app(self):
        app = self.root / "ocrun"
        app.mkdir()
        proc = self.root / "proc"
        proc.mkdir()
        entry = str(app / installer.HELPER / installer.ENTRY)
        other = str(self.root / "other" / installer.HELPER / installer.ENTRY)
        commands = [
            ["/usr/libexec/platform-python", "-I", "-B", entry, "2"],
            ["/usr/libexec/platform-python3.6", "-I", "-B", entry, "2"],
            ["/usr/bin/python3.12", "-E", "-s", "-u", "--", entry, "2"],
            ["/bin/sh", str(app / installer.ENTRY), "2"],
            ["/usr/libexec/platform-python", "-I", "-B", other],
            ["/usr/libexec/platform-python", "-I", "-c", entry],
            ["/usr/libexec/platform-python", "-I", "-m", entry],
            ["/usr/libexec/platform-python", "-I", "-B", entry, "--stop-app", str(app)],
            ["/usr/bin/tee", entry],
            ["/bin/bash", "-c", entry],
            ["/usr/bin/not-platform-python", entry],
            ["/usr/libexec/platform-python", "-I"],
        ]
        for index, argv in enumerate(commands):
            self._process(proc, 800001 + index, argv)
        with mock.patch.object(os, "kill") as kill:
            self.assertEqual(0, collector.stop_monitors(str(app), str(proc)))
        self.assertEqual({(pid, signal.SIGTERM) for pid in range(800001, 800005)},
                         {call[0] for call in kill.call_args_list})


if __name__ == "__main__":
    unittest.main()
