# Writing an Arbutus-1 engine

This folder is a working engine and the documentation at the same time. Copy it,
rename it, replace `Voice`, and you have a box. If you are an agent handed this
by link, everything you need is on this page; the authority behind it is
`docs/design/engine-spec.md` §3, §4, §9 and §15 (ABI 2), and the header
`sdk/include/arbutus_engine.h` wins wherever prose disagrees. This page
describes **ABI 2**: `arb_describe()` sets `abi = ARB_ABI`, and a box built
against any other ABI is refused with `NEEDS FIRMWARE UPDATE`.

## What a box is

An engine ships as a **box**: a folder with a manifest and one compiled binary
per architecture.

```
arbutus/example/
  engine.json                    format, id, version, caps, params snapshot
  darwin-arm64/example.dylib     Mac build
  linux-aarch64/example.so       Pi build
  assets/                        optional: wavetables, IRs, samples
```

Nothing in the box is interpreted. The host `dlopen`s the binary and calls two
exported functions, `arb_describe()` and `arb_entry()`. Everything else — C++,
Rust, Zig, hand-written assembly — is your business.

`engine.json` is **generated** by the build from `arb_describe()`, not typed.
You write the prose half (`author`, `description`); `add_arbutus_engine()` fills
in the rest and the host re-checks it at load. A snapshot that disagrees with
the binary is `ENGINE BROKEN` and the box does not load.

## The ports

An engine has up to six ports, and its **role is which ones it uses**. There is
one ABI, not five; unused ports are NULL.

| Role | audio in | sidechain | audio out | events in | events out | ctl out | Slot |
|---|---|---|---|---|---|---|---|
| Sequencer | | | | ✓ | ✓ | | SEQ |
| Synth / drum | | | ✓ | ✓ | | | ENGINE |
| Effect | ✓ | (opt) | ✓ | (opt) | | | FX rack tab |
| Vocoder-class | ✓ | (opt) | ✓ | ✓ | | | FX |
| Modulator | | (opt) | | (opt) | | ✓ | MOD rack tab |

Declare them as capability bits in `arb_engine_desc.caps`; the host wires only
what the slot provides and drops the rest.

Events are **musical keys only**, plus clock and transport, and — from MIDI —
`ARB_EV_CC` (`a` = controller 0..127, `b` = 0..1). There is no bend event:
bend arrives already resolved as `arb_host.pitch_st` (below). Command keys, arrows, LIFT/DROP and
page keys never reach an engine. Encoder turns arrive as `set_param()`, not as
events. Every event carries a frame offset within the block, so a sequencer is
sample-accurate. Ignore event types you do not know; more are appended, never
renumbered.

### Modulators and `ctl_out`

A modulator (`ARB_CAP_MODULATOR`, `modulation-spec.md` §3) makes control
values, not sound: an LFO, an envelope follower, the IMU. It declares **1–4
outputs** and **no audio ports** (sidechain and events in are allowed), and
every block writes one value per output into `io->ctl_out[i]` — bipolar outputs
−1..1, unipolar 0..1. The host zeroes `ctl_out` before each `process()` and
reads it after; a modulator that writes nothing outputs 0. Any other role
leaves `ctl_out` alone.

```cpp
d.caps = ARB_CAP_MODULATOR | ARB_CAP_EVENTS_IN;
arb::set_outputs(d, {arb::output("sine", "SINE", /*bipolar=*/true),
                     arb::output("gate", "GATE", false)});
```

Outputs land in `engine.json` as `"outputs": [{id, name, bipolar}]`. A modulator
without outputs, outputs without the cap, or a modulator with audio ports is
`ENGINE BROKEN`. Values are **block rate** (one per block, 375 Hz at 128
frames); the receiving engine's smoothing (rule 7) joins the steps.

## The eight parameters

Up to eight, each a `arb_param_desc`, each crossing the boundary **normalised
0..1**. The descriptor says what a 0.42 means: `min`, `max`, and a `curve` of
`lin` or `log`. Three types:

| type | encoder | screen |
|---|---|---|
| `CONTINUOUS` | `step` per detent along the curve | position bar |
| `INTEGER` | one unit per detent | value label |
| `ENUM` | one entry per detent | `labels[i]` |

