# arbutus/envelope

The aim-anywhere envelope (`modulation-spec.md` §3.2, DDR-047). A
**modulator**, not the amp envelope: **one global ADSR** for the whole
instrument, pointed at whatever a route names. `caps: modulator, events_in,
sidechain` (events for NOTE, the sidechain for GATE-IN; the source is the tab's
`sidechain` setting, §4). The UI draws its page smaller and in a different style
than the amp ENV page (`[TBD, Elvin's call]`) — nothing here depends on that.

## Outputs

| id | Name | Range | What |
|---|---|---|---|
| `env` | ENV | 0 … 1 | the envelope |
| `inv` | INV | ±1 | `1 − 2·ENV`: +1 at rest, −1 at the peak — a closing sweep without a negative depth |

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `attack` | 1 ms … 10 s, log | 10 ms | 1 ms is a click-free snap on a filter; 10 s is a swell across a phrase. Takes `A·(1 − start)`, so a retrigger from high up is quick and LOOP keeps its slope. |
| 1 | `decay` | 1 ms … 10 s, log | 300 ms | Same span as ATTACK so an AD loop can be symmetric. Full time from wherever it starts. |
| 2 | `sustain` | 0 … 1 | 0.5 | The level held while the gate is. 0 turns it into an AD shape. |
| 3 | `release` | 1 ms … 20 s, log | 500 ms | Longer than ATTACK/DECAY: a modulation tail that outlasts the note is the point of a separate envelope. Full time from wherever it starts. |
| 4 | `trig` | ENUM `NOTE` `GATE-IN` `FREE` | NOTE | **NOTE**: every note-on the ENGINE gets retriggers the attack from the current level; the **last** key up releases (global, not per voice). **GATE-IN**: the sidechain is the gate — peak detector, instant attack, 50 ms release, opens above −30 dBFS and closes below −33 dBFS (3 dB hysteresis so a hovering level is one gate). No source = never opens. **FREE**: no trigger; see LOOP. |
| 5 | `loop` | ENUM `OFF` `ON` | OFF | While the gate is held, decay's end goes back to attack (from SUSTAIN) instead of sustaining. In FREE, ON cycles A→D→A forever; OFF is **one shot on load**: A, D to SUSTAIN, then R, once — and again on `reset` or on switching TRIG to FREE. |
| 6 | `curve` | −1 … 1 | 0 (`LIN`) | Bends every segment's progress: positive = fast start, slow finish (the analog shape, up and down alike), negative the reverse, `(1−e^{−kx})/(1−e^{−k})` with k = 6·\|c\|. `format_param` draws `LIN`, `EXP 50`, `LOG 50`. |

Slot 7 is empty. No smoothing inside (rule 7): the segment maths reads the
knobs every block and the target smooths the output.

## Timing

Notes and the GATE-IN detector are frame-accurate inside the block; the output
is **the block's last frame** (§7), so a note-on anywhere in a block is already
moving its target that block. Output is block-rate: no ragged test. Note-ons
are counted in every mode, so switching to NOTE with a key down holds. A host
`panic` resets MOD tabs, so a stuck held-count cannot survive it.

## Tests

`ctest -R envelope` — three goldens and `--rt-guard`; `host_modboxes` drives
it from notes through a MOD tab.

Frozen from this box after checking by numbers; INV equals 1 − 2·ENV exactly in all.
`envelope_note.npy` (128-frame blocks, A 50 ms, D 200 ms, S 0.6, R 300 ms, CURVE
+0.5): reaches 1 in the block containing 50 ms and settles on 0.6; the second
note at 24000 retriggers from 0.6; the first key up at 36000 does *not* release
(0.6 held), the last at 48000 does, reaching 0 by 62400; the 2000-frame stab
releases from 0.95 before finishing its attack.
`envelope_gate.npy` (GATE-IN on `fx_side.npy`, LOOP, A 10 ms, D 40 ms, S 0.2, R
100 ms): cycles while each burst holds the gate, releases to 0 after the second.
`envelope_free.npy` (FREE, LOOP, A 250 ms, D 500 ms, S 0, CURVE −0.5, 512-frame
blocks): peaks at 12000, 48000, 84000 — a 36000-frame (0.75 s) cycle.

**Bench:** 0.37 µs/block, 0.00 % of a core at 512 frames / 48 kHz.
