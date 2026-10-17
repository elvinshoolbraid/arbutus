#!/usr/bin/env python3
"""#74: the state document is pushed after an edit, not after each one of a burst.

The host pushes `state` to every client after any edit (PROTOCOL.md § State).
It used to build that document after every request that edited something,
before the reply left: ~2-3 ms on the CM4, and ~14 KB for the client to parse.
A STOP or a SAVE is five to ten requests in a row, so on the CM4 every one of
them cost ~6 ms and a STOP's frame came to 70-95 ms against C6's 40 ms cap
(C1 step 3b's measurement, `arbutus_os/bench/c1-3b-pi.sh`). `main.cpp` now
pushes once no request has come for 8 ms, and at most 50 ms after the first
edit while they keep coming.

Pinned here:
  * a burst of edits gets fewer pushes than edits, and the last push carries
    the last edit (the promise "after any edit" still holds);
  * one edit and then quiet gets its push, promptly;
  * a client that never stops asking still gets pushes.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402


def states(c):
    return [p for p in c.pushes() if p.get("type") == "state"]


def test_a_burst_gets_fewer_pushes_and_the_last_carries_the_last_edit(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        h.c.drain(0.1)
        h.c.clear_pushes()
        n = 40
        for i in range(n):
            h.ok({"type": "tempo", "bpm": 100 + i})
        want = 100 + n - 1
        assert h.c.wait_push("state", where=lambda p: abs(p["tempo"] - want) < 1e-6), (
            "no push carried the last edit", states(h.c)[-1:])            # (#538)
        got = states(h.c)
        assert got, "no state push at all after %d edits" % n
        assert len(got) < n // 2, "%d pushes for %d edits in a burst" % (len(got), n)
        last = got[-1]["tempo"]
        assert abs(last - (100 + n - 1)) < 1e-6, "the last push says %r" % last
        ok("%d back-to-back edits, %d state pushes, the last at the last tempo"
           % (n, len(got)))
    return 0


def test_one_edit_then_quiet_is_pushed_promptly(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        h.c.drain(0.1)
        h.c.clear_pushes()
        h.ok({"type": "tempo", "bpm": 133})
        t0 = time.monotonic()
        h.c._sock.settimeout(0.5)
        try:
            while True:
                m = h.c.recv()
                if m.get("type") == "state":
                    break
        finally:
            h.c._sock.settimeout(h.c.timeout)
        dt = time.monotonic() - t0
        assert abs(m["tempo"] - 133) < 1e-6, m["tempo"]
        assert dt < 0.1, "the push came %.1f ms after the edit" % (dt * 1e3)
        ok("one edit, its push %.1f ms later" % (dt * 1e3))
    return 0


def test_a_client_that_never_stops_asking_still_gets_pushes(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        h.c.drain(0.1)
        h.c.clear_pushes()
        t_end = time.monotonic() + 0.3
        i = 0
        while time.monotonic() < t_end:
            h.ok({"type": "tempo", "bpm": 100 + (i % 50)})
            i += 1
            # --offline nothing drains the command ring but a render; without
            # one the host refuses edits `busy` past ~960 of them (#597).
            if i % 512 == 0:
                h.render(128)
        got = states(h.c)
        # one per 50 ms of asking, so ~6 in 0.3 s; a CI box may be slow
        assert len(got) >= 3, "%d pushes over %d edits in 0.3 s" % (len(got), i)
        ok("%d edits without a pause, %d state pushes on the way" % (i, len(got)))
    return 0


def main():
    a = args()
    for t in (test_a_burst_gets_fewer_pushes_and_the_last_carries_the_last_edit,
              test_one_edit_then_quiet_is_pushed_promptly,
              test_a_client_that_never_stops_asking_still_gets_pushes):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
