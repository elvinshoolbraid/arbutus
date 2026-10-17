---
tags: [spec, active, software, engines, sdk]
status: active
related: [[control-inventory]], [[parameter-framework]], [[design-decisions]], [[hollyhock-demo-spec]], [[emulator-spec]], [[software-stack-v1]], [[engine-plugin-api]]
supersedes: [[engine-plugin-api]] (2026-04-22) in full; [[software-stack-v1]] §4 (DSP language), §7 (process model), §12 (plugin summary)
---

# Engine, Rack & SDK Spec — Arbutus-1

Produced 2026-09-15 from a five-round grill (session log: `docs/logs/2026-09-15.md`).
This is the software half of the spec package; `hollyhock-demo-spec.md`, `control-inventory.md`,
`screen-sketches.md` and `ui-aesthetic.md` are the 320×240 half and remain the authority on
what the player sees. Where this document names a screen it names the *behaviour*; how the
screen is drawn is Elfy's, and marked `[TBD-Elfy]`.

Facts are in the linked docs. This file is the contract.

---

## 0. Thesis

The firmware is a **host**. Everything that makes sound, shapes sound, or generates notes is an
**engine**: a self-contained box of compiled code behind a tiny C interface, loadable at
runtime, shareable as a file. The host owns the pipeline, the 8-slot parameter UI, voice
*policy* (the ENV page), the tape, the mixer, and the **rack** — the FX page's ability to hold
several engines in tabs and expose eight chosen parameters from across them as master knobs.

Consequences, each decided in the grill and expanded below:

- One ABI for synth, drum, effect and sequencer engines; the *role* is which ports it uses (§3.1).
- Engines own their own polyphony; the SDK ships it so nobody has to write it (§4).
- Racks, patches and kits are pure data with stable string ids and a format integer (§7).
- Sound design happens on the device. The phone names, files, shares — it does not author (§6, §10).
- No CPU estimate. The device says when it is over budget (§6.8, DDR-042).
- A new C++ audio process is built alongside the emulator, not inside it (§8, §11).

---

## 1. Terms

| Term | Meaning |
|---|---|
| **Engine** | A box: `engine.json` + compiled binary (+ assets) implementing the ABI (§3). Synths, drums, effects and sequencers are all engines. "Primitive" and "plugin" are retired words. |
| **Box** | The folder an engine ships in; zipped as `.arbox` for transport (§3.9). |
| **Host** | The firmware / audio process. Owns everything an engine does not. |
| **Slot** | One of the fixed positions in the instrument pipeline: SEQ, ENGINE, FX (§5). Not to be confused with the 8 *parameter slots* or the lettered *sound slots*. |
| **Rack** | The contents of the FX slot: 1–8 engines in tabs, a master page of ≤8 macros (§6). |
| **Macro** | One master-page parameter driving 1..N engine parameters, each with lo/hi/curve/depth (§6.2). |
| **Tab** | One page of the rack: the master page (tab 0) or one engine's own 8-slot page. |
| **Host block** | The read-only struct every `process()` receives: rate, tempo, transport, ENV page (§3.3). |
| **Soft-key** | A labelled key an engine may declare; forwarded only to sequencer engines (§3.6). |

---

## 2. What this supersedes

`engine-plugin-api.md` (2026-04-22) is superseded in full. Specifically reversed:

| 2026-04-22 | Now | Why |
|---|---|---|
| Four plugin roles with four interfaces | One ABI; role = ports used | One loader, one SDK, one template. Capabilities mix (a vocoder is an effect that takes notes). |
| Centralised `VoiceAllocator`, engines hold voice indices | Engines own polyphony via `PolyHost<Voice>` in the SDK | Uniform `process()` signature; cross-voice state (paraphonic filters, unison, choke) is natural. ENV page contract kept via the host block. |
| Python-first, Cython kernels, C ABI "frozen at M5" | C++ audio process now; Python engines discarded, not ported | Serial-loop DSP is 30× a vectorised one in Python (`board_bench.py --effects`); the port collapses the cost classes. |
| Single process, RT thread on core 3 | **Separate audio process**, UI talks to it over a socket | Crash isolation from third-party engines; no GIL negotiation; the same protocol serves the phone. |
| 128-frame blocks | Host-declared rate, variable `frames ≤ max_block` | Device, emulator and tape bake may run different sizes against the same box. |
| Plugins under `/opt/arbutus/plugins/<role>/*.so` | Boxes under `library/engines/<author>/<name>/` | Manifest + per-arch binaries + assets; the library is one tree. |
| Trust: "all plugins trusted, V2 revisit" | Native code shared; **inspected at upload** (§10) | Elfy's call: efficiency over sandboxing; the burden moves to the site. |

`software-stack-v1.md` keeps its authority on kernel, ALSA, tape storage, memory budget and
milestones; its §4, §7 and §12 carry a banner pointing here. `parameter-framework.md` stays the
UI-side contract for the 8 slots and gains the parameter `type` enum (§3.5).

---

## 3. The engine

### 3.1 Ports and roles

Every engine has up to five ports. A role is which ones it uses; unused ports are NULL.

| Role | audio in | sidechain in | audio out | events in | events out | Fills slot |
|---|---|---|---|---|---|---|
| Sequencer | | | | ✓ | ✓ | SEQ |
| Synth / drum | | | ✓ | ✓ | | ENGINE |
| Effect | ✓ | (opt) | ✓ | (opt) | | FX (as a rack tab) |
| Vocoder-class | ✓ | (opt) | ✓ | ✓ | | FX |

Capability bits in the manifest (§3.8) declare the ports; the host wires only what the slot
provides and drops the rest (an effect that emits events in the FX slot emits into nothing).

### 3.2 ABI — `arbutus_engine.h`

> **ABI 2 (2026-09-16):** the listing below is ABI 1. §15 carries the delta; `audio/sdk/include/arbutus_engine.h` is the authority once cut.

C only at the boundary. C++ (or Rust, or Zig) inside the box is the author's business. Never
reorder fields; append inside `_reserved`; bump `ARB_ABI` on any break. The host supports exactly
one `ARB_ABI` and refuses any other (§7.4).

```c
/* arbutus_engine.h — ABI 1 */
#include <stdint.h>

#define ARB_ABI          1
#define ARB_MAX_PARAMS   8
#define ARB_MAX_SOFTKEYS 4
#define ARB_MAX_BLOCK    2048        /* default; [TBD-Elfy] */

/* ---- capabilities (engine.json "caps" and arb_engine_desc.caps) ---- */
#define ARB_CAP_AUDIO_IN    (1u << 0)
#define ARB_CAP_AUDIO_OUT   (1u << 1)
#define ARB_CAP_EVENTS_IN   (1u << 2)
#define ARB_CAP_EVENTS_OUT  (1u << 3)
#define ARB_CAP_SIDECHAIN   (1u << 4)   /* wants the side[] port; FX page shows a source selector */
#define ARB_CAP_SEQUENCER   (1u << 5)   /* may fill the SEQ slot; receives soft-keys */

/* ---- events ---- */
typedef enum {
    ARB_EV_NOTE_ON = 1,   /* a = note 0..127, b = velocity 0..1            */
    ARB_EV_NOTE_OFF,      /* a = note                                       */
    ARB_EV_CLOCK,         /* one per tick at host.ppqn; a = tick in beat    */
    ARB_EV_TRANSPORT,     /* a = 0 stop, 1 start, 2 locate; b = song beats  */
    ARB_EV_SOFTKEY,       /* a = key 0..3, b = 1 down / 0 up; flags bit0 = SHIFT held */
} arb_event_type;

typedef struct {
    uint32_t frame;       /* offset within this block, 0 ≤ frame < frames  */
    uint16_t type;        /* arb_event_type                                 */
    uint16_t a;
    float    b;
    uint32_t flags;
} arb_event;

/* ---- parameters ---- */
typedef enum { ARB_PARAM_CONTINUOUS = 0, ARB_PARAM_INTEGER, ARB_PARAM_ENUM } arb_param_type;
typedef enum { ARB_CURVE_LIN = 0, ARB_CURVE_LOG } arb_curve;

typedef struct {
    char           id[16];      /* stable snake_case; never renamed once shipped   */
    char           name[8];     /* screen label, UPPERCASE; length [TBD-Elfy]      */
    arb_param_type type;
    arb_curve      curve;       /* mapping of normalised 0..1 onto min..max         */
    float          min, max, def;
    float          step;        /* per encoder detent, in units; 0 = host default   */
    char           unit[8];
    const char* const* labels;  /* ENUM: (max-min+1) strings; otherwise NULL        */
    uint8_t        _reserved[16];
} arb_param_desc;

typedef struct { char id[16]; char label[8]; } arb_softkey_desc;

typedef struct {
    uint32_t         abi;           /* == ARB_ABI                              */
    char             id[64];        /* "author/name"                           */
    char             version[16];   /* semver                                  */
    uint32_t         caps;          /* ARB_CAP_*                               */
    uint32_t         num_params;    /* ≤ ARB_MAX_PARAMS; slots 4–7 may be empty */
    arb_param_desc   params[ARB_MAX_PARAMS];
    uint32_t         num_softkeys;  /* ≤ ARB_MAX_SOFTKEYS; sequencer only      */
    arb_softkey_desc softkeys[ARB_MAX_SOFTKEYS];
    uint32_t         latency_frames;/* reported, not compensated in v1         */
    uint8_t          honours_env;   /* 1 = ENV page values are applied         */
    uint8_t          _reserved[63];
} arb_engine_desc;

/* ---- the host block: read-only, passed to every call ---- */
typedef struct {
    uint32_t sample_rate;
    uint32_t max_block;
    double   tempo_bpm;
    uint32_t ppqn;              /* clock ticks per quarter; default 24 [TBD-Elfy] */
    uint8_t  playing;
    int64_t  song_frame;        /* transport position                          */
    /* ENV page (DDR: per-voice, never over the mixed output)                  */
    float    env_a, env_d, env_s, env_r;   /* s, s, 0..1, s                    */
    uint8_t  env_mode;          /* 0 poly, 1 mono, 2 legato, 3 unison          */
    uint32_t rng_seed;          /* engines seed from here; determinism for goldens */
    uint8_t  _reserved[32];
} arb_host;

/* ---- one block of I/O ---- */
typedef struct {
    const float* in[2];         /* NULL when the slot provides no audio in     */
    const float* side[2];       /* NULL unless ARB_CAP_SIDECHAIN and a source is set */
    float*       out[2];        /* always valid; ALWAYS write both channels    */
    uint32_t     frames;        /* ≤ host.max_block; any value, every call     */
    const arb_event* ev_in;  uint32_t n_ev_in;
    arb_event*       ev_out; uint32_t ev_out_cap; uint32_t* n_ev_out;
} arb_io;

typedef struct {
    void* (*create)(const arb_host* host, const char* box_dir);  /* load assets here */
    void  (*destroy)(void* h);
    void  (*reset)(void* h);                                     /* silence + clear state */
    void  (*set_param)(void* h, uint32_t idx, float normalised); /* 0..1                   */
    float (*get_param)(void* h, uint32_t idx);                   /* 0..1                   */
    void  (*process)(void* h, const arb_host* host, arb_io* io);
    uint8_t _reserved[64];
} arb_vtable;

/* Exactly these two exported symbols, C linkage. */
const arb_engine_desc* arb_describe(void);   /* callable before create(); no allocation */
const arb_vtable*      arb_entry(void);
```

