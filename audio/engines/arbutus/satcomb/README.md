# arbutus/satcomb

`arbutus/comb` with the nonlinearity **inside** the feedback loop:

```
y = drive*x + fb*d ;   y = y / (1 + |y|)
```

That one line is the whole difference. A linear comb at FDBK 0.95 either rings
forever or it does not; this one compresses as it rings, so a note blooms,
squashes, and dies with a changing timbre. It is the "colourbass" class, and it
is exactly what the Python could not buy at any useful count — `board_bench.py
--effects` put serial DSP at ~30× a vectorised form, so there is no Python
original to conform to. `caps: audio_in, audio_out`. `honours_env: false`.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `tune` | −24 … +24 st | 0 | Semitones around A2 = 110 Hz. Same reasoning as `arbutus/comb`. |
| 1 | `feedback` | 0 … **1.0** | 0.9 | The saturator bounds the delay line to ±1 by construction, so 1.0 is a sustained, self-limiting drone rather than a blow-up. This is the one knob that is wider here than on the linear comb, and the nonlinearity is why. |
| 2 | `damp` | 0 … 1 | 0.35 | One-pole in the loop; the harmonics the saturator makes need somewhere to go or the top octave turns to fizz. |
| 3 | `drive` | 0.5 … 12 | 2 | **LOG**: the audible half of a drive control lives in its bottom third. The saturator has unity slope at zero, so DRIVE changes how hard the loop is *hit*, not how much comes back — the loop gain is still FDBK and FDBK alone. It is also an input level: hard drive is louder and more squashed, which is the point. |
| 4 | `mix` | 0 … 1 | 0.6 | Dry/wet, wet-leaning because the saturated tail is the sound. |

Default: a 110 Hz note that blooms and compresses over about two seconds.

## Notes

- The DC blocker sits **inside** the loop here, not on the input. The saturator
  is an odd function, but a transient hits it asymmetrically and the small DC
  step that results is then multiplied by the loop's DC gain of 1/(1−FDBK) —
  20× at 0.95. Blocking inside the loop is what keeps the line centred.
- **TUNE lands on the note at every DAMP**, as on `arbutus/comb`: the phase
  delay of the loop's lowpass *and* of its DC blocker (a phase lead, which had
  the ring +10 ct sharp at TUNE 0, DAMP 0) is taken off the line once a block.
  Before #630: −86 ct at TUNE +24, DAMP 0.9; now within ±1 ct, measured with
  DRIVE 1 on a small impulse, where the saturator is near linear
  (`ctest -R fx_moves`). A hard-driven loop still bends the pitch a little:
  the saturator is outside the correction.
- Linear interpolation on the delay read, buffer sized in `create()`, nothing
  allocated in `process()` — as `arbutus/comb`.

## Tests

`ctest -R satcomb` — golden, ragged blocks, `--rt-guard`; the tuning at every
DAMP is `ctest -R fx_moves` (#630). Re-frozen by #630 (peak 0.620, RMS 0.0818,
DC +1.3e−04, finite).
`tests/golden/satcomb.npy` was frozen from this box after checking peak, RMS,
DC and finiteness, and after confirming the tail sits on a harmonic of the
tuned fundamental (the 12th, at 659.6 Hz for TUNE −12).

**Bench:** 11.62 µs/block, **0.11 % of a core** at 512 frames / 48 kHz — the
most expensive stock effect, and still eight hundred instances from a core.
