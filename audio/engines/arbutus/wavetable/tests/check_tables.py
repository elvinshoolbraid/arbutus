#!/usr/bin/env python3
"""The committed wavetables are what the script writes, byte for byte.

A library asset in this repo is a *build*: the script is the source and the WAV
is its output (repo CLAUDE.md § Library, `audio/tools/mkwavetable.py`). This
regenerates them into a temporary directory and compares, so a change to the
generator that nobody re-ran is a failing test rather than a silent drift
between the file and the thing that claims to make it.

Also checks the file convention the box depends on: 16-bit mono, and a length
that is a whole number of 2048-sample frames.
"""

import filecmp
import os
import subprocess
import sys
import tempfile
import wave

FRAME = 2048


def main(argv):
    script, committed = argv[1], argv[2]
    names = sorted(f for f in os.listdir(committed) if f.endswith(".wav"))
    if not names:
        print("no wavetables in %s" % committed)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, script, tmp], check=True,
                       stdout=subprocess.DEVNULL)
        for n in names:
            a, b = os.path.join(committed, n), os.path.join(tmp, n)
            if not os.path.exists(b):
                print("FAIL %s: the generator no longer writes it" % n)
                return 1
            if not filecmp.cmp(a, b, shallow=False):
                print("FAIL %s: committed file differs from the generator" % n)
                return 1
            with wave.open(a) as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2:
                    print("FAIL %s: not 16-bit mono" % n)
                    return 1
                if w.getnframes() % FRAME:
                    print("FAIL %s: %d samples is not whole 2048-frames"
                          % (n, w.getnframes()))
                    return 1
            print("ok - %s: %d frames, reproducible" % (n, w.getnframes() // FRAME))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
