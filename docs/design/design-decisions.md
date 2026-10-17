# Arbutus-1 — Design Decisions Record

**Status:** Living document. Companion to `screen-sketches.md`, `control-inventory.md`, `tape-sampling-workflow.md` (no YAML spec exists).
**Purpose:** Captures the *rules and rationale* behind the interface. The YAML
describes what the device does; this describes why, and constrains what future
additions are allowed to do.

**How to use with Claude Code:** Paste the relevant sections as constraints
before generating code for a mode or engine. Where a YAML node implements a
decision, reference it by number (`# see DDR-014`). If an implementation seems
to require breaking a rule here, that's a signal to revisit the design, not to
special-case the code.

---

## A. Interaction grammar

The core patterns. Everything else inherits from these. If a new feature can't
be expressed in this grammar, it either needs redesign or needs a page.

### DDR-001 — Tap commits, hold performs

**Rule:** For any control with both a momentary and a destructive form:
- **Tap** = commit. A permanent, undoable edit.
- **Hold** = perform. A momentary effect that reverts on release.
- **Hold + encoder** = perform with a live parameter.

**Why:** Three keys (REV, SPEED, FILTER) teach one pattern. Once learned it
transfers to every future key that has both forms. This is the single most
important rule in the document — it's what makes the panel learnable rather
than memorizable.

### DDR-002 — Transport state disambiguates live from destructive

**Rule:** Transport state selects which form of DDR-001 applies.
- **Playing** → the key is a performance effect. Non-destructive. Playhead
  never stops advancing underneath; release returns you to correct position.
- **Stopped** → the key is a clip operation on whatever the cursor sits over.
  Destructive, undoable.

**Why:** Not arbitrary — it follows from what the user is doing. Playing means
listening, so the gesture is performance. Stopped means the cursor is an edit
point, so the gesture is a commit. The user cannot be in both situations at
once, so the device cannot be ambiguous. Statable in one sentence in HELP and
never explained again.

**Generalizes:** Any future control with a live analogue and a destructive
analogue inherits this for free. This is a spare interaction dimension
available across the whole panel at no cost in keys.

### DDR-003 — Hold as universal modifier

**Rule:** Hold-a-key-then-press-another is an established modifier pattern,
not a hidden trick. Used by SHIFT, HELP, and the tape performance keys.

**Why:** Once the user learns it from any one key, every other use is
discoverable by experiment. Directly counters the OP-1 failure mode where
each hidden combination must be learned individually from the manual.

---

## B. Labeling and iconography

### DDR-004 — Concepts get words; physical actions with universal symbols don't

**Rule:** A key gets a text label if it names an abstraction the user must
learn (SYNTH, MIXER, SEQ, TEMPO, LIFT, DROP). A key gets no label if it names
a physical action with a pre-existing universal glyph (play, stop, record).

**Why:** The apparent inconsistency is a category difference, not an
oversight. Labeling transport spends the key's scarce space teaching something
nobody needs taught, and shrinks a glyph that gets hit by feel, mid-take, eyes
on screen. The absence of text also marks transport as a different *kind* of
control, which aids peripheral scanning.

**Exception:** If a transport key gains a non-obvious shift function
(punch-in, overdub-vs-replace), that function is a concept and gets a mark.
The primary function stays bare.

### DDR-005 — Dual-label position convention

**Rule:** On any dual-function key: **bottom label = unshifted, top label =
shifted**, with the shifted label in the SHIFT accent brown. Never varied.

**Why:** The panel teaches the convention once instead of once per key.
Applies to SLICE/TAPE and USB/FILE.

### DDR-006 — SHIFT defines the accent color

**Rule:** The SHIFT key is oversized text in the accent brown, with the
keyboard shift-arrow glyph. It is the panel's legend.

**Why:** Without it, brown-means-shift is something the user must *induce*
from scattered examples, and many never will. One large key stating the color
converts induction into instruction. Cases where shift does something
unlabeled aren't contradictions — the key defines what the color means when
present, not that every shift function is marked.

### DDR-007 — Color assignments

**Rule:**
- **Brown** = shift function / page-level function
- **Green** = persistent state and slot identity
- **Navy** — reserved, unassigned

**Why:** Navy is deliberately unspent. The temptation is to use it to separate
the live tape keys (REV/SPEED/FILTER) from the loop keys, but under DDR-002
those keys are genuinely *both* live and destructive — coloring them as one
would contradict the grammar. The likely correct future use is encoder-
parameter linkage: each encoder gets a color, on-screen parameters inherit it,
and a key indicating "this encoder is now live" carries that encoder's color.
Worth reserving for a real mapping rather than spending on grouping.

### DDR-008 — Slot identifiers are letters, not numerals

**Rule:** Memory slots are lettered **A B C · X Y Z** (6 per page, matching the printed keys; amended 2026-09-08 from A–H), set in a small tab outline.
Contextual selector keys are bare numerals **1–4**.

**Why:** Two sets of adjacent numerals makes "press 3" ambiguous in the spec,
in HELP text, and in the companion app. Letters are already idiomatic for slots
(Elektron banks, MPC pad banks, A/B patch banks) so nothing needs teaching.

**Roman numerals rejected:** I/II/III are counted rather than read, so finding
slot 3 in a hurry becomes arithmetic. VII/VIII crowd the sub-label. And Roman
I next to Arabic 1 reintroduces the exact collision being avoided.

**The tab shape** carries "one of a series" — a file-folder or cassette-spine
tab. Stays inside the tape/file metaphor, distinguishes slots from bare
numerals before the character is even read, and its flat bottom edge gives the
sub-label a shelf (same relationship LIFT/DROP have with their bars).

### DDR-009 — Icon inventory and rationale

| Key | Icon | Reasoning |
|---|---|---|
| TEMPO | Metronome | Unmistakable silhouette; stays in the physical-object family |
| USB/FILE | Trident + floppy with FILE inside | Floppy is fully abstracted into a glyph — universally read, never touched. Four shapes, survives being tiny |
| LIFT/DROP | Arrow over a bar | Abstract *by necessity* — see DDR-011 |
| IN/OUT | `[` and `]` brackets | Matched pair, each implies the other; identical to every DAW's loop boundary |
| LOOP | Cycle arrows | Completes `[ ]` + loop as a readable sentence |
| REV | Rewind chevrons + mirrored "ERSE" | The mirrored text tells you what the key does before you read it |
| SPEED | Fast-forward triangles at **unequal sizes** | Standard FF says "a rate"; unequal says "a *variable* rate" |
| FILTER | Filter curve | The one universal EQ glyph; doubles as a hint that the encoder sweeps |
| 1–4 | None | See DDR-012 |

---

## C. Tape and clip operations

### DDR-010 — The three tape keys are REV, SPEED, FILTER

**Rule:** Under DDR-001/002, each key has two forms:

| Key | Playing (hold) | Stopped (tap) |
|---|---|---|
| REV | Playhead runs backward, snaps forward on release | Reverse the clip under cursor |
| SPEED | Live speed/pitch adjust via encoder, on-screen readout | Apply last-set speed to clip under cursor |
| FILTER | Sweep filter via encoder from center detent | Bake current filter setting into clip |

