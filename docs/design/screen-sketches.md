---
tags: [spec, active, ui]
status: active
related: [[control-inventory]], [[ui-aesthetic]], [[parameter-framework]], [[tape-sampling-workflow]], [[design-decisions]]
---

# Screen Sketches — Arbutus-1

Per-page layout, encoder bindings, key-strip contents, menus. Rewritten 2026-09-08 from grill session.
Extended 2026-09-16 for v1 (DDR-045…064): MOD, DRUM › ENV, 8-track TRACK, the full MIXER, TEMPO, FILE, LIVE INPUT, browser file ops. Pages marked **(v1)** are not in the Hollyhock demo.
Aesthetic rules: `ui-aesthetic.md`. Physical controls: `control-inventory.md`.

ASCII boxes are 40 cols ≈ 320 px; row counts are approximate.

---

## Layout (every page, every state)

```
┌────────────────────────────────────────┐ ─┐
│ SYNTH › ENV                            │  │ header       16 px  breadcrumb only, large font
├────────────────────────────────────────┤ ─┤
│ REC → TRK2 · SEQ HOLD                  │  │ dynamic strip 12 px  status subtitles, blank when none
├────────────────────────────────────────┤ ─┤
│                                        │  │
│                MAIN                    │  │ main         160 px  page-specific (an OP-1 screen's height)
│                                        │  │
│                                        │  │
├────────────────────────────────────────┤ ─┤
│ ●ATK    ●DEC    ●SUS    ●REL           │  │ params       36 px  one row: slots 0–3; SHIFT held →
│  12ms    200ms   0.80    340ms         │  │                     ○MODE ○GLIDE … slots 4–7, brown (#1231)
├────────────────────────────────────────┤ ─┤
│ 1 ENGINE │ 2 ENV ▮│ 3 FX   │ 4 MOD    │  │ key strip    16 px  active cell inverted
└────────────────────────────────────────┘ ─┘
```

