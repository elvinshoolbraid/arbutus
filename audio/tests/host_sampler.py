#!/usr/bin/env python3
"""arbutus/sampler v2 through the host: loop, release, REV, seam FADE, readouts.

A ramp is the sample: every frame differs from the next by 1/24000, so a hard
loop is a step of half the ramp at the seam and anything the crossfade leaves
behind is visible as a step far above the ramp's own slope.
"""

import os
import struct
import sys
import wave

from hostcase import Host, args, ok, read_wav

N = 24000                               # frames in the ramp, at 48 kHz: root plays at rate 1


def ramp_wav(path):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", int((-0.5 + i / N) * 32767)) for i in range(N)))


def patch(wav, **params):
    base = {"root": 60 / 127.0}
    base.update(params)
    return {"format": 1, "kind": "patch", "name": "RAMP",
            "engine": {"id": "arbutus/sampler", "params": base, "assets": {"sample": wav}},
            # instant attack, full sustain, a long release so the tail is audible
            "env": {"a": 0.0005, "d": 0.01, "s": 1.0, "r": 2.0, "mode": "poly"}}


def max_step(c, a, b):
    return max(abs(c[i + 1] - c[i]) for i in range(a, b - 1))


def take(h, doc, hold, after, name):
    h.ok({"type": "load_patch", "patch": doc})
    h.render(1024)
    h.ok({"type": "panic"})
    h.render(512)
    h.note(60, True, 1.0)
    path = h.path(name + "-held.wav")
    h.render(hold, path)
    held = read_wav(path)[1][0]
    h.note(60, False)
    path = h.path(name + "-rel.wav")
    h.render(after, path)
    return held, read_wav(path)[1][0]


def main():
    a = args()
    with Host(a.exe, [a.boxes]) as h:
        wav = h.path("ramp.wav")
        ramp_wav(wav)

        # --- default: no loop, one pass, silence after the sample -------------
        held, _ = take(h, patch(wav), 36000, 512, "default")
        assert max(abs(v) for v in held[25000:36000]) == 0.0
        assert max(abs(v) for v in held[1000:20000]) > 0.1
        ok("default LOOP-IN = LOOP-OUT = 1: one pass, silent after the sample")

        # --- hard loop: carried while held, a step at the seam ----------------
        loop = {"loop_in": 0.25, "loop_out": 0.75}
        held, rel = take(h, patch(wav, **loop), 36000, 12000, "hard")
        late = held[30000:36000]
        assert max(abs(v) for v in late) > 0.05, "the loop did not carry the note"
        hard = max_step(held, 2000, 36000)
        assert hard > 0.2, hard
        ok("loop: a held note keeps sounding past the sample (hard seam step %.3f)" % hard)

        # Released inside the loop, it plays on past LOOP-OUT to END: the ramp's
        # top quarter, above anything the loop region can reach.
        loop_top = 0.8 * 0.25
        assert max(rel) > loop_top + 0.1, (max(rel), loop_top)
        ok("loop: on release the voice plays on to END (peak %.3f > loop top %.3f)" % (max(rel), loop_top))

        # --- FADE: the seam is no longer a step --------------------------------
        held, _ = take(h, patch(wav, fade=0.4, **loop), 36000, 512, "fade")
        soft = max_step(held, 2000, 36000)
        assert soft < 1e-3, soft
        ok("FADE 20 %%: largest step across three seams %.2e (hard: %.3f)" % (soft, hard))

        # --- REV: END -> START, and a reversed loop with a fade ----------------
        held, _ = take(h, patch(wav, rev=1.0), 12000, 512, "rev")
        assert held[200] > 0.3 and held[200] > held[6000] > held[11000], (held[200], held[6000], held[11000])
        ok("REV: plays the ramp downward (%.3f -> %.3f)" % (held[200], held[11000]))
        held, _ = take(h, patch(wav, rev=1.0, fade=0.4, **loop), 36000, 512, "revfade")
        assert max(abs(v) for v in held[30000:36000]) > 0.05
        rsoft = max_step(held, 2000, 36000)
        assert rsoft < 1e-3, rsoft
        ok("REV + loop + FADE: carried, largest step %.2e" % rsoft)

        # --- readouts (format_param) -------------------------------------------
        h.ok({"type": "load_patch", "patch": patch(wav, **loop)})
        eng = h.c.state()["instruments"]["synth"]["engine"]
        disp = eng["display"]
        assert disp["root"] == "C4", disp
        assert disp["loop_in"] == "%.2f s" % (0.25 * (N - 1) / 48000.0), disp
        assert disp["end"] == "%.2f s" % ((N - 1) / 48000.0), disp
        ok("display: ROOT %s, LOOP-IN %s, END %s" % (disp["root"], disp["loop_in"], disp["end"]))

        # --- v1 patch ids: `level` and `loop` are dropped, not refused ----------
        doc = patch(wav, level=0.3, loop=1.0)
        h.ok({"type": "load_patch", "patch": doc})
        eng = h.c.state()["instruments"]["synth"]["engine"]
        assert "level" not in eng["params"] and "loop" not in eng["params"], eng["params"]
        ok("v1 patch: level and loop are ids this engine no longer has; dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
