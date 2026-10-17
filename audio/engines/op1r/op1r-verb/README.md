# op1r/op1r-verb — op1r's reverb, unmodified (#1230)

The first OP-1 adapter box (`../README.md`): **op1r's `reverb` effect**, the file itself
(`audio/third_party/op1r-src/synth_sdk/plugins/reverb/reverb.c`, pinned and checked), run as
an Arbutus effect box. op1r is pcppcp's community firmware for the Teenage Engineering OP-1;
its reverb is a fixed-point port of the Mutable Instruments Elements reverb (original code
by Emilie Gillet, MIT). A Griesinger/Dattorro tank like `arbutus/plate`, but grainier: 16-bit
delay lines and Q15 maths, as on the OP-1.

**Licence:** GPL-3.0-or-later with its source, like op1r (`LICENSING.md`).

## Parameters

The four knobs in op1r's order (blue, green, white, red), 0..1 each; the plugin maps them
(the mappings are op1r's, from `reverb.c`'s header). Defaults are op1r's `plugin.json`.

| Knob | What it does, from 0 to 1 | Default |
|---|---|---|
| `TIME` | the loop gain, 0.35 .. 0.98 (Elements stops at 0.95). The measured tail, RT60 at 48 kHz: 2.0 s at 0, 4.3 s at 0.5, 10.4 s at 0.8, ~55 s at 1 | 0.49 |
| `TONE` | the loop's low-pass, 0.3 (dark) .. 0.95 (bright) | 0.61 |
| `DIFF` | the allpasses' gain, 0.5 .. 0.75: how fast echoes smear into a wash | 0.50 |
| `MIX` | dry .. wet | 0.37 |

No range is ours to argue: they are op1r's, and the box exists to run op1r's code as it is.

## What the adapter changes (and what it does not)

- **64 frames late** (1.3 ms; `latency_frames` says so): the plugin renders 64-sample
  blocks, so the box fills one before it renders it. Any host block size gives the same
  samples (`ragged_op1r_verb`).
- **Mono in, the change added to both channels**: the plugin hears (L+R)/2 and its output
  minus that input is added to L and R, as op1r does at the OP-1's master position. Stereo
  dry stays stereo; the reverb itself is mono, as on the OP-1.
- **At most three at once** (the OP-1's three FX positions: op1r keeps its state per
  position). A fourth instance is refused (the host's ENGINE BROKEN), never run on another's
  state.

## Its screen (#1255)

The box's page in the emulator is op1r's own reverb screen, the **Kali** art (credited to
DAN76; no licence stated, here by Elvin's call for his outreach build and held out of the
public repo: `audio/third_party/op1r-src/VENDORED.md`). `screen/rasterise.py` draws
`kalireverb.svg` once with op1r's own drawer (cairo, `harness/render.py`) into `screen/kali.npz`,
layers in the SVG's order; `emulator/ui/kali.py` lays them over black from the four knobs,
as `reverb.c`'s `kali_screen_update` does: TIME shows 1–6 arms, TONE the fire (none, then 5
levels), DIFF the skulls' brightness (5 levels), MIX lights 0–10 lines from the bottom, and
the three needles turn 0–180°. The box's own C never draws it (engine-driven screens wait
for M6). Rasterise again only if op1r's art changes; the command is the script's header.

## 44.1 kHz tuning at 48 kHz

op1r's delay lengths and LFO rates are compile-time constants for the OP-1's 44.1 kHz, and
the box runs them unmodified at the host's rate (48 kHz on the device). Nothing in the harness
can rescale them without editing `reverb.c`. Measured with the same input at both rates:

- the room is **8 % smaller** (every delay is 44100/48000 = 0.92 of its OP-1 length) and the
  tail **7–8 % shorter** at TIME 0–0.8 (2.12 → 1.95 s, 4.68 → 4.33 s, 11.25 → 10.42 s; at
  TIME 1 both are ~55 s);
- the two LFOs that smear the tail run **9 % faster** (0.5 → 0.54 Hz, 0.3 → 0.33 Hz);
- `TONE`'s low-pass sits **9 % higher** in Hz (a one-pole with a fixed coefficient).

Whether that sounds wrong is the play-queue question.

## Tests

- `op1r_vendored`: the op1r files are the pinned commit's, byte for byte.
- `op1r_verb_golden`: frozen from this box (there is nothing else to replay: op1r's harness
  writes 44.1 kHz WAVs of its own input), TIME 0.8 / TONE 0.7 / DIFF 0.7 / MIX 0.6 over the
  shared `fx_input.npy`, 2 s at 48 kHz. npystat: peak 0.289, rms 0.031, finite.
- `ragged_op1r_verb`, `rt_guard_op1r_verb`: the block-size and no-allocation rules.
- `op1r_verb_adapter` (`tests/test_op1r_verb.cpp`): MIX 0 is the input 64 frames late, bit
  for bit; MIX 1 is not; three instances and no fourth; a freed one is taken again; two
  instances share nothing; full-scale noise at every knob's top stays bounded.
- `verify-arbutus op1r-verb`: SHIFT+3 finds OP1R-VERB, its page shows TIME TONE DIFF MIX, a
  note sounds through it.
- `emulator/tests/test_kali.py`: the screen's state is `kali_screen_update`'s; at op1r's six
  preview settings it matches `reverb_preview.png` (box-filtered to 1×); the rack draws it on
  this box's tab only, and not in copy mode.