**Cut from the OP-1 model:** stutter and tape stop. Both are *fixed* — a
gimmick used twice. If either returns, it returns with hold+encoder control
(stutter division 1/4→1/32 under the thumb becomes a fill generator rather
than a stunt).

**SPEED remembers its last value**, so the workflow is: hold while playing,
dial in the feel, release, then tap on any clip to apply. No playback needed
for the commit.

### DDR-011 — LIFT/DROP naming and semantics

**Rule:** The keys are named LIFT and DROP, with text labels.

**Why not CUT/PASTE:** Lift is destructive from timeline sources and
non-destructive from library sources. CUT would be accurate half the time,
and a conditionally false label is worse than a vague one — the user builds a
wrong model and gets punished for trusting it. "Lift" is neutral about the
source in exactly the way English already allows.

**Why labeled at all:** The arrows show a *direction*, not a *concept*. You
can read "something goes up" while having no idea a clipboard exists. Words
close that gap; four letters each, and the flat-bottomed glyphs leave the
space unused otherwise.

**Why not a waveform in the bar:** Lift/drop are context-sensitive by nature —
tape region, slice, pattern, whole track. An audio-specific icon starts lying
the moment it's used on a sequencer pattern. The abstract arrow is honest
about being a verb applied to whatever's under the playhead. (A slightly
thickened bar reads as a *clip* without the mush a tiny waveform becomes.)

### DDR-012 — The lift semantic rule

**Rule:** **Lift removes from timeline-position sources; lift copies from
library sources.** Tape has a hole in it if you take audio out; a synth sample
slot doesn't.

*Amended 2026-09-16 (DDR-055):* "library source" means the **loaded** sample on
an instrument page. In a **browser**, LIFT on an item is a file operation: the
item leaves the list (moves to `.trash/` if never dropped, moves to another
folder if dropped there). Position matters in a list just as on the tape, so the
same sentence still holds: lift removes from wherever position is the point.

**Why:** Same key, same gesture, destructive in one place and not another is
exactly the OP-1 pattern being designed against — a behavior learned only by
losing a take once. Stating it as a single rule makes it learnable in one
sentence in HELP.

**Warning sign:** If exceptions to this rule accumulate, the *behavior* needs
to change, not the documentation.

### DDR-013 — Clip delete is LIFT-without-DROP

**Rule:** No dedicated delete key. Lifting and not dropping is the delete.

**Why:** Cleaner than a separate key, and the clip stays recoverable until the
buffer is overwritten — a better failure mode than a real delete.

### DDR-014 — Clip operations and performance effects stay physically separate

**Rule:** Destructive, deliberate, non-time-locked clip edits (normalize, fade
edges, duplicate-to-fill) live on the TAPE page or under SHIFT. They do not go
on the performance keys.

**Why:** Mixing them breaks the green/orange logic and the panel stops
teaching its own categories.

---

## D. Tempo and grid

### DDR-015 — Page is named TEMPO, not METRONOME

**Rule:** TEMPO, with a metronome icon.

**Why:** Naming the page after the smallest thing on it is part of why the
OP-1 equivalent is hard to find — you go looking for BPM and the affordance is
called "metronome." Someone hunting tempo finds it first try; nobody hunting
the click misses a page called TEMPO with a metronome on it. Icon carries the
click, word carries the grid.

### DDR-016 — BPM sets the grid, not the audio

**Rule:** Changing project BPM moves snap points and tick marks. It does not
rescale recorded audio.

*Amended 2026-09-16 (DDR-059):* true with `LINK` **off**. With `LINK` on (the
default) BPM and tape speed are one control, and audio is *resampled* — repitched,
never time-stretched (DDR-017). The prohibition here is on algorithmic stretch,
which stands.

**Why:** This is a design position, not a limitation. Tape holds sound; the
metronome holds the ruler you measure it against. The alternative requires
timestretch — expensive, worse-sounding, and it quietly promises a DAW
relationship the rest of the device doesn't honor. Also enables deliberate
polyrhythmic work.

### DDR-017 — Speed and pitch stay coupled

**Rule:** All speed changes are resampling. Pitch follows speed. No smart
stretch anywhere.

**Why:** Computationally trivial, sounds good, and it's the device's
signature character. SPEED (DDR-010) exposes it as a one-finger move.

---

## E. Project, files, and storage

### DDR-018 — Bake is two operations, not one

**Rule:**
- **Bake range → sample slot** lives on TAPE, near SLICE. A musical move made
  mid-flow, feeding back into the instrument.
- **Bake project → stereo file** lives on FILE (or the app). A session-end
  move producing an artifact for elsewhere.

**Why:** On the OP-1, Album/bake exists because tape is the only storage —
baking is how you free capacity. It's a storage operation wearing an export
costume. Once project save/load exists, that coupling has no reason to
persist, and collapsing both into one button puts a creative gesture behind a
filing-cabinet door.

### DDR-019 — On-device FILE page is scoped to offline-necessary only

**Rule:** On-device: new / save / load project, storage remaining, USB mode
switch, auto-naming. Everything else — waveform browsing, stems, MP3, tagging,
patch sharing — is the companion app's job.

**Why:** Keeps the hardware interface clean without capability loss.

### DDR-020 — Auto-naming, always

**Rule:** Projects auto-name on save (`PROJ_0037` — date + increment). Renaming
happens in the app.

**Why:** The offline path needs *some* handle, and text entry on-device is a
tax on every save. Naming is a phone job.

