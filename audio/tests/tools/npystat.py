#!/usr/bin/env python3
"""Listen by numbers, before a golden is frozen.

A golden captured from a box that was never checked freezes whatever bug it
had. This prints the four things that catch a bad render without ears: peak,
RMS, DC offset, and whether every sample is finite. Run it with the emulator's
venv python (it needs numpy) on any .npy arbutus-run wrote.

    emulator/venv/bin/python audio/tests/tools/npystat.py out.npy ...
"""

import sys

import numpy as np


def main(argv):
    bad = 0
    for path in argv[1:]:
        a = np.load(path).astype(np.float64)
        flat = a.reshape(-1)
        finite = bool(np.isfinite(flat).all())
        peak = float(np.abs(flat).max()) if flat.size else 0.0
        rms = float(np.sqrt((flat ** 2).mean())) if flat.size else 0.0
        dc = float(flat.mean()) if flat.size else 0.0
        note = []
        if not finite:
            note.append("NOT FINITE")
        if peak == 0.0:
            note.append("SILENT")
        if peak > 4.0:
            note.append("PEAK > 4")
        if abs(dc) > 0.02:
            note.append("DC > 0.02")
        if note:
            bad += 1
        print("%-56s n=%-7d peak=%.4f rms=%.4f dc=%+.2e finite=%s %s"
              % (path, flat.size, peak, rms, dc, finite, " ".join(note)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
