#!/usr/bin/env python3
"""#647: the string's DECAY is seconds to -60 dB and DAMP does not detune,
up the keyboard and not only at A4; the bow's noise leaves no low end.

The code review of m-engines-sound (findings 3-5) measured the three claims
failing where no golden looks:
- DECAY 2 s at DAMP 0 rang 0.40-1.18 s above C6: the loop read its fraction of
  a sample by linear interpolation, a lowpass whose loss nothing counted.
- DAMP 0.75 put C7 +15 ct sharp: the one-pole's phase delay came out of the
  line at its DC value, which is too large at a high note's frequency.
- The bow's "first difference of noise" shared one state between the two
  strings, so each string got white noise: -38.9 dB below 40 Hz, where one
  state per string gives under -45.

Each check below reads a render of arbutus-run (no host). Standard library
only: the pitch is the peak of the windowed spectrum on a one-cent grid, the
ring time is the fundamental's level at 0.05 s and 0.35 s.
"""

import argparse
import math
import os
import subprocess
import sys
import tempfile

from hostcase import read_wav

SR = 48000


def hz(note):
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def level_at(x, f, a, n):
    """|DTFT| of the Hann-windowed x[a:a+n] at f Hz."""
    w = 2.0 * math.pi * f / SR
    re = im = 0.0
    for i in range(n):
        v = x[a + i] * (0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)))
        re += v * math.cos(w * i)
        im -= v * math.sin(w * i)
    return math.hypot(re, im)


def cents_off(x, f, a, n, span=40):
    """Where the spectrum of x[a:a+n] peaks near f, in cents, on a 1 ct grid
    refined by a parabola through the top three points."""
    grid = [level_at(x, f * 2.0 ** (c / 1200.0), a, n) for c in range(-span, span + 1)]
    k = max(range(len(grid)), key=grid.__getitem__)
    if 0 < k < len(grid) - 1:
        l, m, r = grid[k - 1], grid[k], grid[k + 1]
        den = l - 2.0 * m + r
        k += 0.5 * (l - r) / den if den else 0.0
    return k - span


def t60(x, f):
    """Seconds to -60 dB of the fundamental, from its level at 0.05 and 0.35 s."""
    n = 2048
    a1, a2 = int(0.05 * SR), int(0.35 * SR)
    db = 20.0 * math.log10(level_at(x, f, a2, n) / level_at(x, f, a1, n))
    return -60.0 * (a2 - a1) / SR / db


def low_end_db(x, cutoff=40.0, sections=4):
    """Energy under `cutoff` Hz over the whole energy, in dB: x through an
    eighth-order Butterworth lowpass (four RBJ sections at the Butterworth Qs),
    steep enough that the 131 Hz fundamental of C3 is 80 dB down."""
    y = list(x)
    w = 2.0 * math.pi * cutoff / SR
    for k in range(sections):
        q = 1.0 / (2.0 * math.cos(math.pi * (2 * k + 1) / (4 * sections)))
        alpha = math.sin(w) / (2.0 * q)
        a0 = 1.0 + alpha
        b0 = (1.0 - math.cos(w)) / 2.0 / a0
        b1 = (1.0 - math.cos(w)) / a0
        a1 = -2.0 * math.cos(w) / a0
        a2 = (1.0 - alpha) / a0
        x1 = x2 = y1 = y2 = 0.0
        for i, v in enumerate(y):
            o = b0 * v + b1 * x1 + b0 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, v, y1, o
            y[i] = o
    # The first 0.5 s is the filter settling and the bow starting.
    skip = SR // 2
    num = sum(v * v for v in y[skip:])
    den = sum(v * v for v in x[skip:])
    return 10.0 * math.log10(num / den)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="arbutus-run")
    ap.add_argument("--boxes", required=True, help="the box library root")
    o = ap.parse_args()
    box = os.path.join(o.boxes, "arbutus", "string")
    fails = 0

    with tempfile.TemporaryDirectory() as tmp:
        def render(note, sets, length=24000):
            notes = os.path.join(tmp, "notes.txt")
            with open(notes, "w") as f:
                f.write("0 on %d 0.9\n%d off %d\n" % (note, length - 100, note))
            out = os.path.join(tmp, "out.wav")
            cmd = [o.run, box, "--notes", notes, "--len", str(length), "--sr", str(SR),
                   "--env", "0.002", "0.05", "1.0", "0.6", "--wav-bits", "32", "--out", out]
            for k, v in sets.items():
                cmd += ["--set", "%s=%s" % (k, v)]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            return read_wav(out)[1][0]

        def check(good, text):
            nonlocal fails
            print("%s %s" % ("ok -" if good else "FAIL", text))
            fails += not good

        bare = {"decay": 2, "body": 0, "spread": 0}
        # DECAY: the fundamental's T60 within 5 % of the knob. DAMP 0 up to C7;
        # DAMP 0.35 (the default) where the loop gain's 0.9999 ceiling does not
        # bite at 2 s (README: up to about C5).
        for damp, notes in ((0, (60, 72, 84, 90, 96)), (0.35, (60, 69, 72))):
            for note in notes:
                x = render(note, dict(bare, damp=damp))
                t = t60(x, hz(note))
                check(abs(t - 2.0) <= 0.10,
                      "DECAY 2 s, DAMP %.2f, note %d rings %.2f s" % (damp, note, t))
        # Pitch: within 2 ct, read 5-48 ms after the pluck (a DAMP 0.75 C7 is
        # 60 dB down in about 0.3 s).
        for damp in (0, 0.5, 0.75):
            for note in (60, 84, 90, 96):
                x = render(note, dict(bare, damp=damp), length=4800)
                c = cents_off(x, hz(note), 240, 2048)
                check(abs(c) <= 2.0, "DAMP %.2f, note %d is %+.1f ct" % (damp, note, c))
        # BOW: nothing of note under 40 Hz on a held C3.
        x = render(48, {"exc": 1, "decay": 8}, length=SR * 2)
        db = low_end_db(x)
        check(db <= -44.0, "BOW C3, energy under 40 Hz %.1f dB of the whole" % db)

    if fails:
        print("%d check(s) failed: the string's DECAY, tuning or bow" % fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
