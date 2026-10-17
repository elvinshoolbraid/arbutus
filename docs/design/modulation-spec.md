---
tags: [spec, active, software, modulation, engines]
status: active
related: [[engine-spec]], [[parameter-framework]], [[control-inventory]], [[screen-sketches]], [[design-decisions]], [[v1-spec-package]]
---

# Modulation Spec — the MOD rack

Produced 2026-09-16 from the OP-1 gap grill (`docs/logs/2026-09-16.md`, session 2;
audit: `op1-gap-audit.md`). This is the contract for everything that *moves a
parameter without a hand on the knob*. It sits beside `engine-spec.md`, which owns
the ABI and the FX rack; the two share one rack mechanism and one LIFT/DROP grammar.

Where this doc names a screen it names the behaviour; the drawing is Elfy's and
marked `[TBD-Elfy]`.

---

## 0. Thesis

The OP-1's LFO page is one modulator per sound, seven fixed modes, destinations
picked by scrolling an encoder through a wheel of symbols, and an envelope source
that is welded to the amp envelope. Every one of those limits goes:

- A **modulator is an engine** (DDR-045). It ships as a box, is shared like a box,
  and is written with the same SDK. The stock set is six boxes; the community writes
  the rest.
- Subpage 4 of SYNTH and DRUM is a **MOD rack**: up to four modulators in tabs, the
  same tab mechanism as the FX rack (DDR-038).
- A modulator has up to **four named outputs**. Routing is **LIFT the output, DROP on
  the parameter** — the macro-binding gesture (DDR-039) with a second payload type.
- Depth is **relative to the knob** (DDR-046): the knob sets the centre, the route
  sets the swing. Turning the target while it is modulated moves the whole excursion.
- The amp envelope stays the amp envelope. A separate `envelope` box is the
  aim-anywhere envelope (DDR-047).
- Modulators are **global**, not per voice. Velocity is the one per-voice source,
  delivered through the SDK (DDR-048).

---

## 1. Terms

| Term | Meaning |
|---|---|
| **Modulator** | An engine with `ARB_CAP_MODULATOR`: consumes events and/or sidechain audio, emits ≤4 control values per block. |
| **Output** | One named control value of a modulator, e.g. `LFO.SINE`, `FOLLOW.ENV`. Unipolar (0..1) or bipolar (−1..1) as declared. |
| **MOD rack** | The contents of subpage 4: 1–4 modulators in tabs plus a master tab listing the routes. |
| **Route** | `source.output → target.param` with depth, curve and optional lo/hi window. |
| **Target** | Anything in §5: an engine param, an ENV value, an FX-tab param, an FX macro, or a host virtual param. |
| **Ghost** | The on-screen marker showing where a modulated parameter *currently is*, as opposed to where its knob is set. |

---

## 2. The MOD page

Subpage 4 of SYNTH and DRUM. Key-strip label **`MOD`** (was `LFO`; `control-inventory.md`
and `screen-sketches.md` amended).

### 2.1 Tabs

| Tab | Page | Slots |
|---|---|---|
| 0 | **master** — the route list | ENC1–4 = depth of routes 1–4; SHIFT+ENC1–4 = curve of routes 1–4 |
| 1..4 | one modulator each | that modulator's own ≤8 params |

- Navigation, reveal of the empty tab, tab indicator: identical to the FX rack
  (`engine-spec.md` §6.1, DDR-038). SHIFT+‹ › moves between tabs.
- A rack with one modulator and no routes opens on that modulator's tab; otherwise on
  the master tab; keeps your place thereafter (DDR-028).
