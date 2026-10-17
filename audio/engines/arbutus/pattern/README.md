# arbutus/pattern

The fixed-grid step sequencer as an engine. Events in, events out; it makes no
sound of its own. `caps: events_in, events_out, sequencer`. `honours_env: false`.
Fills the **SEQ** slot (§5), spec `engine-spec.md` §15.4.

**`endless`'s sibling and its opposite.** `endless` follows your hand: free
length, and the phrase transposes onto whatever key you hold. This box is a riff
*written down* — a fixed `STEPS`-long grid at **absolute pitch**, so the notes
are the ones in the pattern and the key only starts it. That is the shape a
pattern file wants (DDR-037), which is why this is the box stage H2's browser
hands one to.

Everything else is `endless`'s on purpose: the same four soft-keys with the same
held-REC entry gesture, the same three kinds of step (notes, rest, tie), the
same `pattern` asset and the same swing. A phrase moves between the two boxes
without changing meaning.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `div` | ENUM `1/4 1/8 1/8T 1/16 1/16T 1/32 1/32T` | `1/16` | `endless`'s list, unchanged, so a division means the same thing in all three sequencer boxes. |
| 1 | `steps` | INTEGER 0 … 32, **0 = `AUTO`** | `AUTO` | The grid. `AUTO` is however long the pattern is — what you entered is what loops, so the box behaves like `endless` until you say otherwise. Set **above** the pattern and the tail is rests (five notes on a sixteen-step grid is a riff with space after it); set **below** and the pattern is truncated (three steps of a sixteen-step pattern is a stutter). 32 is two bars of sixteenths, which is as long as a grid can be and still be one idea. `format_param` draws it, because `0 STEPS` on the screen would be a lie — it reads `AUTO 5`, the number being what AUTO currently resolves to. |
| 2 | `gate` | INTEGER 10 … 100 % | 80 | How much of the step the notes sound for. 80 reads as a sequence rather than a pad. 100 is legato — a step ends exactly where the next begins. 10 % is the shortest blip that still reads as a note at 1/32. |
| 3 | `dir` | ENUM `FWD` `REV` `PNG` `RAND` | `FWD` | `PNG` does **not** repeat the end steps (0 1 2 3 2 1, not 0 1 2 3 3 2 1 0), the same necklace `arbutus/stepmod` uses, so a ping-pong does not limp. `RAND` draws from `arb_host::rng_seed`, so a golden repeats (§3.3). |
| 4 | `swing` | INTEGER 50 … 75 % | 50 | `endless`'s swing, same arithmetic — even steps `×(1+swing)`, odd `×(1−swing)`. 50 is straight; 75 is the most a step can be pushed before the pattern stops being the pattern. |
| 5 | `oct` | INTEGER −2 … +2 | 0 | The one transpose there is, in octaves. ±2 covers moving a bass line up out of the mud or a lead down into it; a semitone knob would be a second pitch mechanism, and absolute pitch is the point of this box. |
| 6 | `hold` | ENUM `OFF` `ON` | `OFF` | Runs it with no key down. The key is a **run key** here, not a transpose, so HOLD has nothing to latch — with HOLD on the pattern simply runs. |

Slot 7 is empty on purpose (DDR-044).

## Soft-keys (§3.6)

The same four as `endless`, same ids, same behaviour — REC held starts entry,
chromatic keys write steps, notes pressed together before the first release form
one chord step, REC up ends entry. REST inserts a rest, **or a tie if keys are
still held** (§15.4's "hold keys + `›`"). UNDO removes the last step. CLEAR wipes
the pattern and is the only one that does anything outside entry.

| Key | Label |
|---|---|
| 1 | `REC` |
| 2 | `REST` |
| 3 | `UNDO` |
| 4 | `CLEAR` |

They match `endless`'s deliberately: one entry gesture for the instrument, not
two, and a UI that drives one drives the other.

## The pattern as a file (DDR-037)

```json
{"format": 1, "kind": "pattern", "pattern": [[60, 64], "tie", [67], []]}
```

The same file `endless` reads — a step is an array of MIDI notes, `[]` is a rest
and `"tie"` is a tie — so one pattern plays on either box. **`root` is ignored
here**: this box plays absolute pitch, so a file's root is `endless`'s business.
Loading replaces the pattern and stops playback; it is control-thread only
(§3.2) because it allocates.

## How it behaves

- **Ties are a FWD idea.** Under `REV`, `PNG` or `RAND`, "the step before this
  one" is not the step the player entered, so a tie is played as a rest there.
- **A key is a run key.** Pressing a second one while the first is down changes
  nothing and the phrase keeps its timing, and letting go of either while the
  other is held changes nothing either: the **last** key up stops it (HOLD off).
- **A change between blocks reaches the voice.** HOLD turned off while a
  latched note sounds ends that note at frame 0 of the next block, and a step
  that lands exactly on a block's end is frame 0 of the next one, never
  `frame == frames` (#631). The events are the same at every block size
  (`ragged_events_pattern`).
- **Swing runs on the step count**, not the step index, so REV, PNG and RAND
  swing the same way FWD does.
- **STOP (`ARB_EV_TRANSPORT` 0)** silences a running pattern.
- 32 steps and 8 notes to a chord are the limits.

## Tests

`ctest -R pattern` — three event goldens, the `format_param` table and
`--rt-guard`.

**The golden is an event list, not audio** (§9.4 by extension), as `endless`.

| Golden | What it pins |
|---|---|
| `entry.json` | C E G entered through the soft-keys and run by holding **G** — it comes back 60, 64, 67, which is the absolute-pitch rule doing its job |
| `grid.json` | the four-step asset on a `STEPS` 8 grid, `OCT −1`, HOLD — chord, tie, note, then five rests, looping every 48000 frames, with no key ever down |
| `ping.json` | the same asset under `PNG` with SWING 66 and GATE 50 — indices 0 1 2 3 2 1 on a four-step grid, the tie read as a rest |

`pattern_format` pins the `STEPS` readout: `AUTO 0` on a fresh engine, `AUTO 4`
once the four-step asset is loaded, plain numbers above 0, and the host default
for every other slot.
`pattern_format_threads` (#864) formats `STEPS` 0 on one thread while
`process()` enters and undoes steps on another, the engine compiled into the
test under ThreadSanitizer: the readout comes from a copy of the length the box
publishes atomically at the end of each block, so it may lag a block and never
races the audio thread.

Checked by hand before freezing: `STEPS` 2 truncates the four-step asset to its
chord and its tie; `REV` runs `entry.json`'s three steps backwards from step 0
(C, G, E); entry with the keys still held inserts a tie that holds the step
across the next one.

**Bench:** 0.09 µs/block, 0.00 % of a core at 512 frames / 48 kHz. Arithmetic
once per block and once per step, never per sample.
