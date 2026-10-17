// audio_host.hpp — arbutus-audio's one object: the pipeline (§5), the rack
// (§6), the tape, the mixer, and the protocol's state document (§8.2).
//
// Two threads, one direction each:
//
//   control -> audio   a lock-free SPSC ring of small PODs. Anything with a
//                      constructor is built on the control thread and handed
//                      over as a pointer.
//   audio -> control   a second ring of pointers to retire. The audio thread
//                      never calls delete, never allocates, never locks (§3.7).
//
// In --offline mode both "threads" are the same thread and the rings still
// carry everything, so the offline path exercises the real one.
#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include <audiofile.hpp>
#include <library.hpp>
#include <poly.hpp>                             // kUnisonSpreadCents, the DETUNE default

#include "mixer.hpp"
#include "spsc.hpp"
#include "rack.hpp"
#include "tape.hpp"
#include "tape_io.hpp"

namespace arb::host {

inline constexpr int kProtoVersion = 1;
// The oldest `abi` a *client* may name at hello. A client never loads a box; the
// ABI it sees is the descriptor JSON in `state`, and ABI 2's is ABI 1's plus
// optional fields, so an ABI-1 UI still reads it correctly. Boxes are held to
// ARB_ABI exactly (§7.4); this only keeps the handshake backward compatible.
inline constexpr int kMinClientAbi = 2;
inline constexpr const char* kFirmware = "arbutus-audio 0.2.0";
inline constexpr int kNumInstruments = 2;      // SYNTH, DRUM (§5)
// The master bus is a third rack (§15.5, DDR-062): the same §6 mechanism, on
// the mix instead of on an instrument, addressed as `instrument: "master"`. It
// holds an FX rack and nothing else -- no ENGINE, no SEQ, no MOD rack, no
// patch -- so every rack message works on it unchanged and every instrument
// message refuses it.
inline constexpr int kMasterBus = kNumInstruments;
inline constexpr int kNumBuses = kNumInstruments + 1;
inline constexpr uint32_t kFadeFrames = 256;   // engine hot-swap crossfade, 5.3 ms at 48 kHz
// Ck::Env slots: a d s r glide detune bend_range; i1 == kEnvSlots carries the mode.
inline constexpr int kEnvSlots = 7;
// A kit's pad 0 is the lowest musical key, F3 (emulator app/keys.py
// NOTE_BASE_MIDI, arbutus/drum kBaseMidi): set_note_param's `pad` and the kit
// document's pad keys are this many notes below the note the engine sees.
inline constexpr int kKitBaseNote = 53;
inline constexpr int kKitPads = 24;
// DRUM › ENV (DDR-053) is ATK · DEC. The drum instrument's defaults are v1's
// one-shot: an instant attack and the old DECAY default, -60 dB at 4 s, so a
// kit sounds as it did before the ENV page applied. [default chosen here:
// audio/CLAUDE.md]
inline constexpr float kDrumEnvA = 0.0f, kDrumEnvD = 4.0f;

enum class Ck : uint8_t {
    SetChain, SetCtl, SetClips, SetParam, SetMacro, Note, AllNotesOff, Softkey,
    Transport, Env, Tempo, Active, Record, Arm, Mixer, ResetEngines,
    SetModCtl, HostParam, Motion, Bend, SetNoteParam,
    Metronome, Loop, TapeSpeed, TapeFilter, Scrub, Monitor, Eq, InputSource
};
struct Cmd {
    Ck      k = Ck::AllNotesOff;
    int32_t i0 = 0, i1 = 0, i2 = 0, i3 = 0;
    float   f0 = 0, f1 = 0;
    int64_t pos = 0;
    void*   p = nullptr;
    float   m[6]{};                            // Motion: ax ay az gx gy gz
};

// audio -> control: a probed `note` (#73) as the audio thread applied it. `t1_ns`
// is the start of the block that carries the note, on the same clock as
// `t_ctl_ns` (steady_clock = CLOCK_MONOTONIC on Linux, CLOCK_MONOTONIC_RAW on
// macOS, #251), so a client stamping that clock can subtract.
struct Probe { int32_t id = 0; int64_t t_ctl_ns = 0, t1_ns = 0; int64_t block = 0; };

enum class Rk : uint8_t { Chain, Ctl, Clips, ModCtl };
struct Retire { Rk k = Rk::Chain; void* p = nullptr; };

// ---- control-side mirror of one loaded slot --------------------------------
struct SlotState {
    bool loaded = false;
    std::string id, min_version, version;
    json manifest;                             // engine.json (§3.8), for the UI
    std::vector<std::string> pids;             // param ids, descriptor order
    std::map<std::string, float> params;       // authored, normalised (§3.2)
    std::vector<AssetRef> assets;              // key -> absolute path (§3.2 load_asset)
    // The loaded instance's format_param, for `display` in the state document.
    // Not owning: the instance lives in the chain this state was applied with,
    // and only the control thread deletes chains -- after inst_ has moved on to
    // a newer one. format_param is the one entry the ABI lets the control thread
    // call while the audio thread renders (arbutus_engine.h).
    const arb_vtable* fmt_vt = nullptr;
    void*             fmt_h = nullptr;

