#!/usr/bin/env python3
"""#930: arbutus/plate's tail is what DECAY says, and the longest one is long.

The golden pins the samples of one setting; it cannot say whether the tail is
the length the knob reads, or whether the top of the knob rings for ever. This
renders a short noise burst through the box with arbutus-run and fits the
decay of the windowed level (dB against time, from 5 to 35 dB below the peak,
extrapolated to 60 dB):

  * DECAY reads in seconds: 1, 3.5 and 10 s land within 25 %;
  * SIZE does not move the tail: DECAY 5 at SIZE 0.1 and 1.0 within 25 %;
  * the longest tail (DECAY 30, SIZE 1, DAMP 0) is at least 20 s, and at
    least ten times the room's (arbutus/reverb at SIZE 1);
  * that tail falls: the last second is at least 10 dB under the second
    (a loop gain a few % over 1 grows, and passes a looser bar).

Standard library only (hostcase.read_wav); arbutus-run renders, no host.
"""

import argparse
import math
import os
import struct
import subprocess
import sys
import tempfile

from hostcase import read_wav

SR = 48000
WIN = SR // 10          # 100 ms


def write_wav(path, x):
    data = bytearray()
    for v in x:
        data += struct.pack("<ff", v, v)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, SR, SR * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", len(data)) + bytes(data))


def burst(seconds):
    """50 ms of centred, repeatable noise (an LCG), then silence."""
    n, m, s, x = int(seconds * SR), SR // 20, 12345, []
    for i in range(n):
        if i < m:
            s = (1103515245 * s + 12345) & 0x7FFFFFFF
            x.append(0.5 * (s / 0x3FFFFFFF - 1.0))
        else:
            x.append(0.0)
    return x


def levels(chans):
    """dB of each 100 ms window, both channels' energy together."""
    n = len(chans[0]) // WIN
    out = []
    for w in range(n):
        e = sum(v * v for c in chans for v in c[w * WIN:(w + 1) * WIN])
        out.append(10 * math.log10(e / (2 * WIN)) if e > 0 else -400.0)
    return out


def t60(db):
    """Least-squares slope from 5 to 35 dB under the peak, as seconds to -60 dB."""
    pk = max(range(len(db)), key=lambda i: db[i])
    pts = [(i * WIN / SR, v) for i, v in enumerate(db)
           if i > pk and db[pk] - 35 <= v <= db[pk] - 5]
    if len(pts) < 3:
        return float("nan")
    mx = sum(t for t, _ in pts) / len(pts)
    my = sum(v for _, v in pts) / len(pts)
    slope = sum((t - mx) * (v - my) for t, v in pts) / sum((t - mx) ** 2 for t, _ in pts)
    return -60.0 / slope if slope < 0 else float("inf")


def render(run, box, tmp, seconds, sets, tag):
    src = os.path.join(tmp, f"in_{seconds}.wav")
    if not os.path.exists(src):
        write_wav(src, burst(seconds))
    out = os.path.join(tmp, f"{tag}.wav")
    cmd = [run, box, "--in", src, "--sr", str(SR), "--out", out]
    for kv in sets:
        cmd += ["--set", kv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"arbutus-run failed: {' '.join(cmd)}\n{r.stdout}{r.stderr}")
    return levels(read_wav(out)[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--boxes", required=True)
    a = ap.parse_args()
    plate = os.path.join(a.boxes, "arbutus", "plate")
    room = os.path.join(a.boxes, "arbutus", "reverb")
    fails = []

    def check(ok, msg):
        print(("ok    " if ok else "FAIL  ") + msg)
        if not ok:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        wet = ["mix=1", "damp=0.35", "predelay=0"]
        for want in (1.0, 3.5, 10.0):
            got = t60(render(a.run, plate, tmp, min(4 + want, 14), wet + [f"decay={want}", "size=0.7"], f"d{want}"))
            check(abs(got / want - 1) <= 0.25, f"DECAY {want} s: tail {got:.2f} s")

        small = t60(render(a.run, plate, tmp, 9, wet + ["decay=5", "size=0.1"], "s01"))
        big = t60(render(a.run, plate, tmp, 9, wet + ["decay=5", "size=1"], "s10"))
        check(abs(small / big - 1) <= 0.25, f"SIZE 0.1 vs 1 at DECAY 5: {small:.2f} s vs {big:.2f} s")

        db = render(a.run, plate, tmp, 16, ["mix=1", "damp=0", "predelay=0", "decay=30", "size=1"], "max")
        longest = t60(db)
        r = t60(render(a.run, room, tmp, 4, ["mix=1", "size=1"], "room"))
        check(longest >= 20.0, f"longest tail {longest:.1f} s (want >= 20)")
        check(longest >= 10 * r, f"longest tail {longest:.1f} s vs the room's {r:.2f} s (want >= 10x)")
        # Never grows, never holds: 13 s on, a 30 s tail is ~25 dB down.
        early = sum(db[10:20]) / 10
        late = sum(db[-10:]) / 10
        check(late <= early - 10, f"falls: last second {late:.1f} dB vs second 2 {early:.1f} dB (want 10 dB down)")

    if fails:
        print(f"plate_tail: {len(fails)} failed")
        return 1
    print("plate_tail: all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
