---
tags: [emulator, tests]
status: active
read_when: you write or run this folder's tests, move a screenshot baseline, or use the verification harness
index: emulator/CLAUDE.md
---

# Tests and the verification harness

> Moved verbatim from `emulator/CLAUDE.md` on 2026-10-08 (#440), headings unchanged, so a `§ <name>` citing that file finds its section here. The index of this folder's notes is `emulator/CLAUDE.md` § Read by task.

## Tests

```bash
./run_tests.sh                                   # unit tests
./run_tests.sh verify                            # unit tests, then every feature script
venv/bin/python -m unittest discover -s tests -t .
```

Every test drives the real audio process, offline, with `render` as the clock —
`tests/native_support.py` is the shared rig (`host()`, `app_for()`,
`library_copy()`). No test reads the checkout's `audio/library/` itself: a test's library is
a copy of the files git tracks there (`testing/stock_library.py`, #851), so what a player left
in a played clone never reaches a test. They **skip with the build command** when `audio/build` is
absent, so a bare checkout still runs green. The §11 parity test is gone: it
compared the two audio paths and there is only one.

- `tests/test_native_smoke.py` — every stock synth sounds, the rack binds a
  macro through LIFT/DROP, the SEQ soft-keys reach `arbutus/endless`, a kit
  plays, every `audio/library` document loads and a missing engine is refused
  by name (DDR-043).
- `tests/test_native_tape.py` — the tape keys, the click, `clip_peaks` and
  `tape_edit` against the host's medium.
- `tests/test_tape_ui.py` — stage E2: the tick ladder as arithmetic, the two
  banks, STOP+‹ › jumps, lift undo groups, LIFT ALL dropping per lane, merge
  drop and both drop-on-instrument paths, against the real offline host.
- `tests/test_mixer_ui.py` — stage F2: MIX's eight columns and which four the
  keys address, mute latching on SHIFT+1-4 and a held key soloing (with a tap
  still being the subpage), the gate read back out of the host rather than
  computed, EQ in dB and Hz with the host's clamp in the cell, the drawn curve
  against the host's own sections, the master rack landing on the master bus
  and on neither instrument, MASTER's six knobs and the signal path.
- `tests/test_file_system.py` — stage J2: the settings document (and that a
  project carries none of it), FILE › MIDI / USB / SYSTEM, the clock meter as
  arithmetic, DDR-021's force-save, RESET asking twice, every browser file op
  against a throwaway library, and the battery.
- `tests/test_project_file.py` — stage J1: the FILE frame and all four
  subpages, the 1-2-3-4 confirm and every way it is cancelled, DDR-020's
  auto-naming, and the round trip — a song saved, the instrument NEWed so the
  **host's** document demonstrably moves, then loaded and compared field by
  field (engine, envelope, racks, kit, clips, per-track EQ and mute, tempo and
  `base_bpm`). BOUNCE is measured as a file length and as the MASTER FX switch
  changing the samples.
- `tests/test_fx_boxes.py` — stage I2: the five effect boxes in the FX browser,
  the rack page's eight cells built from each descriptor alone (including
  `arbutus/delay`'s `1/8` and `arbutus/crusher`'s `OFF`, both of which are the
  box's own `format_param` and not a table up here), `arbutus/crusher` leaving
  two cells empty, and the five stage-I2 stock racks loading whole with their
  macros. A macro moves its **position**, never the tab's stored value — the
  host owns the target.
- `tests/test_synth_boxes.py` — stage I1: the four new boxes offered by the
  library and their eight slots built from the descriptor (including
  `wavetable`'s `format_param` frame label), the named default box on SYNTH and
  DRUM, and DRUM › ENGINE editing `arbutus/dbox`'s pads, which carry parameters
  and **no file** — the page draws `NO SAMPLE` and the turn still lands in the
  kit document.
- `tests/test_per_pad.py` — stage N2b (§15.3, §15.11): DRUM › ENGINE's eight
  `per_note` slots and that a turn lands in the **kit document** rather than in
  a Param, the page following the last-hit key, the slots rebuilding when the
  pad changes, `copy_pad` through LIFT/DROP with the note eaten, the AD's
  locked SUS/REL, DRUM keeping the host's `a 0 / d 4`, and sampler v2's slot
  names, `format_param` labels and loop markers.
- `tests/test_voice_policy.py` — the ENV SHIFT row and the bend (DDR-052): the
  row's names and the host's defaults, each knob reaching the host's `env`
  block, the clamps, DRUM keeping its own values, the arrows as octave vs bend,
  and a patch round-trip including one written before the row existed. The bend
  is **measured** — C4 261.7 Hz → 293.0 Hz at BEND 2 and back.
- `tests/test_phase3.py` — chrome geometry (keymap covers all 52 keys + 4
  encoders exactly once, no overlaps, hit-testing, integer framebuffer rect
  inside the cutout), `midi.toml` parsing and all three relative-CC encodings,
  the SEQ C-slot fix, and `main.py --headless --exit-after 1` in a subprocess
  (exit 0 + a screenshot showing photo *and* framebuffer). PNGs to
  `validation/ui/phase3/`: `chrome.png`, `chrome-zoom.png`, `chrome-press.png`,
  `zoom-only.png`.
- `tests/test_live_input.py` — stage G (DDR-063): the box in the SYNTH library
  with `SOURCE · GAIN`, SOURCE moving the host's routing and being read back
  from it on both pages, the RESAMPLE patch selecting resample as it loads,
  GAIN reading in dB, and the signal path's input row.
- `tests/test_mic.py` — 36 tests: the trim/normalise/fade DSP, the capture
  state machine (pre-roll, threshold, LENGTH ceiling, RETRY, ring wraparound,
  MONITOR reaching the host), the INPUT page end to end through `FakeMic`, and
  `.seq` pattern items. No audio *device* needed.
- `tests/test_phase2b.py` — SEQ (entry, chords, rests, playback, HOLD, engine
  menu, slots), TEMPO (params, tap tempo, click), HELP (page + both overlays),
  INPUT/USB-FILE stubs, page background categories, the north-star trace from
  `tape-sampling-workflow.md` §5.1 (with a master WAV), slot recall §5.3 and
  the persistence round-trip. PNGs to `validation/ui/phase2b/`.
- `tests/test_phase2a.py` — DRUM, tape ops, TRACK, MIXER through the same seam;
  PNGs to `validation/ui/phase2a/`. Includes unit tests for the pure clip
  functions in `app/tape.py`.
- `tests/test_phase1_ui.py` — drives the app through the scripted seam
  (`ScriptedInput` + `HeadlessDisplay` + the offline host), writes PNGs to
  `validation/ui/phase1/` and a WAV of the note-on check.

**454 tests, 32/32 verify features** as of stage J2 (405 / 30 at J1). H1 added
`test_seq_boxes.py` (7), pinning the two places `emulator/` assumed there was
only one sequencer box — the SEQ page's default engine and the `.seq` replay
bridge's soft-keys; G added `test_live_input.py` (12) and the `live-input`
feature (7 shots); I1 added `test_synth_boxes.py` (11) — the four new boxes'
pages built from the descriptor alone, DRUM › ENGINE on `arbutus/dbox`'s
**fileless** pads, and `SynthPage.DEFAULT_BOX` / `DrumPage.DEFAULT_BOX`, which
is H1's SEQ trap again (`dbox` sorts before `drum`); I2 added
`test_fx_boxes.py` (11) and one UI fix — see § I2 below. F2 added
`test_mixer_ui.py` (33) and the `mixer-strip` feature (13 shots); F1's
`test_native_tape.py::TestF1MixerMessages` still pins the raw shim surface —
the `eq` message, mute/solo and the master rack — under the page that now
drives it.

The UI test asserts navigation and state, never internals of the renderer:
boot → SYNTH›ENGINE, `2` → ENV, hold SHIFT, SHIFT+1 → engine menu, ENC2 scroll,
DRUM → stub, SYNTH → still ENV (DDR-028).

## Verification harness (phase 4)

`tools/verify.py` drives the app from a feature script through the same seam the
tests use (`ScriptedInput` + `HeadlessDisplay` + the offline host) and writes
framebuffer PNGs plus a WAV per feature. pygame is never opened for a window —
except the `chrome` feature, which shells out to `main.py --headless`.

```bash
venv/bin/python tools/verify.py --list              # the mapped features
venv/bin/python tools/verify.py track-record        # one feature
venv/bin/python tools/verify.py --all --diff        # all, against baselines
venv/bin/python tools/verify.py synth-page --promote # baseline this run's shots
./run_tests.sh verify                               # unit tests, then --all --diff
```

- Scripts: the agent tooling (private)
  (events: `key`, `note`, `enc`+`delta`, `wait_blocks`, `sleep`, `flush`,
  `reload`, `shot`, `assert`).
- Prose map, doctor, grading rules and the Opus grader prompt:
  the agent tooling (private) + `features/README.md`.
- Evidence: `validation/ui/<feature>/`; baselines: `validation/ui/baseline/<feature>/`.
- Each feature runs in its own scratch state dir, `state/verify-<feature>/`,
  wiped per run — never the user's `state/`.
- Grading: baseline present → pixel diff must be 0; no baseline → an Opus
  subagent grades the PNG against `ui-aesthetic.md` §5 + `screen-sketches.md`,
  and only Elvin promotes.
- Needs `pyyaml` in the venv (`venv/bin/pip install pyyaml`).
