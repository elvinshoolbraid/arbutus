# Desktop Emulator — ArbutusOS

320×240 pixel-perfect emulator of the Arbutus. Runs on Mac/Linux/Windows
without hardware. The v1 spec package is built here; nothing in this folder is a
placeholder.

> **Native audio is the architecture (stage N1, 2026-09-17).** `emulator/` is the
> **UI**: it draws, it routes keys, and it owns the tape's clip algebra — every
> sample of sound is made by the `arbutus-audio` process in `audio/`.
> `App(native=...)` is required and raises without it; `run.sh` starts the
> process for you. What was deleted and why: `notes/audio-host.md` § Native audio
> becomes the architecture. The phases' history: `notes/as-built.md` § #1016.

Spec set (obey it, don't reinterpret): the package indexed in
`docs/design/v1-spec-package.md` — for this folder chiefly `hollyhock-demo-spec.md`
(the demo's scope, frozen), `screen-sketches.md`, `ui-aesthetic.md`,
`control-inventory.md`, `parameter-framework.md`, `emulator-spec.md`,
`design-decisions.md`, plus `engine-spec.md` and `modulation-spec.md` — those
two are now this folder's spec as much as `audio/`'s, because the UI is built
out of what the host reports. Pages marked **(v1)** in the sketches are *not*
demo work: they are built natively, later, per the package's build order.

## Read by task

This file is the folder's rules: what every edit here needs. The rest is in `notes/`, one file per kind of work; nothing auto-loads them. Read the row for your task, and only that.

| You are | Read |
|---|---|
| changing the window, the photo, key hotspots, device pixels, keyboard / mouse / MIDI input, the look editor | `notes/chrome-and-input.md` |
| touching how the UI talks to `arbutus-audio`: the rack, the modules, live input; or asking why there is no Python audio | `notes/audio-host.md` |
| touching autosave, `state/`, project or pattern files | `notes/state-and-files.md` |
| writing or running tests, moving a baseline, using the harness | `notes/testing.md` |
| changing how a page draws or what a gesture does, or gathering choices for Elvin's play test | `notes/ui-choices.md`: a choice no spec settles goes there, `[TBD, Elvin's call]` if it is his, else *run's call* |
| asking why something is the way it is (a ticket, a stage, a function) | grep `notes/as-built.md`; never read it whole |
| finishing a ticket with as-built detail | append a `## #<n> — …` section to `notes/as-built.md`, never to this file |

**An old `emulator/CLAUDE.md` § <name> citation** whose section is not below moved with its heading unchanged: `grep -n "^##.*<name>" emulator/notes/*.md`.

## Layout

```
emulator/
  ui/                 rendering, no app knowledge
    font.py           two hand-authored bitmap fonts (5x7 small, 8x12 large)
    palette.py        the ONLY place with literal RGB: a LIGHT and a DARK table (#334)
    zones.py          fixed zone rects (header 16 / strip 12 / main 160 / params 36 / keys 16)
    draw.py           fill_rect, hline, vline, text, polyline on a (240,320,3) uint8 array
    frame.py          render_frame(app): header, strip, param bar, key strip, page main
  app/
    keys.py           every physical key + 4 encoders
    settings.py       the device's own settings document (<library>/settings.json)
    library_ops.py    the browser's LIFT/DROP/trash/undo over real files (DDR-055)
    midi_clock.py     the MIDI clock meter behind FILE > MIDI's CLOCK IN
    battery.py        the platform's battery, or an honest "no telemetry"
    params.py         Param (parameter-framework.md slot schema, no colour field)
    page.py           Page base class + per-page persistent state (DDR-028)
    app.py            page registry, input routing, the per-frame push down
    library.py        the shape of a library folder + the lookup pages do
    instrument.py     which engine, which item, ENV, the octave. No audio.
    menu.py           generic two-pane selector page
    save_row.py       the FX browser's SAVE THIS RACK row: up saves, down loads (#820)
    slots.py          6 slots per page, autosaved to state/<page>.json (DDR-029)
    patch.py          promoting a slot to a named library patch + name generator
    tape.py           clip model + pure clip ops + transport (§2). No audio.
    mixer.py          track gain/pan/master as UI state, pushed as `mixer`
    persist.py        the tape's half of the autosave: tape.json, the tape's folder (spec §5)
    project_file.py   FILE > PROJECT: the project document, its media, and `Document`
                      (app.doc: the open project's owner, state/project.json; #1017)
    mic.py            MicCapture: ring buffer, transient trigger, trim, save
    native_audio.py   the arbutus-audio process, the socket, the state mirror
    native_engine.py  an engine in that process, wearing ISynthEngine
    native_library.py audio/library/ + the host's boxes, as a Library
    native_sequencer.py  a sequencer box in the SEQ slot (soft-keys, patterns)
  pages/
    synth.py          SYNTH: ENGINE / ENV / FX / LFO; the bend gesture (DDR-052)
    drum.py           DRUM: subclasses SynthPage; ENGINE + ENV are per pad
    track.py          TRACK: one bank of 4 lanes, the tick ladder, tape keys
    mixer.py          MIXER: MIX / EQ / FX / MASTER
    seq.py            SEQ: big step number, note row, slots, seq engine menu
    rack.py           SYNTH/DRUM > FX as the §6 rack
    tempo.py          TEMPO: BPM, LINK, COUNT-IN, SYNC, click (owns Metronome)
    help.py           HELP page + the two hold overlays, per-page/control copy
    input.py          INPUT: live meter, arm, sample, auto-load onto SYNTH
    file.py           USB/FILE: PROJECT, MIDI, USB, SYSTEM (all four built)
    stub.py           NOT IN DEMO — unused since J1, kept for the next new page
  drivers/            display/input/audio interfaces + desktop impls (KEEP)
                      native_backend.py = AudioDriver onto arbutus-audio
                      desktop/midi_in.py = MIDI notes + relative encoder CCs
                      audio_in.py + desktop/sounddevice_input.py = mic capture
  testing/            scripted_input.py, headless_display.py,
                      fake_mic.py (scripted audio input + a synthesized bottle)
  chrome/             desktop chrome (phase 3)
    __init__.py       keymap/screen-rect loading, hit-testing, ChromeView
    keymap.json       key name -> rounded-rect polygon, PHOTO px (5120x2880)
                      + `shapes`: which hotspots are circles (the 4 encoders)
    screen_rect.json  LCD rect + corner radius + px_per_mm, photo px; + `shell`, `shell_radius`, `shell_rim` (the app's drawing, #425)
  tools/
    calibrate_keymap.py  interactive hotspot calibrator
    rebuild_keymap.py    rectify a raw panel shot -> photo + keymap + screen rect
    verify.py         feature-script runner: PNGs + WAV per feature (phase 4)
    edit_server.py    --edit: live palette/font editor served to the browser
    edit_page.html    that editor's page (swatches + glyph grid)
    edit_writeback.py saves live edits back into ui/palette.py and ui/font.py
    contact_sheet.py  light | dark side by side from two `verify.py --out` runs (#334)
  midi.toml           MIDI device / channel / note offset / encoder CCs
  state/              autosaved slots (gitignored); --fresh wipes it
  main.py             pygame window: photo chrome, keyboard + mouse + MIDI
```

The library is **`audio/library/`**, not here: it is content the audio process
loads (§7), and `app/native_library.py` presents it plus the host's box list as
the folder tree the pages browse. `emulator/library/` is gone from the tree;
anything still on your disk under it is your own and untracked.

## No DSP in this folder

**There is no audio path here.** A `numpy` expression that produces samples is
in the wrong repository folder — it belongs in `audio/`, as C++, behind the ABI.
What numpy is still for: framebuffers, the mic's capture ring and trim, and
reading peaks back out of the host to draw them.

The old rule (no per-sample Python loops) and why the Python voice goldens still
exist: `notes/as-built.md` § #1016.

## Run

```bash
source venv/bin/activate
python3 main.py                  # fills the display, LCD exactly 320x240
python3 main.py --width 5120     # 2x: LCD exactly 640x480 (window crops to fit)
python3 main.py --scale 0.55     # explicit photo scale (float)
python3 main.py --zoom           # start with the zoom pane showing
python3 main.py --zoom-only      # phase-1 plain framebuffer window
python3 main.py --zoom-only --scale 4
python3 main.py --fresh          # wipe autosaved slots first
python3 main.py --edit           # + live palette/font editor in the browser
python3 main.py --midi "Keystation"   # overrides midi.toml `device`
python3 main.py --no-midi --no-audio
python3 main.py --offline        # no audio device; `render` is the clock
python3 main.py --no-mic         # skip mic capture entirely
python3 main.py --mic-device 2   # sounddevice input index or name substring
python3 main.py --headless --exit-after 1 --screenshot out.png
python3 main.py --frames 120 --screenshot out.png
python3 main.py --appearance dark # this run only; V toggles and remembers (#334)
```

Prefer **`./run.sh`** from any directory: it finds the venv and builds
`audio/build/host/arbutus-audio` on first run. The audio process is started
before the `App` and a missing binary is **fatal** — there is nothing to fall
back to (stage N1), so `main.py` prints the build command and exits 1.

`--headless` sets `SDL_VIDEODRIVER=dummy` and implies `--offline`, so the run
needs no sound device. `--frames N` / `--exit-after SECONDS` end the pygame loop
cleanly (exit code 0) and `--screenshot PATH` writes the final window.

## Rules for anyone editing this package

- Pixel fonts only. No AA, gradients, rounded corners, alpha, animation.
- Zone heights are constant. Empty strips render blank, never collapsed.
- Every colour comes from `ui/palette.py`.
- No hardcoded engine lists — engines are folders with an `engine.json`.
- No page ever loses state; pages are constructed once and kept.
- Slot n renders in encoder (n mod 4)'s colour. Engines never declare colour.
- Clip edits go through the pure functions in `app/tape.py`; `Tape` only
  snapshots for undo and stores the result. Never mutate a clip list in a page.
- Tape keys follow DDR-001/002: **hold performs, tap (while stopped) commits.**
- **The trust boundary**: the emulator's screens are unverified by Elvin until he plays them (the play
  queue); where its UI code and the spec disagree, the spec wins.