- SHIFT+4 (the subpage's selector) opens the two-pane browser for **the current tab**:
  left = modulator engines, right = that engine's saved items. On the master tab it
  opens on tab 1. Same as SHIFT+3 on the FX rack.

### 2.2 The master tab

Four rows, one per route, vertical:

```
│ 1  LFO.SINE     → OSC.CUTOFF     ±0.35  lin │
│ 2  FOLLOW.ENV   → VERB.SIZE      +0.60  exp │
│ 3  MOTION.TILTX → ENV.RELEASE    ±0.20  lin │
│ 4  —                                          │
```

- Each row shows source.output, target, signed depth, curve, and a small live meter
  of the source `[TBD-Elfy]`.
- ENC n turns route n's depth; the row is drawn in ENC n's colour. Routes are limited
  to four **on purpose**: it is what four encoders can own, and it makes the master
  tab a page rather than a list.
- LIFT on the master tab lifts route n (turn ENC n in copy mode) — lift-without-drop
  removes it (DDR-013), DROP onto another row moves it. This is how a route is deleted.

### 2.3 Bypass

Pressing **4** while already on the MOD subpage toggles bypass: on the master tab the
whole rack, on a modulator tab that modulator (DDR-049; same rule as FX with key 3).
A bypassed modulator's routes hold their targets at the knob value. Bypass state is
stored per tab in the `mod` document.

---

## 3. Modulator engines

### 3.1 Declaration

A modulator is an engine (`engine-spec.md` §3) with:

- `caps` including `modulator`; optionally `events_in` (it wants notes/clock/CC) and
  `sidechain` (it wants audio — see §4). It has no `audio_out`; `audio_in` is meaningless
  in the MOD slot and refused.
- `outputs[]`: 1–4 entries `{id, name, bipolar}`. `name` is the ≤8-char screen label.
- `params[]`: its own ≤8 slots like any engine, `parameter-framework.md` rules apply.

Per block the host calls `process()` with events (and sidechain audio if declared) and
the engine writes `io->ctl_out[i]` for each output, one value per block (§7). The host
reads them after the call.

### 3.2 Stock boxes (v1)

Ranges are the author's job (DDR-044) and live in each box's README; this table fixes
the *shape*. All rates that can sync are bipolar RATE knobs (§3.3).

| Box | Outputs | Params (bottom row / SHIFT row) | Notes |
|---|---|---|---|
| `arbutus/lfo` | SINE ±, TRI ±, SQR ±, S&H ± | `RATE · SHAPE-MORPH · PHASE · RETRIG` / `FADE-IN · SYM · — · —` | RETRIG ∈ {FREE, NOTE}. All four shapes run from one phase, so they are usable together (quadrature via PHASE). |
| `arbutus/follower` | ENV +, FAST +, INV ±, GATE + | `ATTACK · RELEASE · GAIN · THRESH` / `HOLD · — · — · —` | Sidechain source selector per §4. GATE is a 0/1 above THRESH. |
| `arbutus/random` | S&H ±, DRIFT ±, WALK ±, — | `RATE · SLEW · RANGE · SEED` / `— · — · — · —` | SEED is an INTEGER so a patch can be deterministic. |
| `arbutus/envelope` | ENV +, INV ±, — , — | `ATTACK · DECAY · SUSTAIN · RELEASE` / `TRIG · LOOP · CURVE · —` | TRIG ∈ {NOTE, GATE-IN (sidechain follower), FREE}. **Drawn smaller and in a different style than the amp ENV page** so nobody mistakes the two `[TBD-Elfy]`. |
| `arbutus/motion` | TILT-X ±, TILT-Y ±, SHAKE +, SPIN ± | `SMOOTH · SHAKE-SENS · SPIN-SENS · —` / `— · — · — · —` | Reads the IMU (§13). Absent IMU → outputs 0, page says `NO SENSOR`. |
| `arbutus/stepmod` | STEP ±, GATE +, — , — | `STEPS · DIV · SLEW · DIR` / `RANGE · STEP · VALUE · —` | Values are entered on **two encoder parameters of the UI's own** (#60, 2026-09-23, replacing "hold SHIFT, turn ENC1, › to step"): `STEP` scrolls which step is selected, 1 … STEPS, and latches there; `VALUE` sets that step, ±1. A modulator with a sequencer's gestures — it declares soft-keys? **No** (DDR-041), and it needs none. |
| `arbutus/keys` | VEL +, NOTE ± | *(none)* | Host-provided source, always present as tab **K** on the master row and not counted in the four. Its VEL output is the one per-voice source (§8). |

Deferred to v1.5: `arbutus/midi` (four CC inputs → four outputs). The `ARB_EV_CC`
event is in ABI 2 now so the box needs no ABI change when the MIDI stack lands.

### 3.3 The bipolar RATE knob

Centre = stopped. Right of centre = tempo-synced divisions (8 bars … 1/32T), left of
centre = free Hz (0.05 … 50). The engine owns this: `RATE` is a CONTINUOUS −1..1 param
and the engine's `format_param()` (ABI 2) returns `1/8`, `STOP`, `3.2 Hz`. The host
draws whatever string comes back. Tempo comes from the host block.

---

## 4. Audio-fed modulators are sidechain consumers

An input follower is a sidechain: rectify, smooth, emit. `follower` and `envelope`
(in GATE-IN mode) declare `ARB_CAP_SIDECHAIN` and get the same source selector as any
rack engine (`engine-spec.md` §6.6). Sources:

| Source | Meaning |
|---|---|
| `input` | mic / line / USB, whatever INPUT is set to — as built (stage G) that is `input_source`, **`resample` included** |
| `tape:1..8` | that track's playback |
| `self` | **this instrument's post-rack bus** — the sound modulating itself ("sum" on the Field). One block late; fine at control rate. |
| `master` | the master bus |

`self` and `master` are new source ids; the host already has both buses.

---

## 5. Targets

| Target class | Address | Notes |
|---|---|---|
| Engine param | `engine.<id>` | Any of the ENGINE tab's slots. |
| ENV page | `env.a`, `env.d`, `env.s`, `env.r`, `env.glide`, `env.detune` | Host-owned values; the host re-sends the host block. Per-voice ADSR is snapshotted at note-on, so ENV modulation affects *new* notes — stated so it is not a surprise. |
| FX tab param | `fx.<instance>.<id>` | Same address the macro binder lifts. |
| FX macro | `fx.macro.<slot>` | One hop to move N bounded targets. |
| Host virtual | `host.pitch` (±24 st, bipolar), `host.level` (0..1), `host.pan` (±1) | On the instrument bus. `host.pitch` is also how the bend gesture works (`control-inventory.md`). |

Not targets in v1: modulator params (no mod→mod, DDR-050), tape, mixer, tempo.
An author who wants recursion writes it *inside* a box.

---

## 6. Routes and depth

### 6.1 Schema

```json
{ "source": "lfo_a", "output": "sine",
  "target": "fx.verb.size",
  "depth": 0.35, "curve": "lin",
  "lo": null, "hi": null }
```

Targets are addressed by **string id**, never index (`engine-spec.md` §7.1). Values
are normalised 0..1 at the target like macros.

### 6.2 Depth semantics (DDR-046)

Let `k` be the target knob's normalised value, `m` the output (−1..1 bipolar or 0..1
unipolar), `d` the depth (−1..1):

