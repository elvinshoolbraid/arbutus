# arbutus/stepmod

A step sequence of control values (`modulation-spec.md` §3.2). A **modulator**
with a sequencer's clock and none of its soft-keys (DDR-041). `caps:
modulator, events_in`. The values are **content**, not parameters (DDR-037):
they arrive as the `pattern` asset. Entering them on the device is the UI's
job, not this box's: since #60 the MOD tab adds two parameters **of its own**
after this box's five — `STEP` scrolls the selection, `VALUE` sets the selected
step — and writes the asset. Nothing below changed; the box still only ever
reads the file (`modulation-spec.md` §3.2.1).

## Outputs

| id | Name | Range | What |
|---|---|---|---|
| `step` | STEP | ±1 | the current step's value × RANGE, glided from the previous step by SLEW |
| `gate` | GATE | 0 / 1 | 1 for the first half of every step |

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `steps` | INTEGER 1 … 16 | 8 | 16 is a bar of sixteenths and what a SHIFT+encoder entry page can show; 8 is half a bar, long enough to hear as a pattern. A pattern shorter than STEPS wraps (`values[i % len]`); a longer one plays its first STEPS. |
| 1 | `div` | ENUM `1 BAR 1/2 1/4 1/8 1/8T 1/16 1/16T 1/32 1/32T` | 1/16 | arbutus/endless's divisions with `1 BAR` and `1/2` in front: a sequencer steps notes, a step modulator also walks a filter across bars. Default 1/16, as endless. |
| 2 | `slew` | 0 … 1 | 0 | Fraction of a step spent gliding (smoothstep) from the previous value: 0 is hard steps (still smooth at the target, §7), 1 is a continuous curve through every value. |
| 3 | `dir` | ENUM `FWD REV PING RAND` | FWD | PING does not repeat the end steps (1 2 3 2 1 2 …). RAND is a hash of the step count and `host.rng_seed`: deterministic, and the same step of the song always picks the same value. |
| 4 | `range` | 0 … 1 | 1 | Scales STEP. The pattern is entered at full resolution; RANGE narrows it for this tab without re-entering it — the same pattern gentle on a cutoff and wide on a pan. |

Slots 5–7 are empty here. The UI fills 5 and 6 with `STEP` and `VALUE`, which
are its own and never reach `set_param` (#60); 7 stays empty. No
`format_param`: STEPS is a number, DIV and DIR are labelled ENUMs.

## The pattern asset

```json
{"format": 1, "kind": "stepmod", "values": [1.0, -1.0, 0.5, -0.5]}
```

1–16 numbers, clamped to ±1. Anything else (wrong `kind`/`format`, empty, > 16,
a non-number) makes `load_asset` refuse and the host refuse the load
(`ENGINE BROKEN`). In a `mod` tab: `"assets": {"pattern": "<library-relative
path>"}`, resolved like a patch's (PROTOCOL.md "Assets"); `add_mod_tab` takes
the same map. **Without an asset** the values are a ramp from −1 to +1 across
STEPS (`−1 + 2·i/(STEPS−1)`; 0 at STEPS 1), so a fresh tab already moves.

## Timing

- **Transport playing: locked to the song.** The step is `floor(song beats /
  DIV)`, from `host.song_frame` and the tempo, so a locate lands on the right
  step and the modulation lines up with the tape and the SEQ slot.
  **A tempo change while playing jumps the step**: "song beats" is
  `song_frame` times the *current* tempo, and `song_frame` runs at 1x whatever
  the BPM, so 120 → 120.5 BPM two minutes in moves it four sixteenths in one
  block. The host's metronome uses the same model, so the two stay together and
  both jump. The cure is a song position in beats from the host (an `arb_host`
  field; the review of 2026-10-06, risk 7, #631).
- **Stopped: free-running** on the host tempo from where the song stopped, and
  a note-on restarts it at step 1 (sample-placed, like the lfo's RETRIG) so it
  follows your playing without a transport.

One value per block at the block's first frame (§7): block-rate output, no
ragged test.

## Tests

`ctest -R stepmod` — two goldens, `--rt-guard`; `host_modboxes` loads the
pattern through a patch and moves a target with it.

Frozen from this box after checking by numbers (arbutus-run's transport plays,
so both are song-locked). `stepmod_ramp.npy` (default ramp, 1/16 at 120 BPM,
128-frame blocks, PING, SLEW 0.5): ends-of-step read −1 −0.714 … 1 0.714 … −1
−0.714 (8 steps, ends not repeated); each glide completes 3088 frames into a
6000-frame step (0.5, block-rounded); FWD at the same settings reads the ramp
mid-step and GATE is 1 for the first half. `stepmod_pattern.npy`
(`tests/pattern.json`, 10 values over 12 steps of 1/8 at 90 BPM, RAND, RANGE
0.5, seed 3, 512-frame blocks): every block's value is one of the pattern's values × 0.5
(largest 0.375 = 0.75 × 0.5 over the six steps rendered).

**Bench:** 0.03 µs/block, 0.00 % of a core at 512 frames / 48 kHz.
