# audio/library — the native library (format 1)

The content half of `audio/`. Boxes (compiled engines) live in
`audio/build/boxes/`; this tree is the **documents** — patches, kits and racks,
every one of them `"format": 1` per `docs/design/engine-spec.md` §7.

It was **regenerated, not migrated** (§11) from the emulator's old positional
`.patch` files, which are gone: this is the only library now (stage N1).

```
synth/<box>/<name>.patch     §7.2 patch: engine + env + fx rack + octave
synth/sampler/*.wav          sample assets, referenced library-relative
drum/drum/<kit>/kit.json     §7.3 kit: an explicit pad -> file map
drum/drum/<kit>/*.wav        the kit's own samples
fx/racks/<name>.rack         §6.7 rack: tabs, macros, sidechain
seq/<box>/<name>.seq         a sequencer pattern (DDR-037); init.seq is empty
mod/racks/<name>.rack        a §6.7 MOD rack; `mod/<box>/` is a browser row
mod/<box>/<name>.stepmod     arbutus/stepmod's step values (#60) — content a
                             patch names, and no browser lists it
projects/<date>/<name>/      a saved session (stage J1) — see § Projects
settings.json                the device's own settings (stage J2) — see § Settings
stock.json                   what shipped in the image, so DDR-055 knows what
                             does not lift — see § Stock
.trash/                      a browser LIFT that was never dropped (DDR-055)
.originals/                  unconverted masters, kept beside what they made
```

**A dot-prefixed folder is not content, and neither is a dot-prefixed file.**
Neither scanner descends into a dot folder — the emulator's browser skips them
(`native_library._from_tree`) and the host's box index does too
(`audio/hostlib/library.cpp`, stage J2) — and since the Q3 audit (#31,
2026-09-25) the browser's file listings skip a `._name.patch` too: a Mac writing
to the exported `/data` leaves one beside every document, and each was a row
that did nothing when picked. That is what makes
`.trash/` a real soft delete rather than a folder full of things still on
offer. The root `.gitignore` already ignores `.trash/` and `.originals/`.

The folder name under a category is the **box name**; the author is `arbutus`,
so `synth/osc/` is `arbutus/osc`. `emulator/app/native_library.py` relies on
that and on nothing else, which is why there is no index file to keep in sync.

