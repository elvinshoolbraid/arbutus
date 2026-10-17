#!/usr/bin/env python3
"""Voice policy through the host (DDR-052, modulation-spec §5).

`env` glide/detune/bend_range, the `bend` message, `host.pitch`, `env.glide` and
`env.detune` routes. Exact readings come from `arbtest/pitchprobe`, a PolyHost
synth whose voice renders its own pitch offset in semitones as DC: with a flat
envelope and one note, the bus *is* the offset. One check counts zero
crossings of `arbutus/osc` so the offset is also heard as a frequency.
"""

import time

from hostcase import Host, args, ok, read_wav

TOL = 1e-3
BLOCK = 480                      # 10 ms: GLIDE 0.1 s is exactly ten blocks


def env(mode="poly", **kw):
    e = {"a": 0.0, "d": 0.0, "s": 1.0, "r": 0.0, "mode": mode}
    e.update(kw)
    return e


def patch(engine="arbtest/pitchprobe", e=None, mod=None, params=None):
    p = {"format": 1, "kind": "patch", "name": "VOICE",
         "engine": {"id": engine, "params": params or {}}, "env": e or env()}
    if mod is not None:
        p["mod"] = mod
    return p


def constmod(bi=0.5, uni=0.0):
    return {"instance": "c", "engine": "arbtest/constmod", "params": {"bi": bi, "uni": uni}}


def modrack(tabs, routes):
    return {"format": 1, "kind": "modrack", "tabs": tabs, "routes": routes}


def route(output, target, depth, source="c"):
    return {"source": source, "output": output, "target": target, "depth": depth, "curve": "lin"}


def frames(h, n=BLOCK, name="v.wav"):
    path = h.path(name)
    h.render(n, path)
    return read_wav(path)[1][0]


def last(h, n=BLOCK):
    return frames(h, n)[-1]


def near(got, want, what, tol=TOL):
    assert abs(got - want) <= tol, "%s: got %.5f, want %.5f" % (what, got, want)


def env_doc(h):
    return h.c.state()["instruments"]["synth"]["env"]


def rising_crossings(x):
    return sum(1 for i in range(1, len(x)) if x[i - 1] < 0.0 <= x[i])


