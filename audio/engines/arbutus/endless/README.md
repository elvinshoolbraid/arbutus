# arbutus/endless

The endless sequencer as an engine. Events in, events out; it makes no sound of
its own. `caps: events_in, events_out, sequencer`. `honours_env: false`.
Fills the **SEQ** slot (§5).

A pattern is a list of steps and a step is a list of notes (`[]` is a rest).
`root` is the first note entered, and playback transposes so the root lands on
whatever chromatic key you hold — **the pattern follows your hand**. It runs
while a key is held, or forever with HOLD on, latched at the root. No fixed
length, 99 steps maximum (`tape-sampling-workflow.md` §3). A second key
transposes onto it; letting go of the newest falls back to a key still down,
and the **last** key up stops it (#631).

- **A change between blocks reaches the voice.** HOLD turned off while a
  latched note sounds ends that note at frame 0 of the next block, and a step
  that lands exactly on a block's end is frame 0 of the next one, never
  `frame == frames` (#631). The events are the same at every block size
  (`ragged_events_endless`).

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `div` | ENUM `1/4 1/8 1/8T 1/16 1/16T 1/32 1/32T` | `1/16` | The Python's `DIVISIONS`, straight, including the triplets. One per detent. |
| 1 | `swing` | INTEGER 50 … 75 % | 50 | 50 is straight; 75 is the most a step can be pushed before the pattern stops being the pattern. Even steps get `×(1+swing)`, odd steps `×(1−swing)`, counted in steps played, not the pattern index, so an odd-length phrase, REV and RAND stay on the beat grid (#631). INTEGER because a percent is a countable thing and it keeps the SHIFT fine-tune row honest. |
| 2 | `hold` | ENUM `OFF` `ON` | `OFF` | Keeps it running with no key down, latched at the root. |
| 3 | `dir` | ENUM `FWD` `REV` `RAND` | `FWD` | `RAND` draws from `arb_host::rng_seed`, so a golden repeats (§3.3). |
| 4 | `gate` | INTEGER 10 … 100 % | 100 | How much of the step the notes sound for. **100 is legato** — a step ends exactly where the next begins, which is what the box did before GATE existed, so the default moves nothing. 10 % is the shortest blip that still reads as a note at 1/32 (150 frames at 120 bpm); below that it is a click. INTEGER because a percent is countable. |
| 5 | `pattern` | ENUM `OFF` `4/4` `8THS` `OFFB` `3-3-2` `CLAVE` `GALLOP` `EVERY3` | `OFF` | A 16-step rhythm mask laid over the steps as they fire; a cleared bit turns that step into a rest **without moving it**, so the phrase you entered stays where it is and only its rhythm changes. Eight is one per detent with room left; they are the masks a phrase actually wants (straight, eighths, offbeat, tresillo, son clave, gallop, every third). |
| 6 | `rot` | INTEGER 0 … 15 | 0 | Rotates the mask against the phrase. 0–15 is one full turn of a 16-step mask; 16 would be 0 again. |

The mask runs on its **own free counter**, not on the step index: a 5-step
phrase against a 16-step mask precesses through it and comes back round after
16 phrases, which is the reason to have both. `OFF` is all sixteen bits set, so
it is not a special case in the code either.

### Why `ROT` is a parameter and not SHIFT+turn

`engine-spec.md` §15.4 asks for PATTERN on the SHIFT row with **SHIFT+turn
rotating it**. An engine cannot have that: a parameter change crosses the ABI
as `set_param(idx, 0..1)` and carries no modifier (DDR-041 — SHIFT is the
host's, and only a *soft-key* event has a SHIFT flag). So the rotation is its
own slot in the next SHIFT position, where SHIFT+turn would have put it anyway.
Turning ROT is the same hand movement one detent to the right.

## Soft-keys (§3.6) — `[TBD, Elvin's call]`

| Key | Label | What it does |
|---|---|---|
| 1 | `REC` | **Held**, exactly the way SHIFT was. Down starts a new pattern; chromatic keys write steps; notes pressed together before the first release form one chord step; up ends entry. The pattern is wiped by the *first step entered*, not by REC itself, so tapping REC by accident costs nothing. |
| 2 | `REST` | Inserts a rest during entry (the Python's `›`) — **or a tie, if keys are still held**. See below. |
| 3 | `UNDO` | Removes the last step during entry (the Python's `‹`). |
| 4 | `CLEAR` | Wipes the pattern. The only one of the four that does anything outside entry. |

An engine never sees SHIFT (DDR-041), so the Python's "hold SHIFT on the SEQ
page" gesture had to become something. REC/REST/UNDO are that gesture and its
two companions, one for one. **CLEAR is a choice, not a translation** — the
fourth key had to be something and wiping a pattern is the thing you currently
cannot do without entering a new one. Swap it for `×2`, `HALVE` or `REV` by
editing `make_desc()` and `Engine::softkey`; both are in one file.

## Tied notes

§15.4 says tied notes are "hold keys + `›` while entering", and `›` is REST
here. So REST has two meanings, told apart by whether your hand is still on the
note: **nothing held → a rest; keys still down → a tie.** A tie is a step that
emits nothing and silences nothing, so the step before it simply keeps
sounding — one step longer per tie, and GATE closes at the end of the run
rather than at the end of the first step.

That is one key doing two jobs rather than a fifth soft-key, which DDR-041 does
not allow (four is the maximum). The gesture is the one the spec names, and the
accident case is safe: pressing REST with no key down is still a rest.

**Ties are a FWD idea.** Under `REV` or `RAND` "the step before this one" is not
the step the player entered, so a tie is played as a rest there rather than
holding whatever happened to come out of the shuffle.

## The pattern as a file (DDR-037)

The box declares one asset key, `pattern`:

```json
{"format": 1, "kind": "pattern", "root": 60,
 "pattern": [[60, 64], "tie", [67], []]}
```

A step is an array of MIDI notes, `[]` is a rest and the string `"tie"` is a
tie — the same three things entry can make, written down. `root` is what
playback transposes from; a file that omits it gets the first note it plays,
which is what entry would have set. Loading replaces the pattern and stops
playback; it is control-thread only (§3.2) because it allocates.

Stage H2 is what puts a pattern file on the browser and retires
`native_sequencer.py`'s replay bridge — the box side of it is here and works
now (`ctest -R endless_asset`).

## Three deliberate differences from the Python

1. **Steps fire sample-accurately.** The Python advances its clock once per
   audio block, so a step lands on a block boundary. §3.4 gives an event a
   `frame` offset precisely so a sequencer does not have to.
2. **The clock is tempo and sample rate, not `ARB_EV_CLOCK`.** At the default
   `ppqn` of 24 one tick is 2000 frames at 120 bpm — coarser than a 1/32
   triplet step. Counting frames against `host.tempo_bpm` is what the Python
   does and it is the only thing that resolves the short divisions.
3. **No `ACTIVE` parameter.** The Python's `ACTIVE` decides whether the
   sequencer is listening at all; here that is the host's question — an engine
   in the SEQ slot is active, and an empty SEQ slot is not (§5).

`GATE` in `app/sequencer.py` was dead code: defined at 0.85 and never read.
It is real here (§15.4) and defaults to 100 %, which is the behaviour that dead
code produced — notes end exactly when the next step starts.

## Tests

`ctest -R endless` — the event golden and `--rt-guard`.

**The golden is an event list, not audio** (§9.4 by extension): what this box
produces is notes, and comparing rendered silence would prove nothing.
`arbutus-run --events-out` dumps what it emitted, rebased onto the run's
timeline, and `tests/golden/events.json` is the frozen answer — 14 events, the
first at frame 4000 where the key lands and one every 6000 frames after it.

Also checked by hand before freezing: transposing (trigger G instead of C gives
67/71/74), SWING 75 (step lengths 7500/4500 alternating), DIR REV (0, 2, 1, 0),
DIV 1/8 (12000-frame steps).

Stage H1 adds four more event goldens, same shape: `endless_gate` (GATE 50 %,
three-step phrase, each note 3000 frames of a 6000-frame step), `endless_mask`
(PATTERN `4/4` over a 3-step phrase — steps 0 and 4 of the mask only, so the
phrase precesses), `endless_tie` (a tie entered with the keys down, then a plain
rest) and `endless_asset` (`tests/pattern.json` loaded through `--asset`, gated
at 75 %). `rt_guard_endless_h1` runs the lot with an asset loaded.

`ctest -R host_seq` is the end-to-end one: this box in the SEQ slot driving
`arbutus/osc`, the pattern entered through the real protocol by holding the REC
soft-key, and the rendered audio read back by counting zero crossings —
`[64, 67, 60, 64, 67, 60]` across steps 1–6, on the beat, silent the moment the
key is let go.

**Bench:** 0.09 µs/block, 0.00 % of a core at 512 frames / 48 kHz. It does
arithmetic once per block and once per step, never per sample.
