#!/usr/bin/env python3
"""The stock modulator boxes through the host (modulation-spec §3.2, §4, §13).

Each box goes in a MOD tab of `arbtest/abi2` and routes to its RATE, a knob
that does not touch the sound (normalised 0.5 at rest), and the `meters` push
says where RATE is. abi2 at a flat envelope makes the bus a DC level equal to
LEVEL, which is what `self` hands the follower.
"""

import os
import sys
import time

from hostcase import Host, args, ok

FLAT = {"a": 0.0, "d": 0.0, "s": 1.0, "r": 0.0, "mode": "poly"}
BLOCK = 128


def patch(tabs, routes, level=0.5):
    return {"format": 1, "kind": "patch", "name": "BOXES",
            "engine": {"id": "arbtest/abi2", "params": {"level": level}}, "env": FLAT,
            "mod": {"format": 1, "kind": "modrack", "tabs": tabs, "routes": routes, "bypass": False}}


def route(source, output, depth, target="engine.rate"):
    return {"source": source, "output": output, "target": target, "depth": depth, "curve": "lin"}


def rate(h, frames=4800):
    """Render, then the newest live value of engine.rate."""
    h.c.clear_pushes()
    time.sleep(0.05)                 # meters are paced by wall clock (<= 30 Hz)
    h.render(frames)
    h.c.wait_push("meters")    # one per offline render; not 50 ms of quiet (#538)
    h.c.state()                # and in before this reply, so [-1] is the render's
    ms = h.c.pushes("meters")
    assert ms, "no meters push"
    v = ms[-1].get("mod", {}).get("engine.rate")
    assert v is not None, ms[-1]
    return v


def near(a, b, tol, what):
    assert abs(a - b) <= tol, "%s: got %.5f, want %.5f" % (what, a, b)


def host(a, **kw):
    h = Host(a.exe, [a.boxes], block=BLOCK, **kw)
    return h