    json to_json() const;
};

struct InstState {
    SlotState engine, seq;
    RackDoc rack;
    std::vector<SlotState> tabs;               // index-aligned with rack.tabs
    ModDoc mod;                                // modulation-spec §11
    std::vector<SlotState> mod_tabs;           // index-aligned with mod.tabs
    // Host virtual params' knobs (modulation-spec §5), normalised: level is the
    // bus gain, pan 0.5 is centre. Not in a patch -- §7.2 has no field for them.
    float host_level = 1.0f, host_pan = 0.5f, host_pitch = 0.5f;
    std::array<float, 8> macro{};
    float env_a = 0.012f, env_d = 0.2f, env_s = 0.8f, env_r = 0.34f;
    // ENV SHIFT row (DDR-052). Defaults reproduce the sound from before it
    // existed: no glide, unison at its old fixed 12-cent spread. BEND 2 st is
    // the MIDI convention and silent until a bend arrives. [default chosen
    // here: audio/CLAUDE.md]
    float env_glide = 0.0f, env_detune = arb::kUnisonSpreadCents, env_bend_range = 2.0f;
    int   env_mode = 0;
    json  lfo = json::object();                // carried, not applied in stage 2
    // §7.3 document, DRUM only, as the UI would save it: every pad in the v2
    // shape {file, params}, params normalised and complete for the loaded
    // engine's per_note list (set_note_param writes here). `dir` is kept.
    json  kit;
    std::string kit_path;                      // the resolved copy the "kit" asset points at
    json  seq_pattern = json::object();
    int   octave = 0;
    std::string name;
};

// ---- audio-side instrument -------------------------------------------------
struct Voice {
    Chain*   cur = nullptr;
    Chain*   old = nullptr;                    // alive only during a crossfade
    RackCtl* ctl = nullptr;
    ModCtl*  mctl = nullptr;
    uint32_t fade = 0;
    std::array<float, 8> macro{};
    ModRun   mod;                              // keys source, host knobs, live values
    float    bus_gain[2] = {1.0f, 1.0f};       // host.level x host.pan, last block's end
    // Bend, -1..1: where the `bend` message asked for, and where the ramp is.
    float    bend_target = 0.0f, bend_pos = 0.0f;
};

struct Options {
    uint32_t sr = 48000;
    uint32_t block = 128;                      // §15.8: the device's block, and the emulator's
    uint32_t max_block = 128;
    double   tape_seconds = 480.0;             // 8 minutes (software-stack-v1 §8.2)
    // Where the media live (`<dir>/tape-1.wav` … `tape-8.wav`), C1 step 2a.
    // Empty = the tape is RAM only and the I/O thread never runs, which is
    // what every caller does until step 3 makes the medium the project's own
    // file. `--tape-dir`.
    std::string tape_dir;
    // How much tape audio is resident, in **track-seconds** (`OS-11` → DDR-072:
    // 120, ~46 MB, Elfy 2026-09-19 — down from the ticket's 240 "for much
    // better headroom for downgrading the compute unit"). It only bites with a
    // `--tape-dir`: with no medium on storage there is nowhere to fetch an
    // evicted range back from, so the pool stays unbounded and the tape is the
    // RAM tape it always was. `--tape-pool-seconds`.
    double   tape_pool_seconds = 120.0;
    // § 3.4's lead, ahead of the tape's write head (#158): the filesystem is
    // asked for blocks a few ranges past the range being committed, so the
    // commit writes into space that already exists. On, because a *growing*
    // medium's commit measured 65-92 ms at p99 on the bench eMMC against
    // § 6.2's ~20 ms; `--no-tape-prealloc` is the *before* switch, which is
    // what lets one boot of `arbutus_os/bench/c1-pi.sh` measure both sides.
    bool     tape_prealloc = true;
    // The peaks sidecar (#163, design § 3.6): the I/O thread keeps 16 min/max
    // pairs per range, so drawing a clip zoomed out reads neither the card nor
    // the samples. On, because #162 measured a TRACK redraw at 4.0 s a bank of
    // four lanes with the tape on the card; `--no-tape-peaks` is the *before*
    // switch, so one boot of that rig measures both sides.
    bool     tape_peaks = true;
    // **Test switches**, and the store one is design § 4's own
    // (`--tape-store posix|fake`). `fake` puts `FakeStore` behind the tape —
    // the media live in RAM inside this process and nothing reaches the card —
    // so a full card, a stalling card and a card that refuses a write are all
    // reachable from a host test through the protocol, which is where § 3.7's
    // three `TAPE FULL` reasons live. `--tape-free-bytes` is how much room
    // that card has; it is also honoured for the **refusal** check on a real
    // card, so the REC-under-92 MB rule can be tested without filling a disk.
    // Neither belongs on an instrument, and `--help` says so.
    bool     tape_store_fake = false;
    int64_t  tape_free_bytes = -1;             // < 0 = ask the filesystem
    // § 3.6's read-ahead horizon, in **seconds of wall clock** (C1 step 5):
    // the I/O thread keeps resident every range the clip lists read inside
    // `[pos − behind, pos + ahead]` of the playhead, in the direction of
    // travel, so the window in frames scales with SPEED. `ahead` 0 turns
    // read-ahead off and leaves only the audio thread's demand asks, which is
    // the *before* switch for the whole mechanism. `--tape-readahead-seconds`,
    // `--tape-behind-seconds`. Only with a `--tape-dir`, like the pool.
    double   tape_readahead_seconds = 4.0;
    double   tape_behind_seconds = 1.0;
    uint32_t seed = 0;
    bool     offline = false;      // --offline drops the device (tests, bake)
    bool     rt_guard = false;     // --rt-guard: count heap blocks across render_block (offline)
    std::vector<std::string> library_roots;
    std::vector<std::string> library_fallbacks;   // --library-fallback (D125)
};

class AudioHost {
public:
    ~AudioHost();

