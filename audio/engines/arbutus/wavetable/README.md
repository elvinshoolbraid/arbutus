# arbutus/wavetable

A table scanned, phase-warped and folded. Two detuned oscillators plus a sine
sub, eight voices on `PolyHost<Voice, 8>` (§4).
`caps: audio_out, events_in`. `assets: wavetable`. `honours_env: true`.

The table is **content, not a parameter** (DDR-037): `load_asset(h,
"wavetable", path)` takes a 16-bit mono WAV of `frames × 2048` samples, one
cycle per frame. Which table you are on is a patch, not a knob — the same way
`arbutus/sampler` takes its WAV.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `pos` | 0 … 100 % | 0 | Where in the table you are, interpolated between adjacent frames. `format_param` prints the frame it lands on (`6.7/15`), because a percentage says nothing about a table with sixteen cycles in it. |
| 1 | `sweep` | −100 … +100 % | 0 | The **ENV page's own ADSR** moving POS, over the whole table at ±100 %. Bipolar, so a note can arrive bright and settle, or the other way round. A second envelope would cost four knobs for a shape the player already set (as on `arbutus/va`'s ENV-F). |
| 2 | `warp` | −100 … +100 % | 0 | Casio-style phase distortion: the cycle's first half plays faster and the second takes up the slack, so the *period* is untouched and only the shape moves — a formant shift, not a pitch shift. ±100 % puts the pivot at 5 %/95 % of the cycle, which is as far as it goes before the wave is a click; 0 is the table exactly. |
| 3 | `fold` | 0 … 100 % | 0 | A sine folder, crossfaded by the knob so **0 is bit-exact bypass** and the top is a fold rather than a clip. Gain into the folder runs to 6×, which is two reflections on a full-scale cycle — past that it is noise, not a timbre. |
| 4 | `spread` | 0 … 30 ct | 10 ct | The detune between the two oscillators, total. They start half a cycle apart, so **SPREAD 0 is two oscillators beating at zero and not one at twice the level** — the knob is a beat rate, and the patch's level does not jump when it leaves 0. |
| 5 | `sub` | 0 … 1 | 0.2 | A sine an octave down, for weight the table has not got. A **balance**, not a gain: the mix is divided by `1 + 0.7·SUB`, so the knob changes the timbre and not the level. |
| 6 | `drift` | 0 … 100 % | 15 % | A slow wander of POS, **per voice** — each note at its own rate (0.06 … 0.17 Hz, derived from the note number, so it is deterministic and a golden repeats). A chord then breathes instead of pulsing as one, which no MOD route can do: the MOD rack is global except for `keys` (modulation-spec §3.2). ±25 % of the table at the top of the knob. |

Slot 7 is **deliberately empty** and becomes fine-tune of slot 6
(`parameter-framework.md`), like `arbutus/osc`'s four. The filter that a
wavetable synth usually carries is `arbutus/va`'s; putting a second one here
would be two boxes in one.

Voice trim is **0.45**, the headroom the other synth boxes take.

## Aliasing, which is the whole engineering problem

A 2048-point cycle can hold 1024 harmonics. Played at 400 Hz, harmonic 60 is
already at Nyquist and everything above it folds back as an inharmonic whistle
that follows the note in the wrong direction.

So `load_asset` builds a **mip chain** per frame: level *k* is the frame at
`2048 >> k` samples, band-limited to its own Nyquist by a 15-tap half-band
windowed sinc (applied circularly — a cycle has no edges) before each
decimation, down to 4 samples. The oscillator then picks the longest level
whose top harmonic still fits under the sample rate at the note being played.
It costs about 2× the table's memory and **nothing per sample**, which is the
trade a CM4 wants.

WARP and FOLD both make harmonics the table did not have, so the level choice
pretends the note is `1 + 2·|WARP| + 2·FOLD` times higher. A folded wavetable
that is a little duller is a sound; one ringing at 11 kHz in a different key is
not.

Measured at C7 (2093 Hz) on `harmonics.wav` at POS 100 — the worst case, a
64-harmonic saw at the top of the keyboard — energy **below the fundamental is
−52.6 dB** of the total. That is where alias products land, and it is where you
would hear them.

**WARP is not mean-preserving**, so the oscillator sum runs through a one-pole
DC blocker at ~15 Hz (−3 dB at 15.3 Hz; "~32 Hz" until #647): without it, ±100 % parks 0.23 of DC under every note,
which a resonant filter or a reverb downstream turns into a thump. It is always
on, so the knob has no seam at zero (measured DC at ±100 %: 4.7e−03).

## The tables

`audio/library/synth/wavetable/` holds two, written by
**`audio/tools/mkwavetable.py`** — the script is the source and the WAV is its
build (repo `CLAUDE.md` § Library, 16-bit only). Every frame is built from a
harmonic amplitude list, never drawn, so the file is band-limited by
construction and byte-for-byte reproducible; `ctest -R wavetable_assets`
regenerates them and compares.

| File | What the scan is |
|---|---|
| `harmonics.wav` | 16 frames, sine → 64-harmonic saw, an octave of harmonics arriving at a time. The wavetable version of opening a filter, and unlike a filter it costs nothing per sample. |
| `vowel.wav` | 16 frames through A E I O U — a 130 Hz buzz shaped by three formant resonances. The scan is the mouth moving. |

A table with more than 64 frames is truncated to 64 (1 MB of mip chain); a file
whose length is not a whole number of 2048-sample frames is refused, which the
host turns into `ENGINE BROKEN` before anything is in the graph.

## Tests

`ctest -R wavetable` — golden, ragged blocks, `--rt-guard`, and the asset
reproducibility check. Every one loads a table: without one the box is silent
by design, because there is nothing to scan.

**POS is resolved on a 32-frame grid, not once per block** (#56). SWEEP's
envelope and DRIFT used to be read once per `process()` call, and the call's
length is the host's to choose — 128 on the device, anything a bake or a bench
asks for elsewhere — so the box sounded different at every block size, by
**60 dBFS** between 512 frames and 1. The grid is `arb::kCtrlStep` in
`sdk/poly.hpp`, counted from the voice's own note-on; DRIFT is computed from
that absolute frame rather than accumulated, so cutting a block into cells
cannot move even its last bit. 0.67 ms at 48 kHz, finer than the device's
block, so the scan is no coarser than it was. `ctest -R ragged_wavetable` is
what checks it — and could not, until #56 fixed the harness itself. Its runs
leave SWEEP at 0, where the envelope term multiplies by zero, so
`ragged_wavetable_sweep` runs the same with SWEEP 50 (#647): that is the run
that goes red if the envelope is read once per call again.

`tests/golden/wavetable.npy` was **frozen from this box** at POS 55 / WARP 30 /
FOLD 25 (defaults leave three of the seven at zero, and a golden should touch
them): peak 0.551, RMS 0.147, DC −6.0e−03, all finite over 96000 frames
(`tests/tools/npystat.py`). **Refrozen 2026-09-23** for the grid above — the
numbers are unchanged to the digits printed here, which is what says the fix
moved POS's timing by ≤ 0.67 ms and not the sound.

**Bench:** 13.6 µs/block, 0.13 % of a core at 512 frames / 48 kHz (three voices
sounding, two oscillators and a sub each).