- **The verification harness**: priority is **screenshot diffing**, not
  audio assertions. Audio is comparatively easy to get right; the UI aesthetic has to match
  the creative vision precisely, so the harness must capture pixels. The harness is the
  `verify-arbutus` skill; it runs after every UI change.

## Parity with hardware

Driver interfaces (`drivers/display.py`, `input.py`, `audio.py`) are identical to
`arbutus_os/` — swap desktop drivers for hardware drivers to deploy.

Known gaps: no UART latency simulation, ALSA timing differs, no tearing.

## Troubleshooting

**No audio:** the device is the **audio process's** now, not this one's — start
with the line `main.py` prints at boot (`audio: <firmware> (live|offline)`). If
it says `offline`, something passed `--offline`/`--headless`/`--no-audio`. If
the process will not start at all, build it:
`cmake -S audio -B audio/build -G Ninja && cmake --build audio/build`.
`sounddevice` is only the **mic** now: `python3 -c "import sounddevice as sd;
print(sd.query_devices())"`, then `--mic-device N`.

**macOS SDL crash (SIGABRT / Cocoa_RegisterApp):** run from Terminal.app, or
`SDL_MAC_BACKGROUND_APP=1 python3 main.py`. (pygame-ce is already the
requirement — see § Chrome, "Device pixels".)

**Window looks soft / the LCD grid looks blocky:** the high-DPI path did not
take. Check `venv/bin/python -c "import pygame; print(pygame.version.ver)"`
says *pygame-ce*, and that `chrome/window.py`'s `Window.ratio` is 2.0 on the
laptop panel (1.0 on a 1x external monitor is correct and already exact).
