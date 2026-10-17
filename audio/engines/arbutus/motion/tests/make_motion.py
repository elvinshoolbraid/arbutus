#!/usr/bin/env python3
"""Writes motion.npy, the --motion feed behind motion_golden: (6, rows), one row
per 128-frame block at 48 kHz (375 rows = 1 s). Run with the emulator's venv
python (numpy):  emulator/venv/bin/python make_motion.py motion.npy

  0.0-0.2 s  flat on a table: az = 1 g, nothing else
  0.2-0.6 s  tilted: ax ramps to +0.7 g, ay to -0.4 g, az falls to match
  0.4 s      one block of a 1.5 g jolt on x (a tap on the case)
  0.6-1.0 s  spun: gz +180 deg/s, then -400 deg/s (past full scale)
  0.8 s on   a small tremor (+-0.05 g on y at 12 Hz)
"""

import sys

import numpy as np

rows = 375
t = np.arange(rows) * 128 / 48000.0
m = np.zeros((6, rows), dtype=np.float32)
m[2] = 1.0
tilt = np.clip((t - 0.2) / 0.2, 0.0, 1.0) * (t < 0.6)
m[0] = 0.7 * tilt
m[1] = -0.4 * tilt
m[2] = np.sqrt(np.maximum(0.0, 1.0 - m[0] ** 2 - m[1] ** 2))
m[0, int(0.4 * 375)] += 1.5
m[5] = np.where((t >= 0.6) & (t < 0.8), 180.0, 0.0) + np.where(t >= 0.8, -400.0, 0.0)
m[1] += np.where(t >= 0.8, 0.05 * np.sin(2 * np.pi * 12.0 * t), 0.0)
np.save(sys.argv[1], m.astype(np.float32))
