# arbutus/arp

The arpeggiator as an engine. Events in, events out; it makes no sound of its
own. `caps: events_in, events_out, sequencer`. `honours_env: false`.
Fills the **SEQ** slot (§5), spec `engine-spec.md` §15.4.

**The chord is the pattern.** Hold notes and the box plays them one at a time on
the clock, in the order MODE names, across OCT octaves. Nothing is entered,
nothing is stored and nothing is loaded — that is the whole difference from
`endless`, which remembers a phrase, and from `pattern`, which reads one off a
file.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `div` | ENUM `1/4 1/8 1/8T 1/16 1/16T 1/32 1/32T` | `1/16` | `endless`'s list, unchanged, so a phrase moved between the two boxes keeps its rhythm. One per detent. |
| 1 | `mode` | ENUM `UP` `DOWN` `UPDN` `DNUP` `ORDER` `RAND` | `UP` | The six that are actually different. `UPDN`/`DNUP` do **not** repeat the end notes (C E G E, not C E G G E), which is what stops a ping-pong from limping. `ORDER` is the chord as you played it, which is the only way to get an arpeggio that is not a sort. `RAND` draws from `arb_host::rng_seed`, so a golden repeats (§3.3). |
| 2 | `oct` | INTEGER 1 … 4 | 1 | The octave span. 1 is the chord itself. 4 is 48 semitones, which already runs off the top of most patches — past that it is a sweep, not an arpeggio. |
| 3 | `gate` | INTEGER 10 … 100 % | 80 | How much of the step each note sounds for. 80 is the classic arp default: separated, not staccato. 100 is legato (a note ends exactly where the next begins); 10 % is the shortest blip that still reads as a note at 1/32. |
| 4 | `swing` | INTEGER 50 … 75 % | 50 | `endless`'s swing, same arithmetic — even steps `×(1+swing)`, odd `×(1−swing)`. 50 is straight; 75 is the most a step can be pushed before the pattern stops being the pattern. |
| 5 | `hold` | ENUM `OFF` `ON` | `OFF` | Latches the chord when the last key comes up. The next key **down** starts a fresh chord rather than adding to the latched one — otherwise a latched arp only ever grows. |
| 6 | `ratchet` | INTEGER 1 … 4 | 1 | Repeats per step: the step is divided into this many equal hits of the same note, each with its own GATE. 4 at 1/16 is a 1/64 roll at 120 bpm, which is the fast end of useful; more is a buzz, and DIV is the knob for that. |

Slot 7 is empty on purpose — there is no eighth thing this box wants, and a
filler parameter is worse than a blank cell (DDR-044).

## Soft-keys (§3.6): none

`endless` needs soft-keys because it has a pattern to enter, undo and clear.
This box has no memory at all, so every gesture it has is already a knob.
A labelled key that duplicated an encoder would be worse than the blank strip
the SEQ page draws without them — DDR-041 allows up to four, it does not ask
for four.

## How it behaves

- **The clock is frames against `host.tempo_bpm`**, not `ARB_EV_CLOCK`, for the
  same reason as `endless`: a tick at the default `ppqn` of 24 is 2000 frames at
  120 bpm, coarser than a 1/32 triplet step. Steps fire sample-accurately inside
  the block (§3.4).
- **Adding or removing a note does not restart the arpeggio.** The step counter
  keeps running and the sequence is recomputed from whatever is held, so a chord
  change lands on the beat. Letting go of everything stops it (HOLD off).
- **A change between blocks reaches the voice.** HOLD turned off while a
  latched note sounds ends that note at frame 0 of the next block, and a step
  that lands exactly on a block's end is frame 0 of the next one, never
  `frame == frames` (#631). The events are the same at every block size
  (`ragged_events_arp`).
- **Position is a pure function of the step count** for every mode but `RAND`,
  so where the arpeggio is depends only on how long it has been running.
- **STOP (`ARB_EV_TRANSPORT` 0)** drops the chord and silences it, latched or
  not.
- Sixteen notes is the chord limit (`kMaxHeld`) — ten fingers and room for a
  sustain pedal. Anything above 127 after the octave transpose is skipped, not
  wrapped.

## Tests

`ctest -R arp` — three event goldens and `--rt-guard`.

**The golden is an event list, not audio** (§9.4 by extension), as `endless`:
`arbutus-run --events-out` dumps what the box emitted, rebased onto the run's
timeline. `tests/notes.txt` plays G, C, E together (out of order on purpose) and
lets go halfway.

| Golden | What it pins |
|---|---|
| `up.json` | defaults — 60, 64, 67 ascending, each note 4800 of a 6000-frame step (GATE 80 %), and silence the moment the keys are up |
| `updn.json` | `UPDN`, OCT 2, SWING 66, RATCHET 2 — 60 60 64 64 67 67 72 72 …, alternate steps stretched, each step two hits |
| `order.json` | `ORDER` + HOLD — 67, 60, 64 in the order played, and still running after the release |

Checked by hand before freezing: `DOWN` is `up.json` reversed; `DNUP` is
`UPDN` mirrored; a fourth note added mid-run joins the sort without restarting
the count; HOLD turned off mid-run stops it and ends the sounding note
(`tests/test_seqmod.cpp`, #631).

**Bench:** 0.07 µs/block, 0.00 % of a core at 512 frames / 48 kHz. Arithmetic
once per block and once per step, never per sample.
