---
tags: [spec, active, ui]
status: active
related: [[design-decisions]], [[screen-sketches]], [[emulator-spec]], [[ui-vision-dump-2026-09-07]]
---

# Control Inventory — Arbutus-1

Every physical input, its gestures, and its global meaning. Per-page meanings live in `screen-sketches.md`.
Ground truth: `reference/lookalike-model.jpg` (printed look-alike, Sep 2026). Photo wins over any table here.

Rewritten 2026-09-08 from the photo + grill session. Previous 5×10 matrix map superseded.
Extended 2026-09-16 for v1 (DDR-045…064): MOD rack, banks, bypass, bend, mute/solo, browser file ops.

---

## Physical layout (as seen from the player)

```
 TEMPO  SLICE/TAPE   ┌──────────┐   (ENC1)  (ENC2)  (ENC3)  (ENC4)
 PLAY   STOP         │  320×240 │
 REC    LIFT         │  screen  │   SYNTH DRUM TRACK MIXER SEQ INPUT HELP USB/FILE
 SHIFT  DROP         └──────────┘
 1  2  3  4   ‹  ›   A/IN  B/OUT  C/LOOP  X/REV  Y/SPEED  Z/FILTER
 [10 black note keys]
 [14 white note keys]                                      2 octaves from F
```

Key counts: 8 left block + 8 page + 12 command row + 24 chromatic (14 white + 10 black) = **52 keys**, 4 encoders, each with a **push** (P3 wires the four into the key matrix, `COL11 × ROW0–3`, `io-map.md`, D9; "no push" was stale until 2026-10-12).
Geometry source of truth: `emulator/chrome/keymap.json` + `screen_rect.json` for counts and topology; pitch is a uniform **16mm** by design. Since 2026-09-10 the photo is rectified and the hotspot file's pixel spacing is metric (`screen_rect.json` carries `px_per_mm`); the mechanical dimensions still live in [[p3-mono-mule-spec]].
Matrix wiring (5×11) is a firmware concern — see `specs/io-map.md`. Not all matrix positions are populated.

## Encoders (×4)

| Control | Colour (cap) | Global rule |
|---|---|---|
| ENC1 | blue | param slot 0 / SHIFT slot 4 |
| ENC2 | green | slot 1 / SHIFT slot 5 |
| ENC3 | red | slot 2 / SHIFT slot 6 |
| ENC4 | orange | slot 3 / SHIFT slot 7; also the live encoder for hold-SPEED / hold-FILTER |

