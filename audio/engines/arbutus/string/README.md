# arbutus/string

A string, by waveguide. Two delay lines a few cents apart per voice, each with
a damping filter in its loop, plucked, struck or bowed, through a fixed body
resonance. Eight voices on `PolyHost<Voice, 8>` (§4).
`caps: audio_out, events_in`. `honours_env: true`.

Karplus-Strong is the one physical model small enough to run eight voices of on
a CM4 and still sound like a string rather than like a filtered saw: the sound
is the excitation going round the loop and losing its top end, which is why
DECAY and DAMP are two knobs and not one.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `exc` | ENUM `PLUCK` `BOW` `STRIKE` | `PLUCK` | Three excitations, not three engines. `PLUCK` fills one period with filtered noise, `STRIKE` an eighth of one (harder, brighter, and scaled by `1/√duty` so it is not merely quieter), `BOW` feeds the loop every sample while the key is held — which is the only one of the three that sustains. |
| 1 | `decay` | 0.05 … 8 s, log | 1.6 s | **Seconds to −60 dB of the fundamental**, worked out per note from that note's own period and DAMP's loss at that note, so a low string and a high one ring for the time on the screen. Measured (#647): DECAY 2 s rings 2.00 s from C4 to C7 at DAMP 0, and to C5 at DAMP 0.35. **Where it stops holding:** the loop gain may not pass 0.9999 (it is also the loop's gain at DC, so above that a stray offset would grow), so where DAMP takes more off a note's fundamental than that can make up, the note rings shorter than the knob: at DECAY 2 s, DAMP 0.35 gives 1.15 s at C6 and 0.16 s at C7, DAMP 0.75 1.16 s at C5. That is DAMP doing its job on the top of the keyboard, not DECAY drifting. 0.05 s is a damped mute (still a sound, not silence); 8 s is a piano with the pedal down. Log, because the interesting half of that span is under a second. |
| 2 | `damp` | 0 … 1 | 0.35 | The one-pole in the loop: it makes the top end die before the fundamental, which is the whole difference between a string and a resonator. 0 is a bright wire (the third harmonic is louder than the first), 1 is nylon. Mapped to a coefficient of 1 … 0.15 — never 0, because a fully closed loop filter is a delay with no sound in it. Its phase delay **at the note's own frequency** is subtracted from the line, so **DAMP does not detune the note** (measured, #647: within 0.2 ct from C4 to C7 at DAMP 0 and 0.5, C7 −1.9 ct at 0.75; the DC figure used before put C7 +15 ct sharp at 0.75). At DAMP 0.9 and 1 the top two octaves are a click gone in milliseconds and read up to 27 ct flat; nothing there rings long enough to have a pitch. |
| 3 | `pos` | 2 … 50 % | 22 % | Pluck position: the excitation minus a copy of itself that fraction of a period later, notching the harmonics with a node there. 2 % is at the bridge (nasal, all harmonics), 50 % is the midpoint (hollow, no even harmonics). Past 50 % is a mirror image of under it, so the knob stops there rather than repeating itself. |
| 4 | `tone` | 0 … 1 | 0.6 | The one-pole over the excitation noise before it enters the loop: how hard the pick is. 0 is felt, 1 is raw noise. It shapes what the string is given, where DAMP shapes what it keeps. |
| 5 | `force` | 0 … 1 | 0.5 | Excitation amplitude — pick strength, bow pressure — over `0.25 … 1.0` of the nominal, so the bottom of the knob is quiet and not silent. |
| 6 | `body` | 0 … 1 | 0.3 | Two fixed resonators across the output (180 Hz Q 4 and 1.05 kHz Q 6, a smallish wooden box), crossfaded in. Fixed on purpose: a body that moves with a knob is a filter, and `arbutus/va` already has one. |
| 7 | `spread` | 0 … 20 ct | 6 ct | The second string. A piano's unison pair and a twelve-string are the same trick — two lines a few cents apart, each with its own excitation, beating against each other. 20 ct is where the pair stops being one string; 0 still runs both, so the knob is a beat rate and not a voice count. |

Voice trim is **0.7**: a plucked string peaks well below a saw, and this puts
the default near 0.33 at full velocity, where `arbutus/osc` sits.

## How it is built

* **The excitation goes behind the write pointer**, over the period the loop is
  about to read. Written ahead of it, the loop overwrites it before it is ever
  read — which is exactly the silent first version of this box.
* **The loop is an integer delay, a first-order allpass for the fraction of a
  sample, the DAMP one-pole and the gain** (#647). The allpass loses nothing at
  any frequency; the linear read it replaced was a lowpass whose loss depended
  on the fraction, so the top two octaves rang 0.4–1.2 s for DECAY 2 s, unevenly
  from key to key. The line's length and the allpass are solved per block for
  the note's frequency, the one-pole's delay there included.
* **DC is taken out of the excitation** after the position comb, over exactly
  the samples the loop holds. The loop is a lowpass with gain, so it barely
  damps DC: whatever mean the noise burst has sits under the note for its whole
  life (filled one period long while the line was shorter than that, a high
  note at DAMP 0.75 carried an offset of 0.01 under a 0.05 peak). The bow's
  noise is a first difference **per string** for the same reason (#647: one
  shared state made it white noise, −38.5 dB under 40 Hz on a bowed C3; now
  −49.6).
* **`BOW` saturates the loop** (`tanh`) because it injects energy every sample
  and would otherwise run away; stick-slip friction has a limit anyway.
  `PLUCK` and `STRIKE` stay linear, so their decay is exactly the T60 asked for.
* **The loop gain is computed once per block**, not per sample: it is a `pow()`
  of the delay and DECAY and neither moves inside a block.
* **The delay memory is one allocation at `create()`** — 154 kB for eight
  voices of two strings that can each reach 20 Hz. §3.7.1 forbids allocating in
  `process()`, not owning memory.

## Tests

`ctest -R string` — golden, ragged blocks, `--rt-guard` for `PLUCK` and again
for `BOW` (the mode that touches the loop every sample), and `string_tuning`
(#647: DECAY's ring time and the tuning from C4 to C7 across DAMP, and the
bow's low end; red in 11 rows on the engine before it).

The tests run with `--env 0.002 0.05 1.0 0.6`: a string's amp envelope has to
hold at sustain or PolyHost's release is what you hear decaying instead of the
string. `tests/golden/string.npy` was **frozen from this box** at its defaults
(there is no Python original; the emulator had no physical model) and
**refrozen by #647**, a sound change on purpose: the notes ring for DECAY now
that DAMP's loss at the fundamental is counted, so peak 0.332 → 0.344, RMS
0.033 → 0.036, DC +4.3e−05 → −6.9e−06, all finite over 96000 frames
(`tests/tools/npystat.py`).

**Bench:** 24.8 µs/block, 0.23 % of a core at 512 frames / 48 kHz (three voices
sounding, two delay lines each). #647's allpass and per-block solve cost
nothing measurable: 27.0 µs against the old engine's 32.0 on the same x86
container, best of 5 each.
