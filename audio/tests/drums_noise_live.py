#!/usr/bin/env python3
"""#647: noise, drum and dbox answer what a player does to a sounding note.

The code review of m-engines-sound (findings 7, 9, 10) found three things no
golden looks at, because goldens hold the knobs and the pitch still:
- noise latched CUTOFF and TYPE at note-on, so a held note ignored both knobs;
- the bend and a MOD route to host.pitch did nothing on noise, drum or dbox;
- dbox's HAT choke cut the other hat in one sample, where drum fades 5 ms.

Each check renders with arbutus-run (`--at` moves a knob, `--pitch` sets
host.pitch_st). Standard library only.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

from hostcase import read_wav

SR = 48000
MOVE = 24000                   # the knob or the pitch moves here (a block start at 512)


def rms(x):
    return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0


def brightness(x):
    """RMS of the first difference over the RMS: a lowpass's cutoff, roughly."""
    d = [x[i] - x[i - 1] for i in range(1, len(x))]
    return rms(d) / rms(x)


def level_at(x, f, a, n):
    w = 2.0 * math.pi * f / SR
    re = im = 0.0
    for i in range(n):
        v = x[a + i] * (0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)))
        re += v * math.cos(w * i)
        im -= v * math.sin(w * i)
    return math.hypot(re, im)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="arbutus-run")
    ap.add_argument("--boxes", required=True, help="the box library root")
    ap.add_argument("--audio-root", required=True)
    o = ap.parse_args()
    fails = 0

    def check(good, text):
        nonlocal fails
        print("%s %s" % ("ok -" if good else "FAIL", text))
        fails += not good

    with tempfile.TemporaryDirectory() as tmp:
        def render(box, notes, extra, length=48000):
            path = os.path.join(tmp, "notes.txt")
            with open(path, "w") as f:
                f.write(notes)
            out = os.path.join(tmp, "out.wav")
            subprocess.run([o.run, os.path.join(o.boxes, "arbutus", box), "--notes", path,
                            "--len", str(length), "--sr", str(SR), "--wav-bits", "32",
                            "--out", out] + extra, check=True, stdout=subprocess.DEVNULL)
            return read_wav(out)[1]

        held = "0 on 60 0.9\n47000 off 60\n"
        env = ["--env", "0.002", "0.05", "1.0", "0.1"]
        before = slice(MOVE - 9600, MOVE)
        after = slice(MOVE + 2400, MOVE + 12000)

        # noise: CUTOFF, TYPE and the pitch move a note already sounding.
        for what, extra, falls in (
                ("CUTOFF 4000 -> 200 Hz", ["--at", "%d:cutoff=200" % MOVE], True),
                ("TYPE WHITE -> PINK at 12 kHz",
                 ["--set", "cutoff=12000", "--at", "%d:type=1" % MOVE], True),
                ("pitch_st -24 st", ["--pitch", "%d:-24" % MOVE], True),
                ("pitch_st +12 st, CUTOFF 1000",
                 ["--set", "cutoff=1000", "--pitch", "%d:12" % MOVE], False)):
            x = render("noise", held, env + extra)[0]
            b, a = brightness(x[before]), brightness(x[after])
            moved = a < 0.8 * b if falls else a > 1.25 * b
            check(moved, "noise, held note, %s: brightness %.3f -> %.3f" % (what, b, a))

        # dbox: pad 0 an open HAT hard left, pad 1 a HAT hard right that chokes
        # it; pad 2 a TOM with no pitch drop and no noise, for the bend.
        kit = os.path.join(tmp, "kit.json")
        with open(kit, "w") as f:
            json.dump({"format": 1, "kind": "kit", "name": "T647", "pads": {
                "0": {"params": {"mode": 0.6, "decay": 1.0, "pan": 0.0}},
                "1": {"params": {"mode": 0.6, "pan": 1.0}},
                "2": {"params": {"mode": 0.4, "snap": 0.0, "tone": 0.0, "drive": 0.0}}}}, f)
        left = render("dbox", "0 on 53 1.0\n6000 on 54 1.0\n", ["--asset", "kit=" + kit],
                      length=12000)[0]
        pre = rms(left[5880:6000])
        first = rms(left[6000:6060])           # the first 1.25 ms of the fade
        tail = max(abs(v) for v in left[6000 + 241:6000 + 2400])
        check(first > 0.5 * pre and tail < 1e-6,     # float steps of 1/240 may leave a sample or two
              "dbox HAT choke fades: RMS %.4f before, %.4f in its first 1.25 ms, "
              "%.1e after 5 ms" % (pre, first, tail))

        for st, f_want, f_not in ((0, 110.0, 220.0), (12, 220.0, 110.0)):
            x = render("dbox", "0 on 55 1.0\n", ["--asset", "kit=" + kit,
                                                  "--pitch", "0:%d" % st], length=9600)[0]
            want, other = level_at(x, f_want, 480, 4096), level_at(x, f_not, 480, 4096)
            check(want > 4.0 * other, "dbox TOM at pitch_st %+d: %g Hz %.3f, %g Hz %.3f" % (
                st, f_want, want, f_not, other))

        # drum: pitch_st +12 plays a pad at twice the rate, so it ends in half
        # the time (the region ends the pad; DEC 4 s keeps the envelope out).
        kit = os.path.join(o.audio_root, "tests", "golden", "kit", "kit.json")
        ends = []
        for st in (0, 12):
            x = render("drum", "0 on 53 1.0\n", ["--asset", "kit=" + kit, "--env", "0", "4", "0", "0",
                                                 "--pitch", "0:%d" % st])[0]
            ends.append(max(i for i, v in enumerate(x) if v != 0.0) + 1)
        ratio = ends[1] / ends[0]
        check(0.48 <= ratio <= 0.52,
              "drum pad at pitch_st +12 ends at frame %d, at 0 %d (x%.3f)" % (ends[1], ends[0], ratio))

    if fails:
        print("%d check(s) failed: a box ignored a knob or the pitch, or the choke clicked" % fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
