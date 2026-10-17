# arbutus/live-input

The world, in the ENGINE slot (DDR-063, `engine-spec.md` §15.2). Load it on
SYNTH or DRUM and the live input runs through the MOD rack, the FX rack, the
tape tap and the mixer like any other instrument. That is why there is no
capture mode anywhere in the device: recording the input is recording an engine.
`caps: audio_out, events_in, sidechain`. `honours_env: false`.

**It has no `audio_in`.** The host hands the selected input to the ENGINE slot
on **`side[]`** — the same port an FX tab's sidechain arrives on — so a source
box is a box with a port rather than a hole cut in the graph. With no input at
all (`--offline`, no device, a source that is not routed) the host passes
`side[]` as NULL and this engine is silence. An engine that invents a signal
when the microphone is missing is worse than one that says nothing.

`events_in` is declared and **ignored**: the chromatic keys must keep playing
while the input is the instrument, and the ENGINE slot is where the notes go.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `source` | ENUM `MIC · LINE · USB · RSMP` | MIC | §15.2's four. `RSMP` is **resample**: the master bus minus the armed track, which is out-to-in bounce (`tape-sampling-workflow.md` §5.4). MIC is the default because it is the one input every unit has. |
| 1 | `gain` | −24 … +24 dB | 0 | A trim on a live microphone, so it is in **dB** and not a 0..8 multiplier — that is the unit a player aims in. +24 lifts a quiet dynamic mic to where the tape can use it; −24 stops a line input clipping the converter. Wider than that is a gain-staging problem the mixer should not be papering over. |

**SOURCE is mirrored by the host.** Turning it here sends `input_source`, and an
`input_source` message writes it back into this knob, so the cell on SYNTH ›
ENGINE and the one on INPUT are the same value and not two opinions. This box
never routes anything itself — it could not, the input stream belongs to the
host (`host/PROTOCOL.md` § `input_source`).

GAIN is this engine's own trim and is **not** INPUT's GAIN: that one is the
capture path's, before the sampling threshold, and lives in `emulator/app/mic.py`.

## Tests

`ctest -R live_input` — golden, ragged blocks, `--rt-guard`.
`ctest -R host_input` is the one that matters: it drives the real host, checks
that `input_source` and the SOURCE knob track each other in both directions,
that MIC passes the host's input through at GAIN, and that RSMP is the master
one block late with the armed track taken back out.

`tests/golden/live-input.npy` is `fx_side.npy` at +6 dB with `source=RSMP`,
frozen from this box: SOURCE changes nothing about what it computes, only what
the host puts on the port, so the golden pins the gain path and the stereo rule.

**Bench:** a copy and a multiply per frame; below the resolution of the bench
harness (< 1 µs/block at 512 frames / 48 kHz).
