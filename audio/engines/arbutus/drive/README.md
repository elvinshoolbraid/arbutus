# arbutus/drive

The rack's distortion, and **the only box in the set that oversamples**.
`caps: audio_in, audio_out`. `honours_env: false`. `latency_frames: 15`.

The MASTER strip already has a DRIVE (`host/mixer.hpp` `DriveStage`): one
`tanh`, at the run's own rate, on the mix. That is right *there* — it is a
finishing knob and it must cost nothing. This is the other one: the distortion
you put on a sound, where running out of headroom is the whole point, and where
the difference between a good one and a bad one is almost entirely aliasing.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `mode` | ENUM `SOFT` `HARD` `FOLD` `FUZZ` | `SOFT` | The *curve* is the one thing about a distortion a knob cannot interpolate, so it is four detents and not a morph. SOFT and HARD are the two ends of clipping; FOLD turns round at the bound instead of flattening, so more level is a different timbre rather than a louder one; FUZZ is asymmetric (slope 1 up, 0.33 down) and is the only mode with even harmonics of its own. Measured on a −6 dBFS 1 kHz sine at DRIVE 0.15 / 0.35 / 0.5, harmonics below the fundamental: SOFT H3 −22.8 / −14.5 / −12.2 dB; HARD H3 −20.8 / −14.0 / −12.0 dB (the same order, a harder knee); FOLD H3 −8.3 / −14.8 / −22.2 and H5 −16.2 / −14.6 / **−2.7** dB — the spectrum moves rather than grows; FUZZ H2 −38.6 / −44.7 / −46.4 dB where the other three are below −130. |
| 1 | `drive` | 0 … 1 | 0.3 | Gain into the shaper, 1 … 32 (**+30 dB**), then crossfaded dry→wet by the same knob so **DRIVE 0 is bit-exact dry** (measured 5.96e−08, one float ulp) — the `DriveStage` idiom, so the instrument's two DRIVE knobs behave the same way. +30 dB at the top because that puts a −30 dBFS tail at full scale, which is as much as anything on this instrument needs rescuing by. |
| 2 | `tone` | 0 … 1 | 0.5 | Tilts **what reaches the shaper** around 700 Hz — the same frequency and the same pair of gains as MASTER's DRIVE TONE, so the two mean the same thing. 0 = lows only into the shaper, 1 = highs only, **0.5 = flat and exactly reconstructing**. Measured: at DRIVE 0 the output is the delayed dry to one ulp at TONE 0.0, 0.5 *and* 1.0 — the tilt cannot colour a signal the shaper is not distorting. |
| 3 | `mix` | 0 … 1 | 1.0 | Parallel blend against the dry — the one bypass. **MIX 0 is bit-exact** with every other knob at its extreme (measured 0.000e+00 against the dry delayed by the box's 15 frames). Default 1.0: a distortion you have to turn up twice is a distortion you will think is broken. |
| 4 | `bias` | 0 … 1 | 0 | An offset **in the shaper's units** (0 … 0.8), not in the signal's. An input-side offset would be multiplied by DRIVE, and at DRIVE 1 an offset of 0.7 is 22 into a `tanh` — the shaper pins and the top of the knob becomes a mute. That was measured before it was fixed (H2 and H3 both fell to −60 dB at BIAS 1). As built, on SOFT at DRIVE 0.5: BIAS 0 → H2 below −168 dB, 0.3 → −33.7, 0.6 → −27.7, 1.0 → **−23.3 dB**, with H3 unmoved at −12.2 … −12.6. This is where even harmonics come from. |
| 5 | `hp` | 20 … 400 Hz, log; 20 = off | 20 Hz (off) | A one-pole highpass **before** the shaper, so the bass stops eating all the distortion and the mids get some. The bottom is a real bypass, as the mixer's EQ does it (`audio/CLAUDE.md`, "EQ off"). Measured on 50 Hz + 1.5 kHz into HARD at DRIVE 0.8: HP off → 50 Hz at 1.004 and 1.5 kHz at 0.149; HP 400 → 50 Hz at 0.266 and 1.5 kHz at **0.928**. The top is 400 Hz because past it a guitar's body is gone as well as its rumble. |
| 6 | `sag` | 0 … 1 | 0 | The shaped level dips while the shaper is being driven and takes 150 ms to come back, the way an under-specified supply does. Measured on a 1 s tone burst, SOFT at DRIVE 0.6: settled level −3.4 dB at SAG 0.25, −5.2 at 0.5, **−7.2 dB at 1.0**, each one 2 … 4.5 dB below its own first 2 ms — so the attack still gets through. It is an *output* dip and not a reduction of DRIVE: pulling the drive down changes nothing audible once the shaper is saturated (measured — a square wave stays a square wave). |
| 7 | `out` | −24 … +12 dB | 0 dB | Trim, on the processed path only, so MIX 0 stays bit-exact whatever OUT says. Measured exact to 0.01 dB at −24, −6, 0 and +12. −24 at the bottom because a fully driven FOLD is 12 dB hotter than what went in and a rack tab has to be able to give that back; +12 at the top for the opposite case. |

## How it is built

* **The shaper runs at 2×, behind a pair of 31-tap windowed-sinc half-band
  filters.** A waveshaper multiplies bandwidth: a hard clipper fed 7 kHz makes
  harmonics at 21, 35 and 49 kHz, and at 48 kHz those fold back into the band
  as inharmonic whistles that move the *wrong way* when you play a scale.
  Oversampling does not remove them — nothing does — it moves the first
  in-band fold from the 5th harmonic to the 13th. **Measured** on a 7 kHz sine
  at DRIVE 1 in HARD (the worst case the box has): worst inharmonic component
  **−21.3 dB** below the fundamental, against **−14.3 dB** for the same clipper
  with no oversampling.
* **31 taps, not 15.** 15 was tried first and measured: it dropped 20 kHz by
  3.8 dB and bought only 8.5 dB on the fold-back. At 31 the linear path reads
  1.0006 at 1 kHz, 1.0002 at 5 kHz, 1.0007 at 10 kHz, 0.9971 at 15 kHz and
  0.9203 at 20 kHz. Half-band means every even offset from the centre is
  exactly zero, so 31 taps cost **16** multiplies a sample each way and one of
  the two polyphase branches is a pure delay.
* **The dry is delayed by the same 15 frames** before the crossfade and before
  MIX. A box that mixes a delayed wet with an undelayed dry comb-filters
  itself; this one does not, and it declares the latency instead of hiding it
  (§3.2 — reported, not compensated).
* **A 12 Hz DC blocker sits on the shaped path only.** FUZZ and BIAS are both
  asymmetric, so they carry DC on signal as well as at rest; keeping the
  blocker off the dry path is what leaves MIX 0 and DRIVE 0 bit-exact.

## Tests

`ctest -R drive` — golden, ragged blocks (512 / 128 / 97 / 2048 / 1 agree),
`--rt-guard`.
MIX 0 against the input 15 frames late, every other knob at its far end, is
`ctest -R fx_moves` (#630).

`tests/golden/drive.npy` was **frozen from this box** — the emulator had no
distortion but the MASTER strip's, which is a different circuit. The freeze is
FUZZ with BIAS and SAG up and both filters off centre, so the asymmetric path,
the DC blocker and the moving gain are all in the render. Before freezing:
peak 0.664, RMS 0.0611, DC +2.8e−04, all finite over 48000 frames
(`tests/tools/npystat.py`), plus the table above.

**Bench:** 30.08 µs/block, 0.28 % of a core at 512 frames / 48 kHz — the most
expensive effect in the set, and all of it is the oversampler: two 16-tap
filters and two shaper evaluations per sample per channel.
