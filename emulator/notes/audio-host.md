---
tags: [emulator, audio]
status: active
read_when: you touch how the UI talks to the arbutus-audio process (the rack, the modules, live input), or need why this folder makes no sound
index: emulator/CLAUDE.md
---

# The UI and the audio host

> Moved verbatim from `emulator/CLAUDE.md` on 2026-10-08 (#440), headings unchanged, so a `§ <name>` citing that file finds its section here. The index of this folder's notes is `emulator/CLAUDE.md` § Read by task.

## INPUT — live sampling (phase 5)

Arm, make a sound, and it is a playable instrument. The page waits on the
**transient**, not on a keypress: a rolling ring buffer hands back the ~20 ms
before the strike, so the attack survives (DDR-036). `app/mic.py` holds the
state machine and the DSP with no driver knowledge, which is why the whole path
is testable with no audio device.

| Control | Does |
|---|---|
| `1` | ARM / DISARM — waits for a sound over THRESH |
| `2` | RETRY — bin the take and re-arm |
| `3` | → SYNTH, where the new sample is already loaded |
| `4` / ENC4 | MONITOR — input to the speakers. **Off by default: a laptop feeds back.** |
| ENC1-3 | THRESH / LENGTH / GAIN; slots 4-7 empty so SHIFT fine-tunes |

The capture is trimmed, normalised and faded at both ends, written to
`audio/library/synth/sampler/mic-NN.wav` — the library root the host scans —
and loaded onto the SYNTH sampler as its `sample` asset, with no save step.
The waveform on SYNTH's ENGINE screen is drawn from the host's `waveform`
message: the UI never sees the samples.

**MONITOR is the host's.** `monitor {on, gain}` puts the live input into the
master bus (never the tape tap); `MicCapture` carries the flag and no second
copy of the audio. Capture itself is still Python — the mic is a UI-side
device, the way the keyboard is. MONITOR listens to the **jack**, never to
`input_source`: monitoring RESAMPLE would be the speakers playing themselves.

Since stage G the SHIFT row carries **SOURCE** (DDR-063), which is the host's
`input_source` and the same value as `arbutus/live-input`'s SOURCE knob — input
*routing* is that box's, and this page only shows the one field they share.

Threads: `MicCapture.feed()` runs on the input callback and only appends;
trimming, normalising and disk I/O happen in `poll()`, called once per frame
from `App.tick()`. The device is opened when INPUT is first *visited*, never at
boot — macOS asks for microphone permission on the first open.

`audio/library/synth/sampler/bottle.wav` (struck glass: inharmonic modes over a
short noise strike) is the fallback if no mic is available, and gives the
sampler something musical out of the box.

## Native audio becomes the architecture

The C++ audio process (`audio/`, `docs/design/engine-spec.md` §8) is **the**
audio path as of stage N1 (2026-09-17). It is not a mode, a flag or an
experiment: `emulator/` draws and routes keys, `audio/` makes sound, and the
socket between them is the only way across.

```bash
./run.sh                             # the audio process owns the device
./run.sh --offline                   # no device; `render` is the clock (tests)
cmake -S audio -B audio/build -G Ninja && cmake --build audio/build
```

A missing `audio/build/host/arbutus-audio` is **fatal** and prints that build
command; `run.sh` builds it for you on first run. `App(native=None)` raises
rather than limping, because there is nothing left to limp on.

### What it replaced

The left column is gone. It is written down because the shape of the UI still
carries its fingerprints, and because a golden or a comment will send you
looking for it.

| | was (Python, ≤ 2026-09-17) | is |
|---|---|---|
| sound | `engines/*.py` through `Instrument`'s graph | boxes in the audio process |
| the 8 slots | `engine.get_parameters()` | the **descriptor in the host's `state`** — id/name/type/curve/min/max/def/step/unit/labels (§3.5) |
| library | `emulator/library/`, positional files | `audio/library/` + the host's box list, format-1 documents (§7) |
| FX subpage | one `EffectChain`, four knobs | the **rack** (§6) — `pages/rack.py` |
| SEQ | `app/sequencer.py` drove the instrument | `arbutus/endless` in the SEQ slot; 1–4 are its soft-keys |
| ENV MODE | display only | poly / mono / legato / unison, in `PolyHost` |
| ENV SHIFT row | MODE / LEGATO / DETUNE / CURVE, three of them inert | `MODE · GLIDE · DETUNE · BEND` — fields of the host's `env` block (DDR-052, stage N2a) |
| tape clip model | `app/tape.py` | **still `app/tape.py`** — every edit goes down as `set_clips` |
| tape audio | numpy in `Tape.render` | the host's media; the UI reads it back with `clip_peaks` |
| mixer | a working compressor in `app/mixer.py` | UI state pushed as `mixer`; MASTER COMP is the host's. Since F1/F2 the per-track EQ, mute and solo are host state too and `app/mixer.py` holds no copy of them |
| the click | `Metronome._burst` into the mix | `metronome {level, pitch}`, into the master only |
| MONITOR | a monitoring FIFO in `app/mic.py` | `monitor {on, gain}` |
| persistence | `project.json` + `tape.npz` | `project.json` + `tape.json` + `tape-N.wav` per track |
| xrun | — | `CPU UNHAPPY: OVERLOAD` in the dynamic strip (D103, #843) |
| the tape behind the card | — | **C1 step 7:** `tape_late` draws `HARD DRIVE SLOW — TAPE PLAYED SILENCE` in the same slot on the same edge (the host's flag, not a count kept here), `tape_full` draws `CARD STALLED` or `TAPE FULL` by its reason, and FILE › SYSTEM gains a read-only `TAPE BUFFER` row off `state.tape.pool` |
| pages, chrome, look editor, verify.py | | unchanged |

### The modules

```
app/native_audio.py      the process, the socket, the state mirror
app/native_engine.py     an engine wearing ISynthEngine; descriptor -> 8 slots
app/native_library.py    the box list + audio/library/ as a Library; §7 documents
app/native_sequencer.py  a sequencer box in the SEQ slot, soft-keys, patterns
drivers/native_backend.py  the AudioDriver: nothing live, `render` offline
pages/rack.py            the FX subpage as a rack
```

`App.native` is no longer a switch — it is a required collaborator, so there is
nothing to branch on. Where the code used to ask "is there a host?" it now just
sends. `App.tick()` is the one place that pushes per-frame state down
(transport, mixer, click, monitor) and it does so on a **fingerprint**: nothing
is sent while nothing changed, so an idle UI is an idle socket.

Every message that can change the host's document is followed by a `state`
request, so a page always draws what the host actually holds rather than one
frame of stale values (`STATEFUL` in `native_audio.py`).

### The rack, key by key (§6, DDR-038/039/040)

On SYNTH › FX or DRUM › FX:

| Gesture | What happens |
|---|---|
| **SHIFT + ‹ ›** | move between tabs: master (macros), one per engine, then **one empty tab past the end** — that empty tab is how you find out the rack is there (DDR-038) |
| **SHIFT + 3** | the effect selector. `RACKS` loads a whole §6.7 rack; any other folder is an effect box and lands on the current tab (`set_tab_engine`), or on a new one from the empty tab (`add_tab`) |
| **LIFT** (on an engine tab) | copy mode: every param cell gets a ring, the strip reads `TURN A PARAM TO COPY ITS ADDRESS` |
| turn a param in copy mode | lifts `INST.PARAM` into the dynamic strip (DDR-024) |
| **DROP** (on the master tab) | paste mode: `TURN A KNOB TO PASTE` |
| turn a macro slot in paste mode | `bind`. Lift another address and drop it on the same slot to **multi-assign** |
| **LIFT** then **DROP**, nothing turned | cancel |
| LIFT, then **SHIFT + 3** to another box | the lift follows the tab's new name when the new box has the same parameter (`DELAY_1.MIX`); when it has not, the lift is cancelled and the strip says `LIFT CANCELLED: <TAB> HAS NO <PARAM>`; scrolling on, in the same browse, to a box that has it brings it back (#711). The MOD rack does the same for a lifted output or route |
| **SHIFT + DROP** (master tab) | open / close the **range popup** for the macro you last turned |
| in the popup | ENC1–4 are **LO · HI · CURVE · DEPTH**; turning LO or HI parks the macro at that end so the target *is* at the value you are setting (DDR-040); a red marker shows where the macro currently lands; ‹ › steps through the targets of a multi-assigned macro |
| **HELP** + LIFT / DROP / ‹ › / 3 | explains the mode (DDR-025) |

The host owns all of it. Every edit is one message and the page re-renders from
the document that comes back; nothing in `pages/rack.py` computes a macro, a
binding or a tab.

### What stays Python

**The tape's clip model.** Lift, drop, slice, merge and undo are the pure
functions in `app/tape.py`; the resulting list is pushed down with `set_clips`
after every edit and a finished take arrives as the host's **`take` push**
(stage E1) — not by diffing the host's clip list, because since overdub became
the only record mode (DDR-056) a take *absorbs* what it covered and the list can
come back the length it went in. `NativeAudio.takes` queues them. The
audio lives in the host's medium — **nothing in §8.2 hands samples back up** —
so a clip carries geometry, and `clip_peaks` is how TRACK draws what is in it.
A clip that is *moved* re-reads the medium at its old address: that is the
`src_offset` field on `app/tape.Clip`, propagated through every pure op.

**`TrackPage._peaks` is keyed on geometry, not on the view, and #162 measured
what that costs.** Zoomed out, `_clip_wave` asks for the **whole clip** —
`a0 = 0`, `a1 = len`, `bins = ceil(len / REC_PEAK_BUCKET)` — so the key does not
change when the view scrolls, and a **jump is free**: 22–24 ms and no message at
all on the bench CM4 with the tape on the card. What is not free is a key the
cache has not seen, and that is the first draw of a clip **and every edit** — a
SLICE, a trim or a move makes new geometry. Behind a `--tape-dir` (C1) that
cost **~1 s a clip**, four seconds for the four lanes TRACK shows at once.

**#163 fixed it in two places, 2026-09-25, and the UI's half is one line.**
`_clip_wave`'s zoomed-out branch bounds `bins` by **`span`** — the clip's own
width in pixels at this zoom — so it asks for one bin per pixel instead of
`ceil(len / 256)`, which on the bench CM4 was 33,750 bins into a ~300 px lane, a
hundred times what it draws and ~60 ms of JSON a frame cannot afford. `span` and
**not** the visible `x1c - x0c`: the key has to stay geometry, or a scroll would
start asking again and #162's *a jump is free* would go with it.

Two things worth knowing about it. **No shot moved** — 0 differing pixels across
the 30 `validation/ui/native` and 12 `validation/ui/phase2a` screenshots, with
the cap and without — so the bins it stopped asking for really were never drawn.
And the cap is what makes the host's half reachable at all: the peaks sidecar
answers only at or below its own resolution (one bin per 3072 frames), so a
question for `ceil(len / 256)` bins is finer than the sidecar and would always
have fallen through to the audio. The reply now carries `source`
(`sidecar`/`audio`) and `state.tape.io.peaks` counts hits and misses, so a bench
cell can see which path answered. `audio/README.md` § A TRACK redraw with the
tape on the card; `audio/CLAUDE.md` § Defaults has the sidecar's row.

Keeping the algebra up here is deliberate. It is the part a player feels (an
undo that groups, a drop that absorbs what it covered), it is all integer
arithmetic on a list, and it is the one thing in the instrument that is easier
to get right in a language with a test suite this cheap.

**Also Python:** the mic's capture and trim (a UI-side device, like the
keyboard), the whole UI, and the LFO subpage — inert until the MOD rack lands
in stage N3. Its values are kept and saved so a patch written now still carries
them; nothing sounds different when they move, and it must not crash.

### Tests

```bash
venv/bin/python -m unittest discover -s tests             # 386, all native
venv/bin/python -m unittest tests.test_native_smoke       # the offline checks
venv/bin/python tools/verify.py native-rack native-synth  # screenshots
```

Screenshots from the `native-*` features go to `validation/ui/native-*/` and are
**not promoted** — only Elfy promotes. `native: true` in a feature script is now
a no-op, kept so the existing scripts still parse: every feature runs against
the audio process.