    bool init(const Options& o, std::string& err);
    const Options& options() const { return opt_; }
    // After the device has stopped (no audio thread any more): end a take
    // still open the way STOP would, so `~TapeIo` writes its tail and an exact
    // header rather than leaving the one-range lag on the card (#612).
    void end_open_take();

    // ---- control thread ----------------------------------------------------
    json handle(const json& msg);               // one message in, one reply out
    json state_doc() const;
    // #853: the host's half of the live stream's *situation* document
    // (PROTOCOL.md § The live stream, `live-schema.json`): transport, tempo
    // and click, the eight tracks, the four slots, the input, the flags. A
    // few hundred bytes, rounded so a still instrument reads the same twice.
    json live_doc() const;
    json tape_io_doc() const;                   // state.tape.io, null when off
    // Control thread: `run_at` with a fetch behind it, for the readers that may
    // block (`bake`, `clip_to_asset`, `clip_peaks`, `tape_save`, `tape_edit`).
    // Pins the range it hands back, so the evictor cannot take it while the
    // caller copies out of it; the next call moves the pin and `tape_unpin()`
    // releases it.
    const float* tape_run(int t, int64_t f, int64_t& run) const;
    void tape_unpin() const { tape_.pin(-1, 0); }
    // Bounded, a take or a `tape_edit` appends at a head that is usually inside
    // an already-written range, so that range has to come back off the card
    // first or the commit would write our zeroes over the audio there.
    void tape_prepare_head(int t, int64_t at) const;
    bool take_state_dirty();                    // true once after any edit
    bool state_dirty() const { return state_dirty_; }   // the same, not taken
    std::vector<json> drain_events();           // xrun / meters pushes
    void poll();                                // retire queue + meter cadence
    bool should_quit() const { return quit_; }
    // A bounce job is rendering (#920): the loop turns without sleeping.
    bool bouncing() const { return bounce_ != nullptr; }

    // The IMU's six floats — accel x/y/z in g, gyro x/y/z in deg/s
    // (modulation-spec §13, device-os-spec § 6.5). Called from the control
    // thread at ~200 Hz by `main.cpp`'s `--imu` loop; the same `Ck::Motion`
    // the offline `motion` message uses, so the audio thread cannot tell a
    // sensor from a test.
    void set_motion(const float m[6]);

