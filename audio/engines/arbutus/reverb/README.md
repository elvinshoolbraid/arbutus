# arbutus/reverb

The emulator's room, in C++. Four parallel feedback combs into two series
allpasses — Schroeder's original topology, delay lengths tuned at 44.1 kHz and
scaled by both the run's sample rate and SIZE. A direct rewrite of
`emulator/engines/effects.py` `SchroederReverb`, kept arithmetic-for-arithmetic
identical. `caps: audio_in, audio_out`. `honours_env: false`.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `size` | 0 … 1 | 0.5 | Moves the delay lengths (×0.5 … ×1.5) **and** the comb feedback (0.76 … 0.86) together, so every position is a room and not a ringing artefact. Splitting them into two knobs would let you build the bad combinations. |
| 1 | `mix` | 0 … 1 | 0.25 | Dry/wet. |

These are the Python's own defaults, and they were already tuned; DDR-044 is
satisfied by inheritance rather than by a re-range.

## Notes

- **Mono in the Python, stereo here**: two independent chains with the same
  parameters. A mono input therefore gives two identical channels, which is
  what makes the 1-D golden comparable against the left one.
- The Python vectorises each comb over a block with `idxs = (arange(M)+pos) % N`
  and writes back to the same indices it read. Because M < N that is exactly a
  circular buffer of length N whose read and write pointer are the same slot:
  every read is the sample written N steps ago. Here each line is a ring sized
  for SIZE 1, read N behind the write, which gives the same numbers at a fixed
  SIZE.
- **The Python is block-size dependent; this is not.** When a block is longer
  than a delay line the Python writes the same slot twice and only the last
  write survives. At SIZE 0.5 / 48 kHz the shortest line is the second allpass
  at 479 samples, so the golden is captured in blocks of 256
  (`tests/tools/make_fx_goldens.py`). `ctest -R ragged_reverb` proves this box
  gives the same samples at 512, 128 and 97.
- **A SIZE change keeps the tail** (#630; the one deliberate difference from the
  Python). The Python's `_build()` allocates fresh, empty lines on every SIZE
  change, and this box used to clear them to match: one encoder detent cut the
  tail dead (−240 dB), and a route or the SPACE macro on SIZE kept emptying
  it. Now a SIZE change only moves the read distance and the comb feedback;
  what is in the lines plays on. Measured (`ctest -R fx_moves`): one detent
  mid-tail +0.1 dB against no move; a 0.45 … 0.55 triangle route, one step per
  128-frame block, −1.3 dB. A jump in read distance is a small discontinuity
  in each line (not yet heard on the board: a fast route may grain); smoothing (#44)
  can now build on this. Buffers are still sized once in `create()`; only a
  `reset()` or a sample-rate change clears them.

## Tests

`ctest -R reverb` — the Python conformance golden, ragged blocks, `--rt-guard`;
SIZE moving mid-tail is `ctest -R fx_moves` (`arbutus-run --at`, #630).

**Conformance:** `tests/golden/reverb.npy`, captured from the Python class,
replays through this box at **−144.5 dBFS** peak error (tolerance −100).

**Bench:** 4.00 µs/block, 0.04 % of a core at 512 frames / 48 kHz.
