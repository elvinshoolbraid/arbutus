# arbutus-audio control protocol — `proto 1`

This is the reference for the message set in `docs/design/engine-spec.md` §8.2.
It is **public API from message one**: a field may be added, never renamed and
never repurposed. A breaking change bumps `proto` and the host refuses the old
one at `hello`.

Transport: **newline-delimited JSON over a Unix domain socket**, and since D2
(#23) the same message set over **TCP** for the companion app — a transport
change, not a message change.

```
arbutus-audio --socket /tmp/arbutus-audio.sock --library <box root>
arbutus-audio --tcp-port 7401 --token-file /opt/arbutus/state/token   # + the above
```

Default socket: `$TMPDIR/arbutus-audio.sock`, else `/tmp/arbutus-audio.sock`.
Several clients may connect at once; **pushes go to all of them** (to every TCP
client that has finished the handshake below). **A client must keep reading.**
The host never waits for one (P9, #89: a blocking write to a full socket used
to stall every client), so what a client has not read piles up on its side of
the host, and past **8 MiB** waiting the host closes that connection.

### TCP (device-os-spec.md § 9.4)

Port **7401** on the device (DDR-065; the HTTP file server is 7402 and both are
in the mDNS TXT record, so nothing hard-codes either). Four rules, and nothing
else differs from the Unix socket:

1. **The listener is bound only when a token exists.** Without `--token-file`,
   or with an empty one, `--tcp-port` binds nothing: an instrument nobody has
   paired with has no open port rather than one that answers and refuses.
2. **`hello` must carry `token`** before anything else. Any other message
   first, or a wrong token, is refused `unauthorized` (`NOT PAIRED`) and the
   connection is **closed after three refusals**. Until the handshake lands the
   client is told nothing — no `state`, no pushes. The Unix socket needs no
   token and never sends `unauthorized`.
3. **`shutdown` and `render` are refused** `bad_request` over TCP. They are the
   local instrument's: a phone that could stop the audio process, or drive the
   offline renderer, could silence the instrument mid-set. So are **`ui_elements`
   and `ui_action`** (§ The screen and the player's actions): they are the UI
   saying what it shows, and a phone that could send them could point Learn's
   arrows at a screen that is not there.
4. **`meters` is capped at 10 Hz** over TCP, whatever `hz` asked for. It is a
   clamp and not a refusal, and the `meters` reply carries the accepted `hz`
   back, which is the only place a client can learn the cap.

The token is one 32-byte value per device (DDR-065), read from the file at
start; `FORGET PHONES` regenerates it and restarts the process.

---

## Shape of every message

| Field | Meaning |
|---|---|
| `type` | required, the message name |
| `req` | optional, any JSON value. Echoed in the reply so a client can match it. **Not `id`** — `id` is an engine/patch/kit identifier. |
| `instrument` | `"synth"` or `"drum"`; omitted means the active one |

Every client message gets **exactly one reply**: `ok`, `error`, or a named reply
(`hello`, `state`, `rack`). Replies carry `for` (the message they answer) and
`req` when the request had one.

```json
{"type":"ok","for":"set_param","req":7}
{"type":"error","code":"needs_engine","message":"NEEDS ENGINE arbutus/formant","detail":"arbutus/formant","for":"load_rack"}
```

`message` is the §7.4 line the player sees, verbatim. `code` is the machine
slug: `needs_engine`, `needs_version`, `needs_firmware_update`,
`no_build_for_this_device`, `engine_broken`, `bad_request`, `not_found`,
`bad_json`, `bake_failed`, `write_failed`, `mod_rack_full`, `tape_full`,
`last_engine`, `busy`, `unauthorized` (TCP only). `detail` is for the author, not the
screen.

**`busy`** (`BUSY`, #597): the ring that carries commands to the audio thread
has too little room for the message, so it is refused before anything
changes; nothing in the state document moved. On a device that means the
audio thread has stalled; `--offline`, that ~960 edits came with no `render`
between them (a `render` drains the ring). `hello`, `state`, `shutdown`,
`library`, `rescan`, `waveform`, `clip_peaks`, `tape_dir`, `meters` and
`render` push nothing to the audio thread and are never refused `busy`.
An `ok` therefore always means the audio thread has the change queued.

**Malformed JSON** gets `{"type":"error","code":"bad_json"}` and the connection
stays open. A dropped connection is never how a client learns it made a mistake.
**A field of the wrong type** (`{"type":"hello","proto":"1"}`) is either read as
its default, as many fields always were, or refused `bad_request` with the message
`BAD MESSAGE` (#596); either way it is one reply and the connection stays open.
**A line has a ceiling**: past **8 MiB** with no newline (**64 KiB** for a TCP
client that has not yet given the token) the host replies `bad_request`
`MESSAGE TOO LONG` and closes that connection, the one case where it does.

---

## Handshake

### `hello` → `hello`

```json
{"type":"hello","proto":1,"abi":2,"firmware":"arbutus-os 0.3"}
{"type":"hello","proto":1,"abi":2,"box_abi":2,"firmware":"arbutus-audio 0.2.0","sample_rate":48000,"block":128,"offline":false,"motion_sensor":false}
```

Over TCP the request additionally carries **`token`** (a string, § TCP above);
on the Unix socket the field is ignored if present. The reply is the same
either way.

A `proto` the host does not serve is refused with `NEEDS FIRMWARE UPDATE`
(§7.4). Refusal does not close the connection — the client may try again with
the right numbers.

**`abi` at hello is the client's, not the boxes'.** A client never loads a box;
the ABI it meets is the descriptor JSON in `state`, and ABI 2's descriptor is
ABI 1's plus optional fields. The host accepts any `abi` from
**`kMinClientAbi` to `ARB_ABI`** and answers with the one the client asked for.
`kMinClientAbi` is **2** since the emulator shim moved to ABI 2 (2026-09-16), so
today only 2 is accepted; `box_abi` in the
reply is the ABI every box must be built against, and boxes are still held to
exactly that one (§7.4). A client that names an `abi` outside that range is
refused. `box_abi` is an added field: a client that does not read it loses
nothing.

**`motion_sensor`** (added field, #116) is `true` when the host has a motion
sensor open and answering — `--imu PATH` found an LSM6DSO and its last
transfer succeeded. It is `false` on a Mac, on the IO board, on a board with
no sensor fitted, and on a board whose sensor has stopped answering: those are
one case on screen, because `device-os-spec.md` § 6.5 gives "no WHO_AM_I" and
"a bus error mid-run" the same answer — `arbutus/motion` says `NO SENSOR` and
FILE › SYSTEM hides `KEY VEL`. **The same field is in the state document**,
and that is the copy to follow: the handshake's is the value at connect, the
state document's changes when the sensor does. (The two duplicate each other
the way `sample_rate`, `block` and `offline` already do.)

---

## State

### `state` → the state document

Requested, and **pushed to every client after any edit**. The pushed document
and the requested one are the same object; a requested one additionally echoes
`req`.

**After an edit, not after each one of a burst** (#74): the push goes out once
no request has arrived for **8 ms**, or **50 ms** after the document first went
dirty while requests keep coming. A reply never waits for it. Building the
document costs ~2–3 ms on the CM4 and parsing it ~1.75 ms, and a STOP or a SAVE
is five to ten requests in a row, so one push per edit made each of them ~6 ms.
A client that needs the document *now* asks for `state`. With `--offline`, a
`render` is the clock and lets a pending push go in its own turn.

```json
{ "type":"state", "format":1, "proto":1, "abi":2, "firmware":"...", "offline":false,
  "sample_rate":48000, "block":128, "max_block":128,
  "tempo":120.0, "ppqn":24, "playing":false, "song_frame":0,
  "active":"synth",
  "instruments": { "synth": <instrument>, "drum": <instrument> },
  "tape": { "capacity_frames":23040000, "seconds":480.0, "armed":-1, "recording":false,
            "position":0, "speed":1.0, "reverse":false,
            "link": {"on":true, "base_bpm":120.0, "factor":1.0},
            "loop": {"in":0, "out":0, "on":false},
            "filter": {"on":false, "cutoff":2000.0, "mode":"LP"},
            "io": null,
            "tracks":[ {"clips":[{"start_frame":0,"len":24000,"src_offset":0,"gain":1.0,
                                  "src_track":0}],
                        "gain":1.0, "pan":0.0, "mute":false, "solo":false,
                        "eq": {"low":0,"mid":0,"high":0,"mid_f":1000,"mid_q":0.7,
                               "hp":20,"lp":20000},
                        "used_frames":24000, "dead_frames":0}, ... x8 ] },
  "mixer": { "master":1.0, "limiter":false, "comp":0.0, "comp_rel":0.12,
             "comp_times":"block", "drive":0.0, "drive_tone":0.5, "bal":0.0,
             "fx": <rack, exactly engine-spec §6.7>, "tabs":[ <slot>, ... ],
             "macros":[0,0,0,0,0,0,0,0] },
  "metronome": { "level":0.0, "pitch":1600.0 },
  "monitor": { "on":false, "gain":1.0 },
  "input": { "source":"mic" },
  "motion_sensor": false,
  "xrun": { "count":0, "window_s":2.0, "total":0, "flag":false } }
```

`<instrument>`:

```json
{ "name":"BIG-CAT", "octave":0,
  "engine": <slot|null>, "seq": <slot|null>, "seq_pattern": {},
  "env": {"a":0.012,"d":0.2,"s":0.8,"r":0.34,"mode":"poly",
          "glide":0, "detune":12, "bend_range":2},
  "lfo": {}, "kit": null | <kit, v2 pad shape — see load_kit>,
  "fx": <rack, exactly engine-spec §6.7>,
  "tabs": [ <slot>, ... ],
  "macros": [0,0,0,0,0,0,0,0],
  "mod": <MOD rack, exactly modulation-spec §11>,
  "mod_tabs": [ <slot>, ... ],
  "host": {"level":1.0, "pan":0.5, "pitch":0.5} }
```

`mod`, `mod_tabs` and `host` were added with the MOD rack (see
[MOD rack](#mod-rack-modulation-spec)); a client that predates them ignores
them. `mod_tabs[i]` is index-aligned with `mod.tabs[i]`, as `tabs` is with
`fx.tabs`.

`<slot>` is one loaded engine. `params` is keyed by parameter **id**, never by
index (§7.1), and every value is **normalised 0..1** (§3.2). `desc` is the
`engine.json` snapshot the UI needs to draw the 8 slots without loading a
binary (§3.8).

```json
{ "id":"arbutus/biquad", "version":"1.0.0", "min_version":"",
  "params": {"cutoff":0.8474641, "q":0.05263158},
  "assets": {},
  "desc": {"caps":["audio_in","audio_out"], "honours_env":false, "latency_frames":0,
           "params":[...], "softkeys":[], "assets":[], "outputs":[], "description":"..."} }
```

`state.abi` is `ARB_ABI`, the box ABI (2).

`assets` on the slot is what was loaded, with every path already resolved to an
absolute one. `desc.assets` is what the engine *accepts*.

ABI 2 added to `desc`, all present for every engine: `outputs` — a modulator's
`[{id, name, bipolar}]`, `[]` for every other role — and `per_voice` /
`per_note` booleans on each entry of `desc.params` (`engine-spec.md` §15.1).
`caps` may now include `"modulator"`.

### `display` — the engine's own readout (optional)

```json
{ "id":"arbtest/abi2", ..., "params": {"level":0.5, "rate":0.5},
  "display": {"rate":"STOP"}, "desc": {...} }
```

When a box implements ABI 2's `format_param`, the slot carries `display`: the
string the engine wants drawn for a parameter's **current value in `params`**
(`1/8`, `STOP`, `3.2 Hz` — `modulation-spec.md` §3.3), keyed by parameter id
like `params`. Only parameters the engine chose to label appear; draw the rest
from `desc` as before. **The key is absent** when the box has no
`format_param` or labels nothing, so a client that predates it sees the slot it
always saw. It is recomputed for every `state`, so it follows `set_param`.
Values are at most 31 bytes.

`tabs[i]` is index-aligned with `fx.tabs[i]`. `macros[n]` is the current
position of macro slot *n*; the §6.7 document holds the bindings, not the
positions, so a saved rack does not carry where a knob happened to be.

---

## Loading

Every load either succeeds completely or **loads nothing** and says why (§7.4).
A refused load leaves the previous graph running.

| Message | Payload | Reply |
|---|---|---|
| `load_engine` | `{instrument?, slot:"engine"\|"seq", id, min_version?, assets?, dir?}` | `ok` |
| `unload_engine` | `{instrument?, slot}` | `ok` |
| `load_rack` | `{instrument?, rack:<§6.7>}` | `ok` + `rack` |
| `load_patch` | `{instrument?, patch:<§7.2>}` | `ok` |
| `load_kit` | `{instrument?, kit:<§7.3>}` | `ok` + `kit_path` |
| `library` | `{}` | `ok` + `engines:[{id,version,dir}]` |
| `library` | `{op, path, name?, to?}` | `ok` + `op`, `path`, `engines` |
| `rescan` | `{}` | `ok` — re-reads the library roots |

**`rescan` is what a file op sends** (DDR-055, stage J2): the UI owns the
library tree and moves real files and folders inside a `--library` root, and
this is how the host's own box index is told. A folder whose name starts with
`.` is **never indexed, at any depth** — `.trash/` is where a browser LIFT that
was never dropped puts an item and `.originals/` holds the masters a sample was
made from, and offering either would mean the soft delete is not a delete
(`audio/tests/host_files.py`).

### `library {op}` — the file operation itself (D2, #62)

The companion app talks to **this process**, not to the UI, so DDR-055's rules
are reachable from the protocol as well as from the browser. Five ops:

| `op` | Fields | What |
|---|---|---|
| `rename` | `path`, `name` | `name` is one path component, in the same folder |
| `move` | `path`, `to` | `to` is the destination **folder**, library-relative; the root is `""` |
| `trash` | `path` | into `<root>/.trash/`, DDR-055's soft delete |
| `delete` | `path` | gone for good (what emptying the trash does) |
| `mkdir` | `path` | an empty folder at `path` (the app's *New folder*, #979, D104); the folder above it must exist |

Every path is **relative to the last `--library` root** — the writable
library, the same root `clip_to_asset` invents a path in. It is the *last*
because every caller passes the read-only boxes first and the player's library
after them (`--library <slot>/boxes --library /data` on the device,
`[BOXES, LIBRARY]` in the emulator), so that a box dropped into the player's
library cannot shadow a shipped one. Until #62 item 3 this said "first", which
no invocation ever matched: a phone's rename, and a bounce with no `file`,
resolved into the slot a `.fw` replaces. An absolute path, or any `..`
component, is refused `BAD PATH`; a dot-prefixed component is *not*, because
`.trash/` is a real folder and putting an item back means naming it. (The HTTP
file server refuses dot components at its own edge — device-os-spec.md § 9.3.)

**`NEEDS ONE <KIND> ENGINE`** (`last_engine`, #839, D106): `trash` or `delete` of the
last engine of a kind (`SYNTH`, `DRUM`, `EFFECT`, `SEQUENCER`), or of a folder holding it;
a box of that kind elsewhere, card or `--library-fallback`, lets it go.

Three named refusals: **`NOT FOUND`** (`not_found`) for a source that is not
there or a `to` that is not a folder, **`EXISTS`** (`bad_request`) for a
`rename` or `move` onto a taken path, and **`STOCK`** (`bad_request`) for
anything `<root>/stock.json` lists — including `delete`, because a shipped
patch that can be deleted but not renamed would be the stranger rule. Stock is
that manifest and nothing else: nothing on disk tells a patch that came in the
image from one the player recorded (`audio/library/README.md` § Stock).
The one write that does not come through here, the file server's `PUT /library/…`, keeps
the same two refusals with the same reply shape since #429 (`device-os-spec.md` § 9.17); it
also refuses a path *inside* a stock kit folder, and overwrites a player's file only when
the client asks with `?replace=1`.

A fourth, since #297: **`OPEN ON THE INSTRUMENT`** (`open_project`) for the
folder the tape is being written in (`tape_dir`), any folder above it, and
anything inside it. With `--tape-dir` the open project's folder holds the live
media, and a rename would leave the tape writing to a path that moved. Every
client's file op arrives here — the UI's browser, the phone's rename, the HTTP
`DELETE` — so this is the one place it is refused.

**One exception since #821 (D99): `rename` of that folder itself** (not one above it,
not a file in it, not `move`, `trash` or `delete`). The phone renames the project
the instrument has open: the tape I/O thread drains, renames the folder (refusing a
taken name, `EXISTS`) and re-points, the open media following the rename. A take
open is `open_project` / `RECORDING`. Every client then gets a push,
**`tape_dir_renamed {from, to}`** (absolute folders), so the UI's autosave follows
the tape. Not with `--tape-store fake`, whose media are in no folder.

`mkdir` refuses `EXISTS` for a taken name (a file or a folder), `NOT FOUND` when the
folder above it is not a folder, `BAD PATH` for a dot-prefixed name (a folder that would
never be indexed), and **`ENGINES`** (`bad_request`) for anything under `engines/`, which
holds installed boxes only (`engines/<author>/<name>`). No parents are made.

`trash` is the one op that does **not** refuse a collision: two lifts of the
same name both have to survive, so `.trash/name.patch` becomes
`name-2.patch` — `free_path` in `emulator/app/library_ops.py`, the same rule.

Every op **rescans before it replies**, and the reply carries the updated
`engines` list, so one round trip both moves the file and re-indexes. `path` in
the reply is where the item landed, library-relative (empty for `delete`).

`min_version` is semver; `""` means any. A `seq` slot refuses an engine that
does not declare `ARB_CAP_SEQUENCER`.

**`instrument` is `"synth"`, `"drum"` or `"master"`** (absent = the active
one). The **master bus** (§15.5, DDR-062) is the §6 rack on the mix, after the
per-track EQs and before DRIVE: `load_rack {instrument:"master"}`, and with it
`set_param`, `add_tab`, `set_tab_engine`, `remove_tab`, `set_macro`, `bind`,
`unbind`, `set_range`, `set_sidechain` and `set_bypass` — the
same messages, the same documents, reported under `state.mixer.fx` /
`.tabs` / `.macros` instead of under an instrument. It is a rack and **nothing
else**: `load_engine`, `unload_engine`, `load_patch`, `load_kit`,
`set_note_param`, `copy_pad`, `waveform`, `env`, `bend`, `host_param`,
`instrument` and every MOD message (`route`, `unroute`, `set_route`,
`add_mod_tab`, `set_mod_tab_engine`, `remove_mod_tab`, `set_mod_sidechain`,
`set_mod_bypass`) are refused `bad_request` on it rather than falling back to
the active instrument.

`load_patch` applies `engine`, `env`, `fx` (the rack, embedded by value),
`mod` (the MOD rack, embedded by value, modulation-spec §11), `seq` +
`seq.pattern`, `octave` and `name`. A patch **without** `mod` loads an empty
MOD rack, exactly as a patch without `fx` loads an empty FX rack — so every
patch written before the MOD rack loads as it always did. The old `lfo` field
is **carried and reported but not applied**; the MOD rack replaced it. The
`host` knobs are session state and survive a `load_patch` (§7.2 has no field
for them). A patch's `env` may carry `glide`, `detune` and `bend_range`
(DDR-052); a patch without them loads the defaults, 0 s / 12 ct / 2 st, which
is how every patch sounded before they existed.

**A param id the engine does not declare is dropped**, not refused: the engine
is the authority on what exists (§7.1 keys by id), and a box's major version is
how a knob is retired — `arbutus/sampler` 2.0.0 has no `level` or `loop`, and a
v1 patch naming them loads with them ignored. This is not the §7.4 case: the
engine, its version and its assets are all there. An engine or asset key that
is missing is still refused.

The DRUM instrument's `env` starts at **a 0, d 4** (not the SYNTH's
0.012/0.2): DRUM › ENV is `arbutus/drum`'s AD (DDR-053) and those are v1's
instant attack and 4 s decay, so a kit sounds as it did before ENV applied.

### Assets — how samples and kits reach the engine

A sample or a kit is **content, not a parameter** (DDR-037): it does not cross
the ABI as a float 0..1. It crosses as `load_asset(h, key, path)`, a
control-thread-only entry of `arb_vtable` (appended inside ABI 1's reserved
space, a named field since ABI 2). The host calls it after `create()` and before the engine
is placed in the graph — never from the audio thread, never while it renders.

An engine **declares the keys it accepts** in `arb_describe()`, so `engine.json`
carries `"assets": ["sample"]` and the host can refuse a document naming an
unknown key before it opens a single file. `"assets": []` means the engine takes
none, and its `load_asset` slot is NULL.

A patch (§7.2) names them as a map of key → path:

```json
{ "format":1, "kind":"patch", "name":"MIC-03",
  "engine": {"id":"arbutus/sampler", "params":{"root":0.472441},
             "assets": {"sample":"synth/sampler/mic-03.wav"}} }
```

Paths are **library-relative**, resolved against, in order: the document's own
optional `"dir"`, each `--library` root, then the working directory. An absolute
path is taken as-is. A patch may also carry the map at the top level, which
applies to the ENGINE slot; the engine's own map wins.

`load_engine` with a **different** `id` clears the slot's params *and* its
assets — another engine has other keys, and carrying them over would only
produce a refusal.

**`load_engine` carries content too** (stage H2): `{slot, id, assets:{key:path},
dir?}` loads the engine *with* that map, resolved like a patch's against `dir`
first. It is present-or-absent, not merged — a map replaces whatever the slot
held and `assets: {}` clears it, while leaving the field out keeps what is
there (so re-sending the same `id` to change one parameter's worth of content
is impossible by accident). This is the door a **pattern** goes through
(DDR-037, §15.4): a sequencer arrives in the SEQ slot with its pattern the way
an FX tab arrives with its impulse, and the UI needs neither a whole patch nor
a message of its own. Like every asset it takes effect at `create()`, so the
slot's engine is rebuilt and crossfaded; the slot's **parameters survive**,
because the id did not change.

### `load_kit` — the pad map is one asset

`load_kit` validates the §7.3 document, checks every pad file exists, resolves
relative paths against the kit's optional `"dir"`, writes a resolved copy to
`$TMPDIR/arbutus-kit-<instrument>-<pid>.json`, and hands the drum engine that
one path as **`load_asset(h, "kit", …)`**. The reply carries `kit_path`.

The name carries the instrument and the pid, so a reload overwrites rather than
accumulates, and the file lives as long as the process does — the host re-reads
that path whenever the box is rebuilt. The host **removes the kits it wrote when
it shuts down** (#105): on the device `$TMPDIR` is a tmpfs, and one file per host
restart is memory that only ever grows until a reboot. A host killed outright
leaves its file behind, as a crash leaves anything behind.

A pad is either shape (both `"format": 1`, `engine-spec.md` §15.3):

```json
"pads": { "0": {"file": "kick.wav", "params": {"start": 0.1, "choke": 0.25}},
          "1": "snare.wav" }
```

`params` are the engine's `per_note` values, normalised, keyed by id. The v1
string migrates forward: `state.instruments.<i>.kit` always reports every pad as
`{file, params}` with **every** `per_note` param of the loaded engine filled in
(absent ones at the descriptor default). `file` stays as authored (relative to
`dir`), and `dir` is kept, so that document is exactly the kit to save.

**There is no `save_kit`**, as there is no `save_patch`: a UI persists a kit by
writing `state.instruments.drum.kit` (less `dir`) to its `kit.json`.
`set_note_param` and `copy_pad` edit that document.

One asset, not one per pad: a descriptor can declare at most `ARB_MAX_ASSETS`
(3) keys, and a pad map is variable-length content. A key naming a document is
the only shape that fits, and it keeps the pad→file mapping in the file format
where §7.3 put it rather than splitting it across 24 ABI calls.

Changing the kit re-creates the engine, crossfaded like any other swap.

### Asset refusals (§7.4)

| Condition | Message | `code` |
|---|---|---|
| the document names a key the engine does not declare | `UNKNOWN ASSET <key>` | `unknown_asset` |
| no file at the resolved path | `ASSET NOT FOUND <key>` | `asset_not_found` |
| `load_asset` returned non-zero, or the engine declares keys and has no `load_asset` | `ENGINE BROKEN` | `engine_broken` |

A refused asset refuses the whole load and leaves the previous graph running: a
drum machine with half a kit is not the shared sound (DDR-043).

---

## Editing

| Message | Payload |
|---|---|
| `set_param` | `{instrument?, slot:"engine"\|"seq"\|"fx"\|"mod", tab?, idx, value}` |
| `set_macro` | `{instrument?, slot:0..7, value}` |

`idx` is the parameter's **slot index** 0..7 as the UI draws it; `value` is
normalised 0..1. `tab` selects the rack tab when `slot` is `"fx"` or `"mod"`.

`set_param` on a `per_note` slot reaches the engine, which ignores it (§15.3).
`set_voice_mod` has no message of its own: the host calls it for `keys` routes
(below).

### Per-pad values (§15.3, §15.6)

| Message | Payload | Reply |
|---|---|---|
| `set_note_param` | `{instrument?, slot?:"engine", pad \| note, idx \| id, value}` | `ok` |
| `copy_pad` | `{instrument?, from, to}` — pads, default instrument `drum` | `ok` + `kit` |

`pad` is 0..23 and `note` is `pad + 53` (pad 0 is F3, the lowest key — the note
the engine receives); give one. `idx` is the slot 0..7, or `id` the param id.
`value` is normalised. The value is written into the kit document's
`pads.<pad>.params.<id>` (so `state` reports it and a saved kit keeps it) and
reaches the engine between blocks as `set_note_param(note, idx, v)`. A rebuilt
engine — a new FX tab, a patch, `load_engine` — gets every pad's values back
before it enters the graph.

Refused: a slot other than `engine` or a param not flagged `per_note`
(`bad_request`), a pad outside 0..23 (`bad_request`), an empty slot, an unknown
param, or a pad the kit has no file for (`not_found`).

`copy_pad` is LIFT on a pad, DROP on another (DDR-039): `pads.<to>` becomes a
copy of `pads.<from>`, file and params. The file changes, so the kit reloads
like `load_kit` — crossfaded, and refused like it. An empty `from` is
`not_found`.

### Rack edits (§6.3–6.6)

Every one of these replies with **the whole rack document**, so the UI never
computes rack state itself.

| Message | Payload |
|---|---|
| `bind` | `{slot:0..7, instance, param, lo?, hi?, curve?, depth?, name?}` |
| `unbind` | `{slot, target?}` — one target, or the whole macro when `target` is absent |
| `set_range` | `{slot, target, lo?, hi?, curve?, depth?, name?}` |
| `add_tab` | `{engine, instance?, min_version?, params?, assets?, dir?, index?}` |
| `remove_tab` | `{index}` |
| `set_tab_engine` | `{index, engine, min_version?}` — clears the tab's params, sidechain and assets; a **different** engine renames the tab after itself (`instance`, as `add_tab` mints it) and carries the new name into the rack's macro targets and the MOD routes' `fx.<instance>.<param>` targets (#329) |
| `set_sidechain` | `{index, source:"none"\|"tape:1".."tape:8"\|"input"\|"self"\|"master"}` |
| `set_bypass` | `{index?, bypass}` — with `index` that FX tab, without it the whole rack (DDR-049) |

Reply: `{"type":"rack","instrument":"synth","rack":<§6.7>,"tabs":[<slot>,...]}`.

`bind` on a slot that is already bound **multi-assigns** (DDR-039). `lo`/`hi`
are normalised positions of the *target*: the macro at 0 sends `lo`, at 1 sends
`hi`, and `lo > hi` inverts (§6.2).

`curve` is one of `lin` `exp` `log` `s`, applied to the macro position before
the `lo..hi` map:

| curve | shape | 0.5 maps to |
|---|---|---|
| `lin` | `x` | 0.5 |
| `exp` | `x²` | 0.25 |
| `log` | `√x` | 0.707 |
| `s` | `x²(3−2x)` | 0.5, with flat ends |

`depth` scales the excursion about the centre of `[lo, hi]`, so `depth: -1`
inverts about that centre and `depth: 0` parks the target there. The result is
clamped to 0..1 and written with `set_param` **every block**; a macro target is
therefore owned by its macro and a direct `set_param` on it will not stick.

`add_tab`/`remove_tab`/`set_tab_engine` reload engines, so they can refuse
(§7.4). `remove_tab` drops the macro targets that pointed at the tab.

**No parallel groups** (engine-spec §6.5, cut 2026-10-02; #273): the tabs run in series.
`set_parallel` is gone (an `unknown message` error), and a §6.7 document that still
carries `"parallel"` loads as flat tabs — the key is ignored and never written back.

`set_sidechain` refuses an engine that does not declare `ARB_CAP_SIDECHAIN`.

**FX tabs take assets** the way MOD tabs do: `"assets": {key: path}` on a §6.7
tab and on `add_tab`, resolved like a patch's (§ Assets) against the message's
`dir` (`add_tab`, `load_rack`), the rack document's own `dir`, or the patch's.
The rack document writes them back resolved and absolute, and omits the key
when a tab has none, so a rack from before reads back unchanged. No stock
effect declares an asset yet.

### Sidechain sources (§6.6, modulation-spec §4)

The same ids for FX tabs and MOD tabs:

| Source | `side[]` is |
|---|---|
| `none` | NULL — the box sees no sidechain |
| `tape:1`..`tape:8` | that track's playback this block, before the mixer |
| `input` | the live input this block (silence offline) |
| `self` | **this instrument's bus, one block late**: after the FX rack and `host.level`/`host.pan`, the signal the record tap sees |
| `master` | **the master bus, one block late**: after master gain and limiter, what leaves the host |

`self` and `master` are delay lines of exactly `--max-block` frames, sized at
start. At the fixed block (the device's, and the default) that is precisely the
previous block; a shorter block reads a slightly older stretch, but the delay
never changes, so a ragged render hears the same signal. `self` is cleared when
the active instrument changes: the other instrument's last block is not it.
All eight tracks are sources (DDR-060); a track that has never been recorded
is silence, not an error.

---

## MOD rack (modulation-spec)

Per instrument: up to **four modulator tabs** and up to **four routes**, plus
the `keys` source, which is always there and is not a tab. Every edit replies
with the whole §11 document, as the FX rack edits do:

```json
{"type":"mod","instrument":"synth","mod":<§11>,"tabs":[<slot>,...]}
```

```json
"mod": { "format":1, "kind":"modrack",
  "tabs": [ {"instance":"lfo_a","engine":"arbutus/lfo","min_version":"1.0.0",
             "params":{"rate":0.62,"retrig":1.0, ...},"bypass":false} ],
  "routes": [ {"source":"lfo_a","output":"sine","target":"engine.cutoff","depth":0.35,"curve":"lin"},
              {"source":"keys","output":"vel","target":"engine.level","depth":0.5,"curve":"exp",
               "lo":0.1,"hi":0.9} ],
  "bypass": false }
```

`lo`/`hi` are **absent when unset** (§6.1 writes `null`; both read back as
unset). A tab's `sidechain` is carried when set (any source above; absent =
`none`), and its `assets` map when it has one — same shape and resolution as a
patch's engine assets (§ Assets), written back resolved and absolute:

```json
{"instance":"st","engine":"arbutus/stepmod","params":{},"bypass":false,
 "assets":{"pattern":"/abs/path/pattern.json"},"sidechain":"self"}
```

### Edits

| Message | Payload |
|---|---|
| `add_mod_tab` | `{engine, instance?, min_version?, params?, assets?, dir?, index?}` — `assets` resolved against `dir` like a patch's |
| `remove_mod_tab` | `{index}` — its routes go with it |
| `set_mod_tab_engine` | `{index, engine, min_version?, assets?, dir?}` — routes from outputs the new engine lacks go; a **different** engine renames the tab after itself and the kept routes' `source` follows (#329). `assets` follows `load_engine`'s rule: present = the whole map, `{}` clears it, absent leaves it alone |
| `set_param` | `{slot:"mod", tab, idx, value}` — a modulator's own knobs |
| `route` | `{source, output, target, depth?, curve?, lo?, hi?, index?}` — `index` reinserts a lifted route at that row |
| `unroute` | `{index}`, or `{source, output, target}` |
| `set_route` | `{index, depth?, curve?, lo?, hi?}` — `lo`/`hi` `null` clears that bound |
| `set_mod_bypass` | `{index?, bypass}` — with `index` that tab (§2.3), without it the whole rack |
| `set_mod_sidechain` | `{index, source}` — `set_sidechain` for a MOD tab (§4); refused (`bad_request`) unless the tab's engine declares `ARB_CAP_SIDECHAIN` |
| `host_param` | `{instrument?, level?, pan?, pitch?}` — the knobs `host.level`/`host.pan`/`host.pitch` swing around, normalised; replies `ok` |

`add_mod_tab`, `remove_mod_tab` and `set_mod_tab_engine` rebuild the chain and
crossfade like `add_tab`. `set_mod_tab_engine` naming a **different** engine
clears the tab's params, sidechain and assets (another engine, other keys);
naming the **same** engine keeps all three, which is how content reaches a tab
that is already there — the UI sends `arbutus/stepmod`'s edited step values
back as `assets.pattern` without resetting the tab's knobs (#60). The others
swap only the route table, so an LFO keeps
its phase while you set its depth. A new route's defaults are §6.2's: `depth
0.25`, `lin`, no window. `source` is a tab `instance` or `keys`; `output` is an
output `id` from that tab's `desc.outputs`, or `vel` / `note` for `keys`.

### Targets (§5)

| Address | Knob it swings around |
|---|---|
| `engine.<id>` | the ENGINE slot's param, as in `params` |
| `fx.<instance>.<id>` | that FX tab's param — or, when a macro owns it, the macro's output |
| `fx.macro.<slot>` | the macro's position (`set_macro`); the modulated position then drives its targets |
| `env.a` `env.d` `env.s` `env.r` | the ENV page, normalised over A 0.001–2 s, D 0.001–2 s, S 0–1, R 0.005–4 s. PolyHost snapshots the ADSR at note-on, so this moves **new notes** |
| `env.glide` | GLIDE, normalised over 0–2 s. Taken at a mono/legato note-on, so it moves **new glides** |
| `env.detune` | DETUNE, normalised over 0–50 ct. Read every block: moves a **held** unison stack |
| `host.level` | the instrument bus gain, knob `host.level` (0..1, default 1) |
| `host.pan` | the instrument bus balance, knob `host.pan` (0.5 centre): unity at centre, `cos` toward the far side |
| `host.pitch` | ±24 st, knob `host.pitch` (0.5 = 0 st). **Added** to the bend in `arb_host.pitch_st`; every voice follows, ringing ones included |

Not targets at all (§5): a modulator's own params (no mod→mod, DDR-050), tape,
mixer, tempo. An address from a newer §5 that this host does not serve is
refused `NEEDS FIRMWARE UPDATE` (`needs_firmware_update`), and a patch routing
to one is refused the same way rather than loading without that route (§7.4);
every §5 address is served today.

### Depth (§6.2)

With `k` the target's knob (normalised), `m` the source output (clamped to
−1..1 bipolar or 0..1 unipolar) and `d` the depth (−1..1):

```
value = clamp(k + d · curve(m), lo, hi)       lo, hi default 0, 1
curve: lin m · exp sign(m)·m² · log sign(m)·√|m| · s sign(m)·m²(3−2|m|)
```

The knob in the document **never changes**: a route writes the engine, not
`params`. Turn the knob and the whole excursion moves. Several routes to one
target **sum** their `d · curve(m)` and clamp once, to the intersection of
their windows (an unset window is `[0,1]`; disjoint windows land on the
highest `lo`). A window given with `lo > hi` is swapped. Remove a route and the
target goes back to its knob on the next block.

Order within a block: SEQ → **modulators** → routes → macros → ENGINE → FX. A
modulator runs before anything it modulates, so this block's value moves this
block (§7).

### Bypass (§2.3)

A bypassed tab is not run and **its routes are not there**: the target sits on
its knob, window included. A bypassed rack runs nothing and routes nothing,
`keys` included. Bypass flags live in the document and round-trip.

### `keys` (§3.2, §8)

`keys.vel` is the last note-on velocity the ENGINE received (0 before any note),
unipolar; `keys.note` is `(note − 60) / 60` clamped to ±1, bipolar. Both follow
the notes the engine gets, so a sequencer's notes count.

- To an engine param flagged **`per_voice`**: forwarded as
  `set_voice_mod(idx, src, depth)` and **not** applied as a knob offset. The
  engine plays each voice at knob + depth × source for that voice's lifetime.
  `curve` and the window do not cross the ABI (it carries a depth only) and are
  ignored for these routes. Several such routes to one param sum their depths.
- To anything else: applied globally with the last note's value, like any
  other route.

### Live values

The ghost marker (§9) needs where each target **is**. That rides the `meters`
subscription:

```json
{"type":"meters", ..., "mod":{"engine.cutoff":0.61, "fx.macro.0":0.5}}
```

keyed by target address, for the active instrument, present only when a route
resolves. A `per_voice` route reports the last note's value. Values rendered
with an older route list are not reported, so an edit never mislabels one.

### Refusals

| Condition | `message` | `code` |
|---|---|---|
| a fifth tab, or a fifth route (§10) | `MOD RACK FULL` | `mod_rack_full` |
| a tab's engine is not in the library | `NEEDS ENGINE arbutus/stepmod` | `needs_engine` |
| a MOD tab engine without `ARB_CAP_MODULATOR`; a modulator in ENGINE, SEQ or FX | `ENGINE BROKEN` (`detail` says which) | `engine_broken` |
| a §5 address this host does not serve (none today) | `NEEDS FIRMWARE UPDATE` | `needs_firmware_update` |
| unknown source, output, target param or FX instance | the reason | `not_found` |
| a malformed target, or `mod.*` (mod→mod) | the reason | `bad_request` |
| a `mod` document whose route names a source that is not a tab or `keys`, duplicate instances, > 4 tabs or routes | `ENGINE BROKEN` | `engine_broken` |

A route whose target stops existing later — `load_engine` with another engine,
an FX tab removed — stays in the document and does nothing until it resolves
again, as a macro target does.

---

## Playing

| Message | Payload | Notes |
|---|---|---|
| `note` | `{on, note, vel, probe?}` | musical keys only (§3.4). `probe` (an integer, #73) asks for a `probe` push naming the block that carried it — see Timing |
| `softkey` | `{key:0..3, down, shift}` | reaches sequencer engines only (§3.6) |
| `panic` | `{}` | every voice off; MOD tabs are `reset()` too, so a modulator counting held notes (`arbutus/envelope`) lets go |
| `reset` | `{}` | `reset()` on every loaded engine: silence, state cleared |
| `instrument` | `{active:"synth"\|"drum"}` | one instrument sounds at a time (§5) |
| `env` | `{instrument?, a, d, s, r, mode, glide, detune, bend_range}` | every field optional, absent = unchanged. `mode` ∈ `poly` `mono` `legato` `unison`. The ENV SHIFT row (DDR-052): `glide` s, clamped 0–2; `detune` ct, unison spread outermost to outermost, clamped 0–50, default 12 (±6 ct); `bend_range` st, clamped 0–24, default 2 |
| `bend` | `{instrument?, value:-1..1}` | MIDI bend and SHIFT+‹ › held. The host ramps the bend position toward `value` at 0 → full in the GLIDE knob's seconds, or lands in one block at GLIDE 0; `value 0` is the release and returns the same way. The position × `bend_range` + `host.pitch` is `arb_host.pitch_st`. No event reaches the engine: there is no `ARB_EV_BEND` |
| `tempo` | `{bpm?, link?, base_bpm?}` | |
| `transport` | `{op:"start"\|"stop"\|"locate", position}` | `position` in frames |

**The transport is the host's, and a client holds a mirror of it** (#99). Several
clients are connected at once — the instrument's UI and the companion app over
TCP — so `playing`, `tape.recording` and the tape position in the pushed `state`
are the answer, and a client sends `transport` only for a change **it** made. A
client that re-asserts its own copy whenever the two disagree fights whoever
rolled the tape: the emulator did, at 20-30 `locate`s a second, and the tape
never got anywhere. Adopt on a **change** in the document rather than on its
value — a document that has not caught up with your own gesture yet says the
same thing twice, and only a change is news.

### Timing

`note` and `softkey` land at the **start of the next block** — the host only
learns about a key press between blocks, so quantising to the block is honest
rather than pretending otherwise. At the default 128 frames / 48 kHz that is
2.7 ms (10.7 ms at 512).
Events a **sequencer engine** emits are sample-accurate inside the block, which
is the case the `frame` field on `arb_event` exists for.

**`probe` (P7, #73)** is how that is measured on a live host. A `note` with
`probe: <int>` is stamped when the control thread parses it, and when the
audio thread applies it the host pushes
`{"type":"probe", "id", "t_ctl_ns", "t1_ns", "block", "frame":0}`: `t1_ns` is the
start of the block that carries the note and `block` its index since start.
Both stamps are `steady_clock`, which is `CLOCK_MONOTONIC` on Linux, so a
client there stamping `time.monotonic_ns()` before it writes can subtract. On
macOS `steady_clock` is `CLOCK_MONOTONIC_RAW`, which counts sleep and Python's
`monotonic_ns` does not: a Mac client stamps `CLOCK_MONOTONIC_RAW` (#251).
`frame` is always 0 — the note sounds from that block's first frame
(`tests/host_probe.py`). `audio/tools/latency-probe.py` is the client; the
CM4 numbers are in `audio/README.md` § Input latency. A full report ring drops
the report, never the note.

Clock (`ARB_EV_CLOCK`, one per tick at `ppqn`, `a` = tick within the beat) and
transport events are generated by the host, sorted by frame, and delivered to
both the SEQ and the ENGINE slot. After a sequencer they are **merged back in**,
so a box that consumes note events does not have to remember to forward the
clock.

### Hot swap

Anything that rebuilds the chain — `load_engine`, `load_patch`, `load_kit`,
`copy_pad`, `add_tab`, `remove_tab`, `set_tab_engine`, `add_mod_tab`, `remove_mod_tab`,
`set_mod_tab_engine` — builds the new graph on the control
thread and hands it over between blocks. The old chain is kept alive and
**crossfaded out over 256 frames** (5.3 ms at 48 kHz), then destroyed on the
control thread. There is no xrun and no click; there *is* a 5 ms window where
both graphs run, which is the cost of not clicking.

Consequence for tests: the first 256 frames after a rebuild are part old chain.
Install an engine and its rack with one `load_patch` if you want frame 0 to be
the new graph alone.

---

## Waveforms

### `waveform` → `waveform`

A peak envelope of an asset for the ENGINE page (§15.6): the sampler's sample,
a drum pad, any slot's asset.

| Addressed by | Payload |
|---|---|
| a file | `{path, dir?, bins?}` — resolved like a patch's asset path |
| a slot's asset | `{instrument?, slot:"engine"\|"seq"\|"fx"\|"mod", tab?, key, bins?}` — `key` may be omitted when the slot has one asset |
| a kit pad | `{instrument?, pad \| note, bins?}` |

```json
{"type":"waveform","for":"waveform","path":"/abs/bottle.wav","bins":4,"frames":17640,
 "sample_rate":44100,"channels":"mono","min":[-0.61,-0.4,-0.2,-0.05],"max":[0.6,0.41,0.2,0.05]}
```

`bins` defaults to 256 and is clamped to 1..4096. The file is split into `bins`
equal runs of frames and each reports its **min and max sample of the mono
mix** — channels averaged, which is what every stock engine plays. A file
shorter than `bins` frames repeats the nearest frame. It is read and reduced on
the **control thread**; nothing reaches the audio thread. Replies are cached per
path and bin count and recomputed when the file's size or mtime changes (at
most 64 entries). No file, or not a readable WAV: `not_found`.

---

## Tape and mixer

The audio process is a **renderer of a clip list**. Lift, drop, slice, merge and
undo stay in the UI; the UI sends the resulting list down.

| Message | Payload |
|---|---|
| `arm` | `{track:0..7}` or `{track:-1}` |
| `record` | `{track, on}` |
| `set_clips` | `{track, clips:[{start_frame, len, src_offset, src_track?, gain}]}` |
| `mixer` | `{track?, gain?, pan?, mute?, solo?, master?, limiter?, comp?, comp_rel?, comp_times?, drive?, drive_tone?, bal?}` |
| `eq` | `{track, low?, mid?, high?, hp?, lp?, mid_f?, mid_q?}` → `ok` + `eq` |
| `bake` | `{tracks?, file, format:"wav16"\|"wav24"\|"f32", frames?, trim?, master_fx?}` |
| `clip_peaks` | `{track, src_track?, src_offset, len, bins?}` **or** `{reqs:[{…}, …]}` (≤ 64) → `clip_peaks`, the batch answering `peaks:[…]` |
| `tape_edit` | `{track, len, sources:[…], op?, factor?, cutoff?, mode?}` → `tape_edit` |
| `tape_save` / `tape_load` | `{track, file}` → `ok` + `frames` (+ `in_place` when `file` is the medium itself) |
| `tape_dir` | `{dir, move?}` → `ok` + `dir`, `moved`, `frames[8]`, `refused[]` — only with `--tape-dir` |
| `tape_compact` | `{track, keep:[{src_offset, len}]}` → `ok` + `started`; the map arrives as a **push** |
| `tape_speed` | `{speed?, reverse?}` |
| `tape_filter` | `{on?, cutoff?, mode?:"LP"\|"HP"}` |
| `clip_to_asset` | `{track:0..7\|"all", start_frame, len, file?, master_fx?, job?}` → `clip_to_asset` + `path`, `frames` (+ `status:"started"` with `job`) |
| `bounce_cancel` | `{}` → `ok` + `running` — drops a `clip_to_asset {job}` (#920) |
| `scrub` | `{position, frames?, reseed?}` |
| `metronome` | `{level?, pitch?}` |
| `monitor` | `{on?, gain?}` |
| `input_source` | `{source:"mic"\|"line"\|"usb"\|"resample"}` (DDR-063) |

`record {track, on:true}` writes the **active instrument bus**, pre-mixer,
**plus what that track already plays**, into that track's medium at the
medium's **head** — the first frame nothing has used — not at the transport
position. Overdub is the only record mode (DDR-056): to replace, LIFT first.
`record {on:false}`, or `transport stop`, ends the take and it **becomes a
clip** — `{start_frame: where recording started, len, src_offset: where in the
medium it landed, gain: 1}` — pushed as a `take` message and applied to that
track's list in `state`. From that moment the UI owns it and may replace the
whole list.

Because the take already contains what it played over, committing it
**absorbs** the clips it covers: a clip it crosses is split, the covered part is
dropped (it is inside the take) and the remainders stay. A track's list never
overlaps. The UI does the same arithmetic on its own model; the two agree
because the geometry is the same.

```json
{"type":"take","track":0,"start_frame":48000,"len":24000,"src_offset":96000,"gain":1.0}
```

Tracks are **8** (DDR-060). A track's medium is allocated when it is first
recorded or loaded, not at start: eight 8-minute stereo float32 media is 1.5 GB,
which the CM4 does not have. A track that has never been written reads as
silence everywhere — playback, `clip_peaks`, `bake`, `tape_save`.

The medium is **append-only**, which is what makes the UI's clip algebra safe:
a region a clip reads is never written again, so a moved, sliced or copied clip
keeps its audio, a retake over it does not clobber it, and one undo level is
pure geometry. `tape.tracks[i].used_frames` is the head; a track whose medium
is full stops growing its take (the transport keeps rolling).

A clip reads `len` frames at `src_offset` from the medium of `src_track` —
its own track unless it says otherwise, which is how LIFT on one track and
DROP on another keeps its sound — and plays them at `start_frame` on the
timeline, scaled by `gain`. `set_clips` is literal — clips given there may
overlap and they sum; only a *take* absorbs. Tracks are 8 × stereo float32,
8 minutes each by default (`--tape-seconds`).

### `--tape-dir`: the take written behind onto storage (C1 step 2a)

With `--tape-dir DIR` the host runs a third thread — the **tape I/O thread** —
which writes each range of a take into `DIR/tape-1.wav` … `tape-8.wav` as it
fills, and nothing else in the host touches storage. A range is 49,152 frames
(1.024 s, exactly 384 KiB); the file is the stereo float32 WAV `tape_save`
already writes, with a **4 KiB header** so the audio starts page-aligned, and
that header **lags the data by one range** so a power cut can never leave a
file claiming audio that is not on the card. At the end of a take the header is
rewritten exact. A track never recorded has no file.

The audio thread's whole part in this is one POD on a ring per finished range:
it never waits and never allocates, and a ring it cannot push to is counted
(`dropped`) rather than waited on.

With no `--tape-dir` none of it happens — no thread, no files — and
`state.tape.io` is `null` rather than a set of zeroes, so "no I/O thread" and
"an idle one" do not read alike. When it is on:

```json
"io": {"dir":"/data/projects/…", "range_frames":49152,
       "queued":3, "committed":3, "dropped":0, "absent":0, "errors":0, "syncs":3,
       "queue":0,
       "pool": {"slots":117, "resident":8, "free":109,
                "fetched":0, "evicted":0, "starved":0},
       "readahead": {"ahead_s":4.0, "behind_s":1.0, "wanted":0, "capped":0},
       "late": {"blocks":0, "tracks":0},
       "peaks": {"on":true, "bins_per_range":16, "bin_frames":3072,
                 "ranges":3, "hit":1, "miss":1},
       "prealloc": {"on":true, "lead_ranges":4, "calls":3, "bytes":2756608,
                    "refused":0},
       "commit_us": {"count":3, "p50":812, "p99":9100, "max":9100,
                     "over_20ms":0, "over_100ms":0},
       "sync_us": {"count":3, "p50":790, "p99":8900, "max":8900,
                   "over_20ms":0, "over_100ms":0}}
```

`commit_us` is the wall time of one commit — the lead, the range's `pwrite`, the
header's, and one `fdatasync` — over the last 4096 of them; it is what the bench
compares against C6's 0.54 + 10 ms on the eMMC. `sync_us` is the **`fdatasync`
alone** out of those same commits, which on the bench eMMC is 93–95 % of the
whole thing. **`over_20ms` and `over_100ms` are counts, and they are the
statistic to read**: `p99` needs samples, a take makes one commit a second, and
over twelve two-minute takes of identical code the p99 ranged 15–142 ms while
the median held to ±0.5 ms (#158). `last_error` appears when a store call has
refused. `queue` is ranges off the ring and not yet on the card.

`peaks` is the sidecar (#163): `ranges` is how many ranges across all eight
tracks it holds bins for, and `hit` / `miss` are `clip_peaks` answers out of RAM
against answers that had to read the audio. A **`miss` that climbs while a
settled clip is being drawn is a client asking too fine**, not a cold cache —
which is why the counter exists: wall time cannot tell a cache hit from a fast
card. `--no-tape-peaks` reads as `on: false` with both counters at zero.

`prealloc` is the **lead** (#158): before each range is written the filesystem
is asked to have blocks `lead_ranges` (4, so 4.1 s) past it, which is what keeps
the `pwrite` out of the allocator. It does **not** move the file's length —
`FALLOC_FL_KEEP_SIZE`, because the length is what the repair above reads — so a
medium written with the lead is byte for byte the medium written without it.
`calls` and `bytes` are what was asked for; `refused` is a filesystem that
cannot preallocate at all, counted **once per medium**, after which that medium
grows the way it did before. `--no-tape-prealloc` turns the lead off, which is
the *before* switch the bench measures against.

`repair` appears only when opening a medium found one to do (C1 step 3a):

```json
"repair": {"media":1, "lost_frames":49152, "refused":0}
```

**The head of a track comes from its file's header, never from the file's
length.** A medium is opened, the two are made to agree, and only then may
anything append to it. A file *longer* than its header claims is a cut
mid-take — the data page of the last commit landed and its header page did not
— and it is truncated back to the claim; a file *shorter* is a repaired
filesystem's truncation and the claim is clamped to the whole frames that are
really there. Either way the file is exact again before the next take appends,
and `lost_frames` is what that cost — the frames a provisional clip has to be
shortened by. `refused` counts a file under one of our names that is **not one
of our media** (a plain 44-byte-header WAV, for instance): its audio does not
start at byte 4096, so it cannot be written in place, and it is left byte for
byte alone rather than overwritten — that track simply has no medium, and
`last_error` says which file.

`pool` is the **bounded RAM window** (C1 step 2b, `--tape-pool-seconds`, 120
track-seconds by default = 117 slots of 1.024 s): `resident` ranges of `slots`,
`free` waiting for a take, `fetched` read back off the card, `evicted` given up
to make room, `starved` a fetch that found no slot and will ask again. It is
under `io` rather than as `tape.pool` because it exists only with a `--tape-dir`
— with no medium on storage the pool is unbounded and the tape is the RAM tape it
has always been, which only a host started by hand without `--tape-dir` runs (the
emulator and the device both pass one: § *Every spawned host is on storage*, below;
#1131).

`readahead` is the **horizon** (C1 step 5, `--tape-readahead-seconds` 4,
`--tape-behind-seconds` 1): the I/O thread keeps resident every range the clip
lists read inside `[pos − behind_s, pos + ahead_s]` of the playhead, in the
direction the tape is travelling. Both are **seconds of wall clock**, so the
window in tape is four ranges at 1× and sixteen at 4×, and a SPEED hold does not
outrun it. It follows the loop seam — within `ahead_s` of the loop's end the
window continues from its start — and it follows a clip onto whichever track's
medium it really reads. `wanted` is how many ranges the last turn asked to keep
resident and `capped` how many turns hit the ceiling of **half the pool**, which
is what stops a wanted set larger than the pool leaving the evictor no range to
give up. A range in the wanted set is not evicted; the recording track's is
fetched first and evicted last, because an overdub records the track's own
playback and a range missing there is silence in the take.

`late` is **`tape_late`** (C1 step 5's measurement, #160): `blocks` is the count
of rendered blocks in which some track wanted a range that was not resident and
played silence for those frames, and `tracks` is the bitmask of which tracks
those were (bit 0 = track 1). The host reads and clears the tape's own sticky
mask once a block, so `blocks` counts blocks and not every block after the first
one. Zero is what a horizon that keeps up looks like, and
`--tape-readahead-seconds 0` is what makes it large.

`--tape-readahead-seconds 0` turns the horizon off. The tape still plays: a
render that reaches an absent range writes silence, counts it, and **asks for
that range**, so the next pass over it sounds — which is what the flag is for,
as the *before* switch for the whole mechanism.

A take that reaches the end of the window because the card has stopped answering
— every slot holding a range the card has not taken — ends with a push, and the
transport keeps rolling:

```json
{"type":"tape_full","reason":"stall","total":1}
```

Read-ahead can never be the cause of that: the I/O thread's own fetches stop at
the free list's low-water mark and evict to get a slot instead, so a pool under
pressure degrades **playback** to `tape_late` before it ends a take. Only a
control-thread reader (`bake`, `clip_peaks`, `tape_save`) may take the last free
slot, because it has to give a real answer and it is not competing every block.

### `tape_late` and the three `TAPE FULL`s (C1 step 7)

A block in which some track wanted a piece of tape the card had not delivered —
that track plays silence for those frames and the clip's other frames in the
block still sound — is counted once, whichever tracks were late, and pushed on
the **same window and the same flag rule as `xrun`** (`[TBD-Elfy: N in M s]`,
3 in 2 s). One alarm rule, one slot on the screen, two sets of words:

```json
{"type":"tape_late","count":198,"window_s":2.0,"total":198,"flag":true,"tracks":1}
```

`count` is the window's, `total` the session's, `tracks` the eight-bit mask
since the last push (bit *t* = track *t*). The UI draws
**`HARD DRIVE SLOW`** where it draws `CPU NO HAPPY` (Elfy, design § 7 question
2 — not the note's own `TAPE LATE`). A **scrub** over a piece the card has not
delivered is silent and **not** counted: the fetch it asks for still happens,
so the next grain there sounds, but dragging a cursor across a tape is not the
instrument being late.

A take that ends early says which of § 3.7's three reasons it was, and the
transport keeps rolling in all three:

```json
{"type":"tape_full","reason":"stall","total":1}
{"type":"tape_full","reason":"format","total":1}
{"type":"tape_full","reason":"disk","total":1}
```

`stall` is the card not answering until the pool was all write queue; `format`
is the medium's own end (today the `--tape-seconds` capacity, far under the WAV
format's 10,922 ranges — before step 7 this stopped the take growing and said
nothing at all); `disk` is storage refusing a commit for want of space. The UI
says **`CARD STALLED`** for the first and **`TAPE FULL`** for the other two
(Elfy, § 7 question 3), because the player's next action differs.

A `disk` take is cut back to the **committed prefix** — the ranges the card
really took — because the slots the pool still holds are evicted soon and would
then read as silence. To keep that from happening mid-take at all, **`record
{on:true}` is refused `tape_full` / `TAPE FULL` when the medium's filesystem has
less than twice the pool free** (~92 MB at the default 120 track-seconds; § 7
question 4, answered yes). With no `--tape-dir` there is no floor and nothing
to refuse under.

`state.tape.pool` is the same thing at rest, and unlike `state.tape.io` it is
always there:

```json
{"ranges": 117, "resident": 40, "queued": 0, "late_total": 198}
```

`ranges` 0 means the whole-track RAM tape — no window — which is only a host
started by hand without `--tape-dir`. FILE › SYSTEM's `TAPE BUFFER` row reads it.

**Every spawned host is on storage** (C1 step 3b, #297): `arbutus-audio.service`
passes `--tape-dir /run/arbutus/tape-start` (a scratch folder the UI moves it off at once, to `/data/.state` or the open project; #781: never `/data`, which a restart may find unmounted), and the emulator's `NativeAudio` passes the
folder it is given or a private scratch one, so on the device and in the
emulator the tape is the window over the media described here (#591 finding 4
corrected the sentence that said nothing passed it). `tape_load` still reads a
whole file into RAM and `tape_save` to any path but the medium's own still
writes one; both read the medium *through* the window, so they are correct, and
they stay the way an old project's 44-byte-header WAV gets in and a
save-as-copy gets out.

### `tape_dir` — where the media live (C1 step 3b, #157)

With `--tape-dir` the eight media are `<dir>/tape-1.wav … tape-8.wav` (and their
`.peaks` sidecars), and `tape_dir` moves the tape to another folder. Elfy's
answer on #157 is that **SAVE moves the tape files into the project folder and
recording carries on there**, and SAVE means "save the project I have open", so
the folder the tape lives in *is* the open project:

```json
{"type":"tape_dir","dir":"/data/projects/2026-10-03/PROJ_0001","move":true}
{"type":"ok","for":"tape_dir","dir":"/data/projects/2026-10-03/PROJ_0001",
 "moved":true,"frames":[69152,0,0,0,0,0,0,0],"refused":[]}
```

* **`move: true` is SAVE.** Every medium and sidecar in the current folder is
  **renamed** into `dir` (created if it is not there). A rename within one disk
  is instant however long the tape is, so SAVE never copies audio (design
  § 3.3). The open files follow the rename, the RAM window is untouched and the
  next take appends to the moved file. Refused **`exists`** — nothing moved — if
  `dir` already holds anything (a file, a folder, a symlink, dangling or not)
  under **any** of the sixteen names `tape-1.wav … tape-8.wav` and their
  `.peaks`, whether or not the tape has that track yet: a track with no medium
  makes one on its first take, from frame zero (#484). Each rename refuses to
  replace what is at its target, so a name taken after the check is not written
  over either. Refused **`move_failed`** if a rename fails (another
  filesystem), with every rename already done put back. Refused
  **`move_split`** if putting one back fails too (a failing card): the tape is
  in two folders, `message` names each file left in `dir`, and the reply's
  folder (`state`'s `tape.io.dir`) is still the old one; the open files keep
  recording where they are. `frames` is each track's head. Moving to the folder
  the tape is already in, under any spelling (`/p/`, a symlink to it), is not an
  error and moves nothing.
* **no `move` is LOAD.** The current media are forgotten — the window's ranges
  retired, nothing deleted — and the media in `dir` are opened **in place**:
  nothing is read into RAM, each file is repaired as on any open (above) and its
  repaired header claim becomes the track's head and `frames[t]`. A track with no
  file in `dir` has none (`frames` 0, head 0) until its first take creates one,
  so a project folder holds only the tracks that have audio. A file under one of
  our names that is **not one of our media** — the 44-byte-header WAV `tape_save`
  writes to any other path, which is every project saved before step 3b — is
  listed in `refused` and left byte for byte alone; that track has no medium.
  Send `set_clips` afterwards, as after `tape_load`: the clips playing until then
  read the new media.

Both are refused **`RECORDING`** while a take is open, and **`NO TAPE DIR`**
without `--tape-dir`. A compaction in progress is **cancelled** first — its
`tape_compact` push says so — because one finishing after the move would rename
its scratch file onto the wrong folder.

**A host started with `--tape-dir` over media already there opens them the same
way**, so a take appends after them rather than over them.

**`tape_save` onto the medium's own path** (`file` names `<dir>/tape-N.wav`) is
a flush and not a copy: the reply carries `in_place: true` and the file stays
one of our media. Copying there would put a 44-byte-header WAV over the medium
behind the I/O thread's open file, and the next open would refuse it.

### Playing the tape: loop, SPEED, REV, the FILTER hold

`transport {op:"loop", in, out, on}` wraps the **tape playhead** into
`[in, out)`; `tape_speed {speed, reverse}` moves it at 0.25…4× and backwards
while REV is held (letting go lands where it would have been rolling forward,
the emulator's `hold_rev`); a take is always captured at 1×. The **clock**
(`song_frame`, the tempo grid, `ARB_EV_CLOCK`, the click) always runs forward at
1× while playing, as the emulator's did — `tape_pos` on a `meters` push and a
`render` reply, and `tape.position` in `state`, is where the tape is.

`tempo {bpm, link}` is **LINK** (DDR-059), on by default: the tape plays at
`bpm / base_bpm`, so slowing the song slows the tape with it and the sequencers
stay on the grid. `base_bpm` is the tempo the *medium* is written at and nothing
moves it on its own — a take recorded while the factor is not 1 is resampled
back to it on the way in, so its clip covers the timeline it was played over and
plays back as it was performed. LINK off returns the tape to its own rate and
BPM moves only the grid, which is how a second song goes on a tape at a new BPM
without repitching the first. SPEED and REV multiply on top of LINK; a take
rides the link factor alone.

`tape_filter {on, cutoff, mode}` is the FILTER hold (DDR-026): a one-pole LP or
HP over the **tape mix only**, live while held, state cleared on release.

Stopped, the tape is silent. `scrub {position, frames, reseed}` plays one grain
of it under the cursor, fading in and out over 64 frames; `reseed: false` keeps
a grain that is already running where it is and only extends it.

### `clip_peaks` — what TRACK draws

The samples never leave the host, so a clip's waveform comes back as an
envelope: the **min and max over both channels** per bin, read from the medium
on the control thread. `bins` defaults to 256 and is clamped to 1..65536. A
region wholly inside `used_frames` is cached (at most 256 entries, dropped by
`tape_load`); a region past the head — a take still rolling — is read fresh.

**`source` says which of two paths answered** (#163, C1 step 6's follow-up).
With the tape on storage, reading the audio for a three-minute clip means
~64 MB off the card and a scan of every sample — 4.0 s for the four lanes TRACK
draws at once, measured on the CM4 (#162). So the tape I/O thread keeps a
**peaks sidecar**: sixteen min/max pairs for every 1.024 s range it commits or
fetches, 128 B a range, written beside the medium as `tape-N.peaks`.

* `"source": "sidecar"` — answered out of RAM. Neither the card nor the samples
  were touched.
* `"source": "audio"` — the scan, exactly as before. That is what a question
  **finer than the sidecar** gets (fewer than 3072 frames a bin), or one whose
  span leaves the medium's **durable prefix** (a range the pool has written and
  the card has not taken), or one covering a range that has no bins yet.

The sidecar is a cache: a client cannot ask for one path or the other, the
numbers are the same either way, and `--no-tape-peaks` turns it off. What a
*client* should take from it is the resolution: **ask for one bin per pixel you
are going to draw**, not one per 256 frames of the clip, or the reply is tens of
thousands of numbers the socket has to carry and the sidecar cannot answer.

**A batch: `{reqs: [{…}, …]}`** (#169). TRACK draws four lanes at a time, so a
redraw is one of these messages per clip and #163's bench cell measured a round
trip at **2.1–2.5 ms on the CM4 with this host's own reply cache warm** — the
cost of the message itself, ~9 ms of a 35 ms frame for four of them. So
`clip_peaks` also takes a list of requests, each one exactly the object above,
and answers **`peaks`** — one body per request, in request order, each the same
object a lone request would have got. Nothing else changes: the elements are
answered one at a time on the control thread, through the same cache, the same
sidecar and the same scan.

* **At most 64 requests**, and an empty list is refused: the whole reply is built
  in memory before it goes down the socket. A caller with more chunks.
* **A bad element refuses the whole batch**, `bad_request`, with `reqs[<i>]` in
  the message. The caller computed every element in one pass from its own
  geometry, so one bad element means that pass is wrong; a per-element error
  object would have every client writing the same loop to find it.
* The reply carries no `source` of its own — each element carries its own, as it
  does alone, because one batch can be answered by both paths.

### `tape_edit` — the ops that change audio, not geometry

REV, SPEED and FILTER **tapped while stopped** bake into the clip, and TAPE
joins clips that are not contiguous in the medium. Those are audio, so they
happen in the medium: the sources are mixed into one run and the result is
appended at the destination track's head.

```json
{"type":"tape_edit","track":0,"len":24000,"op":"reverse",
 "sources":[{"track":0,"offset":0,"len":24000,"at":0,"gain":1.0}]}
{"type":"tape_edit","for":"tape_edit","track":0,"src_offset":48000,"len":24000}
```

`op` is `copy` (the default), `reverse`, `speed` (`factor`, and the reply's
`len` changes with it — speed and pitch are coupled, DDR-017) or `filter`
(`cutoff`, `mode`). The DSP is the emulator's `app/tape.py`: nearest-neighbour
reverse, linearly interpolated resample, one-pole filter. The reply says where
it landed and the UI points a clip at it. Refused `bad_request` **RECORDING**
while a take is open (the take owns the head) and `tape_full` when the medium
cannot hold the result.

It is computed and written **one range at a time** (C1 step 6): the edit's
working set is a handful of ranges rather than its length, so a five-minute
reverse costs the same memory as a five-second one and, with the tape on
storage, does not need a window as big as its result. Two consequences worth
knowing at the wire:

* a source that reads the **destination** track past that track's
  `used_frames` is silence, because reads and writes now interleave and past
  the head is not audio the tape claims;
* the reply's `len` is what was really written. It is the whole result except
  when the RAM window could not give the edit a slot, where the edit ends
  early and the clip the UI points at is that much shorter — the `tape_full`
  refusal stays for an edit that got nowhere at all.

### `tape_compact` — the medium gives its dead audio back (C1 step 8)

The medium is append-only, so a retake, a LIFT with no DROP and every
`tape_edit` leave audio nothing plays. `state.tape.tracks[t].dead_frames` is how
much — the frames **no clip on any track references**, swept over all eight clip
lists because a clip may read another track's medium (`src_track`). It is a
**suggestion, and it can only over-report**: the UI holds two references this
process has never seen, the lift buffer and the one-level undo record
(DDR-054/055), so the host's answer is "worth compacting" and the keep set is
the UI's to compute.

```json
{"type":"tape_compact","track":0,"keep":[{"src_offset":103304,"len":103304}]}
{"type":"tape_compact","for":"tape_compact","track":0,
 "started":true,"dead_frames":103304}
```

and then, as a push:

```json
{"type":"tape_compact","track":0,"frames":103304,"freed":103304,
 "map":[{"from":103304,"to":0,"len":103304}]}
```

**Two messages, not one.** A medium at twice the ruler is ~7 s of copying on the
eMMC, and a SAVE that waited for one would be the freeze C12 (#93) was filed
about; design § 3.8's rule is that SAVE *starts* a compaction. So the request
replies `started` and the result arrives beside `xrun` and `tape_late`. Offline
there is no I/O thread and the copy runs inside the request — the wire shape does
not change, the push is simply already waiting at the next round trip.

`keep` is the UI's **whole** reference set: its clips, its lift buffer and its
undo record. Spans are sorted, coalesced (two that touch or overlap become one)
and clamped to what the medium really claims, and each surviving span lands on a
**range boundary** of the new medium, so a range never straddles two source
spans. That padding is why `frames` can exceed the sum of the kept lengths. A
`keep` of `[]` is legal and means it: the track is truncated to its 4 KiB
header.

The reply is the UI's cue to **remap and send `set_clips`**: a reference at
`from + d` becomes `to + d`, and the same map remaps the lift buffer and the
undo record. Compact a span the undo record points at *away* and undo brings
back a clip that plays somebody else's audio — which is exactly why the keep set
is the UI's and not the host's.

Refused with `RECORDING` while a take is open, `PLAYING` while the transport is
running (the copy would compete with read-ahead for the card), `BUSY` while
another job runs, and `NO TAPE DIR` where there is no medium on storage at all.
PLAY or REC **arriving** while a job runs cancels it: the scratch file is
deleted, the old medium has not been touched, and the push says `cancelled`. The
retry is the next trigger's.

**None of that waits for the card (#167).** Until 2026-09-25 all three promises
above were true only on paper: the copy held the tape's one lock across every
slice of reading and writing, so for the whole ~7 s the host answered *nothing*
— the `started` reply came back at 7.09 s of a 7.14 s job, a fresh connection's
`hello` took 4.7–5.3 s, and PLAY two seconds in cancelled nothing because by the
time the cancel won the lock the copy had finished and replaced the medium. The
copy now drops that lock around every read/write pair, which is ~97 % of its wall
time; `dead_frames` comes back from the start of the job rather than from a
second trip through the lock; and a cancel lands **inside** a slice, so it costs
one range of card time rather than the job. The measurement this was found by is
`arbutus_os/bench/c1-compact-pi.sh`.

### `clip_to_asset` — a stretch of tape becomes a file

`clip_to_asset {track, start_frame, len}` renders that window of the timeline
and writes it as a **16-bit WAV** (engines read nothing else), replying with the
`path`. `track: 0..7` renders that track's own clips at their gains — its audio,
not a mix of it — and `track: "all"` renders through the mixer (the per-track
EQ, mute/solo, gain and pan, then DRIVE, `master`, `bal` and the limiter),
which is the mixdown `bake` would give over the same window.

`master_fx: true` (stage J1) additionally runs the mixdown through the **master
FX rack**, which is FILE › BOUNCE's switch (screen-sketches § FILE). It is
ignored on a single track — one track is not a mixdown. The rack is rebuilt
offline from the same §6.7 document and the same macro positions rather than
borrowed from the audio thread, and the render carries no events, no clock and
no sidechain sources: a bounce is of what is on the tape. A rack that declares
latency (`arbutus/drive` is 15 frames, §12) is **compensated here** — the window
is rendered that much longer and the rack's own delay is dropped off the front —
so the file is exactly `len` frames and lands on the timeline it was played on.
COMP is never in a bounce.

Without `file` it lands in the sampler's library folder
(`<last --library root>/synth/sampler/tape-<n>-NNN.wav`, or `mix-NNN.wav`) and
the library is rescanned, so the browser lists it without a restart. That is the
tape → instrument half of the round trip (DDR-030): the UI lifts a clip, drops
it on SYNTH › ENGINE, and hands the sampler the path that comes back. Refused
`bad_request` **RECORDING** while a take is open.

**As a job** (#920, D98: FILE › BOUNCE starts the render at once and a played
key may cancel it). `job: true` replies at once with `status: "started"` and
the `path` and `frames` it will write; the render then runs on the control
thread **a quarter of a second of tape a turn** of the host's loop (the loop
does not sleep while one runs), and **offline one chunk a `render`**, so a test
decides how far it has got. Everything the mix reads is copied at the start —
the clip lists, the gains, the EQ, the master strip and the macro positions —
so a knob turned or a take recorded meanwhile is not in it. The file is
written **only when the last chunk is in**: until then nothing is on the card.
It ends in a `bounce` push: `{path, frames}`, or `{cancelled: true}`, or
`{error, code}`. **`bounce_cancel`** drops it with nothing written (`running`
says whether one ran); a `tape_dir` without `move` (LOAD, NEW) and a
`tape_load` cancel it too, because they take away the media it reads, while
SAVE's `tape_dir {move}` renames the same media and it carries on. One job at a
time: a second `job`, or one asked during a `tape_compact`, is refused
**BUSY**, and `tape_compact` is refused **BUSY** while a job runs (it moves the
offsets the copied clip lists read). PLAY and REC do **not** cancel it: the UI
asks the player first. Without `job` the message renders whole before it
replies, as it always did.

### `tape_save` / `tape_load` — the tape across a restart

`tape_save {track, file}` writes `used_frames` of the medium as a stereo
float32 WAV; `tape_load {track, file}` reads one back from frame 0 and puts the
head past it. The clip list is the UI's to persist (it is JSON already), so a
restart is: load each medium, then `set_clips` — in that order, because a clip
already playing would otherwise hear the medium change under it. Both are
refused while a take is open.

`bake` renders the selected tracks (default all eight) through the mixer to a
WAV: `wav16` and `wav24` are integer PCM, `f32` is 32-bit float. **MP3 is out of
scope** — the audio process ships no encoder and will not grow one; converting a
baked WAV is the UI's business. The reply carries `start_frame` and `frames`:
the window that was written.

**The companion app's bounce is this message with `trim` and `master_fx`**
(D2 #23, `companion-app-spec.md` §4):

- **`trim: true`** starts the file at the **first** clip start across the
  selected tracks instead of at frame 0, so a bounce carries no leading
  silence. `trim` and `frames` together is refused — `frames` names an absolute
  end and `trim` chooses both ends, so one would have to quietly win.
- **`master_fx: true`** runs the mixdown through the master FX rack, exactly as
  `clip_to_asset {track:"all", master_fx}` does (stage J1): a second copy built
  from the same §6.7 document, with the rack's declared latency compensated.
  It also keeps the rack's **tail** — `latency_frames` + a fixed 2 s past the
  last clip — because a reverb on a bounce goes on ringing after the music
  stops. Without `master_fx` there is no tail: there is nothing to ring.
- COMP is still never baked, on either path.

Mixer: per-track `gain` and `pan` (equal power, −3 dB at centre, the emulator's
law), `master` level, an optional one-knob `limiter`, and `comp` 0..1 — the
emulator's MASTER COMP (DDR-027), a feed-forward peak compressor with one gain
per block. **Everything defaults to unity with the limiter and COMP off**, so an
empty mixer does not colour the sound.

### The mixer strip (stage F1)

The order is §15.5's, and every default is the value at which that stage does
nothing at all:

```
tape 1..8 -> per-track EQ -> mute/solo + gain/pan -> mix (+ instrument bus)
          -> master FX rack -> [click, monitor] -> DRIVE -> COMP -> LEVEL·BAL
          -> limiter -> out
```

**Mute and solo** (DDR-061) are per track on `mixer`: `{track, mute}` latches,
`{track, solo}` is the held gesture, and **solo wins** — with any track soloed
the rest are silent whatever their mute says. The gate is on the **mix only**:
the medium, the sidechain `tape:n`, the overdub tap's own playback and
`clip_to_asset` on one track all read the track itself, so muting is a
performance gesture and never an edit.

**`eq {track, low, mid, high, hp, lp, mid_f, mid_q}`** is the per-track EQ
(DDR-062). Every field is optional and what is not named does not move, so a
page can send one encoder; the reply carries the whole clamped block back, and
`state.tape.tracks[i].eq` has it too. The values are **real units, not
normalised** — this is host DSP, not an engine: `low` / `mid` / `high` in dB
(±12), `mid_f` 100..8000 Hz, `mid_q` 0.2..8, `hp` 20..2000 Hz and `lp`
200..20000 Hz. LOW is a shelf at 200 Hz, HIGH a shelf at 3 kHz, MID a peak at
`mid_f` with width `mid_q`; HP and LP are one biquad each (12 dB/oct,
Butterworth). **`hp` at 20 and `lp` at 20000 are off**, not a filter at the
edge of the band, and three bands at 0 dB with both filters off is bit-exact
bypass. The EQ sits **between the tape and the mixer** (§15.5), so it colours
what is heard and never what is recorded.

**MASTER** is `mixer {master, comp, drive, bal}` / `{comp_rel, drive_tone}`
(DDR-062's `LEVEL · COMP · DRIVE · BAL` / `COMP REL · DRIVE TONE`).
`comp_rel` is the compressor's release in **seconds**, 0.02..1, default 0.12 —
v1's fixed value, so the sound does not move. `comp_times` (#933) is
`"block"` (the default: today's compressor, whose smoothing step is one
frame's but runs once a block, so the 5 ms attack and COMP REL run ~64x their
labels at the device's 64-frame block) or `"labelled"` (the envelope steps on
a 32-frame grid with each step's own coefficient, so attack and release are
the label at every block size); anything else is `bad_request`. It is Elfy's
A/B (play-queue P3), not a project setting: it is not saved. `drive` 0..1 is a soft clipper
(0 = bypass) and `drive_tone` 0..1 tilts what reaches it around 700 Hz
(0 = only the lows, 1 = only the highs, 0.5 = flat). `bal` −1..+1 is a
**balance**, unity at centre, the same law as `host.pan`.

`bake` applies the EQ, mute/solo, gain, pan, DRIVE, `master`, `bal` and the
limiter; **COMP and the master FX rack are not baked** — COMP is a live
performance knob (DDR-027) and MASTER FX on a bounce is FILE › PROJECT's own
switch (screen-sketches § FILE). `clip_to_asset {track:"all"}` is the same
mixdown and follows the same rule, except that it takes **`master_fx: true`**
for that switch (stage J1). `bake` does not: FILE › BOUNCE goes through
`clip_to_asset`.

### `metronome` — the click (TEMPO CLICK LVL / CLICK PITCH)

`metronome {level, pitch}` renders the click **in the master only, never the
record tap**: `level` 0 is off, `pitch` 0 is silent, and beats come from the
host's own clock — beat *b* starts at *b* × 60/BPM seconds of `song_frame`, 30 ms
of sine under a squared decay, an octave up on every fourth beat. It is a pure
function of the clock frame, so it is ragged-stable and a UI can find the beat
from `song_frame` alone (the flashing indicator).

### `monitor` — INPUT through the speakers

`monitor {on, gain}` sums the live input into the master (never the record tap).
Off by default: a laptop's mic and speakers are a feedback loop. MONITOR listens
to the **jack**, not to `input_source`: monitoring `resample` would be the
speakers playing themselves.

### `input_source` — where the input comes from (stage G)

`input_source {source}` picks one of **`mic` · `line` · `usb` · `resample`**
(DDR-063, §15.2) and is reported at `state.input.source`. It is the same value
as `arbutus/live-input`'s SOURCE knob: the message writes the knob, a
`set_param` on the knob sends the message, and a patch carrying SOURCE selects
it as it loads. One fact, two doors — a page needs no special case for this box.

The selected source is what reaches:

- **the ENGINE slot's `side[]`**, for a box in that slot that declares
  `ARB_CAP_SIDECHAIN`. `arbutus/live-input` is the one that does; nothing else
  in the ENGINE slot asks, so nothing else sees it. This is how input reaches
  the MOD rack, the FX rack, the tape tap and the mixer — there is no capture
  mode because loading that box makes one unnecessary.
- **the `input` sidechain source** for MOD and FX tabs (modulation-spec §4,
  "whatever INPUT is set to").

`mic`, `line` and `usb` are the one input stream this process opened — which
jack feeds it is the codec's business on the device, and there is nothing to
choose between here. **`resample` is the master bus one block late**, with the
armed track, the instrument bus, the click and the monitor taken back out:

- *one block late* for the same reason `master` is (modulation-spec §4) — the
  instrument renders before the mix it would be tapping;
- *minus the armed track* is §15.2's rule, so REC on track 8 does not record
  what track 8 is playing back;
- *minus the instrument bus* is what stops `live-input` hearing its own output,
  which at unity gain would be a loop rather than a bounce;
- *minus the click and the monitor* because a bounce must not carry either.

That subtraction is exact through the mixer's gains, COMP, LEVEL, BAL and the
limiter — every one of those is a scalar gain on the block. It does **not**
un-mix DRIVE, the master FX rack or the FILTER hold, so with an effect on the
master the box hears that effect's wet tail of its own output; it converges,
because a rack's wet level is under 1. `tape-sampling-workflow.md` §5.4 is the
trace this exists for: MIXER › FX, `LIVE INPUT` with SOURCE = RESAMPLE, REC on
a spare track, ride a macro.

---

## Pushes

| Message | Payload |
|---|---|
| `state` | the whole document, after any edit |
| `xrun` | `{count, window_s, total, flag}` |
| `tape_late` | `{count, window_s, total, flag, tracks}` — § Playing the tape |
| `tape_full` | `{reason, total}`, `reason` one of `stall` / `format` / `disk` |
| `tape_compact` | `{track, map:[{from,to,len}], frames, freed}`, or `{track, cancelled}`, or `{track, error}` |
| `bounce` | `{path, frames}`, or `{cancelled}`, or `{error, code}` — a `clip_to_asset {job}` ended (#920) |
| `take` | `{track, start_frame, len, src_offset, gain}` — a take became a clip |
| `meters` | `{master:[l,r], instrument:[l,r], tracks:[8], song_frame, tape_pos, comp_gr, mod?}` |
| `probe` | `{id, t_ctl_ns, t1_ns, block, frame}` — one per probed `note` (§ Playing › Timing) |
| `elements` | `{page, elements}` — the UI's screen map changed; **only to a client that sent `actions {on:true}`** (§ The screen and the player's actions) |
| `action` | `{kind, …, seq, t_ms}` — one thing the player did; same subscription |
| `appearance` | `{mode}` — the phone's light or dark changed; **to every client** (§ The phone's appearance) |
| `situation` | `{rev, patch}` — what the instrument is doing changed (an RFC 7396 merge patch); **only to a client that sent `live {on:true}`** (§ The live stream) |
| `frame` | `{rev}` — the screen's drawn frame changed; same subscription |
| `live` | `{watchers}` — how many clients are subscribed to the live stream changed; **to every client** |

`xrun` is pushed whenever the count inside the current window rises. `count` is
xruns in the last `window_s` seconds, `total` since start, and `flag` is the
host's own verdict — true at **3 in 2 s**, the default for §6.8's
`[TBD-Elfy: N in M s]`. That flag is what the UI draws as `CPU NO HAPPY`.
The state document's `xrun` carries the same `total` (#626), so a client that
connects to a host already running can count its own run's xruns as the last
push's `total` minus the state's `total` when it began (`xrun-watch.py` does).

An xrun is a block whose render took longer than its own duration, **or** (live,
since #13) a period the device lost: miniaudio's ALSA backend recovers from a
capture overrun or playback underrun by itself and says so only as a debug log
line, which the host counts. The first kind is CPU; the second is a late
*wakeup*, which no timing inside `render_block` can see — the block that is late
to start renders on time. `--offline` measures the first kind only, which is
what lets the CPU flag be tested with no audio hardware. One blind spot: for
playback miniaudio sets ALSA's stop threshold to the boundary ("loop instead of
stop"), so a playback underrun raises nothing — under `--no-input` a late wakeup
is not counted; with the capture side open (the default) the overrun on the
capture ring reports the same stall.

`tape_late` shares that window and that flag rule exactly — one alarm rule for
both, so `HARD DRIVE SLOW` and `CPU NO HAPPY` mean the same thing about how
often. `tape_full` is not a flag: it is a take that ended, which happens once
and is not a rate, so every one is pushed. Both are § Playing the tape.

`meters` is a **subscription**: `{"type":"meters","on":true,"hz":30}`. Values are
peaks **since the last push**, 0..1, pushed at up to 30 Hz — so a UI drawing the
envelope of a take as it records misses nothing between two pushes. The reply
carries the accepted `on` and `hz` back, clamped: 1..30 here, and 10 over TCP
(§ TCP), which is the one rate a client cannot work out for itself. `comp_gr` is
the lowest gain COMP applied in that span (1 = not compressing). §8.2 reserves this message for a
shared-memory handle; that is **deferred** — carrying eight floats in the JSON
costs nothing at 30 Hz and the day it does, the handle goes in this same message
without the message changing.

### The screen and the player's actions (D17, #276)

`companion-app-spec.md` § 10.6: Learn's guide points red arrows at things on the
instrument's screen and turns a step green when the player has done it, and the
wizard answers from the last ~10 actions. Both are **the UI's** facts, and both
the UI and the phone are already this process's clients, so the host is the
relay: the UI publishes, the host keeps the latest map and a short ring and
passes them on. None of it touches the audio thread or the state document. It
is added messages, so `proto` stays 1.

**From the UI (Unix socket only; `bad_request` over TCP):**

```json
{"type":"ui_elements","page":"SYNTH","elements":[
  {"id":"header","label":"SYNTH › ENGINE","rect":[0,0,320,16]},
  {"id":"param.1","label":"CUTOFF","rect":[80,190,80,34]}]}
{"type":"ui_action","actions":[{"kind":"key","id":"PLAY","down":true}]}
```

- `ui_elements` replaces the map. `page` is a string; `elements` is a list of at
  most 64 objects, each with a string `id` (anything else in an element is
  carried as given). An unchanged map is answered `ok` and **not pushed**: the UI
  republishes after a reconnect, and a phone redraws on every push.
- `ui_action` appends each of `actions` (at most 64 a message, each an object
  with a string `kind`) to the ring. The host adds `seq` (a counter since the
  host started) and `t_ms` (its monotonic milliseconds since start) and drops any
  `type` the UI put in. A tick with more than 64 goes as several messages, in
  order (#412): one refused message would be a whole tick lost.
- Either one malformed is refused `bad_request` whole, and nothing is kept.
- **A UI that leaves takes its map with it** (#412). When the connection that
  published the map closes, and it had said `hello` (the UI always does), the
  host sets `page` back to `null`, empties `elements` and pushes that empty map
  to subscribers — the phone must not point arrows at a screen nobody is
  drawing, and `GET /screen.png` answers 503 at the same moment. A publisher
  that never said `hello` (`python3 -m arbutus_os.mock --ui`, a test) owns
  nothing: its map stands after it closes.

**From anyone:**

| Request | Reply |
|---|---|
| `elements` | `{type:"elements", page, elements}` — the last map; `page` is `null` until the UI has published one |
| `actions {last?, on?}` | `{type:"actions", on, now_ms, actions:[…]}` — the newest `last` (default and most 10) of the ring that are no older than **30 s**, oldest first. `on` (a boolean, optional) subscribes **this connection** to the `elements` and `action` pushes; the reply carries the subscription as it now stands |

The ring is ten deep (§ 10.6's "last ~10 actions or ~30 s": both bounds apply).
`now_ms` is on the same clock as `t_ms`, so a client can say how long ago each
was. `last` is clamped to 0..10 before anything else is done with it. The 30 s
window is `--feed-window-ms` (tests only). The pushes go only to a subscribed connection — not to every client as the
other pushes do — because the UI, which sends them, would otherwise read its own
key presses back at sixty a second.

**What the UI publishes** (`emulator/app/ui_feed.py`): the map is the frame's
fixed zones (`ui/zones.py`, the same on every page), with ids a lesson can rely
on across releases:

| id | what | label |
|---|---|---|
| `header` | the breadcrumb row | the breadcrumb, e.g. `SYNTH › ENV` |
| `strip` | the dynamic strip | always empty: what it says is a value (#412) |
| `main` | the page's main area | the page id |
| `param.0` … `param.7` | encoder slot N's cell (0–3 the bottom row, 4–7 the SHIFT row) | the parameter's name |
| `key.1` … `key.4` | the key-strip cell over number key N | its label |

A slot or strip cell with nothing on it is left out. Labels, never values: a
value moves on every detent and would make the map change on every turn.

The actions, physical controls named by `emulator/chrome/keymap.json`'s ids:

| `kind` | fields |
|---|---|
| `key` | `id` (`PLAY`, `NOTE_7`, `SYNTH`, `1` …), `down` |
| `encoder` | `id` (`ENC1`…`ENC4`), `delta`, `shift`, and `param` + `value` (name and formatted value after the turn) when a cell is under it |
| `page` | `page`, when the page changes |
| `rec` | `on`, when the tape starts or stops recording, by whatever path |
| `note` | a note from a **MIDI keyboard** (#853): `note` (the MIDI note number), `key` (the `NOTE_n` it lands on), `on`, `vel` (0–1, on a note-on), `source: "midi"` |
| `capture` | INPUT took a sample (#853): `file` (its name in the library's sampler folder), `seconds` |
| `project` | FILE › PROJECT made, opened or saved one (#853): `op` (`new`, `load`, `save`), `id` (`<date folder>/<name>`) |
| `track` | TRACK's cursor or the armed track moved (#853): `selected` (1–8), or `armed` (1–8, 0 = none) |

Notes played on the instrument are `key` actions on `NOTE_n`; a MIDI keyboard's
are `note` actions (until #853 they read as `key` actions and could not be told
apart). An action's `kind` a client does not know is to be ignored: kinds are
added, never changed.

### The phone's appearance (#334)

The instrument's screen follows the paired phone's light or dark mode
(`docs/design/ui-aesthetic.md` § 3.1). The phone is a client here and so is the
UI, so the host relays it, as it does the screen map. Added messages; `proto`
stays 1.

```json
{"type":"appearance","mode":"dark"}
{"type":"appearance","for":"appearance","mode":"dark"}
{"type":"appearance"}
{"type":"appearance","for":"appearance","mode":null}
```

| Request | Reply |
|---|---|
| `appearance {mode}` | `mode` is `"light"` or `"dark"`; the host holds it and answers `{type:"appearance", mode}`. Anything else is refused `bad_request` and nothing changes |
| `appearance` (no `mode`) | the mode last set, or `null` when no client has said since the host started |

- **Any client may set it**, over TCP or the Unix socket: the phone is the one
  that does, and a bench or a test may. It is not refused over TCP — it is the
  phone's fact, the reverse of `ui_elements`.
- **A change is pushed to every client** that is listening (`{type:"appearance",
  mode}`), not only to a `feed` subscriber: the UI never subscribes to the D17
  feed (it would read its own actions back), and it is the one that needs this.
  The same mode again is answered and not pushed.
- **The phone sends it** with every accepted `hello` and again whenever the
  phone's appearance changes while connected (`app/ios`, `DeviceSession.dark`).
- **The UI** takes the push on its next tick, draws in that mode from the next
  frame and writes it into the device's settings, so with no phone it keeps
  the last mode (light the first time). A UI that starts after the phone spoke
  asks once with `appearance` and no `mode`. The host forgets it on a restart;
  the setting does not.

### The live stream (#853)

One subscription carries **what the instrument is doing, what the player does,
and when the screen changes**, for the companion app (Learn's steps turning
green by themselves, the live screen) and for anything that reads the
instrument as context: a test, a bench script, a language model. Added
messages; `proto` stays 1. Nothing here touches the audio thread, and with
nobody subscribed none of it is built.

```json
{"type":"live","on":true}
{"type":"live","for":"live","on":true,"watchers":1,"rev":7,"situation":{"transport":{"playing":false,…},…}}
{"type":"situation","rev":8,"patch":{"octave":-1}}
{"type":"situation","rev":9,"patch":{"tracks":{"1":{"has_clip":true,"clips":1,"length_s":4.12}},"transport":{"armed":null}}}
{"type":"action","kind":"key","id":"PLAY","down":true,"seq":41,"t_ms":51230}
{"type":"frame","rev":112}
```

| Request | Reply |
|---|---|
| `live {on?}` | `{type:"live", on, watchers, rev, situation?}`. `on` (a boolean, optional) subscribes **this connection**; while it is subscribed the reply carries the whole **situation** document at `rev`. `watchers` is how many connections are subscribed |
| `situation` | `{type:"situation", rev, situation}` — the whole document now, to anyone, subscribed or not |

A whole-document answer (`situation`, or `live` while subscribed) is the
document at its `rev`, the one every subscriber has at that number, so it
brings the subscribers up to date first, as that 50 ms window's push. Asked
within 50 ms of the last push while anyone else is subscribed, it is the
document at that push, at most 50 ms old, and the next push carries the rest:
a client asking between changes never pushes the others patches faster than
the cap (#1179).

**The situation document** says what the instrument is doing in one object:
the screen (page, header, subpage, slot letter, a menu, HELP), the keys held,
the octave, the transport (playing, recording, position, bar and beat, the
loop, SPEED and REV, the armed and the selected track, a count-in), the tempo
and the click, the eight tracks (has a clip, how many, how long, mute, solo,
level), the four slots (each instrument's ENGINE and SEQ box) and which
instrument is in front, the project (id, name, made by NEW), the input
(source, monitoring, INPUT listening for a sample), the battery, and the CPU
and card flags. **`live-schema.json`** (beside this file, a JSON Schema) gives
every field a sentence in plain words and its unit: it is the glossary a
program or a language model reads to understand the document, and a field
the instrument publishes without a sentence there fails
`emulator/tests/test_live_situation.py`. Its rules:

- **No nulls.** A fact not known is an absent field (a track nobody armed has
  no `transport.armed`), because a merge patch reads null as "remove".
- **Rounded to what a player could tell apart**: the playhead to a tenth of a
  second, levels to two places. A still instrument reads the same twice, so
  nothing is pushed while nothing happens; a rolling tape is about ten patches
  a second.
- **The tracks are an object keyed `"1"`…`"8"`**, not an array: a merge patch
  replaces an array whole, and one new clip would resend all eight.

**The patches.** After the reply, a subscriber gets `situation {rev, patch}`
whenever the document changed: `patch` is an **RFC 7396 JSON merge patch**
(changed members as they now are, removed ones as null, objects merged into,
anything else replaced), `rev` the document's number, one more each push.
Changes are **coalesced to at most one push every 50 ms** (~20 a second) and
**nothing is pushed when nothing changed**. A client applies each patch to its
copy; one that sees `rev` jump (it missed a push) asks `situation` again, or
sends `live {on:true}` again. The `action` and `elements` pushes of D17 (§ The
screen and the player's actions) go to a `live` subscriber too, so one
subscription is enough; a connection with both `actions {on}` and `live {on}`
gets each once. An action may arrive up to 50 ms before the patch that shows
its effect.

**`frame {rev}`** says the frame on the instrument's screen changed. A phone
showing the live screen fetches `GET /screen.png` (`device-os-spec.md`
§ 9.15) when one arrives, and only while it shows the screen, in place of
polling twice a second (companion-app-spec § 10.5). At most one every 200 ms,
the newest; `rev` is the host's count of changed frames since it started, so
only its growth means anything. With nobody subscribed the UI does not even
look (`live {watchers}` below).

**Who publishes.** The host's half — transport, tempo and click, the tracks,
the four slots and the front instrument, the input's source and monitoring,
the flags — is its own (`AudioHost::live_doc`). The UI's half — the screen,
the keys, the octave, the selected track and the count-in, the project, INPUT
listening, the battery — comes from the UI (`emulator/app/ui_feed.py`, the
code the device runs), merged over the host's (RFC 7396 again: the UI may add
members to the host's objects, as `transport.selected`):

```json
{"type":"ui_situation","situation":{"screen":{"page":"SYNTH","header":"SYNTH › ENV",…},"octave":0,…}}
{"type":"ok","for":"ui_situation","watchers":1}
{"type":"ui_frame","rev":5}
```

- **Unix socket only**: both are `bad_request` over TCP, as `ui_elements` is (a
  phone that could send them could describe a screen that is not there).
- `ui_situation` **replaces** the UI's half; a null anywhere in it is refused
  `bad_request` and nothing changes. The UI sends it when its half differs from
  the last it sent (on the element map's cadence: on input, a page change, a
  menu, and every half second idle). The `ok` carries `watchers`.
- `ui_frame` says the drawn frame changed (an optional integer `rev`, the UI's
  own count, is checked and not used). The UI looks — a CRC of the frame —
  only while `watchers` is above 0.
- **`live {watchers}`** is pushed to **every** client when the number of
  subscribers changes: the UI never subscribes, and this is how it learns
  whether anyone is watching.
- **A UI that leaves takes its half with it** (#412's rule): when the
  connection that published it closes, and it had said `hello`, the next patch
  removes the UI's members (as nulls). A publisher that never said `hello`
  (`python3 -m arbutus_os.mock --ui`, the mock's stand-in screen, a test) owns
  nothing: its half stands after it closes, and **it cannot replace a half a UI
  session owns** (answered `ok`, changes nothing).
- **The mock** (`python3 -m arbutus_os.mock`) serves all of it from the real
  host; with no UI attached its stand-in screen publishes its page and a
  frame each time `--page` moves.

---

## Offline mode

`--offline` starts no device. The pipeline is identical; the clock comes from
`render` instead of a callback.

| Message | Payload | Reply |
|---|---|---|
| `render` | `{frames, file?, format?:"f32"\|"wav16"}` | `ok` + `frames`, `song_frame`, `tape_pos`, `file`/`peak` when a file was asked for, and `rt_allocs` under `--rt-guard` |
| `motion` | `{accel?:[x,y,z], gyro?:[x,y,z]}` | `ok`. Sets `arb_host.motion[6]` (g, deg/s) from the next block on, held until the next `motion`; an omitted axis is 0, so `{}` is "no sensor" |

`motion` is offline-only for the same reason as `render`: on a device the host
reads the IMU itself (modulation-spec §13) and no client writes it.

`render` is refused when a device is driving the clock. It renders in blocks of
`--block`, so a long render is ragged exactly the way a live one is.

`shutdown {}` replies `ok` and exits.

`--rt-guard` counts heap blocks gained across every `render_block` (the same
smoke alarm as `arbutus-run --rt-guard`) and reports the running total as
`rt_allocs` on each `render` reply; `-1` means the platform cannot count
(**macOS and glibc count; other platforms return -1**). Offline only the one
thread runs, so nothing else can allocate in between.

macOS counts with `malloc_zone_statistics`; Linux/glibc with a counting
allocator that `--rt-guard` arms at startup and that nothing else in the process
pays for (#52). Both mean the same thing — **live blocks, process-wide, sampled
either side of the block** — so a Mac number and a CM4 number are comparable,
and on both an allocate-then-free inside one block nets to zero and is not
reported.

---

## Command line

```
arbutus-audio [--socket PATH] [--library PATH ...] [--offline] [--no-input]
              [--sr N] [--block N] [--max-block N] [--tape-seconds S]
              [--seed N] [--run-seconds S] [--imu PATH]
              [--tcp-port N] [--token-file PATH] [--quiet] [--rt-guard]
              [--keys PORT] [--keys-socket PATH] [--key-probe]
```

`--keys PORT` (#638, `device-os-spec.md` § 1.3.4) starts the key thread: the
host reads the ATmega's keys on `PORT` (P3: ~~`/dev/ttyAMA5`~~ `/dev/ttyAMA0` on the Zero board since #669, *2026-10-14, #935*; `/dev/ttyAMA5` was the CM4 board's UART5), sounds a chromatic
key itself, and serves the UI **`keys.sock`**, by default beside `--socket`
(`--keys-socket` moves it). That socket's wire is `device-os-spec.md` § 1.3.3
(one JSON object a line, `t` not `type`, no `req`, no replies), not this
file's request/reply. `--key-probe` (bench only) makes a key press carry the
`probe` of § Timing, stamped when its line is parsed. No `--keys`, or an empty
one: no thread and no `keys.sock`, the host as before.

`--tcp-port` + `--token-file` are the § TCP listener; on the device they are
`7401` and `/opt/arbutus/state/token`. Without a token file, or with an empty
one, `--tcp-port` binds nothing and says so on stderr.

`--block` defaults to **128** (`engine-spec.md` §15.8: the device's block and
the emulator's); `--max-block` defaults to `--block`. `--library` is repeatable
and each root is scanned for `<author>/<name>/engine.json`.
`--seed` is `arb_host.rng_seed` (§3.3), for goldens. `--run-seconds` quits after
S seconds, which is how the live smoke test runs unattended.

---

## Session History
- [[logs/2026-10-03-19-04]] — bench routine (#273): `set_parallel` removed; a rack's old `parallel` key loads flat
- [[logs/2026-09-25-01-19]] — cloud routine (**#167**): the `tape_compact` section gains the paragraph saying its three promises — the `started` reply, a fresh client's `hello`, and PLAY cancelling — were true only on paper until the copy stopped holding the tape's lock across the card. Nothing on the wire changed
- [[logs/2026-09-24-09-19]] — cloud routine (#74, C1 step 8): **`tape_compact`** — the medium is append-only, so this is how its dead audio comes back. The keep set is the **UI's** (its clips, its lift buffer, its one-level undo record — the host has never seen the last two), and it is **two messages**: the request replies `started` and the map arrives as a push, because a 2 × ruler medium is ~7 s of copying and SAVE must not wait for one. `state.tape.tracks[t].dead_frames` is the trigger and can only over-report
- [[logs/2026-09-24-01-18]] — cloud routine (#99): § Playing gains the multi-client transport rule — the transport is the host's and a client holds a mirror, sending `transport` only for a change it made and adopting on a **change** in the pushed document rather than on its value. Written here rather than in the emulator because A2's app is the next client
- [[logs/2026-09-23-19-24]] — cloud routine (#74, C1 step 2a): `--tape-dir` and `state.tape.io` documented — the write-behind, the 4 KiB lagging header, `commit_us`, and what `null` means
- [[logs/2026-09-24-00-05]] — bench routine (#74, C1 step 3a): `state.tape.io.repair`, and the sentence a reader needs — **the head of a track comes from its file's header, never from the file's length**. A file longer than its claim is a cut mid-take and is truncated back; one shorter is clamped to the whole frames that are there; one under our name that is **not one of our media** is refused and left alone, and that track simply has no medium. § *What is not built yet* now says plainly that **nothing passes `--tape-dir`**, so the device and the emulator still run the RAM tape
- [[logs/2026-09-24-07-26]] — cloud routine (#74, C1 step 6): `tape_edit` is computed and written **one range at a time**, and the two things that changes at the wire are written down — a source reading the destination track past its `used_frames` is silence, and the reply's `len` is what really landed (a short edit rather than a claim on frames the medium has not got)
- [[logs/2026-09-24-19-38]] — cloud routine (**#163**): `clip_peaks` gains **`source`** (`sidecar` / `audio`), and `state.tape.io` gains `peaks {on, bins_per_range, bin_frames, ranges, hit, miss}`. The numbers are the same either way — the sidecar is a cache — so what a client should take from it is the resolution: ask for one bin per pixel you will draw
- [[logs/2026-10-03-07-15]] — cloud routine (**#157**): § `tape_dir` — where the media live, `move` for SAVE and open-in-place for LOAD; `tape_save` onto the medium's own path replies `in_place`
- [[logs/2026-10-04-15-34]] — mac routine (**#484**): § `tape_dir` — `exists` covers all sixteen names and any entry (a dangling symlink), renames never replace, `move_split` for a failed rollback, any spelling of the current folder moves nothing
- [[logs/2026-10-03-22-08]] — #334: the site's/app's palette, light and dark, following the phone's `appearance`
- [[logs/2026-10-05-08-55]] — bench-2 routine (**#74**): § State — the push waits for a burst of requests to settle (8 ms, 50 ms at most); a reply never waits behind it
- [[logs/2026-10-09-05-17]] — #638 steps 1-4: keys_matrix.hpp, keys_reader state machine, the key ring, arb-keys and --keys; offline-proved
- [[logs/2026-10-11-15-13]] — #591: a take's end claims only its durable prefix; shared ranges wanted at nearest use; renames dir-synced
- [[logs/2026-10-12-14-03]] — arbutus-audio's starting tape folder is /run/arbutus/tape-start; a restart on an unmounted /data creates nothing there
- [[logs/2026-10-13-06-35]] — Live stream: live {on}, situation doc as merge patches (<=20/s), note/capture/project/track actions, frames, schema
- [[logs/2026-10-14-14-30]] — BOUNCE renders as a host job (chunked, file at the end, bounce push); a played key asks CANCEL BOUNCE TO PLAY?
- [[logs/2026-10-15-08-21]] — Library New folder: host library mkdir op (refuses taken, missing parent, dot, engines/), app ⋯ and heading entry
- [[logs/2026-10-15-11-35]] — project.new set by NEW alone; a situation request no longer floods subscribers; digest cache evicts oldest-first
