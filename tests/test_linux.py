"""Runs only on Linux: harmless sleeping processes, no benchmark or hardware calls."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from ocrun.agent import stop_process
from ocrun.common import exclusive_lock


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires Linux process groups and flock")
class LinuxProcessTests(unittest.TestCase):
    def test_single_instance_lock_released_after_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "agent.lock")
            with exclusive_lock(path):
                with self.assertRaises(RuntimeError):
                    with exclusive_lock(path):
                        pass
            with exclusive_lock(path):
                pass

    def test_stop_only_own_process_group(self):
        own = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            stop_process(own, grace=1)
            self.assertIsNotNone(own.poll())
            self.assertIsNone(other.poll())
        finally:
            stop_process(own, grace=1)
            stop_process(other, grace=1)

    def test_unresponsive_parent_and_child_are_both_stopped(self):
        child_code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
        parent_code = ("import signal,subprocess,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                       "p=subprocess.Popen([sys.executable,'-c',sys.argv[1]],stdout=subprocess.PIPE); "
                       "p.stdout.readline(); print(p.pid,flush=True); time.sleep(60)")
        own = subprocess.Popen([sys.executable, "-c", parent_code, child_code], start_new_session=True,
                               stdout=subprocess.PIPE, universal_newlines=True)
        try:
            child_pid = int(own.stdout.readline().strip())
            stop_process(own, grace=0.2)
            self.assertEqual(-signal.SIGKILL, own.returncode)
            deadline = time.monotonic() + 2
            while True:
                try:
                    with open("/proc/{}/stat".format(child_pid)) as stream:
                        state = stream.read().split()[2]
                    if state == "Z":
                        break
                except FileNotFoundError:
                    break
                self.assertLess(time.monotonic(), deadline, "Workload child remained alive")
                time.sleep(0.02)
        finally:
            stop_process(own, grace=0.2)
            own.stdout.close()
