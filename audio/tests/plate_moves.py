#!/usr/bin/env python3
"""#1215: arbutus/plate's SIZE and PREDELAY moved on a ringing tail.

plate_tail.py and the golden render one static setting, so nothing saw a move:
deleting the glide's `lengths();` (the plate's read distances worked out again
while SIZE or PREDELAY glides) left them all green (t930's review, row 2).
Two cases per knob, each moving it mid-render with `--at`:

  * it lands: a noise burst at 0 s rings; at 0.3 s, mid-tail, the knob makes
    a big move; a second burst at 2 s, when the first tail is ~120 dB down,
    asks what geometry the plate settled on. Its 0.8 s against a render at
    the new setting from the start is at least 40 dB under it (the glide
    snaps on landing, so the two are the same plate), while the old
    setting's is not. Without the glide's `lengths();` the plate keeps its
    old geometry and is ~3 dB off.
  * it does not click: a held 220 Hz sine rings the plate; at 0.5 s the knob
    moves one small step. Through the glide, the largest 1 ms RMS of the
    second difference (a sine's is ~w^2 of it; a step's is all of it) stays
    within 8x the old setting held still (the glide bends the pitch: ~4x). A
    jump instead of the glide (the 50 ms one-pole set to 1) is 25x and more.

Standard library only (hostcase.read_wav_arrays); arbutus-run renders, no host.
"""

import argparse
from array import array
import math
import os
import struct
import subprocess
import sys
import tempfile

from hostcase import read_wav_arrays

SR = 48000
MS = SR // 1000


def write_wav(path, x):
    buf = array("f", bytes(8 * len(x)))
    buf[0::2] = x
    buf[1::2] = x
    if sys.byteorder != "little":
        buf.byteswap()
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + 4 * len(buf)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, SR, SR * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", 4 * len(buf)))
        fh.write(buf)


def two_bursts(seconds, second):
    """50 ms of repeatable noise (an LCG) at 0 s and again at `second`."""
    x = array("f", bytes(4 * int(seconds * SR)))
    s = 12345
    for start in (0, int(second * SR)):
        for i in range(start, start + SR // 20):
            s = (1103515245 * s + 12345) & 0x7FFFFFFF
            x[i] = 0.5 * (s / 0x3FFFFFFF - 1.0)
    return x


def sine(seconds, hz=220.0, amp=0.3):
    return array("f", (amp * math.sin(2 * math.pi * hz * i / SR) for i in range(int(seconds * SR))))


def rms(x, a, b):
    return math.sqrt(sum(v * v for v in x[a:b]) / max(1, b - a))


def diff_rms(x, y, a, b):
    return math.sqrt(sum((u - v) ** 2 for u, v in zip(x[a:b], y[a:b])) / max(1, b - a))


def db(r):
    return 20 * math.log10(r) if r > 0 else -999.0


def roughest(x, a, b):
    """The largest 1 ms RMS of the second difference over [a, b)."""
    best = 0.0
    for w in range(a, b - MS, MS):
        e = sum((x[i] - 2 * x[i - 1] + x[i - 2]) ** 2 for i in range(w, w + MS))
        best = max(best, math.sqrt(e / MS))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--boxes", required=True)
    a = ap.parse_args()
    plate = os.path.join(a.boxes, "arbutus", "plate")
    fails = []

    def check(ok, msg):
        print(("ok    " if ok else "FAIL  ") + msg)
        if not ok:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        def render(src, sets, at=None):
            out = os.path.join(tmp, "out.wav")
            cmd = [a.run, plate, "--in", src, "--sr", str(SR), "--frames", "128", "--out", out]
            for kv in sets:
                cmd += ["--set", kv]
            if at:
                cmd += ["--at", at]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"arbutus-run failed: {' '.join(cmd)}\n{r.stdout}{r.stderr}")
            return read_wav_arrays(out)[1]

        # ---- it lands ----------------------------------------------------
        bursts = os.path.join(tmp, "bursts.wav")
        write_wav(bursts, two_bursts(3.0, 2.0))
        move = int(0.3 * SR)
        b0, b1 = 2 * SR, int(2.8 * SR)
        # Shrinking SIZE is the hard way (the reads speed up).
        for knob, old, new, rest in [("size", "0.9", "0.3", ["predelay=0"]),
                                     ("predelay", "20", "200", ["size=0.7"])]:
            base = ["mix=1", "decay=1", "damp=0.35"] + rest
            moved = render(bursts, base + [f"{knob}={old}"], f"{move}:{knob}={new}")
            still = render(bursts, base + [f"{knob}={old}"])
            ref = render(bursts, base + [f"{knob}={new}"])
            for c in (0, 1):
                level = rms(ref[c], b0, b1)
                landed = db(diff_rms(moved[c], ref[c], b0, b1) / level)
                stayed = db(diff_rms(still[c], ref[c], b0, b1) / level)
                check(landed <= -40.0 and stayed > -10.0,
                      f"{knob.upper()} {old} -> {new} mid-tail, ch {c}: the next burst "
                      f"{landed:.1f} dB off the new setting's (<= -40), the old "
                      f"setting's {stayed:.1f} dB (> -10)")

        # ---- it does not click -------------------------------------------
        held = os.path.join(tmp, "sine.wav")
        write_wav(held, sine(1.0))
        move = SR // 2
        m0, m1 = move, move + SR // 4
        for knob, old, new, rest in [("size", "0.7", "0.6", ["predelay=20"]),
                                     ("predelay", "20", "60", ["size=0.7"])]:
            base = ["mix=1", "decay=3.5", "damp=0.35"] + rest
            moved = render(held, base + [f"{knob}={old}"], f"{move}:{knob}={new}")
            still = render(held, base + [f"{knob}={old}"])
            for c in (0, 1):
                got, lim = roughest(moved[c], m0, m1), roughest(still[c], m0, m1)
                check(got <= 8.0 * lim,
                      f"{knob.upper()} {old} -> {new} on a held sine, ch {c}: roughest 1 ms "
                      f"through the glide {got:.2e} vs {lim:.2e} held still (<= 8x)")

    if fails:
        print(f"plate_moves: {len(fails)} failed")
        return 1
    print("plate_moves: all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
