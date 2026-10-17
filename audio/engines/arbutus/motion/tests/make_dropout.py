#!/usr/bin/env python3
"""Writes dropout.npy, the --motion feed behind motion_dropout (#598): (6, rows),
one row per 128-frame block at 48 kHz. The case lies flat (az = 1 g), the
sensor drops out (the host sends all-zero motion, "no sensor"), then comes
back. Nothing moved, so every output must stay exactly 0: a step to or from
the all-zero feed is not a jolt. Run with the emulator's venv python (numpy):
emulator/venv/bin/python make_dropout.py dropout.npy

  0.00-0.17 s  flat: az = 1 g
  0.17-0.33 s  no sensor: all zeros
  0.33-0.50 s  flat again
"""

import sys

import numpy as np

rows = 188
m = np.zeros((6, rows), dtype=np.float32)
m[2, :63] = 1.0
m[2, 125:] = 1.0
np.save(sys.argv[1], m)
