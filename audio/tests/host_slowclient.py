#!/usr/bin/env python3
"""P9 (#89): a client that stops reading stalls nobody but itself.

Every push goes to every client (`main.cpp`'s event loop), so a client that
connects and then stops reading has the host writing into a socket that fills.
With blocking sockets that write blocked the host's whole control loop, and
with it every other client: on the CM4 the UI's `note` went unanswered for
8.7 s, until the bench script that had stopped reading started again. A phone
on TCP 7401 whose Wi-Fi drops without a reset is the same client in the wild.

Pinned here:
  * with a silent client holding a full socket, another client's requests are
    still answered, promptly;
  * the silent client's backlog is bounded: past `kMaxPending` it is dropped,
    said once in the log.
"""

import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402


def silent_client(path):
    """Connect, say hello, and never read again. A small receive buffer so
    the socket fills in a few pushes rather than a few hundred."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.connect(path)
    s.sendall(b'{"type":"hello","proto":1,"abi":2,"firmware":"silent"}\n')
    return s


def test_a_silent_client_does_not_stall_the_others(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        s = silent_client(h.sock)
        t_start = time.monotonic()
        worst = 0.0
        # Each tempo change pushes the whole `state` document to every client,
        # the silent one included: a few hundred of them is megabytes.
        for i in range(600):
            t0 = time.monotonic()
            h.ok({"type": "tempo", "bpm": 100 + (i % 50)})
            h.render(128)                # --offline: the ring drains here
            worst = max(worst, time.monotonic() - t0)
        total = time.monotonic() - t_start
        assert worst < 1.0, "a request waited %.2f s behind a silent client" % worst
        ok("600 requests answered beside a client that never reads "
           "(worst %.1f ms, %.2f s in all)" % (worst * 1e3, total))
        s.close()
    return 0


def test_the_backlog_is_bounded(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        s = silent_client(h.sock)
        state_size = len(json.dumps(h.c.state(), separators=(",", ":")))
        # Past 8 MiB waiting, the host lets the client go; push 12 so the
        # kernel's own buffering cannot keep it under.
        n = (12 << 20) // max(1, state_size)
        for i in range(n):
            h.c.clear_pushes()
            h.ok({"type": "tempo", "bpm": 100 + (i % 50)})
            h.render(128)
            # one push per edit: since #74 the host sends `state` once a burst
            # of requests settles, so wait for it before the next edit or the
            # loop is one burst and one push
            while not any(p.get("type") == "state" for p in h.c.pushes()):
                h.c._pushes.append(h.c.recv())
        h.c.drain(0.3)
        h.log.flush()
        h.log.seek(0)
        log = h.log.read()
        assert "dropping a client that stopped reading" in log, log[-2000:]
        # And the drop is a close: reading what did arrive ends in EOF.
        s.settimeout(5.0)
        while s.recv(65536):
            pass
        ok("a client past 8 MiB of unread pushes is dropped (%d pushes of "
           "~%d bytes), and the host goes on" % (n, state_size))
        s.close()
        h.ok({"type": "tempo", "bpm": 120})
    return 0


def main():
    a = args()
    for t in (test_a_silent_client_does_not_stall_the_others,
              test_the_backlog_is_bounded):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
