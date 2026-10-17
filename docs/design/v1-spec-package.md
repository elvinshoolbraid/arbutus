---
tags: [spec, index, active, v1]
status: active
related: [[docs/index]], [[hollyhock-demo-spec]], [[engine-spec]], [[modulation-spec]], [[companion-app-spec]], [[design-decisions]]
supersedes: [[v1-product-plan]] (2026-02-23, archived)
---

# The v1 Spec Package — what is authoritative, and for what

**Read this before any design or coding session.** It names the documents that describe
Arbutus-1 v1, what each one is the authority on, what is v1 vs later, and the build
order. Anything not listed here is context, history or hardware bring-up — not product
spec. Created 2026-09-16 after the OP-1 gap grill (`op1-gap-audit.md`).

## The rule

- **One fact, one home.** A fact lives in exactly one document below; every other
  mention is a link. If you find the same fact in two places, one is stale — fix the
  link, not the copy.
- **Spec beats code.** Where the emulator or `audio/` disagree with a document here, the
  document wins and the code gets an issue. Where the spec is silent, the code's choice
  is recorded in `emulator/notes/audio-host.md` § Native audio, `audio/CLAUDE.md` § Defaults or
  `arbutus_os/AS-BUILT.md`, each with a `[TBD-Elfy]` tag, until a spec absorbs it — and
  every such tag is rolled into § `[TBD-Elfy]` below. *(2026-10-14, #935: no longer kept up since 2026-09-25; the roll-up's own dated line says where the open questions go now.)*
- **Dated banners, not deletion.** A superseded doc gets a banner at the top naming
  its successor, and moves to `docs/archive/` when nothing links to it for a reason.
  Git keeps the rest.
- **Provenance.** Decisions carry a DDR number (`design-decisions.md`) and a session log
  date. Undated prose is a draft.

## The package

### The instrument (320×240 half)

| Document | Authority on |
|---|---|
| `control-inventory.md` | every physical control, its gestures, global meanings, SHIFT layers, LIFT/DROP grammar |
| `screen-sketches.md` | per-page layout, encoder bindings, key-strip labels, menus, every subpage |
| `ui-aesthetic.md` | rendering rules; the screenshot grader's rubric |
| `parameter-framework.md` | the 8-slot parameter contract between engines and the UI |
| `tape-sampling-workflow.md` | tape, clip and transport model; sequencer run model; sampling; acceptance traces |
| `design-decisions.md` | the rules and their reasons (DDR-001…); open questions |
| `hollyhock-demo-spec.md` | **the P2 demo's scope only** — frozen 2026-09-11; not the v1 scope |
| `emulator-spec.md` | the MacBook build: window, input, verification seam |

### The software (engine / host / app half)

| Document | Authority on |
|---|---|
| `engine-spec.md` | the engine ABI, boxes, the FX rack, formats, refuse policy, the audio process and its protocol, the SDK; §15 = ABI 2 |
| `modulation-spec.md` | the MOD rack, modulator engines, routes, depth, per-voice, the IMU |
| `companion-app-spec.md` | the phone app, pairing, wireless transports, bounce rules, firmware push |
| `audio/host/PROTOCOL.md` | the control protocol, message by message (public API) |
| `audio/sdk/template/README.md` | how to write a box (the SDK's documentation) |
| `audio/library/README.md` | the library's own documents — patch, kit, rack, pattern and (stage J1) **project** file layouts. The project format is defined there because no document in this package specified one |
| `device-os-spec.md` | **the embedded stack** (2026-09-19, P2 #18): process model on the CM4 (*2026-10-14, #935: on P3's Zero 2 W since #569; the CM4 lines are the bench CM4's measurements and the CM4 board's history, the head of `device-os-spec.md` says which sections are the Zero board's*), boot, storage layout (partitions, `/opt/arbutus` slots, `/data` as the library root, where `settings.json` lives), the systemd services, the six device drivers and their fake tests, the software's power contract (H7 fills the electrical half), MIDI's home (the UI process), networking (Wi-Fi, mDNS, the HTTP file service, the TCP listener's rules), `.fw` A/B + signing, USB storage, logging, recovery (REC+STOP at power-on). Its `[TBD-Elfy]` list `OS-1`…`OS-11` is its § 14 — **all answered (DDR-065…073) except `OS-10`**, the USB-C port's role switch, which is constrained rather than decided and rides the schematic's D15 (#100, drawn 2026-09-29 as a proposed default). Every ticket's as-built is a numbered subsection (§ 1.3.1, § 4.3.1, § 7.4.1, § 9.7–9.11, § 10.6–10.11, § 11.5, § 12.5–12.8) |
| `tape-streaming-design.md` | **the tape I/O thread's contract** (C11 #86, 2026-09-23; C1 #74 built it): the range, the pool, read-ahead, eviction, the `fdatasync` cadence, the live WAV header, `tape_late`/`tape_full`, compaction — § 6 is the nine-step build order and **§ 6.2 every deviation the build made**. `device-os-spec.md` § 3.3 holds the ruler and the pool as storage facts (DDR-072); `audio/CLAUDE.md` § Defaults the rules promoted to code; `audio/README.md` every measured number |
| `software-stack-v1.md` | **history since the Q3 audit (2026-09-25)** — every section carries a dated banner naming what replaced it. Still authoritative in it: § 5's *decisions* (ALSA direct; no JACK, PipeWire or PortAudio in production), § 10.1–10.2 (stay on Pi OS, Buildroot deferred past v1 — DDR-067) and § 13 (the rejected alternatives, as the record of why). § 4/§ 11/§ 12 → `engine-spec.md` and `master-plan.md`; § 7 → `device-os-spec.md`; § 8 → `tape-streaming-design.md`; § 1/§ 3/§ 9/§ 14 → the measured numbers named in each banner |

### Hardware and process

| Document | Authority on |
|---|---|
| `docs/specs/p3-mono-mule-spec.md` | the next board (P3), parts, layout doctrine, IMU |
| `docs/specs/p3-schematic-workorder.md` | **the P3 schematic** — F1–F12 (what the V0.2 sheets got wrong), § 8's D1–D15 (Elfy's decisions, all answered but D15, which #100 drew as a proposed default for him to confirm); the four generated sheets' rule sets take their reference from it (**the spec beats the ticket**, #145) |
| `docs/specs/p3-bringup.md` | the order the first P3 board is brought up in — thirteen stages, § 15 the failures that are silent |
| `docs/specs/io-map.md` | pins |
| `docs/specs/v0.2-spec.md`, `p5-integration-scope.md`, `v0.2-multiboard-research.md` | the 3-board split, parked as P5 input |
| `docs/specs/validation-plan.md` | hardware milestones |
| `prototype-ladder.md` | which build answers which question |
| `future-features.md` | the parking lot (post-v1 ideas) |
| `docs/specs/system-architecture.md` | **V0.1 hardware reference only**, bannered — not v1 |
| `docs/specs/lcd-testing.md`, `p2-svi-demo-spec.md` | V0.1/P2-era bring-up notes; hardware history, not product spec |

### Process — how the work is run (not product spec)

| Document | What it is |
|---|---|
| `master-plan.md` | **the month's map**: destination, tracks, tickets. **Every decision lands in `decisions-log.md`** (moved out of the map's § Decisions so far 2026-09-29) with its DDR number and date. A decision is not made until it is a line there |
| `play-queue.md` | what Elfy has not played yet, ranked — the list that turns a `[TBD-Elfy]` drawing choice into a ratified one |
| `.claude/routines/` | the two unattended routines (next ticket, morning review); the digests are in `docs/logs/daily-reviews/` |

### Implementation notes (as-built; spec wins on intent)

`emulator/CLAUDE.md`, `audio/CLAUDE.md`, `audio/README.md`, `audio/PI.md`,
`arbutus_os/CLAUDE.md` (the bench rules), `arbutus_os/AS-BUILT.md` (the record). They say how the code is, not how the product should be.

### Context, not spec

`op1-gap-audit.md` (why the package has what it has), `ui-vision-dump-2026-09-07.md`
(raw grill transcript), `spec-provenance-handoff-2026-09-12.md` (a process letter),
`docs/logs/` (what happened when).

**Measurements and analyses (context — the numbers are true; the authority on the *rule* is
the spec each names):** `latency-budget-2026-09-19.md` (key-to-sound: the budget, the terms,
the prior art; the ratified target is `device-os-spec.md` § 1.3, the bench numbers
`audio/README.md`), `ui-cost-2026-09-19.md` (C5: where the UI's frame goes on the CM4 and the
levers, since landed as `emulator/notes/as-built.md` § C5b), `compute-options-2026-09.md` (C9: the
compute ladder with prices — **a ranking, not a decision; § 7 holds Elfy's four calls**),
`header-pi-input-2026-10.md` (#330: a Pi 3 / Zero 2 W on P3's header — the keys' wire,
one board for both computers, what a Zero-class v1 changes, power-off and wake, a way in per
model — **desk 24 answered (a), keep the board (Elfy, 2026-10-03)**),
`p3-datasheet-review-2026-09-18.md` (H2: what the P3 parts' datasheets say; its findings were
absorbed into the work-order), `docs/specs/p3-jlcpcb-check-2026-09-18.md` (H5: stock on one
day), `hardware/skidl/README.md` (how a generated sheet is made and signed off).

**The audits (records — a finding lives in the spec it corrected or the issue it filed):**
`audit-2026-09-18.md` (Q1: tape, mixer, MOD, the `render_block` sweep), `audit-2026-09-25.md`
(Q2: files, system, device seams, protocol completeness), `audit-2026-09-25-q3.md` (Q3: the
Files area, the fakes against the drivers, BLE, and this close).

**Letters (process, done):** `windows-bench-host-letter.md`, `mac-remote-control-letter.md`.

**V0.2-era hardware history:** `v0.2-schematic-review.md` (the pre-V2-24 review, complete),
`v0.2-multiboard-research.md` (the 3-board split's reasoning — P5 input, parked).

**The package's history starts at `docs/logs/2026-09-08.md`.** Every UI, emulator and
system-spec decision originates in a log from 2026-09-08 onward; those logs are the
provenance chain and are cross-linked from each doc's `## Session History`. Logs before
2026-09-08 are hardware bring-up and the V0.2 era — useful for boards, not for product spec.

### Archived 2026-09-16 (`docs/archive/design-2026-09/`)

`v1-product-plan.md` (Feb 2026 scope — replaced by this file), `engine-plugin-api.md`
(replaced by `engine-spec.md`), `spec-completion-guide.md` + `interaction-matrix.xlsx`
(replaced by the 2026-09-08 spec set), `control-research.md` (OP-1 key reference —
replaced by `op1-gap-audit.md`). `docs/specs/system-architecture.md` stays in place as
V0.1 hardware reference with a banner.

## Scope — v1 / v1.5 / not planned

| Area | v1 | v1.5 | Not planned |
|---|---|---|---|
| Synth boxes | `osc`, `fm`, `noise`, `sampler`, `live-input`, `va`, `string`, `wavetable` | vocoder, kick designer, additive | |
| Drum boxes | `drum` (per-pad, choke), `dbox` | | |
| FX boxes | `biquad`, `comb`, `satcomb`, `reverb`, `duck`, `delay`, `chorus`, `crusher`, `drive`, `eq3`, `transient` | tape-character boxes, OTT | |
| Modulators | `lfo`, `follower`, `random`, `envelope`, `motion`, `stepmod`, `keys` | `midi` | mod→mod routing |
| Sequencers | `endless` (+tied, GATE, PATTERN), `arp`, `pattern`, `euclid` | tombola-like | finger, sketch, crank |
| Racks | FX rack per instrument, MOD rack per instrument, master FX rack | — | rack in rack |
| Tape | 8 tracks in two banks, overdub-only, tick ladder, LINK, STOP+‹ › jumps, tape→instrument drop | | tape styles, tape bus, region lift, redo |
| Mixer | 8-track MIX, per-track EQ, master rack, MASTER (level/comp/drive/bal), mute/solo, signal path | | 5th channel |
| Tempo | tap, click, LINK, COUNT-IN, MIDI clock in/out, nudge | PO sync pulse | |
| Input | INPUT page sampling (DDR-036), `live-input` engine (mic/line/usb/resample) | | radio, tuner (#54) |
| Files | projects (NEW/LOAD/~~SAVE~~/BOUNCE; *2026-10-14, #935: SAVE is gone, the instrument is always saved (D97), and RENAME took its place, `NEW ▸ · LOAD ▸ · RENAME ▸ · BOUNCE ▸` (D98)*), MIDI, USB (storage/midi/pair), SYSTEM | USB audio | |
| Connectivity | USB MIDI notes/bend/CC/clock, Wi-Fi, pairing over the instrument's own Wi-Fi (no Bluetooth in v1: desk 30, #519), HTTP files, mDNS | controller mode, MIDI monitor | BLE MIDI |
| App | iOS: rename, bounce, storage, ~~backup~~ *Offload / Restore (2026-10-14, #935: D99 replaced Backup / Share as a file / Restore; a project is stored on the instrument or offloaded to the phone, never both, `companion-app-spec.md` § 11)*, send-to-device, firmware, status, catalogue stub | accounts, uploads, Android | editing sounds |
| Hardware | P3 + LSM6DSO IMU | | key velocity sensing |

## Build order (software)

Everything below is native: `audio/` makes the sound and `emulator/` is the UI on top of
it. **Since stage N1 (2026-09-17) that is the only arrangement there is** — the Python
audio path is deleted, so "the demo" and "the v1 build" are one program and there is one
UI, not two. The Hollyhock demo's *scope* is still frozen (`hollyhock-demo-spec.md`): what
that freeze protects is the 60-second script, not a second copy of the code.

**Steps 1–8 are all done (2026-09-18).** The instrument described by this package is
built: the last stage (J2 — FILE › MIDI / USB / SYSTEM, the browser as a file manager, the
battery) merged that day, and the staged build handoff that carried the work deleted itself
as it had always said it would. What remains below is **9 and 10**, which both wait for a
Pi on the desk. Per-stage as-built detail and every deviation live in `emulator/CLAUDE.md`
and `audio/CLAUDE.md`; the three formats this package never specified — a project, a
settings document and the stock manifest — are defined in `audio/library/README.md`, and
each of those is recorded as a deviation.

1. ABI 2 + SDK docs (`engine-spec.md` §15) — rebuild the eleven boxes, goldens unchanged.
2. MOD rack: host routes + `pages/rack.py` reuse + the six modulator boxes.
3. Drum per-pad + ENV AD + `transient`; sampler loop/rev/fade + `waveform`.
4. 8 tracks, overdub rule, tick ladder, STOP+‹ ›, LINK, tape→instrument drop.
5. Mixer: 8-track MIX, per-track EQ, master rack, MASTER page, mute/solo, signal path.
6. `live-input`, TEMPO page params, FILE › 4 subpages, SYSTEM, bend gesture, undo on SYNTH/DRUM.
   **Done.** FILE's four subpages are stages J1 (PROJECT) and J2 (MIDI / USB / SYSTEM);
   J2 also built the browser file ops (DDR-055) and the battery on HELP-hold. Three things
   this package asks for are **not built and say so on screen**, each for a reason outside
   the software: MIDI out (no port, and a UI thread cannot hold a clock), USB storage /
   `.fw` auto-apply / ~~BLE pairing~~ pairing / Wi-Fi (step 9, the device OS), and a battery percentage
   on hardware (~~no pin in `io-map.md` measures one~~).
   *2026-10-14, #935 (review I7, I4): pairing runs over the instrument's own Wi-Fi, not
   Bluetooth, since #519 (v1 has no Bluetooth, desk 30; the scope table's Connectivity row);
   and P3 carries a battery gauge, the ADS1115 on I2C1 (`io-map.md` § Battery telemetry on P3,
   `device-os-spec.md` § 6.6), so the percentage waits on P3's first board, not on a pin.*
7. Sequencers `arp` / `pattern` / `euclid`; endless additions; patterns as assets.
8. Remaining boxes — **done**: synths `va`, `string`, `wavetable`, `dbox` (stage I1,
   `engine-spec.md` §15.15) and effects `delay`, `chorus`, `crusher`, `drive`, `eq3`
   (stage I2, §15.16). **Thirty-one stock boxes.**
9. Device OS: Wi-Fi/~~BLE~~ pairing (*2026-10-14, #935: over the instrument's own Wi-Fi since #519; no Bluetooth in v1*), HTTP files, mDNS, A/B firmware; then the app.
   **Spec'd 2026-09-19** (`device-os-spec.md`, P2 #18) and **built — Track D finished
   2026-09-25** (`arbutus_os/`, seven units, every `OS-n` answered but `OS-10`); **no phone,
   radio or panel has met any of it** (E7). The app (Track A) waits on Xcode (E5); its device
   to build against is `python3 -m arbutus_os.mock` (A3, #28).
10. Pi: **done** — the CM4 on the IO board runs the whole gate (`tools/gate.sh --pi`) and every
    bench-runnable row of Track P; what is left of P waits on a P3 board or a real radio.
    *2026-10-14, #935 (G8): P3 is Zero-class (#569), so the board the product is measured on is
    a Zero 2 W: until ~10-19 the bench is a borrowed Pi 3B+ at Zero 2 W clocks
    (`arbutus_os/DEV-BOARD-3BPLUS.md`, no builds on it), then the CM4 held to the Zero-class load
    budget (`device-os-spec.md` § 1.5), then the Zero itself (one ships 11-28).*

Hardware runs in parallel: P3 schematic with the IMU (`p3-mono-mule-spec.md`).

## Unresolved (consolidated pointers)

- `design-decisions.md` § H — open interface questions.
- `engine-spec.md` §13/§15, `modulation-spec.md` §14, `companion-app-spec.md` §8,
  `device-os-spec.md` § 14 (`OS-1`…`OS-11`, the D1 batch) — per-doc lists.
- `[TBD-Elfy]` drawing decisions are indexed in `emulator/notes/audio-host.md` § Native audio.
- **§ `[TBD-Elfy]` — every open question in one list**, below: the roll-up the Q3 audit made
  of every tag in the tree, marked answered or open. Read that before any per-doc list.
- § Inspection 2026-09-18 below — what the whole-system inspection found spec'd and not
  built, or spec and code disagreeing, with no deviation recorded.
- § Zero-class through the package below (2026-10-14, #935) — what P3's move from the CM4 to
  a Zero 2 W changes in each document, what was amended and what is still open.

## Zero-class through the package (2026-10-14, #935)

The whole-system review of 2026-10-13 (`docs/reviews/whole-system-review-2026-10-13.md` § 5,
G8) asked for one pass through every document here: what does P3 being **Zero-class** change
(since #569, Elfy's yes of 2026-10-07: a Raspberry Pi Zero 2 W on the 40-pin header, 512 MB,
four 1 GHz cores, no CM4 socket, the WM8904 codec). One pass, bounded: each place is recorded,
the clear ones amended with a dated line, the rest left open below. The four facts the review
named, and their homes:

- **The keys' UART:** UART0, `/dev/ttyAMA0`, given to the header by `dtoverlay=disable-bt`
  (the BCM2710 has no UART5) — `device-os-spec.md` § 6.2, § 6.2.1; DDR-071 is the CM4 board's
  (amended). It was already written in the spec, not only in `arbutus_os/DEV-BOARD-3BPLUS.md`.
- **The 64-frame block:** the device's audio host runs 64 frames (1.33 ms), the emulator 128 —
  `device-os-spec.md` § 1.3.2 (amended where other documents still said 128 on the device).
- **The load budget is a spec fact**, not a bench artefact: `device-os-spec.md` § 1.5 states
  the rule (the instrument's load may not grow past what it was measured to run on a
  Zero-class board) and names `arbutus_os/load-budget.json` and `tools/load-budget.sh` as its
  number and its check.
- **The spare CM4 Lite** is bench logistics, not product spec: it goes back by ~12-01 (#702),
  and the bench order (3B+ at Zero clocks, then the CM4 held to the budget, then the Zero) is
  the root `CLAUDE.md` status's and `arbutus_os/DEV-BOARD-3BPLUS.md`'s. No package document
  needs it beyond the compute-options row (amended).

| Document | What Zero-class changes there | This pass |
|---|---|---|
| `control-inventory.md` | nothing of the CM4; the gauge line (I4) | amended (I1–I4) |
| `screen-sketches.md` | FILE › USB's `PAIR` said BLE | amended; FILE › USB's "force-saves first" against D97 is not Zero-class and is left |
| `ui-aesthetic.md`, `parameter-framework.md`, `tape-sampling-workflow.md`, `hollyhock-demo-spec.md` | nothing found (the demo spec's one "CM4 memory budget" is in its frozen out-of-scope list) | — |
| `design-decisions.md` | DDR-071's UART5 | amended |
| `emulator-spec.md` | a struck line, and § 8's out-of-scope "CM4 memory limits" (the emulator does not model the device's memory; the load budget does, on the board) | none needed |
| `engine-spec.md` | builds (already amended by #568: cross-built for a 512 MB Pi); the eager-tape memory argument (§ 15.12, 1.5 GB "which the CM4 has not") holds more strongly on 512 MB | none needed |
| `modulation-spec.md` | § 7's block (128 "on the device"); § 13's IMU bus | amended |
| `companion-app-spec.md` | the radio "on the CM4104016"; the BLE pairing sections (history since #519); "the CM4 has no real-time clock" (nor has the Zero, so it stands) | the radio row amended |
| `audio/host/PROTOCOL.md` | `--keys`'s P3 port; the timings "on the CM4" are bench numbers | the port amended |
| `audio/sdk/template/README.md`, `audio/library/README.md` | nothing found | — |
| `device-os-spec.md` | written for the CM4 (about 190 mentions), mostly bench measurements and the CM4 board's history | a dated head note naming the Zero board's sections; § 6.2's heading |
| `tape-streaming-design.md` | the block; the `fdatasync` and card numbers were measured on the bench CM4's eMMC | block amended; card numbers open (below) |
| `software-stack-v1.md` | history by its own banners | none |
| `p3-mono-mule-spec.md` | the wireless CM4, its antenna, the IMU's bus, the WM8962B | a dated banner |
| `io-map.md` | already carries the Zero board (§ Compute sheet (P3), § WM8904 on the Zero board, the derivation table's rows) | none; one heading open (below) |
| `p3-schematic-workorder.md`, `p3-bringup.md` | the Zero board's own record (#569, #744) | not re-read in this pass |
| `validation-plan.md` | V0.1 milestones only; P3's are `p3-bringup.md`'s stages | none |
| `prototype-ladder.md` | P3's row said wireless CM4 | amended |
| this file | the device-os row, the build order's steps 6, 9, 10, the D16 and compute rows, OS-9 | amended |

**Open after this pass (fog, for a ticket each, not decided here):**
- `tape-streaming-design.md` and `device-os-spec.md` § 3.4: the tape I/O's `fdatasync` and card
  numbers were measured on the bench CM4's eMMC; the Zero has only a microSD card. The load
  budget's card-write line covers the rate, not the stall a range waits behind.
- `device-os-spec.md` § 1.3's key-to-sound numbers are the bench CM4's except § 1.3.4's
  (1.30–1.48 ms jitter on the 3B+ at Zero clocks); which of § 1.3's targets were re-checked at
  Zero-class is not written.
- `device-os-spec.md` § 9.5's BLE as-built sections and `companion-app-spec.md`'s BLE flow are
  history since #519 but carry no banner saying so in each section; the BLE code's removal is
  #519's last step.
- `io-map.md` § P3's "CM4 GPIO — derivation" heading names the CM4 while its rows are the
  Zero's header pins, and its I2C1 rows say the bus is "pulled up only by the CM4's own
  1.8 kΩ" (the Zero has the same pull-ups on GPIO2/3, unverified here).
- `device-os-spec.md` § 11's USB role switch: the CM4's OTG pin against the Zero's `USB_ID` on
  GPIO13 and the `arbutus-usbc` overlay (§ 2.2) — not re-read in this pass.

## `[TBD-Elfy]` — every open question, in one list (Q3, 2026-09-25)

*2026-10-14, #935 (the whole-system review of 2026-10-13): **this roll-up is a snapshot of
2026-09-25 and is no longer the whole list.** It is kept here, not moved, because the morning
review still records answers in its rows (`.claude/routines/nightly-review.md` § 5). Since it
was made, questions for Elvin have gone to his desk (`docs/strategy/desk.md`, the live list)
and the play queue (`play-queue.md`, about forty tags), and new tags have been added in specs
without a row here; a full re-roll of the tree is a ticket of its own. This pass amended the
rows the review named (OS-9, D16, the compute options, the 120 s tape) with dated lines.*

The tag `[TBD-Elfy]` marks a choice the code or a spec made where Elfy had not decided —
"do this unless you say otherwise". On 2026-09-25 the Q3 audit found the tag in forty files
and rolled every one into this table, so a reader who has this page has the whole list.
**Rolling them up is not deciding them**: a row marked *answered* names the decision that
answered it (a DDR is a numbered entry in `design-decisions.md`; an `OS-n` or `Dn` is a
question in `device-os-spec.md` § 14 or the work-order's § 8); a row marked **open** is
still Elfy's. The morning digest (`docs/logs/daily-reviews/`) gathers the open ones daily. Two blocks are
pointed at rather than copied, because they are already tables in the form this one
takes: the **82 drawing and gesture choices** in `emulator/CLAUDE.md` (its `[TBD-Elfy]`
tables, entries 1–82 — each row is the spec's words, what was drawn, and the file) and the
**defaults** in `audio/CLAUDE.md` § Defaults (each row the spec's tag, the value chosen,
the file). Every one of those is *open* in the sense that nothing has been ratified by
ear or by eye, and none blocks anything; E1 (#9, the aesthetic pass) and `play-queue.md`
are where they get ratified.

### The device OS (`device-os-spec.md` § 14)

| Tag | Where | The question, in one sentence | Status |
|---|---|---|---|
| `OS-1`, `OS-2` | § 14 | Which two ports the phone talks to, and whether there is one pairing token per device or one per phone | **answered** 2026-09-18 → DDR-065 (7401/7402; one token per device) |
| `OS-3` | § 14, § 10.1 | How a firmware file proves it is Elfy's, and what happens to one that cannot | **answered** 2026-09-19 → DDR-073 (ed25519, the public key in the image; an unsigned file installs after an on-device prompt). The private key exists on Elfy's Mac; `arbutus_os/fw.pub` is the public half |
| `OS-4` | § 14, § 10.3 | Two app folders with a pointer flipped between them, or the Pi bootloader's own A/B | **answered** 2026-09-19 → DDR-066 (two app slots). § 10.3's heading carried the tag until Q3 |
| `OS-5` | § 14, § 5 | Whether the device image is stock Pi OS plus an install script, or a built image | **answered** 2026-09-19 → DDR-067 (stock + `install.sh` now; a built image when a P3 needs a flash; no SSH in a release image) |
| `OS-6` | § 14, § 3.1 | Which filesystem the data partition uses, so a Mac can read it over USB | **answered** 2026-09-19 → DDR-068 (exFAT). Not yet laid out on any board — the bench CM4's `/data` is a folder on the OS partition (C1 step 4 is the unattended-run blocker) |
| `OS-7` | § 14, § 7.2 | At what battery level the instrument saves and turns itself off, and what the gauge shows there | **answered** 2026-09-19 → DDR-069 (5 % / 9.9 V; the gauge calls that point 0 %) |
| `OS-8` | § 14, § 7.3 | How often the working state is saved between explicit saves | **answered** 2026-09-19 → DDR-070 (on stop, plus a rolling save every 2 min — Elfy's number, tentative) |
| `OS-9` | § 14, § 6.2 | Which serial port the key controller uses on a module whose Bluetooth also wants one | **answered** 2026-09-19 → DDR-071 (the ATmega moves to UART5; Bluetooth keeps UART0). *2026-10-14, #935 (review I9): both halves of the premise are gone on the Zero board — the Zero 2 W's chip (the BCM2710) has no UART5, and v1 has no Bluetooth (#519). The keys' port there is the Zero's one full UART (a PL011), UART0 on GPIO14/15, `/dev/ttyAMA0`, given to the header by `dtoverlay=disable-bt`; the home of that fact is `device-os-spec.md` § 6.2 (its "Since #569 / #669" bullet) and § 6.2.1's Zero note, and `install.sh --board p3` writes it as `board.conf`'s keys port. DDR-071 stands for the CM4 board only (its own dated line)* |
| `OS-10` | § 14, § 11.3 | How the USB-C port switches between being a device (storage) and a host (a MIDI keyboard) without a reboot | **answered with D15** 2026-09-30 (Elfy: dual-role as drawn); what is left is the software half (#192) and a board to prove it on |
| `OS-11` | § 14, § 3.3 | How long the tape is and how much of it lives in RAM once it is on storage | **answered** 2026-09-19 → DDR-072 (8 min per track; a 120 track-second RAM window). One consequence is **#168**, open, below |

### The P3 schematic (`docs/specs/p3-schematic-workorder.md` § 8, D1–D16) and the hardware tickets

*2026-10-14, #935 (G8): P3 went Zero-class on 2026-10-07 (#569). Rows below that name the CM4 (D15's USB-OTG pin, #156, #195, #204's Lite CM4, the compute-options row) answered questions about the CM4 board and read as its history; the Zero board's own record is `docs/design/p3-zero-class-plan.md` and `io-map.md` § Compute sheet (P3).*

| Tag | Where | The question, in one sentence | Status |
|---|---|---|---|
| D1–D14 | work-order § 8 | The fourteen part and topology calls the V0.2 sheets left open — the battery gauge part, the 3S protector, the microphone count, the line-in jack, the charger's pins, the pull-ups, the panel's tearing pin, the switch part, the encoder pushes, `VBUS_DET`… | **answered** 2026-09-19 (E6 batch 1; each row's Decision column is the record, `master-plan.md` § Decisions so far the date). The work-order's remaining `[TBD-Elfy]` lines at § 2.2.2, § 2.2.7, § 3.1.2, § 3.2.3, § 3.2.6 and § 3.4.4 are the *questions* those decisions answered and read as history |
| **D15** | work-order § 8, § 2.2.13, § 3.2.7; **#100** | The USB-C port was drawn as a sink — it could not power or host a MIDI keyboard. #100 drew a proposed default (2026-09-29): a **TI TUSB320LI** dual-role controller (C2836598, on I2C at 0x47, powers up a sink), a **TI TPS2051C** 0.5 A switch putting 5 V on the port only while the controller says a device is attached (C129581), a transistor holding the charger off meanwhile, and the controller's "I am the host" wire on GPIO15 and the CM4's own USB-OTG pin. Confirm the parts, or name others | **answered** 2026-09-30 — Elfy: yes, dual-role as drawn ("definitely worth it"); the software half is #192 |
| **D16** | work-order § 8, § 2 *As built*; `p3-jlcpcb-check` J15; **#153**, **#193** | The audio codec, the WM8960, is out of production (Cirrus's last-time buy was 2022-07-26) and no supplier JLCPCB uses has any: buy WM8960s elsewhere and have JLCPCB place them as consigned parts, or change the codec — which redraws the Audio-IO sheet and reaches the codec driver and the mixer routing | **answered** 2026-09-30 — Elfy: investigate the deprecation status and consign if it is temporary; **if the successor (the WM8962B) is similar in availability and specs, run with it**. #193 (`afk`) does both halves; only a successor that does *not* compare comes back to him. **Done 2026-09-30 (#193): the end of life is permanent and the WM8962B compares — P3 carries it** (work-order § 2.4); the driver half is #210. *2026-10-14, #935 (review I8): superseded — P3 went Zero-class (#569, the Pi Zero 2 W on its header in place of the CM4) and its codec is the Cirrus **WM8904** (Elfy's M6 choice, desk 42 (a); `device-os-spec.md` § 6.3.4, `io-map.md` § WM8904 on the Zero board); the WM8962B is the CM4 board's history* |
| D8 | work-order § 8; `p3-jlcpcb-check` line 262; **#65** | The panel has no tearing-sync pin: accept a visible tear on page changes on P3 (the default), or swap the panel | **answered** — swap (2026-09-19), NHD-2.8-240320AF-CSXP-F chosen (#65), and on 2026-09-29 both panels on P3, one at a time; **drawn by #188** (the 40-pin `P2`, TE on its pin 40, backlight current on `JP_BL`) |
| — | work-order § 4.5.6 (**#188**) | Which pad of the 40-pin connector the panel's FPC contact 1 lands on — it turns on which side of the AFC07-S40ECA-00's body the cable mouth is, and that drawing is not in the repo; if it is the pad side, the drawn map is mirrored | **open — H6's check, not Elfy's** (review's call 2026-09-30): settled from the connector's drawing before the netlist freezes (#36) |
| J1 / D10 | `p3-jlcpcb-check` § J1, § D10; **#10, #63** | The Kailh PG1316S key switches are at zero stock at the assembler: consign them (Kailh ships them, JLCPCB places them), hand-place, or accept a two-variant BOM | **answered** 2026-09-30 — Elfy: the PG1316S, CPG1316S01D02 (C42372112; Kailh's "60 ± 10 gf" is its peak force), 150 on reel, consigned by Kailh (~1 month); P3's 52 placed switches are the test of the "not sitting flat" report (#189) |
| — | `arbutus_os/AS-BUILT.md` § Battery gauge; `drivers/power.py`; `p3-bringup.md` § 6.6.1 item 5 | The gauge's `ALERT` wire on GPIO6 will never fire because nothing programs the chip's comparator: write the thresholds at start-up, or drop the wire at the next board revision | **decided** — the wire stays and the thresholds are written at start-up once a board exists (review's call 2026-09-30); the polled voltage enforces the cut-off meanwhile |
| — | **#154** | The battery protector's two ground pins are drawn to the pack's negative terminal against its datasheet | **answered 2026-09-30 by the datasheet — nothing for Elfy**: the protector's ground is the cell stack's negative terminal (`BATT-`), and the sheet is redrawn that way (`docs/design/bq77905-reference-node-2026-09-30.md`) |
| — | **#156** | The Compute sheet's blank BOM lines: the microSD socket, the 2×20 header, and whether the 18-pin panel connector is top-contact | **answered 2026-09-29 — nothing for Elfy**: the socket is Hirose DM3AT-SF-PEJM5 (C114218), the part its footprint was drawn for; the header stays DNP with no part number (run's call); the 18-pin connector is **bottom**-contact, kept, and whether its panel's tail reaches it is H10's (its top-contact twin is C262640). **H10 found it does not (L-10); #252 (2026-10-02) swapped in C262640, its pads reversed against the panel's contacts.** Found on the way: the activity LED `D59` and the CM4's two board-side DF40 receptacles had no line — **#195**, below |
| — | **#195** | The Compute sheet's last two blank BOM lines: the two 100-pin sockets the CM4 plugs into (their height is the gap under the module), and the CM4's activity LED, which V0.2's green-through-1 kΩ left too dim to see | **answered 2026-09-30 — nothing for Elfy**: two Hirose DF40C-100DS-0.4V(51) sockets (C597931, 1.5 mm stack — the CM4 IO board's; run's call, since nothing on P3 sits under the module and the 3.0 mm part fits the same pads) on the CM4's own line, whose footprint was checked against Hirose's drawing; the LED is a red NCD0805R1 (C84256) through 220 Ω (C25091), ≥ 1.4 mA at worst (run's call). One thing for H6 (#36): the assembler's placement file needs two rows for the one CM4 footprint (`p3-jlcpcb-check-2026-09-18.md` § #195) |
| — | **#155** | The Power sheet's three blank BOM lines: the 3.3 MΩ and 470 kΩ resistors beside the pack protector and the buck's 6.8 µH inductor | **answered 2026-09-30 — nothing for Elfy**: C26096, C23178 and Sunltech SLO0630H6R8MTT (C207841), which replaces V0.2's 5.4 × 5.2 mm inductor because that one saturates below the regulator's own current limit (`p3-jlcpcb-check-2026-09-18.md` § #155, J17) |
| — | work-order § 0.1 *As built*; `hardware/skidl/assemble_p3.py` (**#186**) | Nine part labels (reference designators; ten until #100 removed `R7` from Audio-IO) are on both the Audio-IO and the Power sheet, because the V0.2 sheets they came from were separate projects: keep the default, which renames the Audio-IO copies `C201`, `C205`–`C208`, `C212`, `C216`, `C217`, `U204` in the assembled project only, or renumber one sheet in the work-order | **decided** — the default stands: the project-only rename (review's call 2026-09-30; #187's per-sheet prefix is the lasting fix, and H6 #36 freezes it) |
| — | **#204** (H4-11) | The microSD socket's power is not switched, so a Lite CM4 (no eMMC) booting from a UHS card may not come back after a warm reboot: must P3 boot a Lite CM4? | **decided** — draw the switch (AO3401A + 2N7002 on `SD_PWR_ON`, parts already on the BOM), which keeps both modules possible (review's call 2026-09-30); **drawn 2026-09-30** (`Q_SD`, `Q_SDEN`, `io-map.md` § Compute sheet (P3)); which CM4 is ordered stays Elfy's |
| — | **#137** | The drawn V0.2 sheets' symbol library exists only on the Windows box | **open** — a copy into the repo, or accept the sheets as history now that P3's are generated |
| — | **#64** | KiCad 8's netlist disagreeing with 9/10 | **closed** — moot: KiCad 10 is the authority and the P3 sheets are generated (review's call 2026-09-30) |
| — | **#101** | The microphone, Knowles SPH1642HT5H-1 (C111990), was at zero stock at the assembler: out of stock, or gone? | **answered 2026-09-30 — nothing for Elfy**: gone (DigiKey lists it obsolete; only brokers hold it). The mic is now Gettop's top-port NA-FFA381-A10-1 (C50275774): −38 dBV/Pa and 64 dBA against the old part's −38 and 65, 1.6–3.6 V, 3,267 at JLCPCB, $0.53 — every criterion the ticket set, met on its datasheet (`p3-jlcpcb-check` § #101, J25–J27; work-order § 2.2.6) |
| — | `compute-options-2026-09.md` § 7 | Four calls on the compute module: **which reseller**, **order the CM4102000 now** (it was out of stock on 2026-09-23 — retry or backorder), **is 30 Hz still the floor for a Zero-class module**, and **the decision line** to write in the map | **answered** 2026-09-30 — Elfy backorders a CM4 Lite this week, tests on a Pi 3 and a Zero 2 W meanwhile, and (desk 3) P3 gets three solder jumpers so a header Pi has keys and a screen — reverses D14's 2026-09-23 *compute-only*. *2026-10-14, #935 (G8): overtaken by #569, P3 is Zero-class (a Zero 2 W on the header, no CM4 socket); the load budget is the Zero-class rule (`device-os-spec.md` § 1.5); the CM4 Lite bought is a bench spare that goes back by ~12-01 (#702), not a product part* |
| — | **#132** (H10) | The measured positions (answered 2026-09-29, `hardware/p3-positions.toml`); since 2026-10-01 **the placement plan `docs/specs/p3-layout-plan.md`** — L-1…L-58, V-, E- lines to tick and Q-1…Q-9 | **open**, Elfy's — ticking the plan blocks the layout |
| — | E7 (#40) | A USB Wi-Fi/Bluetooth dongle for the bench CM4 | **landed** 2026-09-29 (Realtek `0bda:c820` in the bench CM4); #180 and #150 step 2 done that day; P13 (#107) and A4 (#30) are attended `radio` sessions |

### The engine, protocol and host (`engine-spec.md`, `modulation-spec.md`, `audio/host/PROTOCOL.md`, `audio/CLAUDE.md`)

| Tag | Where | The question, in one sentence | Status |
|---|---|---|---|
| `ARB_MAX_BLOCK` | `engine-spec.md` § 3.1 line 106; `audio/sdk/include/arbutus_engine.h` | The largest audio block a box must handle | **open, harmless** — 2048; the host runs 128 (64 on the device) |
| `name[8]` | `engine-spec.md` § 3.5; `arbutus_engine.h` | How long a parameter's on-screen label may be | **open** — 8 characters as built; every stock box fits |
| `ppqn` | `engine-spec.md` § 3.3; `audio_host.cpp` | Clock ticks per quarter note handed to a sequencer box | **open, harmless** — 24, MIDI's own number |
| soft-key label length | `engine-spec.md` § 3.6; `arbutus/endless` README | How long a sequencer's four soft-key labels may be | **open** — the descriptor's labels go in the strip unclipped (`emulator/CLAUDE.md` entry 12) |
| `.arbox` | `engine-spec.md` § 3.9; `device-os-spec.md` § 10.1 | The file extension a packaged box travels as | **open** — `.arbox` as built everywhere |
| mixer-page FX | `engine-spec.md` § 6, line 349 | Whether the master bus's rack has its own page or reuses the instrument's | **open** — reuses `pages/rack.py` unchanged (`emulator/notes/as-built.md` § F2) |
| rack drawing, copy highlight, range-popup gesture, tab indicator | `engine-spec.md` § 6.1–6.4 | How the effects rack is drawn and how a knob is bound to a macro | **open** — entries 1–7 in `emulator/CLAUDE.md`; play-queue item 12 |
| parallel groups on-device | `engine-spec.md` § 6.5; `design-decisions.md` H6 | Whether a player can make a parallel effects group from the device at all | **closed: cut from v1** (Elfy, 2026-10-02, desk 5) — a box does parallel paths inside itself |
| sidechain slot | `engine-spec.md` § 6.6 line 414 | Which knob a sidechained effect's input takes when the box has no spare slot | **open** — as built the box's declared unused slot; no stock box needs the other case |
| xrun window `N in M s` | `engine-spec.md` § 6.8; `PROTOCOL.md`; `audio_host.cpp` | How many dropouts in how many seconds raise the `CPU NO HAPPY` flag | **open** — **3 in 2 s** as built, and the tape's `HARD DRIVE SLOW` uses the same rule (C1 step 7) |
| the refusal and flag copy | `engine-spec.md` § 6.8, § 7.4 | The exact words on screen for a refused box and for the CPU flag | **open** — the spec's own words, verbatim (`emulator/CLAUDE.md` entries 4, 8) |
| `arbutus/fm`'s fourth slot | `audio/engines/arbutus/fm/README.md` | Whether FM wants a fourth operator slot | **open**, a box author's note |
| `arbutus/envelope`'s look | `modulation-spec.md` § 3.2; `arbutus/envelope` README | The modulator envelope drawn smaller and unlike the amp ENV page | **open** — drawn per `emulator/CLAUDE.md` entries 13–14 |
| the ghost glyph, output-copy drawing, route range popup, route move | `modulation-spec.md` § 2.2, § 6.3, § 9, § 10 | How the MOD rack shows a modulated value, and the routing gestures | **open** — entries 17–20 in `emulator/CLAUDE.md`; play-queue item 12 |
| MASTER COMP's times halve on the device | **#148** (and **#67**) | The master compressor's attack/release are applied once per audio block, so the device's 64-frame block halves them against the emulator's: fix the sound, or accept the divergence | **decided** — accepted as it sounds, the review's reading of Elfy's two closes (review's call 2026-09-30); the listen stays on the checklist (P3) and one word makes the labels true instead |
| the LEVEL tick | **#70** | The tape playhead's level tick: the spec carries two sentences and the build follows one | **decided** — the armed input-level foot, as built; the always-on sentence leaves both specs (review's call 2026-09-30, #70 now `afk`) |
| the SEQ step number | **#91** | The SEQ page's big step number "increments on playback", but the host reports no step | **decided** — the host reports it (`meters.seq_step`), as the sketch says (review's call 2026-09-30, #91 `afk`) |
| the tuner | **#54** | `LIVE INPUT`'s tuner readout is not built (the host hands no pitch up) | **closed: cut from v1** (Elfy, 2026-10-02, desk 6) |

### The screen and the tape (`screen-sketches.md`, `control-inventory.md`, `tape-sampling-workflow.md`)

| Tag | Where | The question, in one sentence | Status |
|---|---|---|---|
| the drum AD curve | `screen-sketches.md` § DRUM line 86 | How the per-pad attack/decay is drawn "visibly unlike the synth ADSR" | **open** — entry 14; `pages/drum.py` |
| the bank glyph | `screen-sketches.md` § TRACK line 90 | What marks the second bank of four tape tracks once it holds a clip | **open** — entry 21 (one letter in the ruler gutter) |
| the signal-path arrows | `screen-sketches.md` § MIXER lines 144, 166 | How the sidechain and route arrows are drawn on the signal-path screen | **open** — entry 36 (three rows of names, sources as a line, no arrows at 5×7) |
| ENC3's readout under LINK | `tape-sampling-workflow.md` § 2.3 line 93; **#69** | With LINK on, whether TRACK's ENC3 cell shows the BPM or the resulting speed ratio | **decided** — the BPM, as built (review's call 2026-09-30) |
| the 120 s emulator tape | `emulator/CLAUDE.md` entry 71; `tape-sampling-workflow.md` § 2 | The emulator's timeline is 120 s where the device's is 480 s | **closed 2026-10-14 (#1039):** `NativeAudio` passes no `--tape-seconds` unless asked, `TRACK_SECONDS` is 480.0 and a test fails when it and the host disagree; the tick ladder gained 32- and 16-bar rungs (a DDR-058 deviation, `emulator/notes/as-built.md` § #1039). *2026-10-14, #935: the one constant's home is named.* The number is DDR-072's ruler, **8 min (480 s) a track**, and its spec home is `device-os-spec.md` § 3.3; in code the one value is the audio host's `--tape-seconds` default (`audio/host/audio_host.hpp`, `tape_seconds = 480.0`). The emulator keeps two copies of its own 120 s, `NativeAudio`'s `tape_seconds` default (`emulator/app/native_audio.py`, which passes it to the host) and `TRACK_SECONDS` (`emulator/app/tape.py`, the timeline and TRACK's SCRUB and ZOOM ranges); making them read the host's value is a code ticket. A per-project tape length (#168's answer) would move the home to the project file; not built |
| FILTER LP-only | `emulator/CLAUDE.md` entry 72; `design-decisions.md` H3 | The FILTER hold is low-pass only from the UI though the host takes both | **open** |
| mixer 0.8 defaults | `emulator/CLAUDE.md` entry 73 | The UI pushes track levels and master at 0.8 where the host's default is unity | **open** |
| SHIFT+STOP undo choices | `emulator/CLAUDE.md` entries 77–81; play-queue item 8 | `NOTHING TO UNDO` in the strip, the transport not stopping, the view not restored | **open** (the octave half is **answered** — #152, Elfy: not an edit) |
| the knob bar under a dimmed cell | `emulator/CLAUDE.md` entry 76; `emulator/ui/frame.py` | Whether a dead (dimmed) cell should also lose its coloured knob bar | **decided** — the bar stays, as built (review's call 2026-09-30) |
| `DISMISS` behind `1 2 3 4` | `device-os-spec.md` § 10.8; `emulator/notes/as-built.md` § D5b | Clearing the `UPDATE FAILED — KEPT` notice costs five presses rather than one turn | **decided** — `1 2 3 4`, as built (review's call 2026-09-30) |
| `FORGET PHONES` asks once | `device-os-spec.md` § 9.8; `emulator/CLAUDE.md` § #97 | Forgetting the paired phone asks `1 2 3 4` once, not RESET's twice | **decided** — once, as built (review's call 2026-09-30) |
| `TAPE BUFFER` row | `emulator/CLAUDE.md` entry 8c | A ninth, read-only row on FILE › SYSTEM the sketch does not list | **open** |
| the scope | `emulator/CLAUDE.md` entries 10, 38; `app/app.py` | The ENGINE screen draws a peak history, not a waveform, because the UI sees no samples | **open** |
| motion velocity | **#117** | Whether key velocity inferred from the case's motion sensor is one control or two | **decided** — one switch, `FIXED`/`MOTION` (review's call 2026-09-30); built when a board has the sensor |

### The device runtime (`arbutus_os/AS-BUILT.md`, `device-os-spec.md`)

| Tag | Where | The question, in one sentence | Status |
|---|---|---|---|
| the empty avahi service file | `arbutus_os/AS-BUILT.md` § The Wi-Fi poll; `device-os-spec.md` § 9.7; root `CLAUDE.md` | Whether an unpaired device should hold an empty (and so not-valid-XML) mDNS service file at all — avahi objects once per boot | **decided** — keep it (review's call 2026-09-29, § 9.7; one word reverses it) |
| keys during a host restart | `arbutus_os/AS-BUILT.md` § Audit Q2 | Keys pressed during the 1.5–3 s `arbutus-audio` restart are queued and replayed late as built, or should be dropped | **decided** — dropped, not replayed late (review's call 2026-09-30; #207) |
| a frame drawn but never pushed at shutdown | `arbutus_os/AS-BUILT.md` § A boot test…; #142 option 3 | Whether the panel's push thread is flushed when the UI unit stops | **open** — a board question |
| where a tape file lives and what SAVE does to it | **#157** (C1 step 3b) | The project folder exists from NEW, so what `SAVE` means for a tape already on the card, and where an unsaved take's file is | **answered** 2026-09-29 — **(a)**: SAVE moves the files into the project and recording carries on there; SAVE saves the open project, with a separate save-as-copy (#157) |
| the sync cadence | **#159** | How often the tape's write-behind forces its data to the card (12 ms a range on a free card; the only lever left) | **decided** — one `fdatasync` per range, ~2 s of a take at worst (review's call 2026-09-29, #159 closed; one flag reverses it) |
| does the ruler grow | **#168** | DDR-072 says a take that reaches the tape's end extends the tape; as built a track is 469 ranges and a take that reaches it ends with `TAPE FULL` | **answered** 2026-09-29 — **no**: DDR-072's fallback, a per-project tape length (8 min default) set before recording, plus a FILE › SYSTEM row (#168) |
| the peaks' precision | **#172** | A waveform reply's cost is its numbers: two decimal places (halves the bytes, moves 8 of 309 screenshots by 2–4 px), a pixel-exact magnitude array, or a binary envelope | **decided** — full precision stays (review's call 2026-09-29, #172 closed; under 1 ms of a 35 ms frame is not worth a moved screenshot) |
| the BLE handover on the radio's thread | **#180** (Q3) | The phone's last write blocks for the Wi-Fi join (up to 45 s) before it is answered; return it at once and run the join on a worker | **answered** 2026-09-29 — parked and run on `arbutus-net`'s own thread, 2.84–2.88 ms on the bench CM4 (§ 9.5.2) |
| the Wi-Fi country | **#182** (from #180) | A stock card keeps the Wi-Fi radio blocked until a regulatory country code is set, and nothing in § 9 asks the player for one — where does the instrument learn its country? | **answered** 2026-09-29 — `install.sh` sets `CA` for now; later the phone sends its region with the Wi-Fi credentials at pairing (#182). The Bluetooth half is done (`device-os-spec.md` § 9.5.3) |

## Inspection 2026-09-18: open gaps

The whole-system inspection after Track S finished (`docs/logs/2026-09-18.md`, session 6)
compared every document above against the tree. Recorded deviations are not listed — they
live in `emulator/CLAUDE.md` and `audio/CLAUDE.md`. These are the gaps and contradictions
that were **not** recorded anywhere, each needing a decision from Elfy; none was changed in
code, because each is a design question rather than a bug. Numbered so a later session can
close them by number.

1. **`proto` 1 vs 2.** `engine-spec.md` §15.6 is titled "Protocol additions (`proto` 2)"
   and §15.18 says "`proto` stays 2"; the host serves `kProtoVersion` = 1, `PROTOCOL.md`
   is headed `proto 1`, and the emulator sends and requires 1. The additions were made
   without the bump. Decide: bump to 2 (the spec's reading — a breaking change bumps
   `proto`) or amend §15.6/§15.18 to say the additions were compatible and 1 stands.
   **Decided 2026-09-18: there is no proto 2.** The built additions are proto 1; `library
   {op}` joins them; `cc`, `battery`, `motion` go to v1.5. **Ticket: #62** (spec amendments
   included). **Closed 2026-09-19 (D2, #23):** §15.6 is retitled "(`proto` 1)" with a
   `Built` column, §15.18's "`proto` stays 2" corrected to 1, and the v1.5 rows moved to
   a new §15.6.1 with a line each on why they wait.
2. **`library {op: rename | move | delete | trash}`** (§15.6) is not a host message. The
   browser's file ops (DDR-055) are `shutil.move` in `emulator/app/library_ops.py`, and
   the host is told with `rescan`. The host's `library` message is the box list. Decide
   whether the companion app (which the spec names as the other client) needs the host
   to own moves, or whether §15.6's row should say "UI-side; `rescan`".
   **Closed 2026-09-19 (D2, #23):** the host owns the operation —
   `library {op: rename | move | delete | trash}`, `PROTOCOL.md` § `library {op}`, with
   `NOT FOUND` / `EXISTS` / `STOCK` and `stock.json` as the only definition of stock
   (`audio/tests/host_files.py`). **Closed in full 2026-09-23 (#62 item 3):** the UI
   moves nothing itself any more — `emulator/app/library_ops.py` sends `trash`, `move`
   and `rename`, and keeps only the gesture layer (what is held, the undo group, the
   words on the strip). The reason it was left open — "the browser also runs with no
   host attached" — had stopped being true: `App` refuses to exist without the audio
   process (stage N1), so a host that has gone away refuses the op like any other
   refusal and **nothing moves**. Rewiring it found the root the ops resolve against
   was the **first** `--library` root while every invocation passes the read-only boxes
   first and the writable library last, so a phone's rename and a bounce with no `file`
   landed inside the running slot; the writable root is the **last** now
   (`PROTOCOL.md` § `library {op}`, `AudioHost::write_root`).
3. **`cc`** (§15.6: MIDI CC → `ARB_EV_CC`) has no host message and no emulator path:
   `drivers/desktop/midi_in.py` maps CC 16–19 to the encoders and drops every other CC.
   `ARB_EV_CC` exists in the ABI and no stock box reads it. Build the wire (small) or
   move `cc` to v1.5 beside `arbutus/midi` (modulation-spec §3.2).
4. **`battery` and `motion` in `state`** (§15.6). No `state.battery` (recorded in the map
   as a wrong assumption, not in the spec); `motion` is an offline-only test message and
   there is no subscription "like `meters`". Amend §15.6 or build the subscription when the
   IMU exists.
   *Items 2–4, decided 2026-09-18:* **`library {op}` is built as a proto 1 host message
   (#62)** so the phone can move files without the UI. `cc`, `battery` and `motion` are
   **v1.5**: `cc` with the MIDI stack (#21), `battery` once P3 has a gauge
   (`p3-mono-mule-spec.md` § Scope — in, #32, then #22/#23), `motion` once the IMU exists
   (#17, #22). **Closed 2026-09-19 (D2, #23)** in the spec: §15.6.1 is where the three
   now live, each with the reason it waits.
5. ~~**DDR-054 — SHIFT+STOP as one-step undo on SYNTH/DRUM** is not built.~~
   **Closed 2026-09-22 (#58, S12).** `emulator/app/edit_undo.py` holds one entry per
   instrument page, snapshotted from the page's own DDR-029 document — so an undo is a
   slot recall nobody had to save — and `App._transport` hands the gesture to the page
   after the browser's lift group. One step, not a revert: a run of turns on one cell, a
   whole browse, an octave move, an FX/MOD knob, a MOD route, DRUM's pad copy or a tape
   drop are one step each; a note and the bend are not. On those two pages it **never**
   reaches the tape's undo and never stops the transport (`emulator/notes/as-built.md` § S12,
   entries 77–81, four `[TBD-Elfy]`s). The stale sentence in `control-inventory.md`
   § LIFT/DROP about restoring a drum pad's previous sample "after a per-pad record" is
   removed with it — there is no per-pad record gesture.
6. **Per-instrument sequencers are half built.** DDR-034 (amended) and §15.4: each
   instrument owns its own sequencer; the host keeps a SEQ slot per instrument and clocks
   the active one. The emulator has **one** sequencer object, armed on the instrument that
   was active when SEQ was tapped, and it stays there — the other instrument has none. The
   inspection made the picture honest (parked box = not running, ticker and header name
   the box's own instrument, a whole-sound load or slot recall re-syncs the mirror with the
   host, a project or restart puts the box back on its own instrument), but the second
   sequencer — "switching SYNTH↔DRUM … resumes the other if it was armed" — is a feature
   nobody built. Decide: build it (a `NativeSequencer` per instrument, the SEQ page
   showing the active one's), or amend DDR-034 to "one sequencer, on the instrument it was
   armed on". **Decided 2026-09-18: build it, and the sequencer follows the instrument's
   A–C/X–Z slot; on the SEQ page the slot keys stay sequencer-only slots (engine + pattern
   + params) so a favourite sequence can be swapped without the sound. Ticket: #59.**
   **Closed 2026-09-23 (#59, S13):** built. `App.sequencers` is a dict keyed by instrument
   and `App.sequencer` is the active one's; a `NativeSequencer` takes its instrument at
   construction and keeps it, so `_set_loaded` no longer arms on "whichever is active now"
   and `restore` deliberately ignores a snapshot's `instrument` key — a SEQ slot letter is a
   sequencer-only slot and one taken on DRUM belongs to SYNTH when SYNTH is showing. Two
   things it exposed: `project_file.apply` was applying the SEQ bank to whichever instrument
   the player happened to be on rather than the project's, and `promote_pattern` wrote only
   one instrument's entered pattern. `emulator/notes/as-built.md` § S13; DDR-034 and
   `control-inventory.md` § Slot keys carry the as-built.
7. **`arbutus/stepmod`'s entry gesture** (modulation-spec §3.2: "hold SHIFT, turn ENC1 for
   the value, › to step") has no UI. The box takes a `pattern` asset
   (`{"kind":"stepmod","values":[…]}`, `audio/CLAUDE.md`) and nothing in `emulator/` writes
   one; a stepmod runs its default ramp. Decide the gesture (the note says SHIFT+encoders,
   DDR-041 forbids soft-keys on a modulator) or ship it as ramp-only. **Proposed
   2026-09-18: a latching STEP encoder that scrolls the selected step plus a VALUE encoder.
   Ticket: #60. CLOSED 2026-09-22** — built exactly as proposed, and the call the ticket
   asked for is taken: STEP and VALUE are the **UI's** pseudo-parameters, editing the
   `pattern` asset, because DDR-037 makes the values content. `modulation-spec.md`
   §3.2.1 is the as-built; `emulator/notes/as-built.md` § S14 has the drawing and gesture
   choices and the one protocol change it needed.
8. **The SEQ step number** (screen-sketches § SEQ: "big step number … increments on entry
   and on playback"). The host reports no step position, so `display_step` is the pattern
   *length*; it only moves during entry. Either the host gains a `seq_step` field on
   `meters` or the sketch drops "on playback".
9. **MIDI pitch bend** was spec'd (DDR-052, this file's scope table "USB MIDI
   notes/bend/CC/clock") and not wired — fixed in the inspection (`midi_in.py` sends
   `pitchwheel` as `bend`), listed here only so the scope row reads true. Aftertouch stays
   modulation-spec §14's question.
10. **DDR-064's recovery gesture** ("REC+STOP held at power-on enters recovery") belongs to
    the device OS with the rest of DDR-064 and is not in `master-plan.md`'s "deliberately
    not built" list; add it there or to #18. **Closed 2026-09-19 (#18):**
    `device-os-spec.md` § 12 is the gesture's home — what recovery is, what it can do and
    how it is exited; on the emulator it does not exist (there is no power-on).
11. **Stale entry gestures in the spec.** `screen-sketches.md` § SEQ (v0.1 block) and
    `tape-sampling-workflow.md` §3 still said "hold SHIFT" for endless entry; N1 moved it
    to the box's REC soft-key and H2 fixed `control-inventory.md` § arrow keys but not
    § chromatic keys. The inspection fixed the workflow and control-inventory lines;
    screen-sketches keeps the v0.1 text under its as-built note, which says it is
    superseded.
12. **`audio/library/README.md` said `parity.patch` was chosen by
    `test_native_parity.py`**, deleted at N1 — fixed (the patch stays as stock content).
    `tape-sampling-workflow.md` §1 said samples come from `emulator/library/`, also gone at
    N1 — fixed. `control-inventory.md` § Physical layout said the hotspot file was traced
    from an off-axis photo; it has been rectified and metric since 2026-09-10 — fixed.

---

## Session History
- #935 (2026-10-14, autist; the whole-system review of 2026-10-13, I5–I9 and G8) — dated lines, nothing rewritten: the scope table loses SAVE (D97) and backup (D99); BLE pairing and "no pin measures" the battery in the build order; the device-os row, steps 9–10, the D16 (WM8904), compute-options and OS-9 rows (the Zero's keys UART is `device-os-spec.md` § 6.2); the 120 s tape row names the constant's home; the roll-up's banner (a 09-25 snapshot, kept for the review's answers); **new § Zero-class through the package**, the G8 pass and its fog
- [[logs/2026-09-29-12-47]] — #186: one new row in § `[TBD-Elfy]` — the ten reference designators (the `C1`/`U4`-style part labels) that are on both the P3 Audio-IO and Power sheets, which the assembled KiCad project renames on the Audio-IO side because one board cannot carry two `U4`s; the default stands and H6 (#36) freezes whatever is chosen
- [[logs/2026-09-25-13-36]] — audit Q3 (#31), the spec package close: **re-indexed** — `tape-streaming-design.md`, the work-order and the bring-up join the tables; `software-stack-v1.md`'s row says what its per-section banners now say; every measurement, audit, letter and V0.2 document named as context; a *Process* table for the map and the play queue; the `device-os-spec.md` row's TBD list corrected to `OS-1`…`OS-11` (all answered but `OS-10`). **New § `[TBD-Elfy]`** — every tag in the tree rolled into one list, answered ones marked with their DDR, open ones left open, nothing decided
- [[logs/2026-09-16]] — created (grill session 2); supersedes `v1-product-plan.md`
- [[logs/2026-09-18]] — stage I2: the FX box list is complete (`delay`, `chorus`, `crusher`, `drive`, `eq3`); thirty-one stock boxes, no more planned for v1
- [[logs/2026-09-18]] — stage J1: `audio/library/README.md` added to the package as the authority on the library's document formats, the project file among them (nothing here specified one)
- [[logs/2026-09-18]] — stage J2: **steps 1–8 of the build order are done** — the instrument this package describes is built, and the staged build handoff deleted itself. Steps 9 (device OS) and 10 (Pi) remain and both wait for hardware. § Settings and § Stock joined § Projects in `audio/library/README.md` as formats this package never specified
- [[logs/2026-09-18]] — session 6, the whole-system inspection: § Inspection 2026-09-18 added — twelve numbered gaps and contradictions between the package and the tree that no stage recorded (`proto` 1 vs 2, `library`/`cc`/`battery` messages, DDR-054 undo, the half-built per-instrument sequencers, stepmod entry, the SEQ step number, and the stale lines it fixed)
- [[logs/2026-09-18]] — session 8: § Inspection items 1–7 decided and ticketed — proto stays 1 with `library {op}` built (#62), per-instrument sequencers with two kinds of slot (#59), DDR-054 undo (#58), stepmod entry (#60); items 3–4 to v1.5
- [[logs/2026-09-19]] — P2 (#18): `device-os-spec.md` joins the package as the authority on the embedded stack; the `software-stack-v1.md` row now names it as §7's successor; § Inspection item 10 closed (recovery lives in `device-os-spec.md` § 12)
- [[logs/2026-09-22-20-18]] — **§ Inspection item 2 closed in full** (#62 item 3): the UI no longer moves its own library files, and the reason it was left half-closed — "the browser also runs with no host attached" — had stopped being true. The rewiring found `library {op}` and `clip_to_asset` resolving against the **first** `--library` root while every invocation passes the writable library **last**, so a phone's rename landed inside the running A/B slot
- [[logs/2026-09-22-23-27]] — S14 (#60): § Inspection **item 7 closed** — the stepmod entry gesture is two UI-side encoder parameters (STEP, VALUE) editing the `pattern` asset; `modulation-spec.md` §3.2.1 is the as-built