    // Is a motion sensor fitted **and answering** (device-os-spec.md § 6.5)?
    // `main.cpp` sets it once when `--imu` opens the part and again whenever
    // the driver's health changes, because § 6.5's failure mode is the same
    // sentence for "no WHO_AM_I" and for "a bus error mid-run": the box says
    // `NO SENSOR` and SYSTEM hides `KEY VEL` in both. So this is not a
    // constant the handshake could carry alone -- a change marks the state
    // document dirty and every client is told (#116).
    void set_motion_sensor(bool present);
    bool motion_sensor() const { return motion_sensor_; }

    // Offline render: the same path the device callback takes.
    void render_offline(uint32_t frames, std::vector<float>* out_l, std::vector<float>* out_r);

    // ---- the key thread (#638, device-os-spec § 1.3.4) ----------------------
    // Its one entry into the host: a note on `keys_cmds_`, applied at frame 0
    // of the next block like every `note`. `probe` >= 0 is the bench stamp and
    // `t_ns` the parse (steady_clock), as `note {probe}` carries them (#73).
    // False = the ring is full (the audio thread has stopped). Never blocks,
    // never allocates.
    bool push_key_note(int note, bool on, float vel, int32_t probe, int64_t t_ns);

    // ---- audio thread ------------------------------------------------------
    void render_block(const float* in_l, const float* in_r, float* out_l, float* out_r,
                      uint32_t n);
    // The device under- or overran (ALSA EPIPE): a block the player lost that
    // render_block's own timing cannot see, because it was late to *start*.
    void count_device_xrun() {
        xrun_count_.fetch_add(1, std::memory_order_relaxed);
    }

private:
    // control
    json reply_ok(const json& msg, json extra = json::object()) const;
    json reply_err(const json& msg, const std::string& code, const std::string& message,
                   const std::string& detail = "") const;
    json reply_refusal(const json& msg, const Refusal& r) const;

