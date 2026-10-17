# audio/ — the C++ side of Arbutus-1

The firmware is a host; everything that makes sound is an **engine** — a box of
compiled code behind a tiny C ABI, loadable at runtime, shareable as a file.
This folder is where that lives. The contract is
[`docs/design/engine-spec.md`](../docs/design/engine-spec.md); this README only
says where things are.

`audio/` is a **new sibling** of `emulator/` and `arbutus_os/`. Nothing here
touches either of them, by construction: the Hollyhock demo keeps working while
this is built. The only thing `audio/` reads from `emulator/` is
`tests/golden/*.npy`, and it reads them.

## What is here

```
sdk/
  include/arbutus_engine.h   the ABI (ABI 2); the one path that must never move
  poly.hpp                   PolyHost<Voice>: stealing, ENV MODE, per-voice ADSR,
                             glide / unison detune / pitch_st, set_voice_mod storage
  params.hpp                 the 0..1 <-> units mapping; arb::Smoothed (rule 7)
  wav.hpp                    16-bit and float32 WAV, for boxes that play files
  desc.hpp                   builders for arb_engine_desc
  manifest.hpp               engine.json <-> the descriptor (host side)
  template/                  one buildable engine, and the documentation
  tools/arb_manifest.cpp     generates engine.json from arb_describe()
  CMakeLists.txt             add_arbutus_engine()
host/                        arbutus-audio: the audio process
  PROTOCOL.md                the control protocol -- public API from message one
  audio_host.{hpp,cpp}       state document, command rings, the block, the mixer
  rack.{hpp,cpp}             the §6 rack: tabs, macros, sidechain;
                             the MOD rack: modulator tabs, routes, keys, host.level/pan
  tape.{hpp,cpp}             4 stereo tracks, clip playback, the record tap
  device.{hpp,cpp}           live audio through miniaudio
hostlib/                     what the host and arbutus-run both need
  box.{hpp,cpp}              the loader and the §7.4 refusals
  library.{hpp,cpp}          the id -> box-folder index
  audiofile.{hpp,cpp}        WAV (16/24/float) and NPY, by hand
engines/arbutus/             the stock boxes, one folder each (table below)
tools/arbutus-run/           the offline engine test client
tools/arbutus-ctl/           the Python protocol client and library
tests/                       SDK units, the ABI layout, the voice goldens, twenty-four
                             offline host tests; boxes/ holds five test-only boxes
third_party/nlohmann/        single-header JSON (MIT)
third_party/miniaudio/       single-header audio I/O (public domain / MIT-0)
```

## ABI 2

The engine ABI is **version 2** (`engine-spec.md` §15): modulator engines with
`ctl_out` and `outputs[]`, `per_voice` / `per_note` parameter flags,
`set_note_param`, `format_param` and `set_voice_mod` in the vtable,
`ARB_EV_CC`, `arb_host.motion[6]`, the ENV SHIFT row and resolved pitch in
`arb_host` (`env_glide`, `env_detune`, `bend_range`, `pitch_st`), and realtime rule 7
(parameter smoothing, `arb::Smoothed`). Every struct was re-laid once with no
implicit padding; `tests/abi_layout.h` pins each size and offset from C and
C++. Until the first release a new field goes in as a named field in its
logical place and the layout is re-pinned, not appended into `_reserved`
(`CLAUDE.md` § Rules). `sdk/template/README.md` documents all of it for box authors. The host
refuses a box built against any other ABI, and its default block is **128
frames** (§15.8).

