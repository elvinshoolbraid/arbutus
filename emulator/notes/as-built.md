---
tags: [emulator, as-built]
status: active
read_when: never whole: grep it for a ticket, stage or function name to learn why the code is the way it is; append a section after a ticket
index: emulator/CLAUDE.md
---

# As built: the emulator's history, stage by stage and ticket by ticket

> Moved verbatim from `emulator/CLAUDE.md` on 2026-10-08 (#440), headings unchanged, so a `§ <name>` citing that file finds its section here. The index of this folder's notes is `emulator/CLAUDE.md` § Read by task.

## N3a review — MOD rack

The MOD page uses the host document for tabs, routes and bypass. `patch_from_state`
includes a detached MOD document so slot recall and engine-item changes preserve it.
Loading a MOD rack file overlays it onto the current full patch; `load_patch` is
replacement, never a partial update. The master-tab browser edits tab 1 (creates
it when empty), and an engine item applies params/assets/sidechain to that tab.

RATE labels use the slot's `display` from `format_param`. Tabs stop at four; the
empty `+` is only offered while there is room. Regression tests: `test_mod_rack.py`.
Display deviation from modulation-spec §2.2: route meters show live **target**
values from `meters.mod`; the protocol does not yet expose source output meters.
Routing gestures landed in N3b below. Palette, font and screenshot baselines are unchanged.

## N3b — routing gestures

MOD output copy, target paste, route lift/move/delete and route-range editing
live in `ModRackView`; `SynthPage` only supplies the string address under each
ENGINE / ENV / FX cell. `Param.ghost` is a normalised live value from
`meters.mod`, drawn independently of the authored knob bar. FX gained the same
master/node bypass gesture as MOD, backed by host-side dry passthrough and
format-1 rack fields. Closing a SHIFT+3/4 browser uses `on_menu_number`, so the
key that accepts the browser does not immediately toggle the engine it loaded.

`test_mod_rack.py` drives all gestures against the real offline host. The
`mod-routing` verify feature captures output-copy, target-paste, route master,
ghost, range and both bypass states. See the `[TBD-Elfy]` table above for the
chosen glyphs and the explicit LO/HI audition deviation.

## E2 — the tape UI

The tape model is **eight tracks in two banks of four** (`app/tape.py`
`NUM_TRACKS = 8`, `BANK_SIZE = 4`). The bank is **derived** — `selected // 4` —
because DDR-060's SHIFT+1-4 flips the bank *and* selects in it, so a stored
bank could only ever disagree with the selection. TRACK draws `tape.bank_tracks()`
and MIXER follows the same bank until F2 makes MIX eight columns.

`tick_ladder(view_len, bar_samples)` is DDR-058 as a pure function: the finest
rung that keeps at most 16 ticks on screen. It is the ruler, the step
SHIFT+‹ › takes (`Tape.nudge_tick`) and the grid REC+note snaps to
(`App.rec_tick`) — one number, three uses, which is the point of tying
precision to zoom. `Tape.snap()` (marker-to-marker) is **gone**: clip edges are
drawn but are not snap targets.

`Tape.jump(direction)` is STOP+‹ › (DDR-057) and `App.stop_held` is the modifier.
Consecutive lifts share one undo snapshot through `push_undo("lift")` (DDR-055);
any other op closes the group. A LIFT ALL buffer now carries `lanes`, so DROP
puts each clip back on its **own** lane at the cursor, and SHIFT+DROP
(`do_merge_drop`) bounces them all into one clip on the selected track.

### Two things E2 found

- **`clip_to_asset` without a `file` lands in the host's *first* library root**,
  which is the box folder in every run and in the tests. The pages pass an
  explicit path under `App.library.root` (`App.sampler_file()`, INPUT's own
  `mic-NN.wav` naming with a `tape-` stem), so a take lands where the browser
  looks.
- **A lifted clip is not on the timeline**, and `clip_to_asset` renders the
  timeline. `Tape.buffer_to_asset()` therefore lays the held audio onto its own
  track for exactly the length of the call and puts the real list straight back.
  That is only safe because `set_clips` is literal (E1): the host renders the
  list it is given and tidies nothing.

## F2 — the mixer UI

MIXER is eight columns, the host's mute/solo, the host's per-track EQ, the
master rack and the whole MASTER strip. **Nothing on this page holds a second
copy of anything the host owns.** `app/mixer.py` lost its fake `eq` and `fx`
dictionaries and gained only what the UI really owns: the four MASTER knobs F1
added (`comp_rel`, `drive`, `drive_tone`, `bal`), pushed by `App._native_sync`
in the same `mixer` message as LEVEL and COMP.

**Mute and solo are read, never computed.** `pages/mixer.py` asks the host
(`state.tape.tracks[i].mute` / `.solo`) what a column is doing. H7 (mute vs
solo) is still an open question in `design-decisions.md`; the host's answer —
**solo wins**, and the gate is on the mix only — is the one that ships, so the
page draws it rather than adding a second opinion. `is_silent(i)` is that
reading, and it is what dims a column.

**`1`-`4` do two jobs.** DDR-001: tapped they are the subpage, held past
`MixerPage.HOLD_S` (0.28 s, evaluated in the page's `tick()`) they solo that
column while held. The solo is not sent on the press — a subpage change must
never flick the other seven tracks silent for 80 ms — and a press that was a
mute (SHIFT down) swallows its own release so it cannot navigate either.

**The EQ page keeps no ranges of its own.** Every cell is rebuilt from
`state.tape.tracks[i].eq` each frame, one encoder sends one field of one `eq`
message, and the clamped block that comes back is what the cell prints. The
numbers in `app/mixer.py` (±12 dB, 100–8000 Hz, …) are *drawing* ranges — how
far a detent goes and how long the bar is — not a second clamp.

**The curve is the host's own filters.** `app/mixer.eq_response_db()` builds
the same five RBJ sections as `audio/host/mixer.hpp` and evaluates them on the
unit circle. It makes no samples and is not DSP in the "no DSP in this folder"
sense; the alternative was a cartoon, because §8.2 hands no magnitude response
up.

**MIXER › FX is `pages/rack.py` unchanged.** `MixerPage.instrument_id` is
`"master"` and `NativeAudio.instrument("master")` resolves to `state.mixer`,
which carries the same `fx` / `tabs` / `macros` keys an instrument does. One
host bug turned up doing this: `rack_reply` labelled the master bus `"drum"`,
so a master rack edit merged onto DRUM's rack in the client's mirror. Fixed in
`audio/host/audio_host.cpp`, and `rack_edit` now trusts the bus it addressed
rather than the reply's label.

## G — the live input

`LIVE INPUT` is an **engine**, not a mode (DDR-063). It is in the SYNTH engine
list because `audio/library/synth/live-input/` exists — `init.patch` on MIC and
`resample.patch` set to resample — and from there it is an ordinary box: eight
slots from the descriptor, `SOURCE · GAIN · — · —`, GAIN printed in dB by the
box's own `format_param`.

**SOURCE has one home and it is the host.** `state.input.source` is the value;
`NativeAudio.input_source()` reads it and `input_source("line")` writes it. The
SOURCE knob on SYNTH › ENGINE is the same value wearing a descriptor — the host
mirrors the two — so a page must never keep a copy. INPUT's SHIFT row shows the
same thing next to the sampling knobs. Both cells are re-read every frame:
`InputPage.slots()` refreshes its cell, and `SynthPage._sync_live_input()`
refreshes the ENGINE cell, because the eight ENGINE cells are built once per
engine load and `input_source` can move while the page is not looking. That one
special case is deliberate: nothing else in a descriptor is host-owned, and a
general rebuild-every-frame would drop the copy rings and ghost markers the rack
gestures put there.

**Mic capture is still Python**, exactly as N1 left it. `app/mic.py` owns the
ring buffer, the transient trigger, the trim and the WAV; SOURCE and the routing
are the host's. INPUT's GAIN is the capture path's trim, before the threshold,
and is **not** the box's GAIN — two knobs, two jobs, and the page says so.

## I2 — the effect boxes, and one thing the rack page was not drawing

Stage I2 is an `audio/` stage: five effect boxes, no UI change and no new page.
It found **one bug up here**. An FX tab's slot carries the engine's own readout
as `display` — the host has put it there since ABI 2 (`SlotState::to_json`) —
and `pages/rack.py` `_engine_slots` built its cells **without** it, where the
ENGINE page (`native_engine.slots_from`) and a MOD tab both pass it. Nothing
had noticed because no stock effect implemented `format_param` until now;
`arbutus/delay`'s TIME drew `0.33` where the box says `1/8`, and
`arbutus/crusher`'s TONE drew `20.0kHz` where the box says `OFF`. One argument,
fixed.

The browser moved again, the way it did at G and I1: the FX folder list is
`RACKS` and then one folder per effect box in alphabetical order, so five new
boxes put `COMB` at index 3 where it had been at 2, and the two feature scripts
that scroll that menu by `delta` (`native-rack`, `mixer-strip`) moved with it.
The unit tests look their folders up by name and did not.

The five stage-I2 stock racks (`audio/library/fx/racks/`: `tape-echo`,
`ensemble`, `handheld`, `stack`, `voice-eq`) are ordinary §6.7 documents and
need no code — SHIFT+3 › RACKS already loads any of them.

## H2 — patterns as assets, SEQ and TEMPO

Two pages finished and one bridge deleted.

### The SEQ row is the box's

`native_sequencer.slots()` returned four Params looked up **by attribute**
(`p_div`, `p_swing`, `p_hold`, `p_dir`) with a UI-only ACTIVE wedged at ENC3.
H1 then put four boxes in that slot declaring seven or eight parameters each,
so `arp`, `euclid` and `pattern` drew nothing at all and `endless`'s own GATE,
PATTERN and ROT were built, saved and sent — and never shown. The row is
`pad_slots(self._params)` now: the descriptor, every cell, with the box's own
`format_param` readout on it (`arbutus/pattern`'s `AUTO 8`), exactly as the
ENGINE page and a MOD tab already did.

**ACTIVE lost its cell.** There is nowhere consistent to put a ninth control
across four boxes that declare seven or eight; the arm gesture is the SEQ key
(DDR-035) and was already, so it is a badge in the header instead — green when
armed, dim when not, which is the sketch's "drawn dim while off".

### A pattern is a file

`load_engine` gained `assets` (`audio/CLAUDE.md`, `PROTOCOL.md` § Assets), so
DDR-037 is one message: a `.seq` picked in the browser goes down **by path**
and the box opens the library's own document. `_replay_pattern` — N1's bridge,
which played the mirror into the box as REC/REST/UNDO/CLEAR presses — is
deleted with its H1 fence. A pattern now reaches `arp` and `euclid` too, which
declare no soft-keys and could never be replayed into.

The mirror stays, because the host reports no pattern back and the note row has
to be drawn from something. It also now mirrors **ties** (REST with a key still
held), which the file format already had and nothing up here could make.

### Two things H2 found

- **A SEQ slot snapshot carried parameters positionally.** `App.set_sequencer`
  copies one box's snapshot onto the next, so switching from `endless` to
  `pattern` put SWING's 50 onto STEPS and clamped it to 32. Keyed by **id**
  now (§7.1's rule for a patch), with the old list still read — every state
  file on disk was written by `endless`.
- **A feature script that scrolls a selector by `delta` is a hostage to the
  library.** Stages G, I1 and I2 each moved every index in every script past
  the box they added. `tools/verify.py` gained `pick: {folder, item}`, which
  matches the displayed name and fails with the list it actually found; the
  new scripts use it and nothing else should scroll a menu blind again.

## J1 — project files

**USB/FILE is a real page** (`pages/stub.py`, unused since, was deleted by #932). `pages/file.py` builds
the frame (`PROJECT · MIDI · USB · SYSTEM` on 1-4) and the PROJECT subpage;
MIDI, USB and SYSTEM are stage J2's and are named placeholders that draw what
will live on them, dim, with no params — a blank screen reads as broken, a
named one reads as not built yet.

### A project is a folder, and the format is defined in the library README

**No document in `v1-spec-package.md` specifies a project file.**
`screen-sketches.md` § FILE says what one *holds* and DDR-019/020 say what the
page may do with it; neither says how it is written down. J1 needed a format,
so it is written down in **`audio/library/README.md` § Projects**, and that is
this stage's one spec deviation.

`app/project_file.py` is the whole of it — capture, save, load, new, bounce —
and it is deliberately not `app/persist.py`. `persist.py` autosaves the
*working* state into `emulator/state/` so a restart lands where you left off;
this is a named thing filed in the library under its date, which is also the
two-pane browser's left column.

What it carries that nothing wrote to disk before (F2 and H2 each left a note
saying so): **both sides of the mixer** — `Mixer.snapshot()` up here and the
host's per-track `gain`/`pan`/`mute`/`solo`/`eq`; the **macro positions**, which
a §6.7 rack document does not hold and which are the sound; the **kit**, which
a §7.2 patch explicitly does not carry; the **master bus**, which belongs to no
instrument; and `base_bpm`, which E1 said moves only when a project load says
so — this is that load, and it is the only place that sends it.

The six memory slots per page **are** the instruments: the active slot's record
is already a whole patch (DDR-029), so a load restores the slots and then puts
back the two things a patch cannot carry. Nothing here re-invents `patch_from_state`.

### NEW is the boot state, walked

The pages are built once and kept forever (DDR-028), so NEW cannot be "throw
the App away". It walks the same pieces `App.__init__` sets up: `Tape.reset()`
and `Mixer.reset()` re-run their own `__init__` (the only version that cannot
drift when a knob is added), each slot page gains a `reset()`, the master rack
is replaced with an empty §6.7 document and TEMPO is restored from a named
`BOOT_TEMPO`.

`SynthPage.reset()` loads the default box as a **whole sound**, not as an
engine. `_load_default_engine` goes through `apply_item`, which since DDR-030
takes only the engine block and leaves the previous rack, MOD rack and envelope
standing — right for SHIFT+1, wrong for NEW.

### Three things J1 found

- **`load_rack` refuses a document with no `kind`.** NEW clears the master bus
  by loading an empty §6.7 rack, and `{"format":1,"name":"RACK","tabs":[]}` came
  back `ENGINE BROKEN — kind is not "rack"` and left the old rack playing.
  `project_file.EMPTY_RACK` carries it.
- **`_native_sync` is a diff, and a project load moves the host too.** A value
  that lands back on the one the fingerprint remembers would never be sent, so
  a load ends by forgetting every fingerprint (`project_file.resync`).
- **A project save has to stop the transport first.** `tape_save`,
  `tape_load` and `clip_to_asset` are all refused while a take is open, and
  `Tape.stop()` is also what clears a pending COUNT-IN punch (H2's note) — one
  left armed would fire into the next project. `project_file.halt` is that call
  and every one of the four actions goes through it.

### New in the harness

`tools/verify.py` gained `projects()`, `project_doc(name=None)` and
`wav_frames(path)` in the assert namespace: an assert runs with no builtins and
no `os`, so anything that has to look at the filesystem lives in the rig. The
`project-file` feature runs `temp_library: true` — it writes a project, and a
project must never land in the repo's library.

**405 tests, 30/30 verify features** as of stage J1. `test_project_file.py`
(19) proves the round trip against the **host's** `state`, with a NEW in the
middle so the comparison means something.

## J2 — the browser, the system pages, the battery (the last stage)

USB/FILE's other three subpages, every browser becoming a file manager, and
the battery. Nothing in the build is a placeholder (`pages/stub.py` is gone, #932).

The theme of the stage is saying what this box cannot do *on the page*, in
words, rather than drawing a control that does nothing. There is no MIDI
output here, no USB gadget, no BLE radio and no Wi-Fi stack (the device OS is
not built — `companion-app-spec.md`), so those cells are named, dim and
**locked**, which is DDR-053's shape, and the main area gives the reason. The
alternative — a knob that stores a number nobody reads — is the kind of thing
that is discovered on stage.

### Settings are a third kind of state

A patch is a sound and lives in the library (§7). A project is a session and
lives in `<library>/projects/` (J1). A **setting** is the device — which MIDI
channel it listens on, how bright the screen is — and belongs to neither.

`app/settings.py` writes one format-1 document, `<library>/settings.json`, and
the format is in `audio/library/README.md` § Settings. The library root is the
right home because it is the one directory that is the *instrument's* rather
than this checkout's: `emulator/state/` is per-install working state and is
wiped by `--fresh`, and a setting has to survive that. Neither scanner sees the
file — the host indexes folders carrying an `engine.json` and the browser walks
`synth/ drum/ fx/ seq/ mod/`, never the root.

`test_file_system.py` asserts a project document contains no setting, in as
many words. A project loaded onto a differently-configured instrument must not
bring its MIDI channel with it.

### MIDI: the one subpage that gained a real input

The emulator has had a MIDI input since phase 3, so `IN CH` and `CLOCK IN` are
not stubs. The channel is read out of the settings document on **every
message** (`MidiIn.in_channel`), and `midi.toml`'s `channel` key is now the
*boot default*: it is copied into the settings once, when there is no settings
document yet. `MIDI VEL CURVE` (SYSTEM) shapes the incoming velocity byte on
the same path — the device's own keys have no velocity at all (plain switches,
control-inventory § chromatic keys), so the curve only ever applies to MIDI.

`app/midi_clock.py` is the meter: 24 pulses to the quarter note, BPM from the
**median** of the last beat's intervals rather than the mean, because USB MIDI
arrives in bursts and one late pulse under a mean moves the tempo by whole
numbers. **Since #1126 (2026-10-15) it is the least-squares slope of the last
bar's arrival stamps** (97 stamps against their pulse numbers): on the 3B+ at
Zero clocks each stamp waits 0–9 ms for the GIL in rtmidi's thread, and the
median of a beat's intervals wandered 127–135 BPM on a clock exact to 0.04 ms;
the slope holds within 0.1 BPM on the same stamps and followed 132 BPM to
0.008–0.034 in five cells. An interval over 3× the window's median is a gap
and starts the window again. A gap longer than 0.5 s starts the measurement again and `alive()`
goes false, so "SYNC MIDI with nothing on the wire" is a state the page can
draw rather than a frozen number pretending to be a tempo.

**This is what H2 was waiting for.** `TempoPage.external_sync` has locked BPM,
BPM FINE, the nudge and tap tempo since H2 with nothing to follow;
`TempoPage.poll_external` (called from `App.tick`) now follows the measured
clock, and the lock stays — while a master is running the tempo is not that
page's to set. The page shows the switch and the cable as two separate facts:
`MIDI CLK` in green when one is arriving, `MIDI NO CLK` in brown when the
switch is on and nothing is.

### The browser is a file manager (DDR-055)

`app/library_ops.py` is the file half; `app/menu.py`'s `MenuPage` is the
gesture. A source answers two new questions — `item_path(f, i)` and
`folder_path(f)` — and a source that cannot is simply not a file manager
(the gesture says `NOT A FILE` and nothing moves).

**A LIFT moves the item to `.trash/` immediately.** Holding the path and moving
only on the drop is the obvious design and it is wrong: "never dropping = the
trash" then has no moment at which it happens, and you would have to decide
that a browser closing, or the instrument shutting down, is the delete. Moving
on the lift makes the rule self-enforcing — the item leaves the folder the
instant you take it, which is also what a lifted tape clip does (DDR-013) — and
a DROP is simply the second move.

`.trash/` is dot-prefixed, which is what keeps it out of both scanners: the
emulator's `_from_tree` has always skipped dot folders and the host's box index
does now too (stage J2, `audio/hostlib/library.cpp`). It is gitignored by the
root `.trash/` rule that was already there for the Obsidian vault.

**Stock content is a manifest, not a guess.** Nothing on disk distinguishes a
patch Elfy promoted from one that shipped — both are a `.patch` written by the
same code — so `<library>/stock.json` lists what came in the image and
`audio/tools/mkstock.py` writes it. **Add stock content to the library and
rerun that tool**, or the new item is liftable. No manifest at all means
nothing is stock, which is right for a library someone assembled themselves.

### What J2 found

- **An assert in a feature script cannot see the namespace from inside a
  comprehension.** It was already written down for `tape_end()`; J2 hit it
  again with `[str(f) for f in …]` raising `NameError: str`. Anything that
  folds a list belongs in the rig, not in the expression.
- **`library_items()` returns filenames, extension and all.** The browser's
  *displayed* rows strip it (`LibrarySource.items`), which is why
  `item_path()` exists at all: the name on screen is not a filename and cannot
  be joined to a folder.
- **A file op can take the highlighted row out from under the cursor**, so
  `MenuPage._sync_params` now clamps `state.folder_idx` and `state.item_idx`
  rather than only the Param values.

### Seams this build leaves behind

Nothing is "next" — the v1 spec package is built — but these are the places a
later feature plugs into, and each one cost a debugging round to find:

- **A new slot page must be added to `project_file._SLOT_PAGES`** or a project
  will not save it. `app/slots.py` `SlotStore.records` is public and a project
  load writes it directly.
- **`NativeEngine.set_pad_file`** writes one pad's file into the kit document
  and reloads it. DDR-030's tape-drop-on-DRUM uses it; a *browser* gesture that
  puts a sample on a pad would use the same call and does not exist.
- **`Page.on_note_key(note, pressed)`** lets a page eat a chromatic key before
  it sounds (N2b's pad copy). **`Param.locked`** draws a named cell dim and
  ignores the encoder, and is now the shape for every control this host cannot
  offer. **`Param.ghost`** is the live-value marker (N3b).
- **A feature script that scrolls a selector by `delta` is a hostage to the
  library.** Use `pick: {folder, item}`. Stages G, I1 and I2 each moved every
  index in every script past the box they added.
- **Encoders are 1-based in a feature script** (`rig.enc` subtracts one) and
  0-based in the unit tests.
- **`load_engine {assets}`** is how content reaches a slot by path (H2). Any
  later box that takes an asset uses the same door: present replaces the whole
  map, `{}` clears it, absent leaves it.
- **`tick_ladder` is `app/tape.py`'s**, not TRACK's. Anything that wants a
  musical grid calls it rather than writing a second one.

**454 tests, 32/32 verify features** as of stage J2 (405 / 30 at J1).
`test_file_system.py` (49) covers the settings document, all three subpages,
the clock meter as arithmetic, every file op against a throwaway library, and
the battery; `file-system` (12 shots) and `browser-ops` (8 shots) are the user
paths. `stubs` keeps its name and is the zone-geometry proof now — there is
nothing left in the build for it to be about.

## Inspection 2026-09-18 — the seams between the stages

The whole-system pass after Track S finished (`docs/logs/2026-09-18.md`,
session 6). Every stage's own suite held; what it found was at the joints,
and all of it was one thing: **the one sequencer up here and the host's SEQ
slot per instrument disagreeing about what was loaded where.** Spec gaps it
could not fix are `docs/design/v1-spec-package.md` § Inspection 2026-09-18.

- **A whole-sound load carries the sequencer** (control-inventory § Patches),
  so `load_patch` loads or empties the host's SEQ slot. Nothing up here
  followed it: after SHIFT+slot onto a sound with no `seq` block the ACTIVE
  badge stayed green, the ticker said `SEQUENCER → SYNTH` and 1–4 were dead
  over an empty slot; after a slot recall onto one *with* a block the row drew
  the previous riff over the box the patch had just loaded.
  `SeqPage.sync_from_host(inst)` runs after every `load_patch` on SYNTH/DRUM
  (`SynthPage._sync_sequencer`: `load_patch`, `restore`, and `reset` through
  them) and makes the mirror what the slot is — `NativeSequencer.adopt_host`
  takes the slot's parameters onto the row and its `pattern` asset onto the
  note row without pushing anything back; `mark_unloaded` is the other half.
- **Arming touches the instrument's slot record.** `App.notify_param_changed`
  on the SEQ page also touches the store of the instrument the box is on;
  before, the record predated the arm and recalling it — even the letter you
  were already on — handed the host a patch with no `seq` block.
- **Entry ends with the mirror as the asset.** The host's `apply` rebuilds
  every engine from its assets on any patch, rack or MOD edit, so an entered
  pattern the slot did not name was lost on the next SHIFT+3 while the row
  kept drawing it. REC's release now `push_pattern`s the scratch file and CLEAR
  pushes `{}`; a re-arm and a promote did already.
- **A parked box is not running.** Only the active instrument's slot is
  clocked (§15.4). With the box on SYNTH and DRUM active the keys play DRUM
  raw; `running` is false then, the ticker and the SEQ header name
  `seq.target_id` (the box's own instrument), never the active one.
- **A SEQ record names its instrument** (`snapshot()["instrument"]`), so a
  project load or a restart puts the box back on SYNTH or DRUM as it was,
  not on whichever was active at restore time. **The second sequencer is not
  built** — DDR-034 as amended gives each instrument its own; up here there is
  one, on the instrument it was armed on, and the other has none. That is
  open-gaps item 6, not a UI decision this pass made.
- **A slot record that names a file that is gone** — an entered pattern's
  `$TMPDIR` scratch file after a reboot, a sample lifted to `.trash/` — had
  its whole patch refused under §7.4, so a restart came back with the engine
  and none of the envelope, rack or MOD rack, silently. `SynthPage.restore`
  retries once without the missing assets (`_without_missing_assets`); the
  SEQ page restores its own mirror straight after and pushes it down on a
  fresh scratch file, so the riff comes back too.
- **MIDI pitch bend** was in DDR-052 and the scope table and had no wire:
  `midi_in.py` now sends `pitchwheel` as `bend` on the active instrument,
  -1..1, and the host owns the ramp and the range as it does for SHIFT+‹ ›.
- Noise: a SEQ row `set_param` before the box is loaded was refused `that
  slot is empty` four times per restore; the row waits for `_push_params`.

Tests: `test_seq_patterns.py` gained `SeqSeamCase` (5) and
`test_entry_ends_with_the_mirror_as_the_slots_asset`; `test_file_system.py`
`TestMidiIn` gained the bend. **461 tests, 32/32 verify features**; CTest 162
unchanged, no golden regenerated, no baseline promoted, `ui/palette.py` and
`ui/font.py` untouched. A routine commit (`11df830`) had carried seven
rewritten `validation/ui/native/*.png` and a `venv` symlink; the PNGs are back
to their pre-F1 bytes (the symlink was already untracked).

## Audit Q1 — 2026-09-18 (#31)

The first Track Q audit (`docs/design/audit-2026-09-18.md`): tape, mixer and MOD against
`tape-sampling-workflow.md`, `screen-sketches.md` § TRACK / § MIXER and
`modulation-spec.md`. Three UI findings go to issues rather than edits, because the spec
states intent and the code is what changes: **the `keys` source has no UI** (no K tab, so
`keys.VEL` / `keys.NOTE` routes cannot be made — modulation-spec §3.2, §8); **TRACK ENC3
does not move BPM while LINK is on** (workflow §2.3 "turning either moves both" is half
built — BPM drags the tape, ENC3 is the host's varispeed); and **the LEVEL tick on the
playhead**, where the spec carries two sentences and the build is the 2026-09-12 foot.
Choices the code made where the spec is silent:

## P3 — what the device asked of `emulator/` (2026-09-19, #19)

`arbutus_os/main.py` runs **this package** (engine-spec §11: imported, never copied), so
the device is a second consumer of `app/` and every difference between it and
`emulator/main.py` had to become a seam here rather than a copy over there. Three
changes, all additive; nothing the desktop run does is different.

**`NativeAudio(attach=True, socket_path=…)`.** The emulator spawns its own host; on the
device systemd owns `arbutus-audio` (`device-os-spec.md` § 4.1) and the UI connects to a
socket it did not create. An attached object therefore also **never sends `shutdown` and
never unlinks the socket in `close()`** — `systemctl restart arbutus-ui` is an ordinary
thing to do, and a UI that took the sound down on its way out would be a bug you can only
find on a board. `client=` names the client in `hello` (`arbutus-ui` vs
`arbutus-emulator`), which is what the journal will show.

**`NativeAudio.reconnect()` and `link_down`.** `PROTOCOL.md` has no `ping`, and inventing
one to poll a live host 60 times a second would cost more than it proves, so liveness is a
flag set in the two places a death actually shows — a failed `send` and a failed `poll` —
and cleared by a successful `reconnect()`. `reconnect()` re-opens with § 4.2's backoff
(0.2 s → 2 s), re-sends `hello`, re-enables `meters` and refreshes the mirror. It does not
restore anything: the host it reaches is a fresh one at boot defaults.

**`project_file.load` split into `read` + `apply`, and `project_file.rewalk`.** § 4.2 says
the reconnect walk is "the same walk a project load does", so it *is* that walk:
`rewalk(app, doc)` runs `apply` over a document `capture` produced, with the tape media
coming from what `app/persist.py` last autosaved instead of a project folder.

> **The ordering is load-bearing.** `capture` reads the host-owned half of the instrument
> — the racks, the MOD routing, the macro positions, the per-track strips — out of
> `NativeAudio.state`, which is the UI's *mirror*. The moment the socket re-opens, that
> mirror belongs to the new host. So the document has to be captured **before** the
> connection dies; `arbutus_os/main.py` keeps a rolling one on a 5 s timer, and
> `arbutus_os/tests/test_reconnect.py` asserts that capturing too late restores the boot
> state instead — the test exists so nobody deletes the rolling capture as redundant.

What `rewalk` cannot bring back is anything made since the last autosave: the tape media
live in the host's RAM (`device-os-spec.md` § 3.3) and die with it. That is § 7.3's loss
bound, not a gap here. § 7.3's other half — temp-file-then-rename — was already true of
every writer in `app/` (`persist`, `slots`, `settings`, `project_file`), so P3 recorded it
rather than rebuilding it.

`emulator/testing/` is no longer only a test seam: `headless_display.py` and
`scripted_input.py` **are** the IO-board mode's display and input drivers (#19), because
the CM4 on the official IO board has no panel and no keyswitch matrix. Treat them as
shipped code.

## P5 — what MIDI on the device asked of `emulator/` (2026-09-19, #21)

`drivers/desktop/midi_in.py` is now the **device's** MIDI decoder too — the transport
moved out (`arbutus_os/midi.py`: every ALSA-sequencer input, hot-plugged, on a reader
thread), the decoding did not. Two additive changes here, nothing the desktop run does
is different:

**`MidiIn(app, open_port=False)`.** The device opens its own ports, and two transports on
one cable would each get half the messages. The desktop default is unchanged.

**`MidiIn.handle(msg, now=None)`.** The device's reader thread queues and the tick drains
(`device-os-spec.md` § 8.1: `App.on_key` is the UI's whole state machine and is not
thread-safe, so dispatching from rtmidi's thread would race the tick). Queueing is free
for notes — one frame, the same worst case the keys have — and it is **not** free for the
clock: a 24-ppqn pulse every 20.8 ms drained on a 16.7 ms frame gives intervals of 0 and
16.7 ms alternately, whose median says 300 BPM. So a caller that knows when a message
arrived passes it, and `MidiClock.pulse(now)` / `transport(kind, now)` already took one.
`None` means "now", which is what polling in the tick means and is why `emulator/main.py`
needed no change at all.

`app.midi_in` is whatever transport is running: `pages/file.py` `_render_midi` reads
`.port_name` off it, and the device's answers `"Keystation 49 +1"` when more than one
keyboard is plugged in. `tests/test_file_system.py::TestMidiIn` gained both (2);
the transport itself is `arbutus_os/tests/test_midi.py` (17).

## P6 — what the drivers asked of `emulator/` (2026-09-19, #22)

The six P3 drivers (`device-os-spec.md` § 6) live in `arbutus_os/` and in `audio/host/`.
They asked `emulator/` for exactly two things, both additive, and nothing the desktop run
does is different.

**`app/battery.py` gained a device branch.** § 6.6: `arbutus-power` reads the gauge and
publishes `/run/arbutus/power.json` every 5 s; this module reads that file and returns the
same `{"percent", "charging", "source"}` it always has. So the HELP-hold line and the
`BATT n%` ticker J2 built came alive with **no page change** — which is the whole point of
the shape `read()` was given. The path is tried before the platform's own battery and
after `ARB_NO_BATTERY`, so a promoted baseline is still the no-telemetry state. Every
failure — no file, half a file, a `source` that is not `device`, a gauge that cannot say
how full — is `None`, because this is read on every frame a HELP overlay is up and a
battery readout may not raise. `POWER_JSON` takes its directory from `ARBUTUS_RUN`, the
same environment variable `arbutus_os/paths.py` uses: the two agree **on the variable
rather than on an import**, because `emulator/` never imports `arbutus_os/` (engine-spec
§11 runs the other way).

**FILE › SYSTEM's BRIGHTNESS row can stop apologising.** The row has said `STORED — NO
BACKLIGHT HERE` since J2 stored the setting with nowhere to send it. On the device there
is somewhere: `arbutus_os/drivers/uart_input.py` turns it into the ATmega's `L n`
(§ 6.2). `pages/file.py` now reads `app.backlight` — set by `arbutus_os/main.py` and by
nothing else — and draws `SCREEN BRIGHTNESS` when there is one. The desktop leaves it
unset, so every screenshot baseline is untouched; the conversion from the setting's
percentage (10–100) to the command's byte (0–255) is in `main.py`, once, because two
places doing it is one place doing it wrong.

Not asked for and worth saying: the **encoder pushes** on the new `COL11` (io-map § P3,
work-order D9) arrive from the driver as `ENC1_PUSH`…`ENC4_PUSH`, and `App.on_key` ignores
them — `app/keys.py` has no member for an encoder push and no page has a gesture for one.
The wiring exists and the software does not use it. That is the entire state of encoder
pushes in v1, and it is here so it is not rediscovered as a bug.

## D6 — what USB storage asked of `emulator/` (2026-09-19, #27)

`device-os-spec.md` § 11 is a device thing — a configfs gadget, an unmount and a role
switch — and all of it lives in `arbutus_os/usb/`. It asked this tree for one flag and the
two sentences that hang off it.

**`app.usb_exported`, set by `arbutus_os/main.py` and by nothing else.** While the computer
holds `/data`, § 11.1 says `LOAD`/`SAVE`/`BOUNCE` and the browsers' file ops are locked
with the reason on the page. `pages/file.py` reads the flag through a `usb_exported`
property that is a `getattr` with a `False` default — the same shape `app.backlight` got
from P6 — so the emulator, which has no `usb.sock` and can never set it, is unchanged to
the pixel and no baseline moves.

Two places, not one, because arming is reachable from the strip keys and not only from a
cell: `slots()` locks and dims the three actions, and `_arm` refuses them outright with
`USB STORAGE — EJECT ON THE COMPUTER, THEN TURN MODE OFF` in the strip. **`NEW` is not
locked** — it resets the session in RAM, and refusing it would be refusing the one action
that still works while the library is away. The same sentence is the page's ticker while
the flag is up, because § 11.1's instrument keeps playing and this is a line on a page, not
a modal.

What did **not** change: `_set_usb_mode`. DDR-021's force-save on entering STORAGE was
already right, and the settings document it writes is exactly the request `main.py`'s
`UsbPump` carries to `usb.sock`. The device half can refuse (no `/data` partition, the
volume busy, no host role on this board) and can change the mode by itself when the cable
is pulled, and in both cases the pump writes the service's answer back into the same
setting — so the cell is driven by one document from both ends, the way every host-owned
cell in this instrument already is.

### #524 — STORAGE and CHARGE read the device (2026-10-05)

Until #524 the two rows drew J2's laptop sentences on every host, the CM4 included
(`NO USB GADGET ON THIS HOST`, `ON · NOT SWITCHABLE HERE`). Now `UsbPump` and
`PowerPump` mirror `usb.sock`'s and `power.sock`'s status documents onto `app.usb_status`
and `app.power` (`None` with no service), read through two `getattr` properties like
`network` and `firmware`, so the laptop still draws J2's sentences and no baseline moves.
**STORAGE:** `EXPORTED TO THE COMPUTER` (green) while exported, else the service's
`storage_blocked` refusal verbatim (`NO /data PARTITION`, dim), else `READY`. **CHARGE:**
`CHARGING` (green) / `NOT CHARGING` from `charging` (`CHG_STAT`, device-os-spec § 7.2's
row), `NO CHARGE LINE` (dim) where the document says `charge_stat: unknown`; the CHARGE
cell shows the same state and **stays locked** (the charger is autonomous, nothing to
switch). The words are *run's calls, 2026-10-05* (no spec sentence names them); the
laptop's own battery stays HELP's (`app/battery.py`), not CHARGE's, because a laptop's
charger is not the instrument's port.

## D5b — the `.fw` line on FILE › USB (2026-09-20, #103)

D5 (#26) built the firmware mechanism — the container, the signature, the two slots, the
rollback, `arbutus-fw` and `fw.sock` — and P12 (#106) ran all of it on a board. None of it
had a screen. `pages/file.py` still drew stage J2's one static sentence
(`.FW / .ARBOX: DEVICE OS APPLIES IT`), so the on-device confirmation DDR-073 requires for
an unsigned file existed only as `apply {file, confirm: true}` typed onto a Unix socket:
a player could not install a third-party box at all, and a rollback's
`UPDATE FAILED — KEPT <ver>` was written to a field nothing read.

**One document in, one door out**, both set by `arbutus_os/main.py`'s `FwPump` and by
nothing else — the shape `app.usb_exported` and `app.backlight` already have:

- **`app.firmware`** is `fw.sock`'s status document, mirrored once a frame. The page reads
  it through a `firmware` property that is a `getattr` returning `None`, and `None` is
  § 4.3's *locked*, not *empty*. On the emulator nothing ever writes it, so every pixel of
  this page outside the device is what it was.
- **`app.fw_command`** is a callable that queues one message. The three actions —
  `reboot`, `apply {file, confirm}`, `dismiss` — unpack a slot, reboot the machine and
  write `slots.json`; all three are root's and the UI is unprivileged, so it performs none
  of them.

**ENC4 is the control, and that is a deviation**: screen-sketches § FILE › USB draws the
fourth cell as `—`. It is the only free cell on the page, and § 10.4 asks for a `1 2 3 4`
confirm *on this page*, which in this instrument means an armed action — and arming is an
encoder turn. The cell is locked until `fw.sock` answers and while `usb_exported` is up
(§ 11.1: the inbox **is** the library root, so there is nothing to install and a reboot
would pull the volume from under whatever is copying onto it), and otherwise it names the
one thing outstanding.

**RESTART wins over INSTALL** when both could be offered. `_stage_app` unpacks into
`slots.other()`, which while something is staged is that *same* slot — so installing a
second file replaces the release the player has already been told is ready. The finished
one is offered first and the unsigned file waits one restart. (Until #111 the reason was
worse: the pointer had already moved, so `other()` was the **rollback** and a second
install threw away the only way back.)

**`staged`, not `pending`** (#111, 2026-09-20). The page reads `status.staged` for its
`RESTART` row, its green and its ENC4 action. The two are different states and the spec's
§ 10.10 is why: `staged` is a release written into the other slot with the pointer left
alone, waiting for a restart; `pending` is one the pointer is already on — *running*, on
trial until it draws a frame — so offering to restart into it would be offering to apply
what is applied. A `pending` slot shows up on the page as the running version in the
`SLOTS` row and nowhere else.

`_fw_armed_line` names `staged_version` first, then `last_result.version`, then
`status.version`. Since #111 the pointer does not move at staging time, so
`status.version` is honestly the running release; `last_result` stays second for the
instant between an `apply` reply and the next status poll, when only the reply knows what
was just written.

## D3/D4's UI half — PAIR, WIFI and FORGET PHONES (2026-09-20, #97)

D3 (#24) and D4 (#25) built `arbutus-net` and stopped at the socket. The three cells
`device-os-spec.md` § 9.2 and § 9.5 name were still J2's "named, dim and inert", so on a
real instrument a player could turn nothing on and **nothing anywhere drew the 4-digit
pairing code** — which the phone cannot pair without, so the whole of Track A was
unreachable from the instrument's own screen.

Same shape as D5b above, one document in and one door out, both set by
`arbutus_os/main.py`'s **`NetPump`**:

- **`app.network`** is `net.sock`'s status document, mirrored once a frame with § 9.5's
  pairing push merged over it. `None` off the device, which is § 4.3's *locked*.
- **`app.net_command`** queues `pair {on}` and `forget_phones`. Opening a radio, deleting
  the token and restarting `arbutus-audio` are all root's or the service's.

**The code is drawn `LARGE`**, and it is the only thing on this page that is. A 5×7 value
on the right-hand edge is legible at arm's length; this number is read across a room and
typed into a phone. While the window is open it takes the foot of the page from the `.fw`
sentence — both want that line, the window is three minutes and is what the player is
doing, and the `.fw` line is persistent and still there afterwards.

**`PAIR` is the one armed-looking control on this page with no `1 2 3 4`.** Every other
one can lose work; this opens a window a second turn closes. What it has instead is
`_pair_sent`: `PairingService.begin()` mints a fresh code on every call, so a second
detent in the same direction must not reach the service, and until the service agrees the
cell shows what was asked for rather than the mirror (`UsbPump.sent`'s shape).

**`FORGET PHONES` asks once, not twice** — DDR-064's second round is for the gesture that
throws away more than a session, and a forgotten phone is re-paired by standing next to
the instrument with the cell that is now live. **`WIFI` stays a read-out**: the join is
the phone's, and `wifi {on}` would be a switch that can only turn a radio off.

`PAIR_CODE_OK` is `ble.py`'s `pair_state` value written out by hand, because `emulator/`
never imports `arbutus_os/`. `arbutus_os/tests/test_net_pump.py::TheStringsAgree` is the
seam that keeps the two the same string — the same arrangement `FW_CONFIRM` has, and the
class of bug #84 and #26 both were.

## L3 — what taking the note off the frame clock asked of `emulator/` (2026-09-20, #125)

**Two lines, and no pixel moves.** #125 is a device ticket: on P3 the UART reader thread
now writes `note` to `arbutus-audio` the moment it parses the key, against a snapshot the
tick republishes, instead of waiting for the frame (`arbutus_os/fastnote.py`,
`device-os-spec.md` § 1.3.1). Nothing in `emulator/` sounds anything differently and
nothing calls the new method on a laptop.

What it needed was one question the App could not be asked. The snapshot's `sound` field
— may a chromatic key sound right now — has to be false wherever `App.on_key` would *not*
sound the key, and one of those places is a page's `on_note_key` hook, which is DRUM's
LIFT/DROP pad copy (§15.3). A hook cannot be asked: calling it copies a pad. So:

- **`Page.claims_note_keys()`** returns False, and is the state `on_note_key` answers by
  acting, asked as a question instead.
- **`DrumPage.claims_note_keys()`** is `self._lift_held or self._drop_held` — and
  `on_note_key`'s own first line is now that call, so the two cannot drift apart. The
  test that says so is `arbutus_os/tests/test_fastnote.py::TheDrumPageAgrees`, in the
  device suite because the contract is L3's.

The alternative was to read the page's private `_lift_held` from `arbutus_os/`, or to
give up fast notes on the DRUM page entirely — which is the page where the latency is
worth the most.

**What the device does not ask of this folder, and deliberately.** The queued event still
runs through `App.on_key` exactly as before, so `_note_owner`, `Instrument._held`, the
sequencer and the autosave see what they have always seen. What does not happen twice is
the message on the wire: the device swallows it by shadowing `note` on **its own**
`NativeAudio` instance (`fastnote.gate_native`), never in the class. A laptop's client is
untouched, which is why the emulator suite's 522 tests and every baseline are unchanged.

## #128 — what taking a MIDI note off the frame clock asked of `emulator/` (2026-09-20)

**One pure function, one tuple, one moved curve — and no pixel moves.** L3 (#125) left MIDI
on the 60 Hz tick; #128 is the device ticket that takes a MIDI note off it too
(`arbutus_os/midi.py`, `device-os-spec.md` § 8.2). The decoder it needs is
`drivers/desktop/midi_in.py`, which is the **device's** decoder as well as the laptop's
(§ P5 above), so the split had to happen here.

The finding is that there was no state machine to split. `handle()`'s note branch already
turned a MIDI message into a **key name** and a velocity — the same currency
`fastnote.FastNotes.key()` takes — because a MIDI note has reached `App.on_key` through a
key name since phase 3. What it could not do was run on another thread, because it read
`Settings` per message. So:

- **`decode_note(msg, snap)`** is a module function: no `self`, no `Settings`, message in,
  `(key name, pressed, velocity)` or `None` out. `None` is everything that is not a note
  for us — CC, bend, clock, transport, and a note on a channel `IN CH` is filtering.
- **`MidiSnapshot`** is the four values that decode reads: `in_ch`, `vel_curve`,
  `note_offset`, `use_velocity`. The first two are settings a player can move; the last two
  are `midi.toml` constants. `MidiIn.snapshot()` reads them off the live state on the main
  thread, and the device's transport publishes one per frame for its reader thread.
- **`handle()` calls `decode_note` too**, with a snapshot taken on the spot. That is the
  point: one implementation, so the two threads cannot decode the same message differently.
  `MidiIn.note_of(msg, snap=None)` is the same call for a caller that has a snapshot.
- **`app/settings.py` gained `shape_velocity(v, curve)`**, a module-level pure function, and
  `Settings.velocity` is now that function with the live curve. `MIDI VEL CURVE` keeps one
  implementation while a thread holding only the curve's *name* can apply it.
  `MidiIn._velocity` is gone — it had no callers left.

**Nothing the desktop run does is different.** The same decode, the same order (the clock
is still read before the channel filter, because system realtime carries no channel), the
same `App.on_key` calls. 529 emulator tests and every baseline unchanged; `ui/palette.py`
and `ui/font.py` untouched.

**What the device does not ask of this folder.** The queued message still runs through
`handle()` on the tick and so through `App.on_key`, with the `note` it would send
swallowed by the same `gate_native` shadow L3 uses — on the device's own `NativeAudio`
instance, never in the class. And CC, pitch bend and the 24-ppqn clock are deliberately
left on the frame: they are not notes, `MidiClock` is state a second thread may not touch,
and the clock's accuracy was already bought by the arrival stamp (§ P5 above).

## #105 — the scratch files this folder leaves in `/tmp` (2026-09-22)

On the CM4 `/tmp` is a **tmpfs**: a file there is RAM, and it counts against the
`MemAvailable` C2 (#75) measured headroom by. A gate run on the bench had left
733 MB of leftovers there. Two of the sources are here.

- **`NativeOfflineAudio`'s render directory** (`drivers/native_backend.py`) held a
  `block.wav` that reached **226 MB**, and `cleanup()` was a no-op. `cleanup()` now
  removes the directory, and a `weakref.finalize` behind it removes the directory of
  a driver that is simply dropped — which is most of them, because the tests and
  `tools/verify.py` build one and never call `cleanup()`. `finalize` rather than
  `__del__`: it is guaranteed to run at interpreter exit and runs at most once.
- **An entered pattern's scratch file** (`$TMPDIR/arbutus-pattern-<inst>-<pid>.json`)
  is removed at **exit and not before**, through one module-level `atexit` hook over
  the set of paths handed out. Since #60 that hook is **`app/scratch.py`** and
  `app/native_sequencer.py` calls it, because a second kind of entered content
  (a stepmod tab's step values) needs the same two rules and a rule hand-copied
  into two places is a rule that will diverge — which is exactly what #140 found
  in `arbutus_os/`. It cannot
  be tied to the `NativeSequencer` that named it: the path is the slot's loaded asset
  and the host re-reads it whenever that box is rebuilt, so a file removed when the
  sequencer object went would be a riff that vanished on the next hot-swap. § J2's
  "a slot record that names a file that is gone" is the same file seen from the
  other end — a reboot is still allowed to lose it.

`ARBUTUS_KEEP_TMP=1` keeps both for a post-mortem. `tests/test_tmp_hygiene.py` is the
proof (3 tests): cleanup removes it, a dropped driver removes it, the env var keeps it.

## S11 — the ENV page a box ignores (2026-09-23, #53)

`NativeEngine.honours_env` has read the descriptor since ABI 2 and **no page
used it**. Two stock boxes declare it false — `arbutus/dbox`, whose envelope is
per pad (DDR-053), and `arbutus/live-input`, because an input is not a note —
and on both of them DRUM › ENV / SYNTH › ENV drew four live-looking knobs, sent
an `env` block the engine throws away, and animated a curve nothing hears.
Deferred twice in the day's logs (stage G, stage I1) before it was a ticket.

`SynthPage.honours_env()` reads the flag off the loaded engine every frame — the
same reason `_sync_live_input` does: a box arrives from a patch, a project load
or the browser without this page being asked. `SynthPage.lock_env_cells(cells)`
sets `locked` + `dimmed` on the four it is given, which is TEMPO's pair under
MIDI clock and DRUM's SUS/REL under DDR-053, so nothing new was invented. DRUM
passes only ATK and DEC: its SUS and REL are locked for **every** kit.

**The values are not blanked.** Dimming is not clearing — a patch has to
round-trip, the `env` block still goes down and is still saved, and the four
cells come alive unchanged the moment a box that honours them is loaded
(`test_live_input.TestTheEnvPage.test_the_values_are_kept_so_a_patch_round_trips`
is that assertion). That is why the DRUM pair's `formatter = "—"` was **not**
copied here: `—` is right for a cell that has no value at all, wrong for one
holding a number a different box will use.

| # | Spec | What was done | Where |
|---|---|---|---|
| 74 | #53: "say why in the ticker on the first turn (`NO ENV · dbox` or similar; wording is `[TBD-Elfy]`)" | `NO ENV · <box>`, named the way the ENGINE header names it (`NO ENV · LIVE-INPUT`), short form `NO ENV`, as a `App.flash` on **every** turn of a dead cell rather than only the first — the flash is a 3 s window and re-arming it is what a second turn should do. The SHIFT row (MODE / GLIDE / DETUNE / BEND) is voice policy and stays live on both pages | `pages/synth.py` `NO_ENV_TICKER`, `on_encoder` |
| 75 | #53: "draw the four cells **and the curve** dim" | the curve keeps its shape and goes `palette.DIM`, with a small `NO ENV` inside the box's top-left corner. The shape is still the block the page would send, so drawing it is honest; drawing it in the encoder colour was not. Both renderers do it — `_render_env`'s four-segment ADSR and `_render_ad`'s filled AD | `pages/synth.py` `_render_env`, `pages/drum.py` `_render_ad` |
| 76 | not in the spec | **the knob bar under a dimmed cell is still drawn in the encoder colour.** `ui/frame.py` dims the dot, the name and the value and not the bar, which has been true of every `dimmed` cell since DDR-053 (TEMPO under MIDI clock, DRUM's SUS/REL, FILE's locked rows). Left alone deliberately: changing it is a one-line edit in a shared renderer that moves those pages' pixels too, and it is Elfy's call whether a dead cell should lose its bar | `ui/frame.py` `_draw_param_cell` |

**538 emulator tests** (532 before): 3 in `test_synth_boxes.py` (`dbox` dim and
inert, a turn that moves nothing and says why, and `arbutus/drum` on the same
page still turning) and 3 in `test_live_input.py` (the same two for the SYNTH
side, plus the round-trip). The `live-input` feature gained two shots
(`08-env-dim`, `09-env-refused`) and is 9. No baseline promoted, no golden
regenerated, `ui/palette.py` and `ui/font.py` untouched.

## #95 — which project is open, and the one field a device service reads (2026-09-22)

**One attribute, one snapshot key, and no pixel moves.** #95 is a device ticket:
`GET /projects/<id>/bounce` names a project and `bake` renders the tape the audio
process has **loaded**, so the device can only ever bounce one project — and
nothing on it recorded *which*, so `arbutus-net` could not tell whether the id
the phone asked for was the right one.

It could not ask, either. § 4.3's device sockets run one way: the UI is the
**client** of the services, so `arbutus-net` has nothing to send a question to.
What it can do is read the autosave document this folder already writes.

- **`App.open_project`** — `"<date>/<name>"` or `None`. The same two components
  under `projects/` that the HTTP route names, produced by the new
  **`project_file.project_id(folder)`** from the folder rather than stored in the
  project document: a folder someone moved is a different project, and a stored
  id would lie.
- **`project_file.save` / `load` / `new` own it, not `pages/file.py`.** Two of the
  three have callers that are not a button — DDR-021's auto-save on the way into
  USB `STORAGE` mode (`_set_usb_mode`), and DDR-064's `RESET`, which goes through
  `new`. Setting it in the page's handler would have left both wrong. `load` sets
  it *after* `apply` returns, so a folder that is not a project leaves it alone.
- **`project_file.rewalk` deliberately does not move it.** § 4.2's reconnect walk
  applies a document `capture` took from the UI's own state mirror, with no folder
  behind it — the instrument has not changed project, so neither has the id. That
  is the whole reason the assignment is in `load` and not in the `apply` the two
  share, and there is a test saying so, because it is the line a later reader
  would tidy away.
- **`persist.snapshot()` emits it and `load_project()` restores it**, so it rides
  in `state/project.json` (on the device, `/data/.state/project.json`). A
  non-string, an empty string or a missing key all restore as `None` — an autosave
  from a build before this field has to read as "the instrument does not know",
  which is what makes the bounce route fall back to its pre-#95 behaviour rather
  than refusing every request.

**Nothing on a page changed.** `FilePage.project_name` / `project_date` are still
the page's own display strings, set from `PF.save`'s return exactly as before;
this is the machine-readable fact beside them. 556 emulator tests (538 before),
no pixel, no baseline, `ui/palette.py` and `ui/font.py` untouched. The device
half and the five decisions are `device-os-spec.md` § 9.9.

## #62 item 3 — the browser's moves are the host's (2026-09-23)

`emulator/app/library_ops.py` no longer moves a file. LIFT is
`library {op: "trash"}`, DROP is `library {op: "move"}`, and a DROP onto a name
already taken is `rename` then `move` — all on the audio process
(`PROTOCOL.md` § `library {op}`, built by D2/#23). What stays in this module is
the **gesture** layer: what is held (`LiftedItem`), which moves belong to one
undo group, the `FOLDER-NN` naming, and the words on the strip.

**The decision that kept it on `shutil` had expired.** #62's own comment left
item 3 open because "the browser also runs with no host attached", and it does
not: `App` raises `ValueError` without `native` (stage N1, `app/app.py`), and
`tests/test_file_system.py` has started a real `arbutus-audio` since. So there
is no third option to pick between — always delegate is the only one left, and
§ 4.2's reconnect window (the one moment on the device when the host really is
away) is served by the op being **refused**: nothing moves, `CANNOT LIFT` is on
the strip, and there is no second implementation quietly doing it anyway.

**The refusals are the host's, the wording is the browser's.** `NOT FOUND` →
`NOTHING THERE`, `STOCK` → `STOCK ITEM`, everything else → the gesture's own
`CANNOT LIFT` / `CANNOT DROP`. Keeping the strings meant no pixel moved; what
moved is which process decides. The two pre-checks that used to make those
strings — `os.path.exists` and a second read of `stock.json` — are gone, which
is the duplication item 3 was about.

**Three things stay UI-side on purpose:**

| What | Why |
|---|---|
| `FOLDER-NN` (`drop_new_folder`) | `library {op}` is four operations on an item that already exists and has no "make a folder"; the *move into* it is still the host's |
| `free_name` (a DROP onto a taken name) | the host's `move` refuses `EXISTS` and is right to — its caller named the destination. A DROP names a *folder*, so the browser picks a free name and asks the host to `rename` to it first. The name has to be free in both folders, because the rename happens where the item is now (`.trash/`) |
| the undo group | DDR-055's "lift, lift, oops" is a gesture fact. A rename inserted by a collision is recorded as its own step, so SHIFT+STOP walks it back too |

**`undo` moves, then renames.** The other order asks the host to free the old
name in the folder the item is sitting in, and for a `.trash/` item that name
is taken by definition — it is the collision that gave this one its `-2`.

**No `rescan` after an op.** Every `library {op}` re-reads the roots before it
replies, so `FileOps.rescan` now re-reads only the browser's own tree.

### What it found: the writable library root was the wrong one

`library {op}` and `clip_to_asset` resolved paths against the **first**
`--library` root. Every invocation in this repo passes the read-only boxes
first and the player's library after them — `[BOXES, LIBRARY]` here,
`--library /opt/arbutus/current/boxes --library /data` in
`arbutus-audio.service` — so a phone's rename, and a bounce with no `file`,
resolved **inside the running A/B slot**, which a `.fw` replaces. The two tests
that covered it (`audio/tests/host_files.py`, `arbutus_os/tests/test_httpd.py`)
each passed the writable tree *first*, so both proved the route against an
order no unit file uses. `AudioHost::write_root()` is the last root now, both
call sites use it, `PROTOCOL.md` says "last" and both tests pass the roots the
way the unit file does.

560 emulator tests (556 before: five new, one — `test_the_host_is_told`, which
asserted a `rescan` that no longer happens — replaced by two that assert the
op itself). No pixel, no baseline, `ui/palette.py` and `ui/font.py` untouched.

## S12 — SHIFT+STOP undoes the last edit on SYNTH/DRUM (2026-09-22, #58)

DDR-054 has been in the spec, in `screen-sketches.md` § SYNTH and in
`control-inventory.md` § left block since the design decisions were written, and
the build order's step 6 listed it as done. **Nothing was built.**
`App._transport` had the tape's undo and the browser's lift group and nothing
else, so on SYNTH and DRUM `SHIFT+STOP` reached past the page and spent the
*tape's* one level — the one undo in the instrument that cannot be got back.

**`app/edit_undo.py` is the holder, shaped like `Tape.push_undo`/`Tape.undo`**
rather than as a new idea: one level, a `group` that coalesces a run of calls
with itself (an encoder sweep is one entry, not sixty), and `clear()`. The one
thing the tape's has not got is a **token**: `push` hands back the entry it
displaced and `rollback(token)` puts it back, so an edit that did not happen —
a turn on a locked cell, an encoder with no cell under it, a pad copy the host
refused — never eats the edit before it. Since #152 it also has **`amend`**:
a gesture that is not an edit but that the snapshot carries anyway (the octave)
moves the held snapshot on instead of recording a step, and with no entry open
it does nothing at all.

What it snapshots is the page's own DDR-029 `snapshot()`, a format-1 patch, so
**undo is a slot recall nobody had to save**. That is why the whole mechanism
is ~70 lines: "put this sound back" already existed and had one user.

| Gesture | Entry |
|---|---|
| first turn of a run on one cell (ENGINE / ENV) | `("param", subpage, idx, shift, name)` — the run joins it, the next cell starts a new one |
| a turn on an FX or MOD tab's cell | `("fx"/"mod", tab, idx, shift)` |
| a MOD route pasted onto a target | its own entry, rolled back if the paste is refused |
| each browser opened on the page (SHIFT+1/3/4, SHIFT+slot) | **one entry per browser**, `("menu", …, object())` |
| DRUM's LIFT/DROP pad copy, both tape → instrument drops | its own entry, rolled back on refusal |

Not an edit: playing a note, the SHIFT+‹ › bend, changing subpage or tab, and
since #152 the `‹ ›` **octave** — see entry 81.
**Cleared** by a slot recall, FILE › NEW and a project load — each replaces the
sound the entry was taken from, and restoring an entry across one of those would
be copying a sound into a different slot rather than undoing anything.

**The push is at the gesture, never inside `load_engine` / `load_patch`.** Those
two are also called by `restore`, `reset` and `_load_default_engine`; a push in
there would record an entry in the middle of a project load, and `load_patch`
calls `load_engine`, which would overwrite the browse's own snapshot with a
half-loaded sound. A browser applies live as you scroll, so its entry is taken
at the **first pick**, under a group unique to that menu instance — a browser
opened and closed without moving records nothing at all.

| # | Spec | What was done | Where |
|---|---|---|---|
| 77 | DDR-054 does not say what a press with nothing to undo does | `NOTHING TO UNDO` in the dynamic strip. The tape's undo says nothing when it has nothing, because on TRACK the clip list is on screen and you can see that nothing moved; here the sound would simply not change, which reads as a dead key. `[TBD-Elfy]` | `pages/synth.py` `undo_edit` |
| 78 | `tape-sampling-workflow.md` § Gestures: "on SYNTH/DRUM it undoes the last edit there **instead**" | the page owns the gesture whether or not it has an entry — there is **no fall-through to the tape** on those two pages, and the page *under* an open browser owns it too (`app.pages[page_id]`, not `current_page`), after the browser's own lift group. Falling through would mean the first SHIFT+STOP after arriving on SYNTH silently spends the tape's one level, which is the bug this ticket is about seen from the other side | `app/app.py` `_transport` |
| 79 | not in the spec | **the transport does not stop.** The tape-undo branch sends `STOP` to the host on its way out; this one returns early, like the browser's lift-group branch. The page said "undo", not "stop", and stopping playback to undo an envelope tweak is a surprise you can only find on stage. `[TBD-Elfy]` | `app/app.py` `_transport` |
| 80 | not in the spec | **undo restores the sound, not the view.** `restore` is a slot recall and carries the subpage with it, which is right when a whole slot arrives and wrong here: you undo a turn while looking at the page you turned it on. DRUM's pad is not restored either — it follows `instrument.last_note` and heals itself | `pages/synth.py` `_restore_edit` |
| 81 | DDR-054 lists "a run of turns on one param, a module swap, or a patch load" | ~~the octave is an edit too~~ — **the octave is not an edit** (#152, Elfy 2026-09-23, overruling this session's call). It is a performance gesture like the bend, so a move records no step and SHIFT+STOP after one undoes whatever was edited before it. It still *rides in the page's snapshot*, which is the other half: a move calls `EditUndo.amend(octave=…)` on the entry already open, so that undo puts the sound back and leaves the octave where the player put it. A patch load is unaffected — the octave it carried is part of that sound and comes back with it | `pages/synth.py` `on_key`, `app/edit_undo.py` `amend` |

**581 emulator tests** (560 before) and **33 verify features** (32 before).
`tests/test_edit_undo.py` is 8 unit tests on the holder and 17 against a running
host (6 and 15 before #152), every value read out of
`native.instrument(...)['env']` rather than off a `Param`; the `edit-undo` feature (4 shots) is the user path. The stale sentence
in `control-inventory.md` § LIFT/DROP about restoring a drum pad's previous
sample "after a per-pad record" is gone — there is no such gesture. No pixel
moved, no baseline promoted, `ui/palette.py` and `ui/font.py` untouched.

## S13 — one sequencer state per instrument (2026-09-23, #59)

`audio/`'s §15.4 has kept a **SEQ slot per instrument** since N1 and clocks only
the active one. Up here there was **one** `NativeSequencer` with an `inst_id`
field, so arming it on DRUM moved SYNTH's sequencer across — the riff left on
SYNTH was not parked, it was gone, because there was nowhere for it to be.
DDR-034's "switching SYNTH↔DRUM stops the running one and **resumes the other
if it was armed**" was a sentence nobody had built (`v1-spec-package.md`
§ Inspection item 6, decided 2026-09-18: build it).

**`App.sequencers` is a dict keyed by instrument and `App.sequencer` is a
property returning the active one's.** That is the whole shape of the change,
and it is why it is small: every existing reader — the `SEQUENCER → X` ticker,
`on_note`, the SEQ key's arm gesture, `notify_param_changed`, `tools/verify.py`
and ~40 assertions across six test files — already meant *the sequencer the
player is holding keys for*, which is exactly what the property returns. The
setter writes to the active instrument, so `self.app.sequencer = engine` still
reads correctly; the places that mean a **specific** instrument index
`app.sequencers[…]` and say so.

**A sequencer's instrument is fixed at construction.** `NativeSequencer(...,
inst_id=…)`, set by `NativeLibrary.instantiate(lf, sr, inst_id=…)`. Two things
stopped consulting the host's *active* flag to decide whose slot they were on:

- **`_set_loaded` no longer picks an instrument.** It armed on "whichever is
  active now and stays there", which is precisely the mechanism by which
  SYNTH's sequencer ended up on DRUM.
- **`restore` no longer reads the snapshot's `instrument` key.** The key is
  still *written* — it is worth having in a state file and in a post-mortem —
  and is deliberately ignored, because a SEQ slot letter is a **sequencer-only**
  slot (map § Track S row S13): a snapshot taken on DRUM and recalled while
  SYNTH is showing belongs to SYNTH. The object already knows its instrument.

**`SeqPage` builds one per instrument at boot** (`INSTRUMENTS`), and nothing
reaches the host doing it: `instantiate` on a `seq` folder only makes the proxy,
and the `init.seq` applied to it pushes nothing while the slot is empty.
`load_engine`, `_load_default_engine`, `sync_from_host` and `reset` each name an
instrument, defaulting to the active one.

**The guard in `sync_from_host` is a bug fixed, not a line deleted.** It read
`if seq.active and seq.inst_id != inst_id: return` — "the UI's sequencer is the
other one's". With one object that was the only honest answer, and it also meant
a patch or slot recall applied to the **parked** instrument left that
instrument's mirror stale for ever, because nothing would ever come back for it.
With one sequencer each, the call operates on `inst_id`'s own and the question
does not arise.

### The two things the second sequencer exposed in `project_file`

- **`apply` sets `active_instrument` before the slot loop, not after it**, and
  walks `_SLOT_PAGES` in order rather than the document's key order. The SEQ
  page's bank is a sequencer-only slot applied to whichever instrument is
  active, so a project saved on DRUM used to come back with its SEQ bank on
  whatever the player happened to be standing on when they pressed LOAD. SYNTH
  and DRUM address themselves by `instrument_id` and do not care when it
  happens; it is set again after `goto`, because a page key *is* the instrument
  selector (DDR-034).
- **`promote_pattern` promotes both instruments' entered patterns.** DDR-037's
  scratch file does not survive the session, so a project saved with a riff on
  each instrument was losing DRUM's. The two may not share a filename, so each
  is `<project> <instrument>` — `proj0001-synth.seq`, `proj0001-drum.seq`.
  Symmetric rather than "SYNTH keeps the project's own name": neither
  instrument's riff is the project's real one, and the browser's row then says
  which instrument it came off. `SynthPage.on_shift_page_key` promotes **this
  page's** instrument's sequencer for the same reason — the two are the same
  object on every real call, which is exactly why being explicit is what stops
  the next reader getting it wrong.

**587 emulator tests** (581 before) and **34 verify features** (33 before).
`tests/test_seq_patterns.py::S13Case` is six tests, each reading the **host's**
slot back as well as the mirror, because "two states" is only true if the host
holds two. The `seq-per-instrument` feature (6 shots) is the user path;
`05-synth-as-left` is the ticket in one picture. No pixel outside that feature's
own evidence moved, no baseline promoted, `ui/palette.py` and `ui/font.py`
untouched.

## S14 — entering a stepmod pattern (2026-09-23, #60)

`modulation-spec.md` §3.2 said a stepmod's values were "entered like an endless
pattern: hold SHIFT, turn ENC1 for the value, › to step". Nothing in `emulator/`
ever wrote one, so every stepmod on the device ran the box's default ramp. The
gesture is now Elfy's (ticket #60, 2026-09-18): **two encoder parameters**, not a
mode. §3.2.1 of the spec is the as-built note; this is what it cost here.

**The decision the ticket asked for: STEP and VALUE are the UI's parameters, not
the box's.** The box declares five and leaves slots 5–7 empty; `ModRackView`
appends its own two after them (5 = STEP, 6 = VALUE) and they never reach
`set_param`. The alternative — real box parameters — needs a `set_param` path
that mutates the pattern, and DDR-037 says a pattern is **content**: the box
would then hold the same numbers twice and a patch would have two places to
disagree about them. The UI edits the asset and hands the file back, which is
the same shape stage H2 gave the SEQ pattern.

**It is offered by capability, not by name.** `_takes_pattern` asks whether the
tab's box declares the `pattern` asset, which among stock modulators only
`arbutus/stepmod` does. One thing is named: `STEPS_PARAM = "steps"`, because
STEP has to know how far it scrolls. A box with the asset and no `steps`
falls back to 16; a box with the asset and seven parameters gets no gesture at
all rather than losing a knob to it.

**The push is one a frame, in `SynthPage.tick` and not in `render`.**
`set_mod_tab_engine` rebuilds the tab and crossfades, so pushing per detent
would rebuild the graph tens of times a second; `_pattern_dirty` holds the tab
index and the frame hook flushes it. `tick` rather than `render` because
content reaching the box is not a drawing concern and a page that is not on
screen still has to finish what the player started on it — a test or a verify
script needs one `wait_blocks`/`app.tick()` between the turn and the assert.

**What is entered is what is written.** The file holds exactly STEPS values.
The box wraps a shorter list (`values[i % len]`), so a loaded file need not
match; the first turn of VALUE materialises the wrapped list, which does not
change what it sounds like and stops the picture disagreeing with the file.

**The picture is a fixed sixteen cells** with only the first STEPS of them
carrying a bar, drawn beside the outputs. Fixed, because a grid that resized as
you turned STEPS would move the step you were aiming at.

### The one host change it needed (`audio/host/PROTOCOL.md` § MOD rack)

There was no way to put content on a MOD tab that already existed: `add_mod_tab`
takes `assets`, `set_mod_tab_engine` did not and cleared the tab's params,
sidechain and assets unconditionally. Remove-and-re-add was not an option — it
takes that source's routes with it, so every turn of VALUE would have deleted
the player's routing. `set_mod_tab_engine` now follows `load_engine`'s rule
instead: a **different** engine clears params, sidechain and assets; the **same**
engine keeps them and takes `assets` if the message carries it. Both existing
callers swap to a different engine, so nothing changed under them.

### Where a promoted pattern lives

`mod/<box>/<slug>.stepmod`, written by `project_file.promote_step_patterns` on
SAVE and by `SynthPage.on_shift_page_key` (with `only=` this instrument) when a
patch is promoted — the same two moments that promote a SEQ pattern, for the
same reason: a patch carries the MOD rack by value, so it would otherwise name
a scratch file that dies with the session. The folder is the box's own row in
the MOD browser and **the file is not listed in it** (`rescan_items` keeps
`.patch`, `.rack` and `.seq`), which is deliberate: it is content a patch names,
not a modulator you can drop on a tab. Format: `audio/library/README.md`
§ Step patterns.

## C5b — the render levers (2026-09-23, #98)

C5 (#78) measured the UI's frame on the CM4 and found five changes to how the
screen is drawn that cut it from **52 % to 17 % of a core** with **no pixel
moved** — 56,321 frames compared byte for byte. It applied them as run-time
patches inside its own bench. This is them, in the code.

| lever | where | what it is |
|---|---|---|
| `fill` | `ui/draw.py` `fill_rect`, `plot` | no `np.array(color)` per call; a rectangle filled from a **cached one-row template** of the colour. NumPy broadcasting a (3,) value over an (h, w, 3) slice is a strided element-wise loop; assigning a (w, 3) row into it is a memcpy per row |
| `text` | `ui/draw.py` `text` | one boolean **ink mask per (string, size)**, one masked assignment, instead of `font.draw_text`'s clipped blit per character. `draw.text` no longer delegates to `font.draw_text` at all |
| `line` | `ui/draw.py` `polyline` | the same integer Bresenham; the in-bounds points collected and written with one fancy-index assignment. `_line` takes the accumulator rather than calling `plot` |
| `cols` | `ui/draw.py` `peaks` + the new `columns`, `pages/input.py` `_draw_meter` | the same integer arithmetic once over the whole width, as a column mask |
| `wave` | `app/native_audio.py` `waveform` | cached until the host pushes anything but `meters` |

`draw.columns(fb, x, top, length, color)` is the new primitive — "what a `vline`
per pixel column would draw" — so the waveform envelope and INPUT's level meter
share one implementation instead of each growing a mask. The sixth lever is the
P3 panel driver's and is `arbutus_os/AS-BUILT.md`'s.

### Where the text cache lives, and who drops it

`ui/font.py` is frozen (hard rule), so the cache is in `draw.py` around the call
it replaces. The ticket offered `font.dirty()` / `spacing_dirty()` as the key and
they are **the wrong instrument**: both compare against a `_ORIGINAL` snapshot
that `font.mark_clean()` *moves*, so after a writeback a genuinely-edited glyph
reads clean again — and asking them per frame is ~100 `array_equal` calls at
60 Hz. So the ticket's other option: **`draw.invalidate_text_cache()`, called
from the editor**. `tools/edit_server.py` is the only thing in the build that
mutates a glyph or an advance, and it calls it in a **`finally`** over the whole
POST dispatch rather than per branch — a POST there is human-paced, and a branch
added later would otherwise leave the live preview showing the old glyph, which
is the one bug this cache can cause.

**Both caches are keyed on values**, so `palette.set_color` needs no
invalidation: a rebound colour asks for a different key. Each has a ceiling (256
colour rows, 4096 masks) and is dropped **whole** rather than evicted — a page
that draws a fresh string every frame must not grow the process, and an LRU here
would cost more than the blit it is protecting.

### `waveform`'s cache has a generation counter

`waveform` calls `send`, `send` calls `_absorb_pushes`, and `_absorb_pushes` is
where the cache is dropped. So a reply already in flight when the push arrived
could be stored *on top of* that drop and be stale until the next one.
`_wave_gen` is bumped by every drop and the reply is stored only if it has not
moved. The cache is dropped on `reconnect()` too: § 4.2's host that comes back is
a fresh one at boot defaults and holds none of the samples.

### The claim stays falsifiable

A lever that is "pixel-identical" is worth nothing once the thing it was
identical *to* has been deleted. **`testing/render_reference.py`** holds the
pre-C5b bodies verbatim and nothing imports it at run time;
**`tests/test_render_levers.py`** (24) draws every call both ways and asserts
`np.array_equal` — randomised rectangles, plots, polylines and envelopes with
every off-screen and degenerate case, every authored character at both sizes,
text clipped past all four edges, 150 random meter histories, and the two
live-edit paths. Its last class is the **negative control**: three deliberately
wrong calls that the same assertion catches, because S15 (#56) had just finished
showing what twenty-four tests that cannot fail look like.

`_meter_scale`'s `**0.5` became `np.sqrt` — both correctly rounded, so the same
double and the same `int()`, and the 150 randomised meters are what says so
rather than the argument.

**622 emulator tests** (598 before), 35 verify features unchanged and diffing
clean, no baseline promoted, `ui/palette.py` and `ui/font.py` untouched. Local
A/B through the real offline host, nine pages: median render **1.606 → 0.513 ms,
3.13×**, every frame identical; per lever fill 2.09×, text 1.62×, cols 1.23×,
line 1.13×, wave 1.07×. The CM4 numbers are still C5's — `tools/c5-bench.sh`
needs the board, and re-running it is left for a bench session.

## #99 — the transport is the host's, and this UI follows it (2026-09-24)

`arbutus-audio` serves several clients at once (`audio/host/PROTOCOL.md`
§ Playing, the rule added by this ticket): the instrument's own UI on the Unix
socket and, since D2, the companion app on TCP 7401. The transport is *its*
value; this UI draws a mirror of it, exactly as it does every other value.

It did not. `App._native_sync` re-asserted its own copy:

```python
if not tp.playing and abs(tp.playhead - native.tape_pos) >= 1.0:
    native.transport("locate", tp.playhead)
```

Anything else that rolled the tape moved `tape_pos` (a `meters` push, 30 Hz)
while `tp.playing` stayed False, so every frame sent `locate` back to where the
UI still thought the playhead was — and `transport` is in `native_audio.STATEFUL`,
so each one fetched the whole `state` document with it. C5 (#78) measured the
cost as 0.5–3 blocking round trips a frame in its `load` rows, 3–6 ms of a
16.7 ms frame on the CM4, and the tape never got anywhere.

### What was built

**`App._adopt_host_transport`**, first thing in `_native_sync`. It reads
`playing` and `tape.recording` out of the host's own document and adopts a
**change** there — never the mere value. That is the whole subtlety: the state
document is fetched synchronously after each of our own stateful messages and
pushed after every edit, so "the host says stopped and we say playing" is the
*normal* one-frame appearance of our own gesture, and adopting on the value
would make the UI stop itself. `_host_playing` / `_host_recording` hold the last
values seen, seeded `False`/`False` — a fresh host's transport — so a UI that
starts against one already rolling adopts it on its first sync.

A frame on which this UI moved the transport itself belongs to the UI: if the
live `(armed, recording, playing, selected)` tuple is not the one the last sync
sent, adoption returns and the ordinary diff goes down. What is adopted updates
that fingerprint in the same breath, so nothing is sent back.

**The `locate` is now sent only for a playhead this UI moved.** `_playhead_seen`
is the playhead as the last sync left it; a difference is what "the UI moved it"
means (a seek, a scrub, an edit, a rewind), and that is what a `locate`
announces. When the two disagree and the UI did not touch its own, the host is
right and `tp.playhead` follows. `None` means "not known" — before the first
sync, and after `project_file.resync` forgets the fingerprints, for the same
reason it forgets those: a load moves the playhead, and a value that happened to
land back on the remembered one would be read as the host's and adopted away
instead of sent.

**`Tape.follow(playing, position)`** is the adopting half, and it is deliberately
not `play()`/`stop()`:

- a roll adopted from the host is **PLAYING, never RECORDING**. Recording is a
  gesture of this UI's own (REC, or REC + a note), and nothing else in v1
  punches in — the companion app is a status mirror plus file jobs
  (`companion-app-spec.md` § 1). If some later client does record, the UI does
  not fight it: it draws the roll, and the take reaches the clip model through
  the `take` push it already drains.
- a remote **stop** ends an open take of ours exactly as STOP does — armed
  cleared, REV let go, the take closed, the clip taken through
  `_native_adopt_clips` — **minus the rewind**. `Tape.stop()` puts the playhead
  back to `loop_in` when it is *already* stopped (STOP twice is the rewind); a
  remote stop leaves it where the host left it, because the host's position is
  the fact being adopted.
- the count-in is cleared. A punch pending on a frame is this UI's, and it has
  no meaning once somebody else has taken the transport somewhere.

### The tests, and that they were seen red

`tests/test_native_tape.py::TestAnotherClientOwnsTheTransport` opens a **second
connection to the same host** — `arbutus_ctl.connect(native.sock)`, which is
what the phone is — and rolls, stops and locates the tape from it, with every
`transport` this UI sends recorded by a spy. Unfixed, the roll case leaves
`tape.playing` False and sends `("locate", 0)`; the locate case leaves the
playhead at 0 while the host sits at 48,000. The third test is the other half of
the rule and passes both ways on purpose: what the UI moves itself is still
located, and **once** — the following frames are silent.

**639 emulator tests** (636 before), no pixel, no baseline, 35/35 verify
features.

## #68 — the MOD rack's K tab (2026-09-24)

Audit Q1 (`docs/design/audit-2026-09-18.md` § P2-2) found the one modulation
source that is not a box unreachable from the device. `modulation-spec.md` § 3.2
lists `arbutus/keys` with outputs VEL and NOTE and says it is "always present as
tab **K** on the master row and not counted in the four"; § 8 and § 10 route from
it; DDR-048 names it. The **host already served it** — `resolve_route` in
`audio/host/rack.cpp` accepts `source == "keys"` with `output` `vel` or `note`,
and § 8's per-voice case crosses the ABI as `set_voice_mod`. What did not exist
was a way to press it: `ModRackView`'s tab row ran master → the four modulators →
the empty tab and stopped, so no `keys.VEL` route could be made without editing a
project file by hand.

**What the tab is.** Not a tab index. The four modulator tabs are `self.tab` 1..4
with `tab - 1` indexing the rack document, and K indexes nothing, so it carries a
sentinel (`MOD_KEYS = 9`) and every guard that reads `tab - 1` asks `on_keys_tab`
first. That is why it is "not counted in the four" in code as well as on screen:
`n_tabs`, `settle()`'s clamp, `add_mod_tab` and the browser are all untouched by
it, and a rack with four modulators still has no fifth empty tab.

**What it shares with a modulator tab**, deliberately, so the gesture needs no
second explanation: LIFT puts it in `copy_source` mode, the bottom row relabels to
its outputs (VEL, NOTE, `—`, `—`), turning ENC n grabs output n, and the strip says
`TURN AN OUTPUT TO GRAB IT` (§ 10). The payload is `("keys", "vel", "KEYS.VEL")`,
the same shape a box's lift makes, so `route_to` and the host need no change at
all — the copy-mode cells are now one helper (`_output_cells`) used by both, and
`tab_outputs()` is the one place that knows a keys tab's source is a string the
host answers to rather than a tab instance.

**The proof** (`tests/test_mod_rack.py`, five new tests). The one that matters
routes `keys.VEL` → `engine.index` entirely through the input seam and then plays
the same note twice at two velocities: the live value from `meters.mod` moves by
`depth × Δvelocity` (0.25 × 0.8 = 0.20), which is § 6.2's default depth applying
§ 8's last-note velocity. Its **negative control** is the first half of the same
test (#56): before the route exists the target has no live value at all, so the
pass cannot come from the meter moving on its own. `App.on_key`'s third argument
is the velocity — `ScriptedInput` has no way to say it and P3's keys are switches
anyway (§ 8: velocity comes from MIDI, or from the case jolt with an IMU), so the
test names the seam directly rather than pretending a scripted key press has a
velocity.

`test_four_tabs_have_no_fifth_empty_tab` grew the other half of its claim: a full
rack has K past tab 4, and nothing past K.

**657 emulator tests** (652 before). No baseline promoted; `ui/palette.py` and
`ui/font.py` untouched.

## #69 — TRACK's ENC3 is the song tempo while LINK is on (2026-09-24)

Audit Q1 (`docs/design/audit-2026-09-18.md` § P2-3) found DDR-059 half built.
`tape-sampling-workflow.md` § 2.3 says BPM and tape speed are **one control**
while `LINK` is on — "turning either moves both" — and only one direction
existed: BPM dragged the tape (the host's link factor, `tempo_ / link_bpm_`),
while TRACK's ENC3 sent `tape_speed {speed}`, the varispeed that multiplies *on
top of* LINK, and never touched BPM. Two turns and the pair disagreed: the tape
ran at `bpm/base_bpm × speed` and the grid at a tempo nothing on this page had
moved.

**What it is now.** While LINK is on, the ENC3 cell is `BPM` and one detent is
1 BPM. The turn goes through **`App.set_bpm`**, which is the whole fix: it is
already the one door the TEMPO page's ENC1, the `‹ ›` nudge and tap tempo use, so
routing here buys the host message (`tempo {bpm}`), the TEMPO page's own
parameter staying in step, and the MIDI-clock lock, without this page learning
any of it. With LINK off the cell is `SPEED` again and moves the varispeed only,
which is § 2.3's other half and was never broken.

**Three things left alone on purpose.**

- **SHIFT+ENC3 is still the fixed SPEED steps** (×0.25…4) with LINK on or off.
  `PROTOCOL.md` § Playing the tape has SPEED and REV multiplying on top of LINK,
  so the varispeed is a clip effect and not a second tempo; putting BPM FINE
  there instead would have invented a control the spec does not name. The
  consequence to know: with LINK on, a varispeed other than ×1 is readable in
  the SHIFT row's `STEPS` cell and not in ENC3's.
- **The SPEED *key* is untouched** — DDR-010, a clip effect, a different gesture.
- **`LINK` is still read, never copied.** `TrackPage.link` asks the TEMPO page,
  which reads `state.tape.link.on` off the host every frame (E1). One fact, one
  home, two pages.

**Under a MIDI master** the cell is named, dim and inert here exactly as it is on
TEMPO (`Param.locked` + `dimmed`, DDR-053's greying). The lock would hold anyway
— `App.set_bpm` goes through `TempoPage.set_bpm`, which refuses under
`external_sync` — but a knob that silently does nothing reads as broken.

## #164 — the UI's half of a compaction (2026-09-25)

C1 step 8's other half (`docs/design/tape-streaming-design.md` § 3.8, § 6 step
8). The *medium* is the file one tape track's recorded audio lives in. It is
append-only, so a retake, a LIFT with no DROP and every `tape_edit` leave audio
nothing plays; `tape_compact` copies out only the spans the UI says to keep and
pushes back a **map** of where each one landed. The host built that in #74; this
is the side that says what to keep and moves its own geometry afterwards.

**Why the keep set is the UI's at all.** Two references never reach the audio
process: the **lift buffer** (what LIFT took off the timeline and DROP puts
back) and the **one-level undo record** (DDR-054/055). Neither is a clip, so
neither is in any clip list the host has. Compact the audio one of them points
at away and pressing undo brings back a clip that plays somebody else's take.
That is T9, and it is a UI test by § 4's own table.

| What | Where |
|---|---|
| the keep set — every clip on every track reading this medium (`src_track`), the lift buffer, the undo record; sorted and coalesced | `app/tape.py` `Tape.keep_spans` |
| the references themselves, by object identity | `app/tape.py` `Tape.medium_clips` |
| the remap: `from + d` → `to + d`, and the orphan count | `app/tape.py` `Tape.remap_medium`, `Tape.compact_orphans` |
| the push absorbed, remapped, clip list forced down | `app/app.py` `App._native_compact` |
| the queue (the host runs one job at a time) | `app/app.py` `App._compact_next` |
| § 3.8's SAVE trigger | `app/app.py` `App.compact_dead_media`, called last in `app/project_file.py::save` |
| the message | `app/native_audio.py` `tape_compact`, `on_compact` |
| a `--tape-dir` for a test | `app/native_audio.py` `NativeAudio(extra=[…])` |

### Four things § 3.8 did not say

1. **Identity, not address.** `do_lift` keeps the clip it removed **and** the
   copy DROP lays down — two different objects at one address, both real
   references, both to be moved. `do_lift_all` puts the **same** object in its
   `clip` and in its `clips`, and moving that one twice displaces it twice. One
   `id()` set settles both, and the two cases are each other's control in
   `test_tape_compact.py`.
2. **An uncovered reference is parked at the end of the new medium, never
   clamped into it.** Clamping lands it on whatever audio is at that offset now,
   which is the exact outcome § 3.8 exists to prevent, arriving through the fix
   instead of the bug. Silence is the honest answer, and the count
   (`compact_orphans`) says the keep set missed something. Coverage must be
   **whole** for the same reason: a reference whose head is in a kept run and
   whose tail is not would have its tail land on the next run.
3. **The host takes one job at a time** (`BUSY`), so "for every track whose dead
   fraction is over half" is a queue, not a loop of sends: SAVE hands over the
   first and each result push hands over the next. The running track is recorded
   **before** the request goes out, because offline there is no I/O thread — the
   copy runs inside the request and the push re-enters `_native_compact` before
   the reply returns. Recording it afterwards leaves a finished track marked
   running and blocks every later compaction; that is its own test.
4. **The trigger needs a "is there a medium at all" guard.**
   `state.tape.pool.ranges` is 0 exactly when the tape is not on storage, which
   is every stock emulator until C1 step 3b passes a `--tape-dir` (#157 owns
   where a tape file lives). Without the guard every SAVE would send a
   `tape_compact` that is refused `NO TAPE DIR` — a refusal in front of a player
   for a feature that is not there yet.

§ 3.8's **other** trigger, the head passing *k* × ruler, is deliberately not
built: a medium cannot exceed the ruler, so *k* can only be 1 and the state it
watches for does not exist (**#168**, Elfy's). The dead-fraction trigger on SAVE
survives either answer to #168 — #168's own option 1 keeps it by name — which is
why that one was built and this one was not. Nothing here decides #168.

### The tests, and what the rig taught

`tests/test_tape_compact.py`, 24 tests in three rigs: the keep set and the remap
arithmetic on `Tape` alone (pure, no host); the trigger and the queue on a
stand-in with the real `App` methods bound to it, the way `test_tape_flags.py`
does with `_absorb_pushes`; and T9 end to end against a real offline host given a
`--tape-dir` of its own. **Twelve mutations, each caught** — among them a keep
set that forgets the lift buffer, one that forgets the undo record, a clamp in
place of the park, head-only coverage, no identity dedupe, the threshold at half
rather than more than half, the running track recorded after the send, and SAVE
not triggering at all.

**The audio claim cannot be made by rendering the transport.** The first version
of T9 dropped the lifted clip, played it, compacted, dropped it again and
compared — and the control (an empty timeline) rendered a **0.22 peak**, because
the engine's own release tail is still sounding. Whatever that test had compared,
it would not have been the tape. The claim is made instead on a copy of the
medium taken before the compaction (`tape_save`, float32, so the chunks are
walked by hand — `wave` will not read format 3) and on `clip_peaks`, which reads
the medium and nothing else. That also crosses #163's peaks sidecar, which a
compaction drops and which heals through the fetch path.

**What is left.** The whole path can only be driven end to end from a test until
**step 3b** makes the shipping emulator pass a `--tape-dir`; in a stock emulator
today `compact_dead_media` returns `[]` on its first line and nothing else here
ever runs. **686 emulator tests** (662 before). No pixel changed, no baseline
promoted; `ui/palette.py` and `ui/font.py` untouched.

## #169 — one `clip_peaks` message for a bank (2026-09-25)

#163 made the host answer a clip's waveform out of RAM, and what was left of a TRACK redraw
was **the message**: on the CM4 a `clip_peaks` round trip costs **2.1–2.5 ms even when the
host does no work at all**, and a redraw asks once per clip drawn. Four of those is ~9 ms of
a 35 ms frame.

**`TrackPage._prefetch_peaks` sends one message for the whole bank**, before the first lane,
and the draw path then reads `_peaks` exactly as it did. The draw path is unchanged on
purpose — the prefetch is a cache fill, not a second way of drawing — which is what the
test asserts: the batched frame is **pixel-identical** to the one the lone fetches draw,
with the prefetch switched off as the control.

Making that possible meant factoring the drawing into three pieces that were already there
implicitly:

| | what it is |
|---|---|
| `_clip_rects(t, cx, cw)` | the lane's on-screen clips with their un-clipped `x0`/`x1` (the clip's own width, which is what the waveform maps pixels through) |
| `_wave_key(clip, track, …)` | the geometry the waveform is asked for, `None` when it asks nothing — and **already the cache key**, so nothing new was invented |
| `_peaks_req(track, key)` | the request dict, one definition, so the batch and the lone fetch cannot drift apart |

**A batch the host refuses changes nothing.** `clip_peaks_batch` returns `None` on any
refusal, on a reply whose `peaks` is the wrong length, and it chunks at
`PEAKS_BATCH_MAX` = 64 because the host builds a reply whole in memory; `_prefetch_peaks`
then simply returns and `_clip_wave` asks for itself, one clip at a time, which is the path
that was there before. A frame with fewer than two fetches to make sends nothing — one fetch
is not a batch.

**The take is deliberately not in it.** `_render_take` reads a region that is growing and is
never cached, so batching it would mean batching a request whose answer is stale by
construction.

**687 emulator tests** (686 before). No pixel changed, no baseline promoted;
`ui/palette.py` and `ui/font.py` untouched. What the host does is
`audio/CLAUDE.md` § Defaults (`clip_peaks`) and `audio/host/PROTOCOL.md`; the numbers the
CM4 owes are **#171**, and the reply's *precision* — measured, refused, 8 shots move — is
**#172**.

## #174 — `goto` on an id that is not a page raises (2026-09-25)

`App.goto(page_id)` returned without a word when `page_id` was not a page. #173's first
end-to-end test drove the UI to `"MIX"` — the page is `MIXER` — and its assertion on
`GET /status`'s `page` passed **for the wrong reason** until the test read the autosave
document back.

It raises `ValueError` now, naming the id and the sorted set of real pages: the App's own
refusal idiom (`__init__` on a missing audio process). Nothing in production passes a
literal that is not a page, so the point is not the bug it fixes but the bug it makes
loud — **`page` is a contract with another process** since #173 (`arbutus-net` reads it off
the autosave snapshot and publishes it on `GET /status`, `device-os-spec.md` § 9.11), and a
silent no-op is the shape a rename produces with nothing going red.

**What had to be checked before raising**, because it is the one way a bad id reaches `goto`
in the field: the two call sites that pass an id **read from a file** — `persist.load` and
`project_file` — already guard with `if page_id in app.pages:`, so a project or autosave
document written by another build falls back to the current page exactly as before rather
than raising. The other three pass a literal (`goto("HELP")`, `InputPage`'s `goto("SYNTH")`)
or the key the very next line already indexes `self.pages` with, which would have raised
`KeyError` anyway. The guard changes no reachable behaviour.

The two tests sit with the navigation tests already in `tests/test_edit_feel.py`. The
control iterates `sorted(app.pages)` rather than a written-out list, so a page added later
is covered without anyone remembering. Mutation run, not assumed (#56): against the old
`return` the alarm fails `ValueError not raised` and the control stays green.

**693 emulator tests** (691 before). No pixel changed, no baseline promoted;
`ui/palette.py` and `ui/font.py` untouched.

## Audit Q3 — 2026-09-25 (#31): the Files area, read in full

`docs/design/audit-2026-09-25-q3.md` is the record; three things changed here, none of
them a pixel, and every fix was seen red against the old code first (#56).

- **`App.usb_exported` is a real attribute now** (False; `arbutus_os/main.py`'s `UsbPump`
  sets it) and it gates the whole working-state autosave: the slot stores' ticks and the
  2 s `project.json` timer in `App.tick`, `App.flush`, and `ProjectStore.save_project` /
  `save_audio` / `tick` (`persist.exported()`). `device-os-spec.md` § 11.1 unmounts `/data`
  in storage mode and the state directory is `/data/.state/`, so every one of those writes
  landed on the bare mount point of the OS partition and was hidden by the remount — a STOP
  during an export put every dirty track's medium there. Dirty flags keep their memory, so
  the first tick after the remount writes what was pending. On a laptop the flag is never
  true and nothing changes. `tests/test_persist_dirty.py::WhileExported`,
  `tests/test_file_system.py::TestWorkingStateWhileExported`.
- **A refused write is a line and a retry, never an exception out of `tick`.**
  `persist.py`'s two writers and `slots.SlotStore.flush` used to raise on any `OSError`; on
  the device that is `/data` full or read-only, `main.py` catches nothing out of `App.tick`,
  and the unit's `Restart=on-failure` turned it into a crash loop. They now print once per
  distinct reason (`persist: cannot write project.json — retrying (…)`) and keep the dirty
  state, as `Settings.save` already did. `tests/test_persist_dirty.py::RefusedWrites`.
- **A dot-prefixed file is not content** — `native_library.py`'s three file listers
  (`rescan_items`' file branch, `_racks`, `_racks_mod`) skip `._name.patch` and friends, the
  AppleDouble files a Mac leaves beside every document on an exported `/data`; each was a
  row that did nothing when picked. `tests/test_file_system.py::TestDotFilesAreNotContent`.

What the audit read and found consistent — DDR-055's gestures as the host's `library {op}`
with only the gesture layer here, the README's three formats field for field, `project_file`'s
save order and S13's apply order, `next_name` across the tree, `SlotStore`'s six letters — is
the audit's § Obs-5. The 82 `[TBD-Elfy]` entries in this file's tables are pointed at, as one
block, from `docs/design/v1-spec-package.md` § `[TBD-Elfy]`, which is now the one list.

**711 emulator tests** (703 before). No pixel changed, no baseline promoted; `ui/palette.py`
and `ui/font.py` untouched.

## D17 — what the phone is told about the screen (2026-10-03, #276)

`app/ui_feed.py` publishes two of the UI's facts to `arbutus-audio`, which relays them
read-only to the companion app (`audio/host/PROTOCOL.md` § The screen and the player's
actions): the **element map** (the frame's fixed zones under stable ids — `header`,
`strip`, `main`, `param.0`–`7`, `key.1`–`4` — labelled with what the cell says, never its
value) and the **actions** (`key`, `encoder` with the parameter it moved, `page`, `rec`).
`App.on_key`, `on_encoder` and `goto` record; `App.tick` flushes once a frame, at most two
messages and none when nothing changed. `NativeAudio.publish` is `send` without the
player: a refusal never reaches the strip and a link that is down is not tried. A page's
main area is one element (`main`) until a lesson needs finer ids — that is where a page
would grow an `elements()` of its own. No pixel moved, no baseline; `ui/palette.py` and
`ui/font.py` untouched.

**`tests/test_lessons.py` (#291) plays every Learn lesson through this feed**: a script per
lesson on a fresh offline instrument, the phone's view recorded into
`app/ios/ArbutusTests/lesson-runs.json`, and the run compared against it, so a page change
that moves what a lesson relies on fails here with a short diff
(`ARBUTUS_WRITE_LESSON_RUNS=1` rewrites it). Two things the feed does that a lesson writer
meets: the map goes out **before** the frame's actions, so an action is labelled with the
screen after it (a page key is already "on" its page); and with HELP held an encoder turn
is not published at all.

**#412 (the t276 review's findings, 2026-10-04).** A tick with more than 64 actions goes
down as several `ui_action` messages of at most 64 (`ui_feed.MAX_ACTIONS`, the host's
limit): `uart_input.step()` dispatches everything queued during a stalled frame in one
tick, and the host used to refuse the whole batch after the UI had cleared it. The
`strip` element's label is now always empty — the strip's text is a value (`SPEED ×1.20`,
`BATT n%`, every flash), and as a label it republished the map on every detent — so
`_strip`, a copy of `ui/frame.py`'s `_strip_text`, is gone; `test_lessons.py`'s trace
calls `_strip_text` itself. *Run's call, 2026-10-04*: no separate field carries the strip's
text, since nothing on the phone reads it. Tests for `rec` and the republish after a host
restart, each mutated red.

**#413 (the t276 review's finding 6, 2026-10-05): the map is built when something moved
it, not every tick.** Measured on the bench CM4 (`arbutus_os/bench/p413-pi.sh`, the feed
timed or patched out around `main.py --stats`, two rounds that agree): `flush` cost
**170 µs on every idle tick** (p50; the map 80 µs, its `repr` and compare the rest), which
was the whole difference between the UI with and without the feed on a page walk — frame
work p50 3.06 against 2.90 ms, 20.2 against 19.2 % of a core. It is pure Python on the UI
thread, so it held the GIL for that long every frame, and the reader thread's wake-up
(the largest term of key-to-sound jitter, `latency-budget-2026-09-19.md` § 4) tracks how
long the main thread holds it. Now `flush` builds the map only on a tick with a queued
action, a page change, a menu opening or closing (the shown page object changes), a new
host connection, or every `REMAP_TICKS` = 30 ticks; and compares the lists by `==`, not
by `repr`. Idle tick **10 µs** p50 (13 mean), the page walk 2.90 ms / 19.2 % — the same as
no feed. A tick with input still makes its blocking round trip to the host (430 µs p50 on
the CM4 under a dense script, 4 inputs in 11 frames: frame work 4.42–4.57 ms against 4.31–4.36
without the feed); that wait is on a socket and releases the GIL, so it lengthens that
frame but does not hold up the reader thread. *Run's call, 2026-10-05*: 30 ticks (0.5 s)
is how late the phone can learn of a map change that came with no input (a hold that
fires late, a box loading); everything a player does moves it at once. Three tests, each
mutated red.

## #297 — the tape lives in the open project (C1 step 3b, the UI half, 2026-10-03)

Elfy's answer on #157 was **(a)**: SAVE moves the tape files into the project
folder and recording carries on there, and SAVE means "save the project I have
open". The audio process's half (`tape_dir {dir, move}`) landed with #157; this
is the half that decides which folder.

**Every spawned host gets `--tape-dir`** (`NativeAudio(tape_dir=…)`, or a
private scratch folder it removes on `close`), and the device unit passes
`--tape-dir /data/.state`. The App never trusts the starting folder: on boot,
LOAD, NEW and a host restart it calls `ProjectStore.open_folder(folder)`, which
is `tape_dir` without `move` — the media already there opened in place, nothing
read into memory. `ProjectStore.tape_folder` is the one place that knows where
the audio is.

| Gesture | What happens to the audio |
|---|---|
| a take | the audio process writes it into `tape_folder` as it happens |
| STOP | nothing — `tape.json` (the clip list) only. #93's dirty-track copy is gone |
| SAVE, a project open | `project.json` rewritten in its folder; no audio moves |
| SAVE, none open | the auto-named folder is made and the tape **moved** into it (`tape_dir` + `move`, a rename); it is now the open project. A refused move (another filesystem) falls back to copying |
| SHIFT+ENC3, save-as-copy | a new auto-named folder; each medium flushed (`tape_save` onto its own path) and **copied byte for byte**, so the copy opens in place; the instrument stays on what it had open |
| LOAD | `open_folder(project)`; leaving the working folder removes its media, which nothing can play again |
| NEW | the working folder's media removed, then `open_folder(working)` |
| a host restart (`rewalk`) | `apply(doc, tape_folder)` — the folder reopened; no finished take is lost |

**Old projects convert on first open** (#157's smaller question 1, its simple
answer). A project saved before this holds `tape_save`'s 44-byte-header WAVs
(`persist.is_old_medium`), which `tape_dir` refuses. `open_folder` renames each
aside (`tape-N.old.wav`), opens the folder, `tape_load`s each back in — which
writes a medium of the new kind into the folder — and deletes the aside only
when its load said ok **and carried every frame** (#410, below). The old working autosave named its files from 0
(`tape-0.wav` for track 0); a project's from 1, which is also the host's.
**This needed a host fix**: `tape_load` under a bounded pool wrote into slots it
never acquired, so the converted medium reopened empty (`audio/CLAUDE.md`
§ Defaults, the `tape_dir` row).

**The open project's folder does not move.** `library {op}` refuses a rename,
move, trash or delete of the folder the tape lives in, any folder above it, and
anything in it, `OPEN ON THE INSTRUMENT` (`open_project`) — in the host, because
the browser's LIFT, the phone's rename and the HTTP DELETE all arrive as that
message. The browser shows the host's words; HTTP answers 409.

**A host with no tape folder** (only a hand-run one now) keeps the tape in RAM:
`open_folder` fails with a journal line and SAVE falls back to `tape_save`
copies (`ProjectStore._on_storage`, `on_storage` since #1017), so a project still gets its audio.

### What it left

- **USB storage mode with the tape on `/data`.** § 11.1 assumed the host holds
  no open file on `/data` ("media are in RAM"); with `--tape-dir` it holds every
  medium open, so `umount /data` will meet `EBUSY` and the mode change fail.
  Not reproducible here (no partition); filed as **#303**.
- **An HTTP `PUT` over a live medium** in the open project's folder replaces
  the name while the host writes the old file. Same ticket, #303.
- Design § 6 step 3's **measurement on the CM4** (a SAVE and a STOP after a
  take, no UI frame over 40 ms) is the bench's.

### #410 — three ways it lost a take, closed (2026-10-03)

Found by the t297 code review (`docs/logs/findings/2026-10-03-t297.md` 1, 2, 3, 7).
Before #297 a project held its own copy, so its document and its audio could not
drift apart; now they are the same files.

- **A compaction rewrites the saved document too.** SAVE starts § 3.8's
  compaction, which now rewrites the open project's own medium. `_native_compact`
  calls `project_file.remap_saved`, which moves the saved `project.json`'s clips
  (every clip whose `src_track` is that medium) through the same map, with
  `Tape.remap_medium`'s rule — shared as `tape.compacted_address`: a reference no
  kept run covers whole is parked at the new end and reads silence. Only when the
  tape folder *is* the open project's (a save-as-copy's folder holds its own copy).
  *Run's call*: rewrite the document after the fact rather than make SAVE wait —
  on the device the compaction is seconds of background copying and SAVE never
  waits for it (§ 3.8).
- **The first boot over a pre-#297 autosave stays in the working folder.** When
  the working folder holds an old-format medium, those files are the live tape
  (SAVE used to copy), so `_boot_folder` opens the working folder — converting
  them — and leaves `open_project` as the autosave says. The next SAVE is that
  project's: the move is refused (the project holds media) and the copy fallback
  replaces its old media with the live tape; the test follows it through a LOAD.
  #411 changes that fallback on `exists` and must keep this path whole.
- **An old medium longer than the ruler keeps its aside.** `tape_load` answers ok
  with fewer frames when it cuts a file at the ruler (the desktop host's 120 s,
  the device's 480 s). The aside (`tape-N.old.wav`) is deleted only when the
  reply's `frames` equals the file's (`persist.old_medium_frames`); otherwise it
  stays in the project folder, untouched by any later open, with a journal line.
  `clear_working` no longer deletes asides either. *Run's call*: keep the full
  copy beside a cut conversion rather than refuse to open the project — the
  player still hears the part that fits, and nothing is lost.
- `arbutus_os/tests/test_httpd.py` `OpenProjectAgainstTheHost`: the phone's
  `DELETE` of the open project through the file server to a real host is 409
  `open_project`, `OPEN ON THE INSTRUMENT`, folder kept.

Tests (each seen red on the old code): `tests/test_tape_dir.py`
`TestTheSavedDocumentFollowsTheTape`, `TestTheFirstBootOverAnOldAutosave`,
`TestAnOldMediumLongerThanTheRuler` (with a control that a whole conversion still
deletes its aside).

### #411 — the working files follow the tape; a refused folder change moves nothing (2026-10-04)

Found by the t297 code review (`docs/logs/findings/2026-10-03-t297.md` 4, 5, 6).

- **Both working files at once when the tape changes folders.**
  `ProjectStore.commit()` (`save_project()` + `save_audio(force=True)`) runs at
  the end of `project_file.load`, `new` and a non-copy `save`, after
  `open_project` is set — so a crash right after any of them boots the folder the
  tape went to under its own clips. *Run's call, 2026-10-04*: in those three
  callers, not at the end of `persist.open_folder` / `move_to` as the review
  suggested — `open_folder` runs before the clips are applied (and at boot before
  `load_audio`), so writing `tape.json` there would write the previous clips.
- **The first SAVE writes `project.json` before the move** (`_move_tape`), so a
  crash between the two leaves the audio in a folder the browser lists; the
  document is written again after, with the media now in place.
- **LOAD opens the tape first and stops on a refusal**: `load` halts, calls
  `open_folder`, and only then `apply(..., open_tape=False)`. A refusal returns
  False with nothing changed — the session, its media and `open_project`. The FILE
  page flashes **`CANNOT OPEN <name>`** / `NOT LOADED` (a document that is not a
  project still flashes `NOT A PROJECT`).
- **SAVE never copies over a folder's live media.** `move_to` now returns the
  refusal's code (`""` once moved). Before anything is written, a folder holding
  a post-#297 medium (`ProjectStore.holds_live_media`) refuses the SAVE: `save`
  returns `None`, and the FILE page flashes **`NOT SAVED: <name> HOLDS OTHER
  AUDIO`** / `NOT SAVED` (`NOT SAVED BEFORE USB` on the way into storage mode,
  which still enters it — the takes are on the card already). The copy fallback
  stays for `move_failed` and for `exists` over old-format media only — #410's
  first boot over a pre-#297 autosave, whose live tape belongs in that project
  (its test still passes). A RAM tape (no `--tape-dir`) copies as before.
  *Run's call, 2026-10-04*: the flash texts; on the play-test checklist.
- The four spec sentences the review named are amended where they stand:
  `device-os-spec.md` § 3.3 (the tape is on disk from NEW, in the working folder
  until the first SAVE), § 7.3 (no audio in the autosave; a cut loses the take's
  unflushed seconds; the folder-change write), boot row 7 (`tape_dir` then
  `set_clips`), `companion-app-spec.md` A5b (4) (renaming the open project is
  refused).

Tests (each seen red on the old code): `tests/test_tape_dir.py`
`TestTheWorkingFilesFollowTheTape` (a crash right after LOAD, the first SAVE, NEW),
`TestARefusedFolderChangeMovesNothing` (a refused LOAD changes nothing; SAVE after it
makes a new project and the old one's media are byte-identical; SAVE into a folder of
other live media is refused, and the FILE page says so).

## #303 — the tape lets go of `/data` for USB storage (2026-10-04)

Since #297 the audio process holds the tape's files open on `/data`, and § 11.1's
unmount refuses a busy partition, so STORAGE would have failed `BUSY` every time.
`ProjectStore.park(folder)` points the tape at a scratch folder (`tape_dir` without
`move`: flush, close every medium, open nothing); `unpark()` opens `tape_folder` in
place again. `tape_folder` never changes while parked, so nothing else here has to know.
`arbutus_os/main.py`'s `UsbPump` calls both — park before it sends `mode storage`,
unpark once `exported` is down and no request is pending — and an App with no
`project` (a pump test's) parks nothing. `device-os-spec.md` § 11.1 is the as-built.

Two run's calls, 2026-10-04:
- **REC is refused while exported** (`App._rec_refused`, on the REC key and REC + a
  note), with `NO REC — LIBRARY ON USB`: a take would go to `/run` and be lost at the
  unpark. The emulator never sets `usb_exported`, so nothing here moved.
- **The parked folder is swept**, before the park and after the unpark: the clip lists
  stay in the audio process, and its read-ahead opens — and creates, empty — the media
  they name there. *(Amended 2026-10-13, #633, t475 #1: since #475 a read never makes a
  medium, so this reason no longer holds; the sweep stays as a guard against a medium
  left in the scratch folder, which the next park's `tape_dir` would open.)* Zero bytes on a tmpfs, but a stale one would be opened by the next
  park. Changing the host so a fetch never creates a file was the other option; it
  touches the take's own open path (`opened` is set by the first open, so a fetch
  that declined would make the next commit fail), which is not a change to make here.

Tests: `arbutus_os/tests/test_usb_tape_park.py` against the real offline host (nothing
under the library root open while exported, its control, the medium back with the same
frames and peaks, REC refused, a refused export unparking) and `test_usb_pump.TheTape`;
both halves seen red with the park, then the REC guard, switched off.

## #617 — one "export asked" state, from the park to the unpark (2026-10-09)

`App.library_held` is `usb_exported or project.parked is not None`, and every § 11.1 guard
reads it: `_rec_refused`, the new `_edit_refused` (wired into `Tape.edit_refused`, asked by
`Tape._edit`, so REV / SPEED / FILTER bakes, MERGE and the bounces all stop), `_compact_next`
(drops the queue while held), `App.tick`/`flush`, `ProjectStore.exported`, and FILE's
`usb_exported` property. `Settings.held` defers a save (`dirty`) and `Settings.flush` writes
it from `App.tick` once the library is back. `DISK_ACTIONS` now has NEW, and `_arm` refuses
RESET too (the old comment said NEW only resets RAM; untrue since #297).

Run's calls, 2026-10-09: the edit refusal says `NO EDIT — LIBRARY ON USB` (REC's shape,
its own verb); the compaction queue is dropped, not kept for the unpark (§ 3.8's next SAVE
re-triggers it, and a kept queue needs a second trigger point). Two tests in
`test_file_system.TestUsbExported` changed with the behaviour (NEW was asserted armable).
t303 row 5 landed with it: `arbutus_os/net/httpd.py`'s `PUT` refusal over the open
project's media compares casefolded (exFAT). Left: row 4, an unpark onto a `/data` that did
not remount (#687).

## #876 — the library's last writers held, and a refused setting kept (2026-10-13)

t617's rows 1–6. `App.library_refused(what)` is the one refusal (`NO <what> — LIBRARY ON
USB`, `ON USB`); `_edit_refused` now calls it. SYNTH's and DRUM's `drop_tape_buffer` ask it
**before** `sampler_file()`, which makes `synth/sampler/` on the bare mount point. The
browser (`LibraryOps._held`) refuses LIFT, DROP and the new-folder DROP before any path or
`os.makedirs`, and keeps the held item; `undo` keeps its group on the stack and returns True,
so SHIFT+STOP does not fall through to the page's own undo. `Settings.save` sets `dirty` first
and clears it only after `os.replace`: a refused write is retried by the next frame's `flush`
(as `persist._refused` retries the working state), and a landed one is never rewritten.

Run's calls, 2026-10-13: the browser's and DROP's refusals are REC's shape with their own
verb (`NO LIFT`, `NO DROP`, `NO UNDO`); a settings write the card refuses (full, read-only) is
now retried every frame instead of given up, the same cost `persist` already pays. Tests:
`test_usb_tape_park.FromTheRequestToTheRemount`'s six pending-window cases (`usb_exported`
still False) and `test_file_system.TestSettings.test_a_refused_write_is_retried_until_it_lands`;
eight mutants red (the late flag at each guard, each new call-site guard deleted, the
`dirty` reset moved or deleted).

## #624 — a boot restores the instrument and the slot the player left (2026-10-09)

`ProjectStore.load_project` ends with `native.set_active(app.active_instrument_id)`: `goto`
sends it only for SYNTH and DRUM, so a boot on any other page left the host on SYNTH under a
UI on DRUM. `SlotStore.select` marks the store dirty (debounced) when the letter changes, so a
recall of a slot that has a record reaches `<page>.json`; `App._build_pages` sets
`page.state.slot_letter` from `store.active` (a LOAD already did, `project_file.py`). The 2 s
timer calls `save_project(if_changed=True)`: it compares the serialised snapshot with the
last text written and skips an identical one while the file is there; `commit`, `flush` and
SAVE still always write. Run's call, 2026-10-09: the comparison is the whole JSON text, not a
dirty flag per field (one string compare every 2 s; nothing to keep in step with the
snapshot). Tests: `test_boot_restore.py`, `test_persist_dirty.OnlyOnChange`.

## As the mock device's UI (#331, 2026-10-03)

`emulator/run.sh --phone` (`tools/emu_phone.py`) starts `python3 -m arbutus_os.mock --no-screen`
and this window attached to it; it prints the iOS app's `-device <ip>:7402 -token <hex>`.
Four `main.py` flags, each off by default and each changing nothing when off (a private
host, `emulator/state/`, `audio/library/`): `--attach SOCKET` (use that host, never spawn or
close one; `--offline` then means "that host is offline: pull `render`", never implied),
`--share DIR` (`arbutus_os/screenshare.Publisher.pump(fb)` once a frame, so
`GET /screen.png` is this window's drawing), `--state-dir DIR` and `--library DIR` (the
mock's `data/.state` and `data/`). The phone's element map and actions need nothing more:
`ui_feed.py` already publishes them to whichever host the App is on. A mock still running
its stand-in screen is refused by the launcher (two writers on `screen.png`). Tests:
`arbutus_os/tests/test_emulator_phone.py` (the window as a subprocess under a driver that posts
F3 and calls SAVE on the main thread). **Run's call:** nothing reconnects if the mock restarts
mid-session; close the window and run `--phone` again.

## #334 — the site's and the app's colours, light and dark (2026-10-03)

`ui-aesthetic.md` § 3 was rewritten and `ui/palette.py` with it, on Elfy's
pre-approval (2026-10-03): **his standing exemption from the hard rule on
`palette.py` and the screenshot baselines, for this ticket only.** The screen
wears the website's beige, brown and green and the shades the app derives from
them, in two modes, and the paired phone picks the mode.

**`palette.py` is two tables and a switch.** `LIGHT` and `DARK` map every role
to an RGB; `set_mode()` binds the chosen table's names as module attributes, so
every draw site still reads `palette.INK` at call time and gets the current
mode's. Three names went, because their colour was their name: `OFFWHITE` is
`INK`, `BLACK` (the zone fill) is `PANEL`, and the per-category
`BG_PERFORMANCE` / `BG_RECORDING` / `BG_UTILITY` / `CATEGORY_BG` are one
`GROUND`. The chrome's LCD well is `chrome.WELL`, black whatever the mode, like
`SCREEN_BORDER`. `Page.category` is left in place; no draw site reads it (only
`test_phase2b`'s category check, which pins the attribute and no colour).

**The mode.** `App.set_appearance(mode)` is the one door: the host's
`appearance` push (`NativeAudio.on_appearance`), the `V` key and `--appearance`
all go through it. It writes `system.appearance` in the device's settings
(`app/settings.py`), and boot reads it back, so with no phone the screen keeps
the last mode (light the first time). A UI that starts after the phone spoke
asks the host once (`adopt_host_appearance`). **`ARB_APPEARANCE`** pins a run
(`tools/verify.py` and `tests/native_support.py` set it to `light`): the mode
still follows a push, and nothing is written to the library. Without it a
player's `V` in `audio/library/settings.json` would turn every screenshot dark.

**Evidence.** `validation/ui/` is the light set, regenerated and committed by
#334. Dark has no tracked set: `verify.py --all --appearance dark --out DIR`
draws every feature dark into a scratch directory, and `tools/contact_sheet.py`
puts the two side by side (the #334 log carries one). `verify.py` gained the
`appearance: dark` event, which says it the way the phone does (a second client
on the host's socket) and fails if the screen did not follow.

## #517 — a spawned host ends with its parent (2026-10-05)

`NativeAudio` spawns `arbutus-audio` with a `preexec_fn` (`_die_with_parent`) that on
Linux sets `prctl(PR_SET_PDEATHSIG, SIGTERM)`: when the process that spawned the host dies,
however it dies, the kernel sends the host SIGTERM and it exits, unlinking its socket.
Before, a test process killed outright never reached `close()`, and the host, a server
nobody told to stop, ran on: four of them for up to two and a half days on the CI runner,
each from a run cancelled by `cancel-in-progress` (`.github/workflows/gate.yml`). The
signal follows the thread that forked, so a host spawned from a thread that ends would
end with it; nothing spawns one off the main thread (`test_reconnect`'s thread attaches).
macOS has no such call and gets no hook. `tests/test_host_dies_with_parent.py` kills a
child Python that spawned a host with SIGKILL and waits for the host to go; seen red
without the hook. No pixel, no baseline; `ui/palette.py` and `ui/font.py` untouched.

## #817 — always saved: NEW makes the project, the autosave writes into it, SAVE goes (2026-10-13)

D97 (Elfy, Play date 1's grill; `device-os-spec.md` § 7.3 *Decided 2026-10-12*). Tests:
`tests/test_always_saved.py` (16, each seen red on the old code and by a mutation of the new).

- **A project is always open.** `project_file.new` makes `projects/<date>/<PROJ_n>/` and opens
  the tape in it before anything else; `project_file.ensure_open` gives a boot one when the
  working state names none (a first boot, an old unsaved session: its take moves in, the old
  first SAVE's rename). **Both `main`s call `ensure_open`, `App.__init__` does not** (run's
  call): a test's instrument makes no folder in a library it did not ask for. `verify.py`
  calls it only on a `temp_library` feature, for the same reason.
- **The autosave writes the project's whole document into its folder**
  (`project_file.autosave`): `ProjectStore.tick` checks every `DOC_INTERVAL` (10 s) with the
  transport stopped and every `DOC_ROLLING_INTERVAL` (2 min, DDR-070) while it rolls,
  choosing the interval at each check so a STOP brings the 10 s back at once; it writes only a
  document that changed, temp-then-rename. `flush` (exit, power-off, USB storage, leaving a
  project) writes it too. **No `tape.json` in the folder** (run's call): the document already
  carries the clip list (`tape.tracks`), so a second file would be a second home for it.
  `/data/.state/` stays as the boot's pointer (`open_project`) and the UI-only state.
- **Pattern promotion and § 3.8's compaction lost their SAVE.** Promotion (`promote_pattern`,
  `promote_step_patterns`) runs on the flush moments only, not on the 10 s clock (run's
  call: it re-points the host's MOD assets, not something to do under a player's hands);
  a compaction is asked after any document write with the transport stopped.
- **An untouched NEW is deleted when left** (`leave`, `untouched`, `power_off`): nothing
  on the tape and the document equal to NEW's, the page, FILE's own subpage and the name
  aside. NEW and LOAD delete it once the tape is off it; a power-off points the tape at the
  empty working folder first. **The device UI's own exit counts as leaving** (run's call):
  `arbutus_os/main.py`'s `finally` runs `power_off` too, or every `main` a test runs against
  the checkout's library left an empty project there. The flag rides in `project.json` as
  `untouched`, so a restart keeps an untouched NEW untouched; a boot with no working state is
  a NEW, an old session with only its knobs turned is not.
- **#955 (t817 rows 3–4, 7–10):** NEW sets the mark (`_mark_new`) *before* the
  `commit()` that writes the working state, so a cut right after NEW boots it untouched.
  `power_off` returns at once when it already dropped the project (`fresh_key == DROPPED`, no
  project open): the device calls it from `PowerPump` and again from `main`'s `finally` on
  SIGTERM, and the second `leave` cleared the mark. `doc_written` is seeded from the folder's
  `project.json` text (`_seed_written`) at a boot's `ensure_open` and at LOAD, so the first
  tick writes only what changed; a document the instrument captures differently is written
  once. The four tests run with no flush or priming; each fix removed is red on an assertion.
- **FILE › PROJECT:** ENC3 is an empty cell until #818's RENAME, SHIFT+ENC3 arms nothing,
  the main area names `app.open_project` (the page keeps no name of its own), and USB
  STORAGE's DDR-021 save is a halt and a flush. `project_file.save` stays as the writer
  under `ensure_open`, a copy (the phone's Duplicate, #821) and the tests.
- `audio/library/projects/` is gitignored: the emulator runs on the checkout's library, so
  its boot's project lands there. The Learn lesson `save-and-start-a-new-project` keeps its
  id (a rename drops every phone's green mark) and teaches NEW.

## #821 — moving projects: held keys, the open project, low storage (2026-10-13)

D99's UI half (`device-os-spec.md` § 7.3's #821 note has the device's).
- **`app/file_jobs.py`** holds the words, the `Prompt` and `StorageWatch`. `App.file_job` is
  set only by `arbutus_os/main.py`'s `NetPump` from `net.sock`'s `job` (a desktop never has
  one); `set_file_job` on the edge into a job halts the transport and `panic()`s, and while
  it stands `on_key` and `on_encoder` do nothing but flash the job's sentence.
- **The open project's offload**: `release_project(id)` is `project_file.new()` (the name
  menu is #818's), whose `commit` writes the boot pointer `arbutus-net` waits on;
  `project_offloaded` flashes `PROJECT OFFLOADED TO PHONE` and lets the low-storage question
  be asked again. A rename from the phone arrives as the host's `tape_dir_renamed` push:
  `_native_renamed` re-points `project.tape_folder` and `open_project` and commits.
- **Low storage**: `StorageWatch.tick` from `App.tick` every 10 s (`statvfs` of the library
  root), not while recording, a job runs or a prompt is up, once per boot or offload. A trash
  with anything in it asks; an empty one flashes `STORAGE LOW · OFFLOAD PROJECTS WITH THE APP`
  for 8 s. EMPTY sends `library {op: delete}` per `.trash` entry. The prompt draws over the
  main area in HELP's overlay shape (`ui/frame.py` `_draw_prompt`, the first line red), its
  answers on key cells 1 and 2, its question alone in the strip.
- **Run's calls, 2026-10-13** (`notes/ui-choices.md`): any key but 1 and 2 answers LATER
  and then plays; `...` for the spec's `…` (no glyph, and the font is not a run's to edit);
  a trash holding no project folder asks `TRASH · <size> · EMPTY TRASH?`; the words for the
  two jobs the spec names none for, `RESTORING FROM PHONE...` and `DUPLICATING PROJECT · HOLD
  TIGHT`. Shots graded by the run: `docs/logs/assets/821-*.png`.
- Tests: `tests/test_project_moves_ui.py` (18, each guard seen red by `tools/mutate.sh`).

## #818 — menus: FILE › PROJECT acts on the turn, the name menu, the red key (2026-10-13)

D98 (Play date 1). `pages/file.py`: `ACTIONS` is `NEW · LOAD · RENAME · BOUNCE`, each cell a
display-only `Param` named `<action> ›`; `on_encoder` on PROJECT calls `_act` on a turn right
and swallows a turn left. The 1-2-3-4 arming (`_arm`, `_confirm_press`) stays for SYSTEM ›
RESET, FORGET PHONES and the `.fw` line only. `app/name_menu.py` is the one name menu (a
`Page` opened with `App.open_menu`): its names come from `WORDS[kind]` (only `project` until
#819), shuffled once by a seed made of the names already taken, and walked three at a time.
`Page.red_strip_cell()` (default None) is the frame's red key (`ui/frame.py`); a prompt's two
answers never have one. **The pushes** are `ENC1_PUSH`–`ENC4_PUSH` (`app/keys.py`
`ENC_PUSH_KEYS`), the names `arbutus_os/drivers/matrix_p3.json` already gives P3's `COL11`, so
the device's key reader needs nothing; they are not in `Key` (the 52 switches, each with a
hotspot). A menu closed by a number key reaches `FilePage.on_menu_number`, which commits on
the red key and only cancels on the others. **RENAME** (`project_file.rename`) is
`library {op: rename}` on the open project's folder, the path the phone's rename takes (#821):
the audio process renames under its I/O lock with the handles following, and the UI calls
`_native_renamed` itself rather than waiting a poll for the push. `App.project_offloaded`
opens NEW's name menu over FILE when the offloaded project is the one `release_project` left;
its pick renames the auto-named project. Learn: four lessons (`save-and-start-a-new-project`,
`bounce-a-song`, `sketch-a-song`, `finish-and-share`) and `lesson-runs.json` follow; the turn
that opens a menu records no `param` (the feed reads the cell after the turn, and the menu has
none), so the NEW lessons key on the header `USB/FILE › NEW`. Not built: BOUNCE's question
(#920). Tests: `tests/test_project_file.py` (`TestNameMenu`, `TestRename`, `TestLoadMenu`),
`tests/test_project_moves_ui.py`; features `project-file` (shots `04-name-menu`, `-dark`,
`08-new-picking`, `09-browser-dark`, `10b-rename`, `10c-renamed` new) and `browser-ops`.

## #851 — tests and feature scripts run on the checkout's stock library (2026-10-13)

Playing the mock or the emulator writes into the checkout's `audio/library/` (a
`settings.json`, `projects/`, new patches and samples, none tracked), and the tests read that
folder or copied it whole: a played clone gated red on a commit a clean worktree passed.

- **`testing/stock_library.py`** is the one place a test's library comes from. `copy_stock`
  copies the files `git ls-files` lists under `audio/library/`, at their **working-tree
  content**, so a stock patch a session is retuning is what its tests see, and a player's
  untracked file never is. Without git (a tarball), everything but `projects/` and
  `settings.json` (a new patch cannot be told from stock then). `scratch()` is one such copy
  per process in a temp folder removed at exit, shared the way the checkout's folder was.
- **Every default now points there:** `native_support.host()`, `app_for()` and
  `native_or_skip()` without a library, `test_native_smoke`'s App and its "every document
  loads" walk, `tools/verify.py`'s features without `temp_library`, and
  `test_phase3.TestChromeWindow`'s `main.py` run (`--library`; it opened a D97 project in the
  checkout every gate). `library_copy()`, `verify.py`'s `_library_copy`, `test_lessons`,
  `test_native_tape` and `test_tape_compact` copy stock instead of the whole folder.
- **Not covered:** a *tracked* stock file a player changed (a patch saved over in place) is
  still what the tests read; it shows in `git status` as a modified file, which is the signal.
  A new stock file a session adds is seen by its tests once it is `git add`ed.
- Seen: with a played library in this checkout (dark `settings.json` with MIDI channel 3, a
  project, a new patch and sample), the old code went 15 red in the emulator suite and the
  `file-system` feature failed; the new code 821/821 and 35/35. `tests/test_played_library.py`
  builds a played library; each of its seven mutants went red (`tools/mutate.sh`).

## #853 — the live stream's UI half (2026-10-13)

`PROTOCOL.md` § The live stream is the contract; `audio/host/live-schema.json` says what each
field is. What this folder does:

- **`ui_feed.situation(app)`** builds the UI's half: `screen` (page, header, subpage and its
  key, the slot letter, menu, HELP), `keys.held` (keys down on the instrument, from the feed's
  own `key` actions), `octave` (the front instrument's page), `transport.selected` and
  `counting_in`, `project` (`id`, `name`, `new` = made by NEW in this session, from
  `ProjectStore.fresh_key`, never `project_file.untouched()`, which captures the whole
  document), `input.listening`, `power`. Built on the element map's cadence (input, a page or
  menu change, every `REMAP_TICKS` idle) and sent as `ui_situation` only when it differs from
  the last one sent. No None anywhere: the host refuses a null.
- **New actions:** `note` (a MIDI keyboard: `MidiIn.handle` sets `feed.midi` around its
  `on_key`, so the keyboard's notes are not read as the instrument's keys and are not in
  `keys.held`; a flag rather than an `on_key` argument because three test fakes take
  `on_key(key, pressed, vel)`), `capture` (`InputPage.tick` when a sample lands), `project`
  (`project_file.new`/`load`/`save`, through `_said`; no player gesture calls `save` since
  D97, so `save` comes only from `ensure_open` and the phone's duplicate), `track` (the
  feed's own flush sees `tape.selected` or the armed track move).
- **`UiFeed.frame_drawn(app, fb)`** after each drawn frame (`arbutus_os/main.py` inside the
  frame gate, `emulator/main.py`): while `native.live_watchers` > 0 (the reply to
  `ui_situation` and the host's `live {watchers}` push), a CRC32 of the frame against the last;
  a change is `ui_frame`. Nobody watching costs one comparison.
- **Records:** `app/ios/ArbutusTests/sketch-a-song-situations.json` (the lesson's script, the
  first document then one merge patch a step, project ids numbered so the date does not move
  it) by `tests/test_live_situation.py`, which also holds every document to the schema.
  `lesson-runs.json` gained the `track`, `project` and `capture` actions; `test_lessons`
  drops a `project` action's `id` (it names today's date folder). The app's matcher ignores a
  kind it does not know (`Learn.swift` `matches`), so no lesson's green moved; the Mac's
  `LearnTests` was not run here.

*Run's calls, 2026-10-13:* the half rides the map's cadence rather than every tick (a dict
built sixty times a second on a Zero for a value that moves on input); `project.new` answers
the ticket's "a new one since when" together with the `project {op:"new"}` action's `t_ms` and
the `rev` at which `project.id` changed; the battery is `power.telemetry: false` when unread,
never a made-up number (battery.py's rule).

## #932 — SHIFT+STOP only undoes; the power-off line over a prompt; a turn under a prompt (2026-10-13)

- **SHIFT+STOP off SYNTH/DRUM** runs the tape's one-level undo and returns: it no longer
  falls through to STOP (which stopped a playing tape, and during a take committed it without
  `save_audio`). On MIXER, SEQ, TEMPO, INPUT, FILE and HELP it is still the tape's undo,
  unseen there, until Elvin says what it means there (desk M5). Test:
  `test_edit_undo.py::test_track_undo_never_stops_the_transport` (asks the host).
- **`tickers()`**: while `powering_off` is set and its flash is live, that line stands alone,
  above a prompt's question (D100); before, the prompt returned first. Test:
  `test_project_moves_ui.py::test_a_power_off_outranks_the_question`.
- **`on_encoder`** answers an open prompt LATER, then turns (D111, by D107), as a key does;
  with HELP held the turn still only names the encoder. Test: `test_a_turn_is_later_and_still_turns`.
- `pages/stub.py` deleted (nothing imported it); the second `n = tp.selected + 1` in
  `tickers()` gone. Each test seen red on its shipped line mutated (`tools/mutate.sh`, 4 mutants).

## #843 — the CPU warning reads CPU UNHAPPY: OVERLOAD (2026-10-14)

- `app/native_audio.py` `XRUN_LONG` is `CPU UNHAPPY: OVERLOAD` (D103: the app's Status says the
  same), `XRUN_SHORT` `CPU UNHAPPY` (*run's call*: the app's first two words; the long form
  fits the strip, so the short one only shows where the strip's own rule picks it). Same 3 s
  flash, same slot `HARD DRIVE SLOW` shares. Test:
  `test_tape_flags.py::TestXrunWords`, seen red with the old words put back at the
  `_absorb_pushes` call site (`tools/mutate.sh`).
- Left: `audio/host/PROTOCOL.md`, `audio_host.cpp`'s comment and `live-schema.json`'s
  `cpu_overload` description still say `CPU NO HAPPY` (#933 holds `audio/host/` today); the
  app's own "flashing after 6 s" from D103 is the app's half, not this.

## #1039 — the emulator's tape is the host's 480 s (2026-10-14)

- **One length.** DDR-072's 8 minutes a track lives in code once, the audio host's
  `--tape-seconds` default (`audio/host/audio_host.hpp`). `NativeAudio` passes
  `--tape-seconds` only when a caller asks (tests ask for 30 s), so the desktop
  emulator, `tools/verify.py` and the device's dev launch spawn a 480 s host as the
  device's unit does. `app/tape.py`'s `TRACK_SECONDS` (the timeline, TRACK's SCRUB and
  ZOOM ranges) is 480.0 and `tests/test_tape_length.py` fails when a host started the
  emulator's way reports another length (seen red on both mutants: the ruler at 120,
  and `NativeAudio` passing 120 again). *Run's call*: a checked copy, not a value read
  from the host at run time, because the ruler is a module constant every page and
  `tape.py` function reads; a per-project length (#168) would move it anyway. **On the
  device this was a bug:** its host has always been 480 s while TRACK clamped SCRUB,
  ZOOM and a dropped clip at 120 s.
- **Memory:** none. With a `--tape-dir` (always, since C1 step 3b) the host's pool is
  bounded and a longer ruler is a longer table, not RAM (`tape.cpp`'s `ensure`).
- **Deviation from DDR-058:** the tick ladder's coarsest rung was 8 bars; at 480 s and
  120 BPM that is 30 ticks on the whole-tape view. `BAR_RUNGS` now starts at 32 and 16
  bars, which keeps at most 16 ticks up to 240 BPM (`test_tape_ui`'s ladder test, which
  walks every zoom from the whole tape down, found it).
- **Shots that move:** the ZOOM and SCRUB knob bars on every TRACK shot (30 s of 480 s,
  not of 120): `validation/ui/native/08-track-clip`, `09-track-rolling-take`,
  `e2-01-bank-b` to `e2-04-merge-drop`; nothing else in them.

## #819 — names per kind; SHIFT+SYNTH saves through the name menu (2026-10-14)

- `app/name_menu.py` `WORDS` holds all four kinds: projects places (`QUIET HARBOUR`, a space),
  patches plants and landmarks, racks tools, patterns action nouns, the last three
  `ADJECTIVE-NOUN` file names ≤ 12 characters (the old patch rule). `free_names` / `first_free`
  are the menu's pool outside the menu. `app/patch.py`'s adjective-noun generator is gone.
- SHIFT+SYNTH / DRUM opens the name menu with **ENC1 as the opener** (*run's call*: no turn
  opened it, so ENC1's push and the red key 1 commit). The other number keys cancel and stay on
  the subpage, as FILE's do. `App.closed_menu` (new) is the menu `close_menu` last shut:
  `SynthPage.on_menu_number` commits only when it is this page's name menu, so a name menu a
  page key dismissed can never commit under a later SHIFT+1 browser shut by its key 1.
  (`pages/file.py` keeps its own `_names` without that check; FILE's page keys go through the
  same path, so the same stale case may exist there: not looked at.)
- A pattern promoted with the patch takes the first free action noun in the pattern folder,
  not the patch's name. Racks' list has no caller until #820.
- Moved: the `patches` and `browser-ops` verify scripts name `dry-sedge` (the first offer with
  `init` and `soft-bell` taken) where they named the seeded `loud-bell`; four Learn lessons
  and their records (`lesson-runs.json`, `lesson-situations.json`) save on `ENC1_PUSH|1`.

## #820 — SAVE THIS RACK above the racks; SAVED RACKS under them (2026-10-14)

- `app/save_row.py` `SaveRowMenu` is SYNTH / DRUM › FX's browser (SHIFT+3): a `MenuPage` whose
  source puts `SAVE THIS RACK` above the library's folders and shifts every index by one on the
  way to the page's pick callback, so `library.list("fx")` and every test that indexes it are
  unchanged (RACKS stays folder 0). It opens on RACKS (`folder_idx` 1). ENC1 up onto the row
  applies nothing; the right pane is a `NameMenu` (kind `rack`, tool names) drawn under
  `PICK A NAME TO SAVE RACK`, its `app` left `None` so it never opens as a menu of its own.
  The row is green in the left pane (`MenuPage._pane`'s new `own` colours), its highlight too.
- The commit: the push of the encoder last turned (ENC1, which reached the row, until ENC2
  turns), or the red key 1. `App` hands a number key that closes a menu to
  `SynthPage.on_menu_number`, which asks `closed_menu.commit_by_key(idx)` first.
- `NativeLibrary.write_rack` writes the host's rack (`native.rack(inst)`: already a §6.7
  document with its `name` swapped, per-tab `bypass` kept) to `fx/racks/saved/<slug>.rack`
  and re-reads only the rack folders (a whole `rescan` would swap every page's engine folder
  object). `_racks` lists **SAVED RACKS** (folder `saved-racks`) after RACKS once it holds a
  rack. `is_rack_folder` replaces the two `lf.folder == "racks"` checks (SYNTH and MIXER), so
  MIXER › FX loads a saved rack too; it has no save row (the ticket names the instrument's
  browser). `rack_names()` is the name menu's `taken`: stock and saved racks.
- Run's calls (`notes/ui-choices.md` § #820): SAVE as the red key's label; the commit closes
  the browser and flashes `SAVED RACK <name> → SAVED RACKS`; numbers 2–4 on the row do what
  they do on any browser (close, go to that subpage), saving nothing.
- Tests: `tests/test_save_rack.py`, 9 (the sound check reads a crusher's level step, which
  does not depend on the oscillator's start phase; a slot change does move the phase).

## Session History
- [[logs/2026-10-05-00-50]] — bench-2 (#413): § D17 — the map built only when something moved it; 170 → 10 µs a CM4 frame idle
- [[logs/2026-10-03-22-46]] — mac-2 routine (**#412**): § D17 — a > 64-action tick in chunks, the strip's label always empty, `_strip` gone
- [[logs/2026-10-03-20-07]] — bench routine (**#410**): § #410 — the saved document follows a compaction, the first boot over an old autosave keeps the live tape, a cut conversion keeps its aside
- [[logs/2026-10-03-13-29]] — #331: `--attach`/`--share`/`--state-dir`/`--library`, `run.sh --phone`
- [[logs/2026-10-03-10-47]] — mac routine (**#291**): `tests/test_lessons.py`, every Learn lesson played through the D17 feed; § D17 notes the map-before-actions order and HELP's silent turns
- [[logs/2026-10-03-08-55]] — mac routine (**#297**): § #297 — the tape lives in the open project; STOP and SAVE copy no audio, save-as-copy on SHIFT+ENC3, LOAD opens in place, old projects converted on first open, the open project's folder refuses to move
- [[logs/2026-10-03-05-11]] — cloud routine (**#276**): § D17 — `app/ui_feed.py`, the element map and the actions for the phone, flushed once a tick through `NativeAudio.publish`
- [[logs/2026-09-25-13-36]] — cloud routine (**audit Q3, #31**): new § Audit Q3 — `App.usb_exported` becomes a real attribute that pauses the whole working-state autosave while `/data` is in a computer's hands (it wrote into the bare mount point on the OS partition); a refused write (`/data` full or read-only) is a line and a retry instead of an exception out of `App.tick`; the browser skips a Mac's `._name.patch` files. 711 emulator tests (703 before), no pixel, no baseline
- [[logs/2026-09-25-11-18]] — cloud routine (**#176**): **row 64's `POWER-OFF` is read by something at last.** The setting has been stored and drawn since J2 and nothing anywhere read it, so a player who set a 10-minute auto-off got silence. The App now carries the *rule* — `note_input` / `idle_seconds` / `idle_power_off_due`, `device-os-spec.md` § 7.2 row 3 — because every fact it is made of but one is this App's: the idle clock (**a key or an encoder and nothing else** — not a render, not a sequencer step playing to nobody), the transport rolling, a take recording, a count-in. The exception, "no client on the network sockets", is another process's listener and arrives as an argument. **Nothing here powers anything off**: `arbutus_os/main.py`'s new `PowerPump` is what sends `poweroff`, and it is also what sets `app.powering_off` and puts `SAVING — LOW BATTERY` / `POWERING OFF` on the strip — tickers rather than screens of their own, which is what every other device-level sentence this UI says already is. 10 new tests in `tests/test_idle_power_off.py`, against a throwaway library copy; no page, no pixel and no baseline moved
- [[logs/2026-09-25-09-13]] — cloud routine (#174): `App.goto()` raises on an id that is not a page instead of returning silently — two tests, no pixel, 693 emulator tests (691 before). § #174 above is why a guard is worth it on a path production never takes
- [[logs/2026-09-23-15-12]] — cloud routine (#152): the `‹ ›` octave stops being an undo step and `EditUndo.amend` arrives with it — an undo entry is the page's snapshot, and the snapshot carries the octave, so dropping the push alone would have made the *previous* edit's undo snap the octave back. § S12 entry 81 above is struck and rewritten. 626 emulator tests (622 before), no pixel, no baseline
- [[logs/2026-09-20-05-28]] — cloud routine (#125, L3): `Page.claims_note_keys()` and `DrumPage`'s override — two lines, no pixel, 522 emulator tests before and after. § L3 above is why they exist
- [[logs/2026-09-20-07-19]] — cloud routine (#128): `decode_note` + `MidiSnapshot` split out of `MidiIn.handle`, and the velocity curve out of `Settings` as `shape_velocity` — so the device's rtmidi thread can decode a note without reading a document the tick is writing. One implementation, both threads; 529 emulator tests (522 before), no pixel, no baseline. § #128 above is why
- [[logs/2026-09-22-15-26]] — cloud routine (#105): the two scratch files this folder left in `/tmp` — `NativeOfflineAudio`'s render directory (`block.wav` up to 226 MB, `cleanup()` a no-op) and an entered pattern's `arbutus-pattern-<inst>-<pid>.json`. § #105 above is why one is a `weakref.finalize` on the object and the other an `atexit` hook. 532 emulator tests (529 before), no pixel, no baseline
- [[logs/2026-09-22-17-09]] — cloud routine (S11, #53): the ENV page goes dim and inert on a box that declares `honours_env: false`. § S11 above is why the values are kept rather than blanked, and why the knob bar under a dimmed cell was left alone
- [[logs/2026-09-22-19-19]] — cloud routine (#95): `App.open_project` and the `open_project` key in `persist.snapshot()` — the id of the saved project the working state came from, so `arbutus-net` can tell whether the project a phone asked to bounce is the one the instrument has open. § #95 above is why `project_file.save`/`load`/`new` own it rather than the FILE page, and why `rewalk` does not. 556 emulator tests (538 before), no pixel, no baseline
- [[logs/2026-09-22-20-18]] — bench routine (#62 item 3): `app/library_ops.py` sends `library {op}` for every move and keeps only the gesture layer. § #62 item 3 above is why always-delegate stopped being a decision, why `FOLDER-NN`, `free_name` and the undo group stay here, and what the rewiring found about which `--library` root is the writable one. 560 emulator tests (556 before), no pixel, no baseline
- [[logs/2026-09-22-21-14]] — cloud routine (S12, #58): DDR-054's one-step undo on SYNTH/DRUM, spec'd since the design decisions and never built. § S12 above is the mechanism (`app/edit_undo.py`, a snapshot of the page's own DDR-029 document), what counts as an edit and what does not, and the four `[TBD-Elfy]`s — chiefly that on those two pages the gesture never reaches the tape's undo and never stops the transport. 581 emulator tests (560 before), 33 verify features, no pixel, no baseline
- [[logs/2026-09-22-22-16]] — bench routine (S13, #59): one sequencer per instrument, `App.sequencers`, and `App.sequencer` as the active one's. § S13 above is why that property makes the change small, why a sequencer's instrument is fixed at construction, why `restore` ignores the snapshot's `instrument` key, and the two things the second sequencer exposed in `project_file`. 587 emulator tests (581 before), 34 verify features, no baseline
- [[logs/2026-09-22-23-27]] — cloud routine (S14, #60): the stepmod entry gesture — `STEP`/`VALUE` as UI-side pseudo-params on the MOD tab, `app/stepmod.py` as the document, `app/scratch.py` as #105's rule in one place, and one host change so content can reach a MOD tab that already exists. § S14 above is why the two parameters are the UI's and not the box's, why the push is one a frame in `tick`, and where a promoted pattern lives. 598 emulator tests (587 before), 35 verify features, no baseline
- [[logs/2026-09-23-01-16]] — cloud routine (C5b, #98): C5's five render levers land in `ui/draw.py`, `pages/input.py` and `app/native_audio.py`, with `testing/render_reference.py` holding the code they replaced so the "pixel-identical" claim can still be falsified. § C5b above is why the text mask cache is dropped by the editor rather than keyed on `font.dirty()`, why both caches need no palette invalidation, and why `waveform`'s cache carries a generation counter. 622 emulator tests (598 before), 3.13x on the local A/B, every page's frame identical, no baseline
- [[logs/2026-09-24-01-18]] — cloud routine (#99): `App._adopt_host_transport`, `_playhead_seen` and `Tape.follow` — the transport is the host's and this UI holds a mirror. § #99 above is why adoption is edge-triggered on the host's document rather than value-driven, why a frame the UI changed the transport on is the UI's, why a roll adopted from the host is never a take, and why a remote stop leaves the playhead where the host left it. 639 emulator tests (636 before), no pixel, no baseline
- [[logs/2026-09-24-13-14]] — cloud routine (#69): TRACK's ENC3 routed to `App.set_bpm` while `LINK` is on, and the cell that reads `BPM` when it is. § #69 above is why the varispeed stayed on SHIFT, why `LINK` is read from the TEMPO page rather than copied, and why the MIDI-master lock is drawn as well as enforced. 661 emulator tests (657 before), no pixel, no baseline
- [[logs/2026-09-24-19-38]] — cloud routine (**#163**): `_clip_wave`'s zoomed-out branch bounds `bins` by the clip's own pixel width — `span`, not the visible width, so the `_peaks` key stays geometry and a jump still asks for nothing. It asked for `ceil(len / 256)` bins (33,750 into a ~300 px lane on the CM4) and drew one in a hundred; **no shot moved**, 0 differing pixels across 42 screenshots. It is also what makes the host's peaks sidecar reachable — that answers only at or below one bin per 3072 frames. **662 emulator tests** (661 before), no pixel changed, no baseline promoted
- [[logs/2026-09-25-03-40]] — cloud routine (**#169**): `TrackPage._prefetch_peaks` sends one `clip_peaks` for the whole bank and the draw path reads the cache unchanged, so a redraw is one round trip where it was one per clip. § #169 above is why the geometry had to come out of `_clip_wave` (it was the cache key already), why a refused batch changes nothing, and why the rolling take is not in it. The ticket's other half — two decimal places on the numbers — **halves the bytes and moves 8 of 309 shots by 2–4 px**, so it was not landed (#172). **687 emulator tests** (686 before), no pixel changed, no baseline promoted
- [[logs/2026-09-25-07-16]] — cloud routine (**#173**): no UI code changed and the point is that none had to — `persist.snapshot()`'s **`page`** is now the app's *current page* row (`GET /status` reads it off the file, `device-os-spec.md` § 9.11), so a key this folder used to own alone is a contract with `arbutus-net`. `tests/test_project_file.TestPageInTheSnapshot` is the guard that says so: the key is present, follows `goto`, reaches the file, and is the page **behind** a menu rather than the menu (`App.page_id`). Two-second staleness is deliberate — writing the document on every page change would make a key press a file write
- [[logs/2026-10-03-22-08]] — #334: the site's/app's palette, light and dark, following the phone's `appearance`
- [[logs/2026-10-04-04-37]] — mac routine (**#303**): § #303 — `ProjectStore.park`/`unpark`, REC refused while exported, the park folder swept
- [[logs/2026-10-05-07-06]] — bench routine (**#517**): § #517 — a spawned host ends with its parent (`PR_SET_PDEATHSIG`)
- [[logs/2026-10-08-21-06]] — emulator/CLAUDE.md 227 KB -> 15 KB index + emulator/notes/ leaves, verbatim; splitdoc.py; context-splits.md
- [[logs/2026-10-09-05-48]] — #624: boot sends the restored instrument, slot recall persists, project.json timer skips unchanged snapshots
- [[logs/2026-10-12-23-26]] — Always saved (D97): NEW and boot open a project folder, the autosave keeps it current, SAVE gone, untouched NEW deleted
- [[logs/2026-10-13-00-45]] — #851: tests and verify features copy the library's git-tracked files, never a player's settings or projects
- [[logs/2026-10-13-01-13]] — D99 device half: checksum, Range, verified delete, duplicate, any-project bounce, held keys, open project, low disk
- [[logs/2026-10-13-02-22]] — FILE › PROJECT acts on the turn; one name menu (places), one red key under the knob, push or red key commits
- [[logs/2026-10-13-05-05]] — #874: boot reads project.json's active instrument before the slot pages restore; DRUM's SEQ bank stays off SYNTH
- [[logs/2026-10-13-06-32]] — "#876: browser ops, undo and the tape DROP refused from STORAGE's park; a refused settings write kept dirty and retried"
- [[logs/2026-10-13-06-35]] — Live stream: live {on}, situation doc as merge patches (<=20/s), note/capture/project/track actions, frames, schema
- [[logs/2026-10-13-15-19]] — SHIFT+STOP off SYNTH/DRUM undoes and returns; power-off line beats a prompt; a turn answers LATER then turns
- [[logs/2026-10-14-05-25]] — #843: the instrument's CPU warning now reads CPU UNHAPPY: OVERLOAD, as the app does; #819 parked behind #854
- [[logs/2026-10-14-08-45]] — Emulator tape = host 480 s: NativeAudio passes no --tape-seconds, TRACK_SECONDS checked by a test; ladder +32/16 bars
- [[logs/2026-10-14-08-46]] — "#819: word lists per kind; SHIFT+SYNTH/DRUM saves on ENC1's push or the red key; a patch's .seq takes an action noun"
- [[logs/2026-10-14-14-30]] — BOUNCE renders as a host job (chunked, file at the end, bounce push); a played key asks CANCEL BOUNCE TO PLAY?
- [[logs/2026-10-14-15-28]] — #632's first half: MIXER's stale cells, loop framing, TRACK's waveform cache; the rest split to #1123
- [[logs/2026-10-14-19-25]] — #1134: undo after a browse carries a pending LIFT back; cancelled lifts end with their browse; 4 rows tested
- [[logs/2026-10-15-07-48]] — arbutus_os/ and emulator/ CLAUDE.md lose 1235 words of history to their as-built files; V0.1 lines marked

## #920 — BOUNCE as a job: CANCEL BOUNCE TO PLAY? (2026-10-14)

D98's second half (`screen-sketches.md` § FILE › PROJECT). FILE › BOUNCE sends
`clip_to_asset {track: "all", job: true}` and returns; the audio process renders a
quarter-second chunk a turn of its loop (offline, one a `render`) and pushes `bounce`
when the WAV is written (`audio/host/PROTOCOL.md` § `clip_to_asset`, *As a job*).
`app.bounce_job` is set **before** the message goes, because the push can arrive in
the same read as the reply. The FILE page draws `BOUNCING...` where the last bounce's
name goes, and `bounce_done` sets `bounce_path` and flashes `BOUNCED <file>` as before.

A played key while it renders puts up `file_jobs.Prompt` with
`CANCEL BOUNCE TO PLAY? · 1 YES · 2 NO` (`App._bounce_key`). YES sends
`bounce_cancel` (nothing was written) and the asking key then does what it does;
NO renders on and the asking key does nothing. *Run's calls, 2026-10-14:*
- **a played key is a note, PLAY or REC**: the ticket names a note and PLAY; REC
  starts the tape too, and a take beside a render is what the question is for;
- **another played key while the question is up waits with it** (swallowed); any
  other key or a turn answers NO and then does what it does (D107, the generic
  `Prompt` path);
- **a key let go before YES** plays from YES and lets go **a tick later**
  (`App._bounce_release`): a press and release in one block sound nothing;
- **the render ending under the question takes the question away**, and the
  asking key's release stays the question's;
- the host copies the clip lists and the mixer at the start, so the bounce is of
  the song as it was when BOUNCE was turned (the one-call bounce was the same,
  being one call).

Proof: `tests/test_project_file.py` `TestBounceJob` (seven tests: mid-render after
one chunk, YES held and let go, NO, PLAY, a page key, the render ending) and
`audio/tests/host_project.py` `test_bounce_as_a_job`; each branch's mutant seen red
on an assertion (the run's log names them).

## #632 — MIXER's cells, TRACK's loop framing and waveform cache (2026-10-14)

The first half of #632 (review m-emu-pages rows 3, 4, 7, 8, 12; m-emu-ui-drivers row 4); the
rest is #1123.
- **MIXER's level, pan and MASTER cells read `app.mixer` every frame** (`MixerPage._sync_params`,
  called by `slots()` and `restore()`): TRACK's ENC4 moves the master and NEW resets the model,
  and a cell that kept its own copy jumped the value back on the next detent.
- **`NativeAudio.media_gen`** counts the times a medium (the file a track's audio lives in) may
  have been replaced: bumped by `tape_dir` (NEW, LOAD, SAVE's move, a boot), `tape_load` and a
  finished compaction's push. `TrackPage._fresh_peaks` clears the waveform cache when it moved,
  because the cache's key is geometry and a new take can sit at an old offset.
- **The cache evicts its oldest reply at `PEAKS_CACHE_MAX` (256)**, never the whole cache: the
  clear at 65 made a bank with 66 clips on screen refetch every clip every frame.
- **`_frame_loop`** widens to 2.2 × the farther loop end from the playhead (`ui-choices.md` § #632).
- TEMPO's HELP for `‹` uses an ASCII minus: U+2212 is not in the font and drew `?`.

## #693 — RESET keeps the appearance; the phone's mode waits for the tick (2026-10-14)

- `Settings.reset()` (`app/settings.py`) restores every default but `system.appearance`, so
  RESET in dark mode leaves the screen and the setting agreeing (review R25's call: the mode
  follows the phone, ui-aesthetic § 3.1). `audio/library/README.md` § Settings says so.
- The host's `appearance` push no longer calls `set_appearance` from inside
  `NativeAudio._absorb_pushes`, which runs in any request, TRACK's `clip_peaks` during a draw
  included. `App._appearance_heard` stores the mode; `App.tick` applies it (and writes the
  setting) after the stores' ticks. `adopt_host_appearance` at boot and the `V` key still
  call `set_appearance` directly: neither runs mid-render.
- `test_palette_modes.TestContrast.ON` holds `ACCENT_BROWN`/`INK` on `SHIFT_HIGHLIGHT` and
  `INK` on `CLIP_FILL`/`CLIP_FILL_SEL` at § 3.4's figures; seen red by mutating
  `palette.TABLES` in memory in a scratch interpreter, `palette.py` never edited.
- ui-aesthetic § 3.4: exception 1 rewritten (the SHIFT row sits on the ground at 3.76 while
  held, not on the panel); exception 7 lists the fills with their figures, no floor.

## #1069 — the document clock compares `now >= last + interval`

`ProjectStore.tick` asked `now - _doc_last >= interval`; a caller that schedules
by `last + interval` (every test of the autosave clock, and `_due` already does
it this way) could get a "no" exactly on time, because the difference rounds a
hair under the interval at some clock values: `(t + 120.0) - t` is
119.99999999999989 at t = 957.209516383. The flaky
`test_while_rolling_the_clock_is_two_minutes` (`120.0 != 96.0`) failed on what
`time.monotonic()` happened to read, about one run in six in a fresh cloud
container (uptime near 1000 s), not on load; on the device a tick a frame
later writes, so nothing a player hears or sees changed. Proof:
`test_a_tick_exactly_on_time_writes_whatever_the_clock_reads` pins both
intervals at 502.0002, a clock value that rounds under for both; the old
comparison restored is red on it.

## #629 — `run.sh --phone`: the host's own clock, the caller's `--root`, the mock's record (2026-10-14)

From the code review of #331 (`docs/logs/findings/2026-10-06-t331.md`, rows 1–4).

- **An attached window reads `offline` off the host.** `main.py --attach` takes it from the
  host's `state` (`"offline"`, which `hello` and `state` carry) and ignores `--offline`;
  `native.offline` is set to match, so `render` is pulled exactly when the host needs it.
  The boot line says so: `audio: <firmware> (attached, offline)` or `(attached, live)`
  (it said `(attached)`). The launcher no longer passes `--offline` to the window.
- **A relative `--root` is the caller's.** `run.sh` remembers `$PWD` before its `cd` and runs
  `tools/emu_phone.py` from there, by absolute paths.
- **The mock records itself** in `run/mock.json` (`pid`, `screen`, `http_port`,
  `control_port`), written once its file port listens and removed by `stop`;
  `arbutus_os.mock.device.read_record(root)` returns it only while its pid is alive. The
  launcher reuses a mock only when the record says `screen: false`, refuses a host with no
  live record (its mock died), and prints the port the mock really serves. The `ps` parsing
  (`mock_has_no_screen`) is gone, with its `--root=DIR` and `IndexError` misreads.
- Two launcher flags for a new mock, used by the tests: `--tape-seconds`, `--no-mdns`.
- Tests: `arbutus_os/tests/test_emulator_phone.py` `TheLauncher` (run through `run.sh` from a
  scratch directory with a relative root; the reuse path without `--offline`; both refusals;
  a dead pid's record). Mutation: `offline = args.offline` in attach mode fails two of them.

## #1123 — the tape's undo, scrub grain, lone LIFT ALL clip, and `refresh` (2026-10-15)

The tape half of #1123 (review m-emu-app rows 5, 6, 11 and risk 9); the rest stays on #1123.
- **Undo is spent only on a change** (`Tape._commit`): SLICE, TAPE, slide, LIFT and LIFT ALL
  compare the new clip list with the old one and take the snapshot only when they differ, so a
  gesture on empty tape no longer erases the one undo level.
- **A slide is one undo group** (`push_undo("slide")`, like DDR-055's lifts): every SHIFT+ENC1
  detent after the first keeps the first one's snapshot; any other op closes the group. Run's
  call: the group is not closed by moving the cursor, so two slides of different clips with
  nothing between them undo together, as two lifts do.
- **The scrub grain expires on wall time** (`Tape._scrub_until`, `Tape.clock`): the host plays
  `SCRUB_GRAIN_S` from the last `scrub`, so a nudge after that reseeds at the cursor rather than
  letting the host play on from where its old grain ran to.
- **A LIFT ALL buffer drops on its own lanes whatever its count**: `do_drop` keys on
  `source_track is None` (LIFT ALL's mark), not on more than one clip; the strip says `1 CLIP`.
  SHIFT+DROP of a one-clip buffer still lays it on the selected track (a mixdown's target).
- **`NativeAudio.refresh` is guarded like `send`**: a dead socket marks `link_down`, records the
  error and returns the last state.
- Tests: `emulator/tests/test_tape_t1123.py`; `test_phase2a`'s one-level undo test now slices
  inside the clip (it sliced at the clip's start, a no-op that used to spend the undo).

## #1123 — a refusing card, the confirm's cancel, HELP's copy, a bar past its range (2026-10-15)

The pages half of #1123 (m-emu-pages risk 11, spec 9, spec 10; m-emu-ui-drivers row 6). Left on
#1123: pages risk 13 (LINK's clock jitter), ui-drivers rows 1, 3, 9, app spec 7 (Elvin's).
- **Risk 11, narrowed by D97/#954:** SAVE and SAVE COPY are gone and `project_file`'s writes
  already catch `OSError` (`write_refused`), so a read-only card no longer crashed NEW, LOAD,
  RENAME, RESET or STORAGE (probed with `open`/`makedirs`/`replace` refusing). Two holes were
  left: **`App.sampler_file`** made the sampler folder unguarded, so BOUNCE and both tape DROPs
  (SYNTH, DRUM) raised out of the key handler. It returns None on `OSError`; `PF.bounce` returns
  None (FILE says `BOUNCE REFUSED`) and the DROPs say `DROP REFUSED`. And **STORAGE's
  force-save said `SAVED <name> BEFORE USB`** after a refused flush; it now asks `PF.not_saved`
  and says `NOT SAVED BEFORE USB`.
- **Spec 10:** `App.on_key` calls `FilePage.cancel_confirm()` on a press of a transport key or
  a note, before handling it, so the run is put down and the key still does its own job.
  `cancel_confirm` acts only while armed (`disarm` also closes LOAD's browser). Run's call:
  **SHIFT and HELP do not cancel** — a modifier, and an overlay that never changes anything
  (DDR-033); the spec's "any other key" read as any key that does something.
- **Spec 9:** `PAGE_HELP`/`GLOBAL_HELP` rewritten for SYNTH (MOD), TRACK (8 tracks, two banks),
  SEQ (REC `1` for entry), INPUT and LIFT (all 8). The finding's better fix, each page owning
  its line, is not done.
- **ui-drivers row 6:** `frame._draw_param_cell` clamps the bar like the ghost, and
  `project_file.apply` sets the metronome's two params through `set_value` (which clamps,
  and its `on_change` clamps `app.metronome`).
- Tests: `emulator/tests/test_pages_t1123.py`.

## #1017 step 1 — `resync` stops reaching into App and ProjectStore (2026-10-15)

The S step of the hot-spot survey's item 1 (`docs/research/burn-b3-architecture-survey.md` § 1).
- **`App.forget_host_sync()`** clears every `self._*_fingerprint` (found by the suffix, so a new
  fingerprint in `_native_sync` needs no edit there) and `_playhead_seen`; `project_file.resync`
  calls it, then `_native_sync`, in place of setting the six fingerprints by name.
- **`ProjectStore.on_storage()` and `write_refused()`**, the public names of `_on_storage` and
  `_refused`, which `project_file.save`, `autosave` and `new` called from outside.
- Left for step 2: `project_file` still calls `App._native_transport`, `App._native_renamed`
  and `SlotStore._snapshot_fn`, and writes `ProjectStore.doc_written`/`doc_refused`.
- Tests: `emulator/tests/test_host_sync_forget.py` (every fingerprint `app.py` names, a seventh
  that does not exist yet, `resync`'s order). Mutation: a `forget_host_sync` listing the six by
  name fails the seventh's test.

## #1017 step 2 — one owner for the open project's document (2026-10-15)

The M step of the hot-spot survey's items 1, 2 and 5a (`docs/research/burn-b3-architecture-survey.md`).
- **`project_file.Document`** (`app.doc`) now holds the document half that sat on
  `persist.ProjectStore`: the working state's `project.json` (`snapshot`, the 2 s timer as
  `save_working`, `load_working`, `boot_folder`, `saved_active_instrument`, `boot`, `commit`,
  `flush`) and what the open project's autosave remembers. Renames, for grepping older notes:
  `doc_written` → `doc.written`, `doc_refused` → `doc.refused`, `doc_full` → `doc.full`,
  `_doc_last` → `doc.checked` (`doc.retry_now()` rewinds it), `fresh_key` and
  `boot_untouched` keep their names on `doc`; `save_project`/`load_project` →
  `save_working`/`load_working`; `project_path` → `doc.path`; `persist.DOC_INTERVAL`,
  `DOC_ROLLING_INTERVAL` → `project_file`'s; `PROJECT_INTERVAL` → `WORKING_INTERVAL`.
- **`ProjectStore`** keeps the tape: its folder, parking, `tape.json`, the media, the refusal
  journal (`write_refused`, now with `write_landed()` and `refusing` for `App.set_file_job`).
  `persist.py` no longer imports `project_file` (the two lazy imports are gone).
- **The last reach-ins became public names:** `App.halt_transport()` (was
  `project_file.halt` calling `_native_transport`), `App.resync_host()` (forget, then
  `_native_sync`), `App.tape_dir_renamed()` (was `_native_renamed`),
  `SlotStore.records_now()` (was `_snapshot_fn` read from outside),
  `ProjectStore.holds_old_media()`.
- Not done (the survey left it to the whole-system review's A5 design call): folding the slot
  stores or the phone's element map into the document.
- Tests: `emulator/tests/test_doc_owner.py` reads both files through `ast` (no `x._private`
  on anything but `self`; `persist` never imports `project_file`). Mutation: a
  `store._last_refusal` put back in `autosave`, and a `project_file` import in `persist.py`,
  each go red. The existing persistence suites moved to `app.doc` unchanged in what they assert.

## #1134 — a pending LIFT across undo, and a cancelled lift's lifetime (2026-10-15, cloud-2)

- **Undo after a browse** (`SynthPage._restore_edit`): the FX and MOD tab instances are read
  before `restore` and, for each tab whose name the restore changed, the view's own
  `_renamed(i, was)` runs, so a lift not yet dropped follows the restored box exactly as the
  browse moved it away (carried when the box has the parameter or output, cancelled on the
  strip when not). In practice the cancel arm is unreachable by gesture today: a LIFT turn is
  itself an undo entry (`("fx"|"mod", tab, …)` group), so the only undo that can follow a lift
  taken after the browse undoes that turn, not the browse. If it ever runs, `undo_edit`'s
  `UNDO · …` flash replaces its `LIFT CANCELLED` flash.
- **Undoing a route's lift** (found while reading, same row): lifting a route off the MOD
  master tab removes it and is an undo entry, so SHIFT+STOP put the route back while
  `lifted_route` still held it, and a DROP then installed it twice. `_restore_edit` now drops
  a `lifted_route` the restored rack already has (and a `paste_route` mode with it).
- **A cancelled lift lives only in the browser that cancelled it** (`rack._lost_here`): both
  views record the open `app.menu` beside `_lost`; once that menu is no longer `app.menu`
  (closed by a number, a page key, `goto`), `_lost` is dropped on its next read. Before this,
  the MOD view's `_lost` survived an FX browse closed by 3, so a later FX browse back to the
  original box reinstalled the cancelled route.
- Tests: `test_fx_boxes.TestScrolledName` (undo carries the lift back and drops; a cancelled
  FX lift stays gone in a later browse), `test_mod_rack.TestModRack` (undo carries a lifted
  source back; a cancelled route stays gone; a lifted route follows or is cancelled by a
  source change; a cancel ends an active `paste_target` and `paste_route`).

## #1016 — moved from `emulator/CLAUDE.md` (2026-10-15)

Verbatim, headings unchanged, so an old `emulator/CLAUDE.md` § citation finds them here.

### The phases (that file's intro)

320×240 pixel-perfect emulator of the Arbutus-1. Runs on Mac/Linux/Windows
without hardware. **Phase 1** = UI framework + SYNTH page. **Phase 2a** adds
DRUM, the 4-track tape (TRACK), and MIXER. **Phase 2b** adds the sequencer
(SEQ + endless engine), TEMPO with tap tempo and the metronome, HELP (page +
both hold overlays), and project persistence. **Phase 3** replaces the plain
window with the look-alike photo, clickable key hotspots and MIDI in.
**Phase 5** makes INPUT real: live mic sampling straight onto the SYNTH
sampler (DDR-036). USB/FILE was the last `NOT IN DEMO` stub; stage J1 built its
PROJECT subpage and **stage J2 built MIDI, USB and SYSTEM** — with that, the v1
spec package is built and nothing in this folder is a placeholder.

> **Native audio is the architecture (stage N1, 2026-09-17).** There is no
> Python audio path any more. `emulator/` is the **UI**: it draws, it routes
> keys, and it owns the tape's clip algebra — every sample of sound is made by
> the `arbutus-audio` process in `audio/`. `App(native=...)` is required and
> raises without it; `run.sh` starts the process for you. The Python engines,
> `core/`, `app/instrument.py`'s graph, `app/sequencer.py`, `app/mixer.py`'s
> DSP and `testing/null_audio.py` are **deleted** — read them at commit
> `3f64451`, the last one that still has them, or on `main` before the N1
> merge. See § Native audio becomes the architecture.

### Where each moved section went

Generated by `tools/ops/splitdoc.py`; a section added since lives where its author put it, so grep before trusting this list.

- `notes/chrome-and-input.md`: Live look editor (`--edit`); Chrome (phase 3); Rebuilding from a new photo; Hotspots and calibration; Mouse and MIDI; Keyboard map (still live alongside mouse + MIDI)
- `notes/audio-host.md`: INPUT; Native audio becomes the architecture
- `notes/state-and-files.md`: Sequencer pattern files; Persistence
- `notes/testing.md`: Tests; Verification harness (phase 4)
- `notes/as-built.md`: N3a review; N3b; E2; F2; G; I2; H2; J1; J2; Inspection 2026-09-18; Audit Q1; P3; P5; P6; D6; D5b; D3/D4's UI half; L3; #128; #105; S11; #95; #62 item 3; S12; S13; S14; C5b; #99; #68; #69; #164; #169; #174; Audit Q3; D17; #297; #303; As the mock device's UI (#331, 2026-10-03); #334; #517; Session History
- `notes/ui-choices.md`: Native audio becomes the architecture › `[TBD-Elfy]`; E2 › `[TBD-Elfy]`; F2 › `[TBD-Elfy]`; G › `[TBD-Elfy]`; H2 › `[TBD-Elfy]`; J1 › `[TBD-Elfy]`; J2 › `[TBD-Elfy]`; Audit Q1 › `[TBD-Elfy]`; D5b › `[TBD-Elfy]`; S14 › `[TBD-Elfy]`; #69 › `[TBD-Elfy]`; #297 › `[TBD-Elfy]`; #334 › Run's calls, 2026-10-03

### Layout › the deleted `core/`

`core/state.py` and `core/renderer.py` were deleted — hallucinated, never
verified. Do not resurrect that structure.

### No DSP in this folder › the old rule

The old rule (no per-sample Python loops; vectorise everything; the biquad is
the most expensive item in the graph) is history now — it is written up in
`docs/logs/2026-09-10.md` and it is why the C++ boxes could be checked against
the Python at all. The Python voice goldens it produced live on as `audio/tests/
golden/python/*.npy`, replayed against the C++ DSP cores by `ctest -R
voice_goldens`: the last thing the deleted engines are still doing is holding
the C++ to the sound they made.

### Polyphony: the amp envelope is per voice

This rule now lives in `audio/`, and the bug report is kept here because it is
where it was found. The ENV page's ADSR goes down as the host's `env` block and
`PolyHost` (`audio/sdk/poly.hpp`) applies it **to each voice**.

**Never put an envelope over the mixed engine output.** That is what phase 1
did, gated on `Instrument._held` going empty, and it made a new note re-trigger
the gate for every voice at once: a note still in its release was pulled to
zero and then dragged back up by the next note's attack — a volume jump on the
fading note, and old tails "riding along" with each new one. Under the
sequencer, which releases the step then fires the next, it happened every step
(found in Elfy's second play session, 2026-09-11).
`audio/tests/test_poly.cpp` pins the contract now.

Stealing is `PolyHost`'s: idle voice first, then the quietest voice already in
release, then the oldest held one. It used to always take voice 0, which
thrashed one voice while the other three rang out.

Engine encoder params are **timbre only** — Attack/Sustain/Release used to sit
on the engine *and* on ENV, two controls writing one envelope. The 8 slots come
from the box's descriptor (§3.5), so an engine cannot offer them any more.

### Run › `--audio=native`

`--audio=native` still parses, and is the only value: it is kept so old command
lines and scripts do not break, and it does nothing.

### Rules › the trust boundary's dated note

The 2026-09-07 note: the
archive, § *Moved from CLAUDE.md*.

## #1231 — one row of encoder cells, a 160 px main zone

`ui/zones.py`: params 68 → 36 px, main 128 → 160 (240 = 16 + 12 + 160 + 36 + 16); the row
is two pixels taller than one old 34 px row, so no padding was cut. `param_cell(col)` lost
its `row`. `ui/frame.py` `_draw_params` draws slots 0–3, or 4–7 on `SHIFT_HIGHLIGHT` while
`app.shift_held` (brown label, hollow dot, `↳ fine` from the slot below, as the old top row
did); the cell's contents sit 1 px under the bar's top rule. `app/ui_feed.py`'s element map
names only the four cells on screen, and SHIFT now triggers a remap. DDR-032 amended (the
run's call on the wording; the decision is Elvin's M7 Decision 17). Shots:
`docs/logs/assets/1231-*.png`, made by a scratch verify script (SYNTH › ENV, MIXER, TEMPO,
SHIFT up and held); play-queue P4.

## #1255 — OP1R-VERB's page is op1r's own reverb screen

op1r's Kali art vendored unmodified at `dbaa3f0` (`audio/third_party/op1r-src/VENDORED.md`:
the two SVGs, `gen_svg.py`, the real `kali_geom.h`, which replaced the box's stub,
`display/reverb_preview.png` and `harness/render.py`). `audio/engines/op1r/op1r-verb/screen/rasterise.py`
draws `kalireverb.svg` with render.py's own `draw_svg` (cairo, needs cairocffi and Homebrew's
cairo; run once, by hand) into `kali.npz`: the SVG's 38 top-level elements as 35 layers in
document order (static runs merged; 26 toggled groups; 3 needles as 1,025 sprites each, every
4/8192 of a turn, rotated with `op1_node_rotate`'s Q10 maths), premultiplied RGBA crops,
275 kB, 6.6 MB once loaded. `ui/kali.py`: `state()` is `kali_screen_update`; `render()` lays
the layers over black with pixman's rounded OVER, cached on the state; `--check` puts it within
1 level of cairo drawing the whole SVG directly (exact needles; at a 1/8192 grid it was 27 MB).
`pages/rack.py` `RackView._draw_box_screen`: the `op1r/op1r-verb` tab with no mode draws it
over the whole main zone from the tab's four normalised params (`kali.word` = the adapter's
`knob_q15`); no art → the usual page. Held out of the public export (`export.py` NEVER,
`allow.txt`). Tests: `tests/test_kali.py`. Against the preview, box-filtered 3× → 1×: mean
0.12–0.25 levels, 88–93 % of pixels identical, max 57 on a few stroke edges.