    Refusal apply(int inst, InstState proposed);
    Refusal build_slot(SlotState& st, InstancePtr& out);
    // DDR-063: SOURCE is one value with two doors. `set_input_source` is the
    // writer -- it moves the routing and writes the knob back into every loaded
    // `arbutus/live-input` ENGINE slot (`skip` is the slot that already holds
    // it). `adopt_input_source` is the reader used when a document arrives.
    void set_input_source(int src, const SlotState* skip = nullptr);
    void adopt_input_source(const SlotState& st);
    // A patch names assets by a path relative to the library; this turns one
    // into something openable. [default chosen here: see audio/CLAUDE.md]
    std::string resolve_asset(const std::string& path, const std::string& dir_hint) const;
    void push(const Cmd& c);
    void retire_all();
    json instrument_doc(int i) const;
    ModInputs mod_inputs(const InstState& s) const;
    void push_mod_ctl(int inst);
    Refusal load_patch(int inst, const json& patch);
    void push_env(int inst);
    Refusal load_kit(int inst, const json& kit);
    void normalise_kit(InstState& s) const;
    std::string kit_pad_file(const InstState& s, const std::string& pad) const;
    json waveform(const json& msg, const std::string& path, int bins, std::string& err);
    // DDR-055's file rules as a host message (#62), so the companion app --
    // which talks to this process and not to the UI -- moves library files
    // through the same code the browser does. Paths are relative to
    // `write_root()`.
    json library_op(const json& msg, std::string& code, std::string& err);
    // The one `--library` root anything may be written into: the **last**.
    // Every caller passes the read-only boxes first and the player's library
    // last -- `--library <slot>/boxes --library /data` on the device
    // (`arbutus-audio.service`), `[BOXES, LIBRARY]` in the emulator -- because
    // a stray box dropped into the player's library must not shadow a shipped
    // one. So the writable root is the last, not the first (#62 item 3):
    // resolving a file op against the first wrote a phone's rename, and a
    // bounce with no `file`, into the slot a `.fw` replaces. Empty when the
    // host was started with no root at all (the tests).
    std::string write_root() const;
    // `start` / `frames` report the window that was written, which `trim`
    // makes something the caller did not choose.
    std::string bake(const json& msg, std::string& err,
                     int64_t* start = nullptr, int64_t* frames = nullptr);
    json clip_to_asset(const json& msg, std::string& code, std::string& err);
    // FILE › BOUNCE's MASTER FX switch (screen-sketches § FILE, stage J1).
    // `master_fx_latency` is what the rack declares (§12: the host reports it
    // and compensates nothing live), so a bounce renders that many extra
    // frames and drops them; `master_fx_offline` builds a *second* copy of the
    // master rack on the control thread and runs a rendered mixdown through it
    // block by block. A second copy, not the live one: the live chain belongs
    // to the audio thread.
    uint32_t master_fx_latency() const;
    std::string master_fx_offline(std::vector<float>& l, std::vector<float>& r);
    // The second rack, built once and run a stretch at a time: the whole
    // mixdown in one call (`master_fx_offline`) or a chunk per turn (#920).
    struct MasterFxRun {
        std::unique_ptr<Chain> chain;
        RackCtlPtr ctl;
        std::array<float, 8> macro{};
        arb_host hb{};
        std::vector<float> in_l, in_r, out_l, out_r;
    };
    // An empty string when built; null `chain` when the rack is empty.
    std::string master_fx_build(MasterFxRun& fx);
    void master_fx_run(MasterFxRun& fx, float* l, float* r, std::size_t n);
    // `clip_to_asset`'s render as a state that can stop between chunks.
    // Everything the mix reads is copied in at the start -- the clip lists,
    // the gains, the EQ, the master strip -- so a knob turned or a take
    // recorded while it renders does not reach a bounce already asked for.
    struct Bounce {
        bool all = false;
        int only = -1;
        std::string path;
        int64_t start = 0, len = 0, lat = 0, done = 0, nrender = 0;
        std::vector<Clip> clips[Tape::kTracks];
        float gl[Tape::kTracks]{}, gr[Tape::kTracks]{};
        TrackEq eq[Tape::kTracks];
        float bal_l = 1.0f, bal_r = 1.0f, master = 1.0f;
        float drive_amt = 0.0f, drive_tone = 0.5f;
        bool limiter = false;
        DriveStage drive;
        float lim = 1.0f;
        arb::io::Audio out;
        MasterFxRun fx;
        bool master_fx = false;
    };
    // Renders up to `n` more frames of `b`; true once every frame is in.
    bool bounce_render(Bounce& b, int64_t n);
    // The file written, the library rescanned; an empty string or the error.
    std::string bounce_write(Bounce& b, std::string& code);
    // #920: FILE › BOUNCE as a job (`clip_to_asset {job: true}`). The reply
    // says `started`; `bounce_step` renders one chunk a turn of the loop
    // (live) or one a `render` (offline, so a test owns the clock), and the
    // result goes out as a `bounce` push: `path`, `cancelled` or `error`.
    // `bounce_cancel` drops it with nothing written. Null when none runs.
    std::unique_ptr<Bounce> bounce_;
    void bounce_step();
    void bounce_end(const json& push);
    static constexpr int64_t kBounceChunkDiv = 4;   // sr / 4 frames a turn
    json clip_peaks(const json& msg, std::string& code, std::string& err);
    // One request's body, shared by the single form and the `reqs` batch
    // (#169). Null on a bad request, with `code`/`err` set.
    json peaks_one(const json& req, std::string& code, std::string& err);
    // The longest `reqs` batch. TRACK's redraw is four lanes of however many
    // clips each, so the caller chunks; the cap is here because the reply is
    // built whole in memory before it goes down the socket.
    static constexpr std::size_t kPeaksBatchMax = 64;
    json tape_edit(const json& msg, std::string& code, std::string& err);
    json tape_compact(const json& msg, std::string& code, std::string& err);
    // Frames of track `t`'s medium no clip references -- § 3.8's compaction
    // trigger, and only ever a suggestion (the UI holds references this
    // process cannot see). `state`'s `tape.tracks[t].dead_frames`.
    int64_t dead_frames(int t) const;
    // A compaction that is running when PLAY or REC arrives is cancelled
    // (§ 3.8): the copy would otherwise compete with read-ahead for the card,
    // and a take appends to the very medium it is replacing.
    void cancel_compaction();
    json tape_dir(const json& msg, std::string& code, std::string& err);
    void adopt_media(const int64_t frames[Tape::kTracks]);
    void commit_take();

