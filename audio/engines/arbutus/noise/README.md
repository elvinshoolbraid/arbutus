# arbutus/noise

White and pink noise through a lowpass that tracks the key, on
`PolyHost<Voice, 8>`. A rewrite of `emulator/engines/noise_engine.py`.
`caps: audio_out, events_in`. `honours_env: true`.

The cutoff is scaled by the note — `cutoff × 2^((note−60)/12)` — so the noise is
*pitched* without being harmonic. That is the whole idea of the engine and it is
the Python's.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `type` | ENUM `WHITE` `PINK` | `WHITE` | Kellet's pink approximation, as the Python has it. |
| 1 | `cutoff` | 100 … 12000 Hz, **LOG** | 4000 | Was linear 100 … 10000. Linear puts nine tenths of the knob above 1 kHz where noise stops changing character; log gives the bottom of the range — where filtered noise turns into a drum — the detents it deserves (DDR-044). The default moved from 5000 to 4000, where the pitch tracking is most obviously musical across the keyboard. |
| 2 | `level` | 0 … 1 | 0.8 | Voice trim. |

## Notes, both of which are the interesting part

- **The RNG is numpy's.** `NpRandom` reproduces the legacy `RandomState`
  stream exactly: `np.random.seed(s)` is MT19937 `init_genrand(s)`, which is
  what `std::mt19937` does, and `random_sample()` is
  `((a >> 5)·2²⁶ + (b >> 6)) / 2⁵³` from two draws. That is what makes a golden
  *made of noise* a conformance test at all. The seed is `arb_host::rng_seed`,
  which §3.3 put there for exactly this.
- **One stream per voice, not the Python's single global one.** A shared stream
  hands out its draws in whatever order the voices happen to be rendered, so
  where the host chopped the block would change the noise. Per voice, a stream
  advances once per frame that voice sounds and nothing else — which is what
  makes `ctest -R ragged_noise` possible. Voice 0 is seeded with `rng_seed`
  itself, so it is bit-for-bit the Python's stream.
- **One fix to the Python:** its lowpass restarts from `prev = 0.0` on every
  call, so its output depends on the block size — a filter restart every 512
  samples. This box keeps the state. `noise_dsp.hpp` has a
  `lp_reset_per_render` flag that reproduces the Python's behaviour; only the
  conformance test sets it.
- **CUTOFF and TYPE move a note already sounding** (#647). The Python latched
  both at note-on, so a held drone ignored the knobs; the cutoff is now worked
  out every block, and a TYPE change starts the pink filter from rest. With
  the knobs still it is the note-on value to the bit, so the conformance
  golden holds.
- **Pitch moves the cutoff** (#647, *run's call*): the bend, a MOD route to
  `host.pitch`, glide and the unison spread scale it as the note does (an
  octave up is twice the cutoff). Noise has no pitch of its own; the cutoff
  tracking the keyboard is what makes it *pitched*, so it is the one thing a
  bend can move.

## Tests

`ctest -R noise` — golden, ragged blocks, `--rt-guard`; `ctest -R
drums_noise_live` turns CUTOFF and TYPE and bends a held note (#647, red on
the latched engine).

**Conformance:** `ctest -R voice_goldens` replays
`emulator/tests/golden/noise_voice.npy` through `src/noise_dsp.hpp` at
**−97.8 dBFS**, against a per-test tolerance of −90 (§9.4 allows the override).
The noise itself matches to 5e-10; the whole difference is the envelope. The
ENV times cross the ABI as float32, and `float32(0.01)` is 0.0099999998 — just
below the accumulated 480/48000 the envelope's clock reaches — so the attack
ends one sample before the Python's double `0.01` ends it, and the decay ramp
is offset by one sample of its slope (0.3/4800 = 6.3e-5) for the rest of the
capture. The goldens whose times round the other way (`adsr`, `fm_voice`, both
0.004/0.006) land at −144.5.

**Bench:** 7.63 µs/block, 0.07 % of a core at 512 frames / 48 kHz.
