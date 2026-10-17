# arbutus/crusher

The two halves of a bad converter, and nothing else. A digital signal has
exactly two resolutions — how finely it is measured (BITS) and how often
(RATE) — so those are the two knobs, and they are deliberately separate:
12 bits at 48 kHz and 16 bits at 6 kHz are different sounds, and a single
"amount" knob would only ever walk one line through them.
`caps: audio_in, audio_out`. `honours_env: false`.

**Six parameters; slots 6 and 7 are empty on purpose.** A crusher with eight
knobs is six knobs and two apologies — the argument `arbutus/osc` already makes
for having two (§14, DDR-044).

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `bits` | 2 … 16 | 8 | **The bottom is 2, not 1.** A mid-tread quantiser with one level rounds everything under half scale to zero, so 1 bit is a mute for anything but a peak — the "bottom of the knob is silence" DDR-044 forbids (`arbutus/va`'s CUTOFF makes the same argument). Measured on a 1 kHz sine at −6 dBFS, worst non-harmonic bin below the fundamental: 2 bits −15.7 dB, 3 −19.7, 4 −22.7, 6 −37.7, 8 −44.7, 12 −76.7, 16 −99.7. 16 is the top because the library is 16-bit (`library/README.md`), so the top of the knob is "the file you loaded" and not a transparency claim. Continuous, not integer: 8.5 bits is a real grid half-way between two whole ones, and a macro sweeping the knob must not step. |
| 1 | `rate` | 200 Hz … 48 kHz, log | 8 kHz | The sample-and-hold clock. Measured on a 1 kHz sine, the strongest image and how far below the fundamental it sits: 48 kHz (off at this run's rate) −99.7 dB, 12 kHz → 11 kHz at −20.1 dB, 5.5 kHz → 4.5 kHz at −12.8 dB, 2 kHz → 3 kHz at −9.5 dB. The bottom is 200 Hz because below it the hold is longer than a note's attack and the box stops being a converter and becomes a stutter. The top is the run's own rate, clamped, so the last detent is genuinely off rather than "nearly off". |
| 2 | `drive` | 0 … 24 dB | 0 dB | **Not a level control.** The pre-gain is undone exactly on the way out, so the knob moves the signal against the quantiser's grid and against the clipper and does nothing else. Measured at BITS 6 on the same sine: DRIVE 0 → fundamental 0.5006, noise −37.7 dB; DRIVE 6 → 0.5000 (**+0.00 dB**), noise −42.2 dB (the signal now uses more of the grid); DRIVE 12 → 0.3058 (−4.3 dB) and DRIVE 24 → 0.0802 (−15.9 dB), noise −13.0 and −9.6 dB — past 6 dB the clipper is taking the level, and what you lose is exactly what it took. 24 dB is the top because at 24 dB a −24 dBFS signal is already at full scale, and there is nothing quieter worth rescuing. |
| 3 | `mix` | 0 … 1 | 1.0 | Dry/wet. Default 1.0: this is a destructive effect and a half-crushed signal is mostly the dry one. **MIX 0 is bit-exact bypass** — measured 0.000e+00 peak error against the dry input. Note that BITS 16 is *not* a bypass; it is a 16-bit quantiser. MIX is the bypass. |
| 4 | `tone` | 500 Hz … 20 kHz, log; the top reads `OFF` | `OFF` | A one-pole lowpass after the crush — the one place to buy back some of the alias hash. The top detent is a real bypass and `format_param` prints `OFF`, because "20000 Hz" would not say so (`arbutus/lfo`'s `STOP` is the same idea). Measured at BITS 5 / RATE 6 kHz, worst component above 6 kHz relative to the fundamental: OFF −16.6 dB, 8 kHz −18.7, 2 kHz −26.6, 500 Hz −32.3. |
| 5 | `jitter` | 0 … 1 | 0 | Lengthens and shortens the hold **period** rather than dropping samples, so the pitch of the alias hash wanders instead of the signal gaining clicks: ±60 % at the top, an octave of wobble and still a recognisable rate. Measured at RATE 5.5 kHz on a 1 kHz sine: the tallest single image falls from 0.1074 (JITTER 0) to 0.0141 (0.25), 0.0079 (0.6) and 0.0089 (1.0) while the *total* energy in 3–5 kHz barely moves (0.133 → 0.127) — the line becomes a band. Default 0, because a clean crush is the thing most people came for. |

## How it is built

* **One hold clock for both channels.** A converter samples them together; two
  clocks would turn a stereo signal into two different signals.
* **The jitter is deterministic.** An xorshift32 seeded from `arb_host.rng_seed`
  advances **once per hold**, never once per sample, so the sound does not
  depend on the block size — that is what `ctest -R ragged_crusher` checks, and
  what lets the golden run with JITTER on. Measured: the same `--seed` twice is
  bit-identical (0.000e+00), a different seed is a different render (0.656).
* **Everything here aliases**, and that is what it is for: the hold's images
  fold back into the band and *are* the sound of a cheap sampler. Oversampling
  a bit-crusher would be removing the effect.

## Tests

`ctest -R crusher` — golden, ragged blocks (512 / 128 / 97 / 2048 / 1 agree),
`--rt-guard`,
and `crusher_bypass` (MIX 0 is the input bit for bit with every other knob
at its far end, #630).

`tests/golden/crusher.npy` was **frozen from this box** — there is no Python
original. The freeze runs with JITTER on, so it also pins the seeding and the
once-per-hold advance. Before freezing: peak 0.402, RMS 0.0455, DC +5.9e−04,
all finite over 48000 frames (`tests/tools/npystat.py`), plus the table above.

**Bench:** 2.20 µs/block, 0.02 % of a core at 512 frames / 48 kHz — the
cheapest effect in the set, which is what a box with no delay line costs.
