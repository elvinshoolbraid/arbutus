# arbutus/euclid

The Euclidean rhythm generator as an engine. Events in, events out; it makes no
sound of its own. `caps: events_in, events_out, sequencer`. `honours_env: false`.
Fills the **SEQ** slot (§5), spec `engine-spec.md` §15.4.

**The numbers are the pattern.** `PULSES` onsets spread as evenly as `STEPS`
allows, rotated by `ROT`; one note fires on every onset, at the pitch of
whatever key is held. Nothing is entered and nothing is stored, which is why
this box has no soft-keys either.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `steps` | INTEGER 1 … 32 | 16 | The cycle. 16 is a bar of sixteenths; 32 is two bars, and past that the cycle is longer than anyone can hear as one thing. 1 is a straight pulse, which is a legitimate place to land while turning. |
| 1 | `pulses` | INTEGER 0 … 32 | 4 | How many onsets go in the cycle. 0 is silence (a real position: it is how you mute the rhythm without unloading it). Anything **at or above STEPS is every step**, so the knob does not have to know what STEPS is — you can turn PULSES up first and STEPS after. 4 in 16 is four-on-the-floor, which is where the defaults land. |
| 2 | `rot` | INTEGER 0 … 31 | 0 | Rotates the necklace under the counter. 0–31 is one full turn of the longest cycle; 32 would be 0 again. ROT is also what decides whether the downbeat is an onset — see ACCENT. |
| 3 | `div` | ENUM `1/4 1/8 1/8T 1/16 1/16T 1/32 1/32T` | `1/16` | `endless`'s list, unchanged, so a division means the same thing in all three sequencer boxes. |
| 4 | `gate` | INTEGER 10 … 100 % | 60 | How much of the step each onset sounds for. 60 is short enough to read as a drum part and long enough to read as a note. 100 is legato — the note runs until the next *step*, onset or rest. 10 % is the shortest blip that still reads as a note at 1/32. |
| 5 | `swing` | INTEGER 50 … 75 % | 50 | `endless`'s swing, same arithmetic — even steps `×(1+swing)`, odd `×(1−swing)`. 50 is straight; 75 is the most a step can be pushed before the pattern stops being the pattern. |
| 6 | `hold` | ENUM `OFF` `ON` | `OFF` | Keeps it running with no key down. Turned on with nothing played yet it latches **middle C**, so the box does something on its own — a rhythm generator that needs a hand on a key to be auditioned is the wrong shape. |
| 7 | `accent` | INTEGER 0 … 100 % | 0 | How far the off-downbeat onsets drop under the downbeat. 0 is flat (every onset at velocity 1). 100 % leaves them at **0.3, not 0** — a silent onset is what PULSES is for, and a rhythm that vanishes at the top of a knob is a trap (DDR-044). The downbeat is `step % STEPS == 0`, so with ROT set it may fall on a rest and nothing is accented; that is ROT doing its job, not a bug. |

## Soft-keys (§3.6): none

There is no pattern to enter, undo or clear — the four knobs *are* the pattern.
A labelled key duplicating an encoder would be worse than the blank strip the
SEQ page draws without them. DDR-041 allows up to four; it does not ask for four.

## The necklace

The spread is the closed form, not Bjorklund's recursion:

```
onset(i)  <->  (i × PULSES) mod STEPS  <  PULSES
```

One multiply per query, no recursion, no allocation and no table — which is what
§3.7.1 wants from a box answering this on the audio thread. It gives Bjorklund's
necklace **up to rotation**: E(5,8) comes out `x.x.xx.x` where the textbook
writes `x.xx.xx.`. Since rotation is a knob here, the difference is one detent
of ROT rather than a different rhythm.

## How it behaves

- **The clock is frames against `host.tempo_bpm`**, not `ARB_EV_CLOCK`: a tick
  at the default `ppqn` of 24 is 2000 frames at 120 bpm, coarser than a 1/32
  triplet step. Steps fire sample-accurately inside the block (§3.4).
- **A new key while one is down re-pitches without restarting**, exactly as
  `endless` transposes: the rhythm is the point and snapping it back to step 0
  mid-bar is never what you want. Letting go of the newest falls back to the
  pitch of a key still down; the **last** key up stops it (with HOLD, latches).
- **A change between blocks reaches the voice.** HOLD turned off while a
  latched note sounds ends that note at frame 0 of the next block, and a step
  that lands exactly on a block's end is frame 0 of the next one, never
  `frame == frames` (#631). The events are the same at every block size
  (`ragged_events_euclid`).
- **STOP (`ARB_EV_TRANSPORT` 0)** silences it and clears the latch.
- Changing STEPS, PULSES or ROT takes effect on the next step. The counter is
  never reset by a knob, so the rhythm morphs in place rather than stuttering.

## Tests

`ctest -R euclid` — three event goldens and `--rt-guard`.

**The golden is an event list, not audio** (§9.4 by extension), as `endless`.
`tests/notes.txt` holds one key for sixteen 1/16 steps and lets go.

| Golden | What it pins |
|---|---|
| `four.json` | the defaults, E(4,16) at 1/16 — an onset every 24000 frames, which is four-on-the-floor at 120 bpm, and silence when the key is up |
| `five8.json` | E(5,8) rotated by 2 with ACCENT 70 — onsets at steps 0, 2, 3, 5, 6, the downbeat at 1.0 and the rest at 0.51 |
| `tresillo.json` | E(3,8) at 1/8 (`x..x..x.`) with SWING 62, GATE 100 and HOLD — alternate steps 13440/10560 frames, notes running to the next step, still going after the key is up |

Checked by hand before freezing: PULSES ≥ STEPS is every step; PULSES 0 is
silence with no stuck note; ROT 1 moves the necklace one step under the counter,
so `four.json`'s onsets land on steps 3, 7, 11, 15; a STEPS change mid-run does
not restart the counter.

**Bench:** 0.08 µs/block, 0.00 % of a core at 512 frames / 48 kHz. Arithmetic
once per block and once per step, never per sample.