    // audio
    void apply_cmd(const Cmd& c);
    void end_take();                            // publish the open take for commit_take
    // Audio thread: hand the I/O thread every range of `t` the take has now
    // filled. A ring push and nothing else (`tape_io.hpp`).
    void queue_ranges(int t, int64_t written_to);
    void render_instrument(int inst, uint32_t n, float* l, float* r);
    void build_events(uint32_t n);

    Options opt_;
    arb::box::Library lib_;
    arb_host hb_{};

    InstState inst_[kNumBuses];                 // [kMasterBus] = the master rack
    // Every resolved kit.json this process wrote under the temp directory, so
    // the destructor can take them back out again (#105): on the device /tmp
    // is RAM, and a file per host restart only ever grows until a reboot.
    std::vector<std::string> temp_kits_;
    int active_ = 0;
    bool state_dirty_ = false;
    bool quit_ = false;
    bool hello_ok_ = false;
    bool motion_sensor_ = false;                // § 6.5, set by main.cpp

    // control-side transport / tape / mixer mirror
    double tempo_ = 120.0;
    // LINK (DDR-059), on by default: the tape plays at `tempo_ / link_bpm_`, so
    // slowing the song slows the tape with it. `link_bpm_` is the tempo the
    // *medium* is written at and does not move -- a take recorded while the
    // factor is not 1 is resampled back to it on the way in -- so turning LINK
    // off returns the tape to its own pitch, which is what the off state is
    // for (a second song at a new BPM on the same tape). [default chosen here:
    // the spec gives LINK, not its reference -- audio/CLAUDE.md]
    bool   link_ = true;
    double link_bpm_ = 120.0;
    double link_factor() const { return link_ && link_bpm_ > 0.0 ? tempo_ / link_bpm_ : 1.0; }
    bool   playing_ = false;
    // The track a compaction is running on, or -1 (§ 3.8). Only the control
    // thread touches it; it is what makes `cancel_compaction` a no-op on every
    // PLAY and REC but the one that meets a job.
    int    compacting_ = -1;
    int64_t song_frame_ = 0;
    int    armed_ = -1;
    bool   recording_ = false;
    std::vector<Clip> clips_[Tape::kTracks];
    float  track_gain_[Tape::kTracks] = {1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f};
    float  track_pan_[Tape::kTracks] = {0, 0, 0, 0, 0, 0, 0, 0};
    float  master_ = 1.0f;
    bool   limiter_ = false;
    float  comp_ = 0.0f;                         // MASTER COMP amount, 0 = off
    // MIXER (stage F1). Mute and solo are DDR-061's; the EQ is DDR-062's, one
    // per track, in dB and Hz like every other host-side knob. The MASTER page
    // is LEVEL (master_) - COMP - DRIVE - BAL / COMP REL - DRIVE TONE.
    bool   mute_[Tape::kTracks] = {false, false, false, false, false, false, false, false};
    bool   solo_[Tape::kTracks] = {false, false, false, false, false, false, false, false};
    EqSettings eq_[Tape::kTracks];
    float  comp_rel_ = 0.120f;                   // COMP REL, seconds (the v1 fixed value)
    bool   comp_labelled_ = false;               // #933: COMP's labelled times, else today's
    float  drive_ = 0.0f, drive_tone_ = 0.5f;    // DRIVE, DRIVE TONE; 0 = bypass
    float  bal_ = 0.0f;                          // BAL, -1..+1, 0 = untouched
    // Transport extras the emulator's tape keys need (hollyhock-demo-spec
    // stories 10, 11, 14): loop, varispeed/REV, the FILTER hold, the click,
    // input monitoring. Control-side mirrors for `state`.
    bool    loop_on_ = false;
    int64_t loop_in_ = 0, loop_out_ = 0;
    float   speed_ = 1.0f;
    bool    rev_ = false;
    bool    tf_on_ = false;
    float   tf_cutoff_ = 2000.0f;
    bool    tf_hp_ = false;
    float   click_level_ = 0.0f, click_pitch_ = 1600.0f;
    bool    monitor_on_ = false;
    float   monitor_gain_ = 1.0f;
    // DDR-063: which input `arbutus/live-input` and the MOD `input` sidechain
    // are fed. 0 mic, 1 line, 2 usb, 3 resample (the master minus the armed
    // track). The host owns it, not the box -- the stream is the device's.
    int     input_source_ = 0;
    bool   meters_on_ = false;
    double meters_hz_ = 30.0;
    std::chrono::steady_clock::time_point last_meter_{};
    int64_t meter_frame_ = 0;                    // --offline: frames at the last push
    std::atomic<int64_t> frames_rendered_{0};
    int64_t xrun_reported_ = 0;

