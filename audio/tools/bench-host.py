#!/usr/bin/env python3
"""Time the host's own offline render (audio/PI.md § 2).

Starts `arbutus-audio --offline --rt-guard` on a private socket, loads each
scenario, holds its notes and renders ten seconds, and prints wall-clock time
against audio time. This is the number that includes the pipeline, the rack
and the mixer, not one box alone. `rt_allocs` is the host's own net-growth
alarm over every block rendered (PROTOCOL.md § Offline) -- 0 or it is a bug.

    audio/tools/bench-host.py [--build audio/build] [--seconds 10] [--block 128]
    bench-host.py --exe SLOT/audio/arbutus-audio --boxes SLOT/boxes   # a deployed slot

`--exe` and `--boxes` default to `--build`'s tree. A slot filled by a cross deploy
has no build tree it refreshed (#1127): measure what it installed, by name.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "arbutus-ctl"))
from arbutus_ctl import connect  # noqa: E402

LIB = os.path.normpath(os.path.join(HERE, "..", "library"))


def doc(rel):
    with open(os.path.join(LIB, rel)) as f:
        return json.load(f)


# (name, patch, rack or None, notes). A rack replaces the patch's own FX rack.
SCENARIOS = [
    ("osc cold-glass, 1 note", "synth/osc/cold-glass.patch", None, [60]),
    ("osc cold-glass + colourbass.rack, 1 note", "synth/osc/cold-glass.patch",
     "fx/racks/colourbass.rack", [60]),
    ("va glass-reed + colourbass.rack, 8 notes", "synth/va/glass-reed.patch",
     "fx/racks/colourbass.rack", [48, 52, 55, 59, 60, 64, 67, 71]),
    ("wavetable init + colourbass.rack, 8 notes", "synth/wavetable/init.patch",
     "fx/racks/colourbass.rack", [48, 52, 55, 59, 60, 64, 67, 71]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="audio/build")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--block", type=int, default=128)
    ap.add_argument("--exe", help="the host binary; default BUILD/host/arbutus-audio")
    ap.add_argument("--boxes", help="the built boxes; default BUILD/boxes")
    a = ap.parse_args()
    exe = a.exe or os.path.join(a.build, "host", "arbutus-audio")
    boxes = a.boxes or os.path.join(a.build, "boxes")
    sr = 48000
    frames = int(a.seconds * sr)
    sock = os.path.join(tempfile.mkdtemp(), "bench.sock")
    host = subprocess.Popen([exe, "--offline",
                             "--quiet", "--rt-guard", "--block", str(a.block),
                             "--socket", sock, "--library", boxes,
                             "--library", LIB])
    worst = 0.0
    try:
        with connect(sock, timeout=600.0) as c:
            c.hello()
            for name, patch, rack, notes in SCENARIOS:
                c.ok({"type": "load_patch", "instrument": "synth", "patch": doc(patch)})
                if rack:
                    c.ok({"type": "load_rack", "instrument": "synth", "rack": doc(rack)})
                for n in notes:
                    c.ok({"type": "note", "on": True, "note": n, "vel": 0.9})
                # settle the crossfades; the file is only there so the reply has a peak
                peak = c.ok({"type": "render", "frames": sr // 4, "format": "wav16",
                             "file": os.path.join(os.path.dirname(sock), "settle.wav")})["peak"]
                t0 = time.perf_counter()
                r = c.ok({"type": "render", "frames": frames})
                wall = time.perf_counter() - t0
                for n in notes:
                    c.ok({"type": "note", "on": False, "note": n, "vel": 0.0})
                c.ok({"type": "render", "frames": sr})            # let the tails go
                load = 100.0 * wall / a.seconds
                worst = max(worst, load)
                print(f"{name}: {a.seconds:.0f} s in {wall:.3f} s = {load:.1f}% of real time "
                      f"({a.seconds / wall:.0f}x), peak {peak:.2f}, rt_allocs {r.get('rt_allocs')}")
            c.request({"type": "shutdown"})
    finally:
        try:
            host.wait(timeout=10)
        except subprocess.TimeoutExpired:
            host.terminate()
    print(f"worst: {worst:.1f}% of one core at block {a.block}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
