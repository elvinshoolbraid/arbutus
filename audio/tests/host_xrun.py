#!/usr/bin/env python3
"""The CPU flag (§6.8): a box that misses the block budget must raise `xrun`."""

import sys
import time

from hostcase import Host, args, ok


def main():
    a = args()
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        r = h.ok({"type": "load_rack", "rack": {
            "format": 1, "kind": "rack", "name": "SLOW",
            "tabs": [{"instance": "spin", "engine": "arbtest/spin",
                      "params": {"load": 0.05}}]}})
        assert r["type"] == "ok", r
        h.note(60, True, 0.9)
        h.c.clear_pushes()
        h.render(2048)                      # four blocks, each way over budget
        h.c.wait_push("xrun")              # a ceiling: drain() stopped at 50 ms (#538)
        xruns = h.c.pushes("xrun")
        assert xruns, "an engine that spins for 20 ms a block raised no xrun"
        assert xruns[-1]["count"] >= 1 and xruns[-1]["window_s"] > 0, xruns[-1]
        ok("xrun: %d blocks over budget, window %.1f s, flag %s" %
           (xruns[-1]["count"], xruns[-1]["window_s"], xruns[-1]["flag"]))

        # #1131: the push that reaches the threshold (3 in the window,
        # `kXrunFlagCount`) is the one that raises the CPU flag. Four blocks
        # over make three in one window unless a 2 s boundary fell among them:
        # then four more.
        third = lambda p: p["count"] >= 3
        hit = h.c.wait_push("xrun", where=third)
        if hit is None:
            h.render(2048)
            hit = h.c.wait_push("xrun", where=third)
        assert hit is not None, h.c.pushes("xrun")
        assert hit["flag"] is True, hit
        for p in h.c.pushes("xrun"):
            assert p["flag"] == (p["count"] >= 3), p
        ok("xrun: the push at %d in the window carries flag true" % hit["count"])

        # #865: the state's `count` is the count inside the current window,
        # which restarts every `window_s` of wall time, so a state read that
        # lands past a boundary honestly says 0. The read is put past one on
        # purpose (a wait this test means: offline nothing renders while it
        # sleeps, so no xrun can join) and the total carries the flag's cause.
        time.sleep(xruns[-1]["window_s"] + 0.5)
        st = h.c.state()
        assert st["xrun"].get("total", 0) >= hit["count"], st["xrun"]
        # The window restarted with nothing in it, the total held (#1131).
        assert st["xrun"]["count"] == 0 and st["xrun"]["flag"] is False, st["xrun"]
        assert st["xrun"]["window_s"] == xruns[-1]["window_s"], st["xrun"]
        ok("xrun: the state document carries the total past its window "
           "(%d now, %d in all)" % (st["xrun"]["count"], st["xrun"]["total"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