    Spsc<Cmd, 1024> cmds_;
    // `handle` refuses `busy` below this many free slots in `cmds_` (#597).
    static constexpr std::size_t kAdmitHeadroom = 64;
    // #638 (device-os-spec § 1.3.4): the key thread's notes. `cmds_` has one
    // producer, the control thread, and cannot take a second; the audio thread
    // drains this one right after it.
    Spsc<Cmd, 256> keys_cmds_;
    Spsc<Retire, 1024> retire_;
    Spsc<Probe, 1024> probes_;                   // #73; drained into events_
    std::vector<json> events_;
    // `waveform` replies, keyed by path and bin count, invalidated by the
    // file's size and mtime. Control thread only; the audio thread never sees
    // a waveform (§15.6).
    struct WaveEntry { std::uintmax_t size = 0; long long mtime = 0; json reply; };
    std::map<std::string, WaveEntry> wave_cache_;
    // `clip_peaks` replies. A committed region of the append-only medium never
    // changes, so the key is the geometry plus a per-track generation that
    // `tape_load` bumps; a region past the head (a take still open) is not cached.
    std::map<std::string, json> peak_cache_;
    uint32_t medium_gen_[Tape::kTracks] = {0, 0, 0, 0, 0, 0, 0, 0};

    // audio-side
    Voice voice_[kNumBuses];
    Tape tape_;
    // Declared before the I/O thread so the thread is joined (its destructor)
    // before the store it writes through goes away.
    std::unique_ptr<TapeStore> tape_store_;
    TapeIo tape_io_;
    // Audio thread: the next range of each track the write-behind owes the
    // card. Set when a take arms, advanced as ranges fill.
    int64_t a_commit_range_[Tape::kTracks] = {};
    // Set by the audio thread when a take ended because the pool had no slot
    // for its next range — a card that stopped answering, converted into write
    // queue until there was nothing left to write into. `poll()` turns it into
    // one `tape_full {reason:"stall"}` push and clears it.
    std::atomic<int64_t> a_tape_stall_{0};
    int64_t tape_stall_reported_ = 0;
    // The same shape for § 3.7's other two ways a take ends. `format` is the
    // medium's own end — today the `--tape-seconds` capacity, which is far
    // under the WAV format's 10,922 ranges, so this is the bound that is
    // really reachable; before step 7 it ended the take's growth silently and
    // said nothing on screen. `disk` is storage refusing for want of space,
    // seen by the I/O thread and acted on here.
    std::atomic<int64_t> a_tape_format_{0};
    int64_t tape_format_reported_ = 0;
    int64_t tape_disk_reported_ = 0;
    // Set when a disk-full ended the take, so `commit_take` shortens the clip
    // to what the card really took. Only then: a commit is deliberately not
    // flushed at the end of an ordinary take, so the durable frame count is
    // behind the take by design and clamping every take to it would throw
    // away the tail of each one.
    bool take_disk_full_ = false;
    // `tape_late` (design § 3.7): one count per rendered block in which some
    // track wanted a range that was absent, and the eight-bit mask of which
    // tracks. The audio thread's own mirror of `xrun_count_`, and the window
    // and the flag are literally the xrun rule — "one rule for both alarms".
    std::atomic<int64_t> a_tape_late_{0};
    std::atomic<uint32_t> a_tape_late_tracks_{0};
    int64_t late_window_base_ = 0;
    int64_t late_reported_ = 0;
    int a_active_ = 0;
    int64_t a_block_ = 0;                        // blocks rendered, for `probe`
    int64_t a_block_t0_ns_ = 0;                  // this block's start, for `probe`
    bool a_playing_ = false;
    bool a_recording_ = false;
    int a_armed_ = -1;
    double a_tempo_ = 120.0;
    int64_t a_song_frame_ = 0;
    float a_track_gain_[Tape::kTracks] = {1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f};
    float a_track_pan_[Tape::kTracks] = {0, 0, 0, 0, 0, 0, 0, 0};
    float a_master_ = 1.0f;
    bool a_limiter_ = false;
    float a_lim_env_ = 1.0f;
    // a d s r glide detune bend_range, in Ck::Env's order (kEnvSlots)
    float a_env_[kNumInstruments][kEnvSlots] = {
        {0.012f, 0.2f, 0.8f, 0.34f, 0.0f, arb::kUnisonSpreadCents, 2.0f},
        {0.012f, 0.2f, 0.8f, 0.34f, 0.0f, arb::kUnisonSpreadCents, 2.0f}};
    uint8_t a_env_mode_[kNumInstruments] = {0, 0};
    int64_t a_rec_start_ = 0;
    int a_rec_track_ = -1;
    int64_t a_rec_medium_ = 0;                  // where the open take writes in the medium
    int64_t a_rec_len_ = 0;                     // frames of *medium* written, = the clip's len
    double  a_rec_frac_ = 0.0;                  // resampler phase when LINK is not 1x
    float   a_link_ = 1.0f;                     // the tape's rate from LINK (DDR-059)
    // The tape playhead. The clock (`a_song_frame_`) always runs forward at 1x
    // while playing -- tempo, SEQ and the click follow it, as the emulator's
    // did -- and this is where the tape reads: varispeed, REV and the loop.
    double  a_tape_pos_ = 0.0;
    float   a_speed_ = 1.0f;
    bool    a_rev_ = false;
    double  a_rev_anchor_ = 0.0, a_rev_elapsed_ = 0.0;
    bool    a_loop_on_ = false;
    int64_t a_loop_in_ = 0, a_loop_out_ = 0;
    bool    a_tf_on_ = false;
    float   a_tf_cutoff_ = 2000.0f;
    bool    a_tf_hp_ = false;
    float   a_tf_y_[2] = {0.0f, 0.0f};
    double  a_scrub_pos_ = 0.0;
    int64_t a_scrub_left_ = 0, a_scrub_total_ = 0;
    float   a_click_level_ = 0.0f, a_click_pitch_ = 1600.0f;
    bool    a_mon_on_ = false;
    float   a_mon_gain_ = 1.0f;
    float   a_comp_ = 0.0f, a_comp_env_ = 0.0f;
    float   a_comp_rel_ = 0.120f;                // COMP REL, seconds
    bool    a_comp_labelled_ = false;            // #933, `comp_times`
    bool    a_mute_[Tape::kTracks]{}, a_solo_[Tape::kTracks]{};
    TrackEq a_eq_[Tape::kTracks];                // DDR-062, flat = bypass
    DriveStage a_drive_st_;
    float   a_drive_ = 0.0f, a_drive_tone_ = 0.5f, a_bal_ = 0.0f;
    std::vector<arb_event> pending_;             // notes / soft-keys / transport, frame 0

