#!/usr/bin/env python3
"""Freeze the fixed FX test input, and arbutus/reverb's golden from the Python.

The C++ box is a rewrite of `emulator/engines/effects.py` SchroederReverb, so
the reference has to come from the Python, not from the rewrite. This runs the
emulator's class on a fixed input and writes both arrays next to the box:

    emulator/venv/bin/python audio/tests/tools/make_fx_goldens.py

It writes three things:

  audio/tests/golden/fx_input.npy          the dry input every Group A box runs
  audio/tests/golden/fx_side.npy           a pulse train, for arbutus/duck
  engines/arbutus/reverb/tests/golden/reverb.npy   the Python capture

It reads `emulator/` and writes only inside `audio/` (audio/CLAUDE.md).

Block size matters *on the Python side only*. `_run_comb` indexes with
`(arange(M) + pos) % N` and writes back to the same indices, so when a block is
longer than a delay line the same slot is written twice and only the last write
survives -- the Python's output then depends on the block size. At SIZE 0.5 and
48 kHz the shortest line is the second allpass at 479 samples, so this renders
in blocks of 256. The C++ box is a per-sample circular buffer and has no such
dependence, which is the point of the ragged-block test.
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIO = os.path.abspath(os.path.join(HERE, "..", ".."))
EMU = os.path.abspath(os.path.join(AUDIO, "..", "emulator"))
sys.path.insert(0, EMU)

from engines.effects import SchroederReverb   # noqa: E402

SR = 48000
N = 24000                 # half a second, enough for the tail to matter
BLOCK = 256
SIZE, MIX = 0.5, 0.25
OUT = os.path.join(AUDIO, "engines", "arbutus", "reverb", "tests", "golden")
SHARED = os.path.join(AUDIO, "tests", "golden")


def make_input():
    """Impulses plus a decaying noise burst: transients and a body to smear."""
    rng = np.random.RandomState(20260915)
    x = np.zeros(N, dtype=np.float32)
    for k in (0, 4000, 9000):
        x[k] = 0.9
    burst = rng.uniform(-1.0, 1.0, 6000).astype(np.float32)
    burst *= np.exp(-np.arange(6000, dtype=np.float32) / 1200.0).astype(np.float32)
    x[12000:18000] += (0.7 * burst).astype(np.float32)
    return x.astype(np.float32)


def make_side():
    """Four 60 ms bursts on the beat at 120 bpm: something to duck under."""
    s = np.zeros(N, dtype=np.float32)
    step = 12000                                  # a beat at 120 bpm, 48 kHz
    env = np.exp(-np.arange(2880, dtype=np.float32) / 700.0).astype(np.float32)
    for k in range(0, N, step):
        n = min(2880, N - k)
        t = np.arange(n, dtype=np.float32) / SR
        s[k:k + n] = (0.8 * env[:n] * np.sin(2.0 * np.pi * 70.0 * t)).astype(np.float32)
    return s


def main():
    x = make_input()
    side = make_side()
    rev = SchroederReverb(sample_rate=SR)
    rev.set_size(SIZE)
    rev.set_mix(MIX)
    y = np.concatenate([rev.process(x[i:i + BLOCK]) for i in range(0, N, BLOCK)])
    y = np.asarray(y, dtype=np.float32)

    os.makedirs(OUT, exist_ok=True)
    os.makedirs(SHARED, exist_ok=True)
    np.save(os.path.join(SHARED, "fx_input.npy"), x)
    np.save(os.path.join(SHARED, "fx_side.npy"), side)
    np.save(os.path.join(OUT, "reverb.npy"), y)
    print("input : %d frames, peak %.4f, rms %.4f" % (len(x), np.abs(x).max(),
                                                      float(np.sqrt((x.astype(np.float64) ** 2).mean()))))
    print("golden: %d frames, peak %.4f, rms %.4f, dc %.2e, finite %s"
          % (len(y), np.abs(y).max(), float(np.sqrt((y.astype(np.float64) ** 2).mean())),
             float(y.mean()), bool(np.isfinite(y).all())))
    print("side  : %d frames, peak %.4f" % (len(side), np.abs(side).max()))
    print("wrote  %s and %s" % (SHARED, OUT))


if __name__ == "__main__":
    main()