def main():
    a = args()

    # --- follower on `self`: the instrument hears itself ------------------------
    with host(a) as h:
        tab = {"instance": "fol", "engine": "arbutus/follower", "params": {}, "sidechain": "self"}
        h.ok({"type": "load_patch", "patch": patch([tab], [route("fol", "env", 0.5),
                                                            route("fol", "gate", 0.2, "engine.level")])})
        h.ok({"type": "meters", "on": True, "hz": 30})
        near(rate(h), 0.5, 1e-6, "silent: ENV 0")
        h.note(60, True, 1.0)
        # Bus = LEVEL 0.5 + GATE route 0.2 once open = 0.7 DC; ENV follows with
        # ATTACK 5 ms, so after 0.1 s it is there: 0.5 + 0.5 x 0.7.
        near(rate(h), 0.85, 2e-3, "note held: ENV 0.7")
        ok("follower on self: silent 0.5, own DC 0.7 -> rate %.4f (GATE opened LEVEL to 0.7)" % rate(h, BLOCK))
        h.ok({"type": "note", "on": False, "note": 60})
        # RELEASE 150 ms: 1 s later ENV has let go (e^-6.7), and GATE with it.
        near(rate(h, 48000), 0.5, 2e-3, "released")
        ok("follower on self: note off -> back to the knob")

    # --- random: S&H moves the target, deterministically ------------------------
    def random_run(seed):
        with host(a, extra=["--seed", str(seed)]) as h:
            tab = {"instance": "rnd", "engine": "arbutus/random", "params": {"rate": 0.2}}  # free Hz
            h.ok({"type": "load_patch", "patch": patch([tab], [route("rnd", "sh", 0.5)])})
            h.ok({"type": "meters", "on": True, "hz": 30})
            return [round(rate(h, 9600), 5) for _ in range(6)]
    r1, r2 = random_run(7), random_run(7)
    assert len(set(r1)) >= 3, "random S&H did not move the target: %s" % r1
    assert all(0.0 <= v <= 1.0 for v in r1), r1
    ok("random: S&H -> rate takes %d values over 6 renders: %s" % (len(set(r1)), r1))
    assert r1 == r2, (r1, r2)
    ok("random: the same host seed gives the same values twice")

    # --- envelope: NOTE mode, global -----------------------------------------------
    with host(a) as h:
        tab = {"instance": "env", "engine": "arbutus/envelope", "params": {}}
        h.ok({"type": "load_patch", "patch": patch([tab], [route("env", "env", 0.5)])})
        h.ok({"type": "meters", "on": True, "hz": 30})
        near(rate(h), 0.5, 1e-6, "idle")
        h.note(60, True, 1.0)
        h.note(64, True, 1.0)
        # A 10 ms, D 300 ms, S 0.5: a second later it sits on S.
        near(rate(h, 48000), 0.75, 1e-4, "sustain")
        h.ok({"type": "note", "on": False, "note": 60})
        near(rate(h, 4800), 0.75, 1e-4, "one key still down")
        h.ok({"type": "note", "on": False, "note": 64})
        # R 500 ms from 0.5: 2 s later, gone.
        near(rate(h, 96000), 0.5, 1e-4, "released")
        ok("envelope: two notes sustain at 0.5 (rate 0.75); the first key up holds, the last releases")

    # --- motion: fed through the offline `motion` message ------------------------
    with host(a) as h:
        tab = {"instance": "mo", "engine": "arbutus/motion", "params": {}}
        h.ok({"type": "load_patch", "patch": patch(
            [tab], [route("mo", "tilt_x", 0.5), route("mo", "spin", 0.25, "engine.level")])})
        h.ok({"type": "meters", "on": True, "hz": 30})
        near(rate(h), 0.5, 1e-6, "no sensor: 0")
        h.ok({"type": "motion", "accel": [0.6, 0.0, 0.8], "gyro": [0.0, 0.0, 125.0]})
        # SMOOTH 50 ms; after 0.5 s TILT-X is 0.6 to e^-10.
        near(rate(h, 24000), 0.8, 1e-3, "tilt x 0.6")
        ok("motion: accel x 0.6 g -> TILT-X 0.6 -> rate 0.8")
        h.ok({"type": "motion", "accel": [0.0, 0.0, 1.0]})
        near(rate(h, 24000), 0.5, 1e-3, "flat again")
        ok("motion: flat again -> back to the knob")

    # --- stepmod: the pattern asset loads -------------------------------------------
    pattern = os.path.abspath(os.path.join(a.audio_root, "engines", "arbutus", "stepmod", "tests",
                                           "pattern.json"))
    with host(a) as h:
        # STEPS 1: the target sits on step 1's value. No asset: the ramp's 0.
        tab = {"instance": "st", "engine": "arbutus/stepmod", "params": {"steps": 0.0}}
        h.ok({"type": "load_patch", "patch": patch([tab], [route("st", "step", 0.25)])})
        h.ok({"type": "meters", "on": True, "hz": 30})
        near(rate(h), 0.5, 1e-6, "no asset, one step")
        tab["assets"] = {"pattern": pattern}
        h.ok({"type": "load_patch", "patch": patch([tab], [route("st", "step", 0.25)])})
        near(rate(h), 0.75, 1e-6, "pattern step 1 = +1")
        st = h.c.state()["instruments"]["synth"]
        assert st["mod"]["tabs"][0]["assets"] == {"pattern": pattern}, st["mod"]["tabs"][0]
        assert st["mod_tabs"][0]["assets"] == {"pattern": pattern}, st["mod_tabs"][0]
        ok("stepmod: pattern asset loads (step 1 = +1 -> rate 0.75), carried in `mod` and the slot")

        # All 16 steps of 1/4 at 120 BPM, stopped transport = free-running: the
        # target walks the pattern's values (x 0.25 around 0.5).
        h.ok({"type": "set_param", "slot": "mod", "tab": 0, "idx": 0, "value": 1.0})
        h.ok({"type": "set_param", "slot": "mod", "tab": 0, "idx": 1, "value": 0.25})   # 1/4
        seen = set(round(rate(h, 24000), 4) for _ in range(6))
        want = {round(0.5 + 0.25 * v, 4) for v in (1.0, -1.0, 0.5, -0.5, 0.25, -0.25, 0.0, 0.75, -0.75, 0.1)}
        assert len(seen) >= 4 and seen <= want, (seen, want)
        ok("stepmod: free-running over the pattern, %d distinct pattern values" % len(seen))

        bad = h.path("bad.json")
        with open(bad, "w") as f:
            f.write('{"format": 1, "kind": "kit", "values": [1]}')
        before = h.c.state()["instruments"]["synth"]["mod"]
        tab["assets"] = {"pattern": bad}
        r = h.req({"type": "load_patch", "patch": patch([tab], [])})
        assert r["type"] == "error" and r["message"] == "ENGINE BROKEN", r
        tab["assets"] = {"sample": pattern}
        r = h.req({"type": "load_patch", "patch": patch([tab], [])})
        assert r["type"] == "error" and r["message"] == "UNKNOWN ASSET sample", r
        assert h.c.state()["instruments"]["synth"]["mod"] == before
        r = h.ok({"type": "add_mod_tab", "engine": "arbutus/stepmod", "instance": "st2",
                  "assets": {"pattern": pattern}})
        assert r["mod"]["tabs"][1]["assets"] == {"pattern": pattern}, r["mod"]
        ok("stepmod: a wrong-kind pattern and an undeclared key are refused; add_mod_tab takes assets")

        # #60: content reaching a tab that is already there. The UI enters step
        # values and sends them back, so `set_mod_tab_engine` naming the SAME
        # engine has to keep the tab's knobs and take the new file; naming a
        # different one still clears both (another engine, other keys).
        h.ok({"type": "set_param", "slot": "mod", "tab": 1, "idx": 2, "value": 0.5})   # slew
        two = h.path("two.json")
        with open(two, "w") as f:
            f.write('{"format": 1, "kind": "stepmod", "values": [0.25, -0.25]}')
        r = h.ok({"type": "set_mod_tab_engine", "index": 1, "engine": "arbutus/stepmod",
                  "assets": {"pattern": two}})
        tab1 = r["mod"]["tabs"][1]
        assert tab1["assets"] == {"pattern": two}, tab1
        assert abs(tab1["params"]["slew"] - 0.5) < 1e-6, tab1
        assert r["tabs"][1]["assets"] == {"pattern": two}, r["tabs"][1]
        r = h.ok({"type": "set_mod_tab_engine", "index": 1, "engine": "arbutus/lfo"})
        assert not r["mod"]["tabs"][1].get("assets"), r["mod"]["tabs"][1]
        assert "slew" not in (r["mod"]["tabs"][1].get("params") or {}), r["mod"]["tabs"][1]
        ok("set_mod_tab_engine: the same engine keeps its knobs and takes new content (#60), another clears both")

    # --- four at once, no allocation in the block ----------------------------------
    with host(a, extra=["--rt-guard"]) as h:
        tabs = [{"instance": "fol", "engine": "arbutus/follower", "params": {}, "sidechain": "master"},
                {"instance": "rnd", "engine": "arbutus/random", "params": {"rate": 0.1}},
                {"instance": "env", "engine": "arbutus/envelope", "params": {"loop": 1.0}},
                {"instance": "st", "engine": "arbutus/stepmod", "params": {"slew": 0.5},
                 "assets": {"pattern": pattern}}]
        routes = [route("fol", "env", 0.2), route("rnd", "drift", 0.2, "engine.level"),
                  route("env", "inv", 0.1, "host.pan"), route("st", "step", 0.1, "env.s")]
        h.ok({"type": "load_patch", "patch": patch(tabs, routes)})
        h.ok({"type": "motion", "accel": [0.1, 0.2, 0.9], "gyro": [1, 2, 3]})
        h.note(60, True, 0.8)
        r = h.ok({"type": "render", "frames": 48000})
        h.ok({"type": "set_mod_tab_engine", "index": 1, "engine": "arbutus/motion"})
        r = h.ok({"type": "render", "frames": 48000})
        allocs = r.get("rt_allocs")
        assert allocs is not None, r
        if allocs < 0:
            ok("rt-guard: unavailable on this platform")
        else:
            assert allocs == 0, "%d heap blocks gained inside render_block with the stock modulators" % allocs
            ok("rt-guard: no allocation in 750 blocks with follower, random/motion, envelope, stepmod")
    return 0


if __name__ == "__main__":
    sys.exit(main())
