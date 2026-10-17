#!/usr/bin/env python3
"""The MOD rack through the host (modulation-spec §2, §5, §6, §8, §11).

Exact inputs come from `arbtest/constmod`, whose outputs are its knobs, and
exact outputs from `arbtest/abi2`, whose voice is LEVEL as DC: with a flat
envelope every frame of the bus *is* the effective LEVEL the engine saw. The
live readout comes from the `meters` push. Expected values are worked by hand
in the comments, not recomputed with the formula under test.
"""

import sys
import time

from hostcase import Host, args, db, ok, peak_error, read_wav

TOL = 1e-4
FLAT = {"a": 0.0, "d": 0.0, "s": 1.0, "r": 0.0, "mode": "poly"}


def patch(engine, params=None, mod=None, fx=None, env=None):
    p = {"format": 1, "kind": "patch", "name": "MODTEST",
         "engine": {"id": engine, "params": params or {}},
         "env": env or FLAT}
    if mod is not None:
        p["mod"] = mod
    if fx is not None:
        p["fx"] = fx
    return p


def modrack(tabs, routes=(), bypass=False):
    return {"format": 1, "kind": "modrack", "tabs": list(tabs), "routes": list(routes),
            "bypass": bypass}


def cm(instance="c", bi=0.5, uni=0.0, bypass=False):
    """constmod tab; `bi`/`uni` are normalised knobs (bi 0.75 -> m = +0.5)."""
    return {"instance": instance, "engine": "arbtest/constmod",
            "params": {"bi": bi, "uni": uni}, "bypass": bypass}


def route(output, target, depth, curve="lin", source="c", lo=None, hi=None):
    r = {"source": source, "output": output, "target": target, "depth": depth, "curve": curve}
    if lo is not None:
        r["lo"] = lo
    if hi is not None:
        r["hi"] = hi
    return r


def bus(h, frames=512):
    """Render and return (left, right) of the last frame."""
    path = h.path("bus.wav")
    h.render(frames, path)
    _, ch = read_wav(path)
    return ch[0][-1], ch[1][-1]


def live(h):
    """The newest `meters` push's `mod` map, after a render that can carry one."""
    h.c.clear_pushes()
    time.sleep(0.05)                 # meters are paced by wall clock (<= 30 Hz)
    h.render(512)
    h.c.wait_push("meters")    # one per offline render; not 50 ms of quiet (#538)
    h.c.state()                # and in before this reply, so [-1] is the render's
    ms = [p for p in h.c.pushes("meters")]
    assert ms, "no meters push"
    return ms[-1].get("mod", {})


def near(a, b, what):
    assert abs(a - b) <= TOL, "%s: got %.6f, want %.6f" % (what, a, b)