- Header: breadcrumb only (`PAGE › SUBPAGE › MENU`). No BPM, no status. Uses whole width.
- Dynamic strip: concatenated tickers, priority `REC > LIFTED > SEQ > other`. Each ticker has a long form (alone) and short form (combined). Examples: `LIFTED: TRK2 4.2s — REMOVED` / `LIFT TRK2`, `RECORDING → TRACK 4` / `REC→4`, `SEQUENCER HOLD` / `SEQ HOLD`, `SPEED ×0.75`.
- Param bar: one row; while SHIFT is held it shows slots 4–7 on the SHIFT highlight, brown labels, hollow dots (DDR-032 as amended, #1231). Empty slot = `—`. Slot with fine-tune fallback shows `↳ fine`.
- Key strip: 4 cells, labels supplied by page. Blank on pages without bindings.
- Zone heights never change (ui-aesthetic §4).

---

## SYNTH (bg: performance)

Subpages on 1–4: `ENGINE · ENV · FX · MOD`. Slots A B C X Y Z recall snapshots. Chromatic keys play. ‹ › octave; SHIFT+‹ › held = bend (DDR-052). Second press of 3 / 4 = bypass (DDR-049). SHIFT+STOP = undo last edit (DDR-054).

### SYNTH › ENGINE
Main: engine-specific visual (sampler: waveform with start/end/loop markers; osc: scope). Engine name + patch name as first line of main. Natively the waveform comes from the host's `waveform` message.
Params: engine's slots 0–7. Sampler v1: `START · END · LOOP-IN · LOOP-OUT` / SHIFT `ROOT · TUNE · REV · FADE`. `LIVE INPUT` (v1): `SOURCE (mic/line/usb/resample) · GAIN · — · —`, main = live waveform (DDR-063; the tuner readout is cut from v1, Elfy 2026-10-02, #54).

*As built, 2026-09-17 (stage G):* the live waveform is the ENGINE scope's peak history — the UI sees no samples — with the source named top-left and the current peak in dB at the right. **There is no tuner readout:** §8.2 hands up no pitch estimate and a detector written in the UI would be DSP in the wrong folder. It belongs in the box, and the box has no way to report a number that is not a parameter. GAIN is the box's own trim, −24…+24 dB, printed by its `format_param`.
SHIFT+1 → engine menu. A modulated slot draws a ghost marker (DDR-051).

### SYNTH › ENV
Main: ADSR curve drawn from current values.
Params: `ATK DEC SUS REL` / SHIFT `MODE (poly/mono/legato/unison) · GLIDE · DETUNE · BEND` (DDR-052). GLIDE = portamento time in mono/legato and the bend ramp; DETUNE = unison spread; BEND = bend range in semitones.

### SYNTH › FX
The **rack** (`engine-spec.md` §6): tab 0 = macros, tabs 1..n = one engine each; SHIFT+‹ › between tabs; drawing decisions in `emulator/notes/audio-host.md` § Native audio. Demo (Python path): one effect, its slots 0–7. SHIFT+3 → engine menu for the current tab.

### SYNTH › MOD (v1)
The **MOD rack** (`modulation-spec.md`): tab 0 = the route list, tabs 1..4 = one modulator each.

```
│ SYNTH › MOD                            │
│                                        │
│ 1  LFO.SINE   → OSC.CUTOFF   ±0.35 lin │  four rows, ENC n colour; live source meter per row
│ 2  FOLLOW.ENV → VERB.SIZE    +0.60 exp │
│ 3  MOTION.TX  → ENV.REL      ±0.20 lin │
│ 4  —                                   │
│ ●DEPTH1 ●DEPTH2 ●DEPTH3 ●DEPTH4        │  SHIFT row = curve per route
│ 1 ENGINE │ 2 ENV  │ 3 FX   │ 4 MOD  ▮ │
```

Modulator tabs: that engine's 8 slots; main = its visual (LFO shape, follower meter, the small `envelope` curve drawn unlike the amp ENV). Copy mode relabels the bottom cells to the outputs. Range popup on SHIFT+DROP (master tab). SHIFT+4 → modulator menu for the current tab.

## DRUM (bg: performance)

Identical structure to SYNTH. Param edits target the **last-hit key**; main area shows which key (`KEY 7 · snare.wav`).
Drum sampler: kit = `kit.json` pad map. Per-pad params (`per_note`, ABI 2): `START · END · PITCH · LEVEL` / SHIFT `PLAY (one-shot/gate/loop) · REV · PAN · CHOKE (off/1–4)`. LIFT on a pad, DROP on another = copy.

### DRUM › ENV (v1)
Per-pad AD (DDR-053): `ATK · DEC · — · —`, SUS/REL greyed; main = a short AD curve for the last-hit pad, drawn visibly unlike the synth ADSR `[TBD-Elfy]`.

## TRACK (bg: recording)

1–4 = track select within the shown bank (strip: `TRK1 TRK2 TRK3 TRK4` or `TRK5 … TRK8`, selected inverted, rec-armed shown red); **SHIFT+1–4 flips bank and selects** (DDR-060). No subpages. A bank glyph near the gutter shows the other bank has content `[TBD-Elfy]`.

```
│ TRACK                                  │
│ REC → TRK1                             │
│ 1 ▐███▌▐██████▌        ▏               │
│ 2 ▐████████▌           ▏               │  4 lanes, clips filled w/ waveform outline
│ 3                      ▏               │  playhead = vertical line, bar ticks from BPM
│ 4        ▐███▌         ▏               │  loop region shaded, selected lane brighter
│   |1   |2   |3   |4   |5   |6    01:24 │  ruler: ticks from the zoom ladder (workflow §2.3)
│ ●SCRUB  ●ZOOM   ●SPEED  ●LEVEL         │  LEVEL is also drawn as a tick on the playhead, always
│  SLIDE   —       STEPS   —      (shift)│
│ TRK1 ▮│ TRK2  │ TRK3  │ TRK4          │
```

Lane numbers sit in a left gutter, **outside** the tape. The lane box's left edge is the head of the tape, so sample 0 — box edge, first clip, playhead at zero — lines up and is never behind a number.
Playhead: 1 px off-white normally. Armed (REC tapped or held) or rolling it goes **hot**: 3 px wide, RED, with an off-white foot rising from the bottom in proportion to the input level — silence still reads as armed, a solid red bar. This is the only arming/level indicator TRACK needs.
A take draws **while it rolls**, growing out from under the cursor on the armed lane (peak envelope, red-tinted block); STOP replaces it with the committed clip in the same place.

Encoders: ENC1 scrub (SHIFT: slide clip under cursor) · ENC2 zoom from playhead · ENC3 tape speed = song speed, linked to BPM while `LINK` is on (SHIFT: fixed steps) · ENC4 record/main level.
Ruler ticks follow zoom (DDR-058): 8 bars … 1/32, then s / ms / samples, always 8–16 on screen. SHIFT+‹ › = one tick; STOP+‹ › = loop / tape ends (DDR-057). Recording is overdub-only (DDR-056): the live take draws over what it covers and replaces it on STOP.
Scrub is **audible**: moving the cursor while stopped plays a ~120 ms grain of what is under it at 1× pitch, so a trim point can be found by ear. Turning back auditions what is *behind* the cursor. The grain runs out on its own; a parked cursor is silent.
Zoom: manual via ENC2. Range full tape ↔ **20 ms** (~3 samples per pixel — you place a trim on the waveform). Below one second ENC2 reads in ms; below two bars of window the ruler stops counting bars and counts seconds. Setting a loop frames it **once**, at the moment you set it — it no longer re-frames every frame, which used to silently undo a zoom-in.
One ENC1 detent moves `view_len / 64`, so scrub precision follows zoom. SHIFT + ‹ › is how you travel.
The timeline starts to the **right** of the track-number gutter, so a clip at 0:00 is never hidden behind a digit.
Hold SPEED / hold FILTER: ENC4 becomes the live control, param bar cell relabels (`SPEED ×0.75`, `FILTER LP 2.1k`), dynamic strip echoes it.
Tape keys, LIFT/DROP/SLICE/TAPE, transport: see `control-inventory.md` and `tape-sampling-workflow.md`.
Bank 5–8: v1, SHIFT+1–4 (above). Not in demo.

### TRACK — audit Q1 (2026-09-18): two sentences the build did not match (the first is fixed)

Recorded here so the page is read against what is built; both are *spec beats code* and
go to issues (`docs/design/audit-2026-09-18.md`), not to an as-built rewrite.

- **ENC3.** ~~Built as the host's varispeed (`tape_speed {speed}`, SPEED ×0.25…4,
  pitch-coupled, persistent), which multiplies *on top of* LINK. With LINK on, turning it
  does **not** move BPM, so `tape-sampling-workflow.md` §2.3's "turning either moves both"
  is half built: BPM drags the tape, ENC3 does not drag BPM.~~ **Fixed 2026-09-24 (#69).**
  *As built:* while `LINK` is on the ENC3 cell **is** the song tempo — it reads `BPM`, one
  detent is 1 BPM, and the turn goes through `App.set_bpm`, the one door the TEMPO page, the
  `‹ ›` nudge and tap tempo already use, so the host gets its `tempo {bpm}` and drags the
  tape by the link factor. With `LINK` off the cell is `SPEED` and moves the varispeed only,
  which is §2.3's other half. Two things kept deliberately: **SHIFT+ENC3 is still the fixed
  SPEED steps** either way — the varispeed multiplies on top of LINK (`PROTOCOL.md`
  § Playing the tape), so it is a different control and not a second tempo — and a MIDI
  master still owns the tempo, so under `SYNC midi` the cell is named, dim and inert here
  exactly as it is on TEMPO (`pages/track.py` `on_encoder` / `slots`).
- **The LEVEL tick.** Line "LEVEL is also drawn as a tick on the playhead, always" and the
  paragraph "Playhead: 1 px off-white normally … the only arming/level indicator TRACK
  needs" disagree with each other; the build is the second one (input peak as a foot, armed
  or rolling only; `docs/logs/2026-09-12.md` § 2). Nothing draws ENC4's value.