`ENUM` is how you get a selector — waveform, division, direction — without any
new input mechanism. Slots 0–3 are the four encoders; 4–7 are SHIFT + encoder.
Leave 4–7 empty and SHIFT becomes fine-tune of the slot below, which is usually
what you want. `id` is a stable snake_case string and is **never renamed once
shipped** — patches are keyed by it.

Ranges are a design decision, not a formality: bound every parameter so that
anywhere on the knob sounds like something a player would keep.

Two flags per parameter (ABI 2), set by wrapping a builder:

- **`per_voice`** — `arb::per_voice(arb::param_lin(...))`. A route from the
  keys' VEL or NOTE to this parameter is applied **per voice** by your engine
  instead of as one global offset (`modulation-spec.md` §8): the host calls
  `set_voice_mod(idx, src, depth)` and each voice plays the parameter at knob +
  depth × source for its own lifetime. Needs `set_voice_mod` in the vtable.
  Unflagged, the same route moves the knob for every voice with the last note's
  velocity — also useful; the flag says which one your engine does.
- **`per_note`** — `arb::per_note(...)`. The value is **per pad** (a drum's
  PITCH, START, CHOKE): the UI edits the last-hit key and the host calls
  `set_note_param(note, idx, v)`. `set_param` on this slot is to be ignored.
  Needs `set_note_param` in the vtable.

A flag without its vtable entry is `ENGINE BROKEN`. Both land in `engine.json`
on every parameter as `"per_voice"` / `"per_note"` booleans.

## Realtime rules (spec §3.7)

Binding. They are checked at upload; `arbutus-run --rt-guard` catches the
first one mechanically and `tests/ragged.sh` the eighth.

1. `process()` does **no allocation, no locks, no I/O, no syscalls**. Load
   assets in `create()` or in `load_asset()` — both run on the control thread.
2. Handle any `frames <= host.max_block`, every call, **including 0**.
3. Write **both** output channels every call, even when silent. Mono engines
   duplicate.
4. Be deterministic given the inputs and `host.rng_seed`. Never read a clock.
5. `reset()` returns to silence with all state cleared; parameters keep their
   values.
6. `arb_describe()` is pure: no allocation, no side effects, the same answer
   every time. It is called before `create()`.
7. **Parameter changes are smoothed** over at least one block toward the
   target. A step is a choice, not a default — an ENUM steps, a value sampled at
   note-on steps, and you say so in a comment.
8. **The output does not depend on the block size** (spec §15.7). The same
   notes at the same frames render the same samples whether the host hands you
   512 frames, 128, 64, a prime or 1. The device runs 64, the emulator 128, a
   bake whatever it likes: one box, one sound.

### Rule 8 with `arb::kCtrlStep`