Parameters cross the boundary **normalised 0..1**, as `parameter-framework.md` already
specifies for the UI; the descriptor's `curve` says how the engine maps that onto `min..max`.
Macros (§6.2) therefore operate in one space regardless of the destination's units.

### 3.3 The host block

Every call receives `arb_host`. Engines built on `PolyHost<Voice>` (§4) apply the ENV page
automatically and declare `honours_env = 1`. A from-scratch engine may ignore it and declares
`honours_env = 0`; the host then **greys the ENV page** for that engine rather than showing
knobs that do nothing. `rng_seed` is for goldens (§9.4), not for the tape bake — a bake is a
recording, not a re-render.

### 3.4 Events

Engines see **musical keys only** plus clock and transport. Command keys, arrows, LIFT/DROP,
page keys never reach an engine (DDR-041). Encoder turns arrive as `set_param`, not as events.
Events carry a `frame` offset so sequencers emit sample-accurately within a block. The host
guarantees `ev_in` is sorted by `frame`; engines must emit `ev_out` sorted.

### 3.5 Parameter descriptors

Each of the ≤8 slots follows `parameter-framework.md`. New here: **`type`**.

| type | Encoder | Screen |
|---|---|---|
| `CONTINUOUS` | `step` per detent along `curve` | position bar |
| `INTEGER` | one unit per detent | value label |
| `ENUM` | one entry per detent | `labels[i]` instead of a bar |

`ENUM` is how an engine gets a *selector* on an encoder (waveform, division, direction) without
any new input mechanism. Slots 4–7 left empty fall back to fine-tune of 0–3 per
`control-inventory.md:42`.

### 3.6 Soft-keys (sequencer engines only)

An engine with `ARB_CAP_SEQUENCER` may declare ≤4 labelled soft-keys. In the SEQ slot the host
maps them to physical **1–4** (and SHIFT+1–4 via `flags` bit 0), draws the labels on the
bottom strip, and answers HELP-hold from the same declaration. No other engine type ever
receives them — 1–4 are subpage keys everywhere else (DDR-041). Label length `[TBD-Elfy]`.

### 3.7 Realtime rules

Binding on every engine; checked by review at upload (§10) and by the SDK test harness where
mechanically possible.

1. `process()` performs **no allocation, no locks, no I/O, no syscalls**. Assets load in `create()`.
2. Handle any `frames ≤ max_block`, every call, including 0.
3. Write both output channels every call, even if silent. Mono engines duplicate.
4. Deterministic given inputs and `rng_seed`. No wall clock.
5. `reset()` returns the engine to silence with all state cleared; parameters keep their values.
6. `arb_describe()` is pure: no allocation, no side effects, same answer every call.

### 3.8 Manifest — `engine.json`

```json
{
  "format": 1,
  "kind": "engine",
  "id": "arbutus/comb",
  "version": "1.0.0",
  "abi": 1,
  "caps": ["audio_in", "audio_out"],
  "honours_env": false,
  "latency_frames": 0,
  "arch": { "linux-aarch64": "linux-aarch64/comb.so",
            "darwin-arm64":  "darwin-arm64/comb.dylib" },
  "author": "arbutus",
  "description": "Feedback comb, tuned in semitones.",
  "params": [
    { "id": "tune",     "name": "TUNE", "type": "continuous", "curve": "lin", "min": -24, "max": 24, "def": 0, "unit": "st" },
    { "id": "feedback", "name": "FDBK", "type": "continuous", "curve": "lin", "min": 0,   "max": 0.98, "def": 0.7 }
  ],
  "softkeys": []
}
```

`params` is a **snapshot** of `arb_describe()` so the library browser, the phone and the site can
render an engine without loading its binary. The loader validates the snapshot against the binary
at load and refuses on mismatch — the one duplication in the system, made unable to drift.

### 3.9 Box packaging

**Decided 2026-10-12 (Elfy, D106): one place for every engine.** The stock boxes are copied
from the firmware slot to `/data`'s `engines/` on first boot (the `stock/` seeding's rule) and
from then on are ordinary installed boxes: one search path, listed, uninstallable, reinstalled
from the Community's Stock section. A firmware update updates a stock box that is still
installed and never reinstalls one the owner removed; a RESET restores them; the last engine
of a kind refuses to uninstall (a drum is told from a synth by a `per_note` param, § 15.3). A host whose engine interface changes must ship rebuilt stock
boxes and mark stale ones, the same rule a community box meets.