def main():
    a = args()

    # --- §6.2 depth maths, read back live ---------------------------------------
    # Target: abi2's RATE, not per_voice, knob 0.5. Source: constmod.
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch(
            "arbtest/abi2", mod=modrack([cm()], [route("bi", "engine.rate", 0.4)]))})
        h.ok({"type": "meters", "on": True, "hz": 30})

        def case(bi, uni, output, depth, curve, want, lo=None, hi=None, label=""):
            h.ok({"type": "set_param", "slot": "mod", "tab": 0, "idx": 0, "value": bi})
            h.ok({"type": "set_param", "slot": "mod", "tab": 0, "idx": 1, "value": uni})
            h.ok({"type": "unroute", "index": 0})
            m = {"type": "route", "source": "c", "output": output, "target": "engine.rate",
                 "depth": depth, "curve": curve}
            if lo is not None:
                m["lo"] = lo
            if hi is not None:
                m["hi"] = hi
            r = h.ok(m)
            assert r["type"] == "mod" and len(r["mod"]["routes"]) == 1, r
            got = live(h).get("engine.rate")
            assert got is not None, "no live value for engine.rate"
            near(got, want, label)
            ok("depth: %-34s -> %.5f" % (label, got))

        # bipolar, m = +0.5 (bi 0.75), d = 0.4, k = 0.5
        case(0.75, 0, "bi", 0.4, "lin", 0.70, label="bipolar +0.5 lin d0.4")       # .5 + .4*.5
        case(0.75, 0, "bi", 0.4, "exp", 0.60, label="bipolar +0.5 exp d0.4")       # .5 + .4*.25
        case(0.75, 0, "bi", 0.4, "log", 0.782843, label="bipolar +0.5 log d0.4")   # .5 + .4*.70711
        case(0.90, 0, "bi", 0.4, "s", 0.8584, label="bipolar +0.8 s d0.4")         # .5 + .4*(.64*1.4)
        # bipolar, m = -0.5 (bi 0.25): the curves are odd
        case(0.25, 0, "bi", 0.4, "lin", 0.30, label="bipolar -0.5 lin d0.4")
        case(0.25, 0, "bi", 0.4, "exp", 0.40, label="bipolar -0.5 exp d0.4")       # .5 - .4*.25
        case(0.25, 0, "bi", 0.4, "log", 0.217157, label="bipolar -0.5 log d0.4")
        case(0.10, 0, "bi", 0.4, "s", 0.1416, label="bipolar -0.8 s d0.4")         # .5 - .3584
        case(0.75, 0, "bi", -0.4, "lin", 0.30, label="bipolar +0.5 lin d-0.4")
        # unipolar, m = 0.5 (uni 0.5): on top of the knob, negative d subtracts
        case(0.5, 0.5, "uni", 0.4, "lin", 0.70, label="unipolar 0.5 lin d0.4")
        case(0.5, 0.5, "uni", -0.4, "lin", 0.30, label="unipolar 0.5 lin d-0.4")
        case(0.5, 0.5, "uni", 0.4, "exp", 0.60, label="unipolar 0.5 exp d0.4")
        case(0.5, 0.5, "uni", 0.4, "log", 0.782843, label="unipolar 0.5 log d0.4")
        case(0.5, 0.5, "uni", 0.4, "s", 0.70, label="unipolar 0.5 s d0.4")         # s(.5) = .5
        case(0.5, 0.0, "uni", 0.4, "lin", 0.50, label="unipolar 0 sits on the knob")
        # clamps: [0, 1] by default, [lo, hi] with a window
        case(0.90, 0, "bi", 1.0, "lin", 1.0, label="clamped at 1")                 # .5 + .8
        case(0.75, 0, "bi", 0.4, "lin", 0.65, lo=0.55, hi=0.65, label="window [.55,.65] top")
        case(0.25, 0, "bi", 0.4, "lin", 0.55, lo=0.55, hi=0.65, label="window [.55,.65] bottom")
        case(0.75, 0, "bi", 0.4, "lin", 0.65, lo=0.65, hi=0.55, label="window given hi<lo")

        # set_route edits the same route; null clears the window
        r = h.ok({"type": "set_route", "index": 0, "depth": 0.2, "curve": "lin",
                  "lo": None, "hi": None})
        assert "lo" not in r["mod"]["routes"][0] and r["mod"]["routes"][0]["depth"] == 0.2, r
        near(live(h)["engine.rate"], 0.60, "set_route")                            # .5 + .2*.5
        ok("set_route: depth 0.2, window cleared -> 0.6")

        # A knob turn moves the centre; the document keeps the knob (DDR-046).
        h.ok({"type": "set_param", "slot": "engine", "idx": 1, "value": 0.25})
        near(live(h)["engine.rate"], 0.35, "knob moved")                           # .25 + .1
        st = h.c.state()["instruments"]["synth"]
        assert abs(st["engine"]["params"]["rate"] - 0.25) < 1e-6, st["engine"]["params"]
        ok("knob change moves the centre (0.35); state still holds the knob (0.25)")

        # unroute by address
        r = h.ok({"type": "unroute", "source": "c", "output": "bi", "target": "engine.rate"})
        assert r["mod"]["routes"] == [], r
        assert "engine.rate" not in live(h)
        ok("unroute: by address; the readout goes with it")

    # --- routes land on the parameter the engine sees -------------------------
    # abi2 at a flat envelope: the bus is LEVEL. Knob 0.5, UNI 0.5, depth 0.2.
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch(
            "arbtest/abi2", params={"level": 0.5},
            mod=modrack([cm(uni=0.5)], [route("uni", "engine.level", 0.2)]))})
        h.note(60, True, 1.0)
        l, _ = bus(h)
        near(l, 0.6, "routed level")
        ok("engine.level: the engine renders 0.6 (knob 0.5 + 0.2 x 0.5)")

        h.ok({"type": "set_mod_bypass", "index": 0, "bypass": True})
        near(bus(h)[0], 0.5, "tab bypass")
        ok("bypassed tab contributes 0: back on the knob (0.5)")
        h.ok({"type": "set_mod_bypass", "index": 0, "bypass": False})
        near(bus(h)[0], 0.6, "tab un-bypassed")
        r = h.ok({"type": "set_mod_bypass", "bypass": True})
        assert r["mod"]["bypass"] is True and r["mod"]["tabs"][0]["bypass"] is False, r
        near(bus(h)[0], 0.5, "rack bypass")
        ok("bypassed rack contributes nothing (0.5); tab flags kept")
        h.ok({"type": "set_mod_bypass", "bypass": False})

        h.ok({"type": "set_param", "slot": "engine", "idx": 0, "value": 0.3})
        near(bus(h)[0], 0.4, "knob moved under a route")
        h.ok({"type": "unroute", "index": 0})
        near(bus(h)[0], 0.3, "unrouted")
        ok("knob 0.3 moves the swing to 0.4; unroute hands LEVEL back to the knob (0.3)")

    # --- keys.VEL per voice: the engine applies it (§8) ------------------------
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch(
            "arbtest/abi2", params={"level": 0.0},
            mod=modrack([], [route("vel", "engine.level", 0.5, source="keys")]))})
        h.ok({"type": "meters", "on": True, "hz": 30})
        h.note(60, True, 1.0)
        near(bus(h)[0], 0.5, "one voice at vel 1")
        h.note(64, True, 0.2)
        near(bus(h)[0], 0.6, "two voices")
        ok("keys.VEL -> per_voice LEVEL: voices at 0.5 and 0.1 sum to 0.6 (global would be 0.2)")
        near(live(h)["engine.level"], 0.1, "ghost at the last note")
        ok("per-voice route's readout is the last note's value (0.1)")

    # --- keys.VEL globally, to a param that is not per_voice ------------------
    # The template's LEVEL: last note's velocity for every voice, which must be
    # exactly the template with LEVEL set by hand.
    def template(level, mod):
        with Host(a.exe, [a.boxes]) as h:
            h.ok({"type": "load_patch", "patch": patch(
                "arbutus/example", params={"level": level}, mod=mod,
                env={"a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})})
            h.note(60, True, 0.9)
            h.note(67, True, 0.4)
            path = h.path("t.wav")
            h.render(8192, path)
            return read_wav(path)[1]
    by_route = template(0.5, modrack([], [route("vel", "engine.level", 0.25, source="keys")]))
    by_hand = template(0.6, None)                                                   # .5 + .25*.4
    e = peak_error(by_route, by_hand)
    assert db(e) <= -100.0, "keys.VEL global != LEVEL 0.6 by hand (%.1f dBFS)" % db(e)
    ok("keys.VEL -> non-per_voice LEVEL applies the last velocity globally (%.1f dBFS)" % db(e))

    # --- keys.NOTE, global --------------------------------------------------------
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch(
            "arbtest/abi2", mod=modrack([], [route("note", "engine.rate", 0.5, source="keys")]))})
        h.ok({"type": "meters", "on": True, "hz": 30})
        h.note(90, True, 1.0)
        near(live(h)["engine.rate"], 0.75, "keys.NOTE")                            # .5 + .5*(30/60)
        ok("keys.NOTE: note 90 = +0.5 -> 0.75")

    # --- env target: new notes take the modulated sustain ----------------------
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch(
            "arbtest/abi2", params={"level": 0.5},
            env={"a": 0.0, "d": 0.0, "s": 0.8, "r": 0.0, "mode": "poly"},
            mod=modrack([cm(uni=0.5)], [route("uni", "env.s", -0.6)]))})
        h.note(60, True, 1.0)
        near(bus(h)[0], 0.25, "env.s")                                              # .5 x (.8 - .3)
        ok("env.s: sustain 0.8 - 0.3 = 0.5, LEVEL 0.5 -> 0.25")

    # --- host.level and host.pan ------------------------------------------------
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch(
            "arbtest/abi2", params={"level": 0.5},
            mod=modrack([cm(bi=0.25, uni=0.5)],
                        [route("bi", "host.level", 1.0), route("uni", "host.pan", 0.25)]))})
        h.note(60, True, 1.0)
        l, r = bus(h, 1024)
        # level 1 - 0.5 = 0.5; pan 0.5 + 0.125 -> p = +0.25: L x cos(pi/8)
        near(l, 0.5 * 0.5 * 0.9238795, "host.level x pan, left")
        near(r, 0.25, "host.level x pan, right")
        ok("host.level 0.5 and host.pan +0.25: L %.5f R %.5f" % (l, r))
        h.ok({"type": "unroute", "index": 1})
        h.ok({"type": "unroute", "index": 0})
        h.ok({"type": "host_param", "pan": 0.0})
        l, r = bus(h, 1024)
        near(l, 0.5, "hard left, left")
        near(r, 0.0, "hard left, right")
        h.ok({"type": "host_param", "pan": 0.5})
        l, r = bus(h, 1024)
        assert l == 0.5 and r == 0.5, (l, r)
        st = h.c.state()["instruments"]["synth"]["host"]
        assert st == {"level": 1.0, "pan": 0.5, "pitch": 0.5}, st
        ok("host_param: pan knob hard left, then centre is unity again")

    # --- FX targets: a param and a macro ---------------------------------------
    def fx_render(cutoff, macro=None, mod=None, macro_value=None):
        rack = {"format": 1, "kind": "rack", "name": "LP",
                "tabs": [{"instance": "lp", "engine": "arbutus/biquad",
                          "params": {"cutoff": cutoff}}]}
        if macro:
            rack["macros"] = macro
        with Host(a.exe, [a.boxes]) as h:
            h.ok({"type": "load_patch", "patch": patch(
                "arbutus/example", mod=mod, fx=rack,
                env={"a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})})
            if macro_value is not None:
                h.ok({"type": "set_macro", "slot": 0, "value": macro_value})
            h.ok({"type": "meters", "on": True, "hz": 30})
            h.note(60, True, 0.9)
            h.note(67, True, 0.7)
            path = h.path("fx.wav")
            h.render(8192, path)
            lv = live(h) if mod else {}
            return read_wav(path)[1], lv

    by_hand, _ = fx_render(0.5)
    by_param, lv = fx_render(0.4, mod=modrack([cm(uni=0.5)], [route("uni", "fx.lp.cutoff", 0.2)]))
    near(lv["fx.lp.cutoff"], 0.5, "fx param live")
    e = peak_error(by_param[:], by_hand)
    assert db(e) <= -100.0, "fx.lp.cutoff route != cutoff 0.5 by hand (%.1f dBFS)" % db(e)
    ok("fx.lp.cutoff: 0.4 + 0.1 renders as cutoff 0.5 (%.1f dBFS)" % db(e))

    macro = [{"slot": 0, "name": "OPEN", "targets": [
        {"instance": "lp", "param": "cutoff", "lo": 0.2, "hi": 0.8, "curve": "lin", "depth": 1.0}]}]
    by_macro, lv = fx_render(0.9, macro=macro, macro_value=0.3,
                             mod=modrack([cm(uni=0.5)], [route("uni", "fx.macro.0", 0.4)]))
    near(lv["fx.macro.0"], 0.5, "macro live")                                       # .3 + .2
    e = peak_error(by_macro, by_hand)                                               # .2 + .5*.6 = .5
    assert db(e) <= -100.0, "fx.macro.0 route != cutoff 0.5 (%.1f dBFS)" % db(e)
    ok("fx.macro.0: macro 0.3 + 0.2 = 0.5 drives cutoff to 0.5 (%.1f dBFS)" % db(e))

    # --- refusals -----------------------------------------------------------------
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbtest/abi2"})
        for n in range(4):
            r = h.ok({"type": "add_mod_tab", "engine": "arbtest/constmod", "instance": "c%d" % n})
            assert r["type"] == "mod" and len(r["mod"]["tabs"]) == n + 1, r
            assert len(r["tabs"]) == n + 1 and r["tabs"][n]["desc"]["outputs"], r["tabs"]
        r = h.req({"type": "add_mod_tab", "engine": "arbtest/constmod"})
        assert r["type"] == "error" and r["message"] == "MOD RACK FULL", r
        ok("refuse: a fifth modulator (%s)" % r["message"])

        for n in range(4):
            h.ok({"type": "route", "source": "c%d" % n, "output": "bi", "target": "engine.rate"})
        r = h.req({"type": "route", "source": "keys", "output": "vel", "target": "engine.rate"})
        assert r["type"] == "error" and r["code"] == "mod_rack_full", r
        ok("refuse: a fifth route (%s)" % r["message"])

        r = h.ok({"type": "remove_mod_tab", "index": 3})
        assert len(r["mod"]["tabs"]) == 3 and len(r["mod"]["routes"]) == 3, r["mod"]
        assert all(x["source"] != "c3" for x in r["mod"]["routes"]), r["mod"]
        ok("remove_mod_tab takes its routes with it")

        r = h.ok({"type": "set_mod_tab_engine", "index": 2, "engine": "arbutus/lfo"})
        assert r["mod"]["tabs"][2]["engine"] == "arbutus/lfo", r["mod"]
        assert all(x["source"] != "c2" for x in r["mod"]["routes"]), r["mod"]["routes"]
        assert r["tabs"][2]["display"] == {"rate": "1/4"}, r["tabs"][2].get("display")
        ok("set_mod_tab_engine: lfo in, the route from an output it lacks is lifted (RATE %s)"
           % r["tabs"][2]["display"]["rate"])

        for bad, want in (
                ({"source": "c0", "output": "nope", "target": "engine.rate"}, None),
                ({"source": "zz", "output": "bi", "target": "engine.rate"}, None),
                ({"source": "c0", "output": "bi", "target": "engine.nope"}, None),
                ({"source": "c0", "output": "bi", "target": "fx.lp.cutoff"}, None),
                ({"source": "c0", "output": "bi", "target": "mod.c1.bi"}, None),
                ({"source": "c0", "output": "bi", "target": "tempo"}, None)):
            h.ok({"type": "unroute", "index": 0})
            m = dict(bad)
            m["type"] = "route"
            r = h.req(m)
            assert r["type"] == "error", (bad, r)
            if want:
                assert r["message"] == want, r
            h.ok({"type": "route", "source": "c0", "output": "bi", "target": "engine.rate"})
        ok("refuse: unknown source/output/target, mod->mod")

        r = h.req({"type": "load_engine", "slot": "engine", "id": "arbtest/constmod"})
        assert r["type"] == "error" and r["message"] == "ENGINE BROKEN", r
        r = h.req({"type": "add_tab", "engine": "arbtest/constmod"})
        assert r["type"] == "error" and r["message"] == "ENGINE BROKEN", r
        r = h.req({"type": "add_mod_tab", "engine": "arbutus/biquad"})
        assert r["type"] == "error" and r["message"] == "ENGINE BROKEN", r
        assert "not a modulator" in r["detail"], r
        ok("refuse: a modulator in ENGINE or FX, an effect in a MOD tab (%s)" % r["detail"])

        before = h.c.state()["instruments"]["synth"]["mod"]
        r = h.req({"type": "load_patch", "patch": patch(
            "arbtest/abi2", mod=modrack([{"instance": "mi", "engine": "arbutus/midi"}]))})
        assert r["type"] == "error" and r["message"] == "NEEDS ENGINE arbutus/midi", r
        assert h.c.state()["instruments"]["synth"]["mod"] == before
        ok("refuse: patch needing arbutus/midi (v1.5); nothing loaded")

    # --- §11 round trip, and a patch without `mod` -------------------------------
    doc = modrack(
        [{"instance": "lfo_a", "engine": "arbutus/lfo", "min_version": "1.0.0",
          "params": {"rate": 0.25, "retrig": 1.0}, "bypass": False},
         {"instance": "c", "engine": "arbtest/constmod", "min_version": "",
          "params": {"bi": 0.75, "uni": 0.0}, "bypass": True}],
        [{"source": "lfo_a", "output": "sine", "target": "engine.rate", "depth": 0.35,
          "curve": "lin"},
         {"source": "keys", "output": "vel", "target": "engine.level", "depth": 0.5,
          "curve": "exp", "lo": 0.1, "hi": 0.9}],
        bypass=False)
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch("arbtest/abi2", mod=doc)})
        got = h.c.state()["instruments"]["synth"]["mod"]
        assert got["format"] == 1 and got["kind"] == "modrack", got
        assert got["routes"] == doc["routes"], (got["routes"], doc["routes"])
        for g, w in zip(got["tabs"], doc["tabs"]):
            for k in ("instance", "engine", "min_version", "bypass"):
                assert g[k] == w[k], (k, g, w)
            for pid, v in w["params"].items():
                assert abs(g["params"][pid] - v) < 1e-6, (pid, g["params"], w["params"])
        # ...and what state reports loads back to itself
        h.ok({"type": "load_patch", "patch": patch("arbtest/abi2", mod=got)})
        again = h.c.state()["instruments"]["synth"]["mod"]
        assert again == got, (again, got)
        ok("§11 round trip: tabs, params, bypass, routes with and without a window")

        h.ok({"type": "load_patch", "patch": patch("arbtest/abi2")})
        empty = h.c.state()["instruments"]["synth"]["mod"]
        assert empty == modrack([]), empty
        ok("a patch without `mod` loads an empty MOD rack")

    # --- an LFO actually moves something, and allocates nothing doing it -------
    with Host(a.exe, [a.boxes], block=128, extra=["--rt-guard"]) as h:
        rack = {"format": 1, "kind": "rack", "name": "LP",
                "tabs": [{"instance": "lp", "engine": "arbutus/biquad", "params": {}}],
                "macros": [{"slot": 0, "name": "M", "targets": [
                    {"instance": "lp", "param": "q", "lo": 0.1, "hi": 0.5}]}]}
        mod = modrack(
            [{"instance": "lfo", "engine": "arbutus/lfo",
              "params": {"rate": 0.25, "retrig": 1.0, "morph": 0.3}},
             cm(uni=0.5)],
            [route("sqr", "engine.level", 0.4, source="lfo"),
             route("uni", "fx.lp.cutoff", -0.2),
             route("uni", "fx.macro.0", 0.3),
             route("vel", "engine.level", 0.2, source="keys")])
        h.ok({"type": "load_patch", "patch": patch("arbtest/abi2", params={"level": 0.5},
                                                   mod=mod, fx=rack)})
        h.ok({"type": "host_param", "level": 0.9})
        h.note(60, True, 0.8)
        r = h.ok({"type": "render", "frames": 48000, "file": h.path("lfo.wav")})
        _, ch = read_wav(h.path("lfo.wav"))
        lo_v, hi_v = min(ch[0][4800:]), max(ch[0][4800:])
        assert hi_v - lo_v > 0.1, "the LFO route did not move LEVEL (%f..%f)" % (lo_v, hi_v)
        ok("lfo.sqr -> engine.level swings the bus %.3f..%.3f" % (lo_v, hi_v))
        allocs = r.get("rt_allocs")
        assert allocs is not None, r
        if allocs < 0:
            ok("rt-guard: unavailable on this platform")
        else:
            assert allocs == 0, "%d heap blocks gained inside render_block with a MOD rack" % allocs
            ok("rt-guard: no allocation in 375 blocks of render_block with a MOD rack loaded")

    # --- a tab's name follows its box (#329) ------------------------------------
    # The browser applies on every detent, so scrolling to reverb past biquad
    # swaps the box on a tab that biquad named. The name, and every address that
    # carries it (a macro target, a route's source or target), must follow.
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbtest/abi2"})
        r = h.ok({"type": "add_tab", "engine": "arbutus/biquad"})
        assert r["rack"]["tabs"][0]["instance"] == "biquad_1", r["rack"]
        h.ok({"type": "bind", "slot": 0, "instance": "biquad_1", "param": "cutoff"})
        r = h.ok({"type": "add_mod_tab", "engine": "arbutus/follower"})
        assert r["mod"]["tabs"][0]["instance"] == "follower_1", r["mod"]
        h.ok({"type": "route", "source": "follower_1", "output": "env", "target": "engine.level"})
        h.ok({"type": "route", "source": "keys", "output": "vel", "target": "fx.biquad_1.cutoff"})

        r = h.ok({"type": "set_tab_engine", "index": 0, "engine": "arbutus/eq3"})
        assert r["rack"]["tabs"][0]["instance"] == "eq3_1", r["rack"]["tabs"]
        assert [t["instance"] for t in r["rack"]["macros"][0]["targets"]] == ["eq3_1"], r["rack"]
        r = h.ok({"type": "set_mod_tab_engine", "index": 0, "engine": "arbutus/envelope"})
        assert r["mod"]["tabs"][0]["instance"] == "envelope_1", r["mod"]["tabs"]
        got = sorted((x["source"], x["output"], x["target"]) for x in r["mod"]["routes"])
        assert got == [("envelope_1", "env", "engine.level"),
                       ("keys", "vel", "fx.eq3_1.cutoff")], got
        ok("set_tab_engine / set_mod_tab_engine: the tab is renamed after its new box "
           "(biquad_1 -> eq3_1, follower_1 -> envelope_1), macro and routes carried")

        # The same box keeps its name (#60's door for content), and a swap back
        # takes the first free name of that box again.
        r = h.ok({"type": "set_mod_tab_engine", "index": 0, "engine": "arbutus/envelope"})
        assert r["mod"]["tabs"][0]["instance"] == "envelope_1", r["mod"]["tabs"]
        r = h.ok({"type": "add_tab", "engine": "arbutus/biquad"})
        r = h.ok({"type": "set_tab_engine", "index": 0, "engine": "arbutus/biquad"})
        assert [t["instance"] for t in r["rack"]["tabs"]] == ["biquad_2", "biquad_1"], r["rack"]["tabs"]
        ok("the same box keeps the tab's name; a second biquad is biquad_2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
