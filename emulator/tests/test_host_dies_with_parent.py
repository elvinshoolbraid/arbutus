"""A spawned `arbutus-audio` ends when the process that spawned it is killed (#517).

CI cancels a superseded gate (`concurrency: cancel-in-progress` in
`.github/workflows/gate.yml`) by killing the test process. `NativeAudio.close`
never runs, and the host, a socket server that nobody tells to stop, lived on
for days on the self-hosted runner: four of them from four cancelled runs.

The test spawns a host from a child Python process, kills that process with
SIGKILL (nothing of its own can run), and waits for the host to go. Linux only:
the parent-death signal is a Linux `prctl`, and the Mac's gate has no runner.
"""

import os
import signal
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # emulator/
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import native_audio as NA                                   # noqa: E402

# The child: start a host, print its pid, then wait to be killed.
CHILD = """
import sys, time
sys.path.insert(0, %r)
from app.native_audio import NativeAudio
n = NativeAudio(offline=True)
print(n.proc.pid, flush=True)
time.sleep(60)
""" % HERE


def _alive(pid: int) -> bool:
    """Is `pid` running? A zombie counts as gone (`/proc/<pid>/stat`'s state Z)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@unittest.skipUnless(sys.platform.startswith("linux"), "PR_SET_PDEATHSIG is Linux's")
@unittest.skipUnless(os.access(NA.EXE, os.X_OK), "no arbutus-audio built")
class TheHostDiesWithItsParent(unittest.TestCase):
    def test_a_killed_parent_takes_its_host_with_it(self):
        child = subprocess.Popen([sys.executable, "-c", CHILD],
                                 stdout=subprocess.PIPE, text=True)
        host = None
        try:
            line = child.stdout.readline()
            self.assertTrue(line.strip(), "the child started no host")
            host = int(line)
            self.assertTrue(_alive(host))
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)
            deadline = time.monotonic() + 5.0
            while _alive(host) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(_alive(host), "host %d outlived its parent" % host)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if host is not None and _alive(host):
                os.kill(host, signal.SIGKILL)       # by pid, never pkill (#517)


if __name__ == "__main__":
    unittest.main()
