#!/usr/bin/env python3
"""#1226: a test's host ends when the test process is killed outright.

A gate cancelled by the bench host's CI runner killed its ctest step mid-run;
`host_exit.py`'s Python died where it stood and its `Host`, a socket server
nobody told to stop, ran on for a day (six of them, from five gates). Here a
child Python starts a `Host`, is killed with SIGKILL (nothing of its own can
run), and the host must be gone within 5 s. Linux only: the parent-death
signal is a Linux `prctl`.
"""

import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import args, ok                                      # noqa: E402

# The child: start a host, print its pid, then wait to be killed.
CHILD = """
import sys, time
sys.path.insert(0, %r)
from hostcase import Host
h = Host(sys.argv[1], [sys.argv[2]])
print(h.proc.pid, flush=True)
time.sleep(60)
""" % os.path.dirname(os.path.abspath(__file__))


def alive(pid):
    """Is `pid` running? A zombie counts as gone (`/proc/<pid>/stat`'s state Z)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def main():
    a = args()
    if not sys.platform.startswith("linux"):
        ok("skipped: PR_SET_PDEATHSIG is Linux's")
        return 0
    child = subprocess.Popen([sys.executable, "-c", CHILD, a.exe, a.boxes],
                             stdout=subprocess.PIPE, text=True)
    host = None
    try:
        line = child.stdout.readline()
        assert line.strip(), "the child started no host"
        host = int(line)
        assert alive(host)
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=5)
        deadline = time.monotonic() + 5.0
        while alive(host) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not alive(host), "host %d outlived its killed test" % host
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if host is not None and alive(host):
            os.kill(host, signal.SIGKILL)           # by pid, never pkill (#517)
    ok("a SIGKILLed test's host exits with it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