def main():
    a = args()

    # --- env message: fields, defaults, clamps, round trip in state ----------
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        e = env_doc(h)
        assert (e["glide"], e["detune"], e["bend_range"]) == (0, 12, 2), e
        h.ok({"type": "env", "glide": 0.25, "detune": 30, "bend_range": 7})
        e = env_doc(h)
        assert (e["glide"], e["detune"], e["bend_range"]) == (0.25, 30, 7), e
        h.ok({"type": "env", "a": 0.5})                      # the emulator's shape: a/d/s/r/mode
        e = env_doc(h)
        assert (e["a"], e["glide"], e["detune"], e["bend_range"]) == (0.5, 0.25, 30, 7), e
        h.ok({"type": "env", "glide": 9, "detune": -1, "bend_range": 99})
        e = env_doc(h)
        assert (e["glide"], e["detune"], e["bend_range"]) == (2, 0, 24), e
        ok("env: glide/detune/bend_range default 0/12/2, round trip in state, clamp to 2 s/0..50 ct/24 st")

        # A patch without them loads the defaults, not what was there before.
        h.ok({"type": "load_patch", "patch": patch(e=env(glide=0.3, detune=40, bend_range=5))})
        e = env_doc(h)
        assert (e["glide"], e["detune"], e["bend_range"]) == (0.3, 40, 5), e
        h.ok({"type": "load_patch", "patch": patch(e={"a": 0.0, "d": 0.0, "s": 1.0, "r": 0.0})})
        e = env_doc(h)
        assert (e["glide"], e["detune"], e["bend_range"]) == (0, 12, 2), e
        ok("patch: env carries glide/detune/bend_range; omitted = defaults")

    # --- a patch without the new fields sounds exactly as with the defaults --
    def unison_render(e):
        with Host(a.exe, [a.boxes], block=128) as h:
            h.ok({"type": "load_patch", "patch": patch("arbutus/osc", e=e)})
            h.note(60)
            path = h.path("u.wav")
            h.render(9600, path)
            return read_wav(path)[1]
    old = unison_render({"a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "unison"})
    new = unison_render({"a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "unison",
                         "glide": 0, "detune": 12, "bend_range": 2})
    assert old == new, "an old unison patch no longer renders like the defaults"
    assert max(abs(v) for v in old[0]) > 0.1, "the unison render is silent"
    ok("patch without glide/detune/bend_range: osc unison bit-identical to explicit defaults")

    # --- bend: ramps over GLIDE, snaps at 0 -----------------------------------
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        h.ok({"type": "load_patch", "patch": patch(e=env(glide=0.1, bend_range=12))})
        h.note(60)
        near(last(h), 0.0, "no bend")
        h.ok({"type": "bend", "value": 1.0})
        near(last(h), 1.2, "one block into a 0.1 s bend: 0.1 x 12 st")
        for _ in range(8):
            frames(h)
        near(last(h), 12.0, "ten blocks: full bend")
        near(last(h), 12.0, "holds")
        h.ok({"type": "bend", "value": 0})
        near(last(h), 10.8, "release ramps back the same way")
        for _ in range(9):
            frames(h)
        near(last(h), 0.0, "and arrives")
        ok("bend: 0 -> 12 st over GLIDE 0.1 s in 10 blocks, release returns over the same")

        h.ok({"type": "env", "glide": 0})
        h.ok({"type": "bend", "value": -0.5})
        x = frames(h)
        near(x[-1], -6.0, "glide 0: snaps within one block")
        assert x[0] > -6.0 + 0.1, "the snap should still be ramped across the block: %f" % x[0]
        h.ok({"type": "env", "bend_range": 24})
        near(last(h), -12.0, "bend_range rescales a held bend")
        ok("bend: GLIDE 0 lands in one block (ramped inside it, first frame %.3f); BEND range 24 -> -12 st"
           % x[0])

    # --- host.pitch: route from constmod, the knob, and an LFO ----------------
    with Host(a.exe, [a.boxes], block=BLOCK, extra=["--rt-guard"]) as h:
        h.ok({"type": "load_patch", "patch": patch(
            mod=modrack([constmod(bi=0.75)], [route("bi", "host.pitch", 0.5)]))})
        h.note(60)
        near(last(h), 12.0, "host.pitch: 0.5 + 0.5 x 0.5 = 0.75 -> +12 st")
        h.ok({"type": "host_param", "pitch": 0.25})
        near(last(h), 0.0, "knob 0.25 (-12 st) plus the same route")
        assert h.c.state()["instruments"]["synth"]["host"]["pitch"] == 0.25
        h.ok({"type": "bend", "value": 1.0})             # GLIDE 0: lands in this block
        near(last(h), 2.0, "bend (+2 st at the default range) adds to the route")
        h.ok({"type": "bend", "value": 0})
        frames(h)
        ok("host.pitch: constmod route +12 st, knob -12 st, bend +2 st all add into pitch_st")

        h.ok({"type": "host_param", "pitch": 0.5})
        h.ok({"type": "load_patch", "patch": patch(mod=modrack(
            [{"instance": "l", "engine": "arbutus/lfo", "params": {}}],
            [route("sine", "host.pitch", 0.25, source="l")]))})
        h.note(60)
        x = frames(h, 48000, "lfo.wav")
        lo, hi = min(x[4800:]), max(x[4800:])
        assert hi - lo > 10.0, "the LFO did not move pitch (%f..%f)" % (lo, hi)
        # 2 Hz x +/-12 st peaks at ~150 st/s: 1.5 st per 480-frame block if it
        # stepped per block, 0.1 st per 32-frame run as PolyHost ramps it.
        steps = max(abs(x[i] - x[i - 1]) for i in range(4801, len(x)))
        assert steps < 0.2, "pitch zippers: a %f st jump between frames" % steps
        r = h.render(4800)
        allocs = r.get("rt_allocs")
        assert allocs is not None and allocs <= 0, "%s heap blocks gained in render_block" % allocs
        ok("host.pitch: lfo.sine swings %.2f..%.2f st, largest frame step %.4f st; rt-guard %s"
           % (lo, hi, steps, "clean" if allocs == 0 else "unavailable"))

    with Host(a.exe, [a.boxes], block=128) as h:
        def hz(route_depth):
            h.ok({"type": "load_patch", "patch": patch("arbutus/osc", e=env(), mod=modrack(
                [constmod(bi=0.75)], [route("bi", "host.pitch", route_depth)]))})
            h.note(69)
            frames(h, 4800)
            n = rising_crossings(frames(h, 48000, "osc.wav"))
            h.note(69, on=False)
            return n
        base, up = hz(0.0), hz(0.5)
        assert abs(base - 440) <= 1 and abs(up - 880) <= 1, (base, up)
        ok("host.pitch on arbutus/osc: A4 %d Hz, +12 st route %d Hz" % (base, up))

    # --- env.glide and env.detune routes --------------------------------------
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        # GLIDE knob 0, UNI 1.0 x depth 0.05 -> 0.05 of 0..2 s = 0.1 s.
        h.ok({"type": "load_patch", "patch": patch(
            e=env("mono"), mod=modrack([constmod(uni=1.0)], [route("uni", "env.glide", 0.05)]))})
        h.note(60)
        frames(h)
        h.note(72)
        x = frames(h, 4800)
        near(x[0], -12.0, "mono glide from 60 to 72 starts 12 st down")
        near(x[2400], -6.0, "halfway through a routed 0.1 s glide", tol=0.1)
        near(last(h), 0.0, "and arrives")
        ok("env.glide route: knob 0 + route -> 0.1 s mono glide (-12 -> %.2f -> 0 st)" % x[2400])

        r = h.ok({"type": "route", "source": "c", "output": "bi", "target": "env.detune",
                  "depth": 0.5})
        assert [x["target"] for x in r["mod"]["routes"]] == ["env.glide", "env.detune"], r["mod"]
        h.ok({"type": "set_param", "slot": "mod", "tab": 0, "idx": 0, "value": 0.75})
        h.c.clear_pushes()
        h.ok({"type": "meters", "on": True, "hz": 30})
        # Turning them on pushes one at once (blocks went by unmetered); take it
        # first, or it arrives behind the render's own push and reads as [-1].
        assert h.c.wait_push("meters"), "no meters push when they were turned on"
        h.c.clear_pushes()
        time.sleep(0.05)
        h.render(BLOCK)
        h.c.wait_push("meters")    # one per offline render; not 50 ms of quiet (#538)
        h.c.state()                # and in before this reply, so [-1] is the render's
        ms = list(h.c.pushes("meters"))
        assert ms, "no meters push"
        # DETUNE knob 12/50 = 0.24, + 0.5 x 0.5 -> 0.49 (24.5 ct)
        near(ms[-1]["mod"]["env.detune"], 0.49, "env.detune live value", tol=1e-4)
        ok("env.detune route accepted, live value %.4f (= 24.5 ct)" % ms[-1]["mod"]["env.detune"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