## MIXER (bg: recording)

Subpages `MIX · EQ · FX · MASTER`. SHIFT+MIXER (held) = the **signal-path screen** (v1): instrument → MOD/FX racks → tape → per-track EQ → mixer → master rack → drive → comp → out, with sidechain and route arrows `[TBD-Elfy]`.

### MIXER › MIX
Main: **8 columns** (v1; 4 in demo), level bar with real-time peak meter behind it, pan marker, dB readout; the shown bank's four drawn bright, muted columns dim, soloed marked.
Params: `TRK1 TRK2 TRK3 TRK4` (or 5–8) levels / SHIFT: pans. SHIFT+‹ › = bank. SHIFT+1–4 = mute, hold 1–4 = solo (DDR-061). Bank follows TRACK's on arrival.

### MIXER › EQ (v1)
Per-track (DDR-062). Main: 8 narrow columns aligned with MIX, the selected one highlighted, its curve drawn.
Params: `TRACK · LOW · MID · HIGH` / SHIFT `HP · LP · MID-F · MID-Q`. LOW/HIGH = shelves ±12 dB, MID = peak ±12 dB at MID-F with width MID-Q; HP/LP = per-track filter cutoffs. Demo: static params, no DSP.

### MIXER › FX (v1)
The master **rack** — identical to SYNTH › FX, on the master bus after the EQs. Tape-character boxes live here. Demo: static.

### MIXER › MASTER
Params: `LEVEL · COMP · DRIVE · BAL` / SHIFT `COMP REL · DRIVE TONE · — · —` (v1; demo `LEVEL · COMP · — · —`, DDR-027). Main: master meter. Last page because it is the last thing in the chain.

### MIXER — as built (stage F2, 2026-09-17)
- **`1`-`4` carry three gestures**, resolved by DDR-001: **tapped** = the subpage, **held** (0.28 s) = solo that column while held, **SHIFT** = mute and latch. The solo is not sent on the press, so changing subpage never flicks the other tracks silent.
- **Exception:** on MIXER › FX, **SHIFT+`3` is the effect browser**, as it is on SYNTH › FX — the master rack has no other door. SHIFT+`1`/`2`/`4` still mute there, and mute of track 3 is one key press away on MIX.
- **Mute and solo are the host's** (`mixer {track, mute|solo}`, §15.13) and the page draws `state.tape.tracks[i]` back. Solo wins over mute and the gate is on the mix only — that is H7 as built, and the UI holds no second opinion.
- **SHIFT+`‹ ›` moves the bank and the selection together**, because the shown bank is `selected // 4` (DDR-060). MIXER and TRACK therefore always show the same four.
- **The EQ page keeps no ranges**: the host's reply carries the clamped block and the cells print that. HP at 20 Hz and LP at 20 kHz read `OFF`, not a frequency, because flat is bit-exact bypass.
- **The signal-path screen's `[TBD-Elfy]` arrows** are three chained rows of stage names in the §15.5 order with a stage that is currently bypass drawn dim, and the sidechain sources listed as one line rather than drawn as arrows — at 5×7 an arrow between two rows is three pixels of ambiguity.

## SEQ (bg: recording)

No 1–4 binding (blank strip). Slots A B C X Y Z = sequencer snapshots (engine + pattern + params). SHIFT+SEQ → sequencer engine menu. HOLD = ENC4 param (no key; C is a slot). ‹ › = octave (playback) / undo step, insert rest (SHIFT held).

