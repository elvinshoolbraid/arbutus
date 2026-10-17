---
tags: [spec, active, ui, audio]
status: active
related: [[control-inventory]], [[screen-sketches]], [[design-decisions]], [[emulator-spec]]
---

# Tape + Sequencer Workflow — Arbutus-1

Tape model, clip model, transport, sequencer run model, state transitions, acceptance traces.
Rewritten 2026-09-08. §1 rewritten 2026-09-11 when live sampling came into scope (DDR-036).
§2–§3 and §6 rewritten 2026-09-16 for v1: 8 tracks, overdub-only, the tick ladder, LINK,
tape→instrument, per-instrument sequencers (DDR-056…060; `op1-gap-audit.md`).

---

## 1. Sampling from INPUT

**In scope as of 2026-09-11 (DDR-036).** The INPUT page listens; it does not record on a keypress.

| Step | Gesture | Effect |
|---|---|---|
| arm | `1` on INPUT | waits for a transient over THRESH; `1` again disarms |
| trigger | any sound over THRESH | capture starts ~20 ms *before* it, from the rolling buffer |
| end | ~350 ms under THRESH, or LENGTH reached | trim, normalise, fade both ends |
| keep | automatic | `library/synth/sampler/mic-NN.wav`, loaded onto SYNTH |
| retry | `2` | discard the take and re-arm |
| audition | any chromatic key | the new sample plays, on INPUT or SYNTH |

