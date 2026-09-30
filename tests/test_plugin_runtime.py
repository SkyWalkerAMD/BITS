"""Real Linux process/lock isolation; execute only in the cloud test matrix."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from mon_sensors_plugin import runtime


RUNTIME = str(Path(runtime.__file__).resolve())


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux flock and /proc required")
class PluginRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "ocrun"
        self.app.mkdir()
        self.processes = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def spawn(self, command, **kwargs):
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, **kwargs)
        self.processes.append(process)
        return process

    def wait_file(self, path, process, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size:
                return path.read_text()
            if process.poll() is not None:
                self.fail("Supervisor exited before readiness: {!r}".format(process.communicate()))
            time.sleep(0.02)
        self.fail("No readiness file: " + str(path))

    def worker(self, app=None, stubborn=False, grandchild=False):
        app = app or self.app
        script = app / "worker.py"
        ready = app / "ready"
        source = "import os, signal, subprocess, sys, time\n"
        if stubborn:
            source += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        if grandchild:
            source += (
                "child = subprocess.Popen([sys.executable, '-c', "
                "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'])\n"
                "with open({!r}, 'w') as stream: stream.write(str(child.pid))\n"
            ).format(str(app / "grandchild"))
        source += "with open({!r}, 'w') as stream: stream.write(str(os.getpid()))\n".format(str(ready))
        source += "while True: time.sleep(0.2)\n"
        script.write_text(source)
        return script, ready

    def run_worker(self, app=None, **options):
        app = app or self.app
        script, ready = self.worker(app, **options)
        process = self.spawn([sys.executable, "-I", "-S", "-B", RUNTIME,
                              "--run", str(app), "--", sys.executable, "-I", "-B", str(script)])
        self.wait_file(ready, process)
        return process

    def assert_not_running(self, pid, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fields = Path("/proc/{}/stat".format(pid)).read_text().rsplit(")", 1)[1].split()
                if fields[0] == "Z":
                    return  # An orphan zombie cannot keep collecting or hold FDs.
            except FileNotFoundError:
                return
            time.sleep(0.02)
        self.fail("Process remains alive: {}".format(pid))

    def test_environment_flag_cannot_bypass_guard(self):
        self.assertFalse(runtime.guard_valid(str(self.app), {runtime.FD_ENV: "999999"}))
        self.assertFalse(runtime.guard_valid(str(self.app), {runtime.FD_ENV: "true"}))

    def test_unlocked_inherited_descriptor_does_not_create_a_guard(self):
        fd = runtime._open_lock(str(self.app), create=True)
        try:
            self.assertFalse(runtime.guard_valid(str(self.app), {runtime.FD_ENV: str(fd)}))
            probe = runtime._open_lock(str(self.app))
            try:
                # Checking must not accidentally leave a lock on the inherited
                # description after the temporary independent probe is closed.
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)
        finally:
            os.close(fd)

    def test_only_the_inherited_locked_description_is_accepted(self):
        fd = runtime._open_lock(str(self.app), create=True)
        other = runtime._open_lock(str(self.app))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            command = [sys.executable, "-I", "-B", RUNTIME, "--check", str(self.app)]
            env = os.environ.copy()
            env[runtime.FD_ENV] = str(fd)
            accepted = subprocess.run(command, env=env, pass_fds=(fd,),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertEqual(0, accepted.returncode, accepted.stderr)
            env[runtime.FD_ENV] = str(other)
            rejected = subprocess.run(command, env=env, pass_fds=(other,),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertEqual(runtime.NEEDS_SUPERVISOR, rejected.returncode, rejected.stderr)
            # The check never unlocks the supervising file description.
            with self.assertRaises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
            os.close(fd)

    def test_other_application_descriptor_does_not_validate(self):
        other_app = self.root / "other"
        other_app.mkdir()
        fd = runtime._open_lock(str(other_app), create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(runtime.guard_valid(str(self.app), {runtime.FD_ENV: str(fd)}))
        finally:
            os.close(fd)

    def test_duplicate_launch_is_rejected_but_other_app_runs(self):
        first = self.run_worker()
        before = (self.app / "ready").read_text()
        duplicate = subprocess.run(
            [sys.executable, "-I", "-B", RUNTIME, "--run", str(self.app), "--",
             sys.executable, "-c", "raise SystemExit(99)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(runtime.ALREADY_RUNNING, duplicate.returncode, duplicate.stderr)
        self.assertEqual(before, (self.app / "ready").read_text())
        other = self.root / "other"
        other.mkdir()
        second = self.run_worker(other)
        self.assertTrue(runtime.stop_runtime(str(self.app)))
        first.wait(timeout=3)
        self.assertIsNone(second.poll())
        self.assertTrue(runtime.stop_runtime(str(other)))
        second.wait(timeout=3)

    def test_installation_and_monitor_start_are_mutually_exclusive(self):
        path = self.app / ".mon-sensors-install.lock"
        install_fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(install_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rejected = subprocess.run(
                [sys.executable, "-I", "-B", RUNTIME, "--run", str(self.app), "--",
                 sys.executable, "-c", "raise SystemExit(99)"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertEqual(1, rejected.returncode, rejected.stderr)
            self.assertIn(b"installation is in progress", rejected.stderr)
            fcntl.flock(install_fd, fcntl.LOCK_UN)
            process = self.run_worker()
            with self.assertRaises(BlockingIOError):
                fcntl.flock(install_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(runtime.stop_runtime(str(self.app)))
            process.wait(timeout=3)
            fcntl.flock(install_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(install_fd)

    def test_shell_bridge_reentry_keeps_lock_without_infinite_recursion(self):
        worker, ready = self.worker()
        bridge = self.app / "mon-sensors"
        quote = shlex.quote
        bridge.write_text(
            "#!/bin/bash\n"
            "if {python} -I -B {runtime} --check {app}; then\n"
            "  :\n"
            "else\n"
            "  rc=$?\n"
            "  [ \"$rc\" = 3 ] || exit \"$rc\"\n"
            "  exec {python} -I -B {runtime} --run {app} -- /bin/bash \"$0\" \"$@\"\n"
            "fi\n"
            "exec {python} -I -B {worker}\n".format(
                python=quote(sys.executable), runtime=quote(RUNTIME), app=quote(str(self.app)),
                worker=quote(str(worker))))
        process = self.spawn(["/bin/bash", str(bridge), "2", str(self.app / "test.mon")])
        self.wait_file(ready, process)
        self.assertTrue(runtime.stop_runtime(str(self.app)))
        self.assertEqual(143, process.wait(timeout=3))

    def test_installed_entry_shares_guard_and_stops_its_supervisor(self):
        source = Path(RUNTIME).parents[1]
        helper = self.app / "mon-sensors-plugin.d"
        helper.mkdir()
        for package in ("mon_sensors_plugin", "sckocp_api"):
            shutil.copytree(str(source / package), str(helper / package),
                            ignore=shutil.ignore_patterns("__pycache__"))
        # Hold open the real cleanup-to-exit window after the lock is released
        # and signal handlers restored. The old legacy fallback re-signalled
        # this already-cleaned supervisor, turning its intended 143 into -15.
        runtime_copy = helper / 'mon_sensors_plugin/runtime.py'
        cleanup = ('        for signum, handler in previous.items():\n'
                   '            signal.signal(signum, handler)\n')
        code = runtime_copy.read_text()
        self.assertEqual(1, code.count(cleanup))
        runtime_copy.write_text(code.replace(cleanup, cleanup + '        time.sleep(0.4)\n'))
        entry = helper / "mon-sensors-plugin"
        shutil.copyfile(str(source / "mon-sensors-plugin"), str(entry))
        worker, ready = self.worker()
        supervised = self.spawn([sys.executable, "-I", "-B", str(entry),
                                 "--guard-run", str(self.app), "--",
                                 sys.executable, "-I", "-B", str(worker)])
        self.wait_file(ready, supervised)
        logfile = self.app / "must-not-exist.mon"
        duplicate = subprocess.run(
            [sys.executable, "-I", "-B", str(entry), "2", str(logfile),
             "--binary", "/nonexistent-sckocp"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(runtime.ALREADY_RUNNING, duplicate.returncode, duplicate.stderr)
        self.assertFalse(logfile.exists())
        stopped = subprocess.run(
            [sys.executable, "-I", "-B", str(entry), "--stop-app", str(self.app)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
        self.assertEqual(0, stopped.returncode, stopped.stderr)
        self.assertEqual(143, supervised.wait(timeout=3))

    def test_term_escalates_for_owned_group_and_releases_lock(self):
        process = self.run_worker(stubborn=True, grandchild=True)
        child_pid = int((self.app / "ready").read_text())
        grandchild_pid = int(self.wait_file(self.app / "grandchild", process))
        started = time.monotonic()
        self.assertTrue(runtime.stop_runtime(str(self.app), timeout=8))
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(143, process.wait(timeout=3))
        self.assert_not_running(child_pid)
        self.assert_not_running(grandchild_pid)
        fd = runtime._open_lock(str(self.app))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_graceful_backend_stop_preserves_success_returncode(self):
        script = self.app / "graceful-worker.py"
        ready = self.app / "ready"
        cleaned = self.app / "cleaned"
        script.write_text(
            "import os, signal, time\n"
            "def stop(signum, frame):\n"
            "    raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "try:\n"
            "    with open({ready!r}, 'w') as stream: stream.write(str(os.getpid()))\n"
            "    while True: time.sleep(0.2)\n"
            "finally:\n"
            "    with open({cleaned!r}, 'w') as stream: stream.write('closed')\n".format(
                ready=str(ready), cleaned=str(cleaned)))
        process = self.spawn([sys.executable, "-I", "-B", RUNTIME,
                              "--run", str(self.app), "--", sys.executable, "-I", "-B", str(script)])
        # Readiness is written only after the backend installed its handler.
        self.wait_file(ready, process)
        process.terminate()
        self.assertEqual(0, process.wait(timeout=8))
        self.assertEqual("closed", cleaned.read_text())
        self.assertFalse(runtime.stop_runtime(str(self.app)))

    def test_lock_survives_normal_child_exit_for_descendant_cleanup(self):
        script = self.app / "exit-parent.py"
        descendant_file = self.app / "descendant"
        script.write_text(
            "import subprocess,sys\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'])\n"
            "with open({!r}, 'w') as stream: stream.write(str(child.pid))\n".format(str(descendant_file)))
        process = self.spawn([sys.executable, "-I", "-B", RUNTIME, "--run", str(self.app),
                              "--", sys.executable, "-I", "-B", str(script)])
        pid = int(self.wait_file(descendant_file, process))
        self.assertEqual(0, process.wait(timeout=8))
        self.assert_not_running(pid)

    def test_stale_metadata_does_not_signal_any_process(self):
        lock = self.app / runtime.LOCK_NAME
        lock.write_text(json.dumps({"pid": os.getpid(), "app": str(self.app)}))
        lock.chmod(0o600)
        with mock.patch.object(runtime.os, "kill") as kill:
            self.assertFalse(runtime.stop_runtime(str(self.app)))
            kill.assert_not_called()

    def test_unknown_held_lock_is_not_forcefully_stopped(self):
        fd = runtime._open_lock(str(self.app), create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(fd, b'{"version": 1, "pid": 1}\n')
            with mock.patch.object(runtime.os, "kill") as kill:
                with self.assertRaises(ValueError):
                    runtime.stop_runtime(str(self.app))
                kill.assert_not_called()
        finally:
            os.close(fd)

    def test_symlink_hardlink_and_public_lock_are_refused(self):
        target = self.root / "target"
        target.write_text("untouched")
        target.chmod(0o600)
        lock = self.app / runtime.LOCK_NAME
        lock.symlink_to(target)
        with self.assertRaises(OSError):
            runtime._open_lock(str(self.app), create=True)
        lock.unlink()
        os.link(str(target), str(lock))
        with self.assertRaises(ValueError):
            runtime._open_lock(str(self.app), create=True)
        lock.unlink()
        lock.write_text("untouched")
        lock.chmod(0o644)
        with self.assertRaises(ValueError):
            runtime._open_lock(str(self.app), create=True)
        self.assertEqual("untouched", lock.read_text())
        self.assertEqual("untouched", target.read_text())

    def test_process_identity_mismatch_is_not_signalled(self):
        process = self.run_worker()
        lock = self.app / runtime.LOCK_NAME
        record = json.loads(lock.read_text())
        record["start"] = "0"
        lock.write_text(json.dumps(record))
        with mock.patch.object(runtime.os, "kill") as kill:
            with self.assertRaises(ValueError):
                runtime.stop_runtime(str(self.app))
            kill.assert_not_called()
        self.assertIsNone(process.poll())


if __name__ == "__main__":
    unittest.main()
