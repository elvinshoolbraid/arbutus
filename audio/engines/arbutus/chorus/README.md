# arbutus/chorus

Modulated short delays: **flanger, chorus and ensemble are the same circuit at
three settings of one knob**, so DELAY is a knob here and not a mode switch.
`caps: audio_in, audio_out`. `honours_env: false`.

Stereo is made by **phase**, not by detuning the two sides: the right channel's
LFO runs SPREAD degrees ahead of the left, so the image opens without either
channel drifting out of tune with the other.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `rate` | 0.02 … 8 Hz, log | 0.55 Hz | 0.02 Hz is a 50-second sweep — the slow drift that makes a pad move without anything audibly wobbling. 8 Hz is where the modulation stops being a chorus and becomes a tremolo/ring sound; past it the sidebands are wider than the effect. Log, because the whole musical range lives under 2 Hz and a linear knob would spend three quarters of its travel above it. |
| 1 | `depth` | 0 … 1 | 0.45 | The sweep is ±90 % of DELAY, so the knob means the same thing at every DELAY setting. Measured at DELAY 8 ms: DEPTH 0 holds the tap at 7.98 ms, 0.25 sweeps 6.19 … 9.77 ms, 0.50 sweeps 4.38 … 11.58 ms, 1.00 sweeps 0.77 … 15.17 ms. It stops at ±90 % rather than ±100 % because a tap that reaches zero is a click, not a chorus. |
| 2 | `delay` | 0.5 … 30 ms, log | 8 ms | **The knob that changes what the box is.** 0.5 ms is a flanger (the comb's first notch sits at 1 kHz); 8 ms is a chorus; 30 ms is the slow doubling people call an ensemble. Below 0.5 ms the comb's first notch is above the audio band and there is nothing to hear; above 30 ms the two copies stop fusing and you hear a slap, which is `arbutus/delay`'s job. Measured: the base tap lands within one sample of the knob at 0.5, 3.5, 8 and 30 ms. |
| 3 | `mix` | 0 … 1 | 0.4 | Dry/wet. A flanger needs the dry path (the comb *is* the sum), so a chorus box with no MIX is a vibrato box. **MIX 0 is bit-exact bypass** — measured 0.000e+00 peak error against the dry input. |
| 4 | `feedback` | −0.9 … +0.9 | 0 | **Bipolar, because the two signs are two different effects.** Measured at DELAY 2 ms, MIX 0.5, over 100 Hz … 12 kHz: FDBK 0 is a plain comb (peaks +1.2 dB, notches to −63 dB); +0.9 turns the peaks into resonances at +17.6 dB; −0.9 moves the whole pattern half a period and lands at +15.4 / −13.3 dB — the hollow one, which is the flanger everybody actually means. ±0.9 and not ±1.0: at 1.0 the comb is an oscillator and the knob's last detent would be a different instrument (DDR-044, as `arbutus/va`'s RES). |
| 5 | `spread` | 0 … 180 deg | 120 deg | The right channel's LFO phase. Measured L/R correlation on a mono input at DELAY 8 ms / DEPTH 0.6: 0 deg = **+1.000** (mono, deliberately — the box must be able to *not* widen), 60 deg = +0.004, 120 deg = +0.009, 180 deg = +0.004. |
| 6 | `voices` | 1 … 3 (integer) | 2 | One tap is a chorus, two is a stereo chorus, three is a section. It stops at three because a fourth adds RMS and no width, and because three taps a channel at 30 ms is already six interpolated reads a sample. Measured wet RMS at DEPTH 0.5: 0.1409 / 0.1405 / 0.1406 — **stacking voices does not change the level** (see below). |
| 7 | `tone` | 0 … 1 | 0.5 | A tilt on the **wet path only**, so it shapes the effect and never the dry signal. Below centre a one-pole lowpass closes from 16 kHz to 500 Hz; above centre a one-pole highpass opens from 20 Hz to 1.2 kHz. **0.5 bypasses both and is bit-exact.** Measured against the centre: TONE 0 takes the wet above 4 kHz down 23.2 dB and leaves below 200 Hz alone (−0.2 dB); TONE 1 takes below 200 Hz down 20.8 dB and leaves above 4 kHz alone (−0.8 dB). |

## How it is built

* **The voices are 0.37 of a cycle apart, not evenly spaced.** Evenly spaced
  taps are a set that SPREAD can rotate *onto itself*: two voices at 180 deg,
  or three at 120 deg, give the two channels the identical tap set, and the
  image collapses to mono at exactly the settings that should be widest. This
  was measured before it was fixed — SPREAD 180 with VOICES 2 read L/R
  correlation +1.000. 0.37 has no such period at one, two or three voices.
* **The wet sum is scaled by 1/√VOICES, not 1/VOICES.** The taps are
  decorrelated, so they add as powers; dividing by the count made three voices
  4.7 dB quieter than one, and a knob that changes the level while it changes
  the texture is two knobs.
* **The feedback tap is the first voice alone**, not the mean. Feeding the mean
  back would make VOICES change the resonance as well as the width.
* The read is linearly interpolated, like `arbutus/comb` and `arbutus/delay`.

## Tests

`ctest -R chorus` — golden, ragged blocks (512 / 128 / 97 / 2048 / 1 agree),
`--rt-guard`,
and `chorus_bypass` (MIX 0 is the input bit for bit with every other knob
at its far end, #630).

`tests/golden/chorus.npy` was **frozen from this box** — there is no Python
original. The freeze is a flanger-ish setting that runs every path at once
(three voices, a wide spread, negative feedback, the wet-only tilt). Before
freezing: peak 0.579, RMS 0.0327, DC +7.0e−04, all finite over 60000 frames
(`tests/tools/npystat.py`), plus the table above.

**Bench:** 27.54 µs/block, 0.26 % of a core at 512 frames / 48 kHz, with all
three voices running — six interpolated reads a sample. One voice is a third of
that.