Params: THRESH / LENGTH / GAIN / MONITOR (ENC1–4; MONITOR off by default — a laptop's mic and speakers feed back). Slots 4–7 stay empty so SHIFT fine-tunes each.

*As built, 2026-09-17 (stage G):* slot 4 is **SOURCE** (`mic / line / usb / resample`, DDR-063) — the host's `input_source` and the same value as the `LIVE INPUT` engine's SOURCE knob, read back rather than kept. 5–7 stay empty, so SHIFT still fine-tunes LENGTH and GAIN but not THRESH. INPUT's GAIN is the capture path's trim, before the threshold, and is a different knob from that engine's.

Samples come from `audio/library/synth/sampler/` WAVs too, loaded via the engine menu (`emulator/library/` is gone since stage N1). Resample and INPUT→tape recording remain post-demo. Lift/drop between tape and sampler engines works on already-loaded audio (DDR-012).

## 2. Tape model

- **8 tracks** in two banks of four (DDR-060), stereo, 48 kHz float32; 8 min each on the device (`--tape-seconds`, the host's default) — **moved to storage 2026-09-19 (Elfy, C1 #74): one-second chunks per track streamed by a tape I/O thread, the ruler 8 min with no take limit and no lift limit, a 240 track-second RAM window over it (`device-os-spec.md` § 3.3, OS-11)**; **the emulator asks for 2 min** (`app/native_audio.py` `tape_seconds`, `app/tape.py` `TRACK_SECONDS` — carried from the Python demo, recorded `[TBD-Elfy]` in `emulator/notes/as-built.md` § Audit Q1). TRACK shows one bank; **SHIFT+1–4 flips bank and selects**, plain 1–4 select within the shown bank. A bank glyph appears once bank B holds a clip `[TBD-Elfy]`. The model is always 8-wide; the mixer, loop and recording are 8-aware.
- Time display: ruler ticks from the **zoom-dependent ladder** (§2.3) + mm:ss at right.
- Tape speed: TRACK ENC3 = the **song** speed, one control with BPM while `LINK` is on (DDR-059); the SPEED key = a **clip** effect (DDR-010). Speed and pitch coupled everywhere (DDR-017); never a stretch.
- Selected track persists across pages. Recording level (ENC4) is drawn as a tick on the playhead at all times, not only while armed.
  *Audit Q1, 2026-09-18 — not built as written:* the playhead carries the **input peak** as a foot, and only while armed or rolling (`docs/logs/2026-09-12.md` § 2, `pages/track.py` `_render_cursor`); nothing draws ENC4's value. `screen-sketches.md` § TRACK carries both sentences. Spec beats code; issue to follow (`docs/design/audit-2026-09-18.md`).

### 2.1 Clip model

Per track: ordered, non-overlapping clips `{start, end, audio, gain}`. Gaps = silence.

| Op | Gesture | Effect |
|---|---|---|
| record | REC (arm selected) + PLAY … STOP | **overdub, always** (DDR-056): the take writes instrument + the track's own playback; clips it covers are split at the take's boundaries and their covered portion is absorbed into the new clip; the remainders stay as separate clips. The list never overlaps. Replace = LIFT first. Punch in/out mid-clip follows the same rule |
| record on the grid | **REC held + a note key** … STOP | same, but the playhead snaps to the nearest **tick of the current ladder** (§2.3, DDR-058; `App.rec_tick`) first and the take starts from that note — takes layer without hunting for the downbeat |
| SLICE | tap | split clip under playhead |
| TAPE | SHIFT+SLICE | merge two clips on selected track with gap < ~10 ms |
| LIFT | tap | remove clip under cursor → buffer (DDR-012/013); playhead parks at that clip's start. Consecutive lifts with no drop between form **one undo group** (DDR-055) |
| LIFT ALL | SHIFT+LIFT | clips under cursor on all 8 tracks → buffer |
| DROP | tap | insert buffer at playhead on selected track (replace what it overlaps), then playhead moves to the end of it — repeat DROP to lay copies end to end. A LIFT ALL buffer drops each clip back onto **its own track** |
| MERGE DROP | SHIFT+DROP | a LIFT ALL buffer mixed down onto the **selected** track. Strip hints `SHIFT+DROP = MERGE` while such a buffer is held, if it fits; HELP-hold always says so |
| DROP on an instrument | DROP on SYNTH/DRUM › ENGINE | the buffer becomes the sampler's sample (SYNTH) or the **last-hit pad's** sample (DRUM); a LIFT ALL buffer arrives as a stereo mixdown. This *is* DDR-018's "bake range → sample slot". Host: `clip_to_asset` |
| SLIDE | SHIFT+ENC1 | move clip under cursor until it touches a neighbour |
| REV / SPEED / FILTER tap (stopped) | | destructive on clip under cursor (DDR-010) |
| undo | SHIFT+STOP | 1 level on the TRACK page (tape ops; a lift group counts as one); on SYNTH/DRUM it undoes the last edit there instead (DDR-054) |

"Under cursor" = under the playhead when stopped, **or within 10 ms of a clip edge** — parking on a marker after a slice still selects the clip beside it. SLICE is the exception: it needs the cursor strictly inside. No region selection; no loop-region lift.

Markers = clip edges (plus 0), drawn as white vertical lines on the lane. SHIFT + `‹ ›` steps by **one tick of the current ladder** (§2.3), whether or not a loop is on (DDR-057); clip edges are not snap targets — zoom in to land on one. STOP held + `‹` / `›` jumps to loop in / loop out when the loop is on, tape start / end of the last clip when it is off.

### 2.3 The tick ladder and LINK (DDR-058, DDR-059)

The ruler shows the finest subdivision that keeps **8–16 ticks on screen**:
8 bars → 4 → 2 → 1 → ½ → ¼ → ⅛ → 1/16 → 1/32, then 1 s → 100 ms → 10 ms → 1 ms →
samples once the window is under ~250 ms. Bar-multiple ticks are labelled with bar
numbers; sub-bar ticks are unlabelled but taller on the beat. One SHIFT+‹ › = one tick;
**REC + note snaps to the nearest tick**, so zoom sets the precision of a punch-in. The
8–16 rule is what to tune by ear. (Backup if the ladder does not feel right: a `SNAP`
ENUM on TRACK's SHIFT row — shelved.)

*As built, 2026-09-17 (stage E2, `app/tape.tick_ladder`):* the musical rungs are
8 bars … 1/32 as written; below them the rungs are **1-2-5 per decade** (1 s, 500 ms,
200 ms, 100 ms … down to one sample — which contains the four this section names) and
they take over where the musical rungs **run out**, ~62 ms at 120 BPM, rather than at a
fixed 250 ms. Bare decades step by 10 and a fixed crossover both leave windows where
neither ladder can keep 8–16 ticks, and the 8–16 rule is the one that matters. One seam
remains near a 0.35 s window, where the 2.5 step gives ~7 ticks.

`LINK` (TEMPO page, default on): BPM and tape speed are one control; turning either
moves both, audio resamples. Off: BPM moves the grid only, ENC3 moves the audio only.

*As built, 2026-09-24 (#69):* "turning either" is both knobs at last. TRACK's ENC3 was only
ever the varispeed, so with LINK on it moved the tape and left BPM behind; it now reads
`BPM` and moves the song tempo while LINK is on (1 BPM a detent, through `App.set_bpm`), and
the tape follows on the host's link factor rather than on a second speed. The varispeed is
still there under SHIFT (the fixed ×0.25…4 steps), because SPEED multiplies on top of LINK
and is a clip effect, not a tempo. What ENC3's cell *reads* while LINK is on — the BPM, or
the resulting speed ratio — is `[TBD-Elfy]` (`emulator/CLAUDE.md` § TRACK); the build takes
the BPM, matching what the knob moves.

### 2.2 Transport

| State | Enter | Notes |
|---|---|---|
| STOPPED | STOP | 2nd STOP → playhead to 0 (or loop in, if loop on); STOP held + ‹ › → loop / tape ends (DDR-057) |
| PLAYING | PLAY | loop on → bounded by IN/OUT |
| RECORDING | REC (armed) then PLAY, or PLAY then REC, or REC held + a note key | selected track only; strip `RECORDING → TRACK n` |
| REV-HOLD / SPEED-HOLD / FILTER-HOLD | hold key while PLAYING | momentary, ENC4 live, snaps back on release (DDR-001/002) |

Loop: IN sets in-point at playhead, OUT sets out-point, LOOP toggles. Loop persists across pages. Transport keys mean the same on every page.

## 3. Sequencer run model

- **Each instrument owns a sequencer** (engine + pattern + armed state); only the active instrument's runs; switching SYNTH↔DRUM stops one and resumes the other if armed (DDR-034 as amended). One at a time is deliberate: this is a notebook for progressive commitment, not a live rig — drums go to tape, loop, then sequence the synth over them (OP-1 §8.16).
- Endless entry: hold the box's REC soft-key (`1` on SEQ, DDR-041 — not SHIFT, since stage N1) → step 0; each key/chord → step, increment; › rest; **hold keys + › = tied note**; ‹ undo. Release REC → done. Next REC press overwrites from 0.
- Playback: runs while a chromatic key is held (transposed to it). HOLD keeps it running.
- Recording while a sequencer runs records its output (it commandeers its instrument).
- Endless params: `DIV · SWING · ACTIVE · HOLD` / SHIFT `GATE · PATTERN · — · DIR`. GATE = 10–100 % of the step (so a 75 % duty pluck no longer needs the amp ENV mangled); PATTERN = ENUM of rhythm masks, SHIFT+turn rotates it. No fixed length; max 99 steps.
- Other v1 sequencers (`arp`, `pattern`, `euclid`) declare their own params and soft-keys (DDR-041); shapes in `v1-spec-package.md`, ranges by playtest.
- A pattern is the sequencer's browser item (an asset, DDR-037): `library/seq/<engine>/<name>.seq`.

## 4. State transition table (nav + transport, demo)

| From | Input | To | Side effect |
|---|---|---|---|
| any page | page key P | P (restored to its last state) | — |
| SYNTH/DRUM | 1–4 | subpage n | persists |
| SYNTH/DRUM | SHIFT+1 / +3 / +4 | menu (engine / fx / lfo) | live-apply on scroll |
| SEQ | SHIFT+SEQ | seq engine menu | |
| menu | any page key | that page (menu closed) | current item stays loaded |
| SYNTH/DRUM/SEQ | slot letter | same page, slot recalled | autosave continues into new slot |
| TRACK | 1–4 | track n selected | |
| any | PLAY / STOP / REC | transport per §2.2 | |
| TRACK stopped | SLICE / TAPE / LIFT / DROP / REV / SPEED / FILTER | clip op | undo point |
| TRACK playing | hold REV / SPEED / FILTER | momentary | ENC4 relabels |
| any | hold HELP (+ control) | overlay | release → back, no nav |
| any | TEMPO | TEMPO page; repeated taps = tap tempo | |

Every page has an exit (any page key). Every overlay dismisses on release. Menus dismiss on any page key.

## 5. Acceptance traces

### 5.1 North-star: build a track (harness + demo script)
1. DRUM → SHIFT+1 → pick kit → SEQ → hold SHIFT, enter 8-step beat → release → LOOP (hold) → TRACK → 1 → REC, PLAY, 4 bars, STOP.
2. SYNTH → SHIFT+1 → bass sample → play → TRACK → 2 → REC, PLAY, STOP.
3. Lead on TRK3 same way.
4. MIXER › MIX → balance levels; SHIFT → pans.
5. TRACK → IN at bar 1, OUT at bar 5, LOOP → PLAY. Hold FILTER + ENC4 sweep, release.
6. Screenshot every page/state visited; all pass `ui-aesthetic.md` §5. Audio: 4 bars of non-silent stereo on master.

### 5.2 Tape edit
Record clip → STOP → scrub into clip → SLICE → LIFT (strip shows `LIFTED: TRK1 …`) → scrub → DROP → SHIFT+STOP undo → clip back.

### 5.3 Slot recall
SYNTH slot A: load patch, tweak ATK → slot B: different engine → slot A → ATK value + engine restored.

### 5.4 Mixdown (v1, native)
Seven tracks recorded → MIXER › MIX: zero the four you don't want → MIXER › FX: bind a macro to the sweep you want → SYNTH › SHIFT+1: load `LIVE INPUT`, SOURCE = RESAMPLE → TRACK: select 8, position → back to MIXER › FX → REC, PLAY, ride the macro, STOP. Track 8 holds the mixdown with the sweep baked; FILE › BOUNCE or the app exports it. No capture mode exists (DDR-063).

*As built, 2026-09-17 (stage G):* the resample bus is the master **one block late** — the engine renders before the mix it taps, exactly as the `master` sidechain does — minus the armed track, the instrument bus, the click and the monitor. The last three are additions to §15.2's wording: without the instrument bus this is a unity-gain loop rather than a bounce, and a bounce must carry neither the click nor the monitor. The subtraction is exact through the mixer's gains, COMP, LEVEL, BAL and the limiter; DRIVE, the master rack and the FILTER hold are not un-mixed, so an effect on the master returns its own wet tail (it converges — a rack's wet level is under 1). One block of latency lands in the recorded track, which at 128 frames is 2.7 ms.

## 6. Sampling round trip

Tape → instrument: LIFT (or LIFT ALL) on TRACK, DROP on SYNTH › ENGINE (sampler) or DRUM › ENGINE (last-hit pad). Instrument → tape: play it and record, or DROP a lifted sample onto the tape (DDR-012, copies). INPUT → instrument: DDR-036. INPUT → tape: load `LIVE INPUT` and record.

---

## Session History
- [[logs/2026-03-27]] — scaffold
- [[logs/2026-04-26]] — REWIND flip decision (superseded: REV key now)
- [[logs/2026-09-08]] — rewrite: clip model, transport, endless run model, transitions, traces
- [[logs/2026-09-11]] — §1 rewritten: sampling is in scope and lives on INPUT (arm → transient → trim → SYNTH)
- [[logs/2026-09-11]] — record gesture gains REC + note (snaps to the nearest bar)
- [[logs/2026-09-16]] — session 2: 8 tracks, overdub-only (DDR-056), lift undo groups, merge drop, drop-on-instrument, tick ladder + LINK (§2.3), STOP+‹ ›, per-instrument sequencers, GATE/PATTERN/tied notes, mixdown trace §5.4, sampling round trip §6
- [[logs/2026-09-17]] — stage E2 built §2/§2.1/§2.3's UI half; §2.3 gains the as-built note on the sub-bar rungs
- [[logs/2026-09-17]] — stage G as-built: §1 gains SOURCE on slot 4, §5.4 says what the resample bus actually subtracts
- [[logs/2026-09-18]] — inspection: §1's sample folder is `audio/library/synth/sampler/` (not the deleted `emulator/library/`); §3's endless entry is the box's REC soft-key, not SHIFT (since N1). §3's "each instrument owns a sequencer" is half built — `v1-spec-package.md` § Inspection 2026-09-18 item 6
- [[logs/2026-09-18]] — audit Q1 (#31): as-built corrections and audit notes; findings in `docs/design/audit-2026-09-18.md`
- [[logs/2026-09-19]] — session 2 (compute downsizing): the tape **moves to storage** — one-second chunks, a tape I/O thread, the ruler 8 min with no take limit, a 240 track-second RAM window (`device-os-spec.md` § 3.3, OS-11); C1 (#74) builds it
- [[logs/2026-09-24-13-14]] — cloud routine (#69): § 2.3 gains its *as built* paragraph — "turning either moves both" is true of both knobs now, and what ENC3's cell reads while LINK is on is `[TBD-Elfy]`