### SEQ (endless, v0.1)
Main: big step number (large font ×3), inverted while in entry mode, increments on entry and on playback. Below: note row for the last ~12 steps (`C E G · · F#`), target instrument. Fun visuals welcome as long as the number dominates.
Entry: hold SHIFT → step 0; each key/chord records and increments; › rest; ‹ undo. Release SHIFT ends entry; next SHIFT press starts over from 0.
Playback: pattern runs while a chromatic key is held (transposed); HOLD keeps it running.
Params: `DIV(1/4…1/32T) · SWING · ACTIVE · HOLD` / SHIFT: `— · — · — · DIR(fwd/rev/rand)`.
ACTIVE (default off, drawn dim while off) arms the pattern. Tap SEQ while already on the SEQ page to toggle it. Armed, a chromatic key on any page drives the pattern, so you can arm a track and trigger the sequence into the take; disarmed, the keys play the instrument everywhere. Re-triggering on a new key while one is held transposes and keeps the running timing — the phrase never restarts.
Dynamic strip: `SEQ → SYNTH` / `SEQ HOLD`.

*As built, 2026-09-17/18 (stages H1, H2):* **the param row above is v0.1's and is superseded.** The SEQ slot takes four boxes now (`engine-spec.md` §15.14) and the eight cells are whatever the loaded one declares — `arbutus/endless` is `DIV · SWING · HOLD · DIR / GATE · PATTERN · ROT · —`. **ACTIVE has no cell**: there is nowhere consistent for a UI-only knob across four boxes that declare seven or eight parameters each, so it is a badge in the header (green armed, dim off) and the SEQ-key tap above is its only gesture. 1–4 are the box's own soft-keys (DDR-041), so entry is `1` (REC) held rather than SHIFT, `2` is REST — or a **tie** with a key still held — and `3` is UNDO; `‹ ›` press the same two. Sequencer browser items are pattern *files* and load as the box's `pattern` asset (DDR-037, §15.17); a pattern is saved by promoting a patch, which writes `seq/<box>/<name>.seq` beside it (control-inventory § slot keys: SEQ has no save gesture of its own).

## TEMPO (bg: utility)

Blank strip. Tap TEMPO repeatedly = tap tempo. ‹ › nudge ±1 BPM, SHIFT ±0.1.
Main: BPM large; metronome glyph; beat indicator blinks on PLAY; `LINK` state shown.
Params: `BPM · CLICK LVL · CLICK PITCH · LINK` / SHIFT `BPM FINE · COUNT-IN (0/1/2 bars) · SYNC (free/midi) · —` (v1; demo has the bottom row minus LINK). Click level 0 = off; pitch 0 fades to silent.
LINK on (default): BPM and tape speed are one control, audio resamples; off: BPM sets the grid only (DDR-016 as amended, DDR-059).

*As built, 2026-09-18 (stage H2):* all eight cells exist. LINK is the host's `tempo {link}` read straight back from `state.tape.link.on` — the page keeps no copy and never moves `base_bpm`. **COUNT-IN is a delayed punch, not a pre-roll**: the transport rolls from where you asked, the click counts, and the take begins N bars later — a pre-roll would have to locate backwards, and the head of the tape is exactly where a count-in is wanted. REC pressed during the count cancels it; REC + a note key ignores it, that gesture being "start here, on the grid, now". **SYNC `midi` locks BPM, BPM FINE, the `‹ ›` nudge and tap tempo** and does nothing else: there is no MIDI clock source until FILE › MIDI (stage J2), and a switch that changes nothing reads as broken. ENC1 is whole BPM and rounds as it turns, so the tenths live on BPM FINE. The main area carries `LINK ON/OFF` at the left, `SYNC FREE/MIDI` at the right and `COUNT-IN` in the middle while one runs.

## HELP (bg: utility)

Tap: page explaining hold-HELP gestures (DDR-025). Hold HELP: overlay on current page describing it. Hold HELP + control: overlay describing that control. Stub copy in demo.

*As built, 2026-09-18 (stage J2):* the overlay's **last line is always the
battery**, on every page and for both overlay kinds — HELP-hold is the one
gesture available everywhere from every state, which is why § FILE › SYSTEM
puts the battery on it. **There is no battery telemetry on the device**:
`docs/specs/io-map.md` assigns no pin to a fuel gauge, so the readout says
`NO TELEMETRY` rather than inventing a percentage, and on the desktop emulator
it reports the **laptop's** battery, which is a real number about the machine
the instrument is running on. The `BATT 12%` ticker appears under 15 % and sits
at the bottom of the dynamic strip's priority order (DDR-031) — it is the one
status nobody asked for.

## INPUT (bg: utility)

Live sampling per `tape-sampling-workflow.md` §1 (DDR-036): `1` arm, `2` retry; params `THRESH · LENGTH · GAIN · MONITOR`. Main: live meter + the captured waveform. Input *routing* is not here — it is the `LIVE INPUT` engine (DDR-063).

*As built, 2026-09-17 (stage G):* the SHIFT row carries **`SOURCE · — · — · —`**, the one field the page and that engine share — it is the host's `input_source` and is read back, never kept here. SHIFT+ENC1 is therefore SOURCE rather than fine THRESH; slots 5–7 stay empty so SHIFT still fine-tunes LENGTH and GAIN. The header names the selected source. MONITOR listens to the **jack**, never to RESAMPLE.

## USB/FILE (v1) (bg: utility)

