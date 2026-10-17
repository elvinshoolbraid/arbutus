# arbutus/fm

Two-operator FM on `PolyHost<Voice, 8>`. The DSP is
`emulator/engines/fm_synth.py` unchanged; **the descriptor is the deliverable.**
`caps: audio_out, events_in`. `honours_env: true`.

Elvin on the Python one:

> initial parameters are completely unlistenable, index and ratio ranges are so
> large with such small bliss points that finding them is difficult, and the
> knobs don't have the resolution to vary in the space that sounds okay

That is DDR-044's named failure case, so this box fixes it in the ranges.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `ratio` | ENUM `0.5 1 1.5 2 3 3.5 5 7` | `1` | Was continuous 0.5 … 8.0. Almost all of that is inharmonic mush; the sounds people want sit on a handful of simple ratios, and a continuous knob puts them between detents where they can be missed entirely. Eight entries, one per detent: 1 is the reed/EP family, 2 and 3 the brass and clav end, 1.5 and 3.5 the bell family (a non-integer ratio gives inharmonic partials, which is what a bell *is*), 7 the metallic top. |
| 1 | `index` | 0 … 6 | 2.2 | Was 0.0 … 10.0, **and that number was not the modulation index.** The Python adds `mod_index * modulator` to a phase measured in *cycles*, so the textbook index is β = 2π × that: its 10.0 is β = 62.8, and everything that sounds like an instrument lives below about 1.0 on that knob. This is β, in the units every FM reference uses, and the engine divides by 2π on the way into the untouched core. |
| 2 | `level` | 0 … 1 | 0.8 | Voice trim, 0.5 of headroom on top. |

### Before and after

| | Python | Here |
|---|---|---|
| ratio | continuous 0.5 … 8.0, default 2.0 | ENUM of 8, default 1 |
| index | continuous 0.0 … 10.0 (β 0 … 62.8), default 2.0 (β 12.6) | 0 … 6 **β**, default 2.2 |
| default sound | eight sidebands wide — a clangy metallic wash | a soft electric-piano/clav: the fundamental on top with two or three sidebands of reed under it. 94.6 % of its energy is below 1.5 kHz. |

RATIO and INDEX are read **live**, not latched at note-on, so turning either
while a note is held is audible. A knob that only takes effect on the next note
feels broken.

## Deferred

An **index envelope** (a falling β per note) is what turns this from a warm
sustained tone into a struck electric piano or a bell, and it is the obvious
next parameter. It is not here because the Python has none and adding one would
have put the conformance golden out of reach without a second hidden switch.
`[TBD, Elvin's call]`: worth a fourth slot?

## Tests

`ctest -R fm` — golden, ragged blocks, `--rt-guard`.

**Conformance:** `ctest -R voice_goldens` replays
`emulator/tests/golden/fm_voice.npy` through `src/fm_dsp.hpp` at the Python's
raw ratio 2.5 / index 3.0 — values this descriptor deliberately cannot reach —
at **−144.5 dBFS** peak error. Widening the shipped ranges so a golden could be
reproduced through the ABI would be letting the test dictate the instrument,
which is the failure DDR-044 names; driving the core directly is the answer.

**Bench:** 14.50 µs/block, **0.14 % of a core** at 512 frames / 48 kHz (three
voices) — the most expensive stock engine, two `sin()` per sample per voice.
