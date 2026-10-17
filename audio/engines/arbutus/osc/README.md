# arbutus/osc

Four waveforms, eight voices, and nothing else. A rewrite of
`emulator/engines/basic_oscillator.py` on `PolyHost<Voice, 8>`, so note
tracking, voice stealing, ENV MODE and the per-voice amp envelope all come from
the SDK (§4). `caps: audio_out, events_in`. `honours_env: true`.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `wave` | ENUM `SINE` `SQR` `SAW` `TRI` | `SINE` | The emulator's `Waveform` declaration order, so a patch written against it picks the same shape. One shape per detent — an ENUM is how an engine gets a selector on an encoder without a new input mechanism (§3.5). |
| 1 | `level` | 0 … 1 | 0.7 | Voice trim. 0.5 of headroom is applied on top, so one note at the default peaks at 0.35 and a handful still has somewhere to go. |

Slots 2–7 are **deliberately empty** and become fine-tune of slots 0–1
(`parameter-framework.md`, `control-inventory.md:42`). The Python exposes
exactly one timbre parameter, because the amp shape belongs to the ENV page and
putting A/S/R on the engine as well meant two controls writing one envelope
(`emulator/notes/as-built.md` § Polyphony). Nothing was invented here to fill the gap:
an engine with two honest knobs beats one with six that a reader has to decode.

## Tests

`ctest -R osc` — golden, ragged blocks, `--rt-guard`.

**Conformance:** `ctest -R voice_goldens` replays
`emulator/tests/golden/osc_sine|square|saw|triangle.npy` through
`src/osc_dsp.hpp` at the Python's 220.5 Hz and gets **−inf dBFS** peak error —
bit-identical, all four. That header is the one the box is built from.

`tests/golden/osc.npy` covers what the voice golden cannot: allocation, release
tails overlapping the next note-on, the box through the ABI. It was frozen from
this box after checking peak, RMS, DC and finiteness, and after confirming
note 60 lands on 261.6 Hz and note 72 on 523.2 Hz.

**Bench:** 7.88 µs/block, 0.07 % of a core at 512 frames / 48 kHz (three voices
sounding).
