# arbutus/plate

A big, clean plate reverb (#930; Elvin's big idea of 2026-10-12, "like a plate
reverb instead, longer max tail"). Jon Dattorro's figure-eight tank ("Effect
Design, Part 1", JAES 1997): the input is band-limited, pre-delayed and smeared
by four allpasses, then circulates through two cross-fed halves (a modulated
allpass, a delay, a damping lowpass, an allpass, a delay); each output is seven
taps summed from all over the tank. That is what makes a plate a dense, even
wash where `arbutus/reverb` (Schroeder's room, the emulator's) is a short room
of discrete combs. The room stays as it is; whether the plate *replaces* it as
the one stock reverb is Elvin's call by ear. `caps: audio_in, audio_out`.
`honours_env: false`.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `decay` | 0.4 … 30 s, log | 3.5 s | **A time, not a feedback gain.** The loop gain is worked out from the tank's length so the knob reads in seconds at every SIZE. 0.4 s is the shortest a plate still sounds like a plate; 30 s is the "longer max tail": measured 26.7 s at DAMP 0, ten times the room's longest (1.9 s). It never reaches a gain of 1, so the top is a very long tail, not a hold. |
| 1 | `mix` | 0 … 1 | 0.3 | Dry/wet, linear like the room's. **MIX 0 is the input bit for bit.** |
| 2 | `size` | 0 … 1 | 0.7 | Scales every delay and tap ×0.4 … ×1.6 of the paper's lengths (0.5 is the paper's plate). Small is a tight, bright spring-like plate, large a hall-sized sheet. Does not change the tail's length (measured: DECAY 5 gives 4.5 s at SIZE 0.1 and 4.7 s at SIZE 1). A move glides (50 ms) through interpolated reads: a tape bend, not a click. |
| 3 | `damp` | 0 … 1 | 0.35 | The tank's lowpass, 18 kHz at 0 to 1 kHz at 1, log: the highs die before the lows, the way a steel plate does. It also shortens the audible tail a little at long DECAY (DECAY 10 measures 8.7 s at DAMP 0.35). |
| 4 | `predelay` | 0 … 250 ms | 20 ms | Keeps the attack out of the wash. Last, because it is set once, not played. Glides like SIZE. |

[TBD, Elvin's call] Every default above is a by-ear guess (a classic 3.5 s vocal
plate); the comment at `make_desc()` says the same.

## How it is built, and where it leaves the paper

* **Clean.** No saturation anywhere. The modulation is half the paper's
  excursion (8 frames at 29.761 kHz) at 0.5 Hz, in quadrature between the
  halves: enough to stop a long tail ringing metallic, not enough to hear as
  chorus.
* **A fixed 40 Hz highpass and 13 kHz lowpass on the input.** With a 30 s
  tail, rumble and DC pile up into mud; the lowpass is the paper's bandwidth
  filter.
* **No denormals.** Every recursive state carries a −360 dB DC offset, so a
  tail fading to silence never drops into the denormal range (the Pi's audio
  thread does not set flush-to-zero). It never reaches the output when MIX is 0.
* **Allocates once, at `create()`,** for SIZE 1 and the modulation; every read
  is clamped to its ring, so a sample-rate change above the create rate makes
  the plate smaller rather than allocating in `process()` (§3.7.1). A failed
  allocation there is NULL from `create()`, the host's ENGINE BROKEN refusal,
  never an exception into the host (§14, #1215).
* **Every fixed read's distance is split into whole frames and a fraction
  once**, and again only while SIZE or PREDELAY glides; that took the box
  from 98 to 34 µs a block.

## Tests

`ctest -R plate` — `plate_golden`, `ragged_plate` (512 / 128 / 64 / 97 / 2048 / 1
agree), `plate_bypass` (MIX 0 is the input bit for bit at DECAY 30, SIZE 1,
DAMP 0), `rt_guard_plate`, and `plate_tail` (`tests/plate_tail.py`): DECAY 1,
3.5 and 10 s measure 1.03, 3.15 and 8.65 s (within 25 %); SIZE does not move
the tail; the longest tail is ≥ 20 s, ≥ 10× the room's, and still falling
(the last second of 16 s is 32 dB under the second one). Seen red: halving the
loop-gain exponent's 4 failed five checks; a loop gain of 1.005 per multiply
(a slow runaway) failed "still falling".
`plate_alloc` (`tests/test_plate_alloc.cpp`, #1215) loads the box through the
host's `make_instance` with an `operator new` that refuses the rings: the
refusal, not an abort (red without the catch: the test aborts). `plate_moves`
(`tests/plate_moves.py`): SIZE 0.9 → 0.3 and PREDELAY 20 → 200 ms moved
mid-tail land on the new setting's plate (the next burst ~100 dB from it; red
without the glide's `lengths();`: ~3 dB off), and SIZE 0.7 → 0.6, PREDELAY
20 → 60 ms on a held sine do not click (the second difference ≤ 8× held
still, ~4× measured; a jump instead of the glide is ≥ 25×).

`tests/golden/plate.npy` was **frozen from this box** (no Python original):
DECAY 8, MIX 0.5, SIZE 0.8, DAMP 0.4, PREDELAY 30 ms on the FX input, 1 s.
Peak 0.4508, RMS 0.0283, DC +2.0e−04, all finite (`tests/tools/npystat.py`);
the L−R peak is 0.147, so the golden pins the stereo taps too. Frozen on
x86-64 without FMA; not yet checked against an arm64 build (#930).

**Bench:** 34.2 µs/block, 0.32 % of a core at 512 frames / 48 kHz on the
bench host (x86-64); the room is 4 µs. Not yet measured on the Pi or against
`arbutus_os/load-budget.json`.