**Amended 2026-10-13 (Elvin, answering R36, desk 49): which boxes run without asking.** A box
signed by Arbutus loads with no question: the stock boxes, and community boxes vetted in the
community sharing service, which count as signed. A box with no such signature (unvetted from
the Community tab, or copied onto the card by a computer) asks on the instrument before it
first runs, roughly *this code wasn't checked by Arbutus; try the app for pre-verified engines*
with `RUN ANYWAY · CANCEL` (DDR-073's prompt, never a refusal). The stock boxes may also stay in
the firmware slot as the host's fallback (a missing copy, the card away on USB); the card's
copies are what the app and the browser show, move, organise and remove, exactly as installed
ones. *The answering session's call:* the answer is remembered for that box's exact contents,
so a box asks once, not at every boot, and asks again when it changes.

*Review's call, 2026-10-14 (R39, D125; was `[TBD-Elfy]` from #935 and the whole-system review's I14 and G4): the search order.* **The card's copy wins whenever it is there; the firmware slot's stock box is a fallback only while the card is away (USB storage mode) or its copy is missing and was not uninstalled. An engine the owner uninstalled never comes back from the slot (item 2 above; RESET restores it). Trust follows the signature, not the folder (desk 49): a stock box changed on the card is no longer Arbutus's by its hash, so it asks once like any unsigned box, and a firmware update leaves it alone. The shadowing rule below goes with #840.** Why: D106's one place and Elvin's "stock engines can stay in firmware if it makes it easy" together, owners in control (an edit of theirs is never overwritten), and the slot covers G4's restart during an export. The record of the question as it stood:
Today the audio host is given two roots, the firmware slot's `boxes` first and `/data` last
(`arbutus_os/services/arbutus-audio.service`), and `audio/CLAUDE.md` § Defaults gives the
reason: a box in the player's library cannot shadow (be found before, and so replace) a
shipped one, and at equal versions the first root wins. D106's "one search path" removes that
reason, and the slot-as-fallback sentence above keeps a second root; which of the two wins
when both hold a box, and whether the shadowing rule goes, is not written. It waits on
Elvin's answer to the review's G4 (engines on `/data`: signing, USB storage mode, the order of
roots; `docs/reviews/whole-system-review-2026-10-13.md` § 5, the desk's 10-13 line), and is
built with #840.

**As built (#839, 2026-10-14):** the unit gives the host one root, `--library /data`, and the
slot's boxes as `--library-fallback /opt/arbutus/current/boxes`; a fallback box is offered
only for an id the card has no copy of and its record does not name. The record is
`/data/engines/.stock.json` (`installed`: each stock id with the hash of the folder as copied;
`removed`: the ids the owner uninstalled), written by `arbutus_os/engines.py`, which runs
before every audio start (the unit's `ExecStartPre`, nothing written while `/data` is not
mounted) and after a RESET: it copies a stock box the card lacks, moves a recorded one that has
gone missing to `removed` (and never copies it again), replaces an untouched copy when the slot's
differs, and leaves an edited one alone. The last engine of a kind refuses `trash` and `delete`,
of the box or any folder holding it: `NEEDS ONE SYNTH ENGINE` (`DRUM`, `EFFECT`, `SEQUENCER`;
code `last_engine`). The kinds come from `caps` (*run's call*, `audio/CLAUDE.md` § Defaults).
A project that is not open bakes with the same roots, the card and the slot as its fallback
(#1064), so an uninstalled engine refuses there `NEEDS ENGINE <id>` rather than playing from
the slot; over HTTP `last_engine` is a 409 and the app says "Your Arbutus needs one synth
engine."

**As built (#1122, 2026-10-15): the instrument's half.** A `synth/<box>/` or `drum/<box>/`
patch folder whose box the host's `library` list lacks stays in the browser as `NEEDS <ENGINE>`,
dimmed with its patches (`emulator/app/native_library.py`); a pick flashes the host's own
`NEEDS ENGINE <id>` and the default engine skips a marked folder. **The signature on the card
is a hash the device wrote when it checked it** (*run's call*, in place of carrying
`manifest.sig` onto the card: the signature covers the packed payload, not the unpacked
folder, so the UI could not check it there): a box under `<card>/engines/` runs silently only
while its tree hash (`engines.tree_hash`) is `.stock.json`'s `installed` one (the stock box as
copied) or `engines/.signed.json`'s (`boxes`: a `.arbox` the installer verified, written by
`arbutus_os/fw/installer.py`; an unsigned install removes its id). A box anywhere else (the
firmware slot, a build tree) came with the firmware and is signed. Any other box asks when a
browser pick would load it: *VA: NOT CHECKED BY ARBUTUS · PRE-VERIFIED ENGINES ARE IN THE APP ·
RUN IT ANYWAY?* with `1 RUN · 2 CANCEL` (the key cells hold ten characters); CANCEL, or any
other key or turn (D107), loads nothing and leaves what was playing; RUN keeps the answer in
`engines/.answers.json` (`run`: id → hash), so it asks again when the contents change
(`emulator/app/box_trust.py`). The records are the device's word on a card the owner can
write: they save an honest player a second question, not a lock (DDR-073 is never one). A
project or patch restored at load, and the slot fallback's copies, do not ask: only a
browser pick does.
**As built (#1161, 2026-10-15): a document that names the box.** The question is also asked,
once for every unsigned box a document names (`2 ENGINES: NOT CHECKED` when more than one),
before FILE › LOAD opens a project (every slot record and the master rack), before a slot
letter recalls a record, and before SHIFT+3 or SHIFT+4 › RACKS loads a whole rack. CANCEL
keeps the project that was open, or the slot that was playing, so no autosave writes over the
record asked about. *Run's call:* the UI asks, not the host (a host-side refusal would need a
protocol change and a way for the UI to say it was answered). Boot, NEW,
the whole-sound browser's racks and a `.rack` inside a box folder still load unasked (#1205).

A box is a folder in the library:

```
library/engines/<author>/<name>/
  engine.json
  linux-aarch64/<name>.so
  darwin-arm64/<name>.dylib
  assets/            (optional: wavetables, IRs, sample banks)
```

Packed for transport as `<author>-<name>-<version>.arbox` (extension `[TBD-Elfy]`). Missing
architecture for the running host → refuse with "no build for this device" (§7.4).

**As built (D5, #26, 2026-09-19):** the container is **not** a zip — it is
`device-os-spec.md` § 10.1's `.fw` container with `kind: box` (an uncompressed tar of
`manifest.json`, an optional `manifest.sig` and the compressed payload), so a box and a
firmware update are verified, signed and installed by one code path and the device has one
format to read. § 10.1 is its authority. The manifest carries `id: "<author>/<name>"`,
which is the folder it lands in (`/data/engines/<author>/<name>/`). The build holds it
to that: `arb-manifest` refuses a binary whose id is not the `<author>/<name>` it is
built into (as-built 2026-10-13, #675).

---

## 4. Polyphony — engines own it, the SDK writes it

The host has no voice concept; it streams events into whatever fills the ENGINE slot. The SDK
ships `PolyHost<Voice>`:

```cpp
struct MyVoice {
    void start(int note, float vel, const arb_host&);
    void stop();                            // enter release
    bool done() const;                      // fully silent → reclaimable
    void render(float* l, float* r, uint32_t frames, const arb_host&);
};
using MyEngine = arb::PolyHost<MyVoice, /*max_voices=*/8>;
```

`PolyHost` implements note tracking, voice stealing (free → quietest-in-release → oldest), the
per-voice ADSR from the host block (**never over the mixed output** — `emulator/CLAUDE.md`
§ Polyphony), ENV MODE (poly / mono / legato / unison with a fixed detune spread), and the
mix. A vibe-coded synth is the `Voice` struct and one `using`. An engine that needs
cross-voice state (paraphonic filter, choke groups, string models) writes its own loop and may
set `honours_env = 0` — the exception is visible, not silent (§3.3). Voice count is the engine's
choice; the CPU flag (§6.8) is the only budget.

---

## 5. The pipeline — fixed slots

Per instrument (SYNTH or DRUM), unchanged in shape from `hollyhock-demo-spec.md`:

```
musical keys ──► [SEQ] ──► [ENGINE] ──► [FX rack] ──► instrument bus ──► tape / mixer
                 events      audio          audio
```

- The slot decides which ports are wired. `caps` decides which slots an engine may fill; one box
  may be offered in more than one (a note-taking effect appears in FX only; a sequencer in SEQ only).
- No free patch graph. MOD-style routing needs a patching UI this instrument will not have.
- **Sidechain** is the one cross-slot input (§6.6): a rack engine with `ARB_CAP_SIDECHAIN` gets
  `side[]` from a host-level source — tape 1–4 or the mic/line input. Not another instrument's
  bus: only one instrument plays at a time.
- Mixer-page FX (`MIXER › FX`) is the same rack mechanism on the master bus. `[TBD-Elfy]` whether
  it ships in v1 or the slot stays a single engine.

---

## 6. The rack — the FX page

The FX subpage *is* a rack. A rack holding one engine looks like nothing special: the engine's
8 slots on one page, no tabs shown. **SHIFT + ›** reveals an empty second tab — that is how a
player discovers the rack exists (DDR-038). ≤8 engines per rack; the CPU flag governs the rest.

### 6.1 Tabs

| Tab | Page | Slots |
|---|---|---|
| 0 | **master** | the rack's macros (≤8) |
| 1..n | one engine each | that engine's own params, editable directly |

Navigation: **SHIFT + ‹ ›** moves between tabs on the FX subpage (`control-inventory.md`, arrow
table). The strip shows the tab position and the tab's name. Drawing `[TBD-Elfy]`; the
tab-count indicator on the master page is required, its form is not specified here.

### 6.2 Master page and macros

A macro is one slot on tab 0 driving 1..N targets:

```
macro { slot 0..7, name, targets[ { instance, param, lo, hi, curve, depth } ] }
```

- `lo`/`hi` are normalised positions of the **target** — the macro at 0 sends `lo`, at 1 sends
  `hi`. `lo > hi` inverts. This is the PSS-480 "brilliance" principle (DDR-044): the author bounds
  every knob so anywhere on it sounds like something you would keep.
- `curve` ∈ {lin, exp, log, s}; `depth` scales the excursion (negative = invert around centre).
- A macro's *displayed* value is its own 0..1; the targets are hidden behind it.
- Macro slots follow `parameter-framework.md` exactly, including empty 4–7 → fine-tune.

### 6.3 Binding — LIFT / DROP (DDR-039)

On any engine tab: press **LIFT** → the page enters *copy* mode, parameter cells highlight, the
strip reads `TURN A PARAM TO COPY ITS ADDRESS`. Turn one → the address is lifted
(`COMB.FDBK`), shown in the dynamic strip like any lift buffer (DDR-024). Go to the master
tab, press **DROP** → *paste* mode, strip reads `TURN A KNOB TO PASTE`, turn a macro slot →
bound with `lo=0, hi=1, lin, depth=1`. Drop again onto the same slot from another lifted
address → **multi-assign**. LIFT with nothing followed by DROP = cancel (mirrors DDR-013).
HELP-hold on either page explains the mode. Highlight and copy `[TBD-Elfy]`.

### 6.4 Range — set by ear (DDR-040)

On the master tab, a gesture on a bound slot `[TBD-Elfy]` opens the range popup for that
binding (one popup per target; ‹ › steps through targets of a multi-assigned macro). The four
encoders become **LO · HI · CURVE · DEPTH**. While LO or HI is being turned, the target
parameter *is at that value* — you hear the bound — and a live marker (red, per Elfy) shows
where the current macro position lands within [lo, hi]. Close = confirm.

### 6.5 (cut: parallel groups)

Cut from v1 (Elfy, 2026-10-02, desk 5; removed from the code by #273, 2026-10-04): tabs run in
series only, and a box that wants parallel paths codes them inside itself. A rack file that
still carries the old `"parallel"` key loads as flat tabs; the key is ignored and never written.
The number is kept so §6.6 and §6.7 keep theirs.

### 6.6 Sidechain source

An engine tab whose engine declares `ARB_CAP_SIDECHAIN` shows a source selector — cannibalising
one of the engine's unused slots if it has one, otherwise `[TBD-Elfy]`. Sources: `tape:1..4`,
`input` (mic / line, auto-detected). Stored on the rack (§6.7), not on the engine.

### 6.7 Rack file

```json
{
  "format": 1,
  "kind": "rack",
  "name": "WET-IRON",
  "requires": ["arbutus/comb", "arbutus/reverb"],
  "tabs": [
    { "instance": "comb_a", "engine": "arbutus/comb",   "min_version": "1.0.0",
      "params": { "tune": 0.5, "feedback": 0.71 }, "bypass": false },
    { "instance": "comb_b", "engine": "arbutus/comb",   "min_version": "1.0.0",
      "params": { "tune": 0.62, "feedback": 0.71 } },
    { "instance": "verb",   "engine": "arbutus/reverb", "min_version": "1.0.0",
      "params": { "size": 0.4, "mix": 0.3 }, "sidechain": "tape:1" }
  ],
  "macros": [
    { "slot": 0, "name": "GRIT",
      "targets": [ { "instance": "comb_a", "param": "feedback", "lo": 0.2, "hi": 0.95, "curve": "exp", "depth": 1.0 },
                   { "instance": "comb_b", "param": "feedback", "lo": 0.2, "hi": 0.95, "curve": "exp", "depth": 1.0 } ] },
    { "slot": 1, "name": "SPACE",
      "targets": [ { "instance": "verb", "param": "size", "lo": 0.1, "hi": 0.9, "curve": "lin", "depth": 1.0 } ] }
  ],
  "bypass": false
}
```

`requires` is derivable but stored so the refuse check (§7.4) is a set comparison before any
loading starts. A rack stored inside a patch is embedded **by value** — a shared patch is one
file.

`bypass` is stored both on the rack and on each tab (default `false`), so
DDR-049's master/node A/B state survives slot and patch recall.

### 6.8 The CPU flag (DDR-042)

No cost estimate, ever. The audio process counts xruns; when they exceed a threshold in a
window `[TBD-Elfy: N in M s]` the host raises a flag the UI shows as
`CPU UNHAPPY: OVERLOAD` (D103, Play date 1, 2026-10-12: the app's words too; it replaced
`CPU NO HAPPY — EFFECT TOO BIG`, #843) until the rack changes. Measured per-engine
costs are available offline from `arbutus-run --bench` (§9.3) for authors who care.

---

## 7. Formats and versioning

### 7.1 Common rules

- Every file carries `"format": <int>` and `"kind"`. Unversioned files are not read.
- Engine parameters are keyed by **`id` string**, never by index. (The 2026-09-11 `.patch`
  format keyed by position and is discarded — reordering an engine's params silently remapped
  every saved patch.)
- Engines referenced by `id` + `min_version` (semver). A patch or rack never references a
  rack by name — racks are embedded by value.
- `name` ≤ 12 UPPERCASE, picked on the name menu (adjective-plant or -landmark for a patch, D98, #819; `app/name_menu.py`); `slug` for the filename.

### 7.2 Patch (`.patch`, format 1)

```json
{ "format": 1, "kind": "patch", "name": "BIG-CAT",
  "engine": { "id": "arbutus/fm", "min_version": "1.0.0", "params": { "ratio": 0.4, "index": 0.2 } },
  "env": { "a": 0.01, "d": 0.2, "s": 0.7, "r": 0.3, "mode": "poly" },
  "lfo": { "...": "unchanged shape, string-keyed" },
  "fx":  { "format": 1, "kind": "rack", "...": "embedded by value" },
  "seq": { "id": "arbutus/endless", "min_version": "1.0.0", "params": { }, "pattern": { } },
  "octave": 0 }
```

Browsing by engine (SHIFT+1) still takes only `engine`; SHIFT+3 takes only `fx`; the whole-sound
browser takes everything — behaviour per `control-inventory.md` unchanged.

### 7.3 Kit (`kit.json`, format 1)

```json
{ "format": 1, "kind": "kit", "name": "TR-707",
  "pads": { "0": "kick.wav", "1": "snare.wav", "...": "..." } }
```

Explicit pad → file. Sorted-filename order is gone. Per-pad record (hold a chromatic key while
INPUT is armed) writes the file and the pad entry; **SHIFT+STOP** restores the one previous
sample for that pad until the next write (RAM buffer, one deep — the existing SHIFT+STOP undo,
extended to pads).

### 7.4 Refuse policy (DDR-043)

A file that cannot load says exactly why, and loads nothing:

| Condition | Message (copy `[TBD-Elfy]`) |
|---|---|
| `requires` names an engine not in the library | `NEEDS ENGINE arbutus/formant` |
| Engine present but `< min_version` | `NEEDS arbutus/formant ≥ 1.2` |
| Box `abi` ≠ host `ARB_ABI` | `NEEDS FIRMWARE UPDATE` |
| No binary for this arch | `NO BUILD FOR THIS DEVICE` |
| Manifest snapshot ≠ `arb_describe()` | `ENGINE BROKEN` (author error) |
| `format` newer than firmware knows | `NEEDS FIRMWARE UPDATE` |

Older `format` values migrate forward in firmware (fill defaults); the file is rewritten on next
save. Never bypass an engine silently — a bypassed node plays a sound that is not the shared
sound, under the shared name.

---

## 8. The audio process and its protocol

### 8.1 Process model

`audio/` builds `arbutus-audio`, a C++20 process: ALSA (device) or PortAudio/CoreAudio
(emulator) callback, the pipeline (§5), the rack (§6), engine loading (`dlopen`), tape, mixer.
On the device it runs on the isolated core with `SCHED_FIFO` and `mlockall` per
`software-stack-v1.md` §7.3 — the *process*, not a thread inside Python. A crashed engine
takes down the audio process; the UI restarts it and reloads state. The UI never shares an
address space with third-party code.

### 8.2 Control protocol — designed once, public from message one

UI ↔ audio over a local Unix socket; the **same** message set is served over TCP on the LAN to
the companion app later (auth `[TBD]`). Newline-delimited JSON, small and readable; if a
measured need for a binary encoding appears it is a transport change, not a message change.

| Message | Direction | Payload |
|---|---|---|
| `hello` | both | `{proto: 1, abi: 1, firmware}` — refuse on mismatch |
| `load_engine` | → audio | `{slot, id, min_version}` |
| `load_rack` / `load_patch` / `load_kit` | → audio | inline document (§7) |
| `set_param` | → audio | `{slot, tab, idx, value}` |
| `set_macro` | → audio | `{slot 0..7, value}` |
| `bind` / `unbind` / `set_range` | → audio | rack edits (§6.3–6.4); audio replies with the updated rack document |
| `note` | → audio | `{on, note, vel}` |
| `softkey` | → audio | `{key, down, shift}` |
| `transport` | → audio | `{op, position}` |
| `env` | → audio | ENV page values |
| `record` / `bake` | → audio | `{track, ...}` / `{tracks, file, format}` |
| `state` | ← audio | full current document, on request and after any edit |
| `meters` | ← audio | subscription; audio-rate data goes over shared memory, this carries the handle |
| `xrun` | ← audio | `{count, window_s}` → CPU flag (§6.8) |
| `error` | ← audio | `{code, message}` — the §7.4 strings originate here |

Versioned by `proto`. Every message the phone will ever need is one of these or an addition —
never a second API.

---

## 9. The SDK

### 9.1 Layout (inside the repo for now; split to `arbutus-sdk` when a second author exists)

```
audio/
  host/                     arbutus-audio (C++20, CMake, no frameworks)
  sdk/
    include/arbutus_engine.h    ← the only path that must never move
    poly.hpp                    PolyHost<Voice>
    template/                   one buildable, testable engine
      engine.json  src/engine.cpp  tests/golden/  CMakeLists.txt  README.md
    CMakeLists.txt              add_arbutus_engine(<name>) → box folder
  engines/                  stock boxes, each a copy of the template
    arbutus/comb  arbutus/reverb  arbutus/fm  arbutus/sampler  arbutus/drum-sampler  arbutus/endless ...
  tools/
    arbutus-run/            the test client (§9.3)
  tests/golden/             host-level goldens (pipeline, rack, tape)
```

### 9.2 The template is the documentation

`sdk/template/README.md` is written for an agent or a person handed a link: what a box is, the
five ports, the realtime rules, `PolyHost`, how to run the goldens, how to build for both
targets. An engine is done when `make box && make test` passes. Every stock engine is a worked
example; on the sharing site every shared engine is one too.

### 9.3 `arbutus-run` — one tool, three jobs

```
arbutus-run <box> [--notes script.txt] [--in in.wav] [--side side.wav]
                  [--set id=value ...] [--frames N] [--sr 48000] [--out out.wav]
                  [--golden ref.wav --tol -100] [--bench]
```

Loads a box, feeds an event script and/or input audio, writes the output, compares to a golden
within tolerance, or reports µs/block. It is how the audio process is tested without the UI,
what the template's `make test` calls, and what a future labs page would wrap.

### 9.4 Goldens and tolerance

Bit-exactness across Apple clang and Pi g++ is not achievable for float DSP (FMA contraction,
libm). Goldens compare within a tolerance: **default −100 dBFS peak error**, per-test override
in the test manifest. The existing Python `tests/golden/` set becomes the *cross-implementation*
conformance suite for the engines that are rebuilt (§11): same inputs, same tolerance, Python
reference vs C++ box, until the Python path is retired.

### 9.5 Build

C++20, CMake, Apple clang on the Mac, g++ on the CM4, **native builds on each target** for v1.
*Amended (Elvin, #568, 2026-10-05):* except a 512 MB Pi (a Zero 2 W, the 3B+ dev board),
which never compiles: `audio/` is cross-built for it on the Linux bench host with Debian
trixie's own aarch64 GCC (`tools/cross-build.sh`, `audio/cmake/aarch64-trixie.cmake`), and
`arbutus_os/deploy.sh` ships that tree. The goldens do not care which (§ Goldens in
`audio/CLAUDE.md`: −100 dBFS, three compilers already); `ctest` on the Pi is the proof.
`add_arbutus_engine()` emits the box folder with the right `arch` key. ~~Cross-compilation
(`zig cc` or a sysroot) is a later SDK convenience, not a v1 requirement.~~
*Amended 2026-10-13 (#633, from review t568 #7):* the line the code draws is **under 1 GB of
`MemTotal`**, not "512 MB": `deploy.sh` cross-builds for any Pi reporting under 1 000 000 kB,
which takes in the Zero 2 W (512 MB), the 3B+ (1 GB, about 900 MB once the GPU's share is
taken) and a 1 GB CM4 alike; the amendment's "512 MB" named the Zero alone. Cross-compilation
is therefore a v1 requirement for those boards, made with the bench host's trixie GCC above;
what stays a later SDK convenience is a cross-build an outside engine author can run anywhere
(`zig cc` or a shipped sysroot).

---

## 10. Sharing and the site — intent, not a deliverable

- **What is shared:** engines (native code), racks, patches, kits. All of it.
- **Where the risk lives:** at **upload**, not on the device. A sanctioned database, not a
  free-for-all. Checks, in order of hardness: (1) mechanical — the binary's import table
  (`readelf -d`, `nm -D`): no syscalls beyond libc math/mem, no network, no file I/O outside
  `box_dir`; abi/manifest/snapshot consistency; `arbutus-run` goldens pass; (2) AI review
  against `sdk/template/README.md` and the design rules (soft-key labels, param names, ranges
  that sound like something) — a policy, not a guarantee, and the spec says so.
- **`arbutus.audio/labs`** (name illustrative): a document repo — the SDK README, the design rules,
  the worked examples — intended to be handed to an agent by link. No hosted inference. If
  inference becomes trivially cheap the same page grows a tester; the system is designed so
  that growth needs no firmware change.
- Publishing the SDK as its own repo happens when there is a second author. Until then `audio/sdk/`.
  **Amended (Elvin, M7 by voice, 2026-10-15):** the SDK, host, stock engines and emulator open
  sooner, in a clean public repo opened with the outreach to the OP-1 modders (#1232).
- **Engines from the OP-1 mod scene (M7, 2026-10-15).** op1r's engines (GPL-3.0-or-later, like
  the stock engines) come over through one **OP-1 adapter box** that runs them unmodified on
  op1r's own PC harness, with an `engine.json` naming their knobs; the first is op1r's Elements
  reverb port, on today's ABI (#1230). The format both sides could read, and whether Arbutus
  engines should ever run on the OP-1, are questions put to the scene's engine authors, not
  decided here; the ABI's next generation (engine-driven animation) waits for M6. Only engines
  whose source and licence are published are ported.

---

## 11. Migration and scope

- **`audio/` is a new sibling** of `emulator/` and `arbutus_os/`. The emulator is **not
  copied and not touched**: it reads `docs/design/reference/lookalike-model.jpg` and writes
  `../validation/`, so a naked copy breaks, and there is nothing in it v2 needs yet. When the
  UI gets a native-audio client it is a small shim behind `--audio=native` in the existing
  emulator; `main` keeps the Python audio path until goldens pass. The Hollyhock demo is not at
  risk by construction.
- **Thrown away, not ported:** the Python engines (`emulator/engines/*`, `arbutus_os/engines/*`),
  `library/*/engine.json` `class:` pointers, positional `.patch` files, `library/fx/chain`.
  Stock engines are rewritten against the SDK. The library is regenerated against §7.
- **Kept:** the UI, pages, chrome, look editor, `verify.py`, the driver seams, the goldens (as §9.4).
- **`arbutus_os/`** gets a staleness banner when `arbutus-audio` first runs on the Pi, not before.
- **Order of work** (Elfy's token window; sized in the grill as 2–4k lines for host + SDK):
  1. `sdk/include/arbutus_engine.h`, `poly.hpp`, template, `arbutus-run` — an engine builds and
     tests with no host.
  2. `arbutus-audio` on the Mac: pipeline, rack, loader, protocol; Python goldens replayed through it.
  3. Stock engines: comb, reverb, biquad, FM, sampler, drum sampler, endless — each a box.
  4. `--audio=native` shim in the emulator; parity; then the Pi.

---

## 12. Rules stated without asking (veto by editing)

- A rack holds ≤ 8 engines; tabs are flat and run in series; no rack inside a rack.
- The host supports exactly one `ARB_ABI`.
- Engines report `latency_frames`; the host does not compensate in v1 (Elfy: let latency happen).
- No engine ever receives command keys except sequencer soft-keys (§3.6).
- Existing `.patch`, `library/fx`, and drum-kit folders are not migrated; regenerated.
- The phone renders racks, patches and kits read-only for browsing; it never edits one.
- Undo for pad overwrite is one deep, RAM-resident, invalidated by the next *write* — playing
  the pad does not invalidate it.
- 48 kHz float32 stereo internally, as `software-stack-v1.md` §14 already fixes.

---

## 13. Unresolved questions

1. `[TBD-Elfy]` screen behaviours flagged above: tab indicator on the master page; LIFT/DROP
   highlight; the gesture that opens the range popup;
   sidechain selector when an engine has all 8 slots used; CPU-flag copy and thresholds;
   param `name` and soft-key label lengths; `ppqn`; `.arbox` name; whether MIXER › FX is a rack in v1.
2. Companion-app auth over TCP (§8.2) — out of scope until the app exists.
3. Whether `PolyHost` unison detune spread is fixed or an ENV-page parameter.
4. Where `arbutus-run --bench` numbers are published, if anywhere (they are not used on-device).

---

---

## 14. As built (2026-09-15/16) — where the implementation settled the spec

Stages 1–4a of §11 were built by four sequential agents; each reported the
`[TBD-Elfy]` items it consumed and every deviation. This section records the
facts that now differ from or sharpen the text above. **Where §3.2's header
listing and `audio/sdk/include/arbutus_engine.h` disagree, the header is
authoritative** — the listing was a sketch; the header is pinned by
`audio/tests/test_abi.cpp` from both C and C++.

### ABI (§3)
- Header gained an include guard, `extern "C"`, `ARB_EXPORT` (boxes build
  `-fvisibility=hidden`), `ARB_CAP_NAMES` (the one mapping between manifest
  strings and `caps` bits), an `arb_env_mode` enum, and documented conventions:
  `create()` returns NULL on failure; the host zeroes `*n_ev_out`; `ev_in` is
  sorted and in range; `out[]` may alias `in[]`.
- **`load_asset(h, key, path)`** added to `arb_vtable` inside `_reserved`
  (`ARB_ABI` stays 1): control-thread only, before the engine enters the graph,
  may allocate and do I/O; NULL = takes no assets. `arb_engine_desc` declares
  its asset keys (`num_assets`, `asset_keys[3][16]`, `ARB_MAX_ASSETS = 3`);
  they appear in `engine.json` as `"assets"` and are validated like every
  snapshot field. Patches and kits carry `"assets": {key: path}`; a kit is one
  asset, `kit` → the resolved `kit.json`. Refusals: `UNKNOWN ASSET <key>`,
  `ASSET NOT FOUND <key>`. This supersedes §7.3's implicit delivery and
  emulator DDR-037's "content, not parameters" now has an ABI home.
- `_reserved` sizes were chosen without accounting for struct padding; nothing
  was reordered. `arb_engine_desc` = 904 bytes, `arb_vtable` = 112,
  `offsetof(load_asset) = 48`. Know this before the next ABI bump.
- Defaults taken: `ARB_MAX_BLOCK 2048`; `name[8]` / `label[8]` (widening is an
  ABI bump); `ppqn 24` — but sequencers step on frames against `tempo_bpm`,
  because 24 ppqn is coarser than a 1/32T step.
- UI key events are block-quantised (≤ 10.7 ms at 512/48 k); sequencer-emitted
  events are sample-accurate. Host-generated clock/transport are merged back
  in *after* the SEQ engine so a sequencer cannot swallow the clock.

### PolyHost (§4)
ADSR is snapshotted at note-on (turning REL does not alter a ringing tail);
segments are linear, attack climbs from the current value (matches the
emulator's `ADSREnvelope`, including the one-sample full-scale zero-attack);
mono returns retrigger, legato returns do not; unison is fixed at 4 voices (spread no longer fixed — §15.10),
±6 cents, `1/√4` gain (`arb::kUnison*` in `poly.hpp`); optional
`Voice::set_detune` / `Voice::reset` found by `requires`.

### Rack (§6)
- Parameter values in §6.7 documents are **normalised 0..1**.
- Curves: `exp = x²`, `log = √x`, `s = x²(3−2x)`; `depth` scales about the
  centre of `[lo, hi]`, clamped 0..1.
- CPU flag: **3 xruns in 2 s** (`kXrunWindowS`); hot-swap crossfade 256 frames.
- Screen choices made by stage 4a, all revisable, listed with file+function in
  `emulator/notes/audio-host.md` § Native audio: tab row of small boxes (`M`, `1..n`,
  `+`); one-engine rack shows a dim `›` only; copy mode = 1 px green ring on
  every turnable cell; strip copy is this spec's own wording; **the range
  popup opens on SHIFT+DROP** on the master tab (the one free combination on
  those keys — open question); ‹ › steps targets inside the popup; FX opens on
  the engine for a one-engine rack, master otherwise; an unbound macro slot
  renders `—`.

### Protocol (§8.2)
Correlation field is **`req`**, not `id` (`id` is an engine). Added messages:
`unload_engine`, `arm`, `set_clips`, `instrument`, `tempo`, `panic`, `reset`,
`render`, `library`, `rescan`, `shutdown`, and rack edits `add_tab` /
`remove_tab` / `set_tab_engine` / `set_sidechain` (`set_parallel` was removed
with parallel groups, #273). `meters`
carries eight floats at ≤ 30 Hz in JSON; the shared-memory handle is deferred
until measured. `hostlib/` holds the loader shared by `arbutus-run` and the host.

### Engines (§9, DDR-044)
`osc` has two parameters (the Python has one timbre knob; nothing invented).
`fm`: the Python's "index" was **not** the modulation index (phase in cycles →
β = 2π·index; the 0…10 knob was β 0…63); INDEX is now β over 0…6, default 2.2,
RATIO an ENUM of 0.5/1/1.5/2/3/3.5/5/7. `drum`: four global knobs, no per-pad
parameters, ignores NOTE_OFF, `honours_env = 0`. `sampler`: LOOP enum over
START…END, linear interpolation on play. Both combs block DC (`satcomb` inside
the loop). `endless` soft-keys: `REC REST UNDO CLEAR` (CLEAR is a choice).
Python bugs found and left in the Python path: `GATE` dead in
`app/sequencer.py`; the reverb's vectorised comb aliases when a block exceeds
the shortest delay line; the noise lowpass restarted every block.

### Native mode (§11, stage 4a)
`emulator/run.sh --audio=native`; the default path is unchanged (206 tests).
Parity gate: `osc → biquad → reverb`, one scripted session both paths,
**−138.5 dBFS** peak error (tolerance −60). Tape in native mode carries clip
geometry only — a take's samples live in the host and TRACK draws a flat
block; `src_offset` on `app/tape.Clip` is the deferred fix. A patch's
`seq.pattern` and `lfo` are carried but not applied (no host LFO; `endless`
has no pattern asset yet). The SEQ slot arms on the instrument active at arm
time (DDR-034 not followed natively — `softkey` carries no instrument).
Stage 4b (the Pi) is unbuilt: `audio/PI.md`.

### Still Elfy's — by ear
Unison spread; mono-return retrigger; live-vs-snapshotted release; exp curve
steepness; depth-about-centre vs about-lo; xrun threshold; SHIFT+DROP as the
range gesture; FX opening tab; ENGINE-page scope replacement; native take
waveform on TRACK; FM index envelope; a TONE knob for `osc`; SAW vs SINE
default; drum DECAY default; CLEAR as the 4th soft-key; `name[8]`.

---

## 15. ABI 2 and the v1 additions (2026-09-16)

Decided in the OP-1 gap grill (`docs/logs/2026-09-16.md` session 2; audit in
`op1-gap-audit.md`). ABI 1 has shipped to nobody, so this is the one cheap moment to bump
it cleanly instead of growing `_reserved`: **`ARB_ABI` becomes 2**, `load_asset` moves out of
the reserved bytes into a real field, and every reserved size is recomputed with padding
accounted (pinned by `audio/tests/test_abi.cpp`). The eleven stock boxes rebuild without
source changes except `arbutus/drum` (per-pad) and the modulator boxes (new). Goldens are
untouched. The SDK template README documents every item below — that README *is* the
documentation of the ABI for anyone writing a box.

### 15.1 Header delta

```c
#define ARB_ABI          2
#define ARB_MAX_OUTPUTS  4            /* modulator control outputs            */

#define ARB_CAP_MODULATOR   (1u << 6) /* fills a MOD-rack tab; emits ctl_out   */

/* events (appended; existing values unchanged) */
ARB_EV_CC,        /* a = controller 0..127, b = 0..1                         */
/* ARB_EV_BEND was here — removed in B2 (§15.10): bend reaches engines as arb_host.pitch_st */

typedef struct { char id[16]; char name[8]; uint8_t bipolar; uint8_t _pad[7]; } arb_output_desc;

/* arb_param_desc gains, inside what was _reserved: */
uint8_t per_voice;    /* 1 = engine applies keys.VEL / keys.NOTE routes per voice (modulation-spec §8) */
uint8_t per_note;     /* 1 = value is per pad; set with set_note_param (§15.3)                          */

/* arb_engine_desc gains: */
uint32_t         num_outputs;                 /* ≤ ARB_MAX_OUTPUTS; modulators only */
arb_output_desc  outputs[ARB_MAX_OUTPUTS];

/* arb_host gains: */
float    motion[6];   /* ax ay az gx gy gz, control-thread sampled ~200 Hz; zeros without an IMU */

/* arb_io gains: */
float    ctl_out[ARB_MAX_OUTPUTS];  /* modulators write one value per output per block */

/* arb_vtable, in full: */
typedef struct {
    void* (*create)(const arb_host*, const char* box_dir);
    void  (*destroy)(void*);
    void  (*reset)(void*);
    void  (*set_param)(void*, uint32_t idx, float normalised);
    float (*get_param)(void*, uint32_t idx);
    void  (*process)(void*, const arb_host*, arb_io*);
    int   (*load_asset)(void*, const char* key, const char* path);          /* was in _reserved */
    void  (*set_note_param)(void*, uint8_t note, uint32_t idx, float v);    /* per_note params; NULL = none */
    int   (*format_param)(void*, uint32_t idx, char* buf, uint32_t n);      /* optional readout; 0 = use host default */
    void  (*set_voice_mod)(void*, uint32_t idx, uint8_t src, float depth);  /* per_voice params; src = ARB_VSRC_* */
    uint8_t _reserved[64];
} arb_vtable;

typedef enum { ARB_VSRC_VEL = 0, ARB_VSRC_NOTE } arb_voice_src;
```

*As built (stage A; audit Q1 2026-09-18):* `format_param` is **`(h, idx, normalised, buf, n)`**
— the value is passed in, so the call is pure and the control thread makes it while
`process()` runs (`SlotState::to_json`). The vtable above keeps the pre-build shape;
`audio/CLAUDE.md` § Defaults records the deviation and `sdk/template/README.md` documents
the built one.
*2026-10-13, #633 (module review m-engines-mod-seq #11):* "pure" was not true of every box:
`arbutus/pattern`'s `STEPS` readout at 0 (`AUTO <n>`, § 15's sequencer table) read the
instance's current pattern length, which the audio thread changes inside `process()` (step
entry, undo), while the control thread formatted. *Closed by #864 (2026-10-13):* the box now
publishes the length through a relaxed `std::atomic<int>` at the end of every `process()` and
`load_asset()`, and `format_param` reads that, so the contract holds without exception:
`format_param` writes nothing, may be called from the control thread while `process()` runs,
and reads only the passed-in value or state the box publishes atomically. The readout may lag
the length by one block. `ctest -R pattern_format_threads` holds it under ThreadSanitizer.

Manifest (`engine.json`) mirrors every new field: `"outputs": [...]`, `"per_voice"` /
`"per_note"` on a param, `"caps": [..., "modulator"]`. The snapshot check (§3.8) covers
them.

### 15.2 Roles, updated

| Role | audio in | side | audio out | events in | events out | ctl out | Fills |
|---|---|---|---|---|---|---|---|
| Sequencer | | | | ✓ | ✓ | | SEQ |
| Synth / drum | | | ✓ | ✓ | | | ENGINE |
| Effect | ✓ | (opt) | ✓ | (opt) | | | FX tab |
| **Modulator** | | (opt) | | (opt) | | ✓ | **MOD tab** |

`arbutus/live-input` is a synth-role engine whose `create()` opens the host's input
stream (`PROTOCOL.md` gains `input_source`); it has `audio_out` + `events_in` (ignored) and
a `SOURCE` ENUM `mic / line / usb / resample`. `resample` is the master bus **minus the
armed track**. It is how input reaches the racks, the tape and the mixdown (DDR-063).

*As built, 2026-09-17 (stage G).* A box cannot open the device — the host owns it — so the
box declares `sidechain` as well and the host hands the selected input to the **ENGINE
slot's `side[]`**, the port an FX tab's sidechain already arrives on. An ENGINE-slot box
that declares `ARB_CAP_SIDECHAIN` is asking for the live input; nothing else does. SOURCE
is mirrored both ways with `input_source`, so the knob and the message are one value and a
patch carrying SOURCE selects it as it loads. `resample` is one block late (the instrument
renders before the mix it taps) and is minus the armed track, **the instrument bus, the
click and the monitor** — see `host/PROTOCOL.md` § `input_source` and `audio/CLAUDE.md`.

### 15.3 Per-pad parameters (`per_note`)

A drum engine flags params `per_note`; the UI edits the **last-hit key** and sends
`set_note_param(note, idx, v)`; `set_param` on a `per_note` slot is ignored. `arbutus/drum`
v2: `START · END · PITCH · LEVEL` / `PLAY (one-shot/gate/loop) · REV · PAN · CHOKE (off/1–4)`,
all per pad; DECAY moves to the ENV page as a per-pad AD (`honours_env = 1`, S and R
unused and greyed). Pad settings are stored **in the kit** (§7.3 gains `"pads": {"0":
{"file": "kick.wav", "params": {...}}}`), because they describe the sample. LIFT on a pad
then DROP on another copies file + params (DDR-039's fourth payload type,
`modulation-spec.md` §10).

### 15.4 Sequencer and pattern additions

- Each instrument owns its own SEQ slot (engine + pattern + armed state); only the
  active instrument's runs (DDR-034 amended). The host keeps both; `instrument` switches
  which one receives clock.
- A pattern is an **asset** (`pattern` key on the seq box, `load_asset`), so a patch's
  `seq.pattern` applies natively and pattern files are the sequencer's browser items
  (DDR-037).
- `arbutus/endless` gains `GATE` (10–100 % of the step) and `PATTERN` (ENUM of rhythm
  masks, SHIFT+turn rotates) on its SHIFT row; tied notes = hold keys + › while entering.
- New boxes `arp`, `pattern`, `euclid` (`v1-spec-package.md` for shapes; ranges by ear).

### 15.5 Pipeline, updated

```
keys ──► [SEQ] ──► [ENGINE] ──► [FX rack] ──► instrument bus ──► tape tap ──┐
                       ▲              ▲                                      │
                 [MOD rack] ──────────┘  (routes; modulation-spec §5)         │
                                                                             ▼
   tape 1..8 ──► per-track EQ ──► mixer ──► master FX rack ──► drive ──► comp ──► level ──► out
```

- 8 tracks (`tape.hpp` already sizes by option; `set_clips`/`arm`/`record` take 0..7).
- **Overdub is the only record mode** (DDR-056): `record` writes instrument bus **plus the
  track's own playback** into the take; on stop the host returns the new clip and the UI
  splits/absorbs the clips it covered. Overlapping clips never exist in a track list.
- Per-track EQ is host DSP (three biquads + HP/LP per track; DDR-062), messages `eq
  {track, low, mid, high, hp, lp, mid_f, mid_q}`. The master rack is the §6 rack on the
  master bus, message `load_rack {instrument: "master"}`.
- MASTER: `mixer {master, comp, comp_rel, drive, drive_tone, bal}`; the stage-2 `limiter`
  becomes `comp`.
- Host virtual params on the instrument bus: `host.pitch` (±BEND st), `host.level`,
  `host.pan` — targets for routes and for the bend gesture.
  *As built (B2/N3; audit Q1 2026-09-18):* `host.pitch` spans a **fixed ±24 st**
  (`modulation-spec.md` §5 is its home; `host/rack.hpp` `kPitchSpanSt`). BEND scales the
  bend *gesture* only; the two add in `arb_host.pitch_st` (§15.10). "±BEND st" here was
  the stale reading.
- Sidechain sources gain `self` (this instrument's bus) and `master`.

### 15.6 Protocol additions (`proto` 1)

**Retitled 2026-09-19 (#62, Elfy 2026-09-18): there is no `proto` 2.** Every
row below is a *compatible addition* — a message the host did not serve before,
or a field on one it did — and the host, `PROTOCOL.md` and the emulator have
always served them as `proto` 1. A `proto` bump is for a change that breaks an
older client, and none of these is one. `bend` is the exception that proves it:
it replaced `ARB_EV_BEND` **before any release**, which is an ABI question, not
a protocol one (§15.10).

| Message | Payload | Built |
|---|---|---|
| `waveform` | → `{asset}` ← `{bins:[…]}` peak envelope for the ENGINE page (sampler, drum pad, live-input) | stage D |
| `clip_to_asset` | → `{track \| "all", start_frame, len}` ← `{path}`; `"all"` renders through the mixer | stage E2 / J1 |
| `route` / `unroute` / `set_route` | MOD rack edits, reply = updated `mod` document | stage N3 |
| `set_note_param` | `{slot, note, idx, value}` | stage C |
| `eq`, `load_rack {instrument:"master"}` | §15.5 | stage F1 |
| `input_source` | `{source}` for `live-input` and the INPUT page | stage G |
| `bend` | `bend {value}` is ramped by the host into `arb_host.pitch_st` (§15.10) | stage B2 |
| `tempo {bpm, link, base_bpm}` | LINK (DDR-059): with `link` the host scales tape speed with BPM | stage E1 |
| `library {op: rename \| move \| delete \| trash}` | file ops for the browser and the app | **D2, #23** — `PROTOCOL.md` § `library {op}` |

`capture` (live master to file) is **not** added: recording the master into a track via
`live-input` RESAMPLE is the live mixdown, and `bake` is the export.

#### 15.6.1 v1.5, not v1 (#62)

Moved out of the table above by Elfy's 2026-09-18 decision, beside
`arbutus/midi`. None of the three is a breaking change either, so they will
also arrive as `proto` 1:

| Message | Why it waits |
|---|---|
| `cc` | MIDI CC → `ARB_EV_CC`. MIDI in exists on the device (P5, #21) but nothing maps a CC to anything yet; the mapping is the feature, not the message |
| `battery` in `state` | the host does not read the battery — `device-os-spec.md` § 6.6 gives it to the UI from sysfs, and the app reads `GET /status` until this lands (#22 built the gauge driver) |
| `motion` in `state`, as a subscription | `arb_host.motion[6]` is fed by `--imu` (#22) and consumed by `arbutus/motion`; reporting it to a *client* is a second job with no screen asking for it |

#### 15.6.2 TCP, which is not a message change (D2, #23)

`device-os-spec.md` § 9.4: the same message set on TCP 7401 behind the one
device token (DDR-065). `hello` gains an optional `token`, `unauthorized`
joins the error-code list, `shutdown` and `render` are refused over that
transport and `meters` is capped at 10 Hz. Nothing a `proto` 1 Unix-socket
client sends or reads changed, which is why it is not a bump either.

### 15.7 Realtime rules, addition

7. Parameter changes are **smoothed** over at least one block toward the target
   (`arb::Smoothed<float>` in `params.hpp`). A step is a choice, not a default.
8. **The output does not depend on the block size.** Given the same inputs, the same
   `rng_seed` and the same event frames, a box renders the same samples whether the host
   hands it 512 frames, 128, a prime or 1 — this is rule 4's determinism applied to rule
   2's "any `frames ≤ max_block`", and it is what makes §15.8's three block sizes one
   instrument. Checked by `audio/tests/ragged.sh` (the 24 `ragged_*` tests), whose own
   negative control is `ragged_catches_block_dependence`. **Added 2026-09-23 (#56)**: the
   rule had lived only in that script's header, and the script could not fail, so two
   stock boxes broke it undetected — they read their envelope once per `process()` call,
   which is one value per *block* and therefore a different sound at every block size
   (16 and 60 dBFS). `arb::kCtrlStep` in `sdk/poly.hpp` is the SDK's answer: a running
   value is read on a fixed grid of the voice's own frames since note-on, never once per
   call. **A parameter glide is outside it** (as-built 2026-10-13, #675, a run's call):
   `set_param` lands only between blocks, so a knob moved under two block sizes is two
   different inputs, and a rule-7 ramp advanced once a block is allowed to step at the
   block; rule 8 is about a box whose parameters hold still, which is what `ragged.sh`
   renders. The template README says so beside rule 8.

### 15.8 Block size

**The host's default is 128 frames, the emulator runs it, and since 2026-09-23 the
device runs 64** (L4, #141: `arbutus-audio.service` passes `--block 64`, and
`device-os-spec.md` § 1.3.2 is why). `--block` is therefore a shipped setting and not
only a benchmarking flag.

**That difference is deliberate and it is allowed to exist only because of §15.7 rule
8.** The block a key press waits out is quantisation — one block, uniform phase — so it
is the last software term in the key-to-sound budget, and halving it is the only thing
that halves it (`latency-budget-2026-09-19.md` L4). Nothing in an engine may notice:
every box reads its running state on `arb::kCtrlStep`, a grid of the voice's own frames,
and all four block sizes are bit-exact (#56). **A box that sounds different on the
device from the emulator is that rule broken**, and `ragged.sh` is where to look.

`ragged.sh` covers the **boxes**. It does not cover the host's own master strip, and
there is one term there that does depend on the block: MASTER COMP's attack/release
coefficient is per-frame but applied once per block (#67, `audio/CLAUDE.md` § COMP REL),
so its time constants are `tc × block` and they **halve on the device** — the one place
the two blocks are audibly different. Off by default, so no golden and no conformance
test moves; the knob is the player's.

Modulation is block-rate (`modulation-spec.md` §7) — that is the **host** driving a
modulator's outputs, and it is not a licence for an engine's own state to depend on how
long a `process()` call is (§15.7 rule 8).

### 15.9 Unresolved (added)

- `name[8]` was kept in ABI 2 (widening is free now — Elfy: say if 8 is too short before
  the header is cut).
- Whether `set_voice_mod` should also carry a curve.
- `ARB_MAX_OUTPUTS` = 4 is the MOD-page decision (`modulation-spec.md` §2), not a DSP limit.
- **§14's "unison is fixed" still reads as written** and is superseded by §15.10 (DETUNE is
  a knob on the ENV SHIFT row). Left standing rather than edited, because §14 is a record
  of how stages 1–4a settled the ABI; read §15.10 as the current answer. (Carried forward
  2026-09-18 from the build handoff's open-question list, which was deleted with it.)

### 15.10 B2 as built — glide, detune, bend (2026-09-17)

DDR-052's ENV SHIFT row is common to every engine through the SDK, implemented once in
`PolyHost`. `arb_host` gains four named fields after `env_r`: `env_glide` (s), `env_detune`
(ct, unison spread outermost to outermost), `bend_range` (st) and `pitch_st` (st) — the
instrument's pitch offset **resolved by the host** as bend position × `bend_range` +
`host.pitch`, so an engine has one pitch mechanism. **`ARB_EV_BEND` is removed** (it would
have been a second one); the `bend {value}` protocol message replaces §15.6's forwarding, and
the host ramps the position over GLIDE. `arb_host` is now 136 bytes, `_reserved` still 46.
This supersedes §14's "unison is fixed": the voice count and gain are, the spread is
`env_detune` (default 12 ct, the old ±6). `PolyHost` finds a new optional
`Voice::set_pitch(cents)`, called before every render with unison + glide + `pitch_st`;
glide is mono/legato only, linear in semitones; `pitch_st` is ramped across each block.
`host.pitch`, `env.glide` and `env.detune` are served targets. Engines not on PolyHost may
read `pitch_st` directly. Details and defaults: `audio/CLAUDE.md`, `sdk/template/README.md`.

**Pre-release ABI rule (Elfy).** ABI 2 has not shipped outside the repo, so a struct does
not grow by appending into `_reserved`: new fields go in as named fields in their logical
place, the layout is recomputed with padding accounted for, every offset is re-pinned in
`audio/tests/abi_layout.h`, and every box is rebuilt. `ARB_ABI` stays 2 until something has
shipped; the `_reserved` tails are post-release headroom.

### 15.11 D as built — drum v2, transient, sampler v2, waveform (2026-09-17)

No ABI change. **`arbutus/drum` 2.0.0**: §15.3's eight params, all `per_note`, as
listed. **Kit shape**: a pad is `{"file", "params"}` (normalised, keyed by id,
absent = default); the v1 string `"0": "kick.wav"` is still read, so kits stay
**`format` 1** and the library's four kits were not regenerated. The host reports
every loaded pad in the v2 shape with every `per_note` param filled — that
document *is* the save (no `save_kit`, as there is no `save_patch`) — and replays
it into every rebuilt engine. `set_note_param {pad | note, idx | id, value}`
(note = pad + 53) and `copy_pad {from, to}` are protocol messages. PLAY gate and
loop stop on NOTE_OFF, and a choke fades the group, over 5 ms. **Deviation from
DDR-053:** the AD is the host block's `env_a`/`env_d`, global and latched per
hit, not stored per pad — the eight slots are already the per-pad list; the ENV
page edits it for the last-hit pad. The DRUM instrument's ENV defaults are
a 0 s / d 4 s, which is v1's sound (the v1 golden holds). **`arbutus/transient`**:
envelope-difference shaper, ATTACK · SUSTAIN · SPEED · MIX / OUTPUT, ±12 dB,
default bit-exact passthrough. **`arbutus/sampler` 2.0.0**: START · END · LOOP-IN ·
LOOP-OUT / ROOT · TUNE · REV · FADE. LEVEL is removed and v1's 0.8 kept as a
constant trim; the host drops param ids an engine does not declare, so v1
patches load. The loop plays while held, then on to END; LOOP-IN = LOOP-OUT
(default 1/1) is no loop; FADE is 0–50 % of the loop. `format_param` gives
seconds and note names. **`waveform`**: min/max per bin of the mono mix,
computed and cached on the control thread, addressed by path, slot + asset key,
or kit pad. **FX tabs take `assets`** like MOD tabs. Defaults:
`audio/CLAUDE.md`; messages: `audio/host/PROTOCOL.md`.

### 15.12 E1 as built — the eight-track tape host (2026-09-17)

No ABI change; `proto` gains `clip_to_asset`, `tempo {link, base_bpm}` and a
`take` push. **Eight tracks** (DDR-060): `arm` / `record` / `set_clips` /
`mixer` / `bake` / `clip_peaks` / `tape_edit` / `tape_save` / `tape_load` take
0..7, the sidechain serves `tape:1..8`, `meters.tracks` is eight long, and the
`state` document carries eight tracks. A medium is **allocated on first use** —
eight 8-minute stereo float32 tracks is 1.5 GB eagerly, which the CM4 has not
got, and DDR-060 reveals bank B only once it holds a clip, so the memory follows
the same rule. An unrecorded track reads as silence everywhere, never an error.

**Overdub is the only record mode** (DDR-056). The record tap writes the active
instrument bus **plus that track's own rendered playback**, so a take is what
was heard. On commit the take **absorbs** what it covers — the crossed clip is
split, the covered part dropped (it is inside the take) and the remainders kept
— so a track's list never overlaps; `set_clips` stays literal, because that list
is the UI's. The finished clip also goes out as its own `take` push rather than
leaving the UI to diff two clip lists.

**LINK** (DDR-059) is `tempo {bpm, link}`, on by default. The tape plays at
`bpm / base_bpm`, where `base_bpm` is the tempo the medium is written at and
moves only when a project says so. A take recorded while the factor is not 1 is
**resampled by it on the way in**, so the medium always sits at `base_bpm`, the
clip covers the timeline it was played over, and it plays back as performed. At
factor 1 the tap is the straight copy it always was, sample for sample. LINK off
returns the tape to its own rate — a second song at a new BPM over the first,
which is what the off state is for. SPEED and REV multiply on top; a take rides
the link factor alone.

**`clip_to_asset {track | "all", start_frame, len}`** renders a window of the
timeline to a **16-bit WAV** (the library rule) and replies with its `path`: one
track renders its own clips at their gains, `"all"` renders through the mixer.
Without `file` it lands in `<library>/synth/sampler/` and the library is
rescanned, so DDR-030's drop-on-instrument has a path to hand the sampler.
Defaults: `audio/CLAUDE.md`; messages: `audio/host/PROTOCOL.md`.

### 15.13 F1 as built — the mixer host (2026-09-17)

No ABI change; `proto` gains `eq` and six fields on `mixer`. §15.5's chain
below the tape is now real, in its stated order:

```
tape 1..8 -> per-track EQ -> mute/solo + gain/pan -> mix (+ instrument bus)
          -> master FX rack -> DRIVE -> COMP -> LEVEL·BAL -> out
```

**Per-track EQ** (DDR-062) is host DSP in `audio/host/mixer.hpp`: five RBJ
biquads per track per channel — HP, LP, a LOW shelf at 200 Hz, a MID peak at
MID-F with width MID-Q, a HIGH shelf at 3 kHz — driven by `eq {track, low,
mid, high, hp, lp, mid_f, mid_q}`. The values are **real units, not
normalised** (dB and Hz): an EQ is the host's own, not an engine, and the page
prints what it sends. Every field is optional, so one encoder is one message.
Flat is **bit-exact bypass**, HP at 20 Hz and LP at 20 kHz are off rather than
filters at the band edge, and the EQ sits strictly between the track and the
mix — the medium, the overdub tap, sidechain `tape:n` and a one-track
`clip_to_asset` never see it.

**The master rack** is the §6 rack on the mix, addressed as a third bus:
`load_rack {instrument:"master"}` and, with it, every other rack message
unchanged (`set_param`, `add_tab`, `set_bypass`, macros, sidechain…). It is
reported under `state.mixer.fx` / `.tabs` / `.macros`. One rack mechanism
everywhere (DDR-038) cost one field — `ChainInput::bus`, the audio fed in
where an engine's output would be. The master bus is a rack and **nothing
else**: the instrument-only messages (`load_engine`, `load_patch`, `load_kit`,
`env`, `bend`, `host_param`, `set_note_param`, `copy_pad`, `waveform`,
`instrument` and every MOD message) are refused on it rather than quietly
falling back to the active instrument.

**MASTER** is `mixer {master, comp, drive, bal}` / `{comp_rel, drive_tone}`.
DRIVE is a `tanh` soft clipper crossfaded dry→wet so 0 is bypass; DRIVE TONE
tilts what reaches it around 700 Hz; BAL is a balance with unity at centre;
COMP REL is v1's fixed 120 ms made a knob (0.02–1 s). **Mute and solo**
(DDR-061) are `mixer {track, mute|solo}` with solo winning, gating the mix
only. `bake` and `clip_to_asset "all"` take the EQ, the gate, gain, pan,
DRIVE, LEVEL and BAL; COMP and the master rack stay out of a bounce — COMP is
a performance knob and MASTER FX on a bounce is FILE › PROJECT's switch.

One behaviour moved: **LEVEL is now after COMP**, as §15.5 orders the strip
(it was before it in stage 2). At the default master level the output is
identical. Defaults and the rest of the deviations: `audio/CLAUDE.md`;
messages: `audio/host/PROTOCOL.md`.

### 15.14 H1 as built — the sequencer boxes (2026-09-17)

No ABI change and no protocol change: §15.4's four boxes fit in seven or eight
ordinary parameters each. `arbutus/endless` gained **GATE** (10–100 % of the
step), **PATTERN** (eight 16-step rhythm masks on their own free counter, so a
phrase precesses through the mask rather than locking to it) and **tied notes**
— REST with the keys still held inserts a tie, which holds the step before it
instead of retriggering, which is §15.4's "hold keys + ›". All three default to
what the box already did, so the frozen event golden did not move.

**§15.4's "SHIFT+turn rotates" could not be built as written.** A parameter
change crosses the ABI as `set_param(idx, 0..1)` and carries no modifier — only
a *soft-key* event has a SHIFT flag (DDR-041) — so the rotation is its own
slot, `ROT`, in the next SHIFT position, where SHIFT+turn would have put the
hand anyway. `arbutus/euclid` has the same knob for the same reason.

The three new boxes:

| Box | Shape | Parameters |
|---|---|---|
| `arbutus/arp` | the chord is the pattern; nothing stored, so **no soft-keys** | DIV · MODE · OCT · GATE / SWING · HOLD · RATCHET |
| `arbutus/pattern` | a riff **written down**: a fixed STEPS grid at absolute pitch, the box a pattern *file* is for | DIV · STEPS · GATE · DIR / SWING · OCT · HOLD |
| `arbutus/euclid` | the numbers are the pattern; `(i·PULSES) mod STEPS < PULSES`, Bjorklund up to rotation | STEPS · PULSES · ROT · DIV / GATE · SWING · HOLD · ACCENT |

`arbutus/pattern` declares `endless`'s four soft-keys with the same behaviour,
so one UI drives both, and `STEPS` 0 reads `AUTO <n>` through `format_param`
because "0 steps" on a screen would be a lie.

**Patterns as content (DDR-037) start here.** `endless` and `pattern` both
declare the `pattern` asset and read
`{"format":1,"kind":"pattern","pattern":[[60,64],"tie",[]]}` — a step is a list
of notes, `[]` is a rest, `"tie"` holds the step before it. Stage H2 is what
puts a pattern file on the browser and retires `native_sequencer.py`'s replay
bridge; the box half of it works now. Ranges and the reason for every one:
each box's `README.md` (DDR-044). Deviations: `audio/CLAUDE.md`.

### 15.15 I1 as built — the synth boxes (2026-09-18)

No ABI change. One **protocol** change, in `load_kit`: §15.3 gives a pad the
shape `{file, params}` and never says a kit must be samples, so a pad that is
`{"params": {…}}` with **no `file`** now loads. `arbutus/dbox` is a synthesised
kit and has nothing to open; `set_note_param`, `copy_pad`, `normalise_kit` and
"the kit document is the save" are all unchanged, and a pad that is neither a
file nor an object is still `ENGINE BROKEN`. A `waveform` request for such a
pad is `not_found`, which is what the UI already draws as `NO SAMPLE`.

One host bug, found by the first patch this stage wrote: `load_patch` carried
the engine's assets over so that a patch onto DRUM keeps its kit, but it did so
onto a **different** engine too — so a patch for a box that takes no assets,
loaded after a sampler patch, was refused `UNKNOWN ASSET sample`. It now
carries them only when the engine id is unchanged, which is the rule
`load_engine` already states.

The four boxes:

| Box | Shape | Parameters |
|---|---|---|
| `arbutus/va` | the subtractive voice: two polyBLEP oscillators, a sub, noise, and a **zero-delay four-pole ladder with the ENV page's own ADSR on it** | WAVE · CUTOFF · RES · ENV-F / SUB · NOISE · SPREAD · DRIVE |
| `arbutus/string` | two waveguide lines a few cents apart, plucked / bowed / struck, through a fixed two-mode body | EXCITE · DECAY · DAMP · POS / TONE · FORCE · BODY · SPREAD |
| `arbutus/wavetable` | a table scanned, phase-warped and folded; the table is an **asset** (`frames × 2048` samples, one cycle per frame) | POS · SWEEP · WARP · FOLD / SPREAD · SUB · DRIFT |
| `arbutus/dbox` | 24 synthesised drum pads, every parameter `per_note`, kit document with no files in it | MODE · TUNE · DECAY · SNAP / TONE · DRIVE · LEVEL · PAN |

**`arbutus/dbox` declares `honours_env: false`**, and that is the point of it.
DDR-053 asked for a per-pad AD; `arbutus/drum` could not give one because its
eight slots were already §15.3's sample list, so §15.11 took the instrument's
global AD instead. Here DECAY *is* per pad, so a global envelope on top could
only make things shorter, and DRUM › ENV has nothing to do on this box.

**Aliasing is `arbutus/wavetable`'s whole engineering problem**, and it is
solved at load rather than per sample: `load_asset` builds a mip chain per
frame (level *k* is the frame at `2048 >> k` samples, half-band filtered
circularly before each decimation) and the oscillator takes the longest level
whose top harmonic still fits under the sample rate at the note played, with
headroom reserved for the harmonics WARP and FOLD invent. Its stock tables are
a **build**: `audio/tools/mkwavetable.py` writes them from harmonic amplitude
lists, and a test regenerates and compares (repo `CLAUDE.md` § Library).

`arbutus/va` and `arbutus/wavetable` both take their sweep from the ENV page's
ADSR rather than carrying a second envelope: DDR-044 allows eight parameters and
four of them would go on a shape the player has already set.

Ranges and the reason for every one: each box's `README.md` (DDR-044) — these
four have no Python original, so every bound in them is a choice made once and
written down. Deviations: `audio/CLAUDE.md`.

### 15.16 I2 as built — the effect boxes (2026-09-18)

No ABI change and no protocol change. Five effect boxes, all
`caps: audio_in, audio_out`, `honours_env: false`, all of them working on an
instrument rack and on the master rack (`instrument: "master"`, §15.5) without
knowing which, all macro-bindable and all honouring rack bypass —
`host_fxboxes.py` measures each of those claims.

| Box | Shape | Parameters |
|---|---|---|
| `arbutus/delay` | the echo, and **the one stock box that reads the tempo**: one two-sided TIME knob carrying thirteen note values one way and free milliseconds the other | TIME · FDBK · TONE · MIX / PING · WOW · DRIVE · DUCK |
| `arbutus/chorus` | modulated short delays — flanger, chorus and ensemble at three settings of DELAY; stereo by LFO phase, never by detuning a side | RATE · DEPTH · DELAY · MIX / FDBK · SPREAD · VOICES · TONE |
| `arbutus/crusher` | the two halves of a bad converter, kept separate: how finely it is measured and how often | BITS · RATE · DRIVE · MIX / TONE · JITTER |
| `arbutus/drive` | four waveshapers at **2× oversampling**, with a pre-tilt, a highpass, asymmetry and supply sag | MODE · DRIVE · TONE · MIX / BIAS · HP · SAG · OUT |
| `arbutus/eq3` | three bands **you can move**, on a rack tab — the pedal, not the desk | LOW · MID · HIGH · MID-F / LOW-F · HIGH-F · MID-Q · OUT |

**`arb_host.tempo_bpm` reaches an effect**, and `arbutus/delay` is the first box
to use it. The host fills one block and hands it to every engine in the graph,
so a rack effect has the same clock a sequencer does; measured, a `1/4` echo
lands at 0.5000 s at 120 BPM, 1.0000 s at 60 and 0.6250 s at 96, from one
unchanged knob position. Its line is sized once at `create()` for 4 s, so a
synced `1 BAR` clamps below 60 BPM rather than allocating on a tempo change
(§3.7.1).

**`arbutus/drive` is the only box with latency.** The shaper runs at 2× behind
two 31-tap half-band filters, which is 15 frames of group delay, declared in
`latency_frames` and **not** compensated by the host (§12). The box delays its
own dry path by the same 15 frames before the DRIVE crossfade and before MIX,
because a box that mixes a delayed wet with an undelayed dry comb-filters
itself. Everything else in the set is zero.

**`arbutus/eq3` is not DDR-062's per-track EQ.** That one is host DSP on the
mixer's eight strips with fixed shelves and HP/LP, edited in real dB and Hz
from MIXER › EQ (§15.13). This one is a rack box: every corner frequency moves,
every parameter is macro-bindable and modulatable like any other engine
parameter, and it carries no HP or LP because the track EQ has them.

**Every one of the five has a bit-exact bypass** — MIX 0 on four of them, all
three bands at 0 dB on `eq3` — measured at 0.000e+00 peak error against the dry
input. An effect on a tab you have not turned up must not colour anything,
which is `ctest -R host_pipeline`'s rule applied to the boxes rather than to the
host.

Ranges and the reason for every one: each box's `README.md` (DDR-044). These
five have no Python original either, and each bound carries the measurement
that settled it — half of them moved because the measurement contradicted the
first guess. Deviations: `audio/CLAUDE.md`.

### 15.17 H2 as built — patterns as assets, SEQ and TEMPO (2026-09-18)

One protocol change and two pages. No ABI change; `ARB_ABI` stays 2.

**`load_engine` carries content.** §15.4 says a pattern is an asset and H1 made
both boxes read one, but the only door into the SEQ slot was a whole §7.2
patch — which rebuilds the engine, the rack and the MOD rack to change a file
the sequencer opens. `load_engine {instrument?, slot, id, assets:{key:path},
dir?}` loads the engine *with* that map, resolved exactly as a patch's:
present replaces the slot's whole map, `{}` clears it, absent leaves it alone,
and the slot's parameters survive because the id did not change. The same
message serves an FX or MOD tab's `assets` shape, so nothing new was invented.
That retires the emulator's `.seq` replay bridge, which pressed
REC/REST/UNDO/CLEAR to reproduce a pattern and therefore could never reach
`arbutus/arp` or `arbutus/euclid` — neither declares a soft-key.

**The pattern document** is written down in `audio/library/README.md`
§ Patterns: `pattern` is the steps (a list of MIDI notes, `[]` a rest, the
string `"tie"` a tie), `root` is what `endless` transposes from, and
`named_params` is **not** part of the asset — the UI applies it to the SEQ row
by the parameter's displayed name, so a saved riff comes back at the division
it was written at. A pattern that has only been *entered* rides to the box on a
scratch file under `$TMPDIR`, the way `load_kit` hands over a resolved kit; it
becomes a library item when a patch is promoted, which writes
`seq/<box>/<slug>.seq` and names it in `seq.assets.pattern`.

**The SEQ row is the descriptor's.** §15.4's row was `arbutus/endless` v0.1's
four knobs and §15.14 replaced it per box; the page was still drawing the old
four by name, so three of the four boxes had a blank row and `endless`'s own
GATE / PATTERN / ROT were sent but never shown. All eight cells now come from
`arb_describe()`, `format_param` readouts included. ACTIVE — which is the slot
being loaded at all, not a parameter — moved off the encoders entirely: four
boxes declaring seven or eight parameters leave nowhere consistent for a
ninth control, and DDR-035's SEQ-key tap was already the gesture.

**TEMPO** is `screen-sketches.md` § TEMPO in full: `BPM · CLICK LVL ·
CLICK PITCH · LINK` / `BPM FINE · COUNT-IN · SYNC · —`, `‹ ›` nudging ±1 BPM
and ±0.1 under SHIFT. LINK is `tempo {link}` and nothing else (§15.12, DDR-059)
— `base_bpm` moves only on a project load. COUNT-IN is a **delayed punch**
rather than a pre-roll: the transport rolls from where you asked and the take
begins N bars later, because a pre-roll has to locate backwards and the head of
the tape is where a count-in is wanted most. SYNC `midi` has no clock source
until FILE › MIDI (stage J2), so it locks the local BPM controls rather than
implying one exists. UI deviations: `emulator/notes/as-built.md` § H2.

### 15.18 J2 as built — the library scan and `rescan` (2026-09-18)

One line of host behaviour, and it is the end of the v1 build. No ABI change,
no protocol change; `ARB_ABI` stays 2 and **`proto` stays 1** (corrected
2026-09-19, #62: this line read "stays 2" and there has never been a proto 2 —
see §15.6).

**A dot-prefixed folder is not indexed, and neither is anything under it.**
§3.9 gives `<root>/<author>/<name>/` as the one tree and says nothing about
what to skip, and until now the scan took every directory it found. That was
harmless while the only dot folder was `.originals/` — the unconverted masters
a sample was made from, which carry no `engine.json` — and stopped being
harmless when DDR-055's browser file ops landed: an item that is lifted and
never dropped goes to `<library>/.trash/`, and `audio/library/` is a
`--library` root. A box folder indexed out of the trash is an item the
instrument still offers after it was thrown away, which makes the soft delete
not a delete.

**`rescan` is the message a file op sends afterwards** and it has been in the
protocol since the first version with no coverage of its own.
`audio/tests/host_files.py` pins both halves: a folder written after the host
started is offered by `library` only once `rescan` arrives, and a box moved
into `.trash/` — or written under `.originals/` at any depth — is never
offered at all.

Everything else in stage J2 is the emulator's: FILE › MIDI / USB / SYSTEM, the
browser as a file manager, and the battery. `emulator/notes/as-built.md` § J2 has the
deviations; the settings document's format is in `audio/library/README.md`
§ Settings and the stock manifest's in § Stock.

---

## Session History
- [[logs/2026-09-15]] — doc created from the five-round engine/rack/SDK grill; supersedes
  `engine-plugin-api.md`; DDR-038…044 added; control-inventory arrows/SEQ/FX-rack rows added.
- [[logs/2026-09-16]] — §14 added: how stages 1–4a settled the ABI (`load_asset`), PolyHost, rack, protocol and engines; header now authoritative over §3.2's listing.
- [[logs/2026-09-17]] — §15.13: F1 as built — per-track EQ, the master rack, the MASTER strip, mute/solo
- [[logs/2026-09-17]] — §15.14: H1 as built — arp / pattern / euclid, endless's GATE, PATTERN and ties, the `pattern` asset
- [[logs/2026-09-17]] — §15.15: I1 as built — `va`, `string`, `wavetable`, `dbox`, the wavetable asset and the fileless kit pad
- [[logs/2026-09-18]] — §15.16: I2 as built — `delay`, `chorus`, `crusher`, `drive`, `eq3`; the tempo reaching an effect and the first box with latency
- [[logs/2026-09-18]] — §15.17: H2 as built — `load_engine` carries content, the SEQ row from the descriptor, the whole TEMPO page
- [[logs/2026-09-16]] — session 2: §15 added — ABI 2 (`ctl_out`, `format_param`, `set_note_param`, `set_voice_mod`, CC/BEND, `motion[]`), live-input, per-pad, per-instrument SEQ, 8 tracks, overdub-only, per-track EQ, master rack, proto 2, block 128
- [[logs/2026-09-17]] — stage G as-built: §15.2's live input arrives on the ENGINE slot's `side[]`, SOURCE is mirrored, and `resample` also drops the instrument bus, the click and the monitor
- [[logs/2026-09-18]] — §15.18: J2 as built — the box scan skips dot-prefixed folders (`.trash/`, `.originals/`) and `rescan` gets its first coverage; the v1 spec package is built
- [[logs/2026-09-18]] — audit Q1 (#31): as-built corrections and audit notes; findings in `docs/design/audit-2026-09-18.md`
- [[logs/2026-09-19-11-39]] — cloud routine (D5, #26): §3.9's "zipped" corrected — a `.arbox` is `device-os-spec.md` § 10.1's container (a tar with `kind: box`), so a box and a firmware update are verified and installed by one code path; the manifest's `id` is the `<author>/<name>` it lands in
- [[logs/2026-09-23-00-14]] — bench routine (S15, #56): **§15.7 gains rule 8** — an engine's output does not depend on the block size. It is rule 4's determinism applied to rule 2's "any `frames ≤ max_block`", and it had lived only in `audio/tests/ragged.sh`'s header comment — where a broken exit status meant nobody was checking it, and `arbutus/va` and `arbutus/wavetable` broke it by 16 and 60 dBFS undetected. §15.8 also says explicitly that `modulation-spec.md` §7's "one value per block" is about **modulators**, which the host drives, and is not a licence for an engine's own state — the sentence both boxes' comments cited as justification for reading their envelope once per `process()` call. The SDK's answer is `arb::kCtrlStep` in `sdk/poly.hpp`
- [[logs/2026-10-03-19-04]] — bench routine (#273): § 6.5 parallel groups cut to a tombstone; § 6.1 MIX row, § 6.7 example, § 12, § 13 and the § 8.2 message list lose them
- [[logs/2026-10-13-01-20]] — #633: the 2026-10-06 sweep's spec drift amended in 10 docs (dated lines); D_AVDD's code comment split to #863
- [[logs/2026-10-13-09-04]] — pattern publishes its length via a relaxed atomic each block; format_param reads it; a TSAN test, red on two mutants
- [[logs/2026-10-14-06-26]] — Stock engines copied to the card and kept in step, the slot a fallback, the last of a kind refuses; the rest is #1064
- [[logs/2026-10-14-15-56]] — Closed bake: card as root, slot as fallback, so an uninstalled engine refuses; last_engine a 409 and the app's sentence
- [[logs/2026-10-14-20-27]] — A missing engine's patches show NEEDS <ENGINE>; an unsigned card box asks RUN · CANCEL, kept per contents
- [[logs/2026-10-15-12-23]] — unsigned-box question before FILE › LOAD, a slot recall and RACKS; CANCEL keeps what was open; rest is #1205
- [[logs/2026-10-15-17-39]] — the M7 voice call ingested (OP-1 mod scene: answers, tickets #1230–#1235)
