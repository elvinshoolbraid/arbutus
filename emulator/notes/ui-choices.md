---
tags: [emulator, TBD, Elvin's call, decisions]
status: active
read_when: you change how a page draws or what a gesture does (record the choice here), or gather the open choices for Elvin's play test
index: emulator/CLAUDE.md
---

# UI choices no spec settled: Elvin's to confirm, and runs' own calls

> Moved verbatim from `emulator/CLAUDE.md` on 2026-10-08 (#440), headings unchanged, so a `§ <name>` citing that file finds its section here. The index of this folder's notes is `emulator/CLAUDE.md` § Read by task.

## Native audio becomes the architecture

### `[TBD, Elvin's call]` — every drawing and gesture decision made here

The spec names the *behaviour*; the look is Elvin's. Each of these is one edit
away from being something else, and the file and line are named so it is one
edit.

| # | Spec | What was done | Where |
|---|---|---|---|
| 1 | §6.1 "the tab-count indicator is required, its form is not specified" | a row of 16×9 boxes under the header: `M`, then `1..n`, then `+` for the empty tab; the current one filled | `pages/rack.py` `_draw_tabs` |
| 2 | DDR-038 "a rack with one engine shows *no* tabs" | no boxes and no count at all — one dim `›` at the right end of the header rule | `pages/rack.py` `render`, `solo` |
| 3 | §6.3 "highlight and copy `[TBD, Elvin's call]`" | a 1 px ring in `palette.GREEN` around every turnable param cell; `Param.highlight`, read only by `ui/frame.py` | `pages/rack.py` `_engine_slots`, `ui/frame.py` |
| 4 | §6.3 strip copy | the spec's own words, verbatim: `TURN A PARAM TO COPY ITS ADDRESS`, `TURN A KNOB TO PASTE` | `pages/rack.py` `COPY_STRIP` |
| 5 | §6.4 "a gesture on a bound slot `[TBD, Elvin's call]` opens the range popup" | **SHIFT + DROP** on the master tab, acting on the macro last turned. SHIFT+DROP was the one unused combination on those two keys | `pages/rack.py` `_open_range` |
| 6 | §6.4 the popup itself | plain: name, `INST.PARAM`, a LO..HI span bar in the macro's encoder colour with a red marker at the live position, `SHIFT+DROP CLOSES` | `pages/rack.py` `_draw_range` |
| 7 | §6.4 "‹ › steps through targets" | ‹ › while the popup is open | `pages/rack.py` `key` |
| 8 | §6.8 CPU-flag copy | **settled by D103** (Elvin, Play date 1, #843): `CPU UNHAPPY: OVERLOAD`, the app's words, as a 3 s flash in the dynamic strip (DDR-031); short form `CPU UNHAPPY` (*run's call, 2026-10-14*: the app's first two words); was `CPU NO HAPPY — EFFECT TOO BIG` | `app/native_audio.py` `XRUN_LONG` |
| 8b | the tape's own alarms (C1 step 7, `tape-streaming-design.md` § 3.7) | **Elvin's words, § 7 answered 2026-09-23**: `HARD DRIVE SLOW` (not the note's `TAPE LATE`) in the same slot, on the same 3-in-2-s edge, because the host sends the flag and one alarm rule covers both; and **two** words for a take that ended — `CARD STALLED` for a card that stopped answering, `TAPE FULL` for a card with no room and for the medium's own end. A reason this build does not know reads as `TAPE FULL`, the half that never tells a player to try again. `tape_full` is said **every** time: it is a take that ended, not a rate | `app/native_audio.py` `LATE_LONG`, `TAPE_FULL_WORDS` |
| 8c | **a ninth row on FILE › SYSTEM** — `TAPE BUFFER`, read-only, after `DATE / TIME` | § FILE › SYSTEM's sketch names eight rows and design § 3.7 says `tape.pool` is "for the FILE › SYSTEM page and the bench", so the row is a deviation from the sketch's list and not from the design. It reads `<resident>/<ranges>` with a note that is the write backlog if there is one, else the late-block count, else `NOTHING HAS BEEN READ LATE`; with no window (`ranges` 0, which is every host until the medium moves onto storage, and an older host with no `pool` at all) it says `ALL IN RAM`. A row and not a meter: the number a player acts on is the flag in the strip, and this is where it can be read afterwards | `pages/file.py` `_tape_cells` |
| 9 | not in the spec | which tab the FX page opens on: one engine → that engine, more than one → master, none → the empty tab. Re-settles when a different rack loads, keeps your place otherwise (DDR-028) | `pages/rack.py` `settle` |
| 10 | not in the spec | the ENGINE scope. The UI no longer sees samples, so it draws a **peak history** from the `meters` push instead of a waveform | `app/app.py` `_native_meters` |
| 11 | not in the spec | an unbound macro slot renders `—` like any empty slot, and becomes turnable only in paste mode | `pages/rack.py` `_macro_slots` |
| 12 | §3.6 "label length `[TBD, Elvin's call]`" | the descriptor's labels go in the key strip unclipped; `REC REST UNDO CLEAR` fit an 80 px cell | `pages/seq.py` `strip_labels` |
| 13 | DDR-053 "SUS/REL greyed" | named and **locked** — drawn dim, value `—`, the encoder does nothing. A blank cell reads as "not built yet"; a named dead cell reads as "a drum voice has no sustain" | `app/params.py` `Param.locked`, `pages/drum.py` `_build_static_params` |
| 14 | DDR-053 "drawn visibly unlike the synth ADSR `[TBD, Elvin's call]`" | one filled decay from the attack peak to zero, scaled across the AD's **whole range** so a short hit stays visibly short — not the synth's four-segment line across the box | `pages/drum.py` `_render_ad` |
| 15 | §15.3 "LIFT on a pad then DROP on another" | **held**, not latched: hold LIFT and hit a pad, hold DROP and hit another. The gesture eats the note, so a pad you are addressing does not fire | `pages/drum.py` `on_note_key` |
| 16 | not in the spec | DRUM's ATK/DEC slots reach **2 s / 8 s** where SYNTH's stop at 2 s. The host hands DRUM `a 0 / d 4` and clamps neither, so the ceiling is a UI choice; 8 s puts the default at mid-bar | `pages/drum.py` `_build_static_params` |
| 17 | modulation-spec §9 ghost glyph `[TBD, Elvin's call]` | a one-pixel off-white vertical rule over the coloured knob bar; the bar and numeric label remain the authored knob value | `app/params.py` `Param.ghost`, `ui/frame.py` `_draw_param_cell` |
| 18 | modulation-spec §10 output-copy drawing `[TBD, Elvin's call]` | the four bottom cells become the descriptor's output names, ringed green; the main-area output list writes bipolar as `+/-` because the pixel font has no `±` | `pages/rack.py` `_modulator_slots`, `_draw_engine` |
| 19 | modulation-spec §6.3 route range popup | the FX popup's plain framed layout reused for one route; SHIFT+DROP opens/closes, ‹ › step routes, live target value is an off-white rule. **Deviation:** LO/HI edits do not park the target at the bound; `meters.mod` is read-only and the protocol has no non-destructive target-audition message | `pages/rack.py` `_draw_range` |
| 20 | modulation-spec §2.2 route move | lifting a route removes it immediately (undropped = delete); DROP then turning a row reinserts the complete route at that row | `pages/rack.py` `_lift_route`, `_paste_route` |

## E2 — the tape UI

### `[TBD, Elvin's call]` — E2's drawing and gesture choices

| # | Spec | What was done | Where |
|---|---|---|---|
| 21 | DDR-060 "a bank glyph appears once bank B holds a clip `[TBD, Elvin's call]`" | one letter, `A` or `B`, in the ruler's gutter under lane 4 — the only column on TRACK that is never tape — in the selected track's encoder colour, drawn once bank B holds a clip or you are standing in it | `pages/track.py` `_render_bank_glyph` |
| 22 | §2.3 "sub-bar ticks are unlabelled but taller on the beat" | 6 px on the bar, 5 px on the beat, 3 px between; whole-bar rungs are 4 px and carry a thinned bar number | `pages/track.py` `_render_ruler` |
| 23 | §2.3 "1 s → 100 ms → 10 ms → 1 ms → samples once the window is under ~250 ms" | **Deviation:** the time rungs are **1-2-5 per decade** (which contains the spec's four) down to one sample, and they take over where the *musical* rungs run out (1/32 bar, ~62 ms at 120 BPM) rather than at a fixed 250 ms. Bare decades step by 10 and a fixed crossover leaves a window where neither ladder can keep 8-16 ticks, which is the rule DDR-058 actually states. One seam remains at ~0.35 s, where the 2.5 step gives ~7 ticks | `app/tape.py` `TIME_RUNGS`, `tick_ladder` |
| 24 | §2.1 "MERGE DROP: strip hints `SHIFT+DROP = MERGE` while such a buffer is held, if it fits" | the ticker is posted whenever the buffer holds more than one clip, long form `SHIFT+DROP = MERGE`, short `SH+DROP=MRG` | `pages/track.py` `tickers` |
| 25 | §2.1 "MERGE DROP: a LIFT ALL buffer mixed down onto the selected track" | a single-clip buffer SHIFT+DROPs as an ordinary DROP — a mixdown of one clip is that clip | `app/tape.py` `do_merge_drop` |
| 26 | DDR-030 "DROP on SYNTH/DRUM › ENGINE" | on DRUM the key is already the pad-copy gesture (§15.3), so a **pad copy in progress wins** (LIFT+pad came first) and otherwise a held tape buffer makes DROP the tape → pad gesture | `pages/drum.py` `on_key` |
| 27 | not in the spec | the drop does **not** consume the lift buffer. Lift-without-drop is the tape's delete (DDR-013), so taking a copy to an instrument must not be what deletes a take | `pages/synth.py` `drop_tape_buffer` |
| 28 | §15.3 has no per-pad *file* message | DRUM's drop edits the **kit document** the host reports (one `file` changed) and sends it back as `load_kit` — the document is the save, so that is the whole edit | `app/native_engine.py` `set_pad_file` |

## F2 — the mixer UI

### `[TBD, Elvin's call]` — F2's drawing and gesture choices

| # | Spec | What was done | Where |
|---|---|---|---|
| 29 | DDR-061 "SHIFT+1-4 mutes, holding 1-4 solos" vs. the sketch's `1`-`4` = subpage | tap = subpage, hold (0.28 s) = solo, SHIFT = mute. The three are the same four keys because DDR-001 already means hold-performs / tap-commits | `pages/mixer.py` `HOLD_S`, `tick`, `on_key` |
| 30 | **Deviation.** DDR-061 says SHIFT+1-4 mutes "on MIXER" | on the **FX subpage** SHIFT+3 is the effect browser instead, as it is on SYNTH › FX — the master rack has no other door, and mute of track 3 is one key press away on MIX. SHIFT+1/2/4 still mute there | `pages/mixer.py` `on_key` |
| 31 | § MIXER "the shown bank's four drawn bright" | out-of-bank and host-silenced columns draw in `palette.DIM`; no derived shades, so the rule that every colour comes from `ui/palette.py` holds | `pages/mixer.py` `_col_color` |
| 32 | § MIXER "muted columns dim, soloed marked" | an `M` in `ACCENT_BROWN` or an `S` in `GREEN` at the column's top-left, with the bank letter moved to the right edge so it cannot collide with track 1's badge | `pages/mixer.py` `_render_mix` |
| 33 | DDR-060 / § MIXER "SHIFT+‹ › = bank" | the bank is `tape.selected // 4`, so moving it on MIXER moves the selection with it and TRACK stays in step. One fact, one home — the same call E2 made | `pages/mixer.py` `_step_bank` |
| 34 | § MIXER "8 narrow columns aligned with MIX, the selected one highlighted, its curve drawn" | the curve fills the panel and the eight cells sit under it on MIX's 40 px pitch; the selected cell is inverted and a track whose EQ is not flat carries a rule in its encoder colour | `pages/mixer.py` `_render_eq` |
| 35 | DDR-062 "HP/LP are per-track filter cutoffs" | at 20 Hz / 20 kHz the cell reads `OFF`, not a frequency — flat is bit-exact bypass in the host and a number there would read as a filter doing nothing | `pages/mixer.py` `_eq_slots` |
| 36 | § MIXER "signal-path screen … with sidechain and route arrows `[TBD, Elvin's call]`" | three chained rows of names in `PROTOCOL.md` § The mixer strip order, a stage that is currently bypass drawn dim (with the legend saying so), and the sidechain sources listed as one line rather than drawn as arrows — at 5×7 an arrow between two rows is three pixels of ambiguity | `pages/mixer.py` `_signal_rows`, `_render_signal_path` |
| 37 | not in the spec | MASTER prints `REL 0.12s` and `GR %` but promises nothing about milliseconds: F1's COMP REL coefficient runs once per block, so the labelled time is approximate | `pages/mixer.py` `_render_master` |

## G — the live input

### `[TBD, Elvin's call]` — G's drawing and gesture choices

| # | Spec | What was done | Where |
|---|---|---|---|
| 38 | screen-sketches § SYNTH › ENGINE: "`LIVE INPUT` … main = live waveform + **tuner readout**" | the waveform is `App.scope`, the peak history the ENGINE scope already draws (the UI sees no samples, `[TBD, Elvin's call]` #10), with the source name top-left and the current peak in dB at the right. **Deviation: there is no tuner.** §8.2 hands up no pitch estimate, and a detector written in `emulator/` would be DSP in the wrong folder — it belongs in the box, and the box has no way to report it | `pages/synth.py` `_draw_live_input` |
| 39 | `tape-sampling-workflow.md` §1: "Params: THRESH · LENGTH · GAIN · MONITOR … Slots 4–7 stay empty so SHIFT fine-tunes each" | SOURCE takes **slot 4**, so SHIFT+ENC1 is SOURCE rather than fine THRESH; 5–7 stay empty and SHIFT still fine-tunes LENGTH and GAIN. THRESH is the one knob whose whole range is 0.01–0.50 and reads to two places already | `pages/input.py` `slots` |
| 40 | § MIXER signal path (F2's `_signal_rows`) | the input is a **fourth row, drawn first**: `INPUT › <SOURCE> › LIVE INPUT › MONITOR`, each stage bright only when it is doing something. It is not a chain like the other three — MONITOR is a second door, not a later stage — but a diagram of where the input can go is the question the row answers | `pages/mixer.py` `_signal_rows` |
| 41 | not in the spec | the engine menu gained a folder at index **1** (`fm · live-input · noise · osc · sampler`), so the three feature scripts that scroll it by `delta` moved with it. Any box added to `audio/library/synth/` does the same | `features/scripts/*.yaml` |

`tests/test_live_input.py` (12) drives all of it against the real offline host;
the `live-input` verify feature (7 shots) is the user path end to end.
**332 tests, 27/27 verify features** as of stage G.

## H2 — patterns as assets, SEQ and TEMPO

### `[TBD, Elvin's call]` — H2's drawing and gesture choices

| # | Spec | What was done | Where |
|---|---|---|---|
| 42 | screen-sketches § SEQ: `DIV · SWING · ACTIVE · HOLD / — — — DIR` | the row is the **box's descriptor**, all eight cells, and ACTIVE is a header badge with the SEQ key as its only gesture. The sketch's row is `arbutus/endless` v0.1's; §15.14 replaced it per box | `app/native_sequencer.py` `slots`, `pages/seq.py` `render` |
| 43 | §15.4 "tied notes = hold keys + ›" | a tie draws as a 12 px rule in the note row where a note name would go — a tie is a *rule* about the step before it, not a pitch, and at 5×7 a glyph for it reads as one more note | `pages/seq.py` `_draw_notes` |
| 44 | control-inventory § slot keys: "SEQ needs no save gesture of its own" | **promoting a patch writes the pattern.** SHIFT+SYNTH/DRUM writes the sequencer's steps to `seq/<box>/<slug>.seq` first, then names that file in the patch's `seq.assets.pattern`. Without it a promoted patch would name the scratch file, which does not survive the session | `pages/synth.py` `on_shift_page_key`, `app/native_sequencer.py` `promote_pattern` |
| 45 | screen-sketches § TEMPO: `COUNT-IN (0/1/2 bars)` | a **delayed punch**, not a pre-roll: the transport rolls from where you asked and the take starts N bars later. A pre-roll has to locate backwards and there is nowhere to go at the head of the tape, which is when a count is wanted most. REC during the count cancels; REC + a note key ignores it (that gesture is already "start here, now") | `app/tape.py` `play`, `rec`, `poll_count_in` |
| 46 | screen-sketches § TEMPO: `SYNC (free/midi)` | **Deviation:** there is no MIDI clock source in the instrument yet — FILE › MIDI's `CLOCK IN` is stage J2 — so `midi` **locks** BPM, BPM FINE, the `‹ ›` nudge and tap tempo (named, dim, inert: DDR-053's shape) and does nothing else. The alternative was a switch that changes nothing, which reads as broken | `pages/tempo.py` `external_sync`, `slots` |
| 47 | screen-sketches § TEMPO: "`LINK` state shown" | one line across the top of the main area: `LINK ON/OFF` at the left, `SYNC FREE/MIDI` at the right, `COUNT-IN` in red in the middle while one is running; the beat dots go green instead of red during the count | `pages/tempo.py` `_state_line`, `_beat_dots` |
| 48 | not in the spec | ENC1 is whole BPM and **rounds as it turns** (`StepType.DISCRETE`), so a tenth set on BPM FINE or with SHIFT+`‹ ›` is gone once the coarse knob moves. Two cells, one number, and the coarse one is allowed to be coarse | `pages/tempo.py` `_build_params` |

## J1 — project files

### `[TBD, Elvin's call]` — J1's drawing and gesture choices

| # | Spec | What was done | Where |
|---|---|---|---|
| 49 | screen-sketches § FILE: "`NEW · LOAD · SAVE · BOUNCE` as ENUM-style actions on ENC1–4 (turn to the item…)" | four **arm switches**, one per encoder, one-of-four: turning right cocks that action, turning left or turning another puts it down. Nothing happens on a turn — three of the four can lose a session | `pages/file.py` `_arm`, `on_encoder` |
| 50 | screen-sketches § FILE: "the strip asks `1 2 3 4 TO CONFIRM` for NEW" | the confirm is on **all four** actions, not only NEW (the J1 brief's reading), and the sentence goes in the **dynamic strip** (DDR-031), where a sentence goes. The key strip's four cells read `CONFIRM` and the highlight walks 1 → 4 as they land, so you can see how far through the run you are. A wrong key, in any position, cancels | `pages/file.py` `strip_labels`, `_confirm_press` |
| 51 | not in the spec | the main area's red line names the **consequence**, not the sentence the strip already carries: `NEW ERASES EVERYTHING`, `SAVE AS PROJ_0002`, `LOAD PROJ_0001`, `BOUNCE 0:32 · MASTER FX ON` | `pages/file.py` `_armed_line` |
| 52 | screen-sketches § FILE: "LOAD opens the two-pane browser (folder = date)" | **Deviation:** it highlights, it does not apply live. Every other selector in the instrument applies on the detent (DDR-030); this one would re-read eight tape media per click of the encoder, so the same `1 2 3 4` commits, with the list still up. The browser is a real `MenuPage` (so `pick:` works in a feature script) with the parent's strip and ticker on it | `pages/file.py` `_ProjectBrowser`, `on_menu_number` |
| 53 | screen-sketches § FILE: "Main: project name, date, tape length used, storage free" | `UNSAVED` until a save or a load names it; `TAPE USED` is the clips' total length as `M:SS`; `FREE` is `statvfs` on the library root, `—` where there is none. `MASTER FX` is shown as a third row because it is a *setting*, not an action, and the SHIFT row is only visible while SHIFT is held | `pages/file.py` `render` |
| 54 | DDR-020 auto-naming `PROJ_0037` | the counter is the largest number already used **anywhere in the tree**, not per date: the browser shows the name and not the folder above it, so two days must not both hold a `PROJ_0001`. `app/patch.py` `slug` strips the underscore, so the promoted pattern beside it is `proj0001.seq` | `app/project_file.py` `next_name` |
| 55 | not in the spec | `1`-`4` do two jobs on this page: the subpages until something is armed, the confirm while it is. The strip says which — the same DDR-001/DDR-022 argument MIXER's tap-vs-hold makes | `pages/file.py` `on_key` |

## J2 — the browser, the system pages, the battery (the last stage)

### `[TBD, Elvin's call]` — J2's drawing and gesture choices

| # | Spec | What was done | Where |
|---|---|---|---|
| 56 | screen-sketches § Menus: "DROP at folder level makes `FOLDER-NN`" | **SHIFT+DROP**, making `FOLDER-NN` inside the highlighted folder and dropping into it. There is no "folder level" in a two-pane browser — both panes are always showing — and SHIFT+DROP is already the "make something new out of the buffer" variant on TRACK (E2's merge drop). A plain DROP is the move | `app/menu.py` `on_key` |
| 57 | DDR-055 "the strip shows the lifted name" | the tape's own sentence about a different kind of thing: `LIFTED: <name>` at `TICKER_LIFT`, from `App.tickers` rather than from the browser, so it survives closing one browser and opening another — which is what makes "lift here, drop there" work at all | `app/app.py` `tickers`, `app/library_ops.FileOps.ticker` |
| 58 | DDR-055 "consecutive lifts form one undo group (SHIFT+STOP puts them all back)" | SHIFT+STOP is the tape's undo everywhere else, so the browser's group only outranks it **while a browser is open or something is still lifted**. Otherwise SHIFT+STOP would stop meaning "undo the edit I just made on this page" | `app/app.py` `_transport` |
| 59 | screen-sketches § FILE › MIDI: `IN CH · OUT CH · CLOCK IN · CLOCK OUT` | **Deviation:** there is no MIDI output on this host, so `OUT CH` and `CLOCK OUT` are named, dim and locked with the reason on the page. Sending a 24-ppqn clock out of a 60 fps UI thread would be a musical lie; it belongs to a device runtime with a real timer | `pages/file.py` `_build_params`, `_render_midi` |
| 60 | screen-sketches § FILE › USB: `MODE (storage / midi) · CHARGE · PAIR (BLE, for the app)` | MODE is live and does the part of DDR-021 that matters — `STORAGE` **force-saves first**, no prompt, and DDR-020's auto-naming is why that costs no naming UI. **Deviation:** `CHARGE` and `PAIR` are locked. A MacBook cannot be a USB device, its charger is not this program's, and BLE pairing is the device OS's (`companion-app-spec.md`) | `pages/file.py` `_set_usb_mode`, `_render_usb` |
| 61 | DDR-064: "a `.fw` or `.arbox` in the storage root is applied automatically" | **not built, and said so on the page.** Firmware is A/B slots, verification and a rollback on a failed boot — a device-OS feature with no device. The USB subpage carries one line naming it | `pages/file.py` `FW_LINE` |
| 62 | screen-sketches § FILE › SYSTEM: "a scrolling list (ENC1 selects, ENC2 changes)" | exactly that: two Params whose *formatters* are the selected row's label and value, so the eight-cell param bar describes a list of any length. ENC3/ENC4 stay empty and the SHIFT row with them | `pages/file.py` `_system_slots`, `_render_system` |
| 63 | § FILE › SYSTEM lists `KEY VEL (fixed / motion — hidden without an IMU)` | **conditional since #116, not hidden for ever.** It was hidden unconditionally, so a P3 with a working sensor said it had none; the host now reports `motion_sensor` on `hello` and in every state document and `_system_rows` is **keyed rather than numbered** (`SYS_VEL`… are strings and the cursor is a key), because the list is not seven long any more. Without a sensor nothing moved: the same seven rows and the same `KEY VEL HIDDEN: NO IMU FITTED` under MIDI VEL CURVE, because a row that is absent explains nothing. **With** one the row appears between MIDI VEL CURVE and POWER-OFF, locked at `FIXED` with `MOTION VELOCITY NOT BUILT` — DDR-048's inference exists in no code (#117) and a settable option that changes nothing is worse than a named one that says so | `pages/file.py` `_system_rows`, `_row_index`, `NO_IMU`, `KEY_VEL_UNBUILT` |
| 63b | modulation-spec § 3.2: "Absent IMU → outputs 0, page says `NO SENSOR`" | drawn nowhere until #116. The MOD rack's engine tab heads `arbutus/motion`'s four outputs with `NO SENSOR` and dims them when the host reports no sensor. It is the *page's* job and not the box's — the box already reads `motion[6]` as zero and has no way to know why | `pages/rack.py` `ModRackView.outputs_heading`, `_draw_engine` |
| 63c | modulation-spec § 3.2: `arbutus/keys` is "always present as tab **K** on the master row and not counted in the four" | **built nowhere until #68.** The host resolved `keys.VEL` and `keys.NOTE` (`audio/host/rack.cpp` `resolve_route`) and the ABI carried `set_voice_mod`, but the MOD rack's tab row stopped at the empty tab, so no route from the player's own keys could be made on the device — a built capability with no door. `K` is now the last box in that row. `[TBD, Elvin's call]` in it: the box is the **same box in the same row**, labelled `K`, set off by one wider gap (§ 3.2 says it is not one of the four, and five equal boxes would say it was); SHIFT+› reaches it from the last tab and SHIFT+‹ comes back; its page names the two outputs exactly as a modulator tab names its own and reads `HOST SOURCE - NO PARAMS`, because § 3.2 gives `keys` no params and an empty bottom row otherwise reads as a page that failed to load. It has no bypass (nothing of the player's to bypass) and holds no engine, so SHIFT+4 on it does what SHIFT+4 on the master tab does — it opens on tab 1 (§ 2.1) | `pages/rack.py` `MOD_KEYS`, `KEYS_OUTPUTS`, `ModRackView.tab_outputs`, `_keys_slots`, `_draw_keys`, `_draw_tabs` |
| 64 | § FILE › SYSTEM lists `POWER-OFF` among rows ENC2 changes | read as an **idle timeout** (`OFF / 5 / 10 / 30 / 60 MIN`), not a button: it is in a list of settings and the sketch says ENC2 changes the selected one. Stored and honoured by the device runtime; this process does not sleep a laptop. **Honoured since #176 and not before** — it was stored and drawn here and read by nothing anywhere, so a player who set a 10-minute auto-off got nothing, silently. The *rule* is `App.idle_power_off_due` (device-os-spec § 7.2 row 3, § 7.4.1) and lives here because every fact it is made of but one is this App's: the idle clock (`App.note_input`, moved by a key or an encoder and by **nothing else** — not a render, not a sequencer step playing to nobody), the transport rolling, a take recording, a count-in. The exception is "no client on the network sockets", which is another process's listener and arrives as an argument. **Nothing here powers anything off**: `arbutus_os/main.py`'s `PowerPump` is what sends `poweroff`, and on a laptop `app.powering_off` is `None` for ever | `app/app.py` `note_input`, `idle_seconds`, `idle_power_off_due`, `powering_off`; `pages/file.py` `_system_rows` |
| 65 | § FILE › SYSTEM lists `DATE / TIME`, `BRIGHTNESS`, `WIFI (status)`, `FORGET PHONES` | `DATE / TIME` is the host clock, read-only (this program does not set a Mac's clock). `BRIGHTNESS` is stored but there is no backlight here, and dimming the framebuffer would change every screenshot. `WIFI` and `FORGET PHONES` are the device OS's and are named, dim and inert | same |
| 66 | DDR-064 "SYSTEM › RESET asks 1-2-3-4 and then again" | J1's confirm with a **round counter** on it: `FilePage._rounds` is 2 for RESET and 1 for everything else, the first full run resets the walk instead of firing, and the strip keeps saying `CONFIRM` while the ticker says `AGAIN`. Moving ENC1 off the row puts the gun down | `pages/file.py` `_arm`, `_confirm_press` |
| 67 | DDR-064 does not say what RESET erases | the instrument back to boot (`project_file.new`) **and** the settings back to defaults. It deliberately does **not** empty the library: a settings menu that deletes a player's samples is a different and much worse decision, and nothing in the spec asks for one | `pages/file.py` `_run` |
| 68 | § FILE › SYSTEM: "Battery % shows on HELP-hold; a `BATT 12%` ticker appears under 15 %" | the overlay's **last line, always**, on every page and both overlay kinds — HELP-hold is the one gesture available everywhere, which is why the spec put the battery there. `app/battery.py` reads `pmset` on macOS and `/sys/class/power_supply` on Linux, caches for 30 s, and never raises | `pages/help.py` `overlay_lines`, `app/battery.py` |
| 69 | the spec assumes a device that can measure its own pack | **there is no battery telemetry anywhere yet.** `docs/specs/io-map.md` assigns no pin to a fuel gauge — the P3 sheet has an IP2326 charger and three BMS boards and nothing that answers "how full". So the device reports `NO TELEMETRY` and the *emulator* reports the laptop's battery, which is a real number about the machine it is running on. A made-up percentage is the one lie a player would act on | `app/battery.py` |
| 70 | not in the spec | `ARB_NO_BATTERY` forces the no-telemetry state, and `tools/verify.py` sets it. A percentage is the one thing on screen that is different on every machine and every hour; without this a promoted baseline would be a snapshot of one afternoon's charge | `app/battery.py`, `tools/verify.py` |

## Audit Q1 — 2026-09-18 (#31)

### `[TBD, Elvin's call]` — choices the audit found unrecorded

| # | Spec | What was done | Where |
|---|---|---|---|
| 71 | `software-stack-v1.md` §8.2 sizes a track at 8 min; workflow §2 said "2 min in the Python demo" | the emulator starts the host with **`--tape-seconds 120`** and its timeline is **120 s** — the Python demo's number, kept when the audio went native. The device's default is 480 s. Two constants to change, and the ruler at full zoom-out follows | `app/native_audio.py` `NativeAudio.__init__` `tape_seconds`, `app/tape.py` `TRACK_SECONDS` |
| 72 | `design-decisions.md` § H3 (FILTER encoder topology) is open | the FILTER hold is **LP only** from the UI: `filter_mode` is initialised `"LP"` and nothing sets `"HP"`, though the host takes both (`tape_filter`, `tape_edit`). The hold's param cell reads `FILTER 2.1k` without the mode; the ticker says `FILTER LP 2.1k` | `app/tape.py` `Tape.__init__`, `pages/track.py` `slots` |
| 73 | no document names a mixer default; `PROTOCOL.md` says the host's is unity | the UI pushes **track levels 0.8 and master 0.8** at boot and on FILE › NEW, so the host never runs at its own unity default under the emulator | `app/mixer.py` `Mixer.__init__` |

---

## D5b — the `.fw` line on FILE › USB (2026-09-20, #103)

### `[TBD, Elvin's call]` — D5b's one gesture choice

**`DISMISS` is behind the same `1 2 3 4` as `RESTART` and `INSTALL`.** § 10.5 says the
rollback line stands "until dismissed" and does not say what dismisses it. The two
alternatives were a bare encoder turn (one gesture, but then the message a player has not
read is one accidental detent from gone, which is the thing "until dismissed" exists to
prevent) and a second, lighter mechanism on a page that has exactly one. Clearing a notice
costs five presses instead of one; nothing is lost if it is wrong. Say the word and it
becomes a turn.

**What this ticket did not build**, because it is #97's plumbing: `fw.sock` *pushes* a
status document when a runtime scan finds something, and `DeviceServices` keeps pushes on
the `DeviceSocket` without surfacing them. A `.fw` the phone `PUT`s with nobody touching a
key therefore reaches the screen on the next 2 s status poll rather than at once — late,
not wrong. `dismiss` and `apply` answer with a document or a message the pump takes
immediately, so nothing a finger does waits on the poll. **#97 built that plumbing on
2026-09-20** (`DeviceServices.take_pushes`); `FwPump` does not call it yet, so the `.fw`
line is still on the poll and the sentence above still describes it. Making it prompt is
now two lines in `FwPump.pump`, not a mechanism.


## S14 — entering a stepmod pattern (2026-09-23, #60)

### `[TBD, Elvin's call]` — S14's drawing and gesture choices

| Thing | Choice |
|---|---|
| where STEP and VALUE sit | slots 5 and 6, the first two the box left empty — the ticket's own "one of the empty slots (5–7)". They are on the SHIFT row, which is where slots 4–7 are; the SHIFT *hold* the old gesture wanted was a modal value-entry, not this |
| VALUE's detent | 0.05, so ±1 is a 40-detent sweep. Coarser than the MOD master's DEPTH (0.02) on purpose: a step value is aimed at, not dialled to taste, and SHIFT-fine still gives 0.005 |
| VALUE's readout | `%+.2f` — the sign is the point of a bipolar step |
| the step bar | sixteen cells, bipolar about a centre rule, each cell outlined `DIM` and the selected one outlined `OFFWHITE`; a value draws a bar up or down from the middle, so a step at 0 draws nothing |
| where the bar goes | right of the outputs list on the engine tab, not under it: a box with four outputs would have pushed it off the bottom |
| a refused pattern | the entered list is dropped and the strip says `STEP PATTERN REFUSED`, so the page goes back to showing what the box is really playing |

## #69 — TRACK's ENC3 is the song tempo while LINK is on (2026-09-24)

### `[TBD, Elvin's call]` — #69's one choice

| # | Spec | What was done | Where |
|---|---|---|---|
| 82 | #69: "what ENC3's readout shows while LINK is on — the BPM, or the resulting speed ratio `[TBD, Elvin's call]`" | the **BPM**, whole, `{:.0f}`, the same format TEMPO's ENC1 wears. The simplest rule is that a cell reads what its knob moves; the speed ratio is derivable and is not what the turn sets. The aesthetic pass (E1, #9) can settle it | `pages/track.py` `p_bpm`, `slots` |

**The proof** (`tests/test_tape_ui.py`, four new tests, all through the input
seam and a real offline host). LINK on: ENC3 +10 makes `app.bpm` 130, the host's
`state.tape.link.factor` 130/`base_bpm`, and `state.tape.speed` still 1.0 — the
tape moved, and it moved as the link factor rather than as a varispeed. LINK off:
the same turn moves the varispeed to ×1.1 and BPM holds. A third turns ENC3 then
TEMPO's ENC1 and reads both cells, which is the bug stated as one sentence: the
two tempos used to differ. Their **negative control** (#56) is the run against
the unfixed page: three of the four fail, and the one that passes is the LINK-off
test, which is the half that was already built.

**One older test had to move, and it is the finding worth keeping.**
`test_native_tape.TestTapeKeys.test_speed_and_rev_reach_the_host` turned ENC3 and
asserted `state.tape.speed < 1.0`; LINK is on by default, so after the fix that
knob no longer touched the varispeed and the gate caught it. It now turns LINK
off through TEMPO's ENC4 first and says why. Nothing about the varispeed path
changed -- the test's own subject is intact -- but a test that asserted the old
rule is the plainest evidence the rule really moved, and the precondition is now
written where the next reader of that test will meet it.

**661 emulator tests** (657 before). No pixel changed, no baseline promoted;
`ui/palette.py` and `ui/font.py` untouched.

## #297 — the tape lives in the open project (C1 step 3b, the UI half, 2026-10-03)

### `[TBD, Elvin's call]` — #297's one gesture

| # | Spec | What was done | Where |
|---|---|---|---|
| 83 | #297 item 4: "which gesture is the building session's call — a SHIFT+SAVE or a second row" | **SHIFT+ENC3** arms `SAVE COPY` on the SAVE cell (lit while armed), confirmed with the same `1 2 3 4`; the red line reads `SAVE A COPY AS PROJ_000N`. SHIFT+ENC4 is already BOUNCE's MASTER FX, so a SHIFT turn on an action's encoder is that action's variant. SAVE's own line now reads `SAVE PROJ_0003` when a project is open and `SAVE AS PROJ_0004` when the first SAVE will name one. Run's call, 2026-10-03; on the play-test checklist | `pages/file.py` `SAVE_COPY`, `on_encoder`, `_armed_line` |

## #334 — the site's and the app's colours, light and dark (2026-10-03)

### Run's calls, 2026-10-03

| # | What | Why |
|---|---|---|
| 1 | Two tables + `set_mode()` rebinding module names | every draw site already reads `palette.X` at call time, so no page changed beyond the three renames, and the live editor keeps working |
| 2 | `OFFWHITE`→`INK`, `BLACK`→`PANEL`, the four tint names→`GROUND` | a name that says its colour lies in the other mode |
| 3 | Light `PANEL` = `GROUND` (cream); dark `PANEL` = `darkCard` | the panel is the app's card in both modes; in dark it buys the knob colours and red the param bar's contrast (6.0 and 4.7 against 4.5 and 3.6 on brown) |
| 4 | Dark knob colours lifted to 4.5:1 on brown, hue kept (`#b2cbec #a0d5a5 #f7b9b9 #f5bd7b`), not the app's `knobDarkHexes` | the app's are 3.1–3.9 on the device's brown ground; device and app now differ in dark mode, see the open question |
| 5 | Record red light `#e0201b` (4.11), dark `#ff9480` (3.55 / 4.73 on the panel); the app's `warning` the same light value | "bright red", clearly brighter than ENC3; dark kept coral so it is not ENC3's pink |
| 6 | Light `ACCENT_BROWN` = ink-soft `#7a4a20`; dark `#daaf76` | a lighter tan in light collides with ENC4's orange; the top row's hollow dot and the SHIFT highlight still mark it |
| 7 | Dark `GREEN` = the app's `darkGreenInk` (3.11 on brown) | #334 names it; lifting it to 4.5 loses the green |
| 8 | `DIM` ~2.6:1, `RULE` ~2.3:1, below § 3.4's 3:1 for rules | dim is meant to recede, and at 3:1 the param grid's lines outweigh its text |
| 9 | The setting is `system.appearance` in `settings.json`, not a new file | it is the device's, not the song's: J2's settings document is where the device remembers things |
| 10 | The host holds the last mode and pushes a change to **every** client | the UI is not a `feed` subscriber; a UI that restarts can ask |
| 11 | `ARB_APPEARANCE` pins a run and writes nothing | screenshots must not depend on what a player last pressed |
| 12 | Dark shots: contact sheet only, no second tracked set | 300 more PNGs to move on every UI change; the light set is the regression record, the sheet is for Elvin's eye |
| 13 | `V` toggles and **remembers**; `--appearance` is this run only | `V` stands in for the phone, which is remembered; a flag is for screenshots |

**Left open.** The recovery screen (`arbutus_os/recovery.py`) draws in whatever
mode the module starts in, which is light: it builds no `App` and reads no
setting. The app's dark knob shades and the screen's differ (call 4); one of
them should move so a knob means one colour on both.

## #821 — moving projects: held keys, low storage (2026-10-13)

### Run's calls, 2026-10-13

- **A key other than 1 or 2 answers the low-storage question LATER and then plays.** The
  question must never cost a note; FILE's old arming had "any other key cancels". A player
  who wanted EMPTY presses 1.
- **`OFFLOADING TO PHONE...`, three dots**: the spec writes `…`, the font has no glyph for it
  and `ui/font.py` is not a run's to edit.
- **A trash with no project folder in it asks `TRASH · <size> · EMPTY TRASH?`** (the spec's
  `<n> TRASHED PROJECTS` would read `0 TRASHED PROJECTS` over a trash of patches).
- **The two jobs the spec gives no words**: `RESTORING FROM PHONE...` and `DUPLICATING
  PROJECT · HOLD TIGHT`, in the shape of the two it does.
- **No file job starts while the tape rolls** (the device answers `409 playing`), not only
  the open project's offload and a bounce: a job stops the audio, and a take cut by a phone
  across the room is the worse surprise.
- **The question is drawn in HELP's overlay shape**, its first line red, answers on key
  cells 1 and 2. Shots: screenshots (private).

## #818 — menus: turn to act, the name menu, the red key, the push (2026-10-13)

### Run's calls, 2026-10-13

- **`›` for `▸`** on a turn-to-act cell (`NEW ›`): the font has no `▸` and `ui/font.py` is
  not a run's to edit; `›` is the nearest glyph it draws. Elvin may draw `▸` (ui-aesthetic § 4).
- **The red key is the one under the turned cell**: `1 PICK` for NEW, `2 OPEN` for LOAD,
  `3 PICK` for RENAME. The spec names the red key but not which; under the knob just turned
  keeps the hand on one column.
- **A cancelling number key only cancels**: pressing `4` to back out of NEW does not also jump
  to SYSTEM (over any other menu, a number key closes it and selects that subpage).
- **In the name menu any encoder moves the highlight**, and the last one turned is the one
  whose push commits; another encoder's push cancels, as "any other page key" does.
- **LIFT and DROP still work in the LOAD list** (DDR-055's file manager); every other key
  closes it.
- **The words**: forty adjectives and forty places, short enough for the FILE page's large
  line (at most 15 letters with the space). The order they come in is seeded by the names on
  the card, so the same card offers the same names: a screenshot and a test can name them.
  `NEW` is not an adjective (`NEW BRIDGE` beside the NEW action read badly).
- **The folder is the name, space and all** (`2026-10-13/WEST HARBOUR`): the phone's file
  server already takes a space (`httpd.py` unquotes the id).
- **Each cell's value line says what the turn opens**: `PICK NAME`, `PROJECTS`, `PICK NAME`,
  `TO WAV`.
- **The red key's label is the panel's colour** (cream in light, the darker brown in dark):
  4.73:1 on the dark red where the inverted text was 3.55 (the grader's finding).
- **Opening NEW, LOAD or RENAME clears the strip's last flash**, so the menu's sentence is
  the only one there (the grader saw `PROJ_0001 · PICK A NAME`). After an offload the
  `PROJECT OFFLOADED TO PHONE` flash stays: it opens the menu itself.
- **The laptop's push**: a click on a knob that turns nothing, or `Ctrl`/`Cmd` + `1`–`4`
  (not a bare letter: the free ones are spread over the keyboard).

## #820 — SAVE THIS RACK (2026-10-14)

### Run's calls, 2026-10-14

- **The red key is 1, labelled `SAVE`**: ENC1 is the turn that reaches the row, so its cell is
  the one under it, as SHIFT+SYNTH's name menu (#819). Turning ENC2 moves the name and makes
  ENC2's push the commit; the red key stays 1.
- **2, 3 and 4 on the row act as on any browser** (close it, go to that subpage) and save
  nothing; a page key dismisses the same way. The spec's "any other key cancels" holds: none
  of them saves.
- **The commit closes the browser** and flashes `SAVED RACK <name> → SAVED RACKS`; SHIFT+3
  again finds it under SAVED RACKS, which appears only once it holds a rack.
- **MIXER › FX has no save row** (the ticket names the instrument's browser); it does load a
  saved rack. Whether the master rack saves the same way is Elvin's call after he plays it.

## #632 — a set loop is framed (2026-10-14)

### Run's calls, 2026-10-14

- **Framing a loop zooms out to 2.2 × the farther loop end from the playhead** (the review's
  recommendation, row 7): the view stays centred on the playhead, as every other frame on
  TRACK, so a loop set the usual way (IN, travel, OUT at the playhead) now shows both ends.
  A playhead far outside the loop when LOOP is turned on zooms out until the loop is on
  screen too; it only ever zooms out, once, at the key, never every frame.

## #1180 — the name menu when the words run out (2026-10-15)

### Run's calls, 2026-10-15

- **Past the last free pair, the name menu numbers names** (`QUIET HARBOUR 2`, `WET-FERN-2`):
  every pair again with a number, in the word lists' own order, none already taken, so the
  menu always shows three and a push always commits one. Only a card holding all 1,600
  project names (or every short patch, rack or pattern pair) ever sees one; it was an empty
  menu and a crash on the push. `free_names` is unchanged; the menu tops its own pool up.
- **Names the phone holds are not yet skipped** (D98 says they are): the instrument has no
  list of them, which needs a protocol message from the phone. Split to #1204.

## #1255 — OP1R-VERB's page is op1r's own screen (2026-10-16)

### The exception to the look rules, Elvin's

- **The main zone on OP1R-VERB's tab is a window onto the OP-1 engine's own screen, drawn as
  that engine draws it** (Elvin, 2026-10-15: "the idea is that you're looking through a 160px
  high window into the adapted engine as-is"). Inside that 320 × 160 window, op1r's Kali art
  is anti-aliased and in op1r's own colours on the OP-1's black, in light mode too: the
  no-smoothing and palette-only rules (`ui-aesthetic.md`) do not apply there. Everything around
  the window (the header, the strip, the param cells, the key strip) keeps them, and so does
  every other page and box. The art's colours live in its data (`kali.npz`), never as RGB in
  code; `palette.py` is untouched.

### Run's calls, 2026-10-16

- **The window is the whole main zone**: no `FX OP1R-VERB` header line and no tab boxes on
  that tab (the strip above still says where you are). Bypass shows only on the other tabs.
- **COPY mode (LIFT) draws the usual rack page**, so its copy line is there to read; the
  screen comes back when the mode ends.