Subpages `PROJECT · MIDI · USB · SYSTEM` (DDR-019 scope: offline-necessary only). Demo: `NOT IN DEMO`.

### FILE › PROJECT
Main: project name, date, tape length used, storage free. Params: `NEW · LOAD · SAVE · BOUNCE` as ENUM-style actions on ENC1–4 (turn to the item, the strip asks `1 2 3 4 TO CONFIRM` for NEW) / SHIFT `— · — · — · MASTER FX (on/off, for BOUNCE)`. LOAD opens the two-pane browser (folder = date). A project holds everything: tape, mixer, all six slots on every page, tempo. Bounce = offline WAV, trimmed to where clips exist.

*As built, 2026-09-18 (stage J1):* the four actions are **arm switches**, one
per encoder and one-of-four — turning right cocks that action and *nothing
happens on a turn*, because three of the four can lose a session. The confirm
is on **all four**, not only NEW. `1 2 3 4` must arrive **in order**; any other
key cancels. The sentence `1 2 3 4 TO CONFIRM` lives in the dynamic strip
(DDR-031) and the four key cells read `CONFIRM` with the highlight walking
1 → 4 as they land; the main area's red line names the consequence instead
(`NEW ERASES EVERYTHING`, `SAVE AS PROJ_0002`, `BOUNCE 0:32 · MASTER FX ON`).
`1`–`4` are the subpages until something is armed and the confirm while it is.
**LOAD's browser highlights rather than applying live** — a deviation from
DDR-030's live-apply, because a project load re-reads eight tape media and doing
that per detent is unusable; the same `1 2 3 4` commits with the list up.
A project is a **folder** (`projects/<date>/<name>/` + a WAV per used track) and
the format is written down in `audio/library/README.md` § Projects, there being
none in the spec package. BOUNCE is `clip_to_asset {track:"all"}` trimmed to
where clips exist, and MASTER FX is the host's `master_fx`, whose rack latency
the host compensates offline. MIDI, USB and SYSTEM are stage J2 and are named
placeholders.