- **bipolar output:** `value = clamp(k + d·m, 0, 1)` — swings around the knob.
- **unipolar output:** `value = clamp(k + d·m, 0, 1)` — adds on top of the knob;
  negative `d` subtracts.
- `curve` shapes `m` before scaling: `lin`, `exp = sign(m)·m²`, `log = sign(m)·√|m|`,
  `s = m²(3−2|m|)·sign(m)` — the FX rack's curves, made odd for bipolar sources.
- **lo/hi** (optional): when set, the excursion is clamped to `[lo, hi]` instead of
  `[0, 1]`. The knob still sets the centre. Unset = the target's whole range.

Default for a new route: `depth 0.25`, `lin`, no window. Turning ENC n on the master
tab changes `d`; SHIFT+ENC n steps the curve.

### 6.3 The range popup

**SHIFT+DROP on the master tab** opens the same LO · HI · CURVE · DEPTH popup the FX
rack uses (DDR-040), for the route last turned. While LO or HI is turned the target
*sits at that bound* so it is set by ear; the live marker shows where the modulator
currently lands. ‹ › steps through routes. SHIFT+DROP closes. Drawing shared with the
FX popup.

---

## 7. Timing and rate

- Modulators produce **one value per output per block**. The block is **128 frames
  at 48 kHz** on the device and in the emulator (2.7 ms, 375 Hz control rate) —
  `software-stack-v1.md` §14's 128 is now the emulator default too; `--block` remains
  a benchmark flag. *2026-10-14, #935 (G8): the device runs **64 frames** since L4
  (#141, 2026-09-23; `device-os-spec.md` § 1.3.2, `arbutus-audio.service`'s `--block 64`),
  1.33 ms and a 750 Hz control rate; the emulator stays at 128. A box's output does not
  depend on the block size (`engine-spec.md` § 15.7 rule 8); the host's MASTER COMP, which
  steps once a block, does, so its times differ between the two (#148).*
- Every engine **smooths parameter changes** over at least one block toward the target
  (realtime rule 7, `engine-spec.md` §3.7; SDK helper `arb::Smoothed<float>`). That is
  what keeps a 375 Hz staircase off the cutoff. A route therefore never zips; a modulator
  that *wants* steps (`stepmod`, `random` S&H) still gets them because the step is
  ≥ many blocks long.
- Sub-block or audio-rate modulation is **not** in v1. The extension is already visible:
  a modulator is an engine with `out[]`, so an audio-rate mode is a cap bit, not a
  redesign.
- Sync, free-run and retrigger are **the modulator's own parameters** (RETRIG, RATE).
  The host offers tempo and clock events; it imposes no timing model.

---

## 8. Per-voice: velocity only (DDR-048)

Modulators are global: one value per block, applied to a knob, all sounding voices move
together. That is the OP-1's model too, and it is what makes a modulated *sound* feel
like one instrument.

The exception is velocity, because it is per note by nature:

- A synth/drum engine may flag a param `per_voice` in its descriptor.
- A route from `keys.VEL` (or `keys.NOTE`) to a `per_voice` param is forwarded by the
  host to the engine as `set_voice_mod(idx, source, depth)` (ABI 2) instead of being
  applied as a knob offset. `PolyHost` implements it: each voice reads the param at
  `k + d·vel` for its own lifetime.
- A route from `keys.VEL` to a param **not** flagged `per_voice` is applied globally
  with the *last* note's velocity — what the Field's Velocity LFO does. Both are useful;
  the flag says which one an engine supports.
- **Velocity sources.** P3 keys are plain switches — no key velocity on device. Velocity
  comes from MIDI notes, or, with an IMU fitted and SYSTEM › KEY VEL = MOTION, from the
  case jolt at note-on (the Field's trick). Default is FIXED (1.0). Without an IMU the
  setting is hidden.

Everything else per-voice (per-voice LFOs, envelope→pitch per note) is an engine's own
business or v2.

---

## 9. Display

- A modulated parameter's cell shows the **knob value** as its label and a **ghost
  marker** on the bar at the live modulated position (DDR-051). The bar moves; the
  number does not. `[TBD-Elfy]` for the glyph.
- A `per_voice`-routed param shows the ghost at the last note's value.
- The dynamic strip carries the lift payload while binding (DDR-024): `LIFTED: LFO.SINE`,
  short `LIFT LFO.SINE`.
- HELP-hold on the MOD page explains tabs, LIFT/DROP and bypass from the descriptor's
  names, as the FX rack does.

---

## 10. Binding — LIFT / DROP with two payload types

One grammar (DDR-039), extended:

| Where you press LIFT | What is lifted | Where DROP pastes it |
|---|---|---|
| an ENGINE / ENV / FX-tab page | a **param address** | FX master tab → onto a macro slot (existing) |
| a **MOD** tab | a **source output** | any target page → onto the param you turn |
| the MOD master tab | a **route** | another route row (move) or nowhere (delete) |
| a **drum pad** on DRUM › ENGINE | the pad's settings + sample | another pad (copy) |

- **Copy mode on a MOD tab:** the four bottom param cells relabel to the modulator's
  outputs (`SINE · TRI · SQR · S&H`; fewer than four → `—`); turning ENC n grabs output n.
  The engine's own params are untouched — no slot is spent on a selector. Strip:
  `TURN AN OUTPUT TO GRAB IT`.
- **Paste mode on a target page:** strip `TURN A PARAM TO ROUTE TO IT`; turning a
  param creates the route with defaults (§6.2) and returns to the MOD master tab so the
  depth is under your hand.
- Dropping a source onto a param that already has four routes on the rack: refused,
  strip `MOD RACK FULL`.
- LIFT then DROP with nothing in between = cancel (DDR-013 mirror).
- The strip always names the payload type, so the two directions never need explaining.

---

## 11. Storage

`mod` lives in the patch by value, next to `fx`, same shape as a rack:

```json
"mod": { "format": 1, "kind": "modrack",
  "tabs": [ { "instance": "lfo_a", "engine": "arbutus/lfo", "min_version": "1.0.0",
              "params": { "rate": 0.62, "retrig": 1 }, "bypass": false },
            { "instance": "fol",   "engine": "arbutus/follower", "min_version": "1.0.0",
              "params": { "attack": 0.1 }, "sidechain": "self" } ],
  "routes": [ { "source": "lfo_a", "output": "sine", "target": "engine.cutoff",
                "depth": 0.35, "curve": "lin" },
              { "source": "keys", "output": "vel", "target": "engine.cutoff",
                "depth": 0.5, "curve": "lin" } ],
  "bypass": false }
```

`requires` on the patch includes the modulator engines; the §7.4 refusals apply
(`NEEDS ENGINE arbutus/stepmod`). SHIFT+4's browser takes only the current tab's
engine + params; the whole-sound browser takes everything.

---

## 12. ABI 2 — what modulation needs from the boundary

Full delta in `engine-spec.md` §15. The parts this doc depends on:

- `ARB_CAP_MODULATOR`; `arb_output_desc outputs[ARB_MAX_OUTPUTS=4]` on the descriptor;
  `float ctl_out[ARB_MAX_OUTPUTS]` on `arb_io`.
- `arb_param_desc.per_voice`.
- `arb_vtable.set_voice_mod(h, idx, source, depth)` — `source` ∈ {`ARB_VSRC_VEL`, `ARB_VSRC_NOTE`}.
- `arb_vtable.format_param(h, idx, buf, n)` — engine-computed readout (§3.3). *As built:
  `(h, idx, normalised, buf, n)` — the value is passed in so the call is pure and safe on
  the control thread (`engine-spec.md` §15.1 as-built note, `audio/CLAUDE.md` § Defaults).*
- Event `ARB_EV_CC` (a = controller, b = 0..1). Bend is **not** an event: the host resolves bend × range + `host.pitch` into `arb_host.pitch_st`, applied by `PolyHost` (engine-spec §15.10).
- Sidechain source ids `self`, `master` (protocol, not ABI).

---

## 13. The IMU

- **Part:** ST **LSM6DSO** (6-axis accel + gyro), I2C on the CM4's spare bus, INT1 to a
  free GPIO. Added to the P3 parts list (`p3-mono-mule-spec.md`); pin assignment goes in
  `io-map.md` when the schematic is drawn. *2026-10-14, #935 (G8): drawn; on P3's Zero 2 W
  it shares I2C1 with the codec, the gauge and the USB-C controller (address 0x6A), its
  `INT1` on GPIO16 (`io-map.md` § I2C1 address plan, § Compute sheet (P3)).*
- **Outputs** (`arbutus/motion`): TILT-X, TILT-Y = gravity vector components, absolute
  (flat on a table = 0), smoothed by SMOOTH; SHAKE = high-passed acceleration magnitude,
  unipolar, decays over ~150 ms; SPIN = gyro Z, bipolar.
- **The instrument frame** (#1048; review R39's call): which way each output points, the
  same on every board, so a tilt goes the way the hand expects. Seen by the player, the
  instrument face up on a table:
  - **TILT-X +** = the right edge down; **TILT-Y +** = the front edge (the player's side)
    down; **SPIN +** = turning clockwise seen from above; flat = 0.
  - So `arb_host.motion[6]` (and the offline `motion` message) carries: accel x **+1 g with
    the right edge straight down**, y **+1 g with the front edge straight down**, z **+1 g
    flat and face up**; gyro z **+ turning clockwise** from above, and (the run's call,
    #1048, the engine reads neither yet) gyro x **+ while the right edge goes down** and
    gyro y **+ while the front edge goes down**, so each gyro slot is the rate of its tilt.
  - Each board maps its chip's axes to this frame in the host (`ImuMount`,
    `audio/host/imu_lsm6dso.hpp`; `--imu-mount`): P3's `U12` sits on the **bottom** at
    rotation 0, so its chip X points left, Y to the rear and Z down (flat reads −1 g on
    the chip's Z). The bring-up's stage 9 tilts the board each way and checks each sign
    (`docs/specs/p3-bringup/09-imu.md` step 9.4).
- **Velocity inference** (§8): SHAKE sampled within 10 ms of a note-on, scaled by SYSTEM ›
  KEY VEL sensitivity.
- The host reads the sensor on the control thread at ~200 Hz and publishes it to the audio
  thread like any other host-block field; the box reads it from `arb_host` (ABI 2 adds
  `motion[6]`). No I2C in `process()`.

---

### 3.2.1 Entering `arbutus/stepmod`'s values — *as built* (#60, 2026-09-23)

§3.2's original gesture ("hold SHIFT, turn ENC1 for the value, › to step") was
never built, so every stepmod on the device ran the box's default ramp. What is
built instead, and what the table above now says:

* **Two encoder parameters, not a mode.** The box declares five parameters and
  leaves slots 5–7 empty; the UI puts `STEP` in slot 5 and `VALUE` in slot 6.
  Entry is: turn STEP to a step, turn VALUE, turn STEP on. Nothing is held and
  nothing is a soft-key, so DDR-041 is kept without a special case.
* **They are the UI's parameters, not the box's** — the decision #60 asked for.
  The box never sees them. They edit the `pattern` **asset**, the file the box
  opens through `load_asset`, and the UI sends the file back with
  `set_mod_tab_engine`. Making them box parameters would need a `set_param`
  that mutates the pattern, and DDR-037 says a pattern is content, not
  parameters: the box would then hold the same numbers twice, and a patch
  would have two places to disagree about them.
* **The tab draws the steps**, one bipolar cell each about a centre line, with
  the selected one outlined — two encoders with nothing to aim at are not a
  gesture.
* **What is entered is what is written.** The file holds exactly STEPS values.
  The box wraps a shorter list (`values[i % len]`), so a loaded file need not
  match; the first turn of VALUE materialises the wrapped list, which does not
  change what it sounds like.
* The file itself: `audio/library/README.md` § Step patterns. Entered, it is a
  scratch file under `$TMPDIR` that dies with the process (#105); promoting a
  patch or saving a project writes it into `mod/<box>/<slug>.stepmod` first.

### 3.2.2 The **K** tab — *as built* (#68, 2026-09-24)

§3.2's `arbutus/keys` row ("always present as tab **K** on the master row and not
counted in the four") described a tab the UI never drew, so until now no
`keys.VEL` or `keys.NOTE` route could be made on the device even though the host
resolved both. What is built:

* **K is the last box in the tab row**, one wider gap past the `+` of the empty
  tab: the same box, the same size, the same label idiom, set off because it is
  not one of the four. SHIFT+› from the last tab reaches it, SHIFT+‹ comes back,
  and there is nothing past it. A rack with four modulators still has no fifth
  empty tab.
* **It is not a tab index.** The four modulator tabs index the rack document;
  K indexes nothing, so nothing about it changes `n_tabs`, the browser, or
  `add_mod_tab`.
* **Its page names its two outputs** the way a modulator tab names its own
  (`+ VEL`, `+/- NOTE`, in the encoder colours) under an `OUTPUTS` heading, and
  says `HOST SOURCE - NO PARAMS` because §3.2 gives `keys` no parameters and an
  empty bottom row otherwise reads as a page that failed to load.
* **LIFT works exactly as it does on a modulator tab** (§10): the bottom row
  relabels to VEL and NOTE, turning ENC n grabs output n, and the payload is the
  same shape a box's lift makes — so §8's per-voice forwarding and the host's
  `route` message needed no change at all.
* **No bypass and no engine.** There is nothing of the player's to bypass, so
  pressing 4 on K does nothing (DDR-049 is about modulators); K holds no engine,
  so SHIFT+4 on it opens the browser on tab 1, as it does from the master tab
  (§2.1).
* The drawing choices above are `[TBD-Elfy]` and are listed in
  `emulator/notes/as-built.md` § #68 with the rest.

## 14. Unresolved

1. `[TBD-Elfy]` drawings: master-tab rows and meters; the ghost glyph; the small
   `envelope` box page; copy-mode relabelled cells.
2. Default depth 0.25 and the S&H/DRIFT ranges — by ear.
3. Whether `keys` should also expose aftertouch when a MIDI keyboard sends it (cheap;
   waits for the MIDI stack).
4. Audio-rate modulation cap bit — not before a box asks for it.

---

## Session History
- [[logs/2026-09-16]] — created from the OP-1 gap grill (session 2); DDR-045…051
- [[logs/2026-09-17]] — stage G: §4's `input` source is `input_source`, `resample` included
- [[logs/2026-09-18]] — audit Q1 (#31): as-built corrections and audit notes; findings in `docs/design/audit-2026-09-18.md`
- [[logs/2026-09-22-23-27]] — S14 (#60): §3.2's stepmod row rewritten and §3.2.1 added — the entry gesture is two UI-side encoder parameters, STEP and VALUE, editing the `pattern` asset
- [[logs/2026-10-14-08-31]] — Tilt frame in modulation-spec §13; host maps IMU axes per board (P3 bottom-side default, --imu-mount); bring-up 9.4
