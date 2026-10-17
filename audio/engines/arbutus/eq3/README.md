# arbutus/eq3

Three bands **you can move**, on a rack tab. `caps: audio_in, audio_out`.
`honours_env: false`.

This is not the per-track EQ (DDR-062, `host/mixer.hpp`). That one is host DSP
on the mixer's eight strips — fixed shelves at 200 Hz and 3 kHz, a sweepable
mid, plus HP and LP, edited in real dB and Hz from MIXER › EQ. It is the desk.
This is the pedal: it goes on any FX tab, on an instrument's rack or on the
master's, every parameter is macro-bindable and modulatable like any other
engine parameter, and **both shelves move as well as the mid** — which the
desk's do not, because a desk's shelves are the same on every channel by design
and these are per sound.

There is **no HP or LP here on purpose.** The track EQ has them, `arbutus/biquad`
is one, and eight slots spent on five filters would leave nothing to move them
with.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `low` | −18 … +18 dB | 0 | An RBJ low shelf at LOW-F. ±18 and not the desk's ±12: this one is a sound-design filter on one sound, not a correction across a mix, and 18 dB is where a shelf stops being tone and starts being a filter — past it you want `arbutus/biquad`. Measured at LOW-F 180 Hz: +6 dB gives +6.00 at 20 Hz, +3.04 at the corner and **+0.01 at 1 kHz**; +18 gives +17.99 / +9.10 / +0.04. |
| 1 | `mid` | −18 … +18 dB | 0 | An RBJ peak at MID-F with MID-Q. Measured exact to 0.00 dB at −18, −9, +9 and +18. |
| 2 | `high` | −18 … +18 dB | 0 | An RBJ high shelf at HIGH-F. Measured at 3.2 kHz: +6 dB gives +0.00 at 100 Hz, +3.00 at the corner, +6.00 at 16 kHz. |
| 3 | `mid_f` | 100 Hz … 8 kHz, log | 900 Hz | The mid is the band you sweep, so it gets the top-row slot. 100 Hz at the bottom because below it a peak and the low shelf are the same control; 8 kHz at the top because above it the high shelf is the better tool and a narrow peak up there is a whistle. Measured: the gain lands at +12.00 dB at MID-F 100, 900 and 8000. |
| 4 | `low_f` | 40 … 600 Hz, log | 180 Hz | Where the low shelf turns. 40 Hz is a sub shelf that leaves the bass note alone; 600 Hz is "everything below the voice". Measured at +12 dB: LOW-F 40 reaches +11.02 at 20 Hz (the shelf has not finished plateauing inside the audio band, which is the honest bottom of the range), 180 and 600 both reach the full +12.00, and LOW-F 600 still only puts +0.13 dB at 2 kHz. |
| 5 | `high_f` | 1.2 … 12 kHz, log | 3.2 kHz | Where the high shelf turns. 1.2 kHz is presence, 12 kHz is air. Measured at +12 dB: the corner reads +6.01 / +6.00 / +6.00 at 1.2 k, 3.2 k and 12 k, and 200 Hz stays inside ±0.01 dB at all three. |
| 6 | `mid_q` | 0.3 … 6, log | 0.9 | Measured bandwidth at +12 dB, between the half-boost points: Q 0.3 → 252 … 3205 Hz (**3.67 octaves**, a broad tilt), 0.9 → 530 … 1523 Hz (1.52 oct, the default and a musical "scoop"), 2.5 → 738 … 1096 Hz (0.57 oct), 6.0 → 829 … 976 Hz (**0.23 oct**, narrow enough to find one resonance and not so narrow it rings). |
| 7 | `out` | −12 … +12 dB | 0 | Makeup. Three bands at +18 is +54 dB of headroom gone, so a trim is not optional; ±12 is enough to give any single band back. Measured exact to 0.00 dB at −12, −6 and +12. |

## How it is built

* **A band at exactly 0 dB is skipped, not run as a unity biquad.** An identity
  biquad is not bit-exact in float, and "flat is bypass" is the rule the
  mixer's EQ already keeps (`audio/CLAUDE.md`, "EQ off"). Measured: all three
  bands at 0 dB with OUT 0 gives **0.000e+00** peak error against the input.
  A skipped band's state is cleared, so re-opening it starts from silence
  rather than from a stale tail.
* **Both shelves are RBJ at S = 1** — the steepest shelf that does not
  overshoot, which is the only shelf shape worth putting on a tone control.
* **Coefficients are recomputed once per block**, not once per change: a macro
  may drive any of these from the audio thread every block (§6.2), and the
  block top is where the trig is safe. No allocation, no locks. Corner
  frequencies are clamped to 0.49 × the sample rate so a high shelf stays a
  filter at any run rate.

## Tests

`ctest -R eq3` — golden, ragged blocks (512 / 128 / 97 / 2048 / 1 agree),
`--rt-guard`,
and `eq3_bypass` (all bands at 0 dB is the input bit for bit, corners and Q
anywhere, #630).

The recursion is written as explicit `std::fma`, not as one arithmetic
expression, and must stay that way: the golden was frozen on arm64, where the
compiler contracts into fma chains by default, and the x86-64 SSE2 baseline has
no fma instruction to contract with. At a 120 Hz low shelf the two roundings
diverge by −92 dBFS, past the −100 dB tolerance. `std::fma` is exact by IEEE-754,
so it gives the same bits everywhere; the order of the terms is part of it (#55).

`tests/golden/eq3.npy` was **frozen from this box** — the emulator's only EQ
was the one-pole in `arbutus/biquad`. The freeze has all three bands off zero
with both shelves moved and a narrow mid, so every section is in the render;
its response measures +4.47 dB at 30 Hz, −2.11 at 400 Hz, **−10.98 at 1.4 kHz**
and +1.98 at 15 kHz. Before freezing: peak 0.989, RMS 0.0753, DC +1.4e−03, all
finite over 48000 frames (`tests/tools/npystat.py`).

**Bench:** 6.85 µs/block, 0.06 % of a core at 512 frames / 48 kHz, with all
three sections running. Flat it is a copy.