**Amended 2026-10-03 (#157 → #297):** **the first SAVE of a session invents
the name**, not every SAVE. Elfy's answer (a) on #157 made SAVE mean "save the
project I have open": the tape is written into the open project's own folder
as it is recorded, so a later SAVE rewrites that project's `project.json` and
copies no audio, and the first SAVE of a session never saved or loaded makes
the auto-named folder and *moves* the tape into it. A separate gesture,
FILE › PROJECT **SHIFT+ENC3** (save-as-copy), makes a new auto-named project
with copies of the media and leaves the instrument on the one it had open.

### DDR-021 — USB mode transition force-snapshots

**Rule:** Entering USB mode auto-snapshots the current project first. No
prompt, no decision.

**Why:** The device changes what it fundamentally is mid-session. If working
state isn't committed, the failure is silent and total. The user should not
have to know that plugging in a cable is a dangerous moment.

---

## F. Display and discoverability

These are where the OP-1's real failures live. None of them are fixable with
iconography.

### DDR-022 — Contextual binding strip for 1–4

**Rule:** Every page that binds 1–4 shows a persistent four-cell strip along
the bottom of the display naming the current bindings.

```
Track:  TRK1   ·  TRK2 ·  TRK3 ·  TRK4
Drum:   ENGINE ·  ENV  ·  FX   ·  LFO
Synth:  ENGINE ·  ENV  ·  FX   ·  LFO
Mixer:  MIX    ·  EQ   ·  FX   ·  MASTER
```
(Amended 2026-09-08: DRUM mirrors SYNTH; kits are slot letters, not 1–4.)

**Why:** 1–4 are an *index*, not a meaning — their contents are supplied by
the page. Numbers are the correct symbol for that, and any icon would be a lie
four pages out of five. But numbers tell you *where* and never *what*, which
is the single biggest source of "I don't know what this device is doing."
Four short words, always visible, converts the most confusing control on the
panel into the most legible one.

**Design constraint this creates:** if a page has up to four of something, it
goes on 1–4. If it has five, the page is designed wrong or needs a different
affordance.

### DDR-023 — 1–4 selection behavior is consistent everywhere

**Rule:** Press = select. Selection persists. Active cell is highlighted in
the strip. Never a momentary jump on some pages and a persistent selection on
others.

**Why:** On the OP-1 this varies by page and is a quiet source of confusion.
Made consistent, 1–4 is learnable in about thirty seconds and stays learned.

### DDR-024 — Lifted-state indicator

**Rule:** When something is held in the lift buffer, a persistent strip shows
what it is *and how it was taken*:

```
LIFTED: TRK2 4.2s — REMOVED
LIFTED: SYNTH SMPL — COPY
```

**Placement (2026-09-08):** the dynamic strip directly under the header (DDR-031).

**Why:** On the OP-1 you cannot tell what you lifted or whether anything is in
the buffer at all. This makes DDR-012's model visible at the exact moment it
matters, makes shift-lift need no icon explanation, stops drop from being a
coin flip, and makes undo legible.

### DDR-025 — HELP + any control explains that control

**Rule:** Hold HELP and press any key **or turn any encoder** to surface an
explanation of that specific control in its current context. The first HELP
screen points this out.

**Why:** Inherits the hold-modifier pattern from DDR-003, so it teaches
nothing new. Encoders included because "what does the blue knob do on this
page" is the question users actually have most often.

---

## F. Engines and the rack (2026-09-15)

Rules from the engine/rack/SDK grill. Contract: `docs/design/engine-spec.md`.

### DDR-038 — The FX page is a rack; the tabs are behind the first page

**Rule:** The FX subpage holds 1–8 engines in tabs. Tab 0 is the master page (the
rack's ≤8 macros); tabs 1..n are each engine's own 8-slot page, editable
directly. A rack with one engine shows that engine's page and *no* tabs.
**SHIFT + ›** on the FX subpage reveals an empty next tab; SHIFT + ‹ › moves
between tabs. HELP-hold on the page explains this.

**Why:** A beginner picks a compound effect and uses the eight knobs its author
chose, never knowing there is a chain. A player who wants inside it finds the
door with one gesture and nothing is hidden — "hidden" means *out of the way*,
never *sealed*. No phone is required for any of it: all sound design is on
the device.

### DDR-039 — LIFT and DROP bind a parameter to a master knob

**Rule:** On an engine tab, LIFT → the page enters copy mode (cells highlight,
strip reads `TURN A PARAM TO COPY ITS ADDRESS`); turning a parameter lifts its
address. On the master tab, DROP → paste mode (`TURN A KNOB TO PASTE`); turning
a macro slot binds it. Dropping another lifted address onto the same slot
multi-assigns (one macro → many targets). LIFT then DROP with nothing turned =
cancel. The lifted address shows in the dynamic strip like any lift buffer
(DDR-024).

**Why:** Pick it up where you found it, put it down where you want it — that
is already what LIFT/DROP mean on this instrument. No menus, no new keys, and
the mode is visible on screen rather than "the last thing I touched".

### DDR-040 — Ranges are set by ear

**Rule:** Each binding has LO · HI · CURVE · DEPTH, edited in a popup on the four
encoders. While LO or HI is turned, the target parameter sits at that value
so the player hears the bound. A red marker shows where the macro's current
position lands inside [LO, HI]. LO > HI inverts.

**Why:** A macro is musical only when every position on the knob sounds like
something you would keep (the PSS-480 "brilliance" principle, DDR-044). Bounds
chosen by listening are the whole point; typed numbers are not.

### DDR-041 — Engines never see command keys; sequencer soft-keys are the one exception

**Rule:** An engine receives musical keys, clock, transport and its ≤8
parameters. Command keys, arrows, page keys, LIFT/DROP never reach it. The
single exception: an engine declared as a sequencer may declare ≤4 *labelled*
soft-keys, which the host maps to 1–4 (and SHIFT+1–4) on the SEQ page, draws
on the strip, and explains via HELP-hold. No other engine type gets them: 1–4
are subpage keys everywhere else.

**Why:** Forwarding raw keys turns "keys mean what the instrument says" from a
guarantee into an upload-review policy. Labelled soft-keys keep it mechanical
— a key's meaning is on screen above it — and sequencers are exactly where
gestures have room to grow.

### DDR-042 — No cost estimate; the CPU flag tells the truth

**Rule:** The device never predicts whether a rack will fit. It plays it, counts
xruns, and raises a visible flag (`CPU NO HAPPY — EFFECT TOO BIG`, copy TBD)
until the rack changes.

**Why:** An estimate that is wrong by 3× either refuses racks that would have
worked or admits ones that glitch after promising they fit — the second is
worse than no promise. Measured costs exist offline (`arbutus-run --bench`)
for authors; the player gets the truth.

### DDR-043 — A shared thing that cannot load says why, and loads nothing

**Rule:** A rack, patch or kit that needs an engine the device lacks, a newer
engine version, a newer ABI, or a build for another architecture is refused
with the specific reason (`NEEDS ENGINE author/name`, `NEEDS FIRMWARE
UPDATE`, `NO BUILD FOR THIS DEVICE`). Never bypass a missing engine silently.

**Why:** A bypassed engine plays a sound that is not the shared sound, under the
shared name, and the sharer gets judged by it. A clear refusal points the
player at the update path instead.

### DDR-044 — Synth engines ship tuned; no macros, no re-ranging

**Rule:** A synth or drum engine exposes ≤8 fixed parameters whose ranges its
author has bounded so that every position sounds like something worth hearing.
Synth engines have no macro layer and the player never re-ranges them. Racks
are for effects.

**Why:** The PSS-480's five sliders ("brilliance", …) mean nothing
mathematically and everything musically: they combine parameters and bound
them to the interesting region. The opposite — the demo FM engine's raw
index/ratio with unlistenable defaults and tiny bliss points — is the failure
this rule forbids. Tuning is the author's job, once, not the player's, every time.

---

## G. DSP posture

### DDR-026 — Temporary DSP is cheap; commit the result

**Rule:** Expensive processing runs only while a key is held. The tap-commit
renders it into the clip permanently.

**Why:** Peak CPU cost is bounded by "one key held at a time," and the baked
result is free forever after. This is the general form of the bake philosophy
— progressive commitment as a performance strategy, not just a workflow one.

### DDR-027 — Filter over compression on the performance keys

**Rule:** FILTER occupies the third tape key. Master compression lives in the
mixer as set-and-forget.

**Why:** Compression is invisible on a first press — the user hears "slightly
different" and learns nothing. Filter is the most legible effect in music;
sweep it and anyone understands the control instantly. For a device betting on
discoverability, that decides it.

---


### DDR-028 — No page ever loses your place

**Rule:** Every page retains its complete state (subpage, selection, scroll,
open menu, entry mode) when left and re-entered. Navigation never resets
anything. TEMPO is a full page for this reason — there is no cost to leaving.

**Why:** "Go check something and come back" is the most common gesture on an
instrument. If coming back costs re-navigation, users stop checking.

### DDR-029 — Everything autosaves; slots recall snapshots

**Rule:** There is no save gesture. Every change is written to the active
slot letter immediately. Tapping a slot recalls its full snapshot (engine,
patch, all parameters, page state). SEQ slots hold sequencer engine + pattern
+ params.

**Why:** A save step is a tax on every edit and a way to lose work. Six slots
× autosave gives "undo by switching" for free.

### DDR-030 — Engine menus are two-pane, discovered, never hardcoded

**Rule:** Every selector (synth, drum, effect, sequencer) is a full-page,
two-pane list: left = engine (folder), right = patch/sample (item). ENC1
scrolls folders, ENC2 items. Selection applies live; there is no confirm;
any page key dismisses. Contents come from the library directory tree.

**Why:** Open ecosystem — engines are drop-in. A single-pane list would have
to special-case engines with and without patches. Live-apply + autosave makes
a confirm step redundant.

### DDR-031 — Dynamic strip carries all non-universal status

**Rule:** One line under the header shows status tickers (recording target,
lift buffer, sequencer state, held-key readouts), concatenated by priority
`REC > LIFTED > SEQ > other`, each with a long and short form. The header
itself is breadcrumb only. Layout height is constant; empty = blank.

**Why:** Almost no status is needed on every page, so nothing earns a
permanent slot. A subtitle line that says what the device is doing right now
replaces the OP-1's "why is it doing that" moments.

### DDR-032 — Layout zones are fixed

**Rule:** Header / dynamic strip / main / param bar / key strip.
*Amended 2026-10-15 (#1231, Elvin's M7 Decision 17; the run's call on the wording):* the
param bar is **one row** of four cells (36 px): slots 0–3, and while SHIFT is held the same
cells show slots 4–7 in the brown SHIFT style. That gives the main zone **160 px**, an OP-1
engine screen's 320 × 160. Header 16 / strip 12 / main 160 / params 36 / key strip 16.
Heights never change between pages or states. Overlays draw over main only.
Menus cover main + params but keep the header.

**Why:** Eyes learn positions. A strip that appears and disappears moves
everything else and breaks that.

### DDR-033 — HELP is a page and a modifier

**Rule:** Tap HELP = help page (teaches the hold gesture). Hold HELP = overlay
explaining the current page. Hold HELP + key/encoder = overlay explaining that
control here. Overlays never navigate; release dismisses. (Extends DDR-025.)

### DDR-035 — A pattern is armed everywhere, and says so

**Rule:** Tapping SEQ when you are *already on* the SEQ page arms and disarms
the pattern (ENC3 ACTIVE mirrors it, and reads dim while off; default off).
While armed the chromatic keys drive the pattern on **every** page; while
disarmed they belong to the instrument everywhere, SEQ included.
Re-triggering on a new key while one is already held changes the transpose
and keeps the running timing.

**Why:** The move the device exists for is "arm a track, hit a key, the beat
rolls into the take". Making that require a trip back to SEQ, or HOLD, breaks
the take. Restarting the phrase on a re-trigger loses the groove mid-bar, so
transposition is a parameter change, not a new start. Arming lives on the SEQ
key itself because that is the key already under the finger, and pressing a
page key you are already on is otherwise a no-op — DDR-028 says navigation
costs nothing, so the second press is free to mean something.

### DDR-034 — Active instrument

**Rule:** The chromatic keys always play the *active instrument* = the
last-visited of SYNTH / DRUM, on every page. A running sequencer drives it.
Recording captures its output.

*Amended 2026-09-16:* each instrument owns its **own** sequencer (engine +
pattern + armed state). Only the active instrument's sequencer runs; switching
SYNTH↔DRUM stops the running one and resumes the other if it was armed. "A
running sequencer drives *its* instrument" — which is always the active one.

*As built 2026-09-23 (S13, #59):* the host has kept a SEQ slot per instrument
since engine-spec §15.4; the UI now keeps a `NativeSequencer` per instrument to
match (`App.sequencers`), and `App.sequencer` is the active one's — which is
what the SEQ page shows and edits. A sequencer is armed on its own instrument
and cannot move to the other: there is no gesture that would, and the box is
not "floating" any more. The sequencer rides in that instrument's A–C/X–Z slot
record, inside the §7.2 patch; the SEQ page's own six letters stay a
**sequencer-only** bank recalled onto whichever instrument is showing
(`control-inventory.md` § Slot keys).

**Why:** One answer to "what will I hear if I press a key" everywhere.

### DDR-036 — Sampling waits for the sound, not for a keypress

**Rule:** INPUT arms and then triggers on the first transient over THRESH,
keeping the ~20 ms *before* it from a rolling buffer. The capture ends on
~350 ms of quiet or at LENGTH, is trimmed, normalised and faded at both ends,
then saved into `library/synth/sampler/` and loaded onto SYNTH. No transport,
no save step, no naming. The fade-in is capped at the run-up to the attack so
it can never reach into the transient.

**Why:** By the time a human has heard a tap and pressed a key, the attack is
gone — and the attack is the part that makes struck glass sound like glass
rather than like a tone. Arming and waiting on the transient is also the only
version that works one-handed while the other hand holds the object. The trim
and the fades are what make the result loop cleanly without an edit pass, which
is what lets a sample become a sequencer pattern's voice in one move.

**Scope note:** this reverses `hollyhock-demo-spec.md`'s "INPUT sampling —
out of scope". Sampling turned out to be the most demonstrable feature in the
build: it is the one thing an audience can watch *become* an instrument.

### DDR-037 — A library item may carry content, not just parameters

**Rule:** An item file whose JSON holds a `pattern` key is loaded by the
engine's own `load_pattern()`. `Library.apply_item` looks for the key and the
method; it never names an engine.

**Why:** Shipping `penta-7.seq` should be a file, not a code change — the same
rule DDR-030 already applies to engines. Selecting a sequencer item previously
only renamed it, which was invisible while the only item carried no content.

---

## J. Modulation, tape, mixer, system (2026-09-16)

Rules from the OP-1 gap grill (`docs/logs/2026-09-16.md` session 2; audit in
`op1-gap-audit.md`). Contracts: `modulation-spec.md`, `engine-spec.md` §15,
`tape-sampling-workflow.md`, `companion-app-spec.md`.

### DDR-045 — A modulator is an engine

**Rule:** Anything that moves a parameter without a hand on it is an engine with
`ARB_CAP_MODULATOR`: a box, shareable, written with the SDK, ≤4 named outputs.
Subpage 4 (`MOD`) is a rack of ≤4 of them, the FX rack's mechanism reused.

**Why:** The OP-1's seven fixed LFO modes are the ceiling of what a closed list can
be. Making the LFO a box means the community writes the eighth, and anything
complex (multi-stage, recursive) is a box that takes one tab instead of a
routing screen. Four tabs because that is what four encoders can own on the
master page.

### DDR-046 — Modulation is relative to the knob

**Rule:** A route swings the target *around its knob value* (bipolar) or adds on
top of it (unipolar), scaled by a signed depth. Turning the target moves the
whole excursion. An optional LO/HI window bounds it, set by ear with the FX
rack's popup.

**Why:** The knob keeps meaning something while modulated — that is how the OP-1's
helicopter exercise works and how every player already thinks of an LFO. Absolute
lo/hi is right for macros (where the author bounds a range once) and wrong for a
vibrato you want to shift with the filter you are also sweeping.

### DDR-047 — The amp envelope is not a modulation source

**Rule:** The ENV page's ADSR is per voice and only ever shapes amplitude. The
aim-anywhere envelope is a separate `envelope` box, drawn differently.

**Why:** The OP-1's Element LFO with "envelope" as its source welds the two, so a
pluck's filter envelope and its amp envelope cannot differ — Elfy's most-felt
limitation. A per-voice envelope also cannot honestly be exposed as a global
value.

### DDR-048 — Modulators are global; velocity is the per-voice exception

**Rule:** A route applies one value per block to a knob, all voices together.
`keys.VEL` / `keys.NOTE` routed to a param flagged `per_voice` is applied inside
the engine per voice (`set_voice_mod`). No other per-voice modulation in v1.

**Why:** Global is what the OP-1 does and what makes a modulated sound feel like
one instrument; per-voice needs a voice concept the host deliberately lacks.
Velocity is per note by nature and the SDK already owns per-voice state, so
that one costs nothing. P3 keys have no velocity sensing: velocity is MIDI, or
IMU-inferred (SYSTEM › KEY VEL = MOTION) as the Field does.

### DDR-049 — Second press of the subpage key you are on = bypass

**Rule:** On FX (key 3) and MOD (key 4), pressing the key again toggles bypass:
the whole rack on the master tab, the current node on an engine tab. State is
stored per tab.

**Why:** The OP-1 toggles FX/LFO with a second press of T3/T4 and it is the most
used A/B gesture on the device. DDR-035 already spent "press the page key you are
on" on SEQ arming; this is the same free gesture, same reasoning — navigation
costs nothing (DDR-028) so the second press is free to mean something audible.

### DDR-050 — No modulator modulates a modulator

**Rule:** Routes target engine, ENV, FX and host params only. Depth 1, as
parallel groups were before they were cut (engine-spec §6.5).

**Why:** Recursion in the host is a patching UI this instrument will not have. An
author who wants it writes it inside a box, where it is forced to be usable and
takes one tab.

### DDR-051 — A modulated parameter shows where it is

**Rule:** The cell keeps the knob value as its label and draws a ghost marker at
the live modulated position. The bar moves; the number does not.

**Why:** DDR-022/024's principle — when meaning is supplied by something else,
the display says so. A knob that reads 0.50 while the filter is obviously
sweeping is a lie.

### DDR-052 — Glide and bend are host-owned voice policy

**Rule:** ENV SHIFT row is `MODE · GLIDE · DETUNE · BEND`. GLIDE = portamento in
mono/legato, DETUNE = unison spread, BEND = bend range in semitones. Bend comes
from MIDI or from SHIFT+‹ › held on SYNTH/DRUM, ramped over GLIDE, snapping back
on release (DDR-001). CURVE is dropped.

**Why:** These are voice policy, which the ENV page already is (§4 of the engine
spec). SHIFT+‹ › on those pages was free, and using it makes the arrows part of
the instrument, not just navigation.

### DDR-053 — Drum ENV is a per-pad AD; transient shaping is an effect

**Rule:** DRUM › ENV shows ATK · DEC per pad (S/R greyed). The Field's transient
shaper is `arbutus/transient`, a rack box.

**Why:** The greyed page was a hole; an AD is what a drum voice actually has; a
transient shaper is useful on anything, so it belongs in the rack where anything
can use it.

### DDR-054 — SHIFT+STOP on SYNTH/DRUM is one-step undo, not revert

**Rule:** SHIFT+STOP undoes the last edit (a run of turns on one param, a module
swap or a patch load). There is no "revert to the patch as loaded"; reload it
from the browser.

**Why:** SHIFT+STOP means "undo one thing" everywhere else; a multi-step revert
under the same key breaks that sentence. The loaded patch is still in its folder.

*As built 2026-09-22 (S12, #58), amended 2026-09-23 (#152):* one step is a run
of turns on one cell, a turn on an FX or MOD cell, a MOD route pasted onto a
target, **each browser opened on the page** (a whole browse, not each pick), and
DRUM's pad copy or a tape drop. **The `‹ ›` octave is not one of them** (Elfy,
#152): like the SHIFT+`‹ ›` bend and a note, it is something the player does to
perform, not to the sound, so it records no step — and because the octave rides
in the page's snapshot, a move also moves the step already held on with it, so
undoing that step leaves the octave where the player put it. On these two pages
the gesture never reaches the tape's own undo and never stops the transport; a
slot recall, FILE › NEW and a project load each forget the step
(`emulator/notes/as-built.md` § S12).

### DDR-055 — Browsers are file managers: LIFT moves, lift-without-drop deletes

**Rule:** In any browser, LIFT takes the highlighted user item; DROP in another
folder moves it; DROP at folder level makes an auto-named folder; never dropping
sends it to `.trash/`, which the app empties. Stock content does not lift.
Consecutive lifts form one undo group (SHIFT+STOP puts them all back).

**Why:** One grammar (DDR-011/013) instead of a delete key or a confirm dialog;
soft delete because it is the only destructive gesture with no natural undo;
grouping because "lift, lift, oops" is exactly how you lose the first one.

### DDR-056 — Overdub is the only record mode

**Rule:** A take sums the instrument with what the track already plays. Existing
clips are split at the take's boundaries; the covered portion is absorbed into
the new clip; the remainders stay. A track's clip list is never overlapping.
Replace = LIFT first.

**Why:** Overdub is the tape idiom (OP-1 §8.3) and the layering workflow; a
replace mode is a second rule to learn and a way to lose a take. Absorbing rather
than stacking keeps LIFT/SLICE/DROP one-dimensional.

### DDR-057 — STOP+‹ › jump to the loop or tape ends; SHIFT+‹ › never changes meaning

**Rule:** STOP held + ‹ / › = loop in / loop out when loop is on, tape start /
end of last clip when off. SHIFT+‹ › always steps by the current tick, loop or
not.

**Why:** Getting to a loop edge to trim it is the most common tape move; the
OP-1's SHIFT+arrows changing meaning when looping is exactly the conditional
behaviour DDR-012's principle forbids.

### DDR-058 — The tick grid follows zoom

**Rule:** TRACK draws the finest subdivision that keeps 8–16 ticks on screen
(8 bars … 1/32, then seconds/ms/samples below ~250 ms). SHIFT+‹ › steps one
tick; REC+note snaps to the nearest tick.

**Why:** Zoom is already how you choose precision; a separate grid setting is a
second control for the same intent. Shelved as a backup (`SNAP` on TRACK's SHIFT
row) if the ladder does not feel right.

### DDR-059 — LINK: tempo and tape speed are one control by default

**Rule:** TEMPO page `LINK`, default on: BPM and tape speed move together and
audio is resampled (DDR-017). Off: BPM moves only the grid. The SPEED key
remains a per-clip effect.

**Why:** Slowing a song before adding drums should slow the sequencers with it;
that is the OP-1's beat match and the more musical default. The off state exists
for recording a second song at a new BPM on the same tape without repitching the
first.

### DDR-060 — Eight tracks in two banks

**Rule:** The tape has 8 tracks. TRACK shows one bank of four; SHIFT+1–4 flips
to the other bank *and* selects that track; plain 1–4 select within the shown
bank. A bank glyph appears once bank B holds a clip. The mixer is 8-wide.

**Why:** Four is the OP-1's limit, not a virtue; 8 makes the resample-to-track
mixdown free and doubles layering without a second page. Revealing bank B only
once used keeps the first session as simple as four.

### DDR-061 — Mute on SHIFT+1–4, solo on hold

**Rule:** On MIXER, SHIFT+1–4 mutes tracks within the shown bank (bank via
SHIFT+‹ ›); holding 1–4 solos while held (DDR-001).

**Why:** Solo is the composition gesture and hold-to-perform already means
momentary; mute is cheap once the bank toggle exists.

### DDR-062 — EQ is per track; the master rack is the same rack

**Rule:** MIXER › EQ: ENC1 selects the track, LOW/MID/HIGH plus HP/LP/MID-F/
MID-Q on SHIFT. Host DSP. MIXER › FX is the §6 rack on the master bus, after the
EQs. MASTER: LEVEL · COMP · DRIVE · BAL.

**Why:** A master EQ is a rack box away; per-track EQ is the thing only the mixer
can do. One rack mechanism everywhere (DDR-038) — tape-character effects are just
boxes on it, not "tape styles".

### DDR-063 — Live input is an engine

**Rule:** `arbutus/live-input` is a synth-role box: SOURCE = mic / line / usb /
resample (master minus the armed track). Loading it puts the input through the
MOD and FX racks, the tape tap and the mixer. The INPUT page stays the sampling
page.

**Why:** Input-to-tape, input-through-effects and out-to-in bounce are three
features on the OP-1 and zero new paths here. Recording resample into a track is
the live mixdown, so no capture mode exists.

### DDR-064 — Firmware applies itself; reset is double-confirmed

**Rule:** A `.fw` in the USB storage root (or pushed by the app) is verified and
applied on next boot into the other A/B slot; a failed boot rolls back and says
so. `.arbox` files install the same way. SYSTEM › RESET asks 1-2-3-4 and then
again. REC+STOP held at power-on enters recovery.

**Why:** Nobody drops a firmware file on a device without wanting it loaded; a
menu item is a step that only exists to be forgotten. Reset is the one thing that
should be hard.

### DDR-065 — Control on TCP 7401, files on HTTP 7402; one token per device

**Rule:** The phone's control connection (the `PROTOCOL.md` NDJSON protocol over
TCP) listens on **7401**, the HTTP file server on **7402**; both numbers go in the
mDNS `_arbutus._tcp` TXT record and the app reads them from there. The device
holds **one** 32-byte pairing token; every paired phone gets the same one, and
FORGET PHONES regenerates it (unpairing all of them). `device-os-spec.md` `OS-1`,
`OS-2` (answered 2026-09-18).

**Why:** The ports only matter to a human typing `curl`, so adjacent and
memorable wins. One lock, one key: a per-phone list costs D2/D3/D4 a list, a
screen and a partial-forget gesture for a device with one owner.

### DDR-066 — Updates swap in as two application copies

**Rule:** The instrument keeps two complete copies of its application folder
(`/opt/arbutus/a` and `b`, `device-os-spec.md` § 3.2, § 10.3). An update is
unpacked into the copy not in use and a pointer is flipped; a copy that fails
to boot cleanly twice is flipped back. The Pi bootloader's two-partition
trial-boot is not used in v1 and is the later mechanism for an OS-level update.
`OS-4`, Elfy 2026-09-19.

**Why:** An update is tens of megabytes and installs in seconds from the phone
with no extra reboot; the partition method makes every update a gigabyte to
fix the rare kernel bug, which v1 handles by a reflash.

### DDR-067 — The image is stock Pi OS Lite plus the install script; no SSH in a release

**Rule:** A device is made by flashing the stock Raspberry Pi OS Lite image and
running the repo's install script (`OS-5` option (a), `arbutus_os/install.sh`).
A pre-built custom image (the Pi Foundation's image builder) comes when the
first P3 needs a flash that is not a sit-down install session; a from-source
minimal Linux is a post-v1 aspiration Elfy finds "even more interesting" for
production. **A release image has no SSH login**; development images do.
Elfy 2026-09-19.

**Why:** (a) exists, is reproducible and needs no toolchain. Note for the
aspiration: a from-source build is still Linux on a kernel — "an OS with
nothing underneath" is not on any list and does not need to be; the measured
headroom (7.6 % of a core for the audio, 0 xruns, 10 µs wake-up) is already
there on the stock kernel.

### DDR-068 — The data partition is exFAT, exported live over USB

**Rule:** `/data`, the library root, is an exFAT partition and USB storage mode
exports that partition as it is (`OS-6` option (a), `device-os-spec.md` § 3.1,
§ 11.1). The mitigations for its missing journal are part of the rule: every
writer is temp-file-then-rename, the autosave (DDR-070) bounds the loss, and
`fsck.exfat` runs each time USB mode ends or the cable is pulled. Elfy
2026-09-19.

**Why:** Every Mac, Windows and Linux machine reads it, there is one copy of a
library that may be gigabytes, and the mode switch already unmounts.

**As built (C1 step 9, 2026-09-25) — the tape medium is the one exception to
temp-then-rename, by design.** "Every writer is temp-file-then-rename" holds for
every *document*: the project, the settings, the patches, and `tape_save` itself
(`arb::io::write_durable`, #93). It cannot hold for a track's medium
(`projects/<date>/<name>/tape-N.wav`) once that file *is* the tape, because a
rename-based writer means copying it — ~184 MB a track, ~737 MB a project, tens of
seconds on a card — on every SAVE, which is exactly what DDR-072's "SAVE never
copies audio" rejects. So the medium is written **in place**, one 1.024 s range at
a time, and its equivalent of the rename is a two-part commit: `pwrite` the range,
`pwrite` a 4 KiB header that claims only through the *previous* range, `fdatasync`.
Because `fdatasync` orders nothing, the lag is what makes the file safe — the
header can never claim audio a commit did not land — and the host repairs the file
from that header on open, never from its length. The loss bound on a cut is about
two seconds of the take instead of one (Elfy, `tape-streaming-design.md` § 7 q6);
`fsck.exfat` after USB mode still applies, and a medium the instrument has closed
is exact. The measured cost on the bench eMMC is a 12.0 ms median commit, 93–95 %
of it inside the `fdatasync` (#158) — and 84–90 ms under a 700 MB phone transfer
with #161's bounded write-behind window in place. The sync cadence itself, which
is what spends this loss bound, is **#159**, Elfy's.

### DDR-069 — Low battery: save and power off at 5 %, and that point reads 0 %

**Rule:** At a measured 5 % state of charge, or a pack voltage of 9.9 V
(3.3 V/cell), or the gauge's alert, the instrument shows `SAVING — LOW BATTERY`,
saves the project and powers off in order (`device-os-spec.md` § 7.2, § 7.4).
**The percentage shown to the player calls that cut-off 0 %**: the displayed
value is the measured charge rescaled over [cut-off, full], so the readout
never shows a number and then switches off; the 15 % low-battery ticker is a
displayed 15 %. `OS-7`, Elfy 2026-09-19; H7 (#4) may still move the electrical
figures, and the display rule follows them.

**Why:** Elfy: "whatever we're choosing to turn the device off at should count
as zero." The 9.9 V figure sits well above the protector's 7.5 V cut so software
always acts first. With D1's voltage-only gauge the measured percentage is a
voltage → charge curve that reads low under load; an early power-off was
accepted over a $12 coulomb counter.

### DDR-070 — Autosave on stop, every 2 min while rolling, and on mode changes

**Rule:** The project autosaves when the transport stops and something changed,
held back 10 s so repeated stops do not thrash the card; **every 2 min while
the transport rolls** (playing or recording); and at every mode change that
already force-saves (DDR-021). Only tracks that changed are written. Because
the tape itself streams to the card as it records (DDR-072, `device-os-spec.md`
§ 3.3), the rolling save is for the **project document** (clip list, slot state,
mixer) and for the **take in progress**, which it writes as a provisional clip so
that audio already on the card is not left unreferenced by a power cut. `OS-8`,
Elfy 2026-09-19 (the 2 min is his number, given tentatively: "it should probably
be like every two minutes if that's the case").

**Why:** The document is kilobytes, so 2 min costs nothing; the loss on a cut
becomes the pool's unflushed seconds of audio plus at most 2 min of edits.

### DDR-071 — The keyboard controller talks on UART5; Bluetooth keeps UART0

**Rule:** On P3 the ATmega keyboard controller is wired to the CM4's UART5 on
GPIO12 (TX) and GPIO13 (RX), enabled with `dtoverlay=uart5`; the Bluetooth
controller keeps UART0 in the Pi's factory configuration. GPIO14/15 become the
spare pair on test pads. `OS-9` option (b), Elfy 2026-09-19: "do the schematic
change if it's already planned" — it was the documented fallback and the two
pins were kept free for it (`io-map.md` § P3).

**Why:** The alternative was a `config.txt` line moving Bluetooth to the mini
UART with a pinned core clock, "usually fine" but a constraint on the radio D4
has yet to test. This leaves the radio untouched at the cost of the I2C5/PWM
escape hatch. (Elfy's stated reason — no reboot on a mode change — belonged to
`OS-10`; neither option here involved one. Recorded so the decision is judged
on its own merits, and it holds on them.)

**Amended 2026-10-14, #935 (the whole-system review of 2026-10-13, I9): the CM4 board's
rule only.** P3 went Zero-class (#569, a Raspberry Pi Zero 2 W on the 40-pin header in place
of the CM4). The Zero's chip, the BCM2710, has no UART5, and v1 has no Bluetooth (#519), so
neither half of this rule's premise holds there. On the Zero board the ATmega is on UART0,
GPIO14 (TX) and GPIO15 (RX), `/dev/ttyAMA0`, with `dtoverlay=disable-bt` giving that UART to
the header; the home of that fact is `device-os-spec.md` § 6.2 and § 6.2.1.

### DDR-073 — Elfy's releases are signed; anyone else's install behind a prompt

**Rule:** A `.fw` update or `.arbox` engine pack may carry an ed25519 signature
over its manifest. Elfy's own releases do, with one key: the public half is baked
into the image, the private half lives on his Mac and never in the repo, and
`mkfw.sh` signs. A file with a valid signature by that key installs with no
prompt. A file with **no** signature, or one by an unknown key, is **not
refused**: it installs after an on-device confirmation that names the risk
(`UNSIGNED UPDATE — INSTALL ANYWAY?`, DDR-064's double-confirm shape). A file
whose signature claims the baked-in key and does not verify is refused — that is
a forgery, not a choice. The companion app's catalogue is the vetted channel,
vetted by Elfy's review procedure; a player who loads a file from anywhere else
has been told, on the device, what that means. `OS-3`, Elfy 2026-09-19
(`device-os-spec.md` § 10.1). **Built the same day by D5 (#26)**; § 10.6 is the
table as built, including the case this rule does not name — a signature on a
device with *no* baked-in key to check it against (a bench CM4, an image built
from the repo) takes the prompt rather than the refusal, or every real release
would be uninstallable on exactly the machines that test them.

**Why:** Elfy wants the instrument open source and the player independent —
"there's a certain level of responsibility I'm willing to put onto users in
order for them to have a more independent experience" — and asked whether a key
was still a good idea. It is, for one reason that has nothing to do with
restricting anyone: without it the device cannot tell his release from anything
else except by taking the phone's word, and a file in the inbox is a file
whoever put it there. The signature is provenance, not permission; third-party
firmware and engines stay a first-class path behind a warning, the way a
phone's "unknown sources" works. Open source and a private key coexist in every
Linux distribution. Cost: one keypair, one line in the build script.

### DDR-072 — The tape is 8 minutes a track, set per project (amended 2026-09-29); 120 track-seconds live in RAM

**Rule:** The tape's ruler is 8 min per track by default and is a **floor, not a
wall**: a take that reaches the end extends the tape rather than stopping — the
file grows, the ruler grows, the outermost zoom follows — bounded only by the
WAV format's 4 GB (~2.9 h per track) and free space, both said on screen. The
pool, the tape audio resident in RAM across all tracks, is **120 track-seconds**
(~46 MB), a flag so play-testing can move it. If a live-growing ruler proves hard
in C1 (#74), the fallback Elfy named is a per-project tape-length setting with
8 min as its default. `OS-11`, Elfy 2026-09-19; the medium (the project's own
per-track WAV, written in place) was decided the same week and is in
`master-plan.md` § Decisions so far.

**Why:** 8 min keeps the most zoomed-out view sane; a long ambient jam should
not be hit by a stop it did not see coming. 120 rather than 240 because "if that
works for play-testing it gives us much better headroom for downgrading the
compute unit" (Track C).

**As built (C1 step 9, 2026-09-25) — two arithmetic notes, neither a change to the
rule.** The format's ceiling is **3.1 h** a track, not ~2.9 h: the bound is the
`data` chunk's 32-bit size (2³² − 1 bytes), and 4 GB read as decimal is where the
lower figure came from. And the **ruler does not grow yet**: each track's range
table is sized from the ruler at `init`, so a medium cannot exceed it (measured,
#165) — a take that reaches the end of the tape ends with `TAPE FULL`
(`tape_full {reason:"format"}`, C1 step 7) rather than extending it. Whether the
ruler should grow, and what that means for a range table already allocated, is
**#168** and is Elfy's; the pool's 120 track-seconds are built exactly as written
here (117 ranges = 46.0 MB, `--tape-pool-seconds`).

**Amended 2026-09-29 (Elfy, #168): the fallback is the rule.** The ruler does not grow.
Each project has its own tape length, 8 min by default, set before recording (a project
setting plus a FILE › SYSTEM row); a take that reaches the end ends with `TAPE FULL`, as
built.

---

## H. Open questions

Flagged rather than resolved. Each needs a decision before the relevant YAML
node is written.

1. ~~**Tape speed encoder persistence**~~ — **resolved 2026-09-16 (DDR-059):**
   both stay and mean different things. The SPEED key is a *clip* effect; TRACK
   ENC3 / TEMPO `LINK` is the *song* repitch.
2. ~~Shift+LIFT scope~~ — **resolved 2026-09-08:** clips under cursor on all
   four tracks — **all eight since DDR-060** (`app/tape.py` `do_lift_all`; audit Q1).
3. **FILTER encoder topology** — bipolar sweep from center detent (LP one way,
   HP the other) or unipolar? Affects the icon: bandpass hump implies motion
   in both directions from a middle, the standard curve implies one.
4. **Green's meaning** — currently green is functioning as "everything that
   isn't shift," i.e. the default, rather than carrying meaning. The loop trio
   (IN/OUT/LOOP) is the natural place to establish green = persistent state,
   the same way SHIFT establishes brown. Deliberate choice needed rather than
   inheriting TE's palette because it looked right.
5. **Navy** — see DDR-007. Held in reserve for encoder-parameter linkage.
   Not for the lookalike demo model.
6. ~~**Parallel groups in the FX rack**~~ — **closed: cut from v1** (Elfy,
   2026-10-02, desk 5; removed from the code by #273). Was: under playtest
   2026-09-16. Elfy leans toward cutting them in favour of "parallelism lives
   inside a box" unless tab navigation and macro-binding across a group prove
   simple. Decide after playing the native rack.
7. **Mute vs solo** — both kept (DDR-061); revisit if SHIFT+1–4 mute within a
   bank turns out to be more confusing than a zeroed fader. **As built (F1/F2,
   2026-09-17):** the *host* decides — solo wins over mute and the gate is on
   the mix only — and the UI reads that back and holds no second opinion, so
   changing the answer is a host change and no UI change at all.
8. **Battery telemetry** — `screen-sketches.md` § FILE › SYSTEM asks for a
   percentage on HELP-hold and a `BATT 12%` ticker under 15 %, and stage J2
   built both; **no pin in `io-map.md` carries a state of charge**, so on the
   device the readout is `NO TELEMETRY` and on the desktop emulator it is the
   laptop's own battery. A fuel gauge on `I2C1`, or an ADC divider off `12V6`,
   is a P3 schematic decision and is not on the work-order. Decide before the
   sheet is drawn (noted 2026-09-18). **Decided 2026-09-19 (work-order D1):
   the ADC divider — an ADS1115 reading `BATT+`, voltage only, at 0x48**; the
   display rule is DDR-069.
9. **MIDI out** — `screen-sketches.md` § FILE › MIDI asks for `OUT CH` and
   `CLOCK OUT`. Stage J2 built neither: the desktop emulator has no output port
   worth the name, and a 24-ppqn clock sent from a UI thread is jitter wearing
   a tempo's clothes. It wants a device-side timer, which is the `arbutus_os/`
   runtime's (#19). The two cells are named, dim and inert until then.

---

## I. Standing principles

Extracted from the decisions above. These are the tests to apply to anything
new.

1. **A conditionally false label is worse than a vague one.** (DDR-011)
2. **If the same gesture behaves differently in different places, either state
   the rule in one sentence or change the behavior.** (DDR-012)
3. **Fixed-parameter effects are gimmicks. Put the parameter under the
   thumb.** (DDR-010)
4. **Naming a thing after its smallest feature makes it unfindable.** (DDR-015)
5. **Icons carry recognition; words carry search. Concepts need both.**
   (DDR-004)
6. **When a control's meaning is supplied by context, the display must state
   the current meaning.** (DDR-022, DDR-024)
7. **Inherited constraints from reference devices must be re-justified, not
   assumed.** The OP-1's design frequently solves a problem Arbutus-1 doesn't
   have. (DDR-018, DDR-010)
8. **Spend a color only when it encodes a real distinction.** (DDR-007)

---

## Session History
- [[logs/2026-09-23-15-12]] — cloud routine (#152): **DDR-054 gains its as-built note** (it had none) — what one step is, and Elfy's call that the `‹ ›` octave is not one of them, with the half that follows from an undo entry being the page's whole snapshot
- [[logs/2026-09-11]] — DDR-036 (sampling waits for the transient) and DDR-037 (library items may carry content) added
- [[logs/2026-09-16]] — session 2: DDR-045…064 (section J: modulation, tape, mixer, system); DDR-012/016/034 amended; H1 closed, H6–7 added
- [[logs/2026-09-18]] — stage J2 as built: § H7 answered by the host (solo wins, gate on the mix only); § H8 (battery telemetry — no pin measures charge, a P3 schematic decision) and § H9 (MIDI out — wants a device-side timer, not a UI thread) added as open questions rather than left as silent gaps. DDR-021, DDR-055 and DDR-064 are all built; their as-built shape is in `control-inventory.md` and `screen-sketches.md`
- [[logs/2026-09-18]] — audit Q1 (#31): as-built corrections and audit notes; findings in `docs/design/audit-2026-09-18.md`
- [[logs/2026-09-18-23-29]] — DDR-065 (OS-1 ports 7401/7402, OS-2 one token per device) — Elfy's answers, unblocking D2 (#23)
- [[logs/2026-09-19-10-37]] — (second exchange) **DDR-073**: Elfy signs his own releases; unsigned or unknown-key files install behind an on-device prompt; a forged signature is refused
- [[logs/2026-09-19-10-37]] — **DDR-066…072** (Elfy's E6 batch-1 answers): two app slots; stock image + install script, no SSH in a release; exFAT; 5 % / 9.9 V shown as 0 %; autosave every 2 min while rolling with a provisional clip; the ATmega on UART5; the tape grows past 8 min and 120 track-seconds live in RAM. § H8 (battery telemetry) is answered by work-order D1 (ADS1115)
- [[logs/2026-09-19-11-39]] — cloud routine (D5, #26): DDR-073 **built** — the rule is unchanged; § 10.6 adds the one case it does not name (a signature on a device with no baked-in key prompts rather than refusing) and the manifest's optional `key` field, without which "unknown signer" and "corrupted release" cannot be told apart
- [[logs/2026-09-24-23-13]] — cloud routine (#74, C1 step 9): **DDR-068** gains an *as built* — the tape medium is the one exception to temp-then-rename, because a rename-based medium is the ~737 MB copy on SAVE that DDR-072 rejects, and the two-part commit with its lagging header is its equivalent; **DDR-072** gains two arithmetic notes that change no rule — the format's ceiling is **3.1 h** a track, and the **ruler does not grow** (a range table is sized from it at `init`, #165), so whether it should is **#168**
- [[logs/2026-10-03-08-55]] — mac routine (**#297**): DDR-020 amended — the first SAVE of a session names the project; SAVE after that is the open project; SHIFT+ENC3 saves a copy
- [[logs/2026-10-15-23-38]] — one-row param bar (SHIFT swaps in slots 4-7), main zone 160 px; DDR-032 amended; demo shots, play-queue P4
