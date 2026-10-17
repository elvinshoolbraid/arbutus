#!/usr/bin/env python3
"""Sidechain for MOD tabs, the `self` and `master` sources, and the motion feed
(modulation-spec §4, §13; engine-spec §6.6).

`arbtest/sidemod` emits side[0][0] (FIRST), side[1][last] (LAST), motion[0]
(AX) and whether side[] is wired (HAS). `arbtest/abi2` at a flat envelope makes
the instrument bus a DC level equal to its LEVEL knob, so the value a route
reads back names the block and the bus it came from. Routes go to abi2's RATE,
which does not touch the sound, so nothing feeds back.
"""

import sys
import time

from hostcase import Host, args, ok

TOL = 1e-4
FLAT = {"a": 0.0, "d": 0.0, "s": 1.0, "r": 0.0, "mode": "poly"}
BLOCK = 512


def patch(level, tab, routes, fx=None):
    p = {"format": 1, "kind": "patch", "name": "SIDE",
         "engine": {"id": "arbtest/abi2", "params": {"level": level}}, "env": FLAT,
         "mod": {"format": 1, "kind": "modrack", "tabs": [tab], "routes": routes, "bypass": False}}
    if fx:
        p["fx"] = fx
    return p


def route(output, depth=1.0):
    return {"source": "s", "output": output, "target": "engine.rate", "depth": depth, "curve": "lin"}


def live_after(h, frames):
    """Render `frames` and return the newest meters `mod` map: the last block's routes."""
    h.c.clear_pushes()
    time.sleep(0.05)                 # meters are paced by wall clock (<= 30 Hz)
    h.render(frames)
    h.c.wait_push("meters")    # one per offline render; not 50 ms of quiet (#538)
    h.c.state()                # and in before this reply, so [-1] is the render's
    ms = h.c.pushes("meters")
    assert ms, "no meters push"
    return ms[-1].get("mod", {})


def near(a, b, what):
    assert abs(a - b) <= TOL, "%s: got %.6f, want %.6f" % (what, a, b)


