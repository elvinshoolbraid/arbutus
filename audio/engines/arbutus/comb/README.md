# arbutus/comb

A feedback comb you play like a note. A short delay line fed back on itself
through a one-pole lowpass: strike it and it rings at a pitch, drive it and it
is a resonator. `caps: audio_in, audio_out`. `honours_env: false` — it is an
effect, so the ENV page is greyed (§3.3).

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `tune` | −24 … +24 st | 0 | Semitones around **A2 = 110 Hz**, so the knob is a keyboard rather than a time in milliseconds. ±2 octaves covers 27.5 Hz (a felt thump) to 440 Hz (a clear pitch); further down the delay line stops being heard as a note and further up it stops being heard at all. |
| 1 | `feedback` | 0 … 0.98 | 0.8 | 1.0 is an oscillator, not an effect — it never decays and the level depends on what you fed it. 0.98 is about 1.5 s of ring at 110 Hz, which is as long as anything musical needs. |
| 2 | `damp` | 0 … 1 | 0.3 | One-pole lowpass **inside** the loop, so each pass is darker than the last — a string, not a tin can. 1 still leaves 5 % of the loop passing per sample: dark, never dead. |
| 3 | `mix` | 0 … 1 | 0.5 | Dry/wet. |

Default: a clear A2 ring at about 1 second, half wet — audible the moment it is
dropped into a rack, which is what DDR-044 asks of every default.

## Notes

- The delay is read with **linear interpolation**, so sweeping TUNE glides
  instead of stepping between integer delays.
- **TUNE lands on the note at every DAMP.** The loop's lowpass delays the ring
  as well as darkening it, more as DAMP rises and on high notes, so its phase
  delay at the note is taken off the line once a block (one `arg` of a complex
  number, no per-sample cost). Before #630 the ring went flat with DAMP: −6 ct
  at TUNE +24 and DAMP 0.3, −88 ct at DAMP 0.9. Now within ±1 ct
  (`ctest -R fx_moves`).
- A DC blocker sits on the way *in* to the loop. A feedback comb has a DC gain
  of 1/(1−FDBK) — ten times at 0.8 — so any offset in the input (a bare impulse
  has plenty) would pile up in the line as a slow ramp under the note. The dry
  path is untouched.
- The delay line is sized once in `create()` for the lowest note at the run's
  sample rate. `process()` allocates nothing; `set_param()` is pure arithmetic,
  because a macro may drive it every block from the audio thread (§6.2).

## Tests

`ctest -R comb` — golden, ragged blocks, `--rt-guard`; the tuning at every DAMP
is `ctest -R fx_moves` (#630). There is no Python
original (a per-sample feedback loop was not affordable there), so
`tests/golden/comb.npy` was frozen from this box after checking peak, RMS, DC
and finiteness, and after confirming the tail rings on the harmonic series of
the tuned fundamental. Re-frozen by #630 (the tuning fix moves it): peak
0.582, RMS 0.0566, DC +2.4e−04, finite.

**Bench:** 5.76 µs/block, 0.05 % of a core at 512 frames / 48 kHz.
