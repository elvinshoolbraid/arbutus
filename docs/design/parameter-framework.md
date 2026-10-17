---
tags: [design, software]
status: active
related: [[engine-spec]], [[modulation-spec]], [[keyboard-matrix]], [[screen-sketches]]
---

# Parameter Slot Framework — Arbutus

The contract between engine plugins and the UI. The UI renders whatever the current engine puts in these slots — it knows nothing about synthesis.

---

## Slot Structure

Every engine (synth, drum, effect) exposes **up to 8 parameter slots**:

| Slot | Encoder (base) | Encoder (shift) |
|------|---------------|-----------------|
| 0 | ENC1 | — |
| 1 | ENC2 | — |
| 2 | ENC3 | — |
| 3 | ENC4 | — |
| 4 | — | ENC1 |
| 5 | — | ENC2 |
| 6 | — | ENC3 |
| 7 | — | ENC4 |

- Slots 0-3: the param bar's one row (#1231, DDR-032 as amended), controllable without shift.
- Slots 4-7: shown in the same row while SHIFT is held, brown-accented (DDR-005), controllable then. (Until #1231 they were an always-visible top row.)
- An engine may use fewer than 8 slots. Unused slot 4-7 → SHIFT+encoder = fine-tune (×0.1 step) of the slot below it; cell shows `↳ fine`. Unused slot 0-3 → `—`.
- Colour is NOT declared by the engine: slot n always renders in encoder (n mod 4)'s colour (`ui-aesthetic.md` §3.2). `color` field removed from schema.

## Slot Schema

Each slot declares:

```
name:           str       # display label (e.g. "FREQ", "MOD", "ATK")
min_val:        float     # minimum value
max_val:        float     # maximum value
default:        float     # initial value
step_type:      enum      # LINEAR, LOGARITHMIC, DISCRETE
step_size:      float     # increment per encoder detent (LINEAR/DISCRETE)
                          # or multiplier per detent (LOGARITHMIC)
unit:           str       # display unit (e.g. "Hz", "ms", "dB", "%", "")
display_format: str       # format string (e.g. "{:.1f}", "{:.0f}")
```

## UI Rendering Contract

The UI does NOT know engine internals. It only:

1. Reads slot metadata (name, value, range)
2. Renders the param bar: 1 row × 4 cells, slots 0–3, or slots 4–7 on the SHIFT highlight while SHIFT is held
3. Sends encoder deltas to the engine: `engine.set_parameter(slot_idx, normalized_value)`
4. The engine handles all audio/DSP internally

```
┌────────────────────────────────────────┐
│ ◆ FREQ      ◆ MOD       ◆ ATK    ◆ REL    │  ← slot.name
│  440 Hz      0.50        0.1s     0.3s     │  ← slot.format_value()
│  ▃▃▃▃▃▅▅    ▃▃▃▃▅▅▅    ▃▅▅▅▅   ▃▃▃▅▅    │  ← normalized position bar
└────────────────────────────────────────┘
  ENC1         ENC2        ENC3     ENC4

  [the same row while SHIFT is held: brown accent, SHIFT highlight]
┌────────────────────────────────────────┐
│ ○ RATIO     ○ DEPTH     ○ RESO   ○ DRIVE  │  ← slots 4-7
│  2.00        0.80        0.60     0.00     │
│  ▃▃▅▅▅▅    ▃▃▃▃▅▅▅    ▃▃▃▅▅   ▃▅▅▅▅▅   │
└────────────────────────────────────────┘

  [Unused slots]
┌────────────────────────────────────────┐
│ ◆ FREQ      ◆ MOD       ·         ·        │  ← slots 2-3 empty
│  440 Hz      0.50        —         —        │
│  ▃▃▃▃▃▅▅    ▃▃▃▃▅▅▅                       │
└────────────────────────────────────────┘
```

## Engine Registration

At load time, an engine declares its slots:

```python
# Pseudocode — engine registers params
class MyEngine(ISynthEngine):
    def get_parameters(self) -> List[Parameter]:
        return [
            Parameter(name="FREQ", min_val=20, max_val=20000, default=440,
                      step_type=LOGARITHMIC, unit="Hz"),
            Parameter(name="MOD",  min_val=0, max_val=10, default=0.5,
                      step_type=LINEAR, step_size=0.01, unit=""),
            Parameter(name="ATK",  min_val=0.001, max_val=5.0, default=0.1,
                      step_type=LOGARITHMIC, unit="s"),
            Parameter(name="REL",  min_val=0.001, max_val=5.0, default=0.3,
                      step_type=LOGARITHMIC, unit="s"),
            # slots 4-7 omitted → SHIFT+encoder = fine-tune of slots 0-3
        ]
```

The UI calls `get_parameters()` once on engine load, then uses the returned list for rendering + encoder mapping. No hardcoded param names in UI code.

## Modularity Test

Adding a new engine should require:
1. Implement `ISynthEngine` (audio processing + parameter declaration)
2. Drop the file into the engines directory
3. Engine appears in the browser, params render automatically

**Zero UI code changes.** If adding an engine requires touching UI rendering code, the framework has a leak.

## Step Type Behavior

| Type | Encoder Turn | Example |
|------|-------------|---------|
| LINEAR | value += delta × step_size | Volume: 0.0 → 1.0 in 0.01 steps |
| LOGARITHMIC | value *= (1 + delta × factor) | Frequency: 20 → 20000 Hz, feels even |
| DISCRETE | value = round(value + delta) | Waveform: 0=sine, 1=saw, 2=square, 3=tri |

## Parameter `type` (2026-09-15, `engine-spec.md` §3.5)

Each slot also declares `type`: `CONTINUOUS` (bar, `step` per detent along `curve`), `INTEGER` (one unit per detent, value label) or `ENUM` (one entry per detent, shows `labels[i]`). ENUM is how an engine gets a *selector* on an encoder — waveform, division, direction — with no new input mechanism. `step_type` above maps onto `curve` ∈ {LIN, LOG} plus `type`. Parameters cross the engine boundary **normalised 0..1** and are keyed by a stable string `id`, never by index.

The FX page's master tab is a rack of ≤8 **macros** that follow this same 8-slot contract; see `engine-spec.md` §6.

## ABI 2 additions (2026-09-16, `engine-spec.md` §15, `modulation-spec.md`)

- **Engine-computed readouts.** A slot may return its own label string via
  `format_param()` (e.g. a bipolar RATE reading `1/8`, `STOP`, `3.2 Hz`). The UI draws
  whatever comes back; an engine that returns nothing gets the host's number + unit.
- **`per_note` slots** (drum engines): the value shown and edited is the **last-hit
  pad's**; the main area names the pad. Turning sends `set_note_param`.
- **`per_voice` slots**: eligible for `keys.VEL` / `keys.NOTE` routes applied per voice.
  No rendering difference except the ghost (below) tracks the last note.
- **Modulated slots.** A slot with a route on it keeps its **knob value as the label**
  and draws a **ghost marker** on the bar at the live modulated position (DDR-051). The
  bar moves, the number does not. Applies to engine, ENV, FX and macro slots alike.
- **Copy mode on a MOD tab** relabels the four bottom cells to the modulator's outputs
  (`modulation-spec.md` §10); the engine's params are not shown while copying.

## Resolved (2026-09-08)

- Effects, LFOs, sequencers, and pages (TRACK, MIXER, TEMPO) all use the same 8-slot framework. Non-engine pages declare static slot lists.
- DISCRETE params render the value label instead of a position bar.
- The subpage determines whose slots are live: SYNTH›FX shows the effect's slots, SYNTH›LFO the LFO's, etc.
- Autosave: every `set_parameter` is persisted to the active slot letter immediately.

---