def main():
    a = args()

    # --- self: the previous block of this instrument's bus ---------------------
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        tab = {"instance": "s", "engine": "arbtest/sidemod", "params": {}, "sidechain": "self"}
        h.ok({"type": "load_patch", "patch": patch(0.2, tab, [route("first")])})
        h.ok({"type": "meters", "on": True, "hz": 30})
        h.note(60, True, 1.0)
        near(live_after(h, 4 * BLOCK)["engine.rate"], 0.7, "self, steady")          # .5 + .2
        ok("self: a steady bus of 0.2 reads 0.2 (rate 0.7)")

        h.ok({"type": "set_param", "slot": "engine", "idx": 0, "value": 0.6})
        near(live_after(h, BLOCK)["engine.rate"], 0.7, "self, first block after the change")
        near(live_after(h, BLOCK)["engine.rate"], 1.0, "self, second block")        # .5 + .6 clamps
        ok("self: LEVEL 0.2 -> 0.6 is seen one block late (0.7, then 1.0)")

        # LAST is the right channel's final frame of the same previous block.
        h.ok({"type": "unroute", "index": 0})
        h.ok({"type": "route", "source": "s", "output": "last", "target": "engine.rate", "depth": 0.5})
        h.ok({"type": "set_param", "slot": "engine", "idx": 0, "value": 0.4})
        near(live_after(h, BLOCK)["engine.rate"], 0.8, "self, last frame, old block")  # .5 + .5*.6
        near(live_after(h, BLOCK)["engine.rate"], 0.7, "self, last frame, new block")  # .5 + .5*.4
        ok("self: both channels, the whole previous block (LAST 0.6 then 0.4)")

        # --- master: after the master gain ------------------------------------
        r = h.ok({"type": "set_mod_sidechain", "index": 0, "source": "master"})
        assert r["type"] == "mod" and r["mod"]["tabs"][0]["sidechain"] == "master", r["mod"]
        h.ok({"type": "mixer", "master": 0.5})
        live_after(h, 2 * BLOCK)
        near(live_after(h, BLOCK)["engine.rate"], 0.6, "master at gain 0.5")         # .5 + .5*.2
        h.ok({"type": "set_param", "slot": "engine", "idx": 0, "value": 0.8})
        near(live_after(h, BLOCK)["engine.rate"], 0.6, "master, one block late")
        near(live_after(h, BLOCK)["engine.rate"], 0.7, "master, then")               # .5 + .5*.4
        ok("master: bus x master gain 0.5, one block late (0.6, then 0.7)")

        # --- none: side[] not wired ---------------------------------------------
        h.ok({"type": "unroute", "index": 0})
        h.ok({"type": "route", "source": "s", "output": "has", "target": "engine.rate", "depth": 0.3})
        near(live_after(h, BLOCK)["engine.rate"], 0.8, "has, wired")
        r = h.ok({"type": "set_mod_sidechain", "index": 0, "source": "none"})
        assert "sidechain" not in r["mod"]["tabs"][0], r["mod"]
        near(live_after(h, BLOCK)["engine.rate"], 0.5, "has, none")
        ok("set_mod_sidechain none: side[] is NULL, the document drops the field")

        # --- refusals -------------------------------------------------------------
        for bad, code in (({"index": 0, "source": "tape:9"}, "bad_request"),
                          ({"index": 0, "source": "bus"}, "bad_request"),
                          ({"index": 3, "source": "self"}, "not_found")):
            m = dict(bad)
            m["type"] = "set_mod_sidechain"
            r = h.req(m)
            assert r["type"] == "error" and r["code"] == code, (bad, r)
        h.ok({"type": "add_mod_tab", "engine": "arbtest/constmod", "instance": "c"})
        r = h.req({"type": "set_mod_sidechain", "index": 1, "source": "self"})
        assert r["type"] == "error" and "ARB_CAP_SIDECHAIN" in r["message"], r
        ok("refuse: unknown source, no such tab, a modulator without ARB_CAP_SIDECHAIN")

        # --- motion feed ------------------------------------------------------------
        h.ok({"type": "unroute", "index": 0})
        h.ok({"type": "route", "source": "s", "output": "ax", "target": "engine.rate", "depth": 1.0})
        near(live_after(h, BLOCK)["engine.rate"], 0.5, "no motion yet")
        h.ok({"type": "motion", "accel": [0.3, 0.0, 1.0], "gyro": [0, 0, 0]})
        near(live_after(h, BLOCK)["engine.rate"], 0.8, "motion ax 0.3")
        near(live_after(h, BLOCK)["engine.rate"], 0.8, "motion held")
        h.ok({"type": "motion"})
        near(live_after(h, BLOCK)["engine.rate"], 0.5, "motion cleared")
        r = h.req({"type": "motion", "accel": [1, 2, 3, 4]})
        assert r["type"] == "error" and r["code"] == "bad_request", r
        ok("motion: accel x 0.3 reaches arb_host.motion and a route (0.8), held, cleared by {}")

    # --- master across the ring's wrap ------------------------------------------
    # Ragged renders put a block across the end of the side ring, so its write is
    # two spans; the read of the next block must see the master's second span too
    # (#1181, t984's review: dropping it left every whole-block case above green).
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        tab = {"instance": "s", "engine": "arbtest/sidemod", "params": {}, "sidechain": "master"}
        h.ok({"type": "load_patch", "patch": patch(0.2, tab, [route("last", 0.5)])})
        h.ok({"type": "meters", "on": True, "hz": 30})
        h.ok({"type": "mixer", "master": 0.5})
        h.note(60, True, 1.0)
        live_after(h, 2 * BLOCK)
        live_after(h, 97)
        h.ok({"type": "set_param", "slot": "engine", "idx": 0, "value": 0.4})
        live_after(h, BLOCK)                                    # wraps: 415 + 97 frames
        near(live_after(h, 416)["engine.rate"], 0.6, "master, across the wrap")    # .5 + .5*.5*.4
        ok("master: a block written across the ring's end reads whole (0.6)")

    # --- documents: MOD and FX tabs carry self/master and round-trip -------------
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        fx = {"format": 1, "kind": "rack", "name": "D",
              "tabs": [{"instance": "duck", "engine": "arbutus/duck", "params": {},
                        "sidechain": "master"}]}
        tab = {"instance": "s", "engine": "arbtest/sidemod", "params": {}, "sidechain": "self"}
        h.ok({"type": "load_patch", "patch": patch(0.5, tab, [], fx=fx)})
        st = h.c.state()["instruments"]["synth"]
        assert st["mod"]["tabs"][0]["sidechain"] == "self", st["mod"]
        assert st["fx"]["tabs"][0]["sidechain"] == "master", st["fx"]
        r = h.ok({"type": "set_sidechain", "index": 0, "source": "self"})
        assert r["rack"]["tabs"][0]["sidechain"] == "self", r["rack"]
        ok("round trip: MOD tab `self`, FX tab `master`; set_sidechain takes `self`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