**This layout is the one home of the filing rule.** A device reports how it applies
here — which box folders the browser lists under `synth/` and `drum/`, which take a
`.wav`, which sequencers take a `.seq` — as `filing` on its root `GET /library`
(`device-os-spec.md` § 9.17, #429), so a client asks rather than keeping a copy.

## Where the numbers come from

Every parameter value is **normalised 0..1** (§3.2, `audio/CLAUDE.md`), so a
document never has to know an engine's units. None of them were typed by hand:
they were computed from the box manifests with the same `min`/`max`/`curve` the
engine declares.

## Kits

`audio/tools/mkkit.py` reads an emulator kit folder in **sorted filename
order** — the old implicit mapping — and writes it down as an explicit `pads`
map, so renaming a file can never silently re-map a kit again.

Four kits here are TR-707, TR-909, South-808 and lofi-2, sliced from
op1.fun OP-1 patches by `emulator/tools/op1_drum_import.py`. **The WAVs are
copied, not referenced**: a kit is a thing you send someone (§10), and a
`kit.json` whose pads point back into `emulator/` is not sendable. That costs
~3 MB of duplication; `mkkit.py --reference` is the other choice and writes
relative paths instead.

The fifth, **BARK** (`drum/drum/bark/`), is ours: `audio/tools/mkstockkit.py`
synthesizes its twelve sounds (kick, short kick, snare, rim, clap, closed and
open hat, three toms, cowbell, crash) from sines, squares and seeded noise, so
the public export, which holds back the four op1.fun kits (no stated licence,
#1239), still has a sample kit on DRUM (#1295). Its folder name is lowercase
so it sorts after `South_808`: a fresh DRUM page here still loads South-808,
and in the export BARK is the only kit and loads first. Run the tool again
after changing a sound, then `mkstock.py`.

**Pad shape (drum v2, `engine-spec.md` §15.3).** A pad is
`{"file": "kick.wav", "params": {"start": 0.1, "choke": 0.25}}` — per-pad
values, normalised, keyed by id, absent = default. The four op1.fun kits are still
in the v1 shape, `"0": "01.wav"`, and were **not regenerated**: the host and
the engine read both, and a v1 pad is that file at defaults, which is the v1
sound. Both shapes are `"format": 1`. The host migrates a loaded kit to the v2
shape in `state`, so the next save from a UI writes v2. `mkkit.py` writes v2
(`--v1-shape` for the old one).

## Patterns (`seq/<box>/<name>.seq`)

A pattern is **content, not parameters** (DDR-037), so it is a file the box
opens through `load_asset(h, "pattern", path)` — not a list of floats the
protocol carries. The document:

```json
{ "format": 1, "kind": "pattern", "name": "penta 7", "root": 57,
  "named_params": {"DIV": "1/8", "SWING": 50, "DIR": "fwd"},
  "pattern": [[57], [64], "tie", [], [67]] }
```

| Field | Meaning |
|---|---|
| `pattern` | the steps, in order. A **list of MIDI notes** is a step (several = a chord), `[]` is a **rest**, and the string `"tie"` holds the step before it instead of retriggering. At most 99 steps, 8 notes a step. |
| `root` | the pitch playback transposes *from* (`arbutus/endless` lands it on whatever key you hold). Absent = the first note in the file. `arbutus/pattern` is at absolute pitch and ignores it. |
| `named_params` | optional, and **not** part of the asset: the UI applies these to the SEQ row by the parameter's displayed name, so a saved riff comes back at the division it was written at. An enum takes its label (`"1/8"`), anything else a number in the box's own units. A name the loaded box does not have is skipped. |
| `name`, `comment` | filing and prose. Nothing reads them but the browser. |

`init.seq` is the empty pattern and is every sequencer folder's first item — a
fresh SEQ slot must not arrive with someone else's tune in it. Boxes that
declare the `pattern` asset: `arbutus/endless`, `arbutus/pattern`. `arbutus/arp`
and `arbutus/euclid` make their pattern out of what you hold and out of two
numbers respectively, so they take no file and their folder shows `use`.

The UI writes one of these two ways (stage H2): promoting a patch on SYNTH/DRUM
saves the sequencer's steps beside it as `seq/<box>/<slug>.seq` and points the
patch's `seq.assets.pattern` at that file; and a pattern that is only *entered*
rides to the box as a scratch file under `$TMPDIR` — nothing in the library is
written until you save. That scratch file lasts exactly as long as the process
that made it: it is removed when the UI exits (#105), so a riff that was never
saved does not survive a restart, and does not sit in the device's RAM either.

## Step patterns (`mod/<box>/<name>.stepmod`)

**Defined here for the same reason § Projects is**: no document in
`v1-spec-package.md` gives the file a home. `arbutus/stepmod` declares the
`pattern` asset and reads a document of its own shape, not § Patterns':

```json
{ "format": 1, "kind": "stepmod", "name": "BIG CAT SYNTH STEPMOD",
  "values": [1.0, -1.0, 0.5, -0.5] }
```

1–16 numbers, each clamped to ±1. The box refuses anything else (wrong `kind`
or `format`, empty, more than 16, a non-number) and the host then refuses the
load with `ENGINE BROKEN`.

The UI writes one the same two ways a SEQ pattern is written (#60). A pattern
that is only *entered* — the STEP/VALUE encoders on the MOD tab
(`modulation-spec.md` §3.2.1) — rides to the box on a scratch file under
`$TMPDIR` named `arbutus-stepmod-<instrument>-<tab>-<pid>.json`, which is
removed when the UI exits and not before (#105, `emulator/app/scratch.py`).
Promoting a patch on SYNTH/DRUM, or saving a project, writes it into
`mod/<box>/<slug>.stepmod` first and repoints the tab, so the `mod` block a
patch carries by value names a file that will still be there tomorrow.

`mod/<box>/` is the box's own folder in the MOD browser. A `.stepmod` file in
it is **not** listed: `NativeFolder.rescan_items` keeps `.patch`, `.rack` and
`.seq` only, and that is deliberate — a step pattern is content a patch names,
not a modulator you can drop on a tab. The folder keeps showing `use`.

## Projects (`projects/<date>/<name>/`)

**Defined here because no document in `v1-spec-package.md` defines it.**
`screen-sketches.md` § FILE says what a project *holds* ("tape, mixer, all six
slots on every page, tempo") and DDR-019/020 say what the on-device page may
do with one; neither says how it is written down. Stage J1 needed a format, so
this is it, and that is a deviation recorded in `emulator/notes/as-built.md` § J1.

A project is a **folder**, because the media are WAVs. **Since D97 (#817) there is
no SAVE**: NEW makes the folder, and the instrument's autosave keeps `project.json` in it
current (`device-os-spec.md` § 7.3).

```
projects/2026-09-18/PROJ_0001/project.json    the document below
                              tape-1.wav      track 1's medium (tape_save)
                              tape-1.peaks    its waveform bins (#163), if any
                              tape-8.wav      ... only for a track with clips
```

**A medium is written while it plays, and carries a 4 KiB header** (C1, a host
given `--tape-dir`). `tape-N.wav` is a stereo float32 WAV either way, but a medium
the tape I/O thread owns is written **in place, one 1.024 s range at a time**, as
the take records — which is what lets SAVE never copy audio (DDR-072,
`device-os-spec.md` § 3.3). Its header is 4 KiB rather than the 44 bytes
`tape_save` writes: `RIFF`/`WAVE`, `fmt `, a `fact` frame count, then a `JUNK` pad
so the `data` payload begins at byte **4096** and every range is a whole number of
pages. It is rewritten on every commit and **lags the audio by one range**, so a
power cut can never leave a header claiming audio the card did not take — the
price is about two seconds of the take on a cut, and the host repairs the file on
open from the header's frame count, never from the file's length. **Every reader
still opens it**: the data offset comes from the chunk walk (a 44-byte file from
today's `tape_save` opens unchanged), `JUNK` is skipped by construction, and a
medium the instrument has closed — take end, `tape_save`, USB storage mode
unmounting `/data` — is exact, with no lag, so a Mac never sees a live header.
The numbers and the reasoning are `docs/design/tape-streaming-design.md` § 3.1
and § 3.5.

`tape-N.peaks` is a **cache, not content**: 16 min/max pairs for every 1.024 s
range of the medium (128 B a range, ~60 KB for an eight-minute track), so TRACK
can draw a clip's shape without reading the audio off the card. It appears beside
a medium the tape I/O thread wrote (`--tape-dir`, C1), it is not synced, and it
is discarded on open unless the frame count in it matches the medium's own
header. **Deleting one costs a slow first draw and nothing else**, and a copy of
a project that leaves it behind is still a complete project.

The date folder is also the left pane of FILE › LOAD's two-pane browser, and
`PROJ_0001` is DDR-020's auto-name — it never prompts, and renaming is the
companion app's job.

```json
{ "format": 1, "kind": "project", "name": "PROJ_0001", "date": "2026-09-18",
  "bpm": 96.0, "base_bpm": 120.0, "link": true,
  "metronome": {"level": 0.4, "pitch": 1600.0},
  "page": "SYNTH", "active_instrument": "SYNTH",
  "instruments": {"SYNTH": {"patch": <§7.2 patch>, "macros": [0, …],
                            "kit": <§7.3 kit>},
                  "DRUM":  { … }},
  "master": {"fx": <§6.7 rack>, "macros": [0, …]},
  "slots": {"SYNTH": {"active": "A", "slots": {"A": { … }, …}},
            "DRUM": { … }, "SEQ": { … }},
  "pages": {"TEMPO": { … }, "TRACK": { … }, "MIXER": { … }, "FILE": { … }},
  "mixer": {"levels": [ …8 ], "pans": [ …8 ], "master": 0.8, "comp": 0.0,
            "comp_rel": 0.12, "drive": 0.0, "drive_tone": 0.5, "bal": 0.0},
  "tape": {"ui": {"selected": 0, "loop_in": 0, "loop_out": 0, "loop_on": false,
                  "speed": 1.0},
           "tracks": [{"clips": [{"start": 0, "len": 24000, "gain": 1.0,
                                  "src_offset": 0, "src_track": 0}],
                       "medium": "tape-1.wav", "medium_frames": 24000,
                       "gain": 1.0, "pan": 0.0, "mute": false, "solo": false,
                       "eq": {"low": 0, "mid": 0, "high": 0, "mid_f": 1000,
                              "mid_q": 0.7, "hp": 20, "lp": 20000}}, … x8]}}
```

| Field | Why it is there |
|---|---|
| `base_bpm` | the tempo the media were written at (DDR-059). **A project load is the only thing allowed to move it** — nothing else in the instrument sends it. |
| `instruments.*.patch` | a whole §7.2 patch: engine, parameters, assets, env, FX rack, MOD rack. |
| `instruments.*.macros` | macro **positions**. A §6.7 document holds the bindings, not where the knob happens to be — and where it happens to be is the sound. |
| `instruments.*.kit` | a patch does not carry the kit (the host says so), so DRUM's pad map and per-pad values ride here. |
| `master` | the master bus is a third rack (§15.5), belonging to no instrument. |
| `slots` | the six memory slots per slot page, exactly as `state/<page>.json` holds them. The **active** slot's record is the instrument that is loaded, so loading a project is: restore the slots, then put back the kit and the macros a patch cannot carry. |
| `tape.tracks[i].medium` | the filename beside the document, or `null` for a track with no clips. On load the medium goes down **first**, then `set_clips` — a clip already playing must not hear the medium change under it. |
| `tape.tracks[i]` strip | `gain`/`pan`/`mute`/`solo`/`eq` are the **host's** half of the mixer; `mixer` above is the UI's. A project saves both. |

**An entered SEQ pattern is promoted on save**, and so is an entered stepmod
pattern (§ Step patterns). A pattern that was played in
rather than loaded rides to the box on a scratch file under `$TMPDIR`; saving
a project writes it into `seq/<box>/<slug>.seq` first (the same thing
promoting a patch does) and the slot record then names a file that will still
be there tomorrow.

## Settings (`settings.json`)

**Defined here for the same reason § Projects is**: no document in
`v1-spec-package.md` specifies a settings file. `screen-sketches.md` § FILE
lists what FILE › MIDI / USB / SYSTEM hold and never says where they are
written down. Stage J2 needed a format, so this is it, and the deviation is
recorded in `emulator/notes/as-built.md` § J2.

A setting is the **device**, not the song: it is in neither a patch nor a
project, so a project loaded onto a differently-configured instrument does not
bring its MIDI channel with it. It lives at the library root because that is
the one directory that belongs to the instrument rather than to a checkout —
`emulator/state/` is per-install working state and `--fresh` wipes it.

```json
{
 "format": 1,
 "kind": "settings",
 "midi":   {"in_ch": -1, "out_ch": 1, "clock_in": false, "clock_out": false},
 "usb":    {"mode": "OFF", "charge": true},
 "system": {"vel_curve": "LINEAR", "power_off": 0, "brightness": 80,
            "appearance": "light"}
}
```

| Field | Means |
|---|---|
| `midi.in_ch` | MIDI channel **0-based**, `-1` = omni. The page shows `OMNI` and `1`–`16` |
| `midi.out_ch` | 1-based, stored only: there is no MIDI output on the emulator |
| `midi.clock_in` | follow an incoming MIDI clock; TEMPO's SYNC `midi` then follows it |
| `midi.clock_out` | stored only, same reason as `out_ch` |
| `usb.mode` | `OFF` · `STORAGE` · `MIDI`. Entering `STORAGE` force-saves a project first (DDR-021) |
| `usb.charge` | stored only — the charger is the device's |
| `system.vel_curve` | `LINEAR` · `SOFT` · `HARD` · `FIXED`, applied to **incoming MIDI** velocity (the device's own keys have none) |
| `system.power_off` | idle minutes before the device powers down; `0` = never |
| `system.brightness` | 10–100 %, stored (there is no backlight on a laptop) |
| `system.appearance` | `light` · `dark`: the screen's mode as it last was (#334). The paired phone sets it (`audio/host/PROTOCOL.md` § The phone's appearance), so with no phone a boot keeps it; anything else reads as `light`. FILE › SYSTEM's RESET leaves it as it is (#693): the screen follows the phone, not the box |

An unknown key is dropped and a missing one takes its default, so an older
document still loads. A broken or absent file **is** the defaults — a settings
file is never a reason not to boot.

## Stock (`stock.json`)

DDR-055: "stock content does not lift." Nothing on disk distinguishes a patch
the player promoted from one that came in the image — both are a `.patch`
written by the same code — so the shipped list is written down.

```json
{"format": 1, "kind": "stock", "items": ["synth/osc/init.patch", "drum/drum/TR_909", …]}
```

An item is one row of a browser's right-hand pane: a `.patch`, `.rack`, `.seq`
or `.wav` inside a box folder, or a kit **folder**. Box folders are not items
(you cannot take `SAMPLER` out of the synth list) and the files inside a kit
are not (they move with it).

**`python3 audio/tools/mkstock.py` writes it; `--check` compares it to the
tree.** Add stock content to this library and rerun the tool, or the new item
will be liftable. No manifest at all means nothing is stock, which is the right
answer for a library someone assembled themselves.

## Racks

| File | What it is |
|---|---|
| `low-pass.rack` | one `arbutus/biquad`. One engine, so the FX page shows no tab chrome (DDR-038). |
| `wet-iron.rack` | biquad → reverb, macros **TONE** (cutoff 300 Hz … 14 kHz) and **SPACE** (reverb size *and* mix together). |
| `colourbass.rack` | two `arbutus/satcomb` in series into a reverb (a parallel span until #273 cut them), one **GRIT** macro driving four targets at once — the multi-assign of DDR-039, with `exp` curves on the drives. |
| `duck.rack` | reverb → `arbutus/duck` with `"sidechain": "tape:1"` — the §6.6 cross-slot input. |
| `tape-echo.rack` | one `arbutus/delay` at a dotted eighth, with the loop filtered, saturated and wobbling. Macros **FEED** (feedback 0.15 … 0.9, `exp`) and **AGE** (WOW up while TONE darkens — one knob, two directions). |
| `ensemble.rack` | one `arbutus/chorus` at 18 ms and three voices, 150° apart. Macro **WIDTH** drives DEPTH and MIX together. |
| `handheld.rack` | `arbutus/crusher` → `arbutus/eq3`: 7 bits at 4.2 kHz through a mid-forward, top-and-bottom-less EQ — a cheap sampler and the box it came in. Macro **CHEAP** takes BITS down as RATE follows. |
| `stack.rack` | `arbutus/drive` in FUZZ → `arbutus/eq3` as the cabinet. Macros **GAIN** (`exp` on DRIVE) and **SIZE** (the EQ's mid and low together). |
| `voice-eq.rack` | one `arbutus/eq3`, both shelves moved off the defaults. Macro **PRESENCE** on MID and HIGH. |

Stage-I2 racks (2026-09-18) were written the same way the stage-1 ones were:
the values are typed in the **boxes' own units** and converted through each
engine's declared `min`/`max`/`curve` from its generated `engine.json`, so a
rack file never contains a number anyone estimated by eye.

## `parity.patch`

`synth/osc/parity.patch` is the only document here chosen by a test rather than
by ear: osc → biquad → reverb at values the Python path computed identically,
which was the §11 parity gate. That test (`test_native_parity.py`) went with
the Python engines at stage N1 — there is one audio path now — and the patch
stays as an ordinary stock sound. Its `level` is 0.5 because the Python
oscillator divided its four-voice mix by four and `PolyHost` does not normalise
at all.