*Decided 2026-10-12 (Elfy, Play date 1's grill; D97–D98), superseding the J1 note above where
they differ:* the params read **`NEW ▸ · LOAD ▸ · RENAME ▸ · BOUNCE ▸`** and **nothing is
armed**: a turn right acts, since the instrument is always saved (`device-os-spec.md` § 7.3)
and none of the four can lose work. `▸` marks every turn-to-act cell on the instrument.
- **NEW ▸** opens the **name menu**: the strip reads `PICK A NAME · RENAME IN APP`, the main
  area lists three place names (adjective + place, e.g. `QUIET HARBOUR`), turning past the
  third rolls three more, and a name already on the instrument or the phone is never offered.
  The pushed encoder or the red key commits; the project's folder exists from that moment.
- **LOAD ▸** opens the project browser; the encoder scrolls, the push or the red `OPEN` key
  loads, any other page key closes it.
- **RENAME ▸** opens the same name menu with the current name first, marked `(current)`.
- **BOUNCE ▸** starts the render at once. A played key during it asks
  `CANCEL BOUNCE TO PLAY? · 1 YES · 2 NO` (a choice, so neither key is red).
- After an offload of the open project the instrument shows `PROJECT OFFLOADED TO PHONE` and
  the name menu for a new project. A file job the phone runs holds the keys
  (`WORKING ON EXPORT · HOLD TIGHT`); low storage asks to empty the trash
  (`device-os-spec.md` § 7.3, 2026-10-12).
- `1 2 3 4 TO CONFIRM`, the arming and SHIFT+ENC3's copy are gone.

*As built (#817, 2026-10-13), D97's half:* SAVE and SHIFT+ENC3's copy are gone, ENC3 is an
empty cell until #818 builds RENAME, and the main area names the open project (there is
always one). NEW, LOAD and BOUNCE still arm and ask `1 2 3 4` until #818.

*As built (#818, 2026-10-13), D98's half:* the four cells read `NEW › · LOAD › · RENAME › ·
BOUNCE ›` and a turn right acts (a turn left does nothing). **`›` stands in for `▸`**: the
font has no `▸` and `ui/font.py` is not a run's to edit; Elfy may draw the glyph (§ 4 of
`ui-aesthetic.md`). Each cell's value line says what the turn opens (`PICK NAME`, `PROJECTS`,
`TO WAV`). **The red key is the one under the turned cell**: `1 PICK` for NEW, `2 OPEN` for
LOAD, `3 PICK` for RENAME, so the key to press sits below the knob just turned. In the name
menu any encoder moves the highlight and the last one turned is the one whose push commits;
another encoder's push, a number key other than the red one, or any other page key cancels,
and a cancelling number key does not also change the subpage. LIFT and DROP still work in the
LOAD list (DDR-055). The breadcrumb reads `USB/FILE › NEW › NAME` (or `RENAME › NAME`). The
same card always offers the same three names first (the order is seeded by the names already
there). RENAME is the phone's rename of the open project (#821), from the instrument. After an
offload of the open project the page opens with NEW's name menu, and its pick renames the
project the release opened (cancelled, the auto-name stays). **Not built:** BOUNCE's
`CANCEL BOUNCE TO PLAY?` (the instrument's bounce blocks until done, so there is no "during"
yet: #920); "a name already on the phone" (the instrument cannot see the phone's names, only the
card's).

### FILE › MIDI
Params: `IN CH · OUT CH · CLOCK IN · CLOCK OUT`.

### FILE › USB
Params: `MODE (storage / midi) · CHARGE · PAIR (~~BLE~~, for the app) · —`. *2026-10-14, #935: `PAIR` opens the instrument's own Wi-Fi network for the phone, not Bluetooth, since #519 (v1 has no Bluetooth; `companion-app-spec.md` § 3.1).* Entering storage mode force-saves first (DDR-021). A `.fw` or `.arbox` in the storage root is applied automatically (DDR-064).

**As built (D5b, #103, 2026-09-20): the fourth cell is `UPDATE`, not `—`.** The `.fw` line
needs a control and this is the only free one: `device-os-spec.md` § 10.4 asks for a
`1 2 3 4` confirm *on this page*, and in this instrument a confirm is an armed action,
which is an encoder turn. The cell is locked and named until `fw.sock` answers (§ 4.3) and
while the computer holds `/data` (§ 11.1 — the inbox *is* the library root); otherwise it
names whichever one of `RESTART` (a staged release: `reboot`), `INSTALL` (an unsigned file
waiting on DDR-073's confirmation: `apply {file, confirm: true}`) or `DISMISS` (a
rollback's `UPDATE FAILED — KEPT <ver>`, § 10.5's "until dismissed") is outstanding. The
main area grows two rows with it — `SLOTS` (which slot is running and which is the way
back) and `UPDATE` (what is waiting) — and the sentence at the foot is § 10's own, taken
from `status.message` rather than composed here. § 10.8 is the full as-built.

**As built (#97, 2026-09-20): `PAIR` is live, and the code takes the foot of the page.**
Stage J2 locked the cell because a laptop has no radio; where `net.sock` answers it opens
and closes `device-os-spec.md` § 9.5's pairing window and the row shows `OFF`, the four
digits, `CODE ACCEPTED`, `PAIRED` or `NO BLUETOOTH ON THIS DEVICE` (a device whose
`arbutus-net` is up but whose adapter is not — that is a different fact from a laptop's
and says so). **The 4-digit code is drawn `LARGE` at the foot of the page**, the only
thing on it that is: it is read from across a room and typed into a phone. While the
window is open it takes that line from the `.fw` sentence, which is persistent and still
there afterwards. There is no `1 2 3 4` in front of it — opening a window a second turn
closes loses nothing — but a second detent in the same direction does **not** re-send,
because `begin()` mints a fresh code every call. § 9.8 is the full as-built.

### FILE › SYSTEM
Main: a scrolling list (ENC1 selects, ENC2 changes): `MIDI VEL CURVE`, `KEY VEL (fixed / motion — hidden without an IMU)`, `POWER-OFF`, `DATE / TIME`, `BRIGHTNESS`, `WIFI (status)`, `FORGET PHONES`, `RESET (1-2-3-4, then again)`. Battery % shows on HELP-hold; a `BATT 12%` ticker appears under 15 %.

### FILE — MIDI / USB / SYSTEM as built (stage J2, 2026-09-18)

The rule of these three pages is that **a control this build cannot honour is
named, dim and locked** (DDR-053's shape) with the reason written in the main
area — never a knob that stores a number nobody reads.

- **MIDI.** `IN CH` (OMNI, 1–16) and `CLOCK IN` are real: the emulator has had a
  MIDI input since phase 3. `CLOCK IN` measures an incoming 24-ppqn clock from
  a bar of its pulses' arrival times (a least-squares fit, #1126; until then the
  median of a beat's intervals), and **that is the source TEMPO's SYNC `midi`
  was locked without since H2** — the local BPM controls stay locked while a
  master is running, and the page shows the switch and the cable as two facts
  (`MIDI CLK` when one is arriving, `MIDI NO CLK` when the switch is on and
  nothing is). **Deviation: there is no MIDI output**, so `OUT CH` and
  `CLOCK OUT` are locked. A 24-ppqn clock sent from a 60 fps UI thread would be
  a musical lie; it belongs to a device runtime with a real timer.
- **USB.** `MODE` is live and does the part of DDR-021 that matters —
  `STORAGE` **force-saves the project first**, with no prompt, which costs no
  naming UI because DDR-020 auto-names. **Deviation: `CHARGE` and `PAIR` are
  locked** — a laptop cannot be a USB device, its charger is not this
  program's, and BLE pairing is the device OS's. DDR-064's "a `.fw` or
  `.arbox` in the storage root applies itself" was **not built** (A/B slots,
  verification and a rollback are a device-OS feature with no device) and the
  page carried one line saying so. **Since #103 that line is live where
  `fw.sock` answers** — the versions, what is waiting, § 10's four sentences
  and ENC4's confirm — and is J2's static sentence everywhere else, which on a
  laptop is everywhere. See *As built (D5b)* under § FILE › USB above.
- **SYSTEM.** The sketch's list exactly, on two knobs, with `KEY VEL` **hidden**
  per the sketch's own parenthesis — there is no IMU. **Since #116 that is
  conditional, not permanent:** the host reports whether a motion sensor is
  fitted and answering (`motion_sensor` on `hello` and in the state document),
  the rows are keyed rather than numbered because the list is no longer a
  fixed seven, and with a sensor `KEY VEL` appears between `MIDI VEL CURVE`
  and `POWER-OFF`. It appears **locked at `FIXED`** with
  `MOTION VELOCITY NOT BUILT` under it: DDR-048's motion-inferred velocity is
  not implemented anywhere (#117), and a settable option that changes nothing
  would be worse than a named one that says so. `POWER-OFF` is read as an
  idle timeout (`OFF / 5 / 10 / 30 / 60 MIN`), because it sits in a list of
  things ENC2 changes; `DATE / TIME` is the host clock, read-only; `BRIGHTNESS`
  is stored but there is no backlight; `WIFI` and `FORGET PHONES` are the
  device OS's and were inert until #97 (below). `RESET` is J1's 1-2-3-4 confirm
  with a **round counter** on it (DDR-064: and then again) and resets the
  instrument *and* the settings — never the library.
- **SYSTEM's ninth row, as built (#74 step 7, 2026-09-24).** The list above names
  eight rows; there is a ninth, **`TAPE BUFFER`**, after `DATE / TIME`, and it is
  read-only. `tape-streaming-design.md` § 3.7 puts the tape's RAM-window numbers
  "on the FILE › SYSTEM page", and this list is already where the instrument says
  what it is doing (`DATE / TIME` and `WIFI` are read-outs too), so it went here
  rather than onto a page of its own. It reads `<resident>/<ranges>` of the window,
  with a note that is the write backlog if the card is behind, else how many blocks
  have played silence because it was, else `NOTHING HAS BEEN READ LATE`. With no
  window — `ranges` 0, which is every build until the tape's medium moves onto
  storage — it reads `ALL IN RAM`. A row and not a meter: what a player acts on is
  the **`HARD DRIVE SLOW`** flag in the dynamic strip, and this is where the count
  can be read afterwards.
- **SYSTEM's two device-OS rows, as built (#97, 2026-09-20).** Where `net.sock`
  answers, `WIFI` draws `device-os-spec.md` § 9.2's
  `OFF · JOINING · <SSID> <ip> · FAILED` and stays a **read-out** — the join is
  the phone's, handed over at pairing, and `FORGET PHONES` is what turns the
  radio off. `FORGET PHONES` is editable, its note says whether there is a
  phone to lose, and it is behind **one** round of 1-2-3-4 and not RESET's two:
  DDR-064's second round belongs to the gesture that throws away more than a
  session, and a forgotten phone is re-paired by standing next to the
  instrument. On a laptop both rows are exactly what J2 drew.
- **Settings are a third kind of state.** A patch is a sound, a project is a
  session, a setting is the device. They are in neither of the other two and
  live in `<library>/settings.json`, whose format is in
  `audio/library/README.md` § Settings, there being none in the spec package.

---

## Menus (selector pages)

Opened by SHIFT+1 / SHIFT+3 / SHIFT+4 on SYNTH/DRUM, SHIFT+SEQ on SEQ. Full-page cover, header shows `SYNTH › ENGINE › SELECT`.

```
│ SYNTH › ENGINE › SELECT                │
│                                        │
│ SAMPLER   ▮│ bass-01.wav            ▮  │  left = folder (engine), right = item (patch/sample)
│ OSC        │ bass-02.wav               │  ENC1 scrolls folders, ENC2 scrolls items
│ FM         │ pad-warm.wav              │  selection applies LIVE, no confirm
│ NOISE      │ pluck.wav                 │  dismiss = any page key (incl. the same one)
│            │                           │
│ ●FOLDER ●ITEM  —  —                    │
│ (blank strip)                          │
```

- Two-pane for **all** engine menus: synth, drum, effect, modulator, sequencer. Folders = discovered from library directories; nothing hardcoded.
- Effects / sequencers without patches show a single-item right pane. Sequencer items are patterns (DDR-037).
- Loaded item goes into the current slot letter; autosave from there.
- **File ops (v1, DDR-055):** LIFT takes the highlighted user item (stock items don't lift); DROP in another folder moves it; DROP at folder level makes `FOLDER-NN`; never dropping = `.trash/`. Strip shows the lifted name. Consecutive lifts undo as one group.

*As built, 2026-09-18 (stage J2):* **LIFT moves the item to `.trash/` at once.**
Holding the path and moving only on the drop leaves "never dropping" with no
moment at which it happens; moving on the lift makes the rule self-enforcing,
and it is what a lifted tape clip already does (DDR-013). A plain **DROP** moves
it into the folder highlighted in the left pane; **SHIFT+DROP** is "DROP at
folder level" — it makes `FOLDER-NN` there and drops into it, there being no
folder-only view in a two-pane browser. `.trash/` is dot-prefixed and neither
scanner descends into a dot folder, so a trashed item is really gone from every
list. **Stock is a manifest** (`<library>/stock.json`, written by
`audio/tools/mkstock.py`): nothing on disk distinguishes a promoted patch from
a shipped one. SHIFT+STOP restores the whole group, and outranks the tape's own
undo only while a browser is open or something is still lifted. A **project**
is a folder with media in it and moves whole, the way a kit does.

---

## Screen checklist (demo)

| Screen | Spec'd | Built | Baseline PNG |
|---|---|---|---|
| SYNTH › ENGINE / ENV / FX / LFO | ✓ | | |
| DRUM › ENGINE / ENV / FX / LFO | ✓ | | |
| TRACK | ✓ | | |
| MIXER › MIX / MASTER | ✓ | | |
| MIXER › EQ / FX (static) | ✓ | | |
| SEQ (endless) | ✓ | | |
| TEMPO | ✓ | | |
| HELP page + 2 overlays | ✓ | | |
| INPUT, USB/FILE stubs | ✓ | | |
| Engine / effect / LFO / seq menus | ✓ | | |
| **v1 (not demo):** SYNTH/DRUM › MOD, DRUM › ENV, TRACK banks, MIXER › EQ/FX/MASTER (full), signal path, TEMPO (full), FILE › 4 subpages, LIVE INPUT page | ✓ 2026-09-16 | ✓ 2026-09-18 (all four FILE subpages: stages J1, J2) | |

---

## Session History
- [[logs/2026-09-19-21-17]] — FILE › USB: the fourth cell is **`UPDATE`**, not `—` (#103). The `.fw` line needs a control and § 10.4 asks for its `1 2 3 4` on this page; in this instrument a confirm is an armed action and arming is an encoder turn. Two rows join it (`SLOTS`, `UPDATE`) and the foot of the page carries § 10's own sentences instead of J2's one placeholder. The J2 *as built* paragraph's "not built" is corrected in place
- [[logs/2026-03-27]] — created, 5 ASCII drafts
- [[logs/2026-09-08]] — full rewrite: layout zones fixed, all demo pages, menus, key-strip contents
- [[logs/2026-09-11]] — TRACK: audible scrub grain, zoom down to 20 ms, ms/seconds readouts, loop frames once, timeline clears the gutter
- [[logs/2026-09-12]] — TRACK: lane-number gutter + tape head at sample 0, hot playhead (arming + input level), take drawn while it rolls
- [[logs/2026-09-16]] — session 2: v1 pages — MOD rack, DRUM › ENV, 8-track TRACK + tick ladder, full MIXER (EQ per track, master rack, MASTER), TEMPO with LINK/COUNT-IN/SYNC, FILE › PROJECT/MIDI/USB/SYSTEM, INPUT vs LIVE INPUT, browser file ops
- [[logs/2026-09-17]] — stage F2: § MIXER as built — `1`-`4` as tap-subpage / hold-solo / SHIFT-mute (DDR-001), SHIFT+3 on FX as the effect browser, mute/solo read from the host (H7 as built), the bank moving the selection with it, the EQ keeping no ranges and reading `OFF` at the filter ends, and what the signal path's `[TBD-Elfy]` arrows became
- [[logs/2026-09-17]] — stage G as-built: § SYNTH › ENGINE's `LIVE INPUT` (live waveform, **no tuner readout**) and § INPUT's SHIFT-row SOURCE
- [[logs/2026-09-18]] — stage H2 as-built: § SEQ's row is the box's descriptor and ACTIVE is a header badge; § TEMPO's eight cells, COUNT-IN as a delayed punch and SYNC `midi` locking the BPM controls
- [[logs/2026-09-18]] — FILE › PROJECT as built (stage J1): the four actions as arm switches, the 1-2-3-4 confirm on all four, LOAD's browser as a chooser not a live-apply, MASTER FX on BOUNCE
- [[logs/2026-09-18]] — stage J2 as built: § FILE's MIDI / USB / SYSTEM (the MIDI clock that unlocks SYNC, DDR-021's force-save, the settings list, RESET twice, and every control this host cannot honour named and locked); § Menus' file ops (LIFT straight to `.trash/`, SHIFT+DROP for a new folder, stock as a manifest); § HELP's battery and the fact that no hardware measures one yet
- [[logs/2026-09-18]] — audit Q1 (#31): as-built corrections and audit notes; findings in `docs/design/audit-2026-09-18.md`
- [[logs/2026-09-20-01-25]] — cloud routine (#97): § FILE › USB's `PAIR` and § FILE › SYSTEM's `WIFI` / `FORGET PHONES` gain *As built* notes — J2's three inert cells are live where `net.sock` answers, the pairing code is the only `LARGE` thing on FILE › USB, `WIFI` stays a read-out and `FORGET PHONES` asks `1 2 3 4` once. `device-os-spec.md` § 9.8 is the full as-built
- [[logs/2026-09-24-05-34]] — cloud routine (#74, C1 step 7): § FILE › SYSTEM gains an *As built* note for a **ninth row** — read-only `TAPE BUFFER` after `DATE / TIME`, the tape's RAM-window numbers, which `tape-streaming-design.md` § 3.7 puts on this page. The flag it belongs to is **`HARD DRIVE SLOW`** in the dynamic strip, where `CPU NO HAPPY` is drawn and on the same 3-in-2-s rule
- [[logs/2026-09-24-13-14]] — cloud routine (#69): § TRACK's first audit-Q1 note is fixed — ENC3 reads `BPM` and moves the song tempo while `LINK` is on, `SPEED` with it off; the as-built sits under the struck note
- [[logs/2026-10-13-02-22]] — FILE › PROJECT acts on the turn; one name menu (places), one red key under the knob, push or red key commits