    std::vector<arb_event> ev_all_, ev_engine_, ev_clock_, ev_musical_;
    std::vector<float> scratch_;                // tape tracks, buses, input
    std::vector<float> self_ring_, master_ring_; // `self`/`master` side[] delay lines, 2 x max_block
    std::vector<float> resample_ring_;           // SOURCE=resample: the same line, minus the armed track
    int     a_input_src_ = 0;
    uint32_t ring_pos_ = 0;
    float* buf(int i, int ch) {
        return scratch_.data() + (std::size_t(i) * 2 + std::size_t(ch)) * opt_.max_block;
    }

    std::atomic<int64_t> xrun_count_{0};
    int64_t xrun_window_base_ = 0;               // control thread: total when the window opened
    std::chrono::steady_clock::time_point xrun_window_start_{};
    std::atomic<float> meter_master_[2]{};
    std::atomic<float> meter_track_[Tape::kTracks]{};
    std::atomic<float> meter_inst_[2]{};
    // audio -> control: the MOD rack's live target values, for `meters`.
    uint32_t mod_gen_[kNumBuses] = {0, 0, 0};             // control side: latest ModCtl
    std::atomic<float> mod_live_[kNumBuses][kMaxRoutes]{};
    std::atomic<uint32_t> mod_live_gen_[kNumBuses]{};
    std::atomic<uint32_t> mod_live_mask_[kNumBuses]{};
    int64_t rt_allocs_ = 0;                             // --rt-guard, offline only
    std::atomic<int64_t> a_song_frame_pub_{0};
    std::atomic<int64_t> a_tape_pos_pub_{0};
    std::atomic<float>   comp_gr_{1.0f};
    // audio -> control: a finished take, as a clip the UI then owns.
    std::atomic<int64_t> take_end_{-1};
    std::atomic<int64_t> take_start_{0};
    std::atomic<int>     take_track_{-1};
    std::atomic<int64_t> take_src_{0};
};

}  // namespace arb::host