The six modulator boxes (`lfo`, `follower`, `random`, `envelope`, `motion`,
`stepmod`) use the new entries (`ctl_out`, `outputs`, `format_param`,
`motion[]`); `drum` v2 flags every param `per_note` and implements
`set_note_param`, and `sampler` v2, `arbutus/delay` (TIME as a note value) and
`arbutus/crusher` (TONE's `OFF` detent) implement `format_param`; the other
stock boxes use none of them, and **none smooths its parameters yet** — only the
template does. Rule 7 for the stock
boxes is listed as deferred in `CLAUDE.md`.

## The MOD rack

Per instrument, beside the FX rack (`modulation-spec.md`): up to four
modulator tabs and four routes, plus the always-present `keys` source
(VEL, NOTE). Modulators run before the engine each block; a route swings its
target around the knob (§6.2) without ever changing the knob in the document.
Targets: `engine.<id>`, `fx.<instance>.<id>`, `fx.macro.<slot>`,
`env.a|d|s|r|glide|detune`, `host.level`, `host.pan`, `host.pitch` (±24 st into
`arb_host.pitch_st`, with the `bend` message; PolyHost glides, detunes and
bends every voice). `keys` routes to a `per_voice` param go to the engine through
`set_voice_mod`. The `mod` document lives in the patch; live target values ride
`meters`. `host/PROTOCOL.md` § MOD rack is the reference.

A modulator with `ARB_CAP_SIDECHAIN` takes a per-tab source (`set_mod_sidechain`):
`input`, `tape:1..4`, and the two sources the MOD rack added host-wide, `self`
(this instrument's bus) and `master`, both **one block late** — FX tabs take
them too. MOD tabs and FX tabs carry `assets` (stepmod's `pattern`; no stock
effect declares one yet). Offline, the `motion`
message writes `arb_host.motion[6]`; `arbutus-run --motion` does the same from
an `.npy`.

## The stock boxes

Thirty-one folders under `engines/arbutus/`, each a copy of the template: a
manifest, one `src/engine.cpp`, a `tests/` with a golden, and a README that
says what every range is for. `arbutus/example` is the SDK template and lives
in `sdk/template/`.

| Box | Role | Caps | Parameters (default) | µs/block | % core | µs/block (CM4) | % core (CM4) | µs/block (3B+ @ Zero) | % core (3B+ @ Zero) |
|---|---|---|---|---|---|---|---|---|---|
| `arbutus/osc` | synth | `audio_out` `events_in` | WAVE *sine* · LEVEL *0.7* | 7.88 | 0.07 | 32.70 | 0.31 | 136.00 | 1.28 |
| `arbutus/fm` | synth | `audio_out` `events_in` | RATIO *1* · INDEX *2.2* · LEVEL *0.8* | 14.50 | 0.14 | 142.08 | 1.33 | 474.82 | 4.45 |
| `arbutus/noise` | synth | `audio_out` `events_in` | TYPE *white* · CUTOFF *4 kHz* · LEVEL *0.8* | 7.63 | 0.07 | 73.70 | 0.69 | 237.53 | 2.23 |
| `arbutus/sampler` | synth | `audio_out` `events_in` | START *0* · END *1* · LOOP-IN *1* · LOOP-OUT *1* / ROOT *60* · TUNE *0* · REV *off* · FADE *0 %* | 4.56 | 0.04 | 22.70 | 0.21 | 91.73 | 0.86 |
| `arbutus/va` | synth | `audio_out` `events_in` | WAVE *saw* · CUTOFF *2.2 kHz* · RES *0.2* · ENV-F *+35 %* / SUB *0.25* · NOISE *0* · SPREAD *8 ct* · DRIVE *0.15* | 41.58 | 0.39 | 136.40 | 1.28 | 500.85 | 4.70 |
| `arbutus/string` | synth | `audio_out` `events_in` | EXCITE *pluck* · DECAY *1.6 s* · DAMP *0.35* · POS *22 %* / TONE *0.6* · FORCE *0.5* · BODY *0.3* · SPREAD *6 ct* | 24.75 | 0.23 | 182.39 | 1.71 | 619.53 | 5.81 |
| `arbutus/wavetable` | synth | `audio_out` `events_in` | POS *0 %* · SWEEP *0 %* · WARP *0 %* · FOLD *0 %* / SPREAD *10 ct* · SUB *0.2* · DRIFT *15 %* | 13.62 | 0.13 | 202.25 | 1.90 | 694.94 | 6.52 |
| `arbutus/drum` | drum | `audio_out` `events_in` | per pad: START *0* · END *1* · PITCH *0* · LEVEL *0.8* / PLAY *one-shot* · REV *off* · PAN *0* · CHOKE *off*; ENV = ATK · DEC | 1.71 | 0.02 | 12.65 | 0.12 | 54.44 | 0.51 |
| `arbutus/dbox` | drum | `audio_out` `events_in` | per pad: MODE *kick* · TUNE *0 st* · DECAY *0.35 s* · SNAP *45 %* / TONE *50 %* · DRIVE *15 %* · LEVEL *0.8* · PAN *0*; no ENV | 30.14 | 0.28 | 214.83 | 2.01 | 703.68 | 6.60 |
| `arbutus/biquad` | effect | `audio_in` `audio_out` | CUTOFF *8 kHz* · Q *1.0* | 2.62 | 0.02 | 7.26 | 0.07 | 25.66 | 0.24 |
| `arbutus/comb` | effect | `audio_in` `audio_out` | TUNE *0 st* · FDBK *0.8* · DAMP *0.3* · MIX *0.5* | 5.76 | 0.05 | 19.20 | 0.18 | 80.32 | 0.75 |
| `arbutus/satcomb` | effect | `audio_in` `audio_out` | TUNE *0 st* · FDBK *0.9* · DAMP *0.35* · DRIVE *2* · MIX *0.6* | 11.62 | 0.11 | 31.29 | 0.29 | 108.94 | 1.02 |
| `arbutus/reverb` | effect | `audio_in` `audio_out` | SIZE *0.5* · MIX *0.25* | 4.00 | 0.04 | 13.29 | 0.25 | 69.21 | 1.30 |
| `arbutus/duck` | effect | `audio_in` `audio_out` `sidechain` | THRESH *−26 dB* · DEPTH *0.85* · ATK *5 ms* · REL *150 ms* | 3.74 | 0.04 | 19.60 | 0.18 | 57.15 | 0.54 |
| `arbutus/delay` | effect | `audio_in` `audio_out` | TIME *1/8* · FDBK *0.35* · TONE *0.5* · MIX *0.3* / PING *0* · WOW *0.1* · DRIVE *0* · DUCK *0* | 14.26 | 0.13 | 116.66 | 1.09 | 360.32 | 3.38 |
| `arbutus/chorus` | effect | `audio_in` `audio_out` | RATE *0.55 Hz* · DEPTH *0.45* · DELAY *8 ms* · MIX *0.4* / FDBK *0* · SPREAD *120°* · VOICES *2* · TONE *0.5* | 27.54 | 0.26 | 306.16 | 2.87 | 832.15 | 7.80 |
| `arbutus/crusher` | effect | `audio_in` `audio_out` | BITS *8* · RATE *8 kHz* · DRIVE *0 dB* · MIX *1* / TONE *off* · JITTER *0* | 2.20 | 0.02 | 12.90 | 0.12 | 58.84 | 0.55 |
| `arbutus/drive` | effect | `audio_in` `audio_out` | MODE *soft* · DRIVE *0.3* · TONE *0.5* · MIX *1* / BIAS *0* · HP *off* · SAG *0* · OUT *0 dB* | 30.08 | 0.28 | 199.52 | 1.87 | 638.22 | 5.98 |
| `arbutus/eq3` | effect | `audio_in` `audio_out` | LOW *0 dB* · MID *0 dB* · HIGH *0 dB* · MID-F *900 Hz* / LOW-F *180 Hz* · HIGH-F *3.2 kHz* · MID-Q *0.9* · OUT *0 dB* | 6.85 | 0.06 | 28.67 | 0.27 | 95.55 | 0.90 |
| `arbutus/transient` | effect | `audio_in` `audio_out` | ATTACK *0 %* · SUSTAIN *0 %* · SPEED *30 ms* · MIX *100 %* / OUTPUT *0 dB* | 4.29 | 0.04 | 36.16 | 0.34 | 109.09 | 1.02 |
| `arbutus/endless` | sequencer | `events_in` `events_out` `sequencer` | DIV *1/16* · SWING *50 %* · HOLD *off* · DIR *fwd* / GATE *100 %* · PATTERN *off* · ROT *0* | 0.09 | 0.00 | 0.69 | 0.01 | 1.92 | 0.02 |
| `arbutus/arp` | sequencer | `events_in` `events_out` `sequencer` | DIV *1/16* · MODE *up* · OCT *1* · GATE *80 %* / SWING *50 %* · HOLD *off* · RATCHET *1* | 0.07 | 0.00 | 0.63 | 0.01 | 1.58 | 0.01 |
| `arbutus/pattern` | sequencer | `events_in` `events_out` `sequencer` | DIV *1/16* · STEPS *auto* · GATE *80 %* · DIR *fwd* / SWING *50 %* · OCT *0* · HOLD *off* | 0.09 | 0.00 | 0.72 | 0.01 | 2.57 | 0.02 |
| `arbutus/euclid` | sequencer | `events_in` `events_out` `sequencer` | STEPS *16* · PULSES *4* · ROT *0* · DIV *1/16* / GATE *60 %* · SWING *50 %* · HOLD *off* · ACCENT *0 %* | 0.08 | 0.00 | 0.77 | 0.01 | 2.54 | 0.02 |
| `arbutus/lfo` | modulator | `events_in` `modulator` | RATE *1/4* · MORPH *0* · PHASE *0°* · RETRIG *free* · FADE-IN *0 s* · SYM *50 %* | 0.05 | 0.00 | 0.25 | 0.00 | 0.78 | 0.01 |
| `arbutus/follower` | modulator | `sidechain` `modulator` | ATTACK *5 ms* · RELEASE *150 ms* · GAIN *0 dB* · THRESH *−30 dB* · HOLD *50 ms* | 1.35 | 0.01 | 1.60 | 0.06 | 6.29 | 0.24 |
| `arbutus/random` | modulator | `modulator` | RATE *1/8* · SLEW *0* · RANGE *1* · SEED *host* | 0.03 | 0.00 | 0.15 | 0.01 | 0.40 | 0.02 |
| `arbutus/envelope` | modulator | `events_in` `sidechain` `modulator` | ATTACK *10 ms* · DECAY *300 ms* · SUSTAIN *0.5* · RELEASE *500 ms* · TRIG *note* · LOOP *off* · CURVE *lin* | 0.37 | 0.00 | 0.64 | 0.02 | 1.64 | 0.06 |
| `arbutus/motion` | modulator | `modulator` | SMOOTH *50 ms* · SHK-SENS *0.5 g* · SPN-SENS *250 dps* | 0.04 | 0.00 | 0.23 | 0.01 | 0.68 | 0.03 |
| `arbutus/stepmod` | modulator | `events_in` `modulator` | STEPS *8* · DIV *1/16* · SLEW *0* · DIR *fwd* · RANGE *1* | 0.03 | 0.00 | 0.15 | 0.01 | 0.32 | 0.01 |
| `arbutus/live-input` | synth | `audio_out` `events_in` `sidechain` | SOURCE *MIC* · GAIN *0 dB* | 0.39 | 0.00 | 2.52 | 0.02 | 11.60 | 0.11 |
| `arbutus/example` | synth (template) | `audio_out` `events_in` | WAVE *saw* · DETUNE *8 ct* · TONE *4 kHz* · LEVEL *0.5* | 11.03 | 0.10 | 41.73 | 0.39 | 181.08 | 1.70 |

Seven boxes take **assets** — content, not parameters (§3.2, DDR-037):
`arbutus/sampler` takes `sample` (one WAV), `arbutus/drum` and `arbutus/dbox`
take `kit` (one resolved `kit.json`, whose `pads` map it reads itself — files
and per-pad params for `drum`, **params alone** for `dbox`, which has no
samples), `arbutus/wavetable` takes `wavetable` (a 16-bit mono WAV of
`frames × 2048` samples, one cycle per frame, written by
`tools/mkwavetable.py`), `arbutus/stepmod`
takes `pattern` (a `{"kind":"stepmod","values":[…]}` JSON of up to 16 steps),
and `arbutus/endless` and `arbutus/pattern` both take `pattern` (a
`{"kind":"pattern","pattern":[[60,64],"tie",[]]}` JSON — a step is a list of
notes, `[]` is a rest, `"tie"` holds the step before it).

Numbers are `arbutus-run --bench`, best of five, 512 frames at 48 kHz, on this
Mac (Apple silicon, RelWithDebInfo), with the box's own test material — three
voices sounding for the synths. **They are a Mac number, not a CM4 number**, and
the device never uses them: §6.8 forbids a cost estimate and counts xruns
instead. They are here because §9.3 says `--bench` exists for authors who care.

**The CM4 columns** (stage 4b, #13, 2026-09-18) are `audio/tools/bench-all.py`
on the board: each box run with the notes, input and settings of its own first
golden test, best of five, 512 frames at 48 kHz (**except six rows**, below). CM4 on the official IO board,
Debian 13 trixie, kernel 6.18 `-rpi-v8` (`PREEMPT`, not RT), **GCC 14.2**,
RelWithDebInfo, governor `performance` at 1.5 GHz, 50 → 49 °C across the run,
not throttled. The ratio to the Mac is **3–15×**, widest on `wavetable`,
`chorus`, `fm` and `noise` — but the Mac rows were not all measured with the
same material, so read it per row, not as one number. The dearest box,
`chorus`, is **2.9 % of a core**. **Six rows of the board columns (CM4 and 3B+)
are not at 512 frames** (#626, the code review's m-audio-tools spec 5):
`bench-all.py` kept each test's own `--frames`, so `reverb` was benched at
**256** and `follower`, `random`, `envelope`, `motion` and `stepmod` at **128**,
while their Mac column is at 512. The µs/block of those rows is per 256- or
128-frame block and the % core is of that shorter period; at 512 the reverb's
CM4 cost is roughly 6.6× the Mac's, not 3.3×. `bench-all.py` now benches every
box at 512; those six rows stand until the next board run replaces them.

**The host's own offline render** (`audio/tools/bench-host.py`: ten seconds, the
host's default block of 128, `--rt-guard` on, the patch's notes held):

| Scenario | CM4 | % of real time | WSL desktop x86, for scale |
|---|---|---|---|
| `osc` `cold-glass`, 1 note | 0.108 s | 1.1 % | 0.3 % |
| same + `colourbass.rack`, 1 note | 0.165 s | 1.6 % | 0.4 % |
| `va` `glass-reed` + `colourbass.rack`, 8 notes | 0.761 s | **7.6 %** | 1.8 % |
| `wavetable` `init` + `colourbass.rack`, 8 notes | 0.567 s | 5.7 % | 1.0 % |

`rt_allocs` 0 in every one, now that the Linux guard counts (#52). **It fits**,
with more than an order of magnitude to spare on one core, before the UI
process exists.

**Re-run at both blocks for L4 (#141, 2026-09-23)**, because the device ships
64 now and the 7.9 % this paragraph used to predict for it was arithmetic.
Same tool, `taskset -c 3` so the isolated core is the only variable, the
target up, two agreeing runs at each cell:

| worst scenario (`va` 8 notes + `colourbass.rack`) | block 128 | block 64 |
|---|---|---|
| 1500 MHz | 8.6 % of a core | **8.8 %** |
| 600 MHz (C3's Zero-class proxy) | 24.1 % | **24.8 %** |

**Halving the block costs 2–3 % of the host's render, not the doubling the
budget note assumed.** What doubles is the *per-block* overhead — the call, the
mixer strip's once-per-block arithmetic, the ring drain — and on this engine
that overhead is a small part of the per-block *work*, which is per-sample and
unchanged. C3 and C4 (#76, #77) therefore lose nothing they were counting on.
`rt_allocs` 0 at both blocks. (The 128 column reads 8.6 % against the 7.6 %
above, measured on 2026-09-18: different day, different tree — #56's
`kCtrlStep` reads landed between them — and the target was up for this one.
Read the two columns against each other, not across the page.)

**Live, with realtime** (`audio/tools/xrun-watch.py`, five minutes of the 8-note
`va` + `colourbass` under a 30 Hz filter sweep, four busy loops on the CPU):
**0 xruns** in each of three runs — plain `SCHED_OTHER`; `SCHED_FIFO` 80 +
`mlockall` on core 3; and that on an isolated core 3. The xrun count cannot tell
them apart, so `cyclictest` (60 s, 1 ms, under the same four loops) is the
margin: worst wake-up **9.4 ms at `SCHED_OTHER`** — over three 2.67 ms periods —
against **35 µs at FIFO 80** on a loaded core and **10 µs on the isolated core**.
The clean `SCHED_OTHER` run was luck the scheduler happened to grant. The device
was `snd-dummy` (no codec on the IO board, no monitor on HDMI); `PI.md` § 6 has the
setup and what it cannot show.

### The whole system (C2, #75, 2026-09-19)

Everything above is the host alone. This is the device as it ships
(the slot `deploy.sh` installs, `arbutus-audio` under its unit: `SCHED_FIFO` 80,
memory locked, core 3), with the UI process attached (`arbutus_os/main.py`,
the IO-board mode: headless display, scripted input, cores 0–2) and a phone
transfer. Same CM4 on the IO board, `snd-dummy`, governor `performance`
at 1.5 GHz, 53–60 °C, not throttled. Method: `tools/c2-bench.sh` →
`arbutus_os/bench/c2-pi.sh`; raw JSON on the bench host under
`~/bench-logs/c2-run2/`. *Load* is `xrun-watch.py --tape`: the 8-note `va`
`glass-reed` + `colourbass.rack` chord, all eight tape media allocated, track 0
recording, 30 Hz meters on.

**The UI process, per page** (`main.py --stats`: CPU seconds over wall seconds
for the whole process, and the frame's work — input, `App.tick`, render, push —
per frame at the 60 Hz pace; 15 s a page):

| Page | host idle: % core | work p50 / p99 | host loaded: % core | work p50 / p99 | frames late (> 16.7 ms), loaded |
|---|---|---|---|---|---|
| SYNTH | 72 | 12.0 / 13.2 ms | 85 | 16.4 / 22.4 ms | **50 %** |
| DRUM | 76 | 13.0 / 14.1 | 78 | 14.4 / 20.6 | **50 %** |
| TRACK | 45 | 7.3 / 8.6 | 50 | 7.4 / 14.9 | 0 |
| MIXER | 52 | 8.6 / 9.8 | 58 | 8.7 / 15.6 | 0 |
| SEQ | 38 | 6.3 / 7.6 | 44 | 6.4 / 13.8 | 0 |
| TEMPO | 41 | 6.8 / 7.9 | 47 | 6.8 / 13.7 | 0 |
| INPUT | 68 | 11.3 / 12.5 | 74 | 11.6 / 19.0 | **49 %** |
| FILE | 40 | 6.5 / 7.6 | 45 | 6.6 / 13.2 | 0 |
| HELP | 43 | 7.1 / 8.3 | 47 | 7.2 / 14.4 | 0 |
| **all** | **52** | 7.3 / 13.1 | **58** | 11.4 / 22.1 | 15 % |

RSS 37 MB. The headless display is one 230 kB copy a frame; the P3 panel's
RGB565 conversion and dirty box (`drivers/st7789_display.py`) will cost more,
not less. SYNTH/DRUM/INPUT's late frames are not all CPU: those pages sit at
78–85 % of a core while half their frames overrun, so part of the work time is
waiting (the socket reader thread and the GIL). C5 (#78) profiles it.
**As measured by C5 (2026-09-19):** the waiting was not the reader thread (other
threads: 0 % of a core) but the UI **fighting the load client for the transport** —
`xrun-watch.py --tape` rolls the tape from a second connection, and the UI, whose
own transport still says stopped, sends `locate` back to its playhead and fetches
the whole `state` 20–30 times a second, 3–6 ms each (#99). With the UI rolling the
tape itself the same pages have 0 late frames. The panel's RGB565 step is
2.7 ms a frame here (4.5 at 600 MHz), on the push thread.
`docs/design/ui-cost-2026-09-19.md` has the rest.

**The whole system, five minutes** (UI parked on TRACK, load, the filter swept
at 30 Hz as an encoder would; then the same with a **1 GB file PUT to and GET
from the device over and over**, via `arbutus_os/bench/transfer_server.py` —
`device-os-spec.md` § 9.3's stdlib `http.server`, stood in — from the bench host
over the IO board's Ethernet). Per core is `/proc/schedstat` run time (exact;
`/proc/stat` is tick-sampled and aliases on core 3 — `sysmon.py` says why):

| | no transfer | with 1 GB transfers |
|---|---|---|
| core 0 / 1 / 2 / 3, mean | 29 / 5 / 25 / 25 % | 36 / 27 / 33 / 28 % |
| core 0 / 1 / 2 / 3, max (1 s) | 57 / 56 / 56 / 59 % | 91 / 63 / 64 / 60 % |
| `arbutus-audio` | 24.8 % of a core | 28.7 % |
| `arbutus-ui` | 54 % | 61 % |
| transfer server | — | 25 % mean, 65 % max |
| **xruns** | **0** | **0** |
| UI work p50 / p99 on TRACK | 7.9 / 16.0 ms | 9.3 / **28.6 ms** |
| UI frames late | 0.2 % | **27 %** |
| throughput | — | PUT 63 MB/s, GET 116 MB/s (11 rounds each) |
| `MemAvailable` low-water (of 3,789 MiB) | 1,422 MiB | **1,343 MiB** (page cache peaks at 1.9 GB) |

**RSS:** `arbutus-audio` 1,429 MiB with all eight 480 s media resident
(22 MiB before — the media are 8 × 176 MiB, exactly 480 s × 48 kHz × 2 ch ×
float32); `arbutus-ui` 38 MiB; the transfer server 20 MiB. At `--tape-seconds 120`
the media would be 8 × 44 MiB and the host ~375 MiB — **computed from that
arithmetic, not run**; C1 (#74) replaces the medium with a bounded pool anyway,
and § What the tape costs in RAM measures both modes.

**Watts: not measured.** The CM4 has no power sensor and the IO board no meter;
the ticket's idle / playing / scenario-2 × `performance` / `schedutil` table
needs an inline USB or 12 V meter on the IO board's input — Elfy's hands.

What it says: **the audio is not the constraint, the UI is.** The host's
worst five minutes use a quarter of its isolated core, the tape and a gigabit
transfer push no xrun, and 1.3 GB stays free with every medium resident. The
UI takes half an A72 core to draw 320×240 at 60 Hz with nothing moving, three
pages already miss 60 Hz half the time once the host is busy, and a phone
transfer makes TRACK drop a quarter of its frames. The host's own 25 % is
mostly its control thread, not the sound: without the 30 Hz filter sweep —
each `set_param` pushes the whole state document to every client
(`PROTOCOL.md` § State) — the same load is 14.7 %.

### Slower clocks, fewer cores (C3, #76, 2026-09-19)

The same device and the same scenarios as C2, at a lower clock and with fewer
cores, to see what a smaller module would leave. Six cells: `scaling_max_freq`
at **1500 / 1000 / 600 MHz** (governor `performance`, so the clamp *is* the
clock — `vcgencmd measure_clock arm` read 1500.3 / 1000.2 / 600.1 MHz) × **4 or 2
cores**. In each cell, `bench-host.py` on core 0 (with the UI stopped), C2's UI per
page with the host idle and then loaded, and C2's scenario 2: TRACK, the load,
the 30 Hz sweep and 1 GB PUT/GET from the bench host, five minutes, xruns counted.
Method: `tools/c3-bench.sh` (→ `arbutus_os/bench/c3-cell.sh`, `c3-restore.sh`,
`c2-pi.sh`), tables by `arbutus_os/bench/c3_report.py`; raw files on the bench
host under `~/bench-logs/c3-run1/`. 48–59 °C, never throttled.

**"2 cores" means cores 0 and 3, by confinement.** This CM4 has no
`/sys/devices/system/cpu/cpuN/online`: the firmware starts the secondary cores in
a way the kernel cannot reverse, so they cannot be taken offline. `maxcpus=2` would
need a `cmdline.txt` edit and would bring up cores 0–1, losing the isolated core 3.
So `c3-cell.sh` pins every bench process (user.slice) to core 0, the services
(system.slice) to 0 + 3 (the audio keeps core 3, the UI and the transfer server get
0), and every IRQ that can move to core 0. All of it is runtime-only. Left on cores
1–2: kernel threads and the per-core timer/PMU interrupts (≈ 1 % each in scenario 2).

| Cell (MHz × cores) | host offline, worst (% of real time) | UI, host idle (% core) | UI, host loaded (% core, frames late) | scenario 2: UI work p50 / p99 ms, late | scenario 2: UI / `arbutus-audio` (% core) | xruns (loaded pages / scenario 2) | PUT / GET MB/s | °C before → after (max) |
|---|---|---|---|---|---|---|---|---|
| 1500 × 4 | 7.6 | 52 | 58, 16 % | 17.3 / 55.9, 55 % | 70 / 30.2 | 0 / 0 | 65 / 116 | 56 → 57 (59) |
| 1000 × 4 | 11.4 | 74 | 74, 67 % | 24.5 / 169.1, 74 % | 73 / 38.9 | 0 / 0 | 66 / 116 | 57 → 54 (56) |
| 600 × 4 | 19.0 | 97 | 75, 100 % | 46.6 / 1672.9, 100 % | 68 / 58.4 | 0 / 0 | 64 / 106 | 54 → 49 (51) |
| 1500 × 2 | 7.6 | 52 | 57, 15 % | 17.9 / 50.2, 57 % | 56 / 29.5 | 0 / 0 | 57 / 99 | 50 → 54 (57) |
| 1000 × 2 | 11.4 | 75 | 75, 66 % | 41.1 / 76.3, 84 % | 54 / 39.5 | 0 / 0 | 56 / 80 | 54 → 52 (55) |
| 600 × 2 | 19.0 | 97 | 75, 100 % | 71.0 / 131.3, 100 % | 50 / 53.9 | 0 / 0 | 50 / 52 | 52 → 48 (49) |

The UI per page with the host idle, % of a core (work p99 ms); a dash is a page
the 150 s script never reached, because `pages.txt` waits in **frames** and a UI
below 60 Hz takes longer than 150 s to get through it:

| Page | 1500 × 4 | 1000 × 4 | 600 × 4 | 1500 × 2 | 1000 × 2 | 600 × 2 |
|---|---|---|---|---|---|---|
| SYNTH | 72 (17.6) | 99 (27.0) | 98 (47.4) | 73 (17.9) | 99 (29.0) | 98 (47.7) |
| DRUM | 76 (18.1) | 96 (26.5) | 96 (48.7) | 76 (18.5) | 96 (29.8) | 96 (48.5) |
| TRACK | 45 (10.0) | 65 (16.5) | 98 (30.4) | 45 (9.9) | 66 (17.6) | 98 (31.1) |
| MIXER | 52 (12.5) | 76 (18.9) | 98 (34.8) | 52 (11.1) | 77 (20.1) | 98 (38.6) |
| SEQ | 39 (8.7) | 56 (13.7) | 90 (27.3) | 38 (8.8) | 56 (14.2) | 91 (27.2) |
| TEMPO | 41 (9.2) | 60 (14.7) | 96 (28.9) | 41 (9.1) | 61 (16.1) | 98 (29.1) |
| INPUT | 68 (15.7) | 98 (27.5) | 98 (45.1) | 68 (16.5) | 99 (23.6) | 98 (46.1) |
| FILE | 40 (9.0) | 57 (13.8) | — | 40 (9.0) | 58 (15.9) | — |
| HELP | 43 (9.7) | 63 (15.7) | 98 (30.5) | 43 (9.6) | 64 (16.3) | 98 (31.2) |
| **all** | **52** (14.2) | **74** (20.7) | **97** (40.5) | **52** (14.1) | **75** (20.8) | **97** (41.3) |

What it says:

- **The audio has no floor in this range.** 0 xruns in all twelve loaded runs,
  down to 600 MHz on two cores with a gigabit transfer on the other core. The
  offline render scales exactly with the clock (7.6 → 11.4 → 19.0 %, the ratio of
  1500 : 1000 : 600), and the live host's share of core 3 in scenario 2 goes
  30 → 39 → 54–58 %, most of it the control thread pushing the state document
  for each swept `set_param` (C2), not the sound. Two cores or four makes no
  difference to it: it has core 3 to itself either way.
- **The UI fails first, and it fails on the clock, not the core count.** It is
  one Python thread, so idle it costs the same on two cores as on four. At
  1000 MHz SYNTH, DRUM and INPUT are already at 96–99 % of a core **with nothing
  moving** (p99 27 ms, over the 16.7 ms frame); at 600 MHz every page is, and the
  script cannot visit all nine pages in 150 s because it runs below 60 Hz.
  Loaded, 67 % of frames are late at 1000 MHz and all of them at 600.
- **Two cores cost the transfer and the UI, not the audio.** In scenario 2 the
  1 GB transfer lands on the UI's core: at 1500 MHz nothing much changes, at
  1000 MHz the UI's work p50 goes 24.5 → 41.1 ms, and at 600 × 2 the transfer
  itself drops to 50 / 52 MB/s. Core 0 sits at 88–96 % in the 2-core cells.
- **These are the UI before C5b (#98).** C5 measured its pixel-identical drawing
  levers taking the UI 52 → 17 % of a core at 1500 MHz and 98 → 39 % with no late
  page at 600 MHz; until those land, this table is the ceiling, not the forecast.
  The loaded rows also carry #99 (the UI fights the load client for the tape,
  C5), which is why scenario 2 at 1500 × 4 read 55 % late here and 27 % in C2:
  that fight is noisy run to run.

**How to read it for a smaller module.** A clock clamp emulates *frequency*, not
*microarchitecture*. The Pi Zero 2 W's Cortex-A53 is in-order and has roughly half
the A72's IPC on branchy interpreter code, and about two-thirds on NEON DSP, so the
**Zero 2 W at 1 GHz sits near this table's 600 MHz rows for the UI and near
~700 MHz for the host**. That is a rough map, which is why C4 (#77) still measures
the real board. The 2 GB / 1 GB CM4 SKUs are the *same silicon* and need no clamp:
they differ only in RAM (C1, #74) and storage (C6, #79).

### A real A53 (C4, #77, 2026-10-06)

The borrowed **Pi 3 B+ at Zero 2 W clocks** (1 GHz, `total_mem=512`, `install.sh --board bare`,
`snd-dummy`; `arbutus_os/DEV-BOARD-3BPLUS.md`), the same rigs as C2, C3 and C5. The box table's
last two columns are `bench-all.py` there (the tree cross-built by #568). Full account, and the
ranked list of what to optimise: `docs/design/c4-a53-floor-2026-10-06.md`.

| | 3B+ @ Zero clocks | CM4 @ 1500 | CM4 clamped to 600 |
|---|---|---|---|
| host offline, worst (`va` 8 notes + `colourbass`, block 128) | **28.1 %** of a core | 8.6 % | 24.1 % |
| live, C2's scenario, 5 min: xruns (no transfer / 1 GB transfers) | **0 / 0** | 0 / 0 | 0 / 0 |
| live: `arbutus-audio`'s share of core 3 | 59.8 / 65.5 % | 24.8 / 28.7 % | 58.4 % (C3) |
| UI frame at 60 Hz, mean of nine pages (C5's profiler, as landed) | **43 %**, 0 late | 17 % | 39 %, 0 late |
| UI process on TRACK in C2's scenario: % core, late frames | 61.7 %, 0.5 % → with transfers 66.6 %, **2.4 %** | 54 %, 0.2 % → 61 %, 27 % (before C5b) | — |
| core 0 / 1 / 2 / 3 mean (no transfer → transfers) | 29/23/31/59 → 40/39/41/65 % | 29/5/25/25 → 36/27/33/28 % | — |
| `MemAvailable` low-water (no transfer / transfers) | **125 / 116 MiB** of 415 | 1,422 / 1,343 of 3,789 | — |
| transfer throughput | PUT 8.9 / GET 10.1 MB/s (**Wi-Fi**) | 63 / 116 (Ethernet) | — |
| key-to-sound jitter, the reader in `arbutus-keys` (idle / load) | **4.4 / 3.1 ms** (≤ 2 ms target missed; #578: 1–2 % of presses 60–570 ms late) | 1.3–1.4 / 1.3–1.7 | — |
| °C (max), throttled | 69, never | 60, never | 51, never |

**The A53 is a CM4 at ~550 MHz for the UI and ~450 MHz for the DSP**, not the ~600 / ~700 C3
estimated: the in-order core costs the NEON loops more than the interpreter. Neither matters
yet (0 xruns, 0 late frames); what misses is the key reader, and what costs the live host its
core is still the control thread pushing the state document per swept `set_param`, now
measured from the outside: **14.7 KB, 20 times a second, 298 KB/s to every client**.

## Input latency

The software path (P7, #73, 2026-09-19): a `note` written
to the host's socket → the start of the audio block that carries it
(`audio/tools/latency-probe.py`, run by `latency-probe-pi.sh`). Live on the CM4,
`snd-dummy`, `arbutus-audio` in a transient unit with the service's own
realtime properties (`SCHED_FIFO` 80, memory locked, core 3), 400 presses per
cell spaced 15–45 ms at random against the block clock. *Load* is the
`va` `glass-reed` + `colourbass.rack` 8-note chord held (the worst host case
above) with the transport rolling, tape track 0 recording and 30 Hz meters on.

| Block | | total p50 | total p99 | total max | socket p50 / max | ring wait max | missed a block |
|---|---|---|---|---|---|---|---|
| 64 (1.33 ms) | idle | 0.77 ms | 1.41 ms | 1.44 ms | 0.06 / 0.18 ms | 1.29 ms | 0 |
| 64 | load | 0.77 ms | 1.43 ms | 1.47 ms | 0.08 / 0.93 ms | 1.20 ms | 0 |
| 128 (2.67 ms) | idle | 1.34 ms | 2.71 ms | 2.80 ms | 0.06 / 0.21 ms | 2.59 ms | 0 |
| 128 | load | 1.47 ms | 2.76 ms | 2.77 ms | 0.08 / 0.93 ms | 2.43 ms | 0 |
| 256 (5.33 ms) | idle | 2.77 ms | 5.35 ms | 5.43 ms | 0.06 / 0.22 ms | 5.22 ms | 0 |
| 256 | load | 2.79 ms | 5.36 ms | 5.44 ms | 0.08 / 0.96 ms | 4.90 ms | 0 |

What it says: the path is **one block of quantisation plus under a
millisecond of socket**. The ring wait is uniform over one period (p50 ≈ half a
block), and in 2,400 presses **no note waited past the next block** — the bug
#73 was looking for is not there, idle or loaded, and no xrun was pushed. The
total p99 sits 0.4–8 % over one block by construction: a note that arrives
just after a block started waits the whole next period, plus the socket.
The onset is **frame 0 of that block, never inside it** (`tests/host_probe.py`;
PROTOCOL.md § Timing), so the one-host-block term of `device-os-spec.md`
§ 1.3's 20 ms budget is measured, not assumed. What this does not include:
the ATmega scan and UART hop, the UI's tick (keys go through it, § 8.1), and
the device buffer + WM8960 DMA after the block — the dummy proves nothing there.

### A MIDI note, through the UI (P9, #89, 2026-09-19)

The front half P7 left out: a MIDI note-on written to a `snd-virmidi` port →
ALSA sequencer → `arbutus_os/midi.py`'s reader thread (stamped on arrival,
queued) → the UI's 60 Hz frame `drain()` → `App.on_key` → `note` on the socket →
the carrying block. Live on the CM4 through the **real units** as they boot
(`arbutus.target`, block 128, `snd-dummy`), 400 notes per cell 15–45 ms apart.
The UI stamps each note-on's `note` with `probe` = its arrival time when
`ARBUTUS_MIDI_PROBE` is set (a systemd drop-in the bench adds and removes), and
the bench client reads the host's `probe` push, so one clock covers the whole
path. `arbutus_os/bench/p9-pi.sh` + `p9_midi_latency.py`; *load* is P7's.

| | total p50 | total p99 | total max | ALSA → reader p50 / p99 | queue (arrival → host) p50 / p99 / max | ring p50 / p99 | over one frame + one block |
|---|---|---|---|---|---|---|---|
| idle | 9.48 ms | 18.87 ms | 19.14 ms | 0.20 / 2.23 ms | 7.74 / 16.55 / 16.62 ms | 1.37 / 2.43 ms | 0 of 400 |
| load | 9.44 ms | 18.98 ms | 19.24 ms | 0.21 / 2.00 ms | 7.76 / 16.57 / 16.80 ms | 1.45 / 2.42 ms | 0 of 400 |

What it says: the queue wait is **uniform over one frame** (p50 ≈ half of
16.7 ms, max ≈ all of it) and the rest is P7's block, so a MIDI note is
**~9.5 ms typical, ~19 ms worst** from the wire to its block — and never past
one frame + one block (19.33 ms), which is the "one tick" § 8.1 of
`device-os-spec.md` budgeted, now measured. The UI's own dispatch (decoder →
`on_key` → socket write) is inside the queue column and does not show above
the frame's own spread. The ALSA sequencer + rtmidi's thread add 0.2 ms
typical, 2 ms at p99. This is the *before* for #96's L3 (the note leaving the
frame clock): what L3 can remove is exactly the queue column.

**The first run found a host bug instead** (fixed, `tests/host_slowclient.py`):
the host accepted **blocking** sockets, so a client that stopped reading —
the bench client itself, asleep between notes while the UI's 30 Hz meters
were pushed to it — filled its socket and blocked the host's control loop on
`write()`. Every other client stalled with it: the UI's `note` went unanswered
for **8.7 s**, and queue waits of 1–8 s looked like MIDI latency. A phone on
TCP 7401 whose Wi-Fi drops without a reset is the same client. Accepted
sockets are non-blocking now (the loop already polled for `POLLOUT`), and a
client with more than 8 MiB waiting is dropped.

### A key press, before and after L3 (P23, #127, 2026-09-20)

The same shape as the MIDI number above, with the injection point moved to
the ATmega's serial port: a `KP:rr:cc` line on the port → the UART reader
thread parses it → **either** it writes `note` itself (L3, #125) **or** the
event waits for the UI's 60 Hz frame as it used to → the carrying block.
Live on the CM4 through the real units on **one boot** (`arbutus.target`,
block 128, `snd-dummy`), 400 presses per cell 15–45 ms apart.

There is no ATmega on the bench carrier, so the bench is one:
`arbutus_os/bench/p23_key_latency.py` makes a pty pair, answers `P` with
`PONG` (and eats the argument byte of `B`/`L`, which may itself be `0x50`),
and writes the key lines; `p23-pi.sh` gives `arbutus-ui` a drop-in with
`ARBUTUS_KEY_PROBE=1` and an `ExecStart` pointing at the pty, runs the four
cells and undoes everything. *Before* is `--no-fast-notes`; *load* is P7's.

| | | total p50 | p99 | max | jitter p99−p1 | port → parse p50 / p99 | → the host p50 / p99 / max | ring p50 / p99 | over frame + block |
|---|---|---|---|---|---|---|---|---|---|
| **before** | idle | 10.01 ms | 18.65 | 18.96 | 16.98 | 0.23 / 4.31 | 7.69 / 16.50 / 16.68 | 1.38 / 2.43 | 0 of 400 |
| **after** | idle | **2.25 ms** | **6.48** | **7.00** | **5.98** | 0.23 / 4.25 | 0.21 / 1.27 / 2.50 | 1.39 / 2.39 | 0 of 400 |
| **before** | load | 10.04 ms | 19.40 | 20.28 | 17.80 | 0.30 / 4.38 | 8.13 / 16.63 / 16.92 | 1.30 / 2.43 | 5 of 400 |
| **after** | load | **2.45 ms** | **6.57** | **7.08** | **5.96** | 0.26 / 4.41 | 0.23 / 2.88 / 3.84 | 1.33 / 2.40 | 0 of 400 |

What it says: **the frame wait is gone.** The "→ the host" column was a wait
for the frame's `drain()`, uniform over one frame (7.7–8.1 ms p50, 16.7 ms
max); after L3 it is one socket write, 0.2 ms typical. **−7.8 ms typical,
−12.2 ms at p99 and −11.0 ms of jitter**, the same idle and loaded, and every
note accounted for: `fast notes: 800 sounded off the tick, 800 tick copies
swallowed` on the unit's exit is exactly 400 presses + 400 releases, so none
sounded twice and none went missing. 0 xruns in all four cells — a second
client writing from a non-RT thread costs the host nothing.

The *before* cells are the cross-check: 10.0 / 18.6 ms against P9's MIDI
9.5 / 18.9 through a completely different front end. Same tick, same answer.

**The new largest term is the reader thread's wake-up** — the "port → parse"
column, 0.23 ms typical but **4.3 ms at p99 and bounded just under 4.7 ms in
every cell**, which was then most of the software path's remaining jitter and
had never been measured. (**Superseded 2026-09-23**: C5b took this term to
1.92 ms at p99 and the block is the largest one now — § The reader thread's
wake-up, before and after C5b.) It is *not* Python's GIL switch interval: with the
UI run at `sys.setswitchinterval(0.0005)`, a tenth of the default, it did not
move (p99 4.66 ms) and the socket write after it got worse (p99 1.27 →
4.37 ms). A single long GIL-holding C call inside the render fits the shape;
that is C5b's ground (#98) and is filed as **#129**.

Two bugs had to be fixed before any of this could be measured, both in front
of the seam rather than in it, and the first is the one that mattered:

- **The reader held every key line for the port's whole read timeout.**
  `serial.Serial.read(size)` is *exactly `size` bytes or the timeout*, and the
  driver asked for 256, so a 9-byte line waited 200 ms and then arrived in a
  batch with five others stamped at the same instant (8.8 ms p50, 173 ms max,
  6 of 30 presses distinct). On a board that is 0–200 ms in front of every
  key, larger than the whole key-to-sound budget, and it would have voided L3
  completely while looking like an ATmega fault. `device-os-spec.md` § 6.2.2.
- **`systemctl stop` skipped the UI's exit path**, so the `fast notes:`
  counters above had never been printed on a device at all (§ 4.2.1).

What this still does not include: the ATmega's scan and debounce (L1, #37),
the 115200 wire itself — **a pty has no baud rate**, so add ~0.8 ms for a
9-byte line on a real board — and the codec DMA after the block (P10, #90).

### A MIDI note, before and after the seam (P130, #130, 2026-09-22)

The *after* number P9 left owing. #128 gave a MIDI note-on L3's seam — rtmidi's
thread sounds it against a four-field snapshot the tick republishes — and every
test in that ticket drove a fake ALSA backend, so what they proved is that the
note is written before the frame, not that it arrives sooner. This is P9's rig
unchanged (`p9_midi_latency.py`, so the *before* cells have to reproduce P9's
published numbers or the rig is measuring itself) in P23's four-cell shape:
`arbutus_os/bench/p130-pi.sh`, one boot, 400 notes per cell 15–45 ms apart,
block 128, through the real units. *Before* is `--no-fast-notes`, which since
#128 turns the seam off for MIDI as well as the keys; *load* is P7's.

| | | total p50 | p99 | max | jitter p99−p1 | ALSA → reader p50 / p99 | → the host p50 / p99 / max | ring p50 / p99 | over frame + block |
|---|---|---|---|---|---|---|---|---|---|
| **before** | idle | 10.46 ms | 18.85 | 19.57 | 17.72 | 0.24 / 3.75 | 8.62 / 16.58 / 16.87 | 1.36 / 2.42 | 1 of 400 |
| **after** | idle | **2.17 ms** | **4.98** | **6.51** | **4.44** | 0.24 / 3.55 | 0.24 / 0.56 / 0.69 | 1.50 / 2.38 | 0 of 400 |
| **before** | load | 10.23 ms | 19.14 | 19.81 | 17.55 | 0.25 / 4.05 | 8.50 / 16.59 / 17.10 | 1.35 / 2.41 | 2 of 400 |
| **after** | load | **2.25 ms** | **6.02** | **7.08** | **5.46** | 0.26 / 4.08 | 0.26 / 2.93 / 3.22 | 1.35 / 2.39 | 0 of 400 |

What it says: **the frame wait is gone from a MIDI note too.** The "→ the host"
column was a wait for `drain()`, uniform over one frame (8.5–8.6 ms p50,
16.9 ms max); it is now one socket write at 0.25 ms. **−8.3 ms typical,
−13.9 ms at p99 and −13.3 ms of jitter** idle (−8.0 / −13.1 / −12.1 loaded),
**0 xruns in every cell**, and `midi fast notes: 800 sounded off the tick, 0
failed` on each *after* cell's exit is exactly 400 note-ons + 400 note-offs —
none sounded twice, none missing, and the shared `fast notes: 800 … 800 tick
copies swallowed` agrees, the scripted input driver having no keys of its own.
A MIDI note now lands within a few tenths of a millisecond of the instrument's
own keys (P23: 2.25 / 6.48 / 7.00), which is what the arithmetic predicted,
because what is left of the two paths is the same two terms.

The whole run was done twice, ten minutes apart, and the two agree within
0.5 ms on every figure in the table. The *before* cells reproduce P9's
9.5 / 18.9 / 19.1 ms to within 1 ms at p50 and 0.5 ms at p99 — a year-old
number through the same front end, which is the cross-check that says the rig
measures the path and not itself.

**The reader thread's wake-up is the same on rtmidi's thread as on the UART's,
and that is evidence for #129.** The "ALSA → reader" column here and P23's
"port → parse" column are the same measurement on two different threads
running two different C libraries — `python-rtmidi`'s ALSA callback against
`pyserial` on a pty — and the distributions are indistinguishable:

| | p50 | p75 | p90 | p95 | p99 | max | > 1 ms | > 2 ms |
|---|---|---|---|---|---|---|---|---|
| UART reader (P23) | 0.23 ms | — | — | — | 4.3 | < 4.7 | — | — |
| rtmidi, idle | 0.24 ms | 0.31 | 1.06 | 1.79 | 3.55 | 4.37 | 11.0 % | 4.5 % |
| rtmidi, load | 0.26 ms | 0.37 | 1.28 | 2.18 | 4.08 | 4.55 | 13.2 % | 5.5 % |

It is unchanged by the seam (the *before* cells measure 0.24 / 3.75 / 4.25 and
the *after* 0.24 / 3.55 / 4.37), unchanged by the host's load — which runs in
another process — and bounded in all eight cells of both runs just under
4.7 ms, the same ceiling P23 found. So it is not `uart_input.py`, not
`pyserial`, and not the pty: what the two threads share is the GIL and the
main thread's render, which is exactly #129's hypothesis, and **#129 was then
the largest single term of the MIDI path as well** — 3.6–4.1 ms of a 5.0–6.0 ms
p99. (**The hypothesis was right and the term is mostly gone since 2026-09-23**
— § The reader thread's wake-up, before and after C5b.) The shape does not fit one rectangular C call of 4.5 ms (that would put
21 % of arrivals past 1 ms *and* 15 % past 2 ms; the measurement is 11–13 %
and 4.5–5.5 %), so it reads as a handful of GIL-holding calls of different
lengths inside one frame, the longest ~4.5 ms and the rarest.

**The 24-ppqn clock still measures the wire.** #128 deliberately left CC,
pitch bend and the clock on the frame, so the same boot ends with a clock
cell: `p130_clock.py` turns FILE › MIDI's `CLOCK IN` on and TEMPO's `SYNC` to
`midi` on disk (both are read when the App is built, so this happens with the
UI down), pulses 0xF8 at 132 BPM for six seconds — 317 pulses, median interval
18.9394 ms against a nominal 18.9394 — and then reads the followed tempo
**back off the host**, because a followed clock is one `tempo {bpm}` on the
socket (DDR-016) and that needs no pixels. The host went from its boot 120.000
to **132.051** (run 2: 131.953) against 132.000 on the wire, 0 xruns, and both
files were put back with the UI down again so `app.flush()` could not persist
either. 0.05 BPM is `TempoPage.poll_external`'s own 0.05 deadband, i.e. the
clock is followed as closely as the page will act on.

What this still does not include: the USB transport in front of a real
keyboard (`snd-virmidi` is a kernel loopback, so a 1 ms USB frame is not in
these numbers — the same caveat P9 carried), and the codec DMA after the block
(P10, #90).

### The reader thread's wake-up, before and after C5b (#129, 2026-09-23)

P23 and P130 both ended at the same place: the input thread's wake-up — a line
landing in the kernel to the Python callback that handles it — is 0.23 ms
typical but **4.3 ms at p99 and bounded just under 4.7 ms**, on two different
threads running two different C libraries, and that is most of what is left of
the path's jitter. The reading was that the thread is waiting for the GIL,
held by the main thread inside the render. C5b (#98) made the render about a
third of what it was, so the test is the same four cells with the switch
changed: the *before* is no longer `--no-fast-notes` but **which render the UI
started with**.

`arbutus_os/bench/p129-pi.sh` is P23's rig with that one change.
`ref` is `bench/c5_before.py` — `main.py` with `c5_levers.install_reference()`
putting the five pre-C5b bodies (`emulator/testing/render_reference.py`) in
before the first frame — and `c5b` is `main.py` as it ships. Fast notes are on
in every cell, so these are comparable to P23's *after* cells. One boot, 400
presses per cell, block 128, SYNTH on screen, `snd-dummy`:

| | | total p50 | p99 | max | jitter p99−p1 | port → parse p50 / p99 / max | → the host p50 / p99 | ring p50 / p99 | UI, % of a core |
|---|---|---|---|---|---|---|---|---|---|
| **ref** | idle | 2.91 ms | 8.72 | 9.74 | 8.10 | 0.76 / 6.95 / 7.44 | 0.21 / 5.28 | 1.37 / 2.38 | 88.5 % |
| **c5b** | idle | **2.06 ms** | **4.36** | **4.91** | **3.84** | **0.22 / 1.92 / 2.12** | 0.20 / 0.62 | 1.50 / 2.40 | **31.2 %** |
| **ref** | load | 3.10 ms | 9.82 | 10.04 | 9.17 | 0.83 / 7.66 / 8.70 | 0.23 / 5.26 | 1.46 / 2.37 | 87.7 % |
| **c5b** | load | **2.32 ms** | **5.02** | **5.98** | **4.48** | **0.23 / 2.22 / 3.94** | 0.22 / 2.87 | 1.52 / 2.38 | **37.6 %** |

**The wake-up is a property of the render, and it fell with it.** The term is
**0.76 → 0.22 ms at p50 and 6.95 → 1.92 at p99**, 3.6× either way, while the
UI's own cost goes 88.5 → 31.2 % of a core — the same **3.1×** C5 predicted
from its 52 → 17 % (a different page and a different rig, so the ratio is the
part that carries). The socket write behind it moves with it too, 5.28 → 0.62
at p99. 0 xruns in every cell; 400/400 presses answered and 400 distinct;
`fast notes: 800 sounded off the tick, 800 tick copies swallowed` on every
cell's exit.

Against P23's published *after* cells (0.23 / 4.25 / < 4.7 for the same term),
today's shipping code is **−2.3 ms at p99 and −2.6 ms at max**, and the whole
path is **2.25 / 6.48 / 7.00 → 2.06 / 4.36 / 4.91** idle with jitter
**5.98 → 3.84 ms**. Key-to-sound in software is now comfortably inside the
ratified ≤ 8 ms typical / ≤ 16 ms worst (#96); **jitter, at 3.84 ms against
≤ 2 ms, is the only one of the three still missed.**

**What it costs to know: three runs, and the first one is thrown away.** Run 1
was started four minutes after a full `deploy.sh` build on the CM4 and reads
1.5–2.5 ms high on every p99 (c5b-idle 4.23 against 1.92) with the UI 7–8
points of a core higher in every cell. Runs 2 and 3, forty minutes later,
agree with each other within **0.3 ms on every figure in the table** and are
what it reports. A bench number taken on a box whose 15-minute load average
still says 3 is not a bench number.

**The `ref` cells are not literally the code P23 ran**, and should not be read
as a second measurement of it: they are *today's* `main.py` with five
functions swapped, and `main.py` has gained the three device-socket pumps
(#97, #126, #134, #140) on the main thread's frame since 2026-09-20. That is
why `ref` is worse than P23's *after* rather than equal to it. The A/B inside
one boot is the measurement; the comparison across dates is indicative.

**The largest software term is no longer this one — it is the block.** In the
`c5b` cells the command ring is 2.40 ms at p99 against the wake-up's 1.92, and
it contributes 2.30 ms of the path's 3.84 ms of jitter against the wake-up's
1.73. The ring is one block at 128 frames and is **quantisation**: a press
arrives at a uniform phase inside the block that carries it, so it cannot go
below that without a smaller block. That is **L4** (block 64,
`latency-budget-2026-09-19.md`), which waited on C5 and no longer does — and
it, not a hunt for the longest GIL-holding call, is what the ≤ 2 ms jitter
figure now turns on. **Done the same day: the next section.**

### The block halved: L4 as measured (#141, 2026-09-23)

The section above ends with the command ring — the wait from the host reading
`note` off its socket to the first sample of the block that carries it — as the
largest software term in key-to-sound and 2.30 ms of its 3.84 ms of jitter, and
says the only thing that halves it is halving the block. **The device runs
`--block 64` from this ticket** (`arbutus-audio.service`; the host's default and
the emulator stay at 128), and this is the before and after.

`arbutus_os/bench/p141-pi.sh` is P129's rig with a third switch: an
`arbutus-audio` drop-in carrying the cell's `--block`, written from the unit's
own `ExecStart` with one flag substituted, so a unit that gains a flag cannot
lose it here. Four cells on one boot — {128, 64} × {idle, load} — the shipping
`main.py` and fast notes on in every one, so these compare directly with
#129's `c5b-*` cells. **Three runs**; the figures are the median of the three
and the spread is below. 400 presses a cell, SYNTH on screen, `snd-dummy`,
1500 MHz, `performance`, 50–55 °C, not throttled.

| | | total p50 | p99 | max | jitter p99−p1 | port → parse p99 | **ring** p50 / p99 / jitter | `arbutus-audio`, % of core 3 |
|---|---|---|---|---|---|---|---|---|
| **128** | idle | 2.00 ms | 4.15 | 4.83 | 3.65 | 1.98 | 1.42 / **2.38** / 2.29 | 10.6 % |
| **64** | idle | **1.24 ms** | **3.08** | **3.68** | **2.64** | 1.91 | 0.70 / **1.16** / **1.11** | **12.1 %** |
| **128** | load | 2.10 ms | 5.01 | 7.54 | 4.47 | 2.21 | 1.26 / **2.38** / 2.31 | 22.9 % |
| **64** | load | **1.39 ms** | **4.19** | **5.93** | **3.70** | 2.15 | 0.66 / **1.16** / **1.10** | **24.6 %** |

**The ring halved, to three decimal places, and it is the only term that
moved.** 2.38 → 1.16 ms at p99 and 2.29 → 1.11 of jitter, idle and loaded
alike; the reader thread's wake-up and the socket hop in front of it are
unchanged within the noise, which is what a quantisation term that has been
halved should look like and is the positive check that nothing else was
disturbed. The whole path is **2.00 / 4.15 / 4.83 → 1.24 / 3.08 / 3.68 ms**
idle, and its **jitter 3.65 → 2.64 ms**.

**The ring is the most reproducible number in this file**: its p99 across the
three runs was 2.378 / 2.376 / 2.378 at 128 and 1.161 / 1.159 / 1.164 at 64 —
±0.003 ms. That is because it is not a wait on anything, it is arithmetic on
the phase of arrival, and it is the evidence for calling it quantisation rather
than contention. Everything else spread more: the whole path's idle jitter read
2.64 / 2.34 / 2.93 at 64, so read that column as **~2.6 ± 0.3**.

**What it cost:** `arbutus-audio` goes **10.6 → 12.1 % of core 3** idle and
**22.9 → 24.6 %** under the 8-note `va` + `colourbass` load with a take
recording — **+1.5 points**, twice as many ALSA callbacks for the same
per-sample work. The offline render alone (§ The whole system, `bench-host.py`
at both blocks) is +0.2 points, so the difference is the callback rate, not the
engine. **0 xruns in all twelve cells**, 4,800 presses, 400/400 answered and
400 distinct in every one, and not a single press past one frame + one block.

**Against #96's ratified figures** — ≤ 8 ms typical, ≤ 16 ms worst, ≤ 2 ms
jitter — typical and worst now have five times the room they need, and **jitter
is still missed: 2.64 ms against 2.** L4 was always the last *software* term
and never the last term; `latency-budget-2026-09-19.md` § 4 says what is left,
and all of it is the board (L1's eager debounce and L2's 1 kHz scan on P3's SPI
expander, which is the −4.5 ms of jitter).

**And the ordering has swapped back.** With the ring at 1.11 ms of jitter, the
largest single contributor is the **UART reader thread's wake-up** again — 1.91
at p99, ~1.7 of jitter — which #129 proved is the main thread holding the GIL
inside the render. So the software term in front of ≤ 2 ms is once more a
render cost, not a block; a second C5-shaped cut, or getting the reader off the
GIL, is what would move it. Nobody has scoped that, and the pty rig understates
the real path anyway: a pty has no baud rate, so the ~0.8 ms an ATmega spends
clocking the line out is missing from every number here, and the 0–5 ms scan
in front of it is L2's.

One thing the gate now covers that it did not: **`ragged.sh` renders 64**. It
tested 512 against {128, 97, 2048, 1}, which brackets 64 without testing it,
and the device had just started shipping a block the gate never rendered. All
24 boxes and the negative control are green at 64 with no golden moved, in
0.69 s — the `arb::kCtrlStep` rule (#56) holding exactly where it was supposed
to.

### The reader out of the UI's process: `arbutus-keys` (#504, 2026-10-05)

#129's step 3. After C5b and L4 the reader's wake-up was still the largest term
(1.9–2.2 ms p99 above, and § L4's 1.91), because a thread inside the UI waits for
the interpreter lock while the main thread draws. `arbutus-keys`
(`arbutus_os/keys_service.py`, `device-os-spec.md` § 1.3.3) runs the same driver in
a process that draws nothing and forwards the events to the UI.
`arbutus_os/bench/p504-pi.sh`: P23's pty rig, block 64, 400 presses a cell, the
cells `inproc` (`--input uart`, the code as it shipped, #129's `c5b`) and `keys`
(`arbutus-keys` + `--input keys`), four runs on one boot (15-minute load 1.4–1.7):

| | `arrive-t0` p99 | presses > 1 ms | `t1-t0` p99 | jitter (p99 − p1) |
|---|---|---|---|---|
| inproc idle | 2.00 – 2.18 ms | 24 – 30 / 400 | 3.17 – 3.37 ms | 2.69 – 2.92 ms |
| inproc load | 2.01 – 2.44 | 27 – 37 | 3.14 – 3.46 | 2.66 – 2.98 |
| **keys idle** | **0.34 – 0.39** (one run 1.80) | **1 – 6** | **1.83 – 1.87** (one 3.33) | **1.34 – 1.38** (one 2.80) |
| **keys load** | **0.33 – 0.81** | **2 – 3** | **1.81 – 2.21** | **1.32 – 1.70** |

Predicted ≤ 0.3 ms and ≤ ~1.2 ms: both narrowly missed, and the term is no longer
the largest. What is left of the jitter is the ring's 1.10 ms (`t1-t_ctl`). The
≤ 2 ms target holds in 7 of 8 `keys` cells, 0 of 8 `inproc`. 800 sounded / 800
swallowed and 0 xruns in every cell; `arbutus-keys` costs ~3 % of a core and
16.5 MB resident. Raw: `~/bench-logs/p504-20261005T08*` on the bench CM4.

### The tape's commit on the eMMC (C1 step 2, #74, 2026-09-24)

`tape-streaming-design.md` § 6.2 hangs C1's write-behind on one number: **what
one range's commit costs** — the 384 KiB `pwrite` at its own offset, the 4 KiB
header rewritten, one `fdatasync` — on a file that is *growing*, against C6's
0.54 + 10 ms (#79, `fio` on a file already laid out). The threshold it sets is
**~20 ms at p99**; over that, § 3.4's fallback — a `fallocate` lead a few ranges
ahead of the write head — becomes the follow-up.

`arbutus_os/bench/c1-pi.sh` is the rig and it times the **production** commit,
not a re-implementation of it: the host's own `state.tape.io.commit_us`, over a
120-range (two-minute) take through a private `--offline --tape-dir` host, so
`arbutus-ui` and `arbutus-audio` keep playing and nothing on the card is
competing. `/data` on this CM4 is a directory on the **ext4** root filesystem
(`OS-5`), 1.4 GB free of 6.6 — 80 % full. 0 dropped, 0 absent, 0 errors, 121
commits and 121 syncs in every run; the 117-slot pool evicted 7 and fetched
back 0, which is what a take that nothing is playing back should do.

| cadence | wall | p50 | p99 | max |
|---|---|---|---|---|
| **paced — one range per 1.024 s, a take** | 122.9 s | **12.01 / 12.03 / 12.10 ms** | **65.1 / 77.7 / 87.5 ms** | 81.8 / 83.9 / 92.1 ms |
| flat out — as fast as the host renders | 2.5–2.7 s | 11.63 / 11.66 / 11.68 ms | 101.6 / 102.1 / 221.9 ms | 102.5 / 104.3 / 250.2 ms |

**The median is C6's number.** 12 ms against C6's 0.54 + 10, in every run, to
within half a millisecond. **The tail is not a number this rig could read**, and
that took a second measurement to find out — see the section below, which
replaces the conclusion this one drew.

**The pacing row is there because the obvious explanation was tested.** The
first three runs read p99 64–102 ms and took 2.5 s of wall clock for a
two-minute take: the host renders on demand, so 46 MiB of `fdatasync`ed 384 KiB
writes went out at ~18 MiB/s, fifty times a take's rate, and the tail of a card
driven flat out is not the tail of a tape. `--pace` (now the probe's default)
hands over no more than a second of audio per second of wall clock. It moved
the p99 from ~102 ms to ~78 and left the median alone, which was read at the
time as *the cost is in growing the file, not in the rate*. **That reading was
wrong**, and #158 is where it was caught.

**Nothing is stalling.** A range buys 1.024 s of cover and the worst commit ever
seen is 170 ms, so one take has an order of magnitude of headroom. What makes
the tail worth understanding is that eight tracks recording at once is eight
commits inside one range, and that step 4 puts a 700 MB transfer next to it.

**What this number is not:** not exFAT (that is step 4, and it needs a
partition, which an unattended run does not make), and not a fresh disk — an
ext4 allocator has fewer choices at 80 % full than at 20 %.

### The `fallocate` lead, and what 121 commits can say (#158, 2026-09-24)

§ 3.4 named a lead — `fallocate` a few ranges ahead of the write head — as the
fallback if a growing file's commit cost more than a laid-out one's, and the
measurement above looked like exactly that. So the lead was built
(`FALLOC_FL_KEEP_SIZE`, four ranges ahead, `--no-tape-prealloc` as the *before*
switch) and the rig grew to **four cells — paced × lead — run three times**, so
one boot measures both sides with nothing else moving.

| cadence | lead | p99 of the three runs |
|---|---|---|
| paced — a take's cadence | **lead** | 63.0 / 82.6 / **142.5** ms |
| paced | grows | 82.0 / 64.3 / 81.6 ms |
| flat out | **lead** | **14.9** / 101.2 / 101.9 ms |
| flat out | grows | 102.6 / **20.6** / 101.6 ms |

**The lead makes no difference, and the interesting part is how obvious it
looked that it did.** The first pair of cells read 63 ms with the lead against
82 without, and the first flat-out pair read 14.9 against 102.6 — a sevenfold
win, from the fix the design predicted, in the direction predicted. Both
reversed on the next two iterations: the no-lead cell hit 20.6 ms once and the
lead cell hit ~101 ms twice, and the worst single commit in the whole table
(170 ms) is a lead cell's. The p50 is identical across all twelve to ±0.5 ms,
and the count of cells inside 20 ms is 1 of 6 either way.

**What was actually wrong is the statistic.** `p99` over 121 commits is the
120th of 121 sorted — the **second-largest sample**, not a percentile. Twelve
runs of identical code put it anywhere from 15 to 142 ms. Step 2's conclusion
that "the cost is in growing the file, not in the rate" came from comparing two
such samples across two runs, and it does not survive a third run. § 6.2's
"~20 ms p99" cannot be tested by a two-minute take at all: a take makes one
commit a second, so a p99 worth the name needs hours.

So the rig reports what 121 commits *can* support: **counts over thresholds** —
`over_20ms` (the design's line) and `over_100ms` (a commit past a tenth of the
1.024 s of cover a range buys, which is what eight tracks at once would turn
into a stall). Those are stable at any N. The probe's exit status is a count,
and the summary prints the counts across the four cells rather than a verdict
from one run's p99.

**Read that way, eight more cells say the eMMC is far better than the ticket
claimed** — and they also say where the cost is, because the same runs time the
`fdatasync` on its own:

| cadence | lead | p50 | of which `fdatasync` | over 20 ms | over 100 ms | worst |
|---|---|---|---|---|---|---|
| **paced — a take** | **lead** | 12.10 / 12.23 ms | **11.40 / 11.50 ms** | **4 / 6** of 121 | **0 / 0** | 62 / 82 ms |
| paced | grows | 12.11 / 12.05 ms | 11.40 / 11.30 ms | 5 / 4 of 121 | 1 / 0 | 121 / 83 ms |
| flat out | lead | 11.73 / 11.89 ms | 11.02 / 11.15 ms | 5 / 9 of 121 | 2 / 2 | 103 / 103 ms |
| flat out | grows | 11.50 / 11.50 ms | 10.80 / 10.82 ms | 7 / 8 of 121 | 2 / 0 | 223 / 64 ms |

**The commit is the `fdatasync`.** 93–95 % of the median, and in **all eight
cells the count of commits over 20 ms equals the count of *syncs* over 20 ms** —
every commit that ran long ran long inside the sync, not in the allocation and
not in our writes, which together are C6's 0.54 ms. `POSIX_FADV_DONTNEED`, the
header rewrite and the range's own `pwrite` are all inside that remaining
0.7 ms.

**And the line is close to met, not missed fourfold.** At a take's cadence
96–97 % of commits are inside § 6.2's ~20 ms, the median is 12 ms, and **nothing
exceeds 100 ms** — the "four to five times the line" this ticket was opened on
was one run's second-largest sample. Pacing is also *better* than flat out on
both counts (4–6 against 5–9 over 20 ms; 0 against 2 over 100 ms), which is the
opposite of what step 2 inferred.

**So the only lever left is § 3.3's sync cadence**, and it is a spec question
rather than a code one: syncing every *N*th range instead of every range would
divide the sync count by *N* and multiply what a power cut can lose by the same,
and how much audio may be lost is Elfy's call. That is filed rather than
chosen.

**The lead is kept, on by default, and it is not kept on the strength of this
measurement.** It costs nothing measurable (the p50 does not move), it is the
standard shape for an appending writer, and **step 4 is where it can matter**:
whether the in-kernel exFAT driver implements `fallocate` at all is § 3.4's own
unverified question, and `state.tape.io.prealloc.refused` is now how step 4
reads the answer instead of guessing it. If exFAT shows nothing either, the
lead should go — `--no-tape-prealloc` is already the switch, and there is no
third measurement it is waiting for.

### Read-ahead under C6's transfer (C1 step 5, #160, 2026-09-24)

`tape-streaming-design.md` § 6 step 5 asks for one number: **`tape_late` over
five minutes of eight tracks playing with a take rolling and the 700 MB
transfer in flight, on eMMC — expected 0** — and the I/O thread's CPU, expected
~1–2 % of a core. `tape_late` is the count of rendered blocks in which some
track wanted a piece of tape audio that was not in memory yet and played
silence for those frames instead. #160 added a counter for it; **C1 step 7
landed in parallel the same day and built the spec's own**, so what the rig
reads is `state.tape.pool.late_total` and #160's duplicate was dropped before
merging — the numbers below are the same quantity either way, one count per
rendered block.

`arbutus_os/bench/c1-readahead-pi.sh` is the rig, `tools/c1-readahead-bench.sh`
puts the transfer beside it from the bench host. The host is **live** (not
`--offline`: offline there is no I/O thread at all, its loop body runs on the
render thread once a block, so every fetch would land before the next block and
the number would be a different mechanism's). Eight tracks each play their own
medium, one records throughout, the page cache is dropped from under the media
twice a second, and `arbutus-ui` and `arbutus-audio` keep running beside it.
Two horizons, two runs each, 300 s a cell, on ext4 on the root filesystem.

| horizon | `tape_late` blocks of ~112,500 | fetched | starved | I/O thread |
|---|---|---|---|---|
| **4 s (the default)** | **31,731 / 32,034** | 1970 / 1895 | 296 / 286 | **1.10 / 1.08 %** of a core |
| 0 s (the *before* switch) | 65,833 / 64,135 | 1492 / 1577 | 319 / 409 | 0.83 / 0.85 % of a core |

And the same two horizons with **no transfer at all**, which is what says whether
the transfer is the cause:

| horizon | `tape_late` blocks | fetched | starved | I/O thread | commit p50 |
|---|---|---|---|---|---|
| 4 s | **0** | 2026 | 198 | **1.24 %** of a core | **12.0 ms** |
| 0 s | 4,468 | 1918 | 207 | 1.01 % of a core | 12.1 ms |

**That is § 6 step 5's line, met exactly.** With the card free, read-ahead keeps
eight tracks and a take sounding for five minutes with **not one late block**,
the I/O thread costs **1.24 % of a core** inside the predicted 1–2 %, and the
take commits once a second for the whole run. The horizon-off cell is what says
the number is a real mechanism and not a counter that cannot fire: the same five
minutes, the same card, **4,468 late blocks** — which is what read-ahead is
worth on a card that is answering. The fetch counts are the same either way
(2026 against 1970), so the pool was equally exercised in both; what the
transfer changes is how long the card takes to answer.

**The I/O thread's CPU is the design's number exactly** — 1.1 % of a core, and
0.85 % with the horizon off, so the ~1–2 % estimate holds and read-ahead's own
share of it is about a quarter.

**`tape_late` is not 0. It is 28 % of every block rendered.** With the horizon
off it is 57 %, so read-ahead is halving it and is working: it is not the thing
that is broken. What is broken is underneath it, and the same runs measure it —
**the commit's median is 153–162 ms**, against the **12 ms** the same code, the
same card and the same rig measured with nothing else running (#158), and
**96–98 % of that is the `fdatasync`**. The worst single commit in each cell is
**4.8–5.4 s**. That is C6's own finding (#79: a 4 kB `fdatasync` waits 3.3–6.4 s
behind a 700 MB transfer on ext4) arriving in the tape: the I/O thread is not
busy, it is **blocked in the kernel on the card behind the transfer**, and while
it is blocked it serves no fetches, so the horizon empties and eight tracks read
ranges that are not there yet.

**And the take does not survive either.** In all four transfer cells the tape
file stops growing **17.7–19.9 s into a 300 s run** — `committed` freezes and
never moves again. That is step 2b's designed end: the pool becomes write queue,
`Tape::write` returns short, the host pushes `tape_full {reason:"stall"}` and the
transport keeps rolling. So § 6.1's ordering — *playback degrades before a take
ends* — is only half right here: playback degrades **and** the take ends, within
twenty seconds of each other. Without the transfer the same take commits once a
second for the whole 300 s (161 → 453 ranges).

**Two things say the number is the card and not the rig.** The two horizon-on
cells agree to **1 %** although one started on an idle box (load15 0.63) and the
other at load15 2.47 — so the CPU is not what is moving it. And 0 xruns in every
cell, with `dropped`, `errors` and `queue` all 0: nothing is late because a
thread missed a deadline, and nothing was silently thrown away.

**What this does not say.** It is not exFAT (step 4, which needs a partition an
unattended run does not make), and it is not the shipped instrument — nothing
passes `--tape-dir` until step 3b (#157). It is also not eight five-minute
media: those are 2.3 GB and this rootfs has 1.3 GB free, so each track's medium
is 20 s and its clip list cycles it across the run. That changes the *locality*
of the reads, not their rate — eight tracks consume ~7.8 ranges a second either
way and the working set (~154 ranges) is larger than the 117-slot pool either
way — but it is a deviation and the fetch counts should be read with it in mind.

**The follow-up is not in the tape.** Read-ahead cannot out-run a card that
takes 150 ms to accept a second of audio; the lever is either the sync cadence
(#159, Elfy's, since it spends the power-cut loss bound) or making the transfer
yield — § 6 step 4's own `ioprio_set` question, and the "`arbutus-net` gets a
ticket to sync in bounded chunks" that step 4 anticipated. Filed as **#161**.

### The transfer made to yield (C1, #161, 2026-09-24)

#160 left the tape late on **28 % of every block rendered** whenever a 700 MB
phone transfer was in flight, with the take itself ending ~19 s into a 300 s
run, and said the fault was not in read-ahead: the commit — the write of one
second of audio plus its `fdatasync` — had a median of **153–162 ms** against
the **12.0 ms** the same rig measures on a free card, and 96–98 % of that was
inside the `fdatasync`. #161 landed the fix in the cloud without a board
(`arbutus_os/net/writeback.py`): an upload's dirty pages bounded to two 4 MiB
windows by `sync_file_range` write-behind, and the request thread's I/O class
dropped. **This is the run that says whether either of those moves anything.**

The two levers are separate environment variables on purpose, so the run could
read them apart: `ARBUTUS_NET_WRITEBACK_BYTES` (`0` = window off) and
`ARBUTUS_NET_IOPRIO` (`idle`, `be7`, `off`). Eight cells, two runs of 300 s
each, horizon 4 s, eight tracks playing with a take rolling throughout,
ext4 on the root filesystem, the transfer PUTting and GETting 700 MB round
after round from the bench host:

| cell | window | I/O class | `tape_late` of ~112,500 | commit p50 | worst commit | the take |
|---|---|---|---|---|---|---|
| control | off | off | **35,451 / 35,622** | 155 / 154 ms | 5.13 / 5.26 s | ends 21.6 / 19.2 s |
| control (repeat, last) | off | off | 30,031 | 104 ms | 4.88 s | ends |
| priority only | off | `be7` | **40,173 / 35,672** | 155 / 152 ms | 4.55 / 4.99 s | ends 22.0 / 18.8 s |
| window only | 4 MiB | off | 845 / 203 | 87 / 85 ms | 0.75 / 1.60 s | **full 300 s** |
| window + `be7` | 4 MiB | `be7` | 2,305 / 2,829 | 81 / 79 ms | 3.17 / 1.64 s | ends 43.6 / 41.9 s |
| window + `be7` (repeat) | 4 MiB | `be7` | 500 / 0 | 85 / 84 ms | 1.50 / 1.09 s | **full 300 s** |
| **window + `idle`** | 4 MiB | `idle` | **0 / 0** | 90 / 90 ms | 0.76 / 0.63 s | **full 300 s** |
| **window + `idle`** (repeat) | 4 MiB | `idle` | **0 / 0** | 89 / 86 ms | 0.58 / 1.02 s | **full 300 s** |

0 xruns, 0 dropped ranges and 0 errors in every cell. The I/O thread is
1.13–1.71 % of a core throughout, inside the design's 1–2 % everywhere.

**The window is the fix; the priority drop is not.** With the window off,
asking the kernel for best-effort 7 changes **nothing measurable** — 40,173 and
35,672 late blocks against the control's 35,451 and 35,622, the same 152–155 ms
median, the same ~20 s take — although the kernel accepted the call every time
(the server reports it back per upload in an `X-Arbutus-Writeback` header, so
"the lever was refused" and "the lever did nothing" cannot be confused). That
is what `mq-deadline`, this card's scheduler, implies: it buckets by I/O
*class* and not by level, so best-effort 7 and an untouched thread are the same
bucket. Turning the window on, by itself, takes the lateness from ~31 % of
blocks to **0.2–0.8 %** and lets a 300 s take finish.

**`idle` is what reaches zero, and it starves nothing.** That reverses the
call #161 shipped with. The module chose best-effort 7 deliberately, on the
argument that an idle-class task is served only when nothing else wants the
card and the tape wants it once a second for as long as the transport rolls —
so `idle` might starve an upload for a whole take. **These cells are that
condition, and it does not happen**: an idle-class upload moved **44.7–44.8
MB/s, against best-effort 7's 44.3–44.5 and the untouched control's
47.4–48.9**, so the class the argument feared costs one percent of what it was
supposed to lose everything to. Meanwhile it is the only setting with
`tape_late` **0 in four runs of four**, where `be7` left a residue of 0–2,829
blocks that moved run to run and in one cell cost the take. The default is
`idle` now, for that reason and no other; `be7` and `off` stay reachable
through `ARBUTUS_NET_IOPRIO`.

**The variance is worth naming, because it is what two runs are for.** The
`be7` cells did not repeat: 2,305 / 2,829 with the take dying at ~42 s in one
cell, 500 / 0 with the take finishing in the other, same code, same card, an
hour apart. Anything read off a single window-on cell here would have been
wrong; the four `idle` runs are the only population in this table with no
spread at all.

**It bounds the damage rather than removing it.** The commit's median is
84–90 ms with the window on, not the 12.0 ms an undisturbed card gives
(#158) — still seven times the free-card figure, and 186–207 of ~453 commits
are over 100 ms. What changed is the tail: the worst single commit falls from
**4.6–5.3 s to 0.58–1.02 s**, and a range buys 1.024 s of cover, which is
exactly why the lateness disappears at the same time as the seconds-long
stalls do. The other lever is untouched and is § 3.3's sync cadence, **#159**,
which is Elfy's because it spends the power-cut loss bound.

**One thing the control found on its own.** The last control cell's first run
aborted in the probe's own seeding guard: `tracks [6] recorded almost nothing`,
294,912 bytes of a 960,128-byte seed. With no window, the transfer starves a
**20-second** recording badly enough to truncate it — the probe refuses to
measure on media it could not write, which is the guard doing its job and an
extra data point for the control at the same time.

**What this does not say.** It is not exFAT (step 4, which needs a partition an
unattended run does not make) and not the shipped instrument — nothing passes
`--tape-dir` until step 3b (#157). The starvation argument is not *wrong*,
only unobserved: it was tested against one other user of the card, on one
board, with one scheduler. A device with several would be the first place to
re-measure this default. And the bench transfer server is a stand-in for
§ 9.3's; it was changed for this run to put its `PUT` body down the same
`arbutus_os.net.writeback` the real one uses, because otherwise the rig would
have measured the pre-#161 transfer and reported it as the post-#161 one.

Rig: `tools/c1-readahead-bench.sh` with `C1RA_WB` / `C1RA_IOPRIO` /
`C1RA_AHEAD`, `arbutus_os/bench/c1-readahead-pi.sh`,
`arbutus_os/bench/transfer_server.py`. Results in
`~/bench-logs/c161-2026-09-25/` on the bench host.

### A TRACK redraw with the tape on the card (C1 step 6, #162, 2026-09-24)

`tape-streaming-design.md` § 6 step 6 asks for one number: **a TRACK redraw
after a jump across a project with eight long clips, in UI frames**, with the
tape on storage. § 3.6 sets the threshold — *if the redraw costs more than one
frame (16.7 ms at 60 Hz), the follow-up is a peaks sidecar.* `clip_peaks` is
the message TRACK sends to draw the shape of a clip; it reads the audio itself
on the control thread, and behind a `--tape-dir` most of that audio is on the
card, so drawing can mean hundreds of 384 KiB reads while the frame waits.

`arbutus_os/bench/p162-pi.sh` + `p162_peaks_probe.py` is the rig: its own
`arbutus-audio --offline --tape-dir`, eight media **seeded by recording**
(nothing can load one until step 3b, #157), the real `App` attached to it the
way `bench/c5_profile.py` attaches one, and TRACK redrawn under a frame timer.
Eight tracks, one 180 s clip each — 176 ranges and 69 MB a medium, 553 MB in
all — at TRACK's default 30 s zoom, on ext4 on the root filesystem. Two runs,
and a third and fourth pair before them that agree with these to 1 %.

**The answer is no, by two hundred and forty times.**

| cell | what it is | one bank | off the card |
|---|---|---|---|
| **A cold** | the first draw, media evicted from the page cache | **4,019–4,123 ms** | 255–260 MB |
| **D bank 0** | a *new geometry* (an edit), media evicted | 3,992 / 4,029 ms | 255 MB |
| **D bank 1** | the same work with the media in RAM | **711 / 714 ms** | 0 |
| **C** | the UI's cache cleared, the host's reply cached | 164–174 ms | 0 |
| **B jump** | park, jump far, redraw | **22–24 ms, and no `clip_peaks` at all** | 0 |

TRACK draws **four lanes at a time** (DDR-060), so a bank is four clips and the
eight-clip figure is twice the first row: **8.1 s of frozen screen**. The frame
that is not drawing peaks costs 18.7–21.3 ms in this rig — the App run flat out
beside the live instrument, the soak and the probe's own host — so that is the
baseline to subtract, not a finding.

**The premise of the measurement is wrong, and that is the first result.** The
TRACK page caches `clip_peaks` replies in `self._peaks` keyed by *geometry*
(`src_track, src, a0, a1, bins`), and zoomed out that geometry is the whole
clip — `a0 = 0`, `a1 = len`, `bins = ceil(len / 256)` — which does not depend on
where the view is. So **a jump asks for nothing**: cell B makes zero calls and
costs a frame. What costs four seconds is the **first** draw of a clip, and
after that every **new geometry**: a project load, entering TRACK, and every
SLICE, trim or move, which is cell D and is the case a player meets while
editing.

**Where the four seconds go.** Cell D's two banks are the same work with and
without the card, because the rig drops the page cache from under all eight
media before each cold bank and bank 0's are still evicted when D reaches them:

| part, per clip (180 s, 176 ranges, 33,750 bins) | ms | how it is known |
|---|---|---|
| the card: 64 MB of 384 KiB `pread`s | **~830** | D bank 0 minus D bank 1 (255 MB in 3.31 s ≈ 77 MB/s) |
| the scan: 8.64 M frames × 2 channels for min/max | **~110** | the same clip at **300** bins, warm: 110–116 ms |
| the reply: 67,500 numbers as JSON over the socket | **~60** | 33,750 bins 170–176 ms *minus* the 300-bin scan |
| **cold total** | **~1,005** | A cold ÷ 4 |

**Every one of the three is over a frame on its own**, which is what decides the
follow-up. A peaks sidecar (§ 3.6 — sixteen min/max bins per range kept in RAM
and written beside the audio) removes the **card** and the **scan** and leaves
the reply: at TRACK's own 33,750 bins that is cell C, **164–174 ms a bank, ten
frames**. So **the sidecar is necessary and not sufficient.** The other half is
the bins count: TRACK asks for one bin per 256 frames however wide the lane is,
and a lane is ~300 px, so it asks for **a hundred times the numbers it can
draw**. Asked at a sidecar's own resolution (2,816 bins) the reply is 5,632
numbers; asked at one bin per pixel it is 600, and the cost that is left is
milliseconds. Capping the bins **alone** buys nothing — 110 ms of scan and
830 ms of card are still there. Filed as **#163**.

**#163 is built (2026-09-25, cloud routine) and measured on this board (#163's
bench cell, 2026-09-24 — the next section).** Both halves landed together: the
I/O thread keeps 16 min/max pairs per range and writes them beside the medium as
`tape-N.peaks`, and TRACK asks for one bin per pixel of the clip instead of one
per 256 frames. The two are more coupled than the ticket knew — the sidecar
answers only at or below its own resolution, so **without the cap it would never
have been asked** for a clip drawn zoomed out. What said the card was **not**
asked is not the stopwatch but `state.tape.io.peaks.hit/miss` and
`/proc/<pid>/io` `read_bytes`, both reported per cell now.
`tape-streaming-design.md` § 6 step 6's *As built, 2026-09-25* has the six
decisions the design note left open.

**What this does not say.** It is ext4 on the eMMC, not exFAT and not a microSD
card (step 4 and C6b, both Elfy's) — a slower card makes the first row worse and
moves nothing else. The host is `--offline`, which is honest here because
`clip_peaks`'s fetch is a `pread` on the **control** thread inside
`TapeIo::fetch` either way and the transport is stopped, so read-ahead — four
seconds around a parked playhead — has no bearing on a read that walks minutes.
And nothing passes `--tape-dir` on a device yet (step 3b, #157), so this is what
3b will switch on, not what an instrument does today.

### The peaks sidecar measured, before and after on one boot (#163's bench cell, 2026-09-24)

The section above is the *before*. This is the same rig on the same board and the
same card with both halves of #163 in — the **peaks sidecar** (the tape I/O
thread keeps sixteen min/max pairs for every 1.024 s range it commits or fetches,
128 B a range, beside the medium as `tape-N.peaks`) and the **bins cap** (TRACK
asks for one bin per pixel of the clip instead of one per 256 frames of it).
`arbutus_os/bench/p162-pi.sh` runs two arms off one boot: the code as it ships,
and `--no-peaks`, which is the host's own `--no-tape-peaks`. Eight 180 s media,
176 ranges and 69 MB each, 553 MB in all, ext4 on the eMMC. **Two runs of each
arm; the four cold banks agree to 1.5 %.**

| one bank of four lanes | sidecar **on** | sidecar **off** (`--no-tape-peaks`) |
|---|---|---|
| **A** first draw, media evicted from the page cache | **55 / 35 / 55 / 41 ms**, **0 MB off the card** | **3,908 / 3,874 / 3,862 / 3,868 ms**, 255–260 MB |
| both banks, i.e. all eight clips | **90 / 97 ms** | **7,783 / 7,730 ms** |
| **D** a new geometry (an edit), media evicted | 58 / 35 / 57 / 35 ms, 0 MB | 3,784 / 3,789 ms, 255 MB |
| **D** the same with the media in RAM | (the same 35 ms — the card was never asked) | 516 / 515 ms, 0 MB |
| **C** the UI's cache cleared, the host's reply cached | 31 ms | 31 ms |
| **B** park, jump far, redraw | 22 ms, no calls | 22 ms, no calls |

**A bank of four 180 s clips goes from 3.9 s to 55 ms, and all eight from 7.8 s
to 90 ms — seventy to eighty-six times, with the card taken out of it
completely.** Every `clip_peaks` in the sidecar arm reports `hit` and every cell
reports **0 bytes** through `/proc/<pid>/io` `read_bytes`, which is what says the
device was not asked; wall time cannot (the rig lesson from #162).

**Per clip, the whole ladder, measured in the same runs** (the probe asks one
clip at three resolutions off the frame clock):

| what answers | ms a clip |
|---|---|
| the card + the scan, 300 bins (`--no-tape-peaks`, cold) | **936–1,041** |
| the scan alone, 300 bins (`--no-tape-peaks`, media in RAM) | **117–124** |
| the sidecar, 300 bins | **3.2** (0.9–1.1 ms measured without the UI's wrapper) |
| the host's own 256-entry reply cache | 2.1–2.5 |

**Both halves are needed and the run shows each one's share separately.** The cap
is why cell C is **31 ms** here against #162's 164–174 ms: the reply is 600
numbers instead of 67,500, and that is a cap-only saving — it is the same 31 ms
in *both* arms. The sidecar is the rest: at the capped 300 bins it answers in
3.2 ms what the scan answers in 117–124 ms warm and 936–1,041 ms cold. And the
fall-through still works on the board, which is the negative control: asked at
**33,750** bins — finer than a bin, so refused — the sidecar arm reads **63.7 MB
off the card** and takes 1,047–1,052 ms, reporting `miss`. Asked at 2,816 bins
over a span that does not line up with the range grid it is refused too
(153–157 ms). Nothing depends on the sidecar; it is a cache that says no.

**§ 3.6's line — "no frame over 16.7 ms" — is not met as written, and it cannot
be in this rig.** The report prints FAIL on 55 ms, and that is worth stating
plainly rather than rounding off: **this rig's idle TRACK frame, drawing no
waveform at all, is 17.9–21.2 ms** (the settled frames in every cell above,
matching #162's 18.7–21.3 ms) — the App run flat out and unpaced beside the live
instrument, the #151 soak and the probe's own host. A pass mark below the rig's
own floor cannot be passed. What the measurement does say, which is what the line
was for: of a 35 ms steady-state redraw, **~13 ms is the tape** (four
`clip_peaks` round trips at 3.2 ms) and **0 ms is the card**, against 3.9 s
before. Bank 0 costs ~20 ms more than bank 1 once per process — the first
waveform ever drawn — and after that a new geometry is 35 ms.

**What is left is the reply, not the tape.** A `clip_peaks` round trip from the
UI costs 2.1–2.5 ms even when the host answers out of its own reply cache, so the
~13 ms is mostly four messages carrying 600 JSON numbers each, not the sidecar's
1 ms of work. Whether TRACK should ask for a bank's four lanes in one message,
and whether an envelope should come back as something cheaper than JSON numbers,
is filed as **#169** — worth ~9 ms of a 35 ms frame, so it is a tidy-up and not a
wall.

Raw output on the bench CM4: `/var/tmp/p163/` (`p162-report.txt` and four JSON
documents; the rig removes its media and its 553 MB when it exits).

### The batched `clip_peaks` on the board (#171's bench cell, 2026-09-25)

#169 made TRACK ask for a whole bank's waveforms in **one** message instead of one per
clip, on the strength of #163's "a `clip_peaks` round trip costs 2.1–2.5 ms on the CM4
even when the host answers out of its own reply cache" — four of those being ~9 ms of a
35 ms redraw. On the board it is worth **1.3 ms**, and the gap is the finding.

Same rig, same board, same card as #163 (`arbutus_os/bench/p162-pi.sh`, eight 180 s media,
176 ranges and 69 MB each, 553 MB in all, ext4 on the eMMC, TRACK at its default 30 s zoom).
Two arms off one boot — the code as it ships and `--no-batch`, which stubs the page's
`_prefetch_peaks` so every lane asks for itself again, the control
`test_native_tape.py` uses for the same claim. Two runs of each.

| cell **A**, first draw, media evicted | batched | one message a clip | delta |
|---|---|---|---|
| bank 1, the steady-state redraw | **34.2 / 34.1 ms** | 35.6 / 35.5 ms | **−1.4** |
| bank 0, the first waveform ever drawn | 55.6 / 53.6 ms | 55.8 / 55.8 ms | −1.2 |
| the bank's `clip_peaks` wall time | **11.92 / 11.60 ms** in **1** message | 12.52 / 12.72 ms in **4** | **−0.6 / −1.1** |
| cell **C**, the host's reply cache warm | 30.0 / 30.8 ms | 30.8 / 31.3 ms | −0.2 / −0.8 |
| `peaks` `hit` / `miss`, bytes off the card | 4/0 a cell, **0 MB** | 4/0 a cell, **0 MB** | unchanged |

**Three round trips removed cost 0.6–1.1 ms, so a message is 0.2–0.4 ms — not 2.1–2.5.**
That number was never a message: it is a **reply**, and a reply's cost is the numbers in it.
The same runs price two reply sizes with everything else held still (the sidecar answering,
no card, no scan):

| one `clip_peaks`, answered out of the sidecar | numbers in the reply | ms |
|---|---|---|
| **1,812 bins** — what TRACK asks for, per clip, at the default zoom | 3,624 | **2.90–3.18** |
| 300 bins — the E-cell's smallest | 600 | **1.0–1.1** |

Two points, so ~**0.66 µs a number** plus a fixed ~0.65 ms of which at most 0.4 is the
message. Of the ~12 ms a bank's four replies cost, **~1.3 ms is the four messages and
~10.5 ms is the 14,496 numbers**, which is why the batch is worth what it is and no more.

**Where 1,812 bins comes from, and why it is not a bug.** #163 capped the ask at one bin per
pixel of the clip's **own** width, not of the visible lane: a 180 s clip at a 30 s zoom is
1,812 px wide and 300 px of it are on screen, so the reply is six times what the screen can
draw. That is bought deliberately. `TrackPage._peaks` is keyed on the clip's geometry, and
`_clamp_view` re-centres the view on the playhead **every frame**, so a reply keyed on the
*visible* range would be re-fetched on every frame the transport rolls. Asking for what the
screen shows would cost a fetch a frame; asking for the clip costs 6× once per geometry.

**So the count cannot fall, and the lever left is the bytes a number costs — which is
#172.** That inverts the rule #169 wrote down (*send fewer numbers, not shorter ones*): on
this board there are no fewer numbers to send without giving up a free scroll. Precision is
one way (two decimal places halve the bytes and move 8 of 309 shots by 2–4 px); a magnitude
array instead of `min` + `max` is another, and pixel-exact; and a binary reply — the
envelope as bytes rather than as JSON floats — is a third nobody has priced. All three are
#172's, and the ~10.5 ms they are aiming at is now measured rather than inferred.

**Everything the ticket said must not move did not.** The per-request wall is **3.13 / 3.18
ms** lone against **2.98 / 2.90** batched (the batch's own wall over its four requests) and
the 300-bin lone call is 1.0–1.1 ms in **both** arms — the same code, the same reply cache,
the same sidecar. Every cell reports `hit` and **0 bytes** off the card. The fall-through
control still works on the board: asked at 33,750 bins — finer than a bin, so refused — the
sidecar reads **63.7 MB** and takes 1,049–1,073 ms, reporting `miss`. The two runs agree to
0.3 % on cell A bank 1 and 1.5 % across the cold banks.

**§ 3.6's 16.7 ms line still reads FAIL and still cannot be met here** — the rig's idle
TRACK frame is 18.0–20.8 ms, #163's rule. On the tape's own share: **~11.6 ms of a 34 ms
redraw, and 0 ms of card.**

Raw output on the bench CM4: `~/bench-logs/p171-2026-09-25/` (`p162-report.txt` and four
JSON documents; the rig removes its 553 MB of media on exit).

### Compaction on the eMMC, and the PLAY that could not stop it (C1 step 8, #165, 2026-09-24)

`tape-streaming-design.md` § 6 step 8 asks for two things: **a 2 × ruler medium
compacting to one ruler, wall time on eMMC (expected ~7 s)**, and **that PLAY
during it cancels cleanly**. Compaction is how a track's *medium* — the file its
audio is stored in, written append-only — gives back the stretches nothing plays
any more: `tape_compact` copies the spans the UI still references into
`<medium>.compact` and renames it over.

`arbutus_os/bench/c1-compact-pi.sh` + `c1_compact_probe.py` is the rig,
`tools/c1-compact-bench.sh` puts C6's 700 MB transfer beside it. Ten cells, two
runs of each, ext4 on the root filesystem, load15 **0.99** at the start.

**The first half of the cell cannot be built, and that is the first result.**
The ruler is how long the tape *is*, and `Tape::init` sizes each track's range
table from it (`capacity_ = tape_seconds × sr`), so a frame past the ruler has
no table entry: `Tape::write` returns short and the take ends. A retake
therefore does **not** append a second copy — a second pass over the same eight
minutes writes nothing at all, the head does not move and the file does not
grow. **A medium is capped at one ruler.** So § 3.8's *"a medium at 2 × ruler"*
and its second trigger, *"the head passing k × ruler"*, describe a state this
host cannot reach; that is **#168**.

What can be measured instead is a **full medium compacting to half**, at
whatever byte size is wanted, because the ruler is a setting: a 960 s ruler
gives the 369 MB medium the note's own arithmetic is about, and the shipped
480 s ruler gives the largest compaction the instrument as configured can ever
be asked for.

| cell | medium | keep set | wall, two runs | copy | off the card |
|---|---|---|---|---|---|
| **369 MB, contiguous** | a 960 s ruler | the front half, 1 span | **6.92 / 7.97 s** | 53.3 / 46.3 MB/s | 184 MB read + 184 MB written |
| **369 MB, interleaved** | a 960 s ruler | half in 59 spans | **6.62 / 6.51 s** | 56.1 / 57.0 MB/s | 186 MB + 186 MB |
| 369 MB, interleaved, `--mode offline` | a 960 s ruler | half in 59 spans | 6.79 / 6.54 s | 54.7 / 56.8 MB/s | 186 MB + 186 MB |
| **184 MB, interleaved** | the shipped 480 s ruler | half in 30 spans | **3.42 / 3.32 s** | 54.4 / 56.1 MB/s | 93 MB + 93 MB |

**§ 6 step 8's ~7 s is met, and it was a good guess.** 184 MB read and 184 MB
written costs **6.5–8.0 s** on this eMMC. A third pair taken later the same
evening with the thread instrumentation on reads 7.14 s, so the spread is the
card's and not the rig's.

**The copy runs at ~55 MB/s of combined traffic and the two directions are
symmetric** — 26–28 MB/s read *and* 26–28 MB/s written at the same time, every
cell. Against C6 (#79: 57 MB/s write, 87 MB/s read, one direction at a time)
that says a compaction costs about what writing the whole medium once would:
the card's total throughput is the ceiling, not either direction's.

**Seeking costs nothing.** Half the medium kept in one span and the same half
kept in 59 blocks scattered across it come out the same (6.9/8.0 against
6.6/6.5, the contiguous pair if anything the slower). So § 3.8's range-aligned
destinations are free to be as fragmented as the keep set makes them, and a UI
that coalesces spans before sending them is optimising nothing.

**The offline cell is the control for the rig, and it agrees.** A medium can
only be read by the host that recorded it until step 3b lands (#157), and
seeding 369 MB by recording in real time is sixteen minutes a cell, so the rig
seeds with an `--offline` host (~20 s, 18.4 MB/s) and measures with a **live**
one opened on the same directory — legitimate because compaction never asks the
track's head, only what the medium's own header claims. Running the whole thing
in one offline host instead gives 6.79 / 6.54 s against the live 6.62 / 6.51 s.
The arrangement changed nothing.

**And PLAY does not cancel it. Four cells, four failures, and the reason is not
in the compaction at all.**

| what was asked | what happened |
|---|---|
| PLAY 2 s into a 6.6 s job | sent, and the copy ran to completion — full 59-row map, the medium replaced |
| `tape_compact`'s own reply (`started`) | came back at **7.09 s of a 7.14 s job**, not immediately |
| a **fresh connection's `hello`** during the copy | **4.72 / 5.26 s** |
| the PLAY request itself, once `hello` returned | 1–2 ms |
| CPU over the 7.14 s job | `arb-tape-io` **0.62 s**, every other thread 0.19 s |

The copy is on the I/O thread where it belongs, and that thread is not busy —
0.62 s of CPU in 7.14 s is a thread blocked in the kernel on the card. What is
wrong is the **lock**: `TapeIo::run()` takes `mu_` for a whole turn and
`drain()` runs a slice of the copy (`kCompactRanges` = 8 ranges ≈ 6 MB of I/O)
inside it, then `compact_busy_` makes the next wait return at once, so `mu_` is
re-acquired immediately. Every control-thread path into the tape needs that same
lock and loses the race for the whole job: `compact_freed()`, which the reply
reads; `compact_state()`, which `poll()` reads; and `compact_cancel()`, which is
what PLAY calls. **So for ~7 s the host answers nothing at all** — no reply, no
new connection, no PLAY, no REC.

Two consequences, and both are the point of this measurement. § 3.8's cancel —
*"the scratch file is deleted, the old medium has not been touched, the push
says `cancelled`"* — **cannot happen on a real card**; the existing
`test_compact_cancelled_live` passes because `FakeStore` finishes each slice in
microseconds and leaves the lock free. And the whole reason step 8 made this
**two messages on the wire** — so SAVE starts a compaction instead of waiting
seven seconds for one — is defeated by one field in the reply. That is
**#167**.

**And under C6's 700 MB transfer it is five times worse, which is #161 again.**
`tools/c1-compact-bench.sh` puts the transfer in flight from the bench host — a
700 MB file PUT and GET round after round, 87 rounds and 60.9 GB moved across
the run — and re-runs the cells at the shipped ruler:

| cell (184 MB medium → 93 MB) | idle | under the transfer |
|---|---|---|
| contiguous | — | **18.83 s**, 4.9 MB/s each way |
| interleaved | **3.42 / 3.32 s**, 27–28 MB/s each way | **17.83 s**, 5.2 MB/s |
| interleaved, offline | — | 17.90 s, 5.2 MB/s |
| seeding the medium | 10.1 s | 37–39 s |
| a fresh connection's `hello` during the job | — | **16.29 s** |

**A 5.2× on the wall time and the card down to a fifth of its throughput.** It is
the same wall #160 hit — the commit's median went 12 ms to 155 ms behind the same
transfer — arriving in a different call, and it makes #167's freeze worse in
proportion: the host answered nothing for **sixteen seconds**. The transfer has to
yield (#161) whatever else is done about the lock. *(Both were then done, and this
table is superseded: #161's window landed 2026-09-24 and #167's lock 2026-09-25,
and the re-take is § *And the same cells under C6's 700 MB transfer* below.)*

#### The lock, fixed — and what is still owed the bench (#167, 2026-09-25)

The copy no longer holds `mu_` across the card: `compact_step` drops it around
every `pread`/`pwrite` pair and takes it back to advance the job, re-reading the
job's own state each time because a cancel may have landed in between. The
reply's `dead_frames` comes back from `compact_start` itself rather than from a
second `compact_freed()` call, and `compact_cancel` puts the job's state down
**before** it waits, so a cancel lands *inside* a slice and costs one range of
card — ~27 ms at the 55 MB/s above — instead of the eight a turn copies, let
alone the job. Dropping the lock means a handle can be in use by a thread that
does not hold `mu_`, so everything that closes one (the cancel,
`compact_clear`, `compact_finish`, shutdown) waits an in-flight slice out first.

It was reproduced before it was fixed, which the table above is the reason for:
`FakeStore` is now thread-safe and takes a per-call delay, so a slice can be
made to cost what a card costs. A 24-range job at 2 ms a call, three runs
agreeing:

| | copy's writes after a cancel 3 ranges in | worst `compact_state()` |
|---|---|---|
| **the lock held across the card** (`set_compact_holds_lock`, the *before* switch) | **26 of 24** — it ran to the end | **117 ms**, the whole job |
| the lock dropped | **4 of 24** — it stopped where PLAY arrived | **0 µs** |

That is the board's four cells in 240 ms, and the *before* switch is what makes
it evidence: `test_compact_cancelled_live` was green throughout the fault, which
is the #56 rule collecting on a real bug.

#### The four cells on the card, after the fix (#170, bench, 2026-09-25)

The acceptance line above, taken on the bench CM4 with the same
`c1-compact-pi.sh` and the same 369 MB medium, two runs of five cells. **All
four pass, and the wall time did not move**, which together are the whole
claim: the lock came off the card, not off the work.

| the cell | before (#165) | after (#170), run 1 / run 2 |
|---|---|---|
| a fresh connection's `hello` during the copy | **4.72 / 5.26 s** | **0.00 / 0.00 s** — under a frame, twice |
| `tape_compact`'s own `started` reply | **7.09 s of a 7.14 s job** | **2 / 3 ms**, and 2–3 ms in all eight live cells |
| PLAY two seconds in | the copy ran to completion and replaced the medium | `tape_compact {cancelled}`, **scratch gone**, **medium byte for byte** — twice |
| PLAY's own request, once `hello` returned | 1–2 ms (it was the *lock* that took the seconds) | **104 / 25 ms** |
| the job with nobody interrupting | 6.5–8.0 s | **7.88 / 7.06** (front), **6.83 / 7.54** (interleaved), **3.33 / 3.57** (the shipped 184 MB) |
| the card during the copy | 26–28 MB/s each way | **23–31 MB/s each way**, still symmetric |

**The cancel costs a tenth of a second, not twenty-seven milliseconds.** #167
predicted one range of card — ~27 ms at 55 MB/s — and PLAY's own reply
measured **104 ms** and **25 ms** in the two runs, with the job ending 0.18 s
and 0.10 s after the cancel was issued. The 25 ms is the predicted figure; the
104 ms is not, and the reason is that a cancel waits out whatever slice is in
flight (`wait_io`), which at the moment PLAY lands may be a `pwrite` only just
begun. So the honest bound is **a slice, not a fraction of one** — two ranges
of card in the worst case, which is what the code says and two orders below the
4.7–5.3 s it replaces. The copy had moved 51.5 MB and 64.9 MB when PLAY
arrived, so the cancel really did land mid-job and not after it.

**The offline cell still answers as late as the push, and that is correct.**
`--mode offline` runs the copy inside the request by design (no I/O thread
exists), so its reply comes at 6.56 s of 6.61 and 6.24 s of 6.29 — the rig's
control for its two-host arrangement, agreeing with the live cells on wall time
(6.61/6.29 against 6.83/7.54) and saying nothing about the lock.

One caveat on the run: `load15` was **0.96** when the rig's own settle loop let
it start, against #165's 0.99 — under the rig's threshold but not by much, the
residue of a build and two killed runs (see the session log). Run 1 and run 2
agree to within the card's own spread, which is what #129's two-run standard is
for.

##### And the same cells under C6's 700 MB transfer (#170, the same run)

`tools/c1-compact-bench.sh` from the bench host, 100 rounds and **70.0 GB**
moved across the run (#165's row was 87 rounds / 60.9 GB), the shipped 184 MB
ruler, two runs of five. **#161's bounded write-back window landed between the
two measurements and it is most of what changed.**

| cell (184 MB medium → 93 MB) | idle (#170) | under the transfer, #165 | under the transfer, now |
|---|---|---|---|
| front | 7.88 / 7.06 s at 369 MB | **18.83 s** | **8.09 / 4.69 s** |
| interleaved | 3.33 / 3.57 s | **17.83 s** | **9.06 / 7.79 s** |
| interleaved, offline | — | 17.90 s | 8.41 / 8.97 s |
| the card each way | 26–28 MB/s | **4.9–5.3 MB/s** | **10.3–19.7 MB/s** |
| seeding the medium | 9.8 / 9.8 s | 37–39 s | 42–56 s |
| a fresh connection's `hello` during the job | 0.00 s | **16.29 s** | **0.00 s**, both cells |
| `tape_compact`'s `started` reply | 2–3 ms | (the job) | **13 / 24 ms** |
| PLAY two seconds in | cancels | never got the lock | **cancels** — scratch gone, medium byte for byte, 0.11 / 0.30 s to stop |

**The sixteen-second `hello` is gone, and so is most of the 5.4×.** The
compaction is now **1.9–2.7× slower** under the transfer than idle (6.4–9.1 s
against 3.3–3.6) where it was 5.4×, and the card gives it 10–20 MB/s each way
rather than 5. Two separate fixes are visible in one table and they are worth
keeping apart: **#167's lock** is why the reply and the `hello` are
milliseconds — that would be true on a free card or a busy one — and **#161's
window** is why the *wall time* fell, because the transfer no longer leaves the
block device with unbounded dirty pages for the copy to queue behind.

**It costs the transfer almost nothing.** Across the run the upload averaged
**41.7 MB/s** and the download **78.9 MB/s**, against the 44.7–44.8 MB/s #161
measured for an idle-class upload with a take rolling. So a compaction and a
phone transfer now share the card rather than one of them winning.

**What is still not equal to a free card**: 6.4 s against 3.3 is a real cost,
and the seed is slower than #165's (42–56 s against 37–39) on a run that moved
15 % more data. The remaining lever is #159, the commit's sync cadence, which is
Elfy's.

**What this does not say.** It is ext4 on the eMMC, not exFAT and not a microSD
card (step 4 and C6b, both Elfy's). Nothing passes `--tape-dir` on a device yet
(step 3b, #157), so this is what 3b switches on, not what an instrument does
today. The UI's half of step 8 — the remap, the SAVE trigger and T9 — was #164
and unbuilt when this ran, so the keep sets here are the rig's rather than a real
project's (**#164 landed 2026-09-25**, after this cell ran). And the take that made each medium is silence, which costs the card
the same bytes but lets no one hear whether the compacted medium is the right
audio; the host's own tests are what say that.

### What the tape costs in RAM, in both modes (C1 step 9, #74, 2026-09-25)

The one number C2 could not measure and had to compute. `arbutus-audio --offline`,
one three-range take on each of two tracks, `VmRSS` read out of `/proc` at idle and
again after the takes, and `VmHWM` as the peak. **A cloud container on x86-64, not a
bench number** — what it settles is what the process asks the allocator for, which
is the same arithmetic on either machine; what a CM4 shows with the UI, the radio
and a transfer beside it is C2's job and, for the tape as an instrument really runs
it, step 3b's.

| host | RSS idle | after two takes | `pool.ranges` / resident |
|---|---|---|---|
| unbounded — no `--tape-dir` (the emulator and the device today) | **5.1 MiB** | **361.1 MiB** | 0 / 938 |
| bounded — `--tape-dir`, the default 120 track-seconds | **49.5 MiB** | **50.2 MiB** | 117 / 6 |
| bounded — `--tape-dir --tape-pool-seconds 30` | **16.2 MiB** | **17.0 MiB** | 29 / 6 |

Three things fall out of it, and the third is the point of C1.

**The arithmetic is exact, not approximate.** A range is 384 KiB, so 117 slots are
43.9 MiB and the bounded host starts 44.4 MiB above the unbounded one's idle — the
pool is allocated at `init` and there is nothing else in it. 29 slots are 10.9 MiB
and measure 11.1. The ruler is 469 ranges (480 s is 468.75 of them, rounded up), so
an unbounded track is 176 MiB: two of them are 352 of the 356 MiB the table grows
by (the rest is the take's own buffers), and `resident 938` is 2 × 469 — the same
count arrived at from the other end.

**The bounded pool does not grow while recording.** Two takes move it 0.7 MiB, which
is the I/O thread's own buffers and the medium's headers, not tape: the take spends
slots it gives back. The unbounded host grows 178 MiB a track on first record, which
is #72's zero-fill measured as memory rather than as latency.

**Eight tracks are the number that mattered.** 8 × 176 MiB = 1.41 GiB, which is P1's
1.46 GB (#13) and C2's 1,429 MiB — the one fixed cost that ruled out every module
under 4 GB (C9, #82). Bounded, eight tracks cost the same 49.5 MiB as one, because
the pool is the budget and the tracks share it. That is the whole of why C1 exists,
and it is not on a device until step 3b (#157).

## Ranges are the author's job (DDR-044)

Every parameter above is bounded so that anywhere on the knob sounds like
something worth hearing. Where a range differs from the Python original, the
box's README says what it was, what it is, and why. The largest such change is
`arbutus/fm`, whose INDEX was never the modulation index at all — see
`engines/arbutus/fm/README.md`.

The stage-I1 boxes have no Python original at all, so every range in them is a
choice made once and written down: `va`'s CUTOFF stops at 80 Hz because below it
a four-pole ladder is silence, its RES stops short of self-oscillation because
the last detent must not be a different instrument, `string`'s DECAY is seconds
to −60 dB *per note* so the knob means the same thing in every octave, and
`dbox`'s DECAY carries a per-mode scale so one knob is "long" on a kick and on a
hat. Each box's README is the argument.

The stage-I2 effects are the same, and each of their READMEs carries the
measurement that settles the bound: `delay`'s FDBK dies away everywhere below
its top, and the top is a **hold** whose repeats keep their level for ever and
never grow (#779); `chorus`'s voices are 0.37 of a cycle apart because
evenly spaced ones collapse to mono at SPREAD 180 (measured: L/R correlation
+1.000 before the fix); `crusher`'s BITS starts at 2 because 1 bit is a mute for
anything under half scale; `drive`'s oversampler is 31 taps because 15 dropped
20 kHz by 3.8 dB; `eq3`'s bands are ±18 dB and a band at 0 dB is skipped rather
than run as a unity biquad, so flat is bit-exact. **Every one of the five has a
bit-exact bypass** — MIX 0 on four of them, flat on `eq3` — and says so with a
measured 0.000e+00.

## Build and test

```bash
cmake -S audio -B audio/build -G Ninja
cmake --build audio/build
ctest --test-dir audio/build --output-on-failure      # 160 tests
```

Boxes land in `audio/build/boxes/<author>/<name>/`. Requires CMake, Ninja, a
C++20 compiler and python3 for the host tests; on this Mac, `brew install cmake
ninja` and Apple clang. **Every test runs offline — the suite needs no audio
device.**

## Running it live

```bash
audio/build/host/arbutus-audio --library audio/build/boxes
```

It opens the default output device and a control socket at
`$TMPDIR/arbutus-audio.sock`. Talk to it from another terminal:

```bash
CTL=audio/tools/arbutus-ctl/arbutus-ctl.py
python3 $CTL load_patch patch='{"format":1,"kind":"patch","name":"LIVE",
  "engine":{"id":"arbutus/example","params":{}},
  "env":{"a":0.01,"d":0.2,"s":0.8,"r":0.4,"mode":"poly"},
  "fx":{"format":1,"kind":"rack","name":"ONE",
        "tabs":[{"instance":"lp","engine":"arbutus/biquad","params":{}}]}}'
python3 $CTL note on=true note=60 vel=0.9
python3 $CTL --follow meters on=true hz=10
```

`--offline` swaps the device for `render {frames}` and `bake`, which is how the
tests drive it. `arbutus_ctl.py` next to the script is the importable library;
the emulator's shim imports it.

## Running the emulator against it

```bash
emulator/run.sh
```

That is the whole thing — `run.sh` builds the host on first run if it is
missing. The emulator spawns `audio/build/host/arbutus-audio` itself, points it
at `audio/build/boxes` and `audio/library`, does the `hello` handshake and
drives it over the §8.2 protocol.

**This is the only audio path** as of stage N1 (2026-09-17): the Python engines
are deleted and a missing binary is fatal, because there is nothing to fall
back to. `emulator/` is the UI — it draws, routes keys and owns the tape's clip
algebra; every sample of sound is made here.

`emulator/notes/audio-host.md` § Native audio becomes the architecture is the reference:
what it replaced, what stays Python, the rack gestures, and every `[TBD-Elfy]`
drawing decision with the line that makes it.

The §11 parity gate is retired — it compared the two audio paths and there is
only one. What replaced it as the cross-language evidence is `ctest -R
voice_goldens`: the Python voice goldens, now at `audio/tests/golden/python/`,
replayed against the C++ DSP cores. The last measurement before the Python went
away was **−138.5 dBFS peak** over 21504 frames of `arbutus/osc` →
`arbutus/biquad` → `arbutus/reverb`, against a −60 dBFS tolerance.

## The library

`audio/library/` is the content half: format-1 patches, the four drum kits as
§7.3 pad maps, the sequencer's `.seq` patterns and the stock racks. Kit pads are
`{file, params}` since drum v2 (§15.3); the old `"0": "kick.wav"` shape still
loads. It was **regenerated, not migrated** (§11) from the emulator's old
positional files, which are gone. See `library/README.md`.

## Start here

- Writing an engine: **`sdk/template/README.md`**.
- Running one: **`tools/arbutus-run/README.md`**.
- Talking to the audio process: **`host/PROTOCOL.md`**.
- Working in this folder as an agent: **`CLAUDE.md`**.

## The proof that matters

`ctest -R biquad_golden` replays `audio/tests/golden/python/biquad_input.npy`
through the C++ box and compares it to `biquad.npy`, the capture from the Python
`BiquadLowpass`. The same numbers, from two languages, within −100 dBFS. That is
what made the rest of the Python golden set usable as a conformance suite while
the engines were rewritten — and it is why the goldens moved **here** when the
Python was deleted (stage N1) instead of going with it.

`ctest -R host_pipeline` is the second one: the template synth into the biquad
**through the host's rack**, against the same two boxes run back to back in
`arbutus-run`. If the host coloured the sound at all, that test would say so.

`ctest -R voice_goldens` is the third: the eight Python goldens that are *voice
DSP* — `adsr`, `adsr_zero`, `osc_sine|square|saw|triangle`, `fm_voice`,
`noise_voice` — replayed through the headers the stage-3 synth boxes are built
from. Six of the eight are bit-identical or better than −144 dBFS. The boxes'
own ranges deliberately cannot reach the values those captures used (DDR-044),
which is exactly why the DSP cores are headers and the test drives them
directly.