- Relative (delta) input. 1 detent = 1 step of the slot's range. No acceleration.
- SHIFT + turn = slot 4–7. Where an engine leaves slot 4–7 empty, SHIFT + turn = fine-tune of slot 0–3.
- ~~No push. Menus need no confirm (live-apply).~~ *2026-10-14, #935 (review I1): stale since D98 (§ Menus below).* A menu that waits for its commit (FILE › PROJECT's LOAD browser, the name menu) commits on the **push of the encoder just turned** or on its red key; the sound and module browsers (SHIFT+1/3/4, SHIFT + slot key) still apply live as you scroll and need no commit (`pages/file.py`, `app/name_menu.py`, `pages/synth.py` `open_patch_menu`).
- ~~On-screen colour = bright pastel of the cap colour (see `ui-aesthetic.md`).~~ The cap colours and the on-screen shades, light and dark, are `ui-aesthetic.md` § 3.2's (Elfy, 2026-10-03: blue, green, red, orange; ENC3 was cream). *2026-10-13, #633 (finding m-emu-ui-drivers #11): this table had kept the old caps (navy, forest green, warm beige, red-brown); text only, the palette was already right.*
- ~~**Push: nothing in v1**~~ **Push: the commit inside a menu, nothing elsewhere** (Elfy, 2026-09-23, #88; D98, 2026-10-12). The Alps EC11J1525402 has a push contact and P3 wires all four as matrix keys on COL11 × ROW0–3 (work-order § 4.6, D9) as a hedge; `App.on_key` ignores `ENC1_PUSH`…`ENC4_PUSH`. A press is not a gesture on any page until this line changes. *2026-10-14, #935 (review I1): it changed with D98 (2026-10-12): inside a menu that waits for a commit the push is the commit (§ Menus). Outside a menu, as built, a push still does nothing on any page. **[TBD-Elfy]** what the push does outside a menu is desk meeting M5's question (`docs/strategy/desk.md`, the whole-system review § 5 G1); until he answers, this line holds outside menus only.*

## Page keys (×8) — top level, one tap each

(TEMPO is listed at the end of this table for its page behaviour, but physically it lives in the left block — it is not a ninth key in the page row.)

| Key | Page | Subpages on 1–4 | SHIFT + key |
|---|---|---|---|
| SYNTH | synth instrument | ENGINE · ENV · FX · **MOD** | **save the current slot as a patch** |
| DRUM | drum instrument | ENGINE · ENV · FX · **MOD** | **save the current slot as a patch** |
| TRACK | tape | 1–4 = **track select** within the shown bank; **SHIFT+1–4 = flip to the other bank and select that track** (DDR-060) | — |
| MIXER | mixer | MIX · EQ · FX · MASTER; **SHIFT+1–4 = mute** (within the shown bank), **hold 1–4 = solo** (DDR-061) | **signal-path screen** (hold to view) |
| SEQ | sequencer | **engine soft-keys** — a sequencer engine may declare ≤4 labelled keys on 1–4 (+ SHIFT layer), drawn on the strip (DDR-041) | sequencer engine menu |

Tapping SEQ while already on SEQ arms/disarms the pattern (DDR-035). Pressing 1–4 while a selector menu is open closes it and selects that subpage.
| INPUT | live sampling (DDR-036) | 1 arm · 2 retry (as today) | — |
| HELP | help | none | — (HELP is also a hold-modifier, below) |
| USB/FILE | files, MIDI, USB, settings | **PROJECT · MIDI · USB · SYSTEM** | — |
| TEMPO | tempo + metronome | none | — |

**Pressing the subpage key you are already on** (DDR-035, DDR-049): on SEQ arms/disarms the pattern; on FX (3) and MOD (4) toggles **bypass** — the whole rack on the master tab, the current node on an engine tab. Elsewhere it is a no-op.

**No page ever loses your place.** Every page keeps its full state (subpage, selection, scroll, open menu) when you leave and return.
COM is gone — MIDI/USB settings live under USB/FILE.

**The 1-2-3-4 confirm: where something is still lost (DDR-064, stage J2).** *Rewritten
2026-10-14, #935 (review I3): PROJECT's half went with D97 and D98 (2026-10-12: the instrument
is always saved, so PROJECT's four act on the turn, § Menus); the old paragraph is in § History
below.* The four keys 1–4 have a second meaning only while an action that loses something is
armed: they become a **confirm run**, pressed in order, and any other key in any position
cancels. The key strip says `CONFIRM` on all four while it is armed and names the subpages
otherwise, so the strip always says which meaning is live (DDR-022). Nothing happens on the
encoder turn itself. **SYSTEM › RESET** (DDR-064): on `USB/FILE › SYSTEM`, turning ENC2 right
on the `RESET` row arms it and the run is `1 2 3 4` **twice** — the first full run resets the
walk instead of firing, the key strip keeps saying `CONFIRM` and the dynamic strip says
`AGAIN`. Moving ENC1 off the row puts it down, as does any wrong key in either round. The run
is also kept, **once**, for `FORGET PHONES` and the firmware line's actions
(`pages/file.py`'s header; `v1-spec-package.md` § `[TBD-Elfy]`, both decided as built). Which
of the instrument's confirm shapes stays the rule is desk meeting M5's question
(`docs/strategy/desk.md`; the whole-system review § 5 G1).

## Subpage / track keys 1–4

- Bare numerals. Meaning supplied by the page, always shown in the bottom key strip (DDR-022).
- Press = select, selection persists, active cell highlighted (DDR-023).
- SHIFT + 1–4 on SYNTH/DRUM = open the selector menu for that subpage (SHIFT+1 engine, SHIFT+3 the current FX tab's engine, SHIFT+4 the current MOD tab's modulator; SHIFT+2 unused). Each takes **only** its own module out of whatever it lands on — the whole-sound path is SHIFT + slot key.
- A selector opens on what is currently loaded, not at the top of the list.
- Pages with no 1–4 binding show a blank strip.

## REC as a modifier

- **REC held + any note key** (any page) starts the take from that note, with the playhead snapped to the **nearest bar**. The selected track arms itself.
- Hunting for the downbeat after PLAY is how takes end up milliseconds off and never layer; here the first note *is* the start, and every take begins on a bar line.
- From **stopped** only. While already rolling, REC on its own is still the punch-in / punch-out.
- A bare REC tap is unchanged: arm / punch, per the transport table.

## Menus: one next input (Elfy, Play date 1's grill, 2026-10-12; D98)

- **A turn-to-act cell** carries `▸` after its label (`NEW ▸`) and acts on a turn right; no arming
  when nothing can be lost.
- **When there is one next input, its key is red.** A menu that is waiting for its commit (a
  browser's `OPEN`, the name menu's pick) draws that key's cell red, which also says the keys
  beside it cancel: you are on a straight path to finish what you started.
- **A menu commits on the push of the encoder you just turned, or on the red key** (both for
  now; one of the two becomes the rule everywhere once play-tested). Any other page key
  cancels. A choice of two (`1 YES · 2 NO`) has no red key.
- **The name menu** is the one way the instrument names anything: three names, the first
  highlighted, turning past the third rolls three more, never a name already taken, the strip
  `PICK A NAME · RENAME IN APP`. The word lists are a metaphor kept for clarity: **patches are
  plants and landmarks, projects are places, effect racks are tools, sequencer patterns are
  action nouns** (`WALK`, `RUN`). Files already saved keep their names.

## Slot keys A B C X Y Z (green legend) — SYNTH, DRUM, SEQ

- 6 memory slots per page. Tap = recall that slot's full snapshot (engine, patch, all params, page state).
- **Everything autosaves.** There is no save gesture *for a slot* — each change is written to the active slot immediately.
- **SHIFT + slot key** = land on that slot **and** open the whole-sound browser for it (the OP-1's SHIFT+1–8, on our lettered keys). Applied live as you scroll, like every other menu.
- SEQ slots hold sequencer engine + pattern + params.
- **The SEQ letters and an instrument's letters are two different banks, and both carry a sequencer** (*as built 2026-09-23, S13 / #59*). Each instrument owns its own sequencer (DDR-034 as amended), and it rides in that instrument's own slot record because a slot record is a §7.2 patch and a patch carries the sequencer. The SEQ page's six letters are a **sequencer-only** bank on top of that: they hold engine + pattern + params and nothing else, and a recall lands on whichever instrument the page is currently showing — which is the active one. So a favourite sequence can be swapped onto either instrument without touching its sound, and recalling a *sound* still brings its own sequence with it.
- Letters are printed hardware legends. DDR-008 amended: A–C / X–Z, not A–H.

## Patches — promoting a slot

A slot is working state and gets overwritten as you play. A **patch** is that
same snapshot written into the library under a name, so it survives and shows
up in the browsers. Promoting is a copy, not a conversion: slot record and
patch payload are the same dict.

- **SHIFT + SYNTH / DRUM** saves the current slot as a patch in the current engine's folder (D98, 2026-10-12): it opens the **name menu** (§ Menus; patches take plant and landmark names) and saves on the commit, so the sound you cared enough to save has a name you chose; turn and push make it two motions. The name is adjective-plant or adjective-landmark (`DRY-SEDGE`), ≤ 12 characters, never one already in that folder; ENC1's push or the red key **1** commits (no turn opened the menu, so the first encoder is the one "just turned"); the other number keys cancel and stay on the subpage; the strip flashes `SAVED PATCH <name> → <engine>`. A pattern saved with the patch takes the first free action noun (`SLOW-WALK`), not the patch's name. *Rewritten 2026-10-14, #935 (review I2); the old paragraph is in § History below. Built 2026-10-14, #819 (`app/name_menu.py` `WORDS`, `pages/synth.py` `on_shift_page_key`).*
- **Saving an effect rack** (2026-10-12, D98): the effect browser's top level has a green row **above** the racks folder, `SAVE THIS RACK`, the folder's shape in a colour that is not a folder's. The cursor opens on the racks folder as now (down loads, up saves); scrolled up onto the row, the right pane shows the name menu (tool names) under `PICK A NAME TO SAVE RACK`, and the commit writes it to **SAVED RACKS**, listed under the stock racks. If it plays well, *up to save* becomes the browsers' general shape.
- **Sequencer patterns** keep no save of their own: they ride in the patch and the slots; a `.seq` written with a patch takes an action-noun name.
- A patch carries the **whole keyboard**: engine + its params, envelope, effect, LFO, octave, and the sequencer riding with it (engine, pattern, enable state). SEQ therefore needs no save gesture of its own; SHIFT + SEQ stays the sequencer-engine menu.
- You reuse *parts* of a patch by loading the whole thing and swapping the other parts: SHIFT+1 (engine), SHIFT+3 (effect), SHIFT+4 (LFO) each take only their own module.
- A promoted patch becomes the loaded item, so the ENGINE header shows its name.
- **No rename on the device.** Encoder-driven text entry is not worth building; patches are plain JSON under `library/<category>/<engine>/` and get renamed on the companion app.

## Tape keys (brown legend) — same 6 keys on TRACK page

| Key | Tap (stopped) | Hold (playing) | Notes |
|---|---|---|---|
| IN `[` | set loop in at playhead | — | |
| OUT `]` | set loop out at playhead | — | |
| LOOP ↻ | toggle loop on/off | — | (on SEQ page C is a slot key; HOLD is the ENC4 param) |
| REV | reverse clip under cursor | playhead runs backward, snaps forward on release | DDR-010 |
| SPEED | apply last-set speed to clip under cursor | live speed via ENC4, on-screen readout | remembers last value |
| FILTER | bake current filter into clip under cursor | sweep filter via ENC4 from centre | DDR-010, topology open (DDR H3) |

## Arrow keys ‹ ›

| Page | ‹ | › |
|---|---|---|
| TRACK | rewind (hold = scrub back); **SHIFT = one tick back** (DDR-058); **STOP held = loop in / tape start** (DDR-057) | fast-forward; **SHIFT = one tick forward**; **STOP held = loop out / end of last clip** |
| SYNTH / DRUM | octave down; **SHIFT held = bend down**, ramped over GLIDE, snaps back on release (DDR-052) | octave up; **SHIFT held = bend up** |
| SYNTH / DRUM › FX or › MOD (racks) | octave down; **SHIFT = previous rack tab** | octave up; **SHIFT = next rack tab** (an empty next tab reveals the rack — DDR-038) |
| SEQ (playback) | octave down | octave up |
| SEQ (entry mode: the box's REC soft-key held, `1` — not SHIFT, stages N1/H2) | undo last step | insert rest step; **with keys held = tie the note** |
| MIXER | — ; **SHIFT = bank 1–4** | — ; **SHIFT = bank 5–8** |
| TEMPO | nudge −1 BPM; SHIFT = −0.1 | nudge +1 BPM; SHIFT = +0.1 |
| menus / lists | — | — |

## Left block

| Key | Tap | SHIFT + key | Hold |
|---|---|---|---|
| TEMPO | go to TEMPO page; repeated taps = tap tempo | — | — |
| SLICE / TAPE | split clip at playhead on selected track | TAPE: merge two clips on the selected track separated by < ~10 ms | — |
| PLAY | start transport | — | — |
| STOP | stop; second tap returns to start | undo (1 level: tape ops on TRACK, last edit on SYNTH/DRUM — DDR-054) | held + ‹ › = jump to loop / tape ends (DDR-057) |
| REC | arm selected track; REC + PLAY records | — | — |
| LIFT | lift clip under cursor (removes from tape, copies from a loaded sample — DDR-012; moves a browser item — DDR-055) | lift clips under cursor on all 8 tracks | — |
| DROP | drop lift buffer at playhead on selected track (or onto an instrument / pad / macro / param per the payload table) | merge a LIFT ALL buffer onto the selected track; on a rack master tab: the range popup (DDR-040) | — |
| SHIFT | — | — | modifier (DDR-003, DDR-005/006) |

Lift without drop = delete (DDR-013). Lift buffer contents always shown in the dynamic strip (DDR-024).

**LIFT/DROP carry four payload types**, and the dynamic strip always names which (DDR-024, DDR-039, `modulation-spec.md` §10):

| LIFT where | carries | DROP where |
|---|---|---|
| TRACK | a clip (or all 8 with SHIFT) | TRACK (own tracks; SHIFT = merged onto selected); SYNTH/DRUM › ENGINE (becomes the sample / the last-hit pad) |
| an ENGINE / ENV / FX-tab page (copy mode: turn a param) | a **param address** | FX master tab: onto a macro slot; second drop multi-assigns |
| a **MOD** tab (copy mode: the bottom cells become the outputs) | a **source output** | any target page: onto the param you turn → a route |
| the MOD master tab | a route | another row (move) or nowhere (delete) |
| a drum pad (DRUM › ENGINE) | that pad's sample + settings | another pad (copy) |
| a **browser** item (user content only) | the file *or folder* | another folder (move); **SHIFT+DROP** = a new `FOLDER-NN` there; nowhere = `.trash/` (DDR-055) |

**Browser file ops as built (stage J2, DDR-055).** LIFT takes the highlighted
row *immediately* into `.trash/` — that is what makes "never dropping" the
delete, with no later moment at which it has to happen — and DROP is simply the
second move, into whichever folder the left pane is on. SHIFT+DROP makes
`FOLDER-NN` there and drops into that ("DROP at folder level"; there is no
folder-only view in a two-pane browser). SHIFT+STOP restores the whole group of
consecutive lifts, and outranks the tape's own one-step undo **only** while a
browser is open or something is still lifted. Stock content refuses, read from
`<library>/stock.json`. An item may be a folder — a kit or a project — and
moves whole.

Ranges for macros and routes are set by ear in the LO · HI · CURVE · DEPTH popup (**SHIFT+DROP** on the rack's master tab, DDR-040). SHIFT+STOP on SYNTH/DRUM = one-step undo of the last edit there (DDR-054) — built 2026-09-23 (#58): a run of turns on one cell, a browse, a pad copy or a tape→pad drop, one step each. **As built:** the `‹ ›` octave is *not* a step (#152) — it is a performance gesture like the bend, and a move leaves the step already held alone rather than joining it; the gesture never reaches the tape's undo on those two pages, and a slot recall, FILE › NEW and a project load each forget the entry. The sentence that used to end this paragraph — “still restores the previous sample on a drum pad after a per-pad record” — is gone: there is no per-pad record gesture anywhere in the instrument.

## HELP as modifier (DDR-025)

- Tap: HELP page (first screen teaches the hold gesture).
- Hold HELP alone: overlay explaining the current page.
- Hold HELP + any key / turn any encoder: overlay explaining that control in this context.
- Overlays never navigate; release dismisses.
- **As built (stage J2):** the overlay's last line is always the **battery**
  (screen-sketches § FILE › SYSTEM). HELP-hold is the only gesture reachable
  from every page in every state, which is why the battery is on it and not on
  a status bar. Where the platform reports nothing it says `NO TELEMETRY` — ~~and
  on the device that is the honest answer, because `io-map.md` assigns no pin
  to a fuel gauge.~~ *2026-10-14, #935 (review I4): stale since D1 (2026-09-19). P3 carries
  a gauge, an **ADS1115** (an analog-to-digital converter on I2C1 reading the pack's voltage
  through a divider; its `ALERT` wire on GPIO6), `io-map.md` § Battery telemetry on P3,
  `device-os-spec.md` § 6.6, read by `arbutus_os/drivers/power.py`. On P3 the line is the
  percentage; `NO TELEMETRY` is the answer on the emulator, the bench boards and V0.1, which
  have no gauge, and on P3 only until its first board is brought up (`p3-bringup.md`).*

## Chromatic keys (×24)

- 14 white + 10 black, **F to E**, 2 octaves. **No velocity sensing on the device** (plain switches); MIDI velocity is honoured, and with an IMU fitted SYSTEM › KEY VEL = MOTION infers velocity from the case jolt at note-on (DDR-048). Default FIXED.
- SYNTH / DRUM: note on/off. DRUM: 24 one-shot voices, last-hit key = param edit target.
- SEQ: entry (the box's REC soft-key held — `1`, not SHIFT) or transposed playback trigger.
- TRACK / MIXER / others: play the active instrument (audible, not recorded unless REC).

## Active instrument

"Active instrument" = whatever the chromatic keys currently produce = the last-visited of SYNTH / DRUM. A running sequencer drives the active instrument. Recording captures the active instrument's output.

---

## History (text moved out of the live sections)

Moved 2026-10-14, #935 (the whole-system review of 2026-10-13, I2 and I3), verbatim, so the
live sections say only what holds now.

**From § Page keys, the 1-2-3-4 confirm as stage J1 built it (2026-09-18) and as amended
2026-10-12:**

> **The 1-2-3-4 confirm (stage J1).** *Gone from PROJECT 2026-10-12 (§ Menus below): the instrument is always saved, so PROJECT's four act on the turn; SYSTEM › RESET keeps the run.* On USB/FILE › PROJECT the four keys have a
> second meaning: while one of `NEW · LOAD · SAVE · BOUNCE` is armed (by turning
> its encoder right) they are a **confirm run** — pressed in order, they run it,
> and any other key in any position cancels. The key strip says `CONFIRM` on all
> four while it is armed and names the subpages otherwise, so the strip always
> says which meaning is live (DDR-022). Nothing happens on the encoder turn
> itself. This is the gesture DDR-064's `SYSTEM › RESET` asks for, and **stage J2
> built that**: on `USB/FILE › SYSTEM`, turning ENC2 right on the `RESET` row
> arms it and the run is `1 2 3 4` **twice** — the first full run resets the walk
> instead of firing, the key strip keeps saying `CONFIRM` and the dynamic strip
> says `AGAIN`. Moving ENC1 off the row puts it down, as does any wrong key in
> either round.

**From § Patches, SHIFT + SYNTH / DRUM as spec'd 2026-09-11 and amended 2026-10-12:**

> - **SHIFT + SYNTH / DRUM** saves the current slot into the current engine's folder, auto-named adjective-noun (≤12 chars, unique in that folder). The strip flashes `SAVED PATCH BIG-CAT → FM`. *2026-10-12 (D98): it opens the name menu (§ Menus, plant and landmark names) and saves on the commit, so the sound you cared enough to save has a name you chose; the push makes it two motions.*

---

## Session History
- #935 (2026-10-14, autist; the whole-system review of 2026-10-13, I1–I4) — the push commits a menu (D98), nothing outside one (M5's question, `[TBD-Elfy]`); SHIFT+SYNTH's paragraph rewritten for the name menu, #819 not built yet; the 1-2-3-4 confirm keeps RESET's run; the old texts moved to a new § History; P3 has a gauge (the ADS1115)
- [[logs/2026-09-23-15-12]] — cloud routine (#152): the SHIFT+STOP paragraph loses the octave from its list of steps — a move is a performance gesture like the bend (Elfy, 2026-09-23)
- [[logs/2026-03-27]] — created with 5×11 scaffold
- [[logs/2026-04-26]] — encoder colours, SHIFT fine-tune
- [[logs/2026-04-27]] — MEMO removed, matrix 5×10
- [[logs/2026-09-08]] — full rewrite from look-alike photo + 4-round grill; TAPE→TRACK, slot letters, tape keys, HELP modifier, autosave
- [[logs/2026-09-09]] — page-key count corrected 9→8 (TEMPO is left-block); keymap.json named as source for counts/topology only; pitch confirmed uniform 16mm
- [[logs/2026-09-11]] — patches (SHIFT+SYNTH/DRUM promote a slot, SHIFT+slot browses whole sounds); REC as a modifier for grid-locked takes
- [[logs/2026-09-15]] — SHIFT+‹ › on FX = rack tabs; SEQ 1–4 = sequencer-engine soft-keys; LIFT/DROP on rack pages bind macros (DDR-038…041)
- [[logs/2026-09-16]] — session 2: LFO→MOD; TRACK/MIXER banks; bypass on second press; bend on SHIFT+‹ ›; STOP+‹ › jumps; tick stepping; mute/solo; TEMPO nudge; FILE subpages; four LIFT/DROP payload types; no key velocity
- [[logs/2026-09-18]] — stage H2: SEQ entry is the box's REC soft-key, not SHIFT (N1 moved it; the arrow row still said SHIFT)
- [[logs/2026-09-18]] — the 1-2-3-4 confirm on USB/FILE › PROJECT: arm on an encoder, four keys in order, any other key cancels (stage J1)
- [[logs/2026-09-18]] — stage J2: the confirm run **twice** for SYSTEM › RESET (DDR-064), the browser's file ops as built (LIFT straight to `.trash/`, SHIFT+DROP for a new folder, the group undo's precedence) and the battery on HELP-hold
- [[logs/2026-09-18]] — inspection: § Physical layout no longer calls the hotspot file off-axis (rectified and metric since 2026-09-10); § chromatic keys says SEQ entry is the REC soft-key, not SHIFT. DDR-054's SHIFT+STOP undo on SYNTH/DRUM is listed here and **not built** — `v1-spec-package.md` § Inspection 2026-09-18 item 5
- [[logs/2026-10-14-08-46]] — "#819: word lists per kind; SHIFT+SYNTH/DRUM saves on ENC1's push or the red key; a patch's .seq takes an action noun"
- [[logs/2026-10-14-09-52]] — "#820: SYNTH/DRUM FX browser gets a green SAVE THIS RACK row; tool names, ENC push or red key 1 saves to SAVED RACKS"
