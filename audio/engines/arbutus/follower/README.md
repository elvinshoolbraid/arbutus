# arbutus/follower

The input follower (`modulation-spec.md` §3.2, §4). A **modulator** that is a
**sidechain consumer**: rectify, smooth, emit. `caps: modulator, sidechain`.
What it follows is the tab's sidechain source — `input`, `tape:1..4`, `self`
(this instrument's own bus, one block late) or `master` — set with
`set_mod_sidechain` or `"sidechain"` in the `mod` document, never a knob, so no
slot is spent on it. With no source it hears silence and lets go.

## Outputs

| id | Name | Range | What |
|---|---|---|---|
| `env` | ENV | 0 … 1 | peak of \|L\|,\|R\| × GAIN through a one-pole with ATTACK / RELEASE, clamped at 1 |
| `fast` | FAST | 0 … 1 | the same with fixed 0.5 ms / 30 ms: transients, for a second route that snaps where ENV swells |
| `inv` | INV | ±1 | `1 − 2·ENV`: +1 in silence, −1 at full level — a ducking source without a negative depth |
| `gate` | GATE | 0 / 1 | 1 while ENV is above THRESH, and HOLD longer |

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `attack` | 0.1 … 500 ms, log | 5 ms | 0.1 ms catches a click inside one block; 500 ms is a swell that ignores the drums. Log because 1 against 5 ms is the character and 400 against 500 ms is nothing (as on duck). |
| 1 | `release` | 5 … 5000 ms, log | 150 ms | 5 ms follows syllables; 5 s is a pad-length tail that makes a filter breathe with the whole phrase. |
| 2 | `gain` | −12 … +36 dB | 0 dB | A mic or a quiet tape track sits at −30 dBFS and would move a route by 3 %; +36 dB brings that to full scale. −12 dB for a hot master that would otherwise sit clamped at 1. |
| 3 | `thresh` | −60 … 0 dBFS | −30 dB | Where GATE opens, measured on ENV (after GAIN). −60 dB is "anything at all is playing"; 0 dB is "only the loudest hit". |
| 4 | `hold` | 0 … 1000 ms | 50 ms | Keeps GATE open after ENV drops, so a signal hovering at the threshold is one gate, not a buzz. 50 ms bridges the gap between two syllables; 1 s holds across a drum bar's rests. |

Slots 5–7 are empty. No `format_param`: every readout is a number and a unit.
Nothing is smoothed beyond ATTACK/RELEASE (rule 7): the knobs are read once
per block and the target smooths what the route sends.

## Timing

Detection runs per frame; the output is **the block's last frame** (§7) —
the newest level there is, since `self`/`master` are already a block old.
Output is block-rate, so it depends on block size and there is no ragged test.

## Tests

`ctest -R follower` — golden and `--rt-guard`; `host_modboxes` loads it in a
MOD tab on `self`.

`follower.npy`: `fx_side.npy` (two decaying bursts 500 ms apart, then
silence) at 128-frame blocks, ATTACK 2 ms, RELEASE 80 ms, GAIN +3 dB, THRESH
−12 dB, HOLD 40 ms. Frozen after checking by numbers: all finite and in range;
INV equals 1 − 2·ENV exactly; ENV passes 0.5 in the second block, FAST in the
first; ENV falls through THRESH at frame 4608 and GATE closes at 6528, exactly
40 ms later; both bursts reopen it; the tail decays to 0.0017.

**Bench:** 1.35 µs/block, 0.01 % of a core at 512 frames / 48 kHz.