The way to break rule 8 is to read a running value — your own filter envelope,
a sweep, a drift — **once per `render()` call**: the host decides how long the
call is, so the value lands on a different point of the curve at every block
size (two stock boxes did, 16 and 60 dBFS out; #56). Count your voice's frames
since note-on and recompute on the fixed 32-frame grid `sdk/poly.hpp` gives you:

```cpp
// in Voice: uint64_t frame_ = 0;  (0 in start(), 0 in reset())
void render(float* l, float* r, uint32_t n, const arb_host& h) {
    uint32_t done = 0;
    while (done < n) {
        const uint32_t m = arb::ctrl_run(frame_, n - done);   // to the end of this cell
        if (arb::ctrl_cell_start(frame_)) recompute();       // NOT on every run
        /* ... render m frames into l + done, r + done ... */
        frame_ += m; done += m;
    }
}
```

The `ctrl_cell_start` guard is the half that is easy to miss: a call can end
mid-cell, so the next call's first run starts mid-cell, and recomputing there
puts the block size back in. Cache the value across calls. `PolyHost` already
does this for glide, `pitch_st` and the envelope; it is your own state that
needs it.

**What rule 8 does not cover: a knob's glide.** `set_param` only ever lands
between blocks, so a knob turned under 64-frame blocks and under 128-frame
blocks is already two different inputs, and a rule-7 ramp advanced once a
block (`Smoothed::advance(io.frames)`, as this template does for DETUNE and
TONE) is a staircase with block-long treads. That is allowed: rule 8 is about a
box whose parameters hold still, which is what `ragged.sh` renders. A box that
wants a glide exact at every block size reads its smoother on the
`kCtrlStep` grid too.

### Rule 7 with `arb::Smoothed<float>`

`sdk/params.hpp`. A linear ramp, allocation-free, plain data:

```cpp
arb::Smoothed<float> level{0.5f};

// create():   level.prepare(*host);          // ramp = one host block
//             cutoff.prepare(*host, 20.0f);  // or longer: max(one block, 20 ms)
// set_param:  level.set(v);                  // before the first block: lands at once
// process():  const float g = level.next();  // per frame
//             params.cutoff = cutoff.advance(io.frames);   // once per block
// reset():    level.reset();                 // glide ends at the target
```

Why one block by default: modulation arrives as a new `set_param` target every
block, and a one-block linear ramp turns that staircase into straight lines
with no loss of depth; a longer ramp becomes a lag filter that eats a fast
LFO. At rest `next()` returns the stored value with no arithmetic, so smoothing
does not change a golden that holds its parameters still. A value a voice only
reads once per block should ask for a ramp several blocks long — a one-block
ramp read at block rate is just a step one block late. The template shows both
(`src/engine.cpp`, `Engine`).

## The host block

`arb_host` arrives with every `process()`: sample rate, `max_block`, tempo,
transport, the ENV page, `rng_seed`, and — ABI 2 — **`motion[6]`**: the IMU as
`ax ay az` (g) and `gx gy gz` (deg/s), sampled by the host at ~200 Hz. All six
are **0 when no IMU is fitted**; there is nothing to ask. Read it; never talk
to the sensor from a box.

## The vtable

Ten entries. The first six are required; the rest are **NULL unless you need
them**, and the host checks that what you declare is backed by an entry.

```cpp
const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset      -- declared asset keys
    nullptr,                 // set_note_param  -- per_note params
    nullptr,                 // format_param    -- your own value readouts
    nullptr,                 // set_voice_mod   -- per_voice params
    {}
};
```

| Entry | Thread | What for |
|---|---|---|
| `create` / `destroy` | control | allocate everything here; `create` returns NULL on failure |
| `reset` | owner | silence, clear state, keep parameter values |
| `set_param` / `get_param` | owner | normalised 0..1 |
| `process` | audio | one block; rules 1–8 |
| `load_asset(h, key, path)` | control, before the graph | content (below); 0 = ok |
| `set_note_param(h, note, idx, v)` | owner | per-pad value of a `per_note` param |
| `format_param(h, idx, v, buf, n)` | **control, concurrently with `process`** | readout for value `v` |
| `set_voice_mod(h, idx, src, depth)` | owner | a VEL/NOTE route to a `per_voice` param |

"Owner" is whichever thread holds the instance: the control thread before it
enters the graph, the audio thread between blocks after. Never concurrently
with `process()` — except `format_param`.

### `format_param` — draw your own value

`int format_param(void* h, uint32_t idx, float normalised, char* buf, uint32_t n)`
writes the text for parameter `idx` **at `normalised`** — `1/8`, `STOP`,
`3.2 Hz` — as a NUL-terminated string of at most `n − 1` bytes and returns
non-zero. Return 0 and the host draws its default from the descriptor. It is
how the bipolar RATE knob (`modulation-spec.md` §3.3) shows `STOP` at centre.

The value is an argument so the host can label *any* position — the knob, the
ends of a range popup, a modulated ghost — and so it can run on the control
thread while `process()` renders. That makes it a pure function: depend only on
`(idx, normalised)` and what `create()` fixed. Never read or write what
`set_param` or `process` touch. The host puts the strings in the protocol's
`state` as `display` (`host/PROTOCOL.md`).

### `set_voice_mod` — velocity per voice

`void set_voice_mod(void* h, uint32_t idx, uint8_t src, float depth)`, `src`
∈ `ARB_VSRC_VEL`, `ARB_VSRC_NOTE`; depth 0 removes the route. On `PolyHost` it
is one line — the storage is done for you:

```cpp
void e_set_voice_mod(void* h, uint32_t idx, uint8_t src, float depth) {
    if (idx < desc().num_params && desc().params[idx].per_voice)
        static_cast<Engine*>(h)->poly.set_voice_mod(idx, src, depth);
}
```

`PolyHost` snapshots depth × source into each voice at note-on;
`poly.voice_mod(voice, idx)` is that voice's offset in normalised units, fixed
for its lifetime. VEL is the note's velocity 0..1; NOTE is bipolar, 0 at C4
(60) and ±1 sixty semitones either side. Applying it is your Voice's choice.

### `set_note_param` — per pad

`void set_note_param(void* h, uint8_t note, uint32_t idx, float normalised)`
for a `per_note` parameter: store it per note (a 128-entry array is fine),
apply it when that note plays. `tests/boxes/abi2/` is a box that implements all
three new entries, as small as they get.

## Assets (samples, kits, IRs)

Content is not a parameter. An engine that plays a file declares the keys it
accepts and implements `load_asset`:

```cpp
arb::set_assets(d, {"sample"});            // in make_desc()

int e_load_asset(void* h, const char* key, const char* path);  // returns 0 on success

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    e_load_asset, nullptr, nullptr, nullptr, {}
};
```

`load_asset` is **control thread only** — it may allocate and read files. The
host calls it after `create()` and before the engine renders anything, and
refuses the patch if the key is not one you declared. Leave the slot `nullptr`
when your engine takes no assets. Offline:
`arbutus-run <box> --asset sample=kick.wav`.

## PolyHost

You do not write polyphony. `arb::PolyHost<Voice, MaxVoices>` (`sdk/poly.hpp`)
owns note tracking, voice stealing (free → quietest-in-release → oldest), ENV
MODE (poly / mono / legato / unison), and one ADSR per voice taken from the ENV
page in the host block. You write a `Voice`:

```cpp
struct Voice {
    void start(int note, float vel, const arb_host&);
    void stop();                                       // enter your own release
    bool done() const;                                 // your tail is finished
    void render(float* l, float* r, uint32_t n, const arb_host&);
    void reset();                                      // clear the sound, keep your pointers
};
using MyEngine = arb::PolyHost<Voice, 8>;
```

`reset()` is required: `PolyHost::reset()` (the host's panic, a transport
reset) calls it on every voice. Clear what makes sound — phase, filter memory,
a tail — and keep what `create()` gave the voice, such as the pointer to your
parameter struct. A Voice without it does not compile; PolyHost used to
assign a fresh `Voice{}` instead, which nulled that pointer and crashed the
audio process on the next block (#675).

The envelope is applied **per voice, after the voice, never over the mix**. A
voice renders into a scratch buffer; PolyHost multiplies that buffer by that
voice's envelope and sums. An envelope over the mixed output gates every ringing
note whenever a new one starts — a volume jump on the fading note, old tails
riding along with each new one, once per step under a sequencer. It shipped
once; do not bring it back.

Optional members PolyHost finds by name if you declare them:

- `set_pitch(float cents)` — **declare this and GLIDE, DETUNE and BEND work
  for free.** PolyHost calls it before every `render()` (and once just before
  `start()`) with the voice's whole offset from its note: unison spread + glide
  + `arb_host.pitch_st`. Apply it in `render()`, not only in `start()`, or a
  ringing note will not follow a bend. At offset 0 compute exactly what you
  would without it; `arbutus/fm` and `arbutus/sampler` show the two usual shapes.
- `set_detune(float cents)` — the older hook: called once at note-on with the
  unison spread only. A voice with it and no `set_pitch` gets no glide or bend.

What PolyHost does with the ENV SHIFT row (DDR-052):

| Field | Modes | Behaviour |
|---|---|---|
| `env_glide` (s) | mono, legato | a new note starts at the pitch the voice is sounding (a glide in flight included) and slides to its own, **linear in semitones**, over GLIDE taken at that note-on; any interval takes the same time; a voice still in release glides too, silence does not. Poly and unison ignore it |
| `env_detune` (ct) | unison | the 4 voices sit evenly across DETUNE, outermost to outermost (12 = ±6 ct, the default); read every block |
| `pitch_st` (st) | all | added to every voice; ramped linearly across each block from the last block's value, so an LFO on `host.pitch` does not zipper |

While a glide or the `pitch_st` ramp moves, PolyHost renders in runs of
`kPitchStep` (32) frames and calls `set_pitch` per run; glide runs sit on a
fixed frame grid, so a glide is the same at any block size. When nothing moves
the block renders in one piece, exactly as before pitch existed.

**Not on PolyHost?** `arb_host.pitch_st` is the instrument's pitch offset in
semitones, already resolved by the host (bend position × `bend_range` +
`host.pitch`). A from-scratch synth that wants bend reads that one field and
smooths it; there is nothing else to handle. `env_glide` and `env_detune` are
there too if the engine has a use for them. Drums, sequencers, effects and
modulators ignore all four.

An engine that needs cross-voice state — a paraphonic filter, choke groups, a
string model — writes its own loop and sets `honours_env = 0`. The ENV page is
then greyed out for it. The exception is visible, never silent.

## Build

C++20, CMake — Apple clang on the Mac, g++ on Linux. A box is built on the
machine it runs on, except for a 512 MB Pi, which cannot build the tree: there
`tools/cross-build.sh` builds the whole of `audio/`, boxes included, on a Linux
host and copies it over (spec §9.5). `add_arbutus_engine()` writes the right
arch key either way.

```bash
cmake -S audio -B audio/build -G Ninja
cmake --build audio/build
ctest --test-dir audio/build --output-on-failure
```

Your engine's `CMakeLists.txt` is the box and its three tests (this folder's own is the same, plus a `--describe` check). The folder the
box lands in is `<author>/<target name>` — `author` from your `engine.json`,
the name from `add_arbutus_engine` — and the id in `arb_describe()` must be
exactly that, or the build stops (`engine id … is not its folder`): a copy of
this template whose id still says `arbutus/example` would otherwise build a
second box of that name and only one of the two would load.

```cmake
add_arbutus_engine(example SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/example")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

add_test(NAME example_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --frames 512 --sr 48000 --len 32768
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/example.npy" --tol -100)
add_test(NAME ragged_example COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
         $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
         --notes "${_notes}" --len 32768 --sr 48000)
add_test(NAME rt_guard_example COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 32768 --sr 48000)
```

Paths are absolute (`${CMAKE_CURRENT_SOURCE_DIR}`): `ctest` runs each test
from the build directory, where a bare `tests/notes.txt` is not found.

**A box is done when** all three pass: the **golden** (your sound, frozen),
**`ragged.sh`** (rule 8: the same samples at 512, 128, 64, 97, 2048 and 1 frames
a block) and **`--rt-guard`** (rule 1: nothing allocated inside `process()`).

## Goldens

A golden is a captured render. Bit-exactness across Apple clang and Pi g++ is
not achievable for float DSP, so goldens compare within a tolerance: **−100 dBFS
peak error** by default.

Capture one with `--out`, check against it with `--golden`:

```bash
arbutus-run <box> --notes tests/notes.txt --len 32768 --sr 48000 \
                  --out tests/golden/example.npy
arbutus-run <box> --notes tests/notes.txt --len 32768 --sr 48000 \
                  --golden tests/golden/example.npy --tol -100
```

Regenerate a golden only when a sound change is the *intent*. If a speed-up
breaks the golden, you changed the instrument.

`arbutus-run` also reports cost — `--bench` gives µs/block and the percentage of
one core at that block size and rate — and refuses to load a box that is broken,
with the message the device would show. See `tools/arbutus-run/README.md`.

## This engine

`src/engine.cpp` is a two-oscillator polyphonic synth: `Voice` is 40 lines, the
ABI shims are another 30. Parameters are WAVE (enum), DETUNE, TONE and LEVEL,
with slots 4–7 left empty so SHIFT fine-tunes. LEVEL glides per frame over one
block after the mix, DETUNE and TONE per block over 20 ms, WAVE steps (rule 7). `tests/notes.txt` plays a chord,
releases one note under the others and starts a late note while the first is
still in its release — the case a mixed-output envelope gets wrong.
