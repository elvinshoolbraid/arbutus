#include "audio_host.hpp"
#include "events.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <limits>
#include <set>

#include <unistd.h>

#include <audiofile.hpp>
#include <manifest.hpp>
#include <params.hpp>
#include <rt_alloc_count.hpp>
#include <wav.hpp>

namespace arb::host {
namespace {

// Do two paths name one file? Lexically first -- a fake card's media are not
// on disk at all -- then, when both exist, by inode, so a symlinked or
// relative spelling of the medium's own path is still recognised.
bool same_file(const std::string& a, const std::string& b) {
    namespace fs = std::filesystem;
    if (fs::path(a).lexically_normal() == fs::path(b).lexically_normal()) return true;
    std::error_code ec;
    return fs::equivalent(a, b, ec) && !ec;
}

constexpr int kBufTape0 = 0;      // 0..7 tape tracks (DDR-060)
constexpr int kBufInstBus = Tape::kTracks;
constexpr int kBufInstOld = kBufInstBus + 1;
constexpr int kBufInput = kBufInstBus + 2;
constexpr int kBufMaster = kBufInstBus + 3;
constexpr int kBufSelfSide = kBufInstBus + 4;   // `self` side[]: the bus, one block late
constexpr int kBufMasterSide = kBufInstBus + 5; // `master` side[]
constexpr int kBufTapeMix = kBufInstBus + 6;    // the tracks through the mixer, pre FILTER hold
constexpr int kBufTake = kBufInstBus + 7;       // the overdub take: instrument + this track (DDR-056)
constexpr int kBufTrackEq = kBufInstBus + 8;    // one track through its EQ, on the way to the mix
constexpr int kBufMasterIn = kBufInstBus + 9;   // what the master rack is fed (§15.5)
constexpr int kBufMasterOld = kBufInstBus + 10; // the master rack's outgoing chain, mid-crossfade
constexpr int kBufInputSrc = kBufInstBus + 11;  // what SOURCE selects (DDR-063): the ENGINE's side[]
constexpr int kBufResampleCut = kBufInstBus + 12; // what `resample` takes back out of the master
constexpr int kBufResample = kBufInstBus + 13;  // `resample`: the master minus that, one block late
constexpr int kNumBufs = kBufInstBus + 14;

// SOURCE (DDR-063, §15.2), in the spec's order. `resample` is the master bus
// minus the armed track; mic / line / usb all read the one stream this process
// opened, because choosing which jack feeds it is the codec's job on the device
// and there is nothing here to choose between (the emulator has one input).
constexpr int kSrcResample = 3;
const char* const kSourceNames[] = {"mic", "line", "usb", "resample"};
constexpr int kNumSources = 4;
constexpr const char* kLiveInputId = "arbutus/live-input";
constexpr const char* kLiveInputSource = "source";

int source_from(const std::string& s) {
    for (int i = 0; i < kNumSources; ++i)
        if (s == kSourceNames[i]) return i;
    return -1;
}

// DDR-056: a take absorbs what it covers -- the overlapped portion is already
// inside it (record sums the track's own playback), the remainders survive as
// their own clips, and a track's list never overlaps. The same geometry as the
// UI's `record_commit` (emulator app/tape.py), kept here so `state` is always
// a legal list whoever asked for the take.
std::vector<Clip> absorb(const std::vector<Clip>& clips, const Clip& take) {
    std::vector<Clip> out;
    const int64_t s = take.start_frame, e = take.start_frame + take.len;
    for (const Clip& c : clips) {
        const int64_t cs = c.start_frame, ce = c.start_frame + c.len;
        if (ce <= s || cs >= e) { out.push_back(c); continue; }
        if (cs < s) {                                  // head survives
            Clip h = c;
            h.len = s - cs;
            out.push_back(h);
        }
        if (ce > e) {                                  // tail survives, reading later
            Clip t = c;
            t.start_frame = e;
            t.len = ce - e;
            t.src_offset = c.src_offset + (e - cs);
            out.push_back(t);
        }
    }
    out.push_back(take);
    std::stable_sort(out.begin(), out.end(),
                     [](const Clip& a, const Clip& b) { return a.start_frame < b.start_frame; });
    return out;
}

// Ck::Mixer's `i0`: a track's gain, a track's pan, then the master strip.
constexpr int kMixMaster = 2 * Tape::kTracks;
constexpr int kMixLimiter = kMixMaster + 1;
constexpr int kMixComp = kMixMaster + 2;
// Stage F1: the rest of the MASTER page, and DDR-061's mute/solo (i1 = track).
constexpr int kMixCompRel = kMixMaster + 3;
constexpr int kMixDrive = kMixMaster + 4;
constexpr int kMixDriveTone = kMixMaster + 5;
constexpr int kMixBal = kMixMaster + 6;
constexpr int kMixMute = kMixMaster + 7;
constexpr int kMixSolo = kMixMaster + 8;
// #933: MASTER COMP's times, today's per-block coefficient or the labelled ones.
constexpr int kMixCompTimes = kMixMaster + 9;
// The labelled times' grid: the envelope steps every 32 frames whatever the
// block (audio/CLAUDE.md § Block size), so 5 ms means 5 ms at 64 and at 256.
constexpr uint32_t kCompStep = 32;

// The emulator's scrub grain and click, as it had them (tape.py SCRUB_FADE,
// tempo.py CLICK_LEN_S): same numbers, so the demo sounds the same natively.
constexpr uint32_t kScrubFade = 64;
constexpr double kClickLenS = 0.030;
constexpr float kSpeedMin = 0.25f, kSpeedMax = 4.0f;

// --rt-guard: live heap blocks, the same smoke alarm arbutus-run uses -- and
// since #52 the same code, counting on Linux/glibc as well as on macOS.
using arb::rt::live_blocks;

constexpr double kXrunWindowS = 2.0;   // §6.8 [TBD-Elfy: N in M s]; 3 in 2 s
constexpr int kXrunFlagCount = 3;

const char* env_mode_name(int m) {
    switch (m) {
    case 1: return "mono";
    case 2: return "legato";
    case 3: return "unison";
    default: return "poly";
    }
}

int env_mode_from(const std::string& s) {
    if (s == "mono") return 1;
    if (s == "legato") return 2;
    if (s == "unison") return 3;
    return 0;
}

float getf(const json& j, const char* k, float d) {
    auto it = j.find(k);
    return (it != j.end() && it->is_number()) ? it->get<float>() : d;
}

std::string gets(const json& j, const char* k, const std::string& d = {}) {
    auto it = j.find(k);
    return (it != j.end() && it->is_string()) ? it->get<std::string>() : d;
}

}  // namespace

// ---------------------------------------------------------------------------
// state documents
// ---------------------------------------------------------------------------
json SlotState::to_json() const {
    if (!loaded) return nullptr;
    json p = json::object();
    for (const auto& [id, v] : params) p[id] = arb::manifest::nice(v);
    json desc = json::object();
    for (const char* k : {"caps", "honours_env", "latency_frames", "params", "softkeys",
                          "assets", "outputs", "description"})
        if (manifest.contains(k)) desc[k] = manifest[k];
    json as = json::object();
    for (const AssetRef& a : assets) as[a.key] = a.path;      // resolved, absolute
    json j{{"id", id}, {"version", version}, {"min_version", min_version},
           {"params", p}, {"assets", as}, {"desc", desc}};
    // ABI 2 format_param: the engine's own readout ("1/8", "STOP") for the
    // value in `params`, keyed the same way. Only params the engine chose to
    // label appear, and the key is absent when none do, so a state document
    // for a box without format_param is byte-for-byte what it was (PROTOCOL.md).
    if (fmt_vt && fmt_vt->format_param && fmt_h) {
        json disp = json::object();
        for (std::size_t i = 0; i < pids.size(); ++i) {
            const auto it = params.find(pids[i]);
            if (it == params.end()) continue;
            char buf[32] = {};
            if (fmt_vt->format_param(fmt_h, uint32_t(i), it->second, buf, sizeof buf) != 0) {
                buf[sizeof buf - 1] = '\0';
                disp[pids[i]] = std::string(buf);
            }
        }
        if (!disp.empty()) j["display"] = std::move(disp);
    }
    return j;
}

json AudioHost::instrument_doc(int i) const {
    const InstState& s = inst_[i];
    json tabs = json::array();
    for (const SlotState& t : s.tabs) tabs.push_back(t.to_json());
    json mod_tabs = json::array();
    for (const SlotState& t : s.mod_tabs) mod_tabs.push_back(t.to_json());
    json macro = json::array();
    for (float v : s.macro) macro.push_back(arb::manifest::nice(v));
    return json{
        {"name", s.name},
        {"octave", s.octave},
        {"engine", s.engine.to_json()},
        {"seq", s.seq.to_json()},
        {"seq_pattern", s.seq_pattern},
        {"env", json{{"a", arb::manifest::nice(s.env_a)}, {"d", arb::manifest::nice(s.env_d)},
                     {"s", arb::manifest::nice(s.env_s)}, {"r", arb::manifest::nice(s.env_r)},
                     {"mode", env_mode_name(s.env_mode)},
                     {"glide", arb::manifest::nice(s.env_glide)},
                     {"detune", arb::manifest::nice(s.env_detune)},
                     {"bend_range", arb::manifest::nice(s.env_bend_range)}}},
        {"lfo", s.lfo},
        {"kit", s.kit.is_null() ? json() : s.kit},
        {"fx", s.rack.to_json()},
        {"tabs", tabs},
        {"macros", macro},
        // Added with the MOD rack; a client that predates it ignores them.
        {"mod", s.mod.to_json()},
        {"mod_tabs", mod_tabs},
        {"host", json{{"level", arb::manifest::nice(s.host_level)},
                      {"pan", arb::manifest::nice(s.host_pan)},
                      {"pitch", arb::manifest::nice(s.host_pitch)}}}};
}

// What `state.tape.io` says: the ranges the audio thread has handed over and
// what the card has taken. `null` with no `--tape-dir`, so "no I/O thread" and
// "an I/O thread that has done nothing" do not read the same.
json AudioHost::tape_io_doc() const {
    if (!tape_io_.on()) return nullptr;
    const TapeIo::Stats st = tape_io_.stats();
    json j = json{{"dir", tape_io_.dir()},
                  {"range_frames", Tape::kRangeFrames},
                  {"queued", st.queued},
                  {"committed", st.committed},
                  {"dropped", st.dropped},
                  {"absent", st.absent},
                  {"errors", st.errors},
                  {"syncs", st.syncs},
                  {"queue", st.queue}};
    // The bounded pool (C1 step 2b), as the **I/O thread** sees it: the free
    // list, the fetches, the evictions, the starvation. Only with a
    // `--tape-dir`, because without one there is no thread. `slots` and
    // `resident` are the same two accessors `tape.pool` renders (step 7) —
    // that document is the **player's** view and is there whether or not a
    // thread is, which is why the bench may read either and the UI reads one.
    if (tape_.bounded())
        j["pool"] = json{{"slots", tape_.pool_slots()},
                         {"resident", tape_.resident_count()},
                         {"free", tape_.free_count()},
                         {"fetched", st.fetched},
                         {"evicted", st.evicted},
                         {"starved", st.starved}};
    // § 3.6's horizon (C1 step 5). `wanted` is the set the last turn planned
    // and `capped` counts the turns the `pool / 2` cap truncated it — eight
    // tracks at 4x, the rare case § 3.6 names, and the number that says the
    // flags want lowering. Under `io` for the same reason `pool` is.
    if (tape_.bounded())
        j["readahead"] = json{{"ahead_s", tape_io_.ahead_seconds()},
                              {"behind_s", tape_io_.behind_seconds()},
                              {"wanted", st.wanted},
                              {"capped", st.capped}};
    // Repair on open (step 3, design § 3.5). Reported rather than only done,
    // because `lost_frames` is what a cut cost the player -- the frames the
    // provisional clip (DDR-070) has to be shortened by -- and `refused` is a
    // file in the directory that is not one of our media, which is a thing
    // somebody has to be told about rather than a counter to watch.
    if (st.repaired || st.lost_frames || st.refused)
        j["repair"] = json{{"media", st.repaired},
                           {"lost_frames", st.lost_frames},
                           {"refused", st.refused}};
    // § 3.4's lead (#158). `refused` is a filesystem that cannot preallocate --
    // one count per medium, after which that medium grows the way it did before
    // -- so it is reported beside the commit times it is there to shorten.
    // The peaks sidecar (#163). `hit`/`miss` are `clip_peaks` answers, and a
    // `miss` that climbs while a settled clip is drawn is the bins count being
    // asked finer than `bin_frames`, not a cold sidecar -- which is the other
    // half of that ticket and lives in the UI.
    j["peaks"] = json{{"on", tape_io_.peaks_on()},
                      {"bins_per_range", TapeIo::kPeakBins},
                      {"bin_frames", TapeIo::kPeakBinFrames},
                      {"ranges", st.peaks_ranges},
                      {"hit", st.peaks_hit},
                      {"miss", st.peaks_miss}};
    j["prealloc"] = json{{"on", tape_io_.prealloc()},
                         {"lead_ranges", TapeIo::kAllocLeadRanges},
                         {"calls", st.allocs},
                         {"bytes", st.alloc_bytes},
                         {"refused", st.alloc_refused}};
    if (!tape_io_.last_error().empty()) j["last_error"] = tape_io_.last_error();
    // Step 2's own measurement (design § 6.2): the commit's wall time on the
    // card, from the production path. Percentiles over the last 4096, which is
    // more than an hour of a take at one range a second.
    std::vector<int32_t> us;
    int64_t n = 0;
    int32_t worst = 0;
    const auto percentiles = [](std::vector<int32_t>& v, int64_t count, int32_t max) {
        json d = json{{"count", count}, {"max", max}};
        if (!v.empty()) {
            std::sort(v.begin(), v.end());
            const auto pick = [&v](double p) {
                const std::size_t i =
                    std::min(v.size() - 1, std::size_t(p * double(v.size() - 1) + 0.5));
                return v[i];
            };
            d["p50"] = pick(0.5);
            d["p99"] = pick(0.99);
            // **Counts, because a percentile needs samples and a two-minute
            // take has 121** (#158). `p99` of 121 is the 120th of 121 sorted,
            // which is the second-largest sample, not a percentile: across
            // twelve CM4 cells it ranged 15-142 ms while `p50` held to
            // +/-0.5 ms, and reading it as a percentile is what made step 2
            // conclude the cost was in growing the file. These two counts are
            // stable at any N and are what § 6.2's line should be read
            // against: how often a commit ran past a range's own 1.024 s of
            // cover, and how often it ran past the design's ~20 ms.
            d["over_20ms"] = int64_t(v.end() - std::upper_bound(v.begin(), v.end(), 20000));
            d["over_100ms"] = int64_t(v.end() - std::upper_bound(v.begin(), v.end(), 100000));
        }
        return d;
    };
    tape_io_.commit_times(us, n, worst);
    j["commit_us"] = percentiles(us, n, worst);
    // The `fdatasync` alone, out of the same commits. #158's lead did not bring
    // `commit_us` inside § 6.2's ~20 ms on the eMMC, and "the commit is 63 ms"
    // does not say whether that is our two `pwrite`s or the card -- so the two
    // are reported apart, and the difference is what the follow-up works on.
    tape_io_.sync_times(us, n, worst);
    j["sync_us"] = percentiles(us, n, worst);
    return j;
}

// Control thread only: `Tape::run_at` with a fetch behind it (C1 step 2b). The
// six readers that walk a medium — `bake`, `clip_to_asset`, `clip_peaks`,
// `tape_save`, `tape_edit`'s sources and the mixdown — are allowed to block on
// the card, and with a bounded pool they have to: the range they want may be on
// it and not in RAM. Unbounded (no `--tape-dir`) this is exactly `run_at`.
//
// The pin is what stops the evictor taking the range between the fetch and the
// copy. One pin is enough because a reader holds one run at a time; the caller
// releases it with `tape_unpin()` when it is done.
const float* AudioHost::tape_run(int t, int64_t f, int64_t& run) const {
    const float* p = tape_.run_at(t, f, run);
    if (p || run <= 0 || f < 0 || !tape_.bounded() || !tape_io_.on()) return p;
    const int64_t k = Tape::range_of(f);
    tape_.pin(t, k);
    if (!const_cast<TapeIo&>(tape_io_).fetch(t, k)) {
        tape_.pin(-1, 0);
        return nullptr;                        // silence, as an absent range is
    }
    return tape_.run_at(t, f, run);
}

// A take and a `tape_edit` append at the head, which is usually inside a range
// that already holds audio. Bounded, that range must be fetched back before it
// is written into: `acquire` would otherwise hand out a zeroed slot and the
// commit would write those zeroes over the earlier take.
void AudioHost::tape_prepare_head(int t, int64_t at) const {
    if (!tape_.bounded() || !tape_io_.on()) return;
    if (at % Tape::kRangeFrames == 0) return;  // aligned: nothing in front of it
    const_cast<TapeIo&>(tape_io_).fetch(t, Tape::range_of(at));
}

// § 3.8's compaction trigger, the half the host can answer. A medium's dead
// frames are the ones **no clip anywhere references** -- a retake's first
// version, a LIFT with no DROP, a `tape_edit`'s discarded source. The host
// cannot say it exactly, because the UI holds two references this process has
// never seen (the lift buffer and the one-level undo record, DDR-054/055), so
// this is a *suggestion*: it can only over-report, never under-report, and the
// UI takes it as "worth compacting" and computes the real keep set itself.
//
// A clip on any track may read this track's medium (`src_track`), so the sweep
// is over all eight lists, not over track `t`'s.
int64_t AudioHost::dead_frames(int t) const {
    const int64_t used = tape_.head[t].load(std::memory_order_relaxed);
    if (used <= 0) return 0;
    std::vector<std::pair<int64_t, int64_t>> spans;
    for (int u = 0; u < Tape::kTracks; ++u)
        for (const Clip& c : clips_[u]) {
            if ((c.src_track >= 0 ? c.src_track : u) != t) continue;
            const int64_t from = std::max<int64_t>(0, c.src_offset);
            const int64_t to = std::min(used, c.src_offset + c.len);
            if (to > from) spans.emplace_back(from, to);
        }
    std::sort(spans.begin(), spans.end());
    int64_t covered = 0, at = 0;
    for (const auto& sp : spans) {
        const int64_t from = std::max(sp.first, at);
        if (sp.second > from) {
            covered += sp.second - from;
            at = sp.second;
        }
    }
    return std::max<int64_t>(0, used - covered);
}

json AudioHost::state_doc() const {
    json tracks = json::array();
    for (int t = 0; t < Tape::kTracks; ++t) {
        json cl = json::array();
        for (const Clip& c : clips_[t])
            cl.push_back(json{{"start_frame", c.start_frame}, {"len", c.len},
                              {"src_offset", c.src_offset}, {"gain", arb::manifest::nice(c.gain)},
                              {"src_track", c.src_track >= 0 ? c.src_track : t}});
        const EqSettings& e = eq_[t];
        tracks.push_back(json{{"clips", cl},
                              {"gain", arb::manifest::nice(track_gain_[t])},
                              {"pan", arb::manifest::nice(track_pan_[t])},
                              // DDR-061 / DDR-062: the mixer strip of this track.
                              {"mute", mute_[t]},
                              {"solo", solo_[t]},
                              {"eq", json{{"low", arb::manifest::nice(e.low)},
                                          {"mid", arb::manifest::nice(e.mid)},
                                          {"high", arb::manifest::nice(e.high)},
                                          {"mid_f", arb::manifest::nice(e.mid_f)},
                                          {"mid_q", arb::manifest::nice(e.mid_q)},
                                          {"hp", arb::manifest::nice(e.hp)},
                                          {"lp", arb::manifest::nice(e.lp)}}},
                              {"used_frames", tape_.head[t].load(std::memory_order_relaxed)},
                              // C1 step 8 (§ 3.8): what a compaction could win
                              // back, as far as this process can tell.
                              {"dead_frames", dead_frames(t)}});
    }
    // The master bus (§15.5): the same rack document an instrument reports,
    // plus its tabs' slot states, under `mixer` because that is the page it is
    // on (MIXER › FX).
    json mtabs = json::array();
    for (const SlotState& t : inst_[kMasterBus].tabs) mtabs.push_back(t.to_json());
    json mmacro = json::array();
    for (float v : inst_[kMasterBus].macro) mmacro.push_back(arb::manifest::nice(v));
    const int64_t xtotal = xrun_count_.load(std::memory_order_relaxed);
    const int64_t xw = xtotal - xrun_window_base_;
    return json{
        {"type", "state"},
        {"format", 1},
        {"proto", kProtoVersion},
        {"abi", ARB_ABI},
        {"firmware", kFirmware},
        {"offline", opt_.offline},
        {"sample_rate", opt_.sr},
        {"block", opt_.block},
        {"max_block", opt_.max_block},
        {"tempo", tempo_},
        {"ppqn", hb_.ppqn},
        {"playing", playing_},
        {"song_frame", song_frame_},
        {"active", active_ == 0 ? "synth" : "drum"},
        {"instruments", json{{"synth", instrument_doc(0)}, {"drum", instrument_doc(1)}}},
        {"tape", json{{"capacity_frames", tape_.capacity()},
                      {"seconds", opt_.tape_seconds},
                      {"armed", armed_},
                      {"recording", recording_},
                      {"position", a_tape_pos_pub_.load(std::memory_order_relaxed)},
                      {"loop", json{{"in", loop_in_}, {"out", loop_out_}, {"on", loop_on_}}},
                      {"speed", arb::manifest::nice(speed_)},
                      {"reverse", rev_},
                      {"link", json{{"on", link_},
                                    {"base_bpm", arb::manifest::nice(float(link_bpm_))},
                                    {"factor", arb::manifest::nice(float(link_factor()))}}},
                      {"filter", json{{"on", tf_on_}, {"cutoff", arb::manifest::nice(tf_cutoff_)},
                                      {"mode", tf_hp_ ? "HP" : "LP"}}},
                      // The write-behind's own counters (C1 step 2a). Present
                      // only with a `--tape-dir`, so a client that has never
                      // seen one is not told about a thread that is not there.
                      {"io", tape_io_doc()},
                      // The RAM window (C1 step 7, § 3.7): what FILE > SYSTEM
                      // reads and what the bench reads. Always present, even
                      // unbounded — `ranges` 0 then, which is how a client
                      // tells "no window" from "a window with nothing in it".
                      {"pool", json{{"ranges", tape_.pool_slots()},
                                    {"resident", tape_.resident_count()},
                                    {"queued", tape_io_.stats().queue},
                                    {"late_total", a_tape_late_.load(std::memory_order_relaxed)}}},
                      {"tracks", tracks}}},
        {"mixer", json{{"master", arb::manifest::nice(master_)}, {"limiter", limiter_},
                       {"comp", arb::manifest::nice(comp_)},
                       {"comp_rel", arb::manifest::nice(comp_rel_)},
                       {"comp_times", comp_labelled_ ? "labelled" : "block"},
                       {"drive", arb::manifest::nice(drive_)},
                       {"drive_tone", arb::manifest::nice(drive_tone_)},
                       {"bal", arb::manifest::nice(bal_)},
                       {"fx", inst_[kMasterBus].rack.to_json()},
                       {"tabs", mtabs},
                       {"macros", mmacro}}},
        {"metronome", json{{"level", arb::manifest::nice(click_level_)},
                           {"pitch", arb::manifest::nice(click_pitch_)}}},
        {"monitor", json{{"on", monitor_on_}, {"gain", arb::manifest::nice(monitor_gain_)}}},
        {"input", json{{"source", kSourceNames[std::clamp(input_source_, 0, kNumSources - 1)]}}},
        // § 6.5: fitted *and* answering. In the state document and not only in
        // `hello` because a bus error mid-run turns it off again (#116).
        {"motion_sensor", motion_sensor_},
        // `total` (since start, as on the push) lets a client that connects
        // late count only its own run's xruns: xrun-watch.py takes it as the
        // baseline, where it used to report the host's lifetime count (#626).
        {"xrun", json{{"count", xw}, {"window_s", kXrunWindowS}, {"total", xtotal},
                      {"flag", xw >= kXrunFlagCount}}}};
}

// #853 (PROTOCOL.md § The live stream). Every number is rounded to what a
// player could tell apart, because the stream pushes a patch whenever this
// document differs from the last one sent: a playhead in frames would be a
// patch every block, in tenths of a second it is ten a second while rolling
// and none while stopped. No nulls anywhere: a merge patch (RFC 7396) reads
// null as "remove", so an absent fact is an absent field.
json AudioHost::live_doc() const {
    // round(v * n) / n, not round(v / step) * step: 41 * 0.1 is 4.1000000000000005
    auto round_to = [](double v, double per) { return std::round(v * per) / per; };
    const double sr = double(opt_.sr > 0 ? opt_.sr : 48000);
    // The song clock in beats at the tempo now; 4/4, the only metre v1 has
    // (TEMPO has no time signature), so bar and beat count from 1.
    const double beats = tempo_ > 0.0 ? double(song_frame_) * tempo_ / (60.0 * sr) : 0.0;
    const int64_t whole = int64_t(std::floor(std::max(0.0, beats)));
    const int64_t pos = a_tape_pos_pub_.load(std::memory_order_relaxed);
    json transport{{"playing", playing_},
                   {"recording", recording_},
                   {"position_s", round_to(double(pos) / sr, 10)},
                   {"bar", whole / 4 + 1},
                   {"beat", whole % 4 + 1},
                   {"loop", loop_on_},
                   {"speed", round_to(speed_, 100)},
                   {"reverse", rev_}};
    if (armed_ >= 0) transport["armed"] = armed_ + 1;
    // Keyed "1".."8", not an array: a merge patch replaces an array whole, so
    // one new clip would resend all eight tracks.
    json tracks = json::object();
    for (int t = 0; t < Tape::kTracks; ++t) {
        // From the start of its first clip to the end of its last, on the
        // timeline: what a player means by "how long is track 2".
        const bool has = !clips_[t].empty();
        int64_t first = has ? clips_[t].front().start_frame : 0, last = 0;
        for (const Clip& c : clips_[t]) {
            first = std::min(first, c.start_frame);
            last = std::max(last, c.start_frame + c.len);
        }
        tracks[std::to_string(t + 1)] =
            json{{"has_clip", has},
                 {"clips", clips_[t].size()},
                 {"length_s", has ? round_to(double(last - first) / sr, 100) : 0.0},
                 {"mute", mute_[t]},
                 {"solo", solo_[t]},
                 {"level", round_to(track_gain_[t], 100)}};
    }
    auto slots = [&](int i) {
        json s = json::object();
        if (!inst_[i].engine.id.empty()) s["engine"] = inst_[i].engine.id;
        if (!inst_[i].seq.id.empty()) s["sequencer"] = inst_[i].seq.id;
        return s;
    };
    const int64_t xw = xrun_count_.load(std::memory_order_relaxed) - xrun_window_base_;
    const int64_t lw = a_tape_late_.load(std::memory_order_relaxed) - late_window_base_;
    return json{
        {"transport", transport},
        {"tempo", json{{"bpm", round_to(tempo_, 10)},
                       {"link", link_},
                       {"click", json{{"on", click_level_ > 0.0f},
                                      {"level", round_to(click_level_, 100)}}}}},
        {"tracks", tracks},
        {"instruments", json{{"front", active_ == 0 ? "synth" : "drum"},
                             {"synth", slots(0)},
                             {"drum", slots(1)}}},
        {"input", json{{"source", kSourceNames[std::clamp(input_source_, 0, kNumSources - 1)]},
                       {"monitor", monitor_on_}}},
        {"flags", json{{"cpu_overload", xw >= kXrunFlagCount},
                       {"tape_slow", lw >= kXrunFlagCount},
                       {"motion_sensor", motion_sensor_}}}};
}

// ---------------------------------------------------------------------------
// lifecycle
// ---------------------------------------------------------------------------
AudioHost::~AudioHost() {
    // Tear the graph down on the control thread, where delete is legal.
    for (Voice& v : voice_) {
        delete v.cur;
        delete v.old;
        delete v.ctl;
        delete v.mctl;
        v = Voice{};
    }
    retire_all();
    for (int t = 0; t < Tape::kTracks; ++t) delete tape_.swap_clips(t, nullptr);
    // The resolved kits go back out of $TMPDIR (#105). The engines that held
    // them as the "kit" asset are gone by now, above.
    std::error_code ec;
    for (const std::string& p : temp_kits_) std::filesystem::remove(p, ec);
    temp_kits_.clear();
}

bool AudioHost::init(const Options& o, std::string& err) {
    opt_ = o;
    opt_.max_block = std::max(opt_.max_block, opt_.block);
    if (opt_.max_block > ARB_MAX_BLOCK) opt_.max_block = ARB_MAX_BLOCK;
    if (opt_.block > opt_.max_block) opt_.block = opt_.max_block;

    for (const std::string& r : opt_.library_roots) lib_.add_root(r);
    for (const std::string& r : opt_.library_fallbacks) lib_.add_fallback(r);
    lib_.rescan();

    hb_.sample_rate = opt_.sr;
    hb_.max_block = opt_.max_block;
    hb_.tempo_bpm = tempo_;
    hb_.ppqn = 24;                      // §3.3 default; [TBD-Elfy]
    hb_.playing = 0;
    hb_.song_frame = 0;
    hb_.env_a = inst_[0].env_a;
    hb_.env_d = inst_[0].env_d;
    hb_.env_s = inst_[0].env_s;
    hb_.env_r = inst_[0].env_r;
    hb_.env_mode = 0;
    hb_.env_detune = inst_[0].env_detune;
    hb_.env_glide = inst_[0].env_glide;
    hb_.bend_range = inst_[0].env_bend_range;
    hb_.pitch_st = 0.0f;
    hb_.rng_seed = opt_.seed;
    inst_[1].env_a = a_env_[1][0] = kDrumEnvA;     // DDR-053, see kDrumEnvA
    inst_[1].env_d = a_env_[1][1] = kDrumEnvD;
    // ABI 2 motion[]: zeros until there is an IMU to read (modulation-spec
    // §13); zero is the documented "no sensor", not a missing value.
    for (float& m : hb_.motion) m = 0.0f;

    // The pool is bounded only when there is a medium on storage behind it
    // (C1 step 2b): without one, an evicted range cannot be fetched back, and a
    // bounded pool that cannot refetch does not save memory — it loses audio on
    // its first eviction. Two slots is the floor a test can ask for, since
    // `tape-streaming-design.md` § 4's T2 control runs a 3-second pool.
    const int64_t pool_slots =
        opt_.tape_dir.empty()
            ? 0
            : std::max<int64_t>(2, int64_t(opt_.tape_pool_seconds * double(opt_.sr) /
                                           double(Tape::kRangeFrames) + 0.5));
    tape_.init(int64_t(opt_.tape_seconds * double(opt_.sr)), pool_slots);
    // C1 step 2a: with a `--tape-dir` the take is written behind onto storage
    // by the I/O thread (`tape_io.hpp`); with none the tape is RAM only and
    // every call into `tape_io_` is a no-op, which is what the emulator, the
    // device and every test do until step 3.
    if (!opt_.tape_dir.empty()) {
        std::error_code ec;
        std::filesystem::create_directories(opt_.tape_dir, ec);
        if (ec) {
            err = "tape dir: " + ec.message();
            return false;
        }
        if (opt_.tape_store_fake) {
            auto fake = std::make_unique<FakeStore>();
            if (opt_.tape_free_bytes >= 0) fake->set_free_bytes(opt_.tape_free_bytes);
            fake->set_clock(&tape_.block_counter());
            tape_store_ = std::move(fake);
        } else {
            tape_store_ = std::make_unique<PosixStore>();
        }
        tape_io_.set_prealloc(opt_.tape_prealloc);
        tape_io_.set_peaks(opt_.tape_peaks);
        tape_io_.set_free_cap(opt_.tape_free_bytes);
        tape_io_.configure(&tape_, tape_store_.get(), opt_.tape_dir, opt_.sr);
        // C1 step 3b (#157): the folder may already hold media -- the working
        // tape of the session before this one. Open them in place, repaired,
        // and take each head from its header, so a take appends after them
        // rather than over them. Before the thread starts: nothing can race.
        {
            int64_t frames[Tape::kTracks];
            std::string code, why;
            if (tape_io_.set_dir(opt_.tape_dir, false, frames, code, why)) adopt_media(frames);
        }
        // § 3.6's horizon (C1 step 5). After `configure`, which does not touch
        // it: the flags outlive a reconfigure.
        tape_io_.set_horizon(opt_.tape_readahead_seconds, opt_.tape_behind_seconds);
        // Offline there are no threads: `render_offline` services the ring
        // once per block, so the offline path is the real path.
        if (!opt_.offline) tape_io_.start();
    }
    scratch_.assign(std::size_t(kNumBufs) * 2 * opt_.max_block, 0.0f);
    // `self` / `master` (modulation-spec §4): a max_block-frame delay line each,
    // sized here so the block never allocates.
    self_ring_.assign(2 * std::size_t(opt_.max_block), 0.0f);
    master_ring_.assign(2 * std::size_t(opt_.max_block), 0.0f);
    resample_ring_.assign(2 * std::size_t(opt_.max_block), 0.0f);
    ring_pos_ = 0;
    ev_all_.reserve(1024);
    ev_engine_.reserve(1024);
    ev_clock_.reserve(1024);
    ev_musical_.reserve(1024);
    pending_.reserve(1024);
    xrun_window_start_ = std::chrono::steady_clock::now();
    last_meter_ = xrun_window_start_;
    err.clear();
    return true;
}

void AudioHost::push(const Cmd& c) {
    if (cmds_.push(c)) return;
    // `handle` refuses a message while fewer than kAdmitHeadroom slots are
    // free (#597), so only a push of the host's own (`poll`, the IMU) gets
    // here, and only with the audio thread stopped. The payload never reached
    // the ring, so the audio thread cannot be reading it: free it here.
    std::fprintf(stderr, "arbutus-audio: command ring full, message dropped\n");
    switch (c.k) {
    case Ck::SetChain:  delete static_cast<Chain*>(c.p); break;
    case Ck::SetCtl:    delete static_cast<RackCtl*>(c.p); break;
    case Ck::SetModCtl: delete static_cast<ModCtl*>(c.p); break;
    case Ck::SetClips:  delete static_cast<ClipList*>(c.p); break;
    default: break;
    }
}

bool AudioHost::push_key_note(int note, bool on, float vel, int32_t probe, int64_t t_ns) {
    Cmd c{};
    c.k = Ck::Note;
    c.i0 = on ? 1 : 0;
    c.i1 = note;
    c.f0 = vel;
    if (probe >= 0) {
        c.i2 = 1;
        c.i3 = probe;
        c.pos = t_ns;
    }
    return keys_cmds_.push(c);
}

// DDR-063. The input's routing is the host's and the SOURCE knob is the box's,
// and they are the same fact: this is the one writer. `input_source` calls it,
// and so does a turn of the knob, which is why the ENGINE page needs no special
// case for this box.
void AudioHost::set_input_source(int src, const SlotState* skip) {
    input_source_ = std::clamp(src, 0, kNumSources - 1);
    Cmd c{};
    c.k = Ck::InputSource;
    c.i0 = input_source_;
    push(c);
    const float v = float(input_source_) / float(kNumSources - 1);
    for (int i = 0; i < kNumBuses; ++i) {
        SlotState& st = inst_[i].engine;       // the master bus never has one
        if (!st.loaded || st.id != kLiveInputId || &st == skip) continue;
        for (std::size_t k = 0; k < st.pids.size(); ++k) {
            if (st.pids[k] != kLiveInputSource) continue;
            if (st.params[kLiveInputSource] == v) break;
            st.params[kLiveInputSource] = v;
            Cmd p{};
            p.k = Ck::SetParam;
            p.i0 = i;
            p.i1 = 1;                          // the ENGINE slot
            p.i3 = int(k);
            p.f0 = v;
            push(p);
            break;
        }
    }
}

// A document that carries SOURCE wins over the host's current routing: loading
// a patch with RSMP selects resample, and a fresh box arrives at its own MIC
// default. The message is the writer while you are playing; the file is the
// writer at the moment it lands.
void AudioHost::set_motion(const float m[6]) {
    Cmd c{};
    c.k = Ck::Motion;
    for (int k = 0; k < 6; ++k) c.m[k] = m[k];
    push(c);
}

// A change is a state push, which is how the UI hears about a sensor that
// stopped answering. Idempotent on purpose: `main.cpp` calls this every turn
// of its poll loop, which is every 5 ms with a sensor fitted.
void AudioHost::set_motion_sensor(bool present) {
    if (present == motion_sensor_) return;
    motion_sensor_ = present;
    state_dirty_ = true;
}

void AudioHost::adopt_input_source(const SlotState& st) {
    if (!st.loaded || st.id != kLiveInputId) return;
    const auto it = st.params.find(kLiveInputSource);
    if (it == st.params.end()) return;
    set_input_source(int(std::lround(arb::clamp01(it->second) * float(kNumSources - 1))), &st);
}

void AudioHost::retire_all() {
    Retire r;
    while (retire_.pop(r)) {
        switch (r.k) {
        case Rk::Chain: delete static_cast<Chain*>(r.p); break;
        case Rk::Ctl:   delete static_cast<RackCtl*>(r.p); break;
        case Rk::Clips: delete static_cast<ClipList*>(r.p); break;
        case Rk::ModCtl: delete static_cast<ModCtl*>(r.p); break;
        }
    }
}

// ---------------------------------------------------------------------------
// building the graph (control thread)
// ---------------------------------------------------------------------------
Refusal AudioHost::build_slot(SlotState& st, InstancePtr& out) {
    if (Refusal r = make_instance(lib_, st.id, st.min_version, hb_, st.assets, out); !r.ok())
        return r;
    st.version = out->version;
    st.manifest = out->box.manifest;
    st.fmt_vt = out->box.vt;
    st.fmt_h = out->h;
    st.pids.clear();
    const arb_engine_desc& d = out->desc();
    std::map<std::string, float> merged;
    for (uint32_t i = 0; i < d.num_params; ++i) {
        const std::string id = arb::manifest::fixed(d.params[i].id, sizeof d.params[i].id);
        st.pids.push_back(id);
        auto it = st.params.find(id);
        const float v = it != st.params.end() ? it->second
                                              : arb::norm(d.params[i], d.params[i].def);
        out->set_authored(i, v);
        merged[id] = out->authored[i];       // §7.1: keyed by id, never by index
    }
    st.params = std::move(merged);
    st.loaded = true;
    return {};
}

Refusal AudioHost::apply(int inst, InstState p) {
    // §7.4: the refuse check is a set comparison before any loading starts.
    std::vector<std::string> req = p.rack.requires_ids();
    for (const std::string& id : p.mod.requires_ids()) req.push_back(id);   // modulation-spec §11
    if (p.engine.loaded) req.push_back(p.engine.id);
    if (p.seq.loaded) req.push_back(p.seq.id);
    if (Refusal r = check_requires(lib_, req, &p.rack.tabs); !r.ok()) return r;
    if (Refusal r = check_requires(lib_, {}, &p.mod.tabs); !r.ok()) return r;
    for (const SlotState* s : {&p.engine, &p.seq})
        if (s->loaded && !s->min_version.empty() && !lib_.find(s->id, s->min_version))
            return Refusal{"NEEDS VERSION", s->id + " ≥ " + s->min_version};

    auto chain = std::make_unique<Chain>();
    chain->alloc(opt_.max_block);

    if (p.engine.loaded) {
        InstancePtr i;
        // Assets ride on the slot now, so the ENGINE slot is no longer a special
        // case and a rack tab could take one too (§3.2 load_asset).
        if (Refusal r = build_slot(p.engine, i); !r.ok()) return r;
        // A slot decides which ports are wired (§5): a modulator has no audio
        // out and fills a MOD tab only (modulation-spec §3.1).
        if (i->desc().caps & ARB_CAP_MODULATOR)
            return Refusal{"ENGINE BROKEN", p.engine.id + " is a modulator; it fills a MOD tab, not ENGINE"};
        // Per-pad values (§15.3) live in the kit document, not in the file the
        // engine read at load_asset, so a rebuilt engine (a new FX tab, a
        // patch) gets every set_note_param back before it enters the graph.
        if (i->box.vt->set_note_param && p.kit.is_object() && p.kit.contains("pads") &&
            p.kit["pads"].is_object()) {
            const arb_engine_desc& d = i->desc();
            for (auto it = p.kit["pads"].begin(); it != p.kit["pads"].end(); ++it) {
                const int pad = std::atoi(it.key().c_str());
                if (pad < 0 || pad >= kKitPads || !it.value().is_object()) continue;
                const json pj = it.value().value("params", json::object());
                if (!pj.is_object()) continue;
                for (uint32_t k = 0; k < d.num_params; ++k) {
                    if (!d.params[k].per_note) continue;
                    const auto v = pj.find(p.engine.pids[k]);
                    if (v != pj.end() && v->is_number())
                        i->box.vt->set_note_param(i->h, uint8_t(kKitBaseNote + pad), k,
                                                  arb::clamp01(v->get<float>()));
                }
            }
        }
        chain->engine = std::move(i);
    }
    if (p.seq.loaded) {
        InstancePtr i;
        if (Refusal r = build_slot(p.seq, i); !r.ok()) return r;
        if (!(i->desc().caps & ARB_CAP_SEQUENCER))
            return Refusal{"ENGINE BROKEN", p.seq.id + " is not a sequencer engine (§3.1)"};
        chain->seq = std::move(i);
    }

    p.tabs.resize(p.rack.tabs.size());
    for (std::size_t t = 0; t < p.rack.tabs.size(); ++t) {
        SlotState& st = p.tabs[t];
        st.id = p.rack.tabs[t].engine;
        st.min_version = p.rack.tabs[t].min_version;
        st.params.clear();
        for (const auto& [id, v] : p.rack.tabs[t].params) st.params[id] = v;
        st.assets.clear();                     // FX tabs take content too (§3.2)
        for (const auto& [key, path] : p.rack.tabs[t].assets) st.assets.push_back(AssetRef{key, path});
        InstancePtr i;
        if (Refusal r = build_slot(st, i); !r.ok()) return r;
        if (i->desc().caps & ARB_CAP_MODULATOR)
            return Refusal{"ENGINE BROKEN", st.id + " is a modulator; it fills a MOD tab, not FX"};
        i->instance_id = p.rack.tabs[t].instance;
        chain->tabs.push_back(std::move(i));
    }

    // MOD rack (modulation-spec §2): built like FX tabs, refused the other way.
    p.mod_tabs.resize(p.mod.tabs.size());
    for (std::size_t t = 0; t < p.mod.tabs.size(); ++t) {
        SlotState& st = p.mod_tabs[t];
        st.id = p.mod.tabs[t].engine;
        st.min_version = p.mod.tabs[t].min_version;
        st.params.clear();
        for (const auto& [id, v] : p.mod.tabs[t].params) st.params[id] = v;
        st.assets.clear();
        for (const auto& [key, path] : p.mod.tabs[t].assets) st.assets.push_back(AssetRef{key, path});
        InstancePtr i;
        if (Refusal r = build_slot(st, i); !r.ok()) return r;
        if (!(i->desc().caps & ARB_CAP_MODULATOR))
            return Refusal{"ENGINE BROKEN", st.id + " is not a modulator; a MOD tab takes ARB_CAP_MODULATOR only"};
        i->instance_id = p.mod.tabs[t].instance;
        chain->mods.push_back(std::move(i));
    }
    for (std::size_t t = 0; t < p.mod.tabs.size(); ++t) {
        p.mod.tabs[t].params.clear();
        for (const auto& [id, v] : p.mod_tabs[t].params) p.mod.tabs[t].params.emplace_back(id, v);
    }
    // The rack document mirrors what the engines actually hold (§7.1).
    for (std::size_t t = 0; t < p.rack.tabs.size(); ++t) {
        p.rack.tabs[t].params.clear();
        for (const auto& [id, v] : p.tabs[t].params) p.rack.tabs[t].params.emplace_back(id, v);
    }

    std::vector<std::vector<std::string>> pids;
    for (const SlotState& t : p.tabs) pids.push_back(t.pids);
    RackCtlPtr ctl = build_ctl(p.rack, pids);

    normalise_kit(p);
    inst_[inst] = std::move(p);
    // DDR-063: the SOURCE knob that just arrived is the input routing, so a
    // patch or an engine load selects it. After the move, so the skip is the
    // slot that already holds the value and the mirror reaches the other bus.
    adopt_input_source(inst_[inst].engine);
    Cmd c1{};
    c1.k = Ck::SetChain;
    c1.i0 = inst;
    c1.p = chain.release();
    push(c1);
    Cmd c2{};
    c2.k = Ck::SetCtl;
    c2.i0 = inst;
    c2.p = ctl.release();
    push(c2);
    for (int m = 0; m < 8; ++m) {
        Cmd c3{};
        c3.k = Ck::SetMacro;
        c3.i0 = inst;
        c3.i1 = m;
        c3.f0 = inst_[inst].macro[std::size_t(m)];
        push(c3);
    }
    push_mod_ctl(inst);
    state_dirty_ = true;
    return {};
}

ModInputs AudioHost::mod_inputs(const InstState& s) const {
    ModInputs in;
    if (s.engine.loaded) {
        in.engine_pids = s.engine.pids;
        for (const json& pj : s.engine.manifest.value("params", json::array()))
            in.engine_per_voice.push_back(pj.value("per_voice", false));
    }
    in.rack = &s.rack;
    for (const SlotState& t : s.tabs) in.fx_pids.push_back(t.pids);
    for (const SlotState& t : s.mod_tabs) {
        std::vector<std::pair<std::string, bool>> outs;
        for (const json& oj : t.manifest.value("outputs", json::array()))
            outs.emplace_back(oj.value("id", std::string{}), oj.value("bipolar", false));
        in.outputs.push_back(std::move(outs));
    }
    return in;
}

// The MOD rack's routes resolve against everything else loaded, so any change
// to the chain or the document hands the audio thread a fresh ModCtl.
void AudioHost::push_mod_ctl(int inst) {
    Cmd c{};
    c.k = Ck::SetModCtl;
    c.i0 = inst;
    c.p = build_mod_ctl(inst_[inst].mod, mod_inputs(inst_[inst]), ++mod_gen_[inst]).release();
    push(c);
    state_dirty_ = true;
}

// ---------------------------------------------------------------------------
// the audio thread
// ---------------------------------------------------------------------------
void AudioHost::apply_cmd(const Cmd& c) {
    switch (c.k) {
    case Ck::SetChain: {
        Voice& v = voice_[c.i0];
        if (v.old) { retire_.push(Retire{Rk::Chain, v.old}); v.old = nullptr; }
        v.old = v.cur;
        v.cur = static_cast<Chain*>(c.p);
        v.fade = v.old ? kFadeFrames : 0;       // §8: swap between blocks, short fade
        if (!v.old && v.cur) v.fade = 0;
        break;
    }
    case Ck::SetCtl: {
        Voice& v = voice_[c.i0];
        if (v.ctl) retire_.push(Retire{Rk::Ctl, v.ctl});
        v.ctl = static_cast<RackCtl*>(c.p);
        break;
    }
    case Ck::SetModCtl: {
        Voice& v = voice_[c.i0];
        if (v.mctl) retire_.push(Retire{Rk::ModCtl, v.mctl});
        v.mctl = static_cast<ModCtl*>(c.p);
        break;
    }
    case Ck::HostParam:
        voice_[c.i0].mod.host_knob[std::clamp(c.i1, 0, 2)] = c.f0;
        break;
    case Ck::Bend:
        voice_[c.i0].bend_target = c.f0;
        break;
    case Ck::SetClips: {
        ClipList* old = tape_.swap_clips(c.i0, static_cast<ClipList*>(c.p));
        if (old) retire_.push(Retire{Rk::Clips, old});
        break;
    }
    case Ck::SetParam: {
        Voice& v = voice_[c.i0];
        auto to = [&](Chain* ch) {
            if (!ch) return;
            Instance* in = nullptr;
            if (c.i1 == 0) in = ch->seq.get();
            else if (c.i1 == 1) in = ch->engine.get();
            else if (c.i1 == 3) {
                if (c.i2 >= 0 && c.i2 < int(ch->mods.size())) in = ch->mods[std::size_t(c.i2)].get();
            }
            else if (c.i2 >= 0 && c.i2 < int(ch->tabs.size())) in = ch->tabs[std::size_t(c.i2)].get();
            if (in && c.i3 >= 0 && c.i3 < int(in->desc().num_params))
                in->set_authored(uint32_t(c.i3), c.f0);
        };
        to(v.cur);
        to(v.old);
        break;
    }
    case Ck::SetNoteParam: {
        // Between blocks, like SetParam; both chains during a crossfade.
        for (Chain* ch : {voice_[c.i0].cur, voice_[c.i0].old}) {
            if (!ch || !ch->engine || !ch->engine->box.vt->set_note_param) continue;
            if (c.i3 >= 0 && c.i3 < int(ch->engine->desc().num_params))
                ch->engine->box.vt->set_note_param(ch->engine->h, uint8_t(c.i2), uint32_t(c.i3), c.f0);
        }
        break;
    }
    case Ck::SetMacro: voice_[c.i0].macro[std::size_t(c.i1 & 7)] = c.f0; break;
    case Ck::Note: {
        if (pending_.size() >= pending_.capacity()) break;
        arb_event e{};
        e.type = uint16_t(c.i0 ? ARB_EV_NOTE_ON : ARB_EV_NOTE_OFF);
        e.a = uint16_t(c.i1);
        e.b = c.f0;
        pending_.push_back(e);
        // Only once the note is really in this block; a full ring drops the
        // report, never the note.
        if (c.i2) probes_.push(Probe{c.i3, c.pos, a_block_t0_ns_, a_block_});
        break;
    }
    case Ck::Softkey: {
        if (pending_.size() >= pending_.capacity()) break;
        arb_event e{};
        e.type = ARB_EV_SOFTKEY;
        e.a = uint16_t(c.i1);
        e.b = c.i2 ? 1.0f : 0.0f;
        e.flags = c.i3 ? 1u : 0u;
        pending_.push_back(e);
        break;
    }
    case Ck::AllNotesOff: {
        for (Voice& v : voice_)
            for (Chain* ch : {v.cur, v.old}) {
                if (!ch) continue;
                if (ch->engine) ch->engine->box.vt->reset(ch->engine->h);
                if (ch->seq) ch->seq->box.vt->reset(ch->seq->h);
                // A modulator that counts held notes (arbutus/envelope) would
                // otherwise hold through a panic that silenced every voice.
                for (InstancePtr& t : ch->mods) t->box.vt->reset(t->h);
            }
        break;
    }
    case Ck::Transport: {
        arb_event e{};
        e.type = ARB_EV_TRANSPORT;
        if (c.i0 == 2) {                                   // locate
            a_song_frame_ = c.pos;
            a_tape_pos_ = double(std::clamp<int64_t>(c.pos, 0, std::max<int64_t>(0, tape_.capacity() - 1)));
            e.a = 2;
        } else if (c.i0 == 1) {                            // start
            a_playing_ = true;
            a_scrub_left_ = 0;                             // PLAY cancels a scrub grain
            e.a = 1;
        } else {                                           // stop
            a_playing_ = false;
            e.a = 0;
            if (a_recording_) end_take();
        }
        e.b = float(double(a_song_frame_) * a_tempo_ / (60.0 * double(hb_.sample_rate)));
        if (pending_.size() < pending_.capacity()) pending_.push_back(e);
        break;
    }
    case Ck::Env:
        if (c.i1 == kEnvSlots) a_env_mode_[c.i0] = uint8_t(c.i2);
        else if (c.i1 >= 0 && c.i1 < kEnvSlots) a_env_[c.i0][c.i1] = c.f0;
        break;
    case Ck::Tempo:
        a_tempo_ = c.f0;
        a_link_ = c.f1;
        break;
    case Ck::Active:
        // `self` is the instrument's own bus: the other one's last block is
        // not it, so a switch starts from silence.
        if (a_active_ != c.i0) std::fill(self_ring_.begin(), self_ring_.end(), 0.0f);
        a_active_ = c.i0;
        break;
    case Ck::Motion:
        for (int k = 0; k < 6; ++k) hb_.motion[k] = c.m[k];
        break;
    case Ck::Arm: a_armed_ = c.i0; break;
    case Ck::InputSource: a_input_src_ = c.i0; break;
    case Ck::Record:
        if (c.i0) {
            if (a_recording_) end_take();
            a_rec_track_ = c.i1;
            a_armed_ = c.i1;
            a_rec_start_ = int64_t(std::floor(a_tape_pos_));
            // Append-only (tape.hpp): the take goes at the medium's head, not
            // at its timeline position, so nothing a clip reads is overwritten.
            a_rec_medium_ = tape_.head[std::clamp(c.i1, 0, Tape::kTracks - 1)].load(std::memory_order_relaxed);
            a_rec_len_ = 0;
            a_rec_frac_ = 0.0;
            a_recording_ = true;
            // The range the take starts in may already hold an earlier take's
            // tail; it is committed whole when it fills, which is what the
            // slot holds anyway.
            a_commit_range_[std::clamp(c.i1, 0, Tape::kTracks - 1)] =
                Tape::range_of(a_rec_medium_);
        } else if (a_recording_) {
            end_take();
        }
        break;
    case Ck::Mixer:
        if (c.i0 >= 0 && c.i0 < Tape::kTracks) a_track_gain_[c.i0] = c.f0;
        else if (c.i0 >= Tape::kTracks && c.i0 < 2 * Tape::kTracks)
            a_track_pan_[c.i0 - Tape::kTracks] = c.f0;
        else if (c.i0 == kMixMaster) a_master_ = c.f0;
        else if (c.i0 == kMixLimiter) a_limiter_ = c.f0 > 0.5f;
        else if (c.i0 == kMixComp) a_comp_ = c.f0;
        else if (c.i0 == kMixCompRel) a_comp_rel_ = c.f0;
        else if (c.i0 == kMixCompTimes) a_comp_labelled_ = c.f0 > 0.5f;
        else if (c.i0 == kMixDrive) { a_drive_ = c.f0; if (a_drive_ <= 0.0f) a_drive_st_.reset(); }
        else if (c.i0 == kMixDriveTone) a_drive_tone_ = c.f0;
        else if (c.i0 == kMixBal) a_bal_ = c.f0;
        else if (c.i0 == kMixMute && c.i1 >= 0 && c.i1 < Tape::kTracks) a_mute_[c.i1] = c.f0 > 0.5f;
        else if (c.i0 == kMixSolo && c.i1 >= 0 && c.i1 < Tape::kTracks) a_solo_[c.i1] = c.f0 > 0.5f;
        break;
    case Ck::Eq: {
        // The knobs arrive in dB and Hz; the biquads are recomputed here rather
        // than on the control thread because a Cmd carries floats, not five
        // filters. Trigonometry, no allocation -- the same deal tape_filter has.
        if (c.i0 < 0 || c.i0 >= Tape::kTracks) break;
        EqSettings s;
        s.low = c.m[0];
        s.mid = c.m[1];
        s.high = c.m[2];
        s.mid_f = c.m[3];
        s.mid_q = c.m[4];
        s.hp = c.m[5];
        s.lp = c.f0;
        TrackEq& e = a_eq_[c.i0];
        const bool was = e.active();
        e.set(s, double(opt_.sr));
        // A filter that was off has stale state; a flat EQ is a bypass, so its
        // state must not leak into the next time it is switched on.
        if (!was || !e.active()) e.reset();
        break;
    }
    case Ck::Metronome:
        a_click_level_ = c.f0;
        a_click_pitch_ = c.f1;
        break;
    case Ck::Loop:
        a_loop_on_ = c.i0 != 0;
        a_loop_in_ = c.pos;
        a_loop_out_ = int64_t(c.i1);
        break;
    case Ck::TapeSpeed: {
        a_speed_ = c.f0;
        const bool rev = c.i0 != 0;
        if (rev && !a_rev_) {
            a_rev_anchor_ = a_tape_pos_;
            a_rev_elapsed_ = 0.0;
        } else if (!rev && a_rev_) {
            // Letting go of REV lands where the tape would have been had it
            // kept rolling forward (the emulator's hold_rev).
            Travel tr;
            tr.pos = a_rev_anchor_ + a_rev_elapsed_;
            tr.step = 0.0;
            tr.loop = a_loop_on_;
            tr.loop_in = a_loop_in_;
            tr.loop_out = a_loop_out_;
            tr.capacity = tape_.capacity();
            a_tape_pos_ = tr.at(0);
        }
        a_rev_ = rev;
        break;
    }
    case Ck::TapeFilter:
        a_tf_on_ = c.i0 != 0;
        a_tf_cutoff_ = c.f0;
        a_tf_hp_ = c.i1 != 0;
        if (!a_tf_on_) a_tf_y_[0] = a_tf_y_[1] = 0.0f;
        break;
    case Ck::Scrub:
        if (a_playing_) break;
        // A grain still running near the new target keeps reading where it
        // is; the UI decides that (tape.py scrub_preview) and says `reseed`.
        if (c.i1) a_scrub_pos_ = double(c.pos);
        a_scrub_total_ = std::max<int64_t>(1, c.i0);
        a_scrub_left_ = a_scrub_total_;
        break;
    case Ck::Monitor:
        a_mon_on_ = c.i0 != 0;
        a_mon_gain_ = c.f0;
        break;
    case Ck::ResetEngines:
        for (Voice& v : voice_)
            for (Chain* ch : {v.cur, v.old}) {
                if (!ch) continue;
                if (ch->engine) ch->engine->box.vt->reset(ch->engine->h);
                if (ch->seq) ch->seq->box.vt->reset(ch->seq->h);
                for (InstancePtr& t : ch->tabs) t->box.vt->reset(t->h);
                for (InstancePtr& t : ch->mods) t->box.vt->reset(t->h);
            }
        break;
    }
}

void AudioHost::end_open_take() {
    if (a_recording_) end_take();
}

void AudioHost::end_take() {
    a_recording_ = false;
    const int t = std::clamp(a_rec_track_, 0, Tape::kTracks - 1);
    const int64_t end = a_rec_medium_ + a_rec_len_;
    // No range is the take's any more, so the evictor may have them all back
    // once the I/O thread has them on the card.
    tape_.set_write_range(t, -1);
    tape_.head[t].store(end, std::memory_order_relaxed);
    // Whatever whole ranges are still owed, then the partial last one with the
    // header written exact (no lag): a file the instrument has stopped writing
    // is not a live header (`tape_io.hpp`, § 3.5).
    queue_ranges(t, end);
    if (tape_io_.on()) {
        RangeCommit c;
        c.track = t;
        c.flush = 1;
        c.range = Tape::range_of(end);
        c.frames = end - c.range * Tape::kRangeFrames;
        tape_io_.enqueue(c);
    }
    take_start_.store(a_rec_start_, std::memory_order_relaxed);
    take_src_.store(a_rec_medium_, std::memory_order_relaxed);
    take_track_.store(a_rec_track_, std::memory_order_relaxed);
    take_end_.store(a_rec_start_ + a_rec_len_, std::memory_order_release);
}

void AudioHost::queue_ranges(int t, int64_t written_to) {
    if (!tape_io_.on() || t < 0 || t >= Tape::kTracks) return;
    // Ranges [0, complete) are full; the one `written_to` sits in is not.
    const int64_t complete = Tape::range_of(written_to);
    while (a_commit_range_[t] < complete) {
        RangeCommit c;
        c.track = t;
        c.range = a_commit_range_[t];
        c.frames = Tape::kRangeFrames;
        // A full ring is counted and retried next block rather than waited on:
        // the audio thread does not block on storage, ever (§ 3.9).
        if (!tape_io_.enqueue(c)) break;
        ++a_commit_range_[t];
    }
}

void AudioHost::build_events(uint32_t n) {
    ev_musical_.clear();
    ev_clock_.clear();

    // Everything the UI sent since the last block lands at frame 0. Sequencer
    // engines emit sample-accurately inside the block; a key press cannot,
    // because the host only learns about it between blocks (PROTOCOL.md).
    for (const arb_event& e : pending_) {
        arb_event c = e;
        c.frame = 0;
        if (c.type == ARB_EV_TRANSPORT) ev_clock_.push_back(c);
        else ev_musical_.push_back(c);
    }
    pending_.clear();

    if (a_playing_ && a_tempo_ > 0.0 && hb_.ppqn) {
        const double fpt = double(hb_.sample_rate) * 60.0 / (a_tempo_ * double(hb_.ppqn));
        int64_t k = int64_t(std::ceil(double(a_song_frame_) / fpt));
        while (ev_clock_.size() < ev_clock_.capacity()) {
            const double f = double(k) * fpt;
            if (f >= double(a_song_frame_ + int64_t(n))) break;
            if (f >= double(a_song_frame_)) {
                arb_event e{};
                e.frame = uint32_t(f - double(a_song_frame_));
                e.type = ARB_EV_CLOCK;
                e.a = uint16_t(((k % hb_.ppqn) + hb_.ppqn) % hb_.ppqn);
                ev_clock_.push_back(e);
            }
            ++k;
        }
        // Not std::stable_sort: it allocates its merge buffer on the audio
        // thread (events.hpp). The list is already in order -- transport at
        // frame 0, then ticks ascending -- so this is one pass.
        sort_events_by_frame(ev_clock_.data(), ev_clock_.data() + ev_clock_.size());
    }

    ev_all_.clear();
    ev_engine_.clear();
    for (const arb_event& e : ev_musical_) {
        ev_all_.push_back(e);
        if (e.type != ARB_EV_SOFTKEY) ev_engine_.push_back(e);   // §3.6
    }
    for (const arb_event& e : ev_clock_) { ev_all_.push_back(e); ev_engine_.push_back(e); }
}

void AudioHost::render_instrument(int inst, uint32_t n, float* l, float* r) {
    Voice& v = voice_[inst];
    if (!v.cur) {
        std::fill_n(l, n, 0.0f);
        std::fill_n(r, n, 0.0f);
        return;
    }

    ChainInput ci;
    ci.ev = ev_all_.data();
    ci.n_ev = uint32_t(ev_all_.size());
    ci.ev_engine = ev_engine_.data();
    ci.n_engine = uint32_t(ev_engine_.size());
    ci.ev_clock = ev_clock_.data();
    ci.n_clock = uint32_t(ev_clock_.size());
    for (int t = 0; t < Tape::kTracks; ++t) {
        ci.sides.tape[t][0] = buf(kBufTape0 + t, 0);
        ci.sides.tape[t][1] = buf(kBufTape0 + t, 1);
    }
    // modulation-spec §4: `input` is "whatever INPUT is set to", so it is the
    // selected source and not the raw jack -- one selector, one answer.
    ci.sides.input[0] = buf(kBufInputSrc, 0);
    ci.sides.input[1] = buf(kBufInputSrc, 1);
    ci.sides.self[0] = buf(kBufSelfSide, 0);
    ci.sides.self[1] = buf(kBufSelfSide, 1);
    ci.sides.master[0] = buf(kBufMasterSide, 0);
    ci.sides.master[1] = buf(kBufMasterSide, 1);
    ci.engine_side[0] = buf(kBufInputSrc, 0);       // DDR-063, for a box that asked
    ci.engine_side[1] = buf(kBufInputSrc, 1);

    float* out[2] = {l, r};
    render_chain(*v.cur, v.ctl, v.macro.data(), v.mctl, &v.mod, true, hb_, ci, out, n);

    if (v.old && v.fade) {
        float* old_out[2] = {buf(kBufInstOld, 0), buf(kBufInstOld, 1)};
        render_chain(*v.old, v.ctl, v.macro.data(), v.mctl, &v.mod, false, hb_, ci, old_out, n);
        const uint32_t f = std::min(n, v.fade);
        const uint32_t done = kFadeFrames - v.fade;
        for (uint32_t k = 0; k < f; ++k) {
            const float t = float(done + k + 1) / float(kFadeFrames);
            l[k] = l[k] * t + old_out[0][k] * (1.0f - t);
            r[k] = r[k] * t + old_out[1][k] * (1.0f - t);
        }
        v.fade -= f;
        if (!v.fade) {
            retire_.push(Retire{Rk::Chain, v.old});
            v.old = nullptr;
        }
    } else if (v.old) {
        retire_.push(Retire{Rk::Chain, v.old});
        v.old = nullptr;
    }

    // host.level and host.pan (modulation-spec §5), on the instrument bus after
    // any crossfade. Level is a linear gain; pan is a balance, unity at centre
    // and cos-shaped toward the far side. At the default knobs with no route
    // both gains are exactly 1 and the bus is not touched, so the host still
    // does not colour the sound. [default chosen here: the spec names the
    // params only -- audio/CLAUDE.md]
    const float pn = std::clamp(2.0f * v.mod.pan - 1.0f, -1.0f, 1.0f);
    const float lv = arb::clamp01(v.mod.level);
    constexpr float kHalfPi = 1.5707963267948966f;
    const float gl = lv * (pn > 0.0f ? std::cos(pn * kHalfPi) : 1.0f);
    const float gr = lv * (pn < 0.0f ? std::cos(-pn * kHalfPi) : 1.0f);
    if (gl != 1.0f || gr != 1.0f || v.bus_gain[0] != 1.0f || v.bus_gain[1] != 1.0f) {
        // A block-rate step on a gain is a zipper; ramp across the block.
        const float g0l = v.bus_gain[0], g0r = v.bus_gain[1];
        for (uint32_t k = 0; k < n; ++k) {
            const float t = float(k + 1) / float(n);
            l[k] *= g0l + (gl - g0l) * t;
            r[k] *= g0r + (gr - g0r) * t;
        }
    }
    v.bus_gain[0] = gl;
    v.bus_gain[1] = gr;

    for (int k = 0; k < kMaxRoutes; ++k)
        mod_live_[inst][k].store(v.mod.live[k], std::memory_order_relaxed);
    mod_live_mask_[inst].store(v.mod.live_mask, std::memory_order_relaxed);
    mod_live_gen_[inst].store(v.mod.gen, std::memory_order_release);
}

void AudioHost::render_block(const float* in_l, const float* in_r, float* out_l, float* out_r,
                             uint32_t n) {
    const auto t0 = std::chrono::steady_clock::now();
    a_block_t0_ns_ = std::chrono::duration_cast<std::chrono::nanoseconds>(
                         t0.time_since_epoch()).count();

    Cmd c;
    while (cmds_.pop(c)) apply_cmd(c);
    // The key thread's notes after the control thread's (§ 1.3.4 part 2): a
    // `panic` in the same block cannot then silence a key pressed after it.
    while (keys_cmds_.pop(c)) apply_cmd(c);

    if (n > opt_.max_block) n = opt_.max_block;
    if (!n) return;

    float* il = buf(kBufInput, 0);
    float* ir = buf(kBufInput, 1);
    if (in_l && in_r) { std::copy_n(in_l, n, il); std::copy_n(in_r, n, ir); }
    else { std::fill_n(il, n, 0.0f); std::fill_n(ir, n, 0.0f); }

    hb_.tempo_bpm = a_tempo_;
    hb_.playing = a_playing_ ? 1 : 0;
    hb_.song_frame = a_song_frame_;
    hb_.env_a = a_env_[a_active_][0];
    hb_.env_d = a_env_[a_active_][1];
    hb_.env_s = a_env_[a_active_][2];
    hb_.env_r = a_env_[a_active_][3];
    hb_.env_mode = a_env_mode_[a_active_];
    hb_.env_glide = a_env_[a_active_][4];
    hb_.env_detune = a_env_[a_active_][5];
    hb_.bend_range = a_env_[a_active_][6];

    // Bend (DDR-052): the position moves toward the `bend` target at 0 -> full
    // bend in GLIDE seconds, so a wheel sweep follows with bounded lag and a
    // release returns the same way; GLIDE 0 snaps. The GLIDE knob, not an
    // env.glide route: the route is applied inside the chain, after this.
    // PolyHost ramps pitch_st across the block, so a snap is still no click.
    // [default chosen here: DDR-052 says "ramped over GLIDE" -- audio/CLAUDE.md]
    {
        Voice& av = voice_[a_active_];
        const float glide = hb_.env_glide;
        if (glide <= 0.0f || hb_.sample_rate == 0) {
            av.bend_pos = av.bend_target;
        } else {
            const float step = float(n) / (glide * float(hb_.sample_rate));
            const float d = av.bend_target - av.bend_pos;
            av.bend_pos = std::abs(d) <= step ? av.bend_target : av.bend_pos + (d > 0.0f ? step : -step);
        }
        hb_.pitch_st = av.bend_pos * hb_.bend_range;
    }

    build_events(n);

    // The tape. Rolling: along the playhead at SPEED, backwards under REV,
    // wrapped by the loop; a take is always captured at 1x (the emulator's
    // effective_speed). Stopped: silence, or the scrub grain.
    Travel tr;
    tr.capacity = tape_.capacity();
    tr.loop = a_loop_on_;
    tr.loop_in = a_loop_in_;
    tr.loop_out = a_loop_out_;
    uint32_t grain = 0;
    if (a_playing_) {
        tr.pos = a_tape_pos_;
        // LINK (DDR-059) scales the tape with the tempo; SPEED and REV are
        // performance on top of it, and a take rides the link factor alone so
        // an overdub lands on the grid it was played to.
        tr.step = a_recording_ ? double(a_link_)
                               : double(a_link_) * double(a_speed_) * (a_rev_ ? -1.0 : 1.0);
        for (int t = 0; t < Tape::kTracks; ++t)
            tape_.render_track(t, tr, n, buf(kBufTape0 + t, 0), buf(kBufTape0 + t, 1));
        // `tape_late` (C1 step 7, design § 3.7): **one count per block** in
        // which any track wanted a range the card had not delivered, plus the
        // mask of which ones — `xrun_count_`'s shape, because the two are one
        // alarm rule. The mask is the audio thread's own and nothing else
        // writes it, so read-then-clear cannot lose a bit.
        if (const uint32_t lm = tape_.late_mask()) {
            tape_.clear_late_mask();
            a_tape_late_.fetch_add(1, std::memory_order_relaxed);
            a_tape_late_tracks_.fetch_or(lm, std::memory_order_relaxed);
        }
    } else {
        grain = uint32_t(std::min<int64_t>(n, a_scrub_left_));
        // Stopped, the playhead the I/O thread plans around is the transport's
        // own, not the scrub cursor's: a scrub crosses minutes of tape and a
        // horizon that chased it would evict everything PLAY is about to want.
        // The grain itself is served by the demand ask in `render_track`
        // (design § 3.6: a grain over an absent range is silent and requests
        // the fetch, so the next grain there sounds).
        tr.pos = a_scrub_pos_;
        tr.step = 1.0;
        tr.loop = false;
        for (int t = 0; t < Tape::kTracks; ++t) {
            float* tl = buf(kBufTape0 + t, 0);
            float* trr = buf(kBufTape0 + t, 1);
            if (!grain) { std::fill_n(tl, n, 0.0f); std::fill_n(trr, n, 0.0f); continue; }
            tape_.render_track(t, tr, n, tl, trr);
            std::fill(tl + grain, tl + n, 0.0f);
            std::fill(trr + grain, trr + n, 0.0f);
        }
        if (grain) {
            // The emulator's grain envelope: a 64-frame ramp in when (re)armed
            // and out as it runs dry, so a slow turn never clicks.
            const uint32_t fade = std::min(kScrubFade, grain);
            const bool fresh = a_scrub_left_ >= a_scrub_total_;
            const bool dry = a_scrub_left_ - int64_t(grain) <= 0;
            for (uint32_t k = 0; k < grain; ++k) {
                float env = 1.0f;
                const float ramp_den = fade > 1 ? float(fade - 1) : 1.0f;
                if (fresh && k < fade) env = float(k) / ramp_den;
                if (dry && k >= grain - fade) env = float(grain - 1 - k) / ramp_den;
                for (int t = 0; t < Tape::kTracks; ++t) {
                    buf(kBufTape0 + t, 0)[k] *= env;
                    buf(kBufTape0 + t, 1)[k] *= env;
                }
            }
            a_scrub_pos_ = tr.at(grain - 1) + 1.0;
            a_scrub_left_ -= int64_t(grain);
        }
        // Elfy, design § 7 question 2: a scrub over a piece the card has not
        // delivered is **silent and uncounted**. The ask `render_track` made
        // stands — the next grain over that spot sounds — but dragging a
        // cursor across a tape is not the instrument being late.
        tape_.clear_late_mask();
    }

    // `self` and `master` side[] (modulation-spec §4). Each is a delay line of
    // exactly max_block frames: read the n oldest frames now, overwrite the
    // same n after the block. At the fixed device block that is precisely the
    // previous block; a short block reads a longer-ago stretch but the delay
    // never changes, so a ragged render is still the same signal. [default
    // chosen here: the spec says "one block late" and nothing about ragged
    // blocks -- audio/CLAUDE.md]
    const uint32_t mb = opt_.max_block;
    // The n frames from ring_pos_ on, wrapped: two straight spans, not a `%`
    // per sample (#984). n <= mb, so the second span starts at 0 and is short.
    const uint32_t span1 = std::min(n, mb - ring_pos_);
    auto ring_read = [&](const std::vector<float>& ring, int dst) {
        for (int ch = 0; ch < 2; ++ch) {
            const float* src = ring.data() + std::size_t(ch) * mb;
            float* d = buf(dst, ch);
            std::copy_n(src + ring_pos_, span1, d);
            std::copy_n(src, n - span1, d + span1);
        }
    };
    ring_read(self_ring_, kBufSelfSide);
    ring_read(master_ring_, kBufMasterSide);
    ring_read(resample_ring_, kBufResample);

    // SOURCE (DDR-063). mic / line / usb are the one hardware stream the host
    // opened: on a CM4 the codec chooses which jack feeds it, and this process
    // is handed the result, so all three read the same buffer here. `resample`
    // is the master bus **minus the armed track**, one block late for the same
    // reason `master` is (modulation-spec §4) -- the engine renders before the
    // mix it would be tapping.
    for (int ch = 0; ch < 2; ++ch) {
        const float* src = a_input_src_ == kSrcResample ? buf(kBufResample, ch) : buf(kBufInput, ch);
        std::copy_n(src, n, buf(kBufInputSrc, ch));
    }

    float* ibl = buf(kBufInstBus, 0);
    float* ibr = buf(kBufInstBus, 1);
    render_instrument(a_active_, n, ibl, ibr);

    // Record tap: the active instrument bus, pre-mixer (DDR-034 / §5), **plus
    // what that track already plays** -- overdub is the only record mode
    // (DDR-056) -- at the take's place in the medium. A full medium ends the
    // take's growth, not the transport.
    if (a_recording_ && a_rec_track_ >= 0 && a_rec_track_ < Tape::kTracks) {
        float* kl = buf(kBufTake, 0);
        float* kr = buf(kBufTake, 1);
        const float* pl = buf(kBufTape0 + a_rec_track_, 0);
        const float* pr = buf(kBufTape0 + a_rec_track_, 1);
        for (uint32_t k = 0; k < n; ++k) {
            kl[k] = ibl[k] + pl[k];
            kr[k] = ibr[k] + pr[k];
        }
        const int64_t room = std::max<int64_t>(0, tape_.capacity() - (a_rec_medium_ + a_rec_len_));
        // § 3.7's third `TAPE FULL`: the medium has no more room. Before step
        // 7 the take simply stopped growing and said nothing, which leaves a
        // take that looks like it is still recording and is not. `end_take`
        // clears `a_recording_` and queues the tail itself, so this fires once
        // and the rest of the tap is skipped rather than queueing it twice.
        if (room == 0) {
            a_tape_format_.fetch_add(1, std::memory_order_relaxed);
            end_take();
        } else {
            // The medium runs at the *tape's* rate, not the output's: under LINK
            // (DDR-059) the playhead moves `a_link_` frames per rendered frame, so
            // the take is resampled by the same factor on the way in and plays back
            // as it was performed. At 1x -- every session with LINK off or the BPM
            // unmoved -- this is the straight copy it always was, sample for sample.
            const double rate = double(a_link_);
            bool stalled = false;                   // the pool had no slot for the next range
            if (rate == 1.0) {
                const uint32_t w = uint32_t(std::min<int64_t>(n, room));
                if (w) {
                    const uint32_t got = tape_.write(a_rec_track_, a_rec_medium_ + a_rec_len_, kl, kr, w);
                    a_rec_len_ += int64_t(got);
                    stalled = got < w;
                }
            } else {
                double pos = a_rec_frac_;
                int64_t wrote = 0;
                while (pos < double(n) && wrote < room) {
                    const uint32_t i0 = uint32_t(pos);
                    const uint32_t i1 = std::min<uint32_t>(i0 + 1, n - 1);
                    const float fr = float(pos - double(i0));
                    const float l = kl[i0] * (1.0f - fr) + kl[i1] * fr;
                    const float r = kr[i0] * (1.0f - fr) + kr[i1] * fr;
                    if (!tape_.write(a_rec_track_, a_rec_medium_ + a_rec_len_ + wrote, &l, &r, 1)) {
                        stalled = true;
                        break;
                    }
                    ++wrote;
                    pos += 1.0 / rate;
                }
                a_rec_len_ += wrote;
                a_rec_frac_ = std::max(0.0, pos - double(n));
            }
            // Write-behind (C1 step 2a): every range the take has now filled goes
            // to the I/O thread as one POD on a ring. Nothing here touches storage.
            queue_ranges(a_rec_track_, a_rec_medium_ + a_rec_len_);
            // The pool has become all write queue — a card that stopped answering.
            // The take ends where it stopped and the transport keeps rolling
            // (`tape_full {reason:"stall"}`): a frozen screen is not acceptable,
            // more latency is (Elfy, #74).
            if (stalled) {
                a_tape_stall_.fetch_add(1, std::memory_order_relaxed);
                end_take();
            }
        }
    }

    float* ml = buf(kBufMaster, 0);
    float* mr = buf(kBufMaster, 1);
    float* xl = buf(kBufTapeMix, 0);
    float* xr = buf(kBufTapeMix, 1);
    // What `resample` takes back out of the master (DDR-063): the armed track's
    // own contribution, the instrument bus, the click and the monitor. The
    // instrument bus is in there because that is where `arbutus/live-input`'s
    // own output lands -- without removing it, SOURCE=resample is a unity-gain
    // feedback loop rather than a bounce. The click and the monitor are
    // listening aids and must not be baked.
    float* al = buf(kBufResampleCut, 0);
    float* ar = buf(kBufResampleCut, 1);
    std::fill_n(xl, n, 0.0f);
    std::fill_n(xr, n, 0.0f);
    std::fill_n(al, n, 0.0f);
    std::fill_n(ar, n, 0.0f);
    // Stopped with no scrub grain, every track buffer is the zeros filled
    // above: a track with no EQ to ring out adds 0 to both mixes and a peak of
    // 0, which leaves its meter as it was, so it is skipped (#984: the idle
    // audio thread). An EQ'd track still runs, its filters' tail is heard.
    const bool tape_silent = !a_playing_ && !grain;
    for (int t = 0; t < Tape::kTracks; ++t) {
        if (tape_silent && !a_eq_[t].active()) continue;
        const float p = std::clamp(a_track_pan_[t], -1.0f, 1.0f);
        const float ang = (p + 1.0f) * 0.25f * 3.14159265358979f;   // equal power, -3 dB centre
        // DDR-061: solo wins over mute, and the gate is on the *mix* only --
        // the sidechain, the record tap's own playback and `clip_to_asset` on
        // one track all read the track itself.
        const float gate = mix_gate(a_mute_, a_solo_, Tape::kTracks, t);
        const float gl = std::cos(ang) * a_track_gain_[t] * gate;
        const float gr = std::sin(ang) * a_track_gain_[t] * gate;
        const float* tl = buf(kBufTape0 + t, 0);
        const float* trk = buf(kBufTape0 + t, 1);
        // Per-track EQ (DDR-062) sits between the track and the mixer (§15.5),
        // so it colours what is heard and never what is recorded: the medium,
        // the tap and `tape:n` are all upstream of this copy.
        if (a_eq_[t].active()) {
            float* el = buf(kBufTrackEq, 0);
            float* er = buf(kBufTrackEq, 1);
            std::copy_n(tl, n, el);
            std::copy_n(trk, n, er);
            a_eq_[t].process(0, el, n);
            a_eq_[t].process(1, er, n);
            tl = el;
            trk = er;
        }
        float peak = 0.0f;
        const bool armed = (t == a_armed_);
        for (uint32_t k = 0; k < n; ++k) {
            xl[k] += tl[k] * gl;
            xr[k] += trk[k] * gr;
            if (armed) { al[k] += tl[k] * gl; ar[k] += trk[k] * gr; }
            peak = std::max(peak, std::max(std::abs(tl[k] * a_track_gain_[t]), std::abs(trk[k] * a_track_gain_[t])));
        }
        // Held to the next `meters` push, which takes it (poll).
        meter_track_[t].store(std::max(meter_track_[t].load(std::memory_order_relaxed), peak),
                              std::memory_order_relaxed);
    }

    // The FILTER hold (DDR-026): a one-pole over the tape mix only, and only
    // while held -- tape.py's one_pole, coefficient per block, state kept
    // across blocks and cleared on release.
    if (a_tf_on_) {
        const double w = 2.0 * 3.14159265358979323846 * std::max(1.0, double(a_tf_cutoff_)) / double(opt_.sr);
        const float a = float(std::clamp(1.0 - std::exp(-w), 1e-5, 1.0));
        float* xs[2] = {xl, xr};
        for (int ch = 0; ch < 2; ++ch) {
            float y = a_tf_y_[ch];
            for (uint32_t k = 0; k < n; ++k) {
                const float v = xs[ch][k];
                y += a * (v - y);
                xs[ch][k] = a_tf_hp_ ? v - y : y;
            }
            a_tf_y_[ch] = y;
        }
    }

    std::copy_n(ibl, n, ml);
    std::copy_n(ibr, n, mr);
    for (uint32_t k = 0; k < n; ++k) {
        ml[k] += xl[k];
        mr[k] += xr[k];
        al[k] += ibl[k];                  // resample: never its own output
        ar[k] += ibr[k];
    }

    // The master rack (§15.5, DDR-062): the §6 rack with the mix fed in where
    // an engine's output would be. It runs before the click and the monitor,
    // which are listening aids and not part of the mix -- a reverb on the
    // master must not smear the metronome.
    {
        Voice& mv = voice_[kMasterBus];
        if (mv.cur) {
            float* in_l = buf(kBufMasterIn, 0);
            float* in_r = buf(kBufMasterIn, 1);
            std::copy_n(ml, n, in_l);
            std::copy_n(mr, n, in_r);
            ChainInput ci;
            ci.ev = ev_all_.data();
            ci.n_ev = uint32_t(ev_all_.size());
            ci.ev_engine = ev_engine_.data();
            ci.n_engine = uint32_t(ev_engine_.size());
            ci.ev_clock = ev_clock_.data();
            ci.n_clock = uint32_t(ev_clock_.size());
            for (int t = 0; t < Tape::kTracks; ++t) {
                ci.sides.tape[t][0] = buf(kBufTape0 + t, 0);
                ci.sides.tape[t][1] = buf(kBufTape0 + t, 1);
            }
            ci.sides.input[0] = buf(kBufInputSrc, 0);
            ci.sides.input[1] = buf(kBufInputSrc, 1);
            ci.sides.self[0] = buf(kBufSelfSide, 0);
            ci.sides.self[1] = buf(kBufSelfSide, 1);
            ci.sides.master[0] = buf(kBufMasterSide, 0);
            ci.sides.master[1] = buf(kBufMasterSide, 1);
            ci.bus[0] = in_l;
            ci.bus[1] = in_r;
            float* mout[2] = {ml, mr};
            render_chain(*mv.cur, mv.ctl, mv.macro.data(), mv.mctl, &mv.mod, true, hb_, ci, mout, n);
            if (mv.old && mv.fade) {
                float* old_out[2] = {buf(kBufMasterOld, 0), buf(kBufMasterOld, 1)};
                render_chain(*mv.old, mv.ctl, mv.macro.data(), mv.mctl, &mv.mod, false, hb_, ci,
                             old_out, n);
                const uint32_t f = std::min(n, mv.fade);
                const uint32_t done = kFadeFrames - mv.fade;
                for (uint32_t k = 0; k < f; ++k) {
                    const float tx = float(done + k + 1) / float(kFadeFrames);
                    ml[k] = ml[k] * tx + old_out[0][k] * (1.0f - tx);
                    mr[k] = mr[k] * tx + old_out[1][k] * (1.0f - tx);
                }
                mv.fade -= f;
                if (!mv.fade) { retire_.push(Retire{Rk::Chain, mv.old}); mv.old = nullptr; }
            } else if (mv.old) {
                retire_.push(Retire{Rk::Chain, mv.old});
                mv.old = nullptr;
            }
        }
    }

    // The metronome (TEMPO CLICK LVL / CLICK PITCH): master only, never the
    // record tap. A pure function of the clock frame, so it is ragged-stable:
    // beat b starts at b x beat length, sounds CLICK_LEN_S of a sine under a
    // squared linear decay, an octave up on every fourth beat -- tempo.py
    // Metronome._burst, on the host's own transport. [default chosen here: the
    // emulator counted beats from PLAY; this counts them on the song grid, so
    // the click lands on TRACK's bar ticks -- audio/CLAUDE.md]
    if (a_playing_ && a_click_level_ > 0.0f && a_click_pitch_ > 0.0f && a_tempo_ > 0.0) {
        const double beat = 60.0 / a_tempo_ * double(opt_.sr);
        const double len = kClickLenS * double(opt_.sr);
        for (uint32_t k = 0; k < n; ++k) {
            const double f = double(a_song_frame_ + int64_t(k));
            const double b = std::floor(f / beat);
            const double i = f - b * beat;
            if (i >= len) continue;
            const double hz = double(a_click_pitch_) * ((int64_t(b) % 4) == 0 ? 2.0 : 1.0);
            const double env = (1.0 - i / len) * (1.0 - i / len);
            const float v = float(std::sin(2.0 * 3.14159265358979323846 * i * hz / double(opt_.sr)) *
                                  env * double(a_click_level_) * 0.5);
            ml[k] += v;
            mr[k] += v;
            al[k] += v;                   // a bounce must not carry the click
            ar[k] += v;
        }
    }

    // INPUT MONITOR: the live input into the master, never the record tap.
    // Off by default -- a laptop feeds back.
    if (a_mon_on_) {
        for (uint32_t k = 0; k < n; ++k) {
            ml[k] += il[k] * a_mon_gain_;
            mr[k] += ir[k] * a_mon_gain_;
            al[k] += il[k] * a_mon_gain_;
            ar[k] += ir[k] * a_mon_gain_;
        }
    }

    // MASTER DRIVE / DRIVE TONE (DDR-062), before COMP as §15.5 orders the
    // strip: drive -> comp -> level. Bypassed at 0, so the default path is
    // untouched.
    if (a_drive_ > 0.0f) {
        a_drive_st_.process(0, ml, n, a_drive_, a_drive_tone_, double(opt_.sr));
        a_drive_st_.process(1, mr, n, a_drive_, a_drive_tone_, double(opt_.sr));
    }
    // MASTER COMP (DDR-027): the emulator's one-knob feed-forward peak
    // compressor, one gain per block -- mixer.py Compressor, number for number,
    // with v1's fixed 120 ms release now the COMP REL knob (§15.5).
    // Off at 0, so the default host still does not colour the sound.
    // #933: that coefficient is one frame's, applied once a block, so the
    // labelled 5 ms attack and COMP REL run ~64x slow at the device's block.
    // `comp_times: "labelled"` steps the envelope on a 32-frame grid with
    // each step's own coefficient, exp(-m/(sr*tc)), so the label is the time;
    // the default stays today's until Elfy picks (#15: a sound is his).
    if (a_comp_ > 0.001f) {
        const float thr = 1.0f - 0.85f * a_comp_;
        const float ratio = 1.0f + 7.0f * a_comp_;
        const float makeup = 1.0f + 0.6f * a_comp_;
        const uint32_t step = a_comp_labelled_ ? kCompStep : n;
        float gmin = 1.0f;
        for (uint32_t k0 = 0; k0 < n; k0 += step) {
            const uint32_t m = std::min(step, n - k0);
            float peak = 0.0f;
            for (uint32_t k = k0; k < k0 + m; ++k)
                peak = std::max(peak, std::max(std::abs(ml[k]), std::abs(mr[k])));
            const double tc = peak > a_comp_env_ ? 0.005 : std::max(0.001, double(a_comp_rel_));
            const double frames = a_comp_labelled_ ? double(m) : 1.0;
            const float coef = float(std::exp(-frames / (double(opt_.sr) * tc)));
            a_comp_env_ = peak + coef * (a_comp_env_ - peak);
            const float g = a_comp_env_ <= thr ? 1.0f : std::pow(a_comp_env_ / thr, 1.0f / ratio - 1.0f);
            for (uint32_t k = k0; k < k0 + m; ++k) {
                ml[k] *= g * makeup;
                mr[k] *= g * makeup;
                al[k] *= g * makeup;      // the armed track rides the same gain
                ar[k] *= g * makeup;
            }
            gmin = std::min(gmin, g);
        }
        comp_gr_.store(std::min(comp_gr_.load(std::memory_order_relaxed), gmin), std::memory_order_relaxed);
    }

    // LEVEL and BAL, last in the strip (§15.5). BAL is a balance: unity at
    // centre, so the default is still exactly the mix.
    {
        float bl = 1.0f, br = 1.0f;
        bal_gains(a_bal_, &bl, &br);
        bl *= a_master_;
        br *= a_master_;
        if (bl != 1.0f || br != 1.0f)
            for (uint32_t k = 0; k < n; ++k) {
                ml[k] *= bl;
                mr[k] *= br;
                al[k] *= bl;
                ar[k] *= br;
            }
    }

    float pk[2] = {0.0f, 0.0f};
    float ipk[2] = {0.0f, 0.0f};
    for (uint32_t k = 0; k < n; ++k) {
        if (a_limiter_) {
            // One-knob peak limiter, off by default so the host does not colour
            // the sound (the conformance test depends on that).
            const float mag = std::max(std::abs(ml[k]), std::abs(mr[k]));
            const float want = mag > 0.95f ? 0.95f / mag : 1.0f;
            const float coef = want < a_lim_env_ ? 0.2f : 0.0005f;
            a_lim_env_ += (want - a_lim_env_) * coef;
            ml[k] *= a_lim_env_;
            mr[k] *= a_lim_env_;
            al[k] *= a_lim_env_;
            ar[k] *= a_lim_env_;
        }
        pk[0] = std::max(pk[0], std::abs(ml[k]));
        pk[1] = std::max(pk[1], std::abs(mr[k]));
        ipk[0] = std::max(ipk[0], std::abs(ibl[k]));
        ipk[1] = std::max(ipk[1], std::abs(ibr[k]));
    }
    if (out_l) std::copy_n(ml, n, out_l);
    if (out_r) std::copy_n(mr, n, out_r);

    // The taps behind `self` (the instrument bus after host.level/pan, as
    // recorded) and `master` (after the master gain and limiter, as heard).
    // ... and behind `resample` (DDR-063): the same master with the armed track,
    // the instrument bus, the click and the monitor taken back out, so REC on
    // track 8 through `arbutus/live-input` is a bounce and not a loop. The
    // subtraction is exact through the mixer's gains, COMP, LEVEL, BAL and the
    // limiter -- every one of those is a scalar gain on the block. It does
    // **not** un-mix DRIVE, the master rack or the FILTER hold, which are
    // nonlinear or stateful, so with an FX on the master the box hears its own
    // wet tail: that is out-to-in, and it converges because a rack's wet level
    // is under 1. [deviation: §15.2 says "the master minus the armed track" --
    // audio/CLAUDE.md]
    for (int ch = 0; ch < 2; ++ch) {
        const float* sb = buf(kBufInstBus, ch);
        const float* mbus = buf(kBufMaster, ch);
        const float* arm = buf(kBufResampleCut, ch);
        float* sr = self_ring_.data() + std::size_t(ch) * mb;
        float* mrg = master_ring_.data() + std::size_t(ch) * mb;
        float* rsg = resample_ring_.data() + std::size_t(ch) * mb;
        // The same two spans the read took: frames [0, span1) to ring_pos_ on,
        // the rest to the ring's start.
        auto ring_write = [&](uint32_t k0, uint32_t len, uint32_t at) {
            std::copy_n(sb + k0, len, sr + at);
            std::copy_n(mbus + k0, len, mrg + at);
            for (uint32_t k = 0; k < len; ++k) rsg[at + k] = mbus[k0 + k] - arm[k0 + k];
        };
        ring_write(0, span1, ring_pos_);
        ring_write(span1, n - span1, 0);
    }
    ring_pos_ = (ring_pos_ + n) % mb;
    for (int ch = 0; ch < 2; ++ch) {
        meter_master_[ch].store(std::max(meter_master_[ch].load(std::memory_order_relaxed), pk[ch]),
                                std::memory_order_relaxed);
        meter_inst_[ch].store(std::max(meter_inst_[ch].load(std::memory_order_relaxed), ipk[ch]),
                              std::memory_order_relaxed);
    }

    frames_rendered_.fetch_add(int64_t(n), std::memory_order_relaxed);
    if (a_playing_) {
        a_song_frame_ += int64_t(n);
        a_tape_pos_ = tr.at(n);
        if (a_rev_ && !a_recording_) a_rev_elapsed_ += double(n) * double(a_speed_);
    }
    a_song_frame_pub_.store(a_song_frame_, std::memory_order_relaxed);
    a_tape_pos_pub_.store(int64_t(std::floor(a_tape_pos_)), std::memory_order_relaxed);

    // The playhead the tape I/O thread plans read-ahead around (C1 step 5):
    // where the tape *will be* next block, at the speed and through the loop
    // it is travelling. Published even while stopped, because § 3.6 wants the
    // window around a parked playhead so PLAY after a locate is warm — and it
    // is the transport's position, never the scrub cursor's. Five relaxed
    // stores; the I/O thread is the only reader.
    Tape::View tv;
    tv.pos = int64_t(std::floor(a_tape_pos_));
    tv.step = a_playing_ ? tr.step : 1.0;
    tv.loop = a_loop_on_;
    tv.loop_in = a_loop_in_;
    tv.loop_out = a_loop_out_;
    tv.playing = a_playing_;
    tape_.set_view(tv);

    // §6.8: no cost estimate, ever — the device says when it is over budget.
    const double spent = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    if (spent > double(n) / double(opt_.sr)) {
        xrun_count_.fetch_add(1, std::memory_order_relaxed);
    }
    ++a_block_;
    // The pool's grace period is counted in rendered blocks (`Tape::blocks`),
    // not in seconds, so it means the same thing at every block size and in
    // `--offline`, where there is no clock at all.
    tape_.tick();
}

void AudioHost::render_offline(uint32_t frames, std::vector<float>* out_l, std::vector<float>* out_r) {
    std::vector<float> l(opt_.block), r(opt_.block);
    if (out_l) out_l->reserve(out_l->size() + frames);
    if (out_r) out_r->reserve(out_r->size() + frames);
    uint32_t done = 0;
    while (done < frames) {
        const uint32_t n = std::min(opt_.block, frames - done);
        // Offline only the render thread and the control thread are the same
        // one, so nothing else can allocate between the two counts.
        const long before = opt_.rt_guard ? live_blocks() : 0;
        render_block(nullptr, nullptr, l.data(), r.data(), n);
        if (opt_.rt_guard) {
            const long after = live_blocks();
            if (before < 0) rt_allocs_ = -1;
            else if (after > before) rt_allocs_ += after - before;
        }
        if (out_l) out_l->insert(out_l->end(), l.begin(), l.begin() + n);
        if (out_r) out_r->insert(out_r->end(), r.begin(), r.begin() + n);
        // Offline the I/O thread is this thread, one loop body per rendered
        // block (`tape_io.hpp`). Outside the `--rt-guard` window on purpose:
        // the guard counts the *audio* path, and this is not it.
        tape_io_.service();
        done += n;
    }
}

// ---------------------------------------------------------------------------
// control thread: polling, takes, bake
// ---------------------------------------------------------------------------
void AudioHost::commit_take() {
    const int64_t end = take_end_.load(std::memory_order_acquire);
    if (end < 0) return;
    const int64_t start = take_start_.load(std::memory_order_relaxed);
    const int t = take_track_.load(std::memory_order_relaxed);
    take_end_.store(-1, std::memory_order_relaxed);
    recording_ = false;
    // Deliberately **not** a flush. `end_take` has already queued the take's
    // tail with an exact header, and the I/O thread takes it within a poll
    // interval; waiting for it here would put the control thread behind the
    // card at the end of every take, which is #93's freeze in a new place.
    // The control thread waits for storage in exactly two places, both of
    // them refused while a take is open: `tape_edit` and `tape_load`.
    if (t < 0 || t >= Tape::kTracks || end <= start) { state_dirty_ = true; return; }

    // The take becomes a clip; from here the UI owns it and may replace the
    // whole list with set_clips (§8.2, "the UI keeps owning clip editing").
    const int64_t src = take_src_.load(std::memory_order_relaxed);
    int64_t len = end - start;
    // § 3.7 reason 2: a take that ended because storage was full ends **at the
    // last committed range**, so the clip covers what is really on the card
    // and not what the pool still happens to hold — those slots are evicted
    // soon and would then read as silence. Only on a disk-full: an ordinary
    // take is deliberately not flushed here, so its durable count is behind
    // it by design and clamping every take would cut the tail off each one.
    if (take_disk_full_) {
        take_disk_full_ = false;
        len = std::clamp<int64_t>(tape_io_.durable_frames(t) - src, 0, len);
        if (len <= 0) { state_dirty_ = true; return; }
    }
    const Clip take{start, len, src, 1.0f, -1};
    clips_[t] = absorb(clips_[t], take);
    // The UI is told the take, not left to diff two clip lists for it.
    events_.push_back(json{{"type", "take"},
                           {"track", t},
                           {"start_frame", take.start_frame},
                           {"len", take.len},
                           {"src_offset", take.src_offset},
                           {"gain", 1.0}});
    auto cl = std::make_unique<ClipList>();
    cl->clips = clips_[t];
    Cmd c{};
    c.k = Ck::SetClips;
    c.i0 = t;
    c.p = cl.release();
    push(c);
    // The same list to the I/O thread, which plans read-ahead from it (C1
    // step 5). A copy under its mutex: the list the audio thread plays is the
    // audio thread's and must not be walked by a second thread.
    tape_io_.set_clips(t, clips_[t]);
    state_dirty_ = true;
}

void AudioHost::poll() {
    retire_all();
    commit_take();
    // #920: live, a bounce job takes a chunk every turn of the loop; offline
    // it waits for `render`, so a test decides how far it has got.
    if (!opt_.offline) bounce_step();
    song_frame_ = a_song_frame_pub_.load(std::memory_order_relaxed);

    // The window is the running total minus where it stood when the window
    // opened, and it is reported *before* it rolls over. It used to be a second
    // counter zeroed at the roll-over, which wiped every xrun counted since the
    // last poll -- and a stall long enough to cost a block delays the poll too,
    // so the xruns that mattered most were the ones lost (#13, on the CM4).
    const auto now = std::chrono::steady_clock::now();
    const int64_t total = xrun_count_.load(std::memory_order_relaxed);
    const int64_t w = total - xrun_window_base_;
    if (w > xrun_reported_) {
        xrun_reported_ = w;
        events_.push_back(json{{"type", "xrun"},
                               {"count", w},
                               {"window_s", kXrunWindowS},
                               {"total", total},
                               {"flag", w >= kXrunFlagCount}});
    }
    // `tape_late` (C1 step 7, § 3.7): the card did not deliver a piece of tape
    // in time and a track played silence for those frames. The same window and
    // the same 3-in-2-s flag as `xrun` on purpose — Elfy's § 7 answer put the
    // words in the slot `CPU NO HAPPY` uses, and one alarm rule is what makes
    // that honest. `tracks` is the mask since the last push, taken with an
    // exchange so nothing is counted twice.
    const int64_t late_total = a_tape_late_.load(std::memory_order_relaxed);
    const int64_t lw = late_total - late_window_base_;
    if (lw > late_reported_) {
        late_reported_ = lw;
        events_.push_back(
            json{{"type", "tape_late"},
                 {"count", lw},
                 {"window_s", kXrunWindowS},
                 {"total", late_total},
                 {"flag", lw >= kXrunFlagCount},
                 {"tracks", a_tape_late_tracks_.exchange(0, std::memory_order_relaxed)}});
    }
    // The three ways a take ends early (§ 3.7), each its own push because the
    // player's next action differs and the UI says two different things
    // (Elfy, § 7 question 3: `TAPE FULL` / `CARD STALLED`). One push per
    // occurrence, beside `xrun`'s, and `commit_take` above has already turned
    // what was captured into a clip.
    const int64_t stalls = a_tape_stall_.load(std::memory_order_relaxed);
    if (stalls > tape_stall_reported_) {
        tape_stall_reported_ = stalls;
        events_.push_back(json{{"type", "tape_full"},
                               {"reason", "stall"},
                               {"total", stalls}});
    }
    const int64_t fmt = a_tape_format_.load(std::memory_order_relaxed);
    if (fmt > tape_format_reported_) {
        tape_format_reported_ = fmt;
        events_.push_back(json{{"type", "tape_full"},
                               {"reason", "format"},
                               {"total", fmt}});
    }
    // Storage refused for want of space. The I/O thread saw it; ending the
    // take is the control thread's, because `record` is — and it is done here
    // rather than by the audio thread so the take is shortened to what the
    // card really took (`commit_take`).
    const int64_t disk = tape_io_.disk_full();
    if (disk > tape_disk_reported_) {
        tape_disk_reported_ = disk;
        if (recording_) {
            take_disk_full_ = true;
            Cmd c{};
            c.k = Ck::Record;
            c.i0 = 0;
            c.i1 = armed_;
            push(c);
            recording_ = false;
            state_dirty_ = true;
        }
        events_.push_back(json{{"type", "tape_full"},
                               {"reason", "disk"},
                               {"total", disk}});
    }
    // A compaction that has finished, been cancelled or failed (C1 step 8,
    // § 3.8). The swap itself is the control thread's -- it renames the file
    // in, reopens the medium and drops every resident range of the track,
    // whose slots hold audio from offsets that have just moved -- and it
    // happens here rather than in the reply because the copy is seconds long.
    // The push carries the map the UI remaps its clips, its lift buffer and
    // its undo record through, and it is the UI's cue to send `set_clips`.
    if (compacting_ >= 0) {
        const int t = compacting_;
        const TapeIo::CompactState cs = tape_io_.compact_state();
        if (cs == TapeIo::CompactState::kDone) {
            std::vector<CompactMove> map;
            int64_t frames = 0;
            std::string err;
            if (tape_io_.compact_finish(map, frames, err)) {
                const int64_t was = tape_.head[t].load(std::memory_order_relaxed);
                tape_.head[t].store(frames, std::memory_order_relaxed);
                // The peak cache is keyed by (track, generation, window): the
                // medium's frames have moved, so every entry for it is stale.
                ++medium_gen_[t];
                json rows = json::array();
                for (const CompactMove& m : map)
                    rows.push_back(json{{"from", m.from}, {"to", m.to}, {"len", m.len}});
                events_.push_back(json{{"type", "tape_compact"},
                                       {"track", t},
                                       {"map", rows},
                                       {"frames", frames},
                                       {"freed", std::max<int64_t>(0, was - frames)}});
                state_dirty_ = true;
            } else {
                events_.push_back(json{{"type", "tape_compact"},
                                       {"track", t},
                                       {"error", err.empty() ? std::string("COMPACT FAILED") : err}});
            }
            tape_io_.compact_clear();
            compacting_ = -1;
        } else if (cs == TapeIo::CompactState::kCancelled ||
                   cs == TapeIo::CompactState::kFailed) {
            const bool cancelled = cs == TapeIo::CompactState::kCancelled;
            const std::string e = tape_io_.compact_error();
            json j{{"type", "tape_compact"}, {"track", t}};
            if (cancelled) j["cancelled"] = true;
            else j["error"] = e.empty() ? std::string("COMPACT FAILED") : e;
            events_.push_back(j);
            tape_io_.compact_clear();
            compacting_ = -1;
        }
    }

    if (std::chrono::duration<double>(now - xrun_window_start_).count() >= kXrunWindowS) {
        xrun_window_start_ = now;
        xrun_window_base_ = total;
        xrun_reported_ = 0;
        late_window_base_ = late_total;
        late_reported_ = 0;
    }

    // Offline the clock is `render`, so the meters are paced by it rather than
    // by wall time: a client renders a second of audio in a millisecond and
    // would otherwise never see a meter (and `hz` would mean nothing).
    const int64_t rendered = frames_rendered_.load(std::memory_order_relaxed);
    const bool due = opt_.offline
        ? rendered > meter_frame_        // one push per render, however short
        : std::chrono::duration<double>(now - last_meter_).count() >= 1.0 / meters_hz_;
    if (meters_on_ && due) {
        last_meter_ = now;
        meter_frame_ = rendered;
        // Peaks since the last push, so nothing between two pushes is missed
        // (the live take's envelope on TRACK is drawn from these).
        json tr = json::array();
        for (int t = 0; t < Tape::kTracks; ++t)
            tr.push_back(arb::manifest::nice(meter_track_[t].exchange(0.0f, std::memory_order_relaxed)));
        events_.push_back(json{
            {"type", "meters"},
            {"master", json::array({arb::manifest::nice(meter_master_[0].exchange(0.0f, std::memory_order_relaxed)),
                                    arb::manifest::nice(meter_master_[1].exchange(0.0f, std::memory_order_relaxed))})},
            {"instrument", json::array({arb::manifest::nice(meter_inst_[0].exchange(0.0f, std::memory_order_relaxed)),
                                        arb::manifest::nice(meter_inst_[1].exchange(0.0f, std::memory_order_relaxed))})},
            {"tracks", tr},
            {"song_frame", song_frame_},
            {"tape_pos", a_tape_pos_pub_.load(std::memory_order_relaxed)},
            {"comp_gr", arb::manifest::nice(comp_gr_.exchange(1.0f, std::memory_order_relaxed))}});
        // The MOD rack's live target values (modulation-spec §9's ghost), for
        // the active instrument -- the only one rendering. Keyed by target
        // address; absent when nothing is routed. Only values rendered with the
        // current route list are reported, so an edit never mislabels one.
        const int ai = active_;
        if (mod_live_gen_[ai].load(std::memory_order_acquire) == mod_gen_[ai]) {
            const uint32_t mask = mod_live_mask_[ai].load(std::memory_order_relaxed);
            const std::vector<RouteDoc>& routes = inst_[ai].mod.routes;
            json mj = json::object();
            for (std::size_t k = 0; k < routes.size() && k < std::size_t(kMaxRoutes); ++k)
                if (mask & (1u << k))
                    mj[routes[k].target] = arb::manifest::nice(mod_live_[ai][k].load(std::memory_order_relaxed));
            if (!mj.empty()) events_.back()["mod"] = std::move(mj);
        }
    }
}

std::vector<json> AudioHost::drain_events() {
    Probe p;
    while (probes_.pop(p))
        events_.push_back(json{{"type", "probe"}, {"id", p.id}, {"t_ctl_ns", p.t_ctl_ns},
                               {"t1_ns", p.t1_ns}, {"block", p.block}, {"frame", 0}});
    std::vector<json> v;
    v.swap(events_);
    return v;
}

bool AudioHost::take_state_dirty() {
    const bool d = state_dirty_;
    state_dirty_ = false;
    return d;
}

std::string AudioHost::bake(const json& msg, std::string& err,
                            int64_t* start, int64_t* frames) {
    std::vector<int> tracks;
    if (msg.contains("tracks") && msg["tracks"].is_array()) {
        for (const json& t : msg["tracks"])
            if (t.is_number() && t.get<int>() >= 0 && t.get<int>() < Tape::kTracks)
                tracks.push_back(t.get<int>());
    } else {
        for (int t = 0; t < Tape::kTracks; ++t) tracks.push_back(t);
    }
    const std::string file = gets(msg, "file");
    if (file.empty()) { err = "bake needs \"file\""; return {}; }
    const std::string fmt = gets(msg, "format", "wav24");
    int bits = 24;
    if (fmt == "wav16") bits = 16;
    else if (fmt == "f32") bits = 32;
    else if (fmt != "wav24") { err = "format must be wav16, wav24 or f32 (MP3 is out of scope)"; return {}; }

    // A bounce is this message with `trim` and `master_fx` on
    // (companion-app-spec.md §4, the map's D2): the same mixdown, cut to where
    // the clips are and optionally printed through the master rack.
    const bool trim = msg.value("trim", false);
    const bool master_fx = msg.value("master_fx", false);
    const bool has_frames = msg.contains("frames") && msg["frames"].is_number();
    if (trim && has_frames) { err = "bake takes \"frames\" or \"trim\", not both"; return {}; }

    int64_t begin = 0, end = 0;
    bool any = false;
    for (int t : tracks)
        for (const Clip& c : clips_[t]) {
            begin = any ? std::min(begin, c.start_frame) : c.start_frame;
            end = std::max(end, c.start_frame + c.len);
            any = true;
        }
    if (!trim) begin = 0;
    if (has_frames) end = msg["frames"].get<int64_t>();
    if (end <= begin) { err = "nothing to bake: the selected tracks hold no clips"; return {}; }

    // §12: the host reports a rack's latency and compensates nothing live, so
    // an offline print renders `lat` extra frames and drops them off the front
    // -- the same arithmetic `clip_to_asset {master_fx}` does. The tail is
    // what companion-app-spec §4 asks for: the rack goes on ringing after the
    // last clip, so the file keeps `lat` + 2 s of it and no more.
    const int64_t lat = master_fx ? int64_t(master_fx_latency()) : 0;
    const int64_t tail = master_fx ? lat + 2 * int64_t(opt_.sr) : 0;
    const int64_t body = end - begin;
    const int64_t out_len = body + tail;
    const int64_t nrender = out_len + lat;

    arb::io::Audio out;
    out.sample_rate = opt_.sr;
    out.ch.assign(2, std::vector<float>(std::size_t(nrender), 0.0f));

    // The same arithmetic as render_block()'s mixer, offline: a bake is a
    // recording of the mix, so it must agree with what was played (§3.3). The
    // per-track EQ and mute/solo are mixer state and are applied; COMP and the
    // master rack are not (PROTOCOL.md "Tape and mixer") -- COMP is a
    // performance knob, and MASTER FX on a bounce is FILE › PROJECT's switch.
    float lim = 1.0f;
    TrackEq eq[Tape::kTracks];
    for (int t = 0; t < Tape::kTracks; ++t) eq[t].set(eq_[t], double(opt_.sr));
    float bal_l = 1.0f, bal_r = 1.0f;
    bal_gains(bal_, &bal_l, &bal_r);
    DriveStage drive;
    for (int64_t k = 0; k < nrender; ++k) {
        const int64_t f = begin + k;
        float l = 0.0f, r = 0.0f;
        for (int t : tracks) {
            const float p = std::clamp(track_pan_[t], -1.0f, 1.0f);
            const float ang = (p + 1.0f) * 0.25f * 3.14159265358979f;
            const float gate = mix_gate(mute_, solo_, Tape::kTracks, t);
            const float gl = std::cos(ang) * track_gain_[t] * gate;
            const float gr = std::sin(ang) * track_gain_[t] * gate;
            float tl = 0.0f, tr = 0.0f;
            for (const Clip& c : clips_[t]) {
                if (f < c.start_frame || f >= c.start_frame + c.len) continue;
                int64_t run = 0;
                const float* src = tape_run(
                    c.src_track >= 0 && c.src_track < Tape::kTracks ? c.src_track : t,
                    c.src_offset + (f - c.start_frame), run);
                if (!src) continue;             // absent or never written: silence
                tl += src[0] * c.gain;
                tr += src[1] * c.gain;
            }
            if (eq[t].active()) {
                eq[t].process(0, &tl, 1);
                eq[t].process(1, &tr, 1);
            }
            l += tl * gl;
            r += tr * gr;
        }
        if (drive_ > 0.0f) {
            drive.process(0, &l, 1, drive_, drive_tone_, double(opt_.sr));
            drive.process(1, &r, 1, drive_, drive_tone_, double(opt_.sr));
        }
        l *= master_ * bal_l;
        r *= master_ * bal_r;
        if (limiter_) {
            const float mag = std::max(std::abs(l), std::abs(r));
            const float want = mag > 0.95f ? 0.95f / mag : 1.0f;
            lim += (want - lim) * (want < lim ? 0.2f : 0.0005f);
            l *= lim;
            r *= lim;
        }
        out.ch[0][std::size_t(k)] = l;
        out.ch[1][std::size_t(k)] = r;
    }
    tape_unpin();                               // done walking the media

    if (master_fx) {
        if (std::string e = master_fx_offline(out.ch[0], out.ch[1]); !e.empty()) {
            err = "MASTER FX: " + e;
            return {};
        }
        for (std::vector<float>& ch : out.ch) {
            ch.erase(ch.begin(), ch.begin() + std::size_t(lat));
            ch.resize(std::size_t(out_len), 0.0f);
        }
    }

    if (std::string e = arb::io::write(file, out, bits); !e.empty()) { err = e; return {}; }
    if (start) *start = begin;
    if (frames) *frames = out_len;
    return file;
}

// ---------------------------------------------------------------------------
// control thread: the protocol (§8.2). PROTOCOL.md is the reference.
// ---------------------------------------------------------------------------
namespace {

int slot_of(const std::string& s) {                 // 0 seq, 1 engine, 2 fx, 3 mod
    if (s == "seq") return 0;
    if (s == "fx" || s == "rack") return 2;
    if (s == "mod") return 3;
    return 1;
}

}  // namespace

json AudioHost::reply_ok(const json& msg, json extra) const {
    json j = json{{"type", "ok"}, {"for", gets(msg, "type")}};
    if (msg.contains("req")) j["req"] = msg["req"];
    for (auto it = extra.begin(); it != extra.end(); ++it) j[it.key()] = it.value();
    return j;
}

json AudioHost::reply_err(const json& msg, const std::string& code, const std::string& message,
                          const std::string& detail) const {
    json j = json{{"type", "error"}, {"code", code}, {"message", message},
                  {"for", gets(msg, "type")}};
    if (!detail.empty()) j["detail"] = detail;
    if (msg.contains("req")) j["req"] = msg["req"];
    return j;
}

json AudioHost::reply_refusal(const json& msg, const Refusal& r) const {
    std::string code = "engine_broken";
    if (r.code == "NEEDS ENGINE") code = "needs_engine";
    else if (r.code == "NEEDS VERSION") code = "needs_version";
    else if (r.code == "NEEDS FIRMWARE UPDATE") code = "needs_firmware_update";
    else if (r.code == "NO BUILD FOR THIS DEVICE") code = "no_build_for_this_device";
    else if (r.code == "UNKNOWN ASSET") code = "unknown_asset";
    else if (r.code == "ASSET NOT FOUND") code = "asset_not_found";
    return reply_err(msg, code, refusal_message(r), r.detail);
}

// "library-relative" (§7.2) resolved against, in order: the document's own
// optional "dir", each --library root, then the working directory. An absolute
// path is taken as-is. A path that resolves nowhere is returned anyway so the
// ASSET NOT FOUND refusal can name it. [default chosen here: the spec says
// library-relative and the library is a list of roots -- audio/CLAUDE.md]
std::string AudioHost::resolve_asset(const std::string& path, const std::string& dir_hint) const {
    namespace fs = std::filesystem;
    std::error_code ec;
    const fs::path rel(path);
    if (rel.is_absolute()) return rel.lexically_normal().string();

    std::vector<fs::path> roots;
    if (!dir_hint.empty()) roots.emplace_back(dir_hint);
    for (const std::string& r : lib_.roots()) roots.emplace_back(r);
    roots.emplace_back(fs::current_path(ec));
    for (const fs::path& r : roots) {
        const fs::path c = r / rel;
        if (fs::exists(c, ec)) return fs::absolute(c, ec).lexically_normal().string();
    }
    return fs::absolute(rel, ec).lexically_normal().string();
}

Refusal AudioHost::load_patch(int inst, const json& p) {
    if (!p.is_object()) return Refusal{"ENGINE BROKEN", "patch is not an object"};
    const int fmt = p.value("format", 0);
    if (fmt < 1) return Refusal{"ENGINE BROKEN", "patch has no \"format\""};
    if (fmt > 1) return Refusal{"NEEDS FIRMWARE UPDATE", "patch format " + std::to_string(fmt)};
    if (p.value("kind", std::string{}) != "patch")
        return Refusal{"ENGINE BROKEN", "kind is not \"patch\""};

    InstState s;
    s.kit = inst_[inst].kit;                    // a patch does not carry the kit
    s.kit_path = inst_[inst].kit_path;
    // ...so it does not carry the kit asset either -- but only onto the *same*
    // engine. A different box has different asset keys, and carrying one over
    // refuses the load with UNKNOWN ASSET (§7.4); `load_engine` already says so
    // and clears both. Found in stage I1: a patch for a box that takes no
    // assets, loaded after a sampler patch, was refused for the sampler's.
    const std::string had_engine = inst_[inst].engine.id;
    const std::string wants_engine =
        (p.contains("engine") && p["engine"].is_object() && p["engine"].contains("id"))
            ? p["engine"].value("id", std::string{}) : had_engine;
    if (wants_engine == had_engine) s.engine.assets = inst_[inst].engine.assets;
    s.name = p.value("name", std::string{});
    s.octave = p.value("octave", 0);
    s.lfo = p.value("lfo", json::object());     // carried, not applied: the MOD rack is "mod"
    s.host_level = inst_[inst].host_level;      // session knobs, not in §7.2
    s.host_pan = inst_[inst].host_pan;

    // Where a relative asset path is measured from, when the document says.
    const std::string dir = p.value("dir", std::string{});
    auto take_assets = [&](SlotState& st, const json& aj) {
        if (!aj.is_object()) return;
        st.assets.clear();
        for (auto it = aj.begin(); it != aj.end(); ++it)
            if (it.value().is_string())
                st.assets.push_back(AssetRef{it.key(),
                                             resolve_asset(it.value().get<std::string>(), dir)});
    };
    auto fill = [&](SlotState& st, const json& j) {
        st.loaded = true;
        st.id = j.value("id", std::string{});
        st.min_version = j.value("min_version", std::string{});
        const json pj = j.value("params", json::object());
        for (auto it = pj.begin(); it != pj.end(); ++it)
            if (it.value().is_number()) st.params[it.key()] = it.value().get<float>();
        // §3.2: content, not parameters. Named here, opened by the engine.
        if (j.contains("assets")) take_assets(st, j["assets"]);
    };
    // A patch may put the map at the top level; the engine's own wins.
    if (p.contains("assets") && !(p.contains("engine") && p["engine"].is_object() &&
                                  p["engine"].contains("assets")))
        take_assets(s.engine, p["assets"]);
    if (p.contains("engine") && p["engine"].is_object()) fill(s.engine, p["engine"]);
    if (p.contains("seq") && p["seq"].is_object()) {
        fill(s.seq, p["seq"]);
        s.seq_pattern = p["seq"].value("pattern", json::object());
    }
    if (p.contains("env") && p["env"].is_object()) {
        const json& e = p["env"];
        s.env_a = getf(e, "a", s.env_a);
        s.env_d = getf(e, "d", s.env_d);
        s.env_s = getf(e, "s", s.env_s);
        s.env_r = getf(e, "r", s.env_r);
        s.env_mode = env_mode_from(e.value("mode", std::string("poly")));
        // DDR-052; absent = the InstState defaults, which are today's sound.
        s.env_glide = std::clamp(getf(e, "glide", s.env_glide), 0.0f, kEnvMax[kEnvGlide]);
        s.env_detune = std::clamp(getf(e, "detune", s.env_detune), 0.0f, kEnvMax[kEnvDetune]);
        s.env_bend_range = std::clamp(getf(e, "bend_range", s.env_bend_range), 0.0f, kBendRangeMax);
    }
    if (p.contains("fx") && p["fx"].is_object())
        if (Refusal r = rack_from_json(p["fx"], s.rack); !r.ok()) return r;
    // modulation-spec §11. Absent = an empty MOD rack, as absent "fx" is an
    // empty FX rack, so a patch without it loads exactly as it always did.
    if (p.contains("mod") && p["mod"].is_object())
        if (Refusal r = mod_from_json(p["mod"], s.mod); !r.ok()) return r;
    for (TabDoc& t : s.mod.tabs)
        for (auto& [key, path] : t.assets) path = resolve_asset(path, dir);
    for (TabDoc& t : s.rack.tabs)
        for (auto& [key, path] : t.assets) path = resolve_asset(path, dir);

    if (Refusal r = apply(inst, std::move(s)); !r.ok()) return r;
    push_env(inst);
    return {};
}

void AudioHost::push_env(int inst) {
    const InstState& s = inst_[inst];
    const float v[kEnvSlots] = {s.env_a, s.env_d, s.env_s, s.env_r,
                                s.env_glide, s.env_detune, s.env_bend_range};
    for (int k = 0; k < kEnvSlots; ++k) {
        Cmd c{};
        c.k = Ck::Env;
        c.i0 = inst;
        c.i1 = k;
        c.f0 = v[k];
        push(c);
    }
    Cmd c{};
    c.k = Ck::Env;
    c.i0 = inst;
    c.i1 = kEnvSlots;
    c.i2 = s.env_mode;
    push(c);
}

Refusal AudioHost::load_kit(int inst, const json& k) {
    if (!k.is_object()) return Refusal{"ENGINE BROKEN", "kit is not an object"};
    const int fmt = k.value("format", 0);
    if (fmt < 1) return Refusal{"ENGINE BROKEN", "kit has no \"format\""};
    if (fmt > 1) return Refusal{"NEEDS FIRMWARE UPDATE", "kit format " + std::to_string(fmt)};
    if (k.value("kind", std::string{}) != "kit")
        return Refusal{"ENGINE BROKEN", "kind is not \"kit\""};
    const json pads = k.value("pads", json::object());
    if (!pads.is_object() || pads.empty())
        return Refusal{"ENGINE BROKEN", "kit has no \"pads\""};

    namespace fs = std::filesystem;
    const fs::path dir = k.contains("dir") ? fs::path(k["dir"].get<std::string>()) : fs::path{};
    json resolved = json::object();
    json kit = k;
    for (auto it = pads.begin(); it != pads.end(); ++it) {
        // §15.3 shape {file, params}, or the v1 shape "0": "kick.wav", which
        // migrates forward here: the document the host keeps is v2 from now on
        // and is written that way on the next save (§7.1). Both are format 1.
        json pad;
        if (it.value().is_string())
            pad = json{{"file", it.value()}, {"params", json::object()}};
        else if (it.value().is_object() && it.value().contains("file") && it.value()["file"].is_string())
            pad = json{{"file", it.value()["file"]},
                       {"params", it.value().value("params", json::object())}};
        else if (it.value().is_object())
            // A **synthesised** drum box has no file per pad: its `pads` map is
            // parameters only (`arbutus/dbox`, stage I1). The pad is still a
            // pad -- set_note_param writes here, copy_pad copies it, and this
            // document is still the save -- so the file is what is optional,
            // not the pad. §15.3 gives the shape {file, params} and does not
            // say a kit must be samples. [deviation, audio/CLAUDE.md]
            pad = json{{"params", it.value().value("params", json::object())}};
        else
            return Refusal{"ENGINE BROKEN", "pad " + it.key() + " is neither a file nor params"};
        if (!pad["params"].is_object()) pad["params"] = json::object();
        kit["pads"][it.key()] = pad;
        if (pad.contains("file")) {
            fs::path f = pad["file"].get<std::string>();
            if (f.is_relative() && !dir.empty()) f = dir / f;
            std::error_code ec;
            if (!fs::exists(f, ec)) return Refusal{"ENGINE BROKEN", "kit pad " + it.key() + ": no file at " + f.string()};
            pad["file"] = fs::absolute(f, ec).string();
        }
        resolved[it.key()] = std::move(pad);
    }

    InstState s = inst_[inst];
    s.kit = std::move(kit);
    json out = s.kit;
    out["pads"] = resolved;
    const fs::path path = fs::temp_directory_path() /
                          ("arbutus-kit-" + std::to_string(inst) + "-" +
                           std::to_string(::getpid()) + ".json");
    std::ofstream f(path);
    if (!f) return Refusal{"ENGINE BROKEN", "cannot write " + path.string()};
    f << out.dump(2) << '\n';
    f.close();
    s.kit_path = path.string();
    // The name is per instrument and per pid, so a reload overwrites rather
    // than accumulates; what grows is one file per host restart, and the
    // destructor removes these (#105).
    if (std::find(temp_kits_.begin(), temp_kits_.end(), s.kit_path) == temp_kits_.end())
        temp_kits_.push_back(s.kit_path);
    // The pad map *is* the drum sampler's asset table, and it reaches the engine
    // as one asset -- the resolved kit.json -- not as 24. A descriptor can only
    // declare ARB_MAX_ASSETS keys, and a pad map is variable-length content
    // (DDR-037); one key that names a document is the only shape that fits.
    s.engine.assets.clear();
    s.engine.assets.push_back(AssetRef{"kit", s.kit_path});
    return apply(inst, std::move(s));
}

// Every pad's params complete for the loaded engine's per_note list, missing
// ones at the descriptor default, so `state.instruments.drum.kit` is the kit a
// UI saves -- there is no save_kit message, as there is no save_patch.
// Ids the engine does not declare are kept: they are the document's, not ours.
void AudioHost::normalise_kit(InstState& s) const {
    if (!s.kit.is_object() || !s.kit.contains("pads") || !s.kit["pads"].is_object()) return;
    if (!s.engine.loaded) return;
    const json params = s.engine.manifest.value("params", json::array());
    for (auto it = s.kit["pads"].begin(); it != s.kit["pads"].end(); ++it) {
        json& pad = it.value();
        if (pad.is_string()) pad = json{{"file", pad}, {"params", json::object()}};
        if (!pad.is_object()) continue;
        if (!pad.contains("params") || !pad["params"].is_object()) pad["params"] = json::object();
        for (const json& pj : params) {
            if (!pj.value("per_note", false)) continue;
            const std::string id = pj.value("id", std::string{});
            if (id.empty() || pad["params"].contains(id)) continue;
            arb_param_desc d{};
            d.curve = pj.value("curve", std::string("lin")) == "log" ? ARB_CURVE_LOG : ARB_CURVE_LIN;
            d.min = pj.value("min", 0.0f);
            d.max = pj.value("max", 1.0f);
            pad["params"][id] = arb::manifest::nice(arb::norm(d, pj.value("def", 0.0f)));
        }
    }
}

std::string AudioHost::kit_pad_file(const InstState& s, const std::string& pad) const {
    if (!s.kit.is_object() || !s.kit.contains("pads") || !s.kit["pads"].contains(pad)) return {};
    const json& pj = s.kit["pads"][pad];
    std::string file = pj.is_string() ? pj.get<std::string>()
                                      : (pj.is_object() ? pj.value("file", std::string{}) : std::string{});
    if (file.empty()) return {};
    namespace fs = std::filesystem;
    fs::path f(file);
    if (f.is_relative() && s.kit.contains("dir") && s.kit["dir"].is_string())
        f = fs::path(s.kit["dir"].get<std::string>()) / f;
    std::error_code ec;
    return fs::absolute(f, ec).lexically_normal().string();
}

// §15.6 `waveform`: a peak envelope of an asset for the ENGINE page. Read and
// reduced here, on the control thread, from the file; nothing reaches the
// audio thread. Mono: channels are averaged first, which is what every stock
// engine plays (sdk/wav.hpp). Per bin, the min and max sample. [default chosen
// here: §15.6 says "peak envelope" -- audio/CLAUDE.md]
json AudioHost::waveform(const json& msg, const std::string& path, int bins, std::string& err) {
    namespace fs = std::filesystem;
    std::error_code ec;
    const std::uintmax_t size = fs::file_size(path, ec);
    if (ec) { err = "no file at " + path; return nullptr; }
    const long long mtime = static_cast<long long>(fs::last_write_time(path, ec).time_since_epoch().count());
    const std::string key = path + "#" + std::to_string(bins);
    if (auto it = wave_cache_.find(key); it != wave_cache_.end() &&
                                         it->second.size == size && it->second.mtime == mtime) {
        json j = reply_ok(msg, it->second.reply);
        j["type"] = "waveform";
        return j;
    }
    arb::wav::Sample smp;
    if (std::string e = arb::wav::load_mono(path, smp); !e.empty()) { err = e; return nullptr; }
    const std::size_t n = smp.data.size();
    json mn = json::array(), mx = json::array();
    for (int b = 0; b < bins && n > 0; ++b) {
        std::size_t a = std::size_t(uint64_t(b) * n / uint64_t(bins));
        std::size_t e = std::size_t(uint64_t(b + 1) * n / uint64_t(bins));
        a = std::min(a, n - 1);
        e = std::max(e, a + 1);                 // fewer frames than bins: repeat the nearest
        float lo = smp.data[a], hi = smp.data[a];
        for (std::size_t i = a + 1; i < e; ++i) {
            lo = std::min(lo, smp.data[i]);
            hi = std::max(hi, smp.data[i]);
        }
        mn.push_back(arb::manifest::nice(lo));
        mx.push_back(arb::manifest::nice(hi));
    }
    json body{{"path", path}, {"bins", bins}, {"frames", n}, {"sample_rate", smp.sample_rate},
              {"channels", "mono"}, {"min", mn}, {"max", mx}};
    if (wave_cache_.size() >= 64) wave_cache_.clear();   // a browser's worth; never grows past it
    wave_cache_[key] = WaveEntry{size, mtime, body};
    json j = reply_ok(msg, body);
    j["type"] = "waveform";
    return j;
}

// `clip_peaks`: TRACK's waveform for a clip, read from the medium on the
// control thread (the samples never come back up otherwise). Per bin, the min
// and max over *both* channels -- the emulator drew max |sample| of the stereo
// clip per pixel, so this is that picture. Cached like `waveform`; a region
// past the medium's head (a take still open) is read fresh every time.
// [default chosen here: §8.2 has no such message -- audio/CLAUDE.md]
// `clip_to_asset` (§15.6): a stretch of tape becomes a file an engine can load.
// This is the tape -> instrument half of the round trip (DDR-030): the UI lifts
// a clip, drops it on SYNTH > ENGINE, and the sampler is handed the path that
// comes back. One track renders its own clips at their gains -- that track's
// audio, not a mix of it -- while `"all"` renders through the mixer, which is
// the mixdown FILE > BOUNCE would give (tape-sampling-workflow §5.4).
//
// The file is **16-bit WAV**: engines read nothing else (repo CLAUDE.md), and
// it lands in the sampler's own library folder so the browser lists it next
// session. [default chosen here: §15.6 gives the message, not the path --
// audio/CLAUDE.md]
json AudioHost::clip_to_asset(const json& msg, std::string& code, std::string& err) {
    namespace fs = std::filesystem;
    commit_take();
    code = "bad_request";
    if (recording_) { err = "RECORDING"; return nullptr; }

    const json tj = msg.contains("track") ? msg["track"] : json("all");
    const bool all = tj.is_string() && tj.get<std::string>() == "all";
    int only = -1;
    if (!all) {
        if (!tj.is_number()) { err = "track is 0..7 or \"all\""; return nullptr; }
        only = tj.get<int>();
        if (only < 0 || only >= Tape::kTracks) { err = "track must be 0..7"; return nullptr; }
    }
    const int64_t start = msg.value("start_frame", int64_t(0));
    const int64_t len = msg.value("len", int64_t(0));
    if (start < 0 || len <= 0) { err = "clip_to_asset needs start_frame >= 0 and len > 0"; return nullptr; }
    if (len > tape_.capacity()) { err = "len is longer than the tape"; return nullptr; }

    std::string path = gets(msg, "file");
    std::error_code ec;
    if (path.empty()) {
        // The sampler's folder in the writable library root, or the working
        // directory when the host was started without one (the tests).
        fs::path dir = write_root().empty() ? fs::current_path(ec)
                                            : fs::path(write_root()) / "synth" / "sampler";
        fs::create_directories(dir, ec);
        if (ec) { code = "write_failed"; err = "cannot create " + dir.string(); return nullptr; }
        const std::string stem = all ? "mix" : "tape-" + std::to_string(only + 1);
        for (int n = 1; n <= 999; ++n) {
            char buf[16];
            std::snprintf(buf, sizeof buf, "-%03d.wav", n);
            const fs::path c = dir / (stem + buf);
            if (!fs::exists(c, ec)) { path = c.string(); break; }
        }
        if (path.empty()) { code = "write_failed"; err = "no free name in " + dir.string(); return nullptr; }
    }

    // #920: a job may not start over another one, nor over a compaction,
    // which moves the very offsets the copied clip lists read.
    const bool job = msg.value("job", false);
    if (job && bounce_) { err = "BUSY"; return nullptr; }
    if (job && compacting_ >= 0) { err = "BUSY"; return nullptr; }

    auto b = std::make_unique<Bounce>();
    b->all = all;
    b->only = only;
    b->path = path;
    b->start = start;
    b->len = len;
    // "all" is the mixdown, so it takes the mixer the same way `bake` does:
    // EQ, mute/solo, gain, pan, drive, master and BAL, but not COMP. One
    // track hands back what it holds, so a clip dropped on the sampler
    // sounds like the clip: unity, no EQ.
    for (int t = 0; t < Tape::kTracks; ++t) {
        if (!all && t != only) continue;
        b->clips[t] = clips_[t];
        b->gl[t] = b->gr[t] = 1.0f;
        if (all) {
            const float pn = std::clamp(track_pan_[t], -1.0f, 1.0f);
            const float ang = (pn + 1.0f) * 0.25f * 3.14159265358979f;
            const float gate = mix_gate(mute_, solo_, Tape::kTracks, t);
            b->gl[t] = std::cos(ang) * track_gain_[t] * gate;
            b->gr[t] = std::sin(ang) * track_gain_[t] * gate;
            b->eq[t].set(eq_[t], double(opt_.sr));
        }
    }
    bal_gains(bal_, &b->bal_l, &b->bal_r);
    b->master = master_;
    b->drive_amt = drive_;
    b->drive_tone = drive_tone_;
    b->limiter = limiter_;

    // FILE › BOUNCE's MASTER FX switch (stage J1): with `master_fx` the
    // mixdown is run through a second copy of the master rack afterwards.
    // The rack may have latency (`arbutus/drive` declares 15 frames and is the
    // first box in the instrument that does), and nothing compensates it live
    // (§12) — so the window is rendered `lat` frames long and the first `lat`
    // frames of the rack's output are dropped, which puts the bounce back on
    // the timeline it was played on.
    b->master_fx = all && msg.value("master_fx", false);
    b->lat = b->master_fx ? int64_t(master_fx_latency()) : 0;
    b->nrender = len + b->lat;
    if (b->master_fx) {
        if (std::string e = master_fx_build(b->fx); !e.empty()) {
            code = "bad_request";
            err = "MASTER FX: " + e;
            return nullptr;
        }
    }
    b->out.sample_rate = opt_.sr;
    b->out.ch.assign(2, std::vector<float>(std::size_t(b->nrender), 0.0f));

    if (job) {
        // The file is written only when the last chunk is in, so a cancel
        // leaves nothing on the card (#920: "the scratch file deleted").
        bounce_ = std::move(b);
        json j = reply_ok(msg, json{{"path", path}, {"frames", len},
                                    {"status", "started"}});
        j["type"] = "clip_to_asset";
        return j;
    }
    bounce_render(*b, b->nrender);
    if (std::string e = bounce_write(*b, code); !e.empty()) {
        err = e;
        return nullptr;
    }
    json j = reply_ok(msg, json{{"path", path}, {"frames", len}});
    j["type"] = "clip_to_asset";
    return j;
}

bool AudioHost::bounce_render(Bounce& b, int64_t n) {
    const int64_t end = std::min(b.nrender, b.done + std::max<int64_t>(0, n));
    const int64_t from = b.done;
    for (int64_t k = from; k < end; ++k) {
        const int64_t f = b.start + k;
        float l = 0.0f, r = 0.0f;
        for (int t = 0; t < Tape::kTracks; ++t) {
            if (!b.all && t != b.only) continue;
            float tl = 0.0f, tr = 0.0f;
            for (const Clip& c : b.clips[t]) {
                if (f < c.start_frame || f >= c.start_frame + c.len) continue;
                int64_t run = 0;
                const float* src = tape_run(
                    c.src_track >= 0 && c.src_track < Tape::kTracks ? c.src_track : t,
                    c.src_offset + (f - c.start_frame), run);
                if (!src) continue;
                tl += src[0] * c.gain;
                tr += src[1] * c.gain;
            }
            if (b.all && b.eq[t].active()) {
                b.eq[t].process(0, &tl, 1);
                b.eq[t].process(1, &tr, 1);
            }
            l += tl * b.gl[t];
            r += tr * b.gr[t];
        }
        if (b.all) {
            if (b.drive_amt > 0.0f) {
                b.drive.process(0, &l, 1, b.drive_amt, b.drive_tone, double(opt_.sr));
                b.drive.process(1, &r, 1, b.drive_amt, b.drive_tone, double(opt_.sr));
            }
            l *= b.master * b.bal_l;
            r *= b.master * b.bal_r;
            if (b.limiter) {
                const float mag = std::max(std::abs(l), std::abs(r));
                const float want = mag > 0.95f ? 0.95f / mag : 1.0f;
                b.lim += (want - b.lim) * (want < b.lim ? 0.2f : 0.0005f);
                l *= b.lim;
                r *= b.lim;
            }
        }
        b.out.ch[0][std::size_t(k)] = l;
        b.out.ch[1][std::size_t(k)] = r;
    }
    tape_unpin();
    // The rack runs over what was just mixed, in order, so a chunked bounce
    // and a whole one hand it the same frames in the same sequence.
    if (b.fx.chain && end > from)
        master_fx_run(b.fx, b.out.ch[0].data() + from, b.out.ch[1].data() + from,
                      std::size_t(end - from));
    b.done = end;
    return b.done >= b.nrender;
}

std::string AudioHost::bounce_write(Bounce& b, std::string& code) {
    if (b.lat > 0) {
        for (std::vector<float>& ch : b.out.ch) {
            ch.erase(ch.begin(), ch.begin() + std::size_t(b.lat));
            ch.resize(std::size_t(b.len), 0.0f);
        }
    }
    if (std::string e = arb::io::write(b.path, b.out, 16); !e.empty()) {
        code = "write_failed";
        return e;
    }
    lib_.rescan();                          // the browser lists it without a restart
    return {};
}

// One chunk of a bounce job (#920), a quarter of a second of tape: on the
// CM4 a few milliseconds of this thread, so a message waits at most that
// long behind it, and the keys never do (they have their own thread).
void AudioHost::bounce_step() {
    if (!bounce_) return;
    Bounce& b = *bounce_;
    if (!bounce_render(b, std::max<int64_t>(1, int64_t(opt_.sr) / kBounceChunkDiv)))
        return;
    std::string code;
    if (std::string e = bounce_write(b, code); !e.empty()) {
        bounce_end(json{{"type", "bounce"}, {"error", e}, {"code", code}});
        return;
    }
    bounce_end(json{{"type", "bounce"}, {"path", b.path}, {"frames", b.len}});
}

void AudioHost::bounce_end(const json& push) {
    bounce_.reset();
    events_.push_back(push);
}

// FILE › BOUNCE's MASTER FX switch (screen-sketches § FILE, stage J1). A
// mixdown leaves COMP and the master rack out (PROTOCOL.md "Tape and mixer"):
// COMP is a live performance knob, and whether the rack is printed is the
// player's choice at the moment of the bounce. `master_fx: true` is that
// choice, and these two are what it costs.
//
// The rack is rebuilt rather than borrowed. `voice_[kMasterBus].cur` is the
// audio thread's chain and rendering it from here is exactly the race the
// command queue exists to prevent; building a second one from the same §6.7
// document is cheap (it is the same work `load_rack` does) and leaves the
// live sound untouched, which matters because a bounce can be asked for while
// the transport is rolling.
uint32_t AudioHost::master_fx_latency() const {
    const InstState& m = inst_[kMasterBus];
    if (m.rack.bypass) return 0;
    uint32_t n = 0;
    for (std::size_t t = 0; t < m.tabs.size(); ++t) {
        if (t < m.rack.tabs.size() && m.rack.tabs[t].bypass) continue;
        n += m.tabs[t].manifest.value("latency_frames", 0u);
    }
    return n;
}

std::string AudioHost::master_fx_offline(std::vector<float>& l, std::vector<float>& r) {
    MasterFxRun fx;
    if (std::string e = master_fx_build(fx); !e.empty()) return e;
    if (fx.chain) master_fx_run(fx, l.data(), r.data(), std::min(l.size(), r.size()));
    return {};
}

std::string AudioHost::master_fx_build(MasterFxRun& fx) {
    const InstState& m = inst_[kMasterBus];
    if (m.rack.tabs.empty()) return {};

    auto chain = std::make_unique<Chain>();
    chain->alloc(opt_.max_block);
    std::vector<SlotState> tabs(m.rack.tabs.size());
    for (std::size_t t = 0; t < m.rack.tabs.size(); ++t) {
        SlotState& st = tabs[t];
        st.id = m.rack.tabs[t].engine;
        st.min_version = m.rack.tabs[t].min_version;
        for (const auto& [id, v] : m.rack.tabs[t].params) st.params[id] = v;
        for (const auto& [key, path] : m.rack.tabs[t].assets)
            st.assets.push_back(AssetRef{key, path});
        InstancePtr i;
        if (Refusal ref = build_slot(st, i); !ref.ok())
            return ref.code + (ref.detail.empty() ? "" : ": " + ref.detail);
        i->instance_id = m.rack.tabs[t].instance;
        chain->tabs.push_back(std::move(i));
    }
    std::vector<std::vector<std::string>> pids;
    for (const SlotState& t : tabs) pids.push_back(t.pids);
    fx.ctl = build_ctl(m.rack, pids);
    fx.chain = std::move(chain);
    // The macro positions as the bounce was asked for: a job renders over
    // many turns and a knob turned meanwhile is not in it (#920).
    fx.macro = m.macro;

    // No events, no sidechains and not playing: a bounce is a render of what
    // is on the tape, so there is no clock to hand a delay and no live bus for
    // a `tape:n` or `input` sidechain to read. `side_ptrs` leaves those NULL
    // and a box that asked for one simply sees no `side[]`.
    fx.hb = hb_;
    fx.hb.playing = 0;
    fx.hb.song_frame = 0;
    const uint32_t mb = opt_.max_block;
    fx.in_l.assign(mb, 0.0f);
    fx.in_r.assign(mb, 0.0f);
    fx.out_l.assign(mb, 0.0f);
    fx.out_r.assign(mb, 0.0f);
    return {};
}

void AudioHost::master_fx_run(MasterFxRun& fx, float* l, float* r, std::size_t n) {
    ChainInput ci;
    const uint32_t mb = opt_.max_block;
    ci.bus[0] = fx.in_l.data();
    ci.bus[1] = fx.in_r.data();
    float* outp[2] = {fx.out_l.data(), fx.out_r.data()};
    for (std::size_t p = 0; p < n; p += mb) {
        const uint32_t k = uint32_t(std::min<std::size_t>(mb, n - p));
        std::copy_n(l + p, k, fx.in_l.data());
        std::copy_n(r + p, k, fx.in_r.data());
        render_chain(*fx.chain, fx.ctl.get(), fx.macro.data(), nullptr, nullptr, false,
                     fx.hb, ci, outp, k);
        std::copy_n(fx.out_l.data(), k, l + p);
        std::copy_n(fx.out_r.data(), k, r + p);
    }
}

// One request's body -- everything a `clip_peaks` reply carries except the
// envelope. The single form and the `reqs` batch (#169) both come through
// here, so a batched element is answered by exactly the same code, cache and
// sidecar as a lone one.
json AudioHost::peaks_one(const json& msg, std::string& code, std::string& err) {
    const int t = msg.value("track", -1);
    if (t < 0 || t >= Tape::kTracks) { code = "bad_request"; err = "track must be 0..7"; return nullptr; }
    const int st = msg.value("src_track", t);
    if (st < 0 || st >= Tape::kTracks) { code = "bad_request"; err = "src_track must be 0..7"; return nullptr; }
    const int64_t off = msg.value("src_offset", msg.value("start_frame", int64_t(0)));
    const int64_t len = msg.value("len", int64_t(0));
    const int bins = std::clamp(msg.value("bins", 256), 1, 65536);
    if (len <= 0 || off < 0) { code = "bad_request"; err = "clip_peaks needs len > 0 and src_offset >= 0"; return nullptr; }

    const int64_t head = tape_.head[st].load(std::memory_order_relaxed);
    const bool settled = off + len <= head;
    const std::string key = std::to_string(st) + ":" + std::to_string(medium_gen_[st]) + ":" +
                            std::to_string(off) + ":" + std::to_string(len) + ":" + std::to_string(bins);
    if (settled) {
        if (auto it = peak_cache_.find(key); it != peak_cache_.end()) {
            json body = it->second;
            body["track"] = t;          // the cache is keyed by the *source*
            return body;
        }
    }
    json mn = json::array(), mx = json::array();
    // The peaks sidecar (#163, design § 3.6): the I/O thread worked these bins
    // out as it committed or fetched each range, so a draw at or below its
    // resolution neither reads the card nor scans a sample. It refuses
    // everything it cannot answer exactly and the scan below is what answers
    // then -- which is why this is a fast path and not a behaviour.
    std::vector<float> smn, smx;
    const bool sidecar = tape_io_.peaks_for(st, off, len, bins, smn, smx);
    if (sidecar) {
        for (std::size_t i = 0; i < smn.size(); ++i) {
            mn.push_back(arb::manifest::nice(smn[i]));
            mx.push_back(arb::manifest::nice(smx[i]));
        }
    }
    for (int b = 0; !sidecar && b < bins; ++b) {
        int64_t a = off + int64_t(uint64_t(b) * uint64_t(len) / uint64_t(bins));
        int64_t e = off + int64_t(uint64_t(b + 1) * uint64_t(len) / uint64_t(bins));
        e = std::max(e, a + 1);                 // fewer frames than bins: repeat the nearest
        float lo = 0.0f, hi = 0.0f;
        bool any = false;
        // Run at a time: a bin can span a range boundary, and the pointer is
        // only good to the end of its range (C1; step 6 puts a fetch here).
        for (int64_t f = a; f < e;) {
            int64_t run = 0;
            const float* src = tape_run(st, f, run);
            if (run <= 0) break;                // past the end of the medium
            const int64_t take = std::min(run, e - f);
            if (!src) { f += take; continue; }  // absent or never written: silence
            for (int64_t i = 0; i < take * 2; ++i) {
                const float v = src[std::size_t(i)];
                if (!any) { lo = hi = v; any = true; }
                lo = std::min(lo, v);
                hi = std::max(hi, v);
            }
            f += take;
        }
        mn.push_back(arb::manifest::nice(lo));
        mx.push_back(arb::manifest::nice(hi));
    }
    if (!sidecar) tape_unpin();
    // `source` says which path answered, because wall time cannot: a cache-warm
    // cell of the #162 rig has to be able to show that the card was not asked.
    json body{{"track", t}, {"src_track", st}, {"src_offset", off}, {"len", len},
              {"bins", bins}, {"source", sidecar ? "sidecar" : "audio"},
              {"min", mn}, {"max", mx}};
    if (settled) {
        if (peak_cache_.size() >= 256) peak_cache_.clear();
        peak_cache_[key] = body;
    }
    return body;
}

// `clip_peaks` takes either one request (the fields inline) or a **batch** of
// them under `reqs`, and answers a `peaks` array in the same order (#169).
// A redraw of TRACK is one message per lane, and #163's bench cell measured a
// round trip at 2.1-2.5 ms on the CM4 *with the host's own reply cache warm* --
// the fixed cost of the message itself, four of which is ~9 ms of a 35 ms
// frame. Batching removes three of the four. The elements are answered
// independently, in order, on the same thread, so nothing about one request's
// answer changes; only the number of round trips does.
//
// A bad element fails the whole batch (naming its index) rather than coming
// back as a hole: the caller computed every element of it in one pass from its
// own geometry, so one bad element means that pass is wrong, and a per-element
// error object would have every client writing the same loop to find it.
json AudioHost::clip_peaks(const json& msg, std::string& code, std::string& err) {
    if (msg.contains("reqs")) {
        code = "bad_request";
        const json& reqs = msg["reqs"];
        if (!reqs.is_array() || reqs.empty()) { err = "reqs must be a non-empty array"; return nullptr; }
        if (reqs.size() > kPeaksBatchMax) {
            err = "reqs holds at most " + std::to_string(kPeaksBatchMax) + " requests";
            return nullptr;
        }
        json out = json::array();
        for (std::size_t i = 0; i < reqs.size(); ++i) {
            if (!reqs[i].is_object()) {
                err = "reqs[" + std::to_string(i) + "] must be an object";
                return nullptr;
            }
            json body = peaks_one(reqs[i], code, err);
            if (body.is_null()) { err = "reqs[" + std::to_string(i) + "]: " + err; return nullptr; }
            out.push_back(std::move(body));
        }
        json j = reply_ok(msg, json{{"peaks", out}});
        j["type"] = "clip_peaks";
        return j;
    }
    json body = peaks_one(msg, code, err);
    if (body.is_null()) return nullptr;
    json j = reply_ok(msg, body);
    j["type"] = "clip_peaks";
    return j;
}

// `tape_edit`: the clip ops that change *audio*, not geometry -- REV, SPEED
// and FILTER tapped while stopped (story 12), and TAPE joining clips that are
// not contiguous in the medium. Sources are mixed into one run, the op is
// applied, and the result is appended at the destination track's head; the
// reply is where it landed, and the UI points a clip at it. The DSP is tape.py's
// (reverse, linear-interpolated resample, one-pole LP/HP), so a baked clip
// sounds as it did in the emulator. Control thread; refused while a take is
// open, because the take is writing at the same head.
//
// **It streams (C1 step 6, design § 3.6 "A `tape_edit`").** One destination
// range at a time: the run window that range needs is mixed out of the
// sources, the op is applied to it, the range is written, committed and
// released. The working set is a handful of ranges, not the edit's length,
// which is what § 3.6 asks for -- and what a bounded pool needs, because the
// whole-buffer version asked for one slot per range of the *result*: a
// five-range reverse against a three-slot window wrote nothing, reported
// success, and left the take replaced by silence (`acquire` hands back
// "no slot", `write_frames` returns short, and the commit then wrote the
// zeroes of an absent range). It also cost two buffers the length of the
// edit -- ~230 MB for a five-minute reverse -- on a machine chosen for
// having little RAM.
//
// Every op is streamable and the arithmetic is unchanged, frame for frame:
// `reverse` reads its source ranges last-to-first, `filter` carries its one
// pole across the seam, `speed` indexes the run by the same global `k` it
// always did, and the sources are mixed in the same order into the same
// frames. The goldens and host_clips.py's four op assertions are the proof.
json AudioHost::tape_edit(const json& msg, std::string& code, std::string& err) {
    commit_take();
    code = "bad_request";
    const int t = msg.value("track", -1);
    if (t < 0 || t >= Tape::kTracks) { err = "track must be 0..7"; return nullptr; }
    if (recording_) { err = "RECORDING"; return nullptr; }
    const int64_t total = msg.value("len", int64_t(0));
    if (total <= 0 || total > tape_.capacity()) { err = "tape_edit needs 0 < len <= capacity"; return nullptr; }
    const json sources = msg.value("sources", json::array());
    if (!sources.is_array() || sources.empty()) { err = "tape_edit needs sources"; return nullptr; }

    const int64_t head = tape_.head[t].load(std::memory_order_relaxed);

    struct Source {
        int track;
        int64_t off, len, at, limit;
        float gain;
    };
    std::vector<Source> srcs;
    srcs.reserve(sources.size());
    for (const json& sj : sources) {
        Source s;
        s.track = sj.value("track", t);
        if (s.track < 0 || s.track >= Tape::kTracks) { err = "source track must be 0..7"; return nullptr; }
        s.off = sj.value("offset", int64_t(0));
        s.len = sj.value("len", int64_t(0));
        s.at = sj.value("at", int64_t(0));
        s.gain = getf(sj, "gain", 1.0f);
        // A source on the *destination* track reads only what the track has:
        // the whole-buffer version read every source before it wrote anything,
        // and streaming interleaves the two, so a source running past the head
        // would read the edit's own output back. Past the head is not audio the
        // tape claims (it is what the next take will overwrite), so it is
        // silence here -- which is what an unwritten medium returned anyway.
        s.limit = s.track == t ? head : std::numeric_limits<int64_t>::max();
        srcs.push_back(s);
    }

    const std::string op = gets(msg, "op", "copy");
    // Validated before a frame is written, because the writing now starts
    // before the last source has been read.
    const bool rev = op == "reverse", spd = op == "speed", flt = op == "filter";
    if (!rev && !spd && !flt && op != "copy") {
        err = "op must be copy, reverse, speed or filter";
        return nullptr;
    }
    // tape.py resample: `factor`x faster -- shorter and higher (DDR-017).
    const double factor = spd ? std::max(0.05, double(getf(msg, "factor", 1.0f))) : 1.0;
    const double cutoff = std::max(1.0, double(getf(msg, "cutoff", 2000.0f)));
    const bool hp = gets(msg, "mode", "LP") == "HP";
    const float a1 = float(std::clamp(1.0 - std::exp(-2.0 * 3.14159265358979323846 * cutoff / double(opt_.sr)),
                                      1e-5, 1.0));
    const int64_t frames =
        spd ? std::max<int64_t>(1, int64_t(std::llround(double(total) / factor))) : total;

    if (head + frames > tape_.capacity() || !tape_.ensure(t)) {
        code = "tape_full";
        err = "TAPE FULL";
        return nullptr;
    }
    // Bounded, the head's own range comes back off the card first (otherwise
    // the zeroes of a fresh slot would be committed over the audio in front of
    // the head) -- and it is **held** until the first chunk commits it. The
    // whole-buffer version read every source before it fetched that range, so
    // nothing ran between the fetch and the write; streaming reads sources
    // between the two, and one of those fetches evicts it. Then `acquire`
    // hands out a zeroed slot and the commit writes the zeroes over the audio
    // in front of the head -- exactly the fault `tape_prepare_head` exists to
    // prevent, arriving by a different door. The test that found it is four
    // edits in a row against a three-slot window: the second one took the tail
    // of the first with it.
    tape_prepare_head(t, head);
    tape_io_.hold_span(t, head, 1);

    std::vector<float> run, out;
    // Mix the sources into `buf` for run frames [from, from + n), which is the
    // whole-buffer version's inner loop with a window around it: same sources,
    // same order, same frames, so the same float sum lands in each one.
    const auto fill_run = [&](int64_t from, int64_t n, std::vector<float>& buf) {
        buf.assign(std::size_t(std::max<int64_t>(0, n)) * 2, 0.0f);
        if (n <= 0) return;
        for (const Source& s : srcs) {
            if (!tape_.allocated(s.track)) continue;    // never written: silence
            const int64_t d0 = std::max({s.at, from, int64_t(0)});
            int64_t d1 = std::min({s.at + s.len, total, from + n});
            if (s.limit != std::numeric_limits<int64_t>::max())
                d1 = std::min(d1, s.at + std::max<int64_t>(0, s.limit - s.off));
            for (int64_t d = d0; d < d1;) {
                int64_t span = 0;
                const float* src = tape_run(s.track, s.off + (d - s.at), span);
                if (span <= 0) break;
                const int64_t take = std::min(span, d1 - d);
                if (!src) { d += take; continue; }      // absent or never written
                for (int64_t i = 0; i < take; ++i) {
                    const std::size_t w = std::size_t(d - from + i) * 2;
                    buf[w] += src[std::size_t(i) * 2] * s.gain;
                    buf[w + 1] += src[std::size_t(i) * 2 + 1] * s.gain;
                }
                d += take;
            }
        }
        tape_unpin();
    };

    // The op, over output frames [a, a + n), into `out`. `filter` is the one
    // with state, and the loop below walks `a` forwards, so its pole carries.
    float y[2] = {0.0f, 0.0f};
    const auto emit = [&](int64_t a, int64_t n) {
        if (rev) {
            fill_run(total - a - n, n, run);
            out.assign(std::size_t(n) * 2, 0.0f);
            for (int64_t f = 0; f < n; ++f) {
                const std::size_t r = std::size_t(n - 1 - f);
                out[std::size_t(f) * 2] = run[r * 2];
                out[std::size_t(f) * 2 + 1] = run[r * 2 + 1];
            }
        } else if (spd) {
            out.assign(std::size_t(n) * 2, 0.0f);
            // One output frame reads `factor` input frames, so the run window
            // is bounded by taking the output in pieces: at 8x a whole range
            // of output would want eight ranges of run, and the point of this
            // function is that nothing here scales with the edit's length.
            const int64_t piece = std::clamp<int64_t>(
                int64_t(double(Tape::kRangeFrames) / std::max(1.0, factor)), 1, n);
            for (int64_t k0 = 0; k0 < n; k0 += piece) {
                const int64_t k1 = std::min(k0 + piece, n);
                const int64_t lo = int64_t(std::clamp(double(a + k0) * factor, 0.0, double(total - 1)));
                const int64_t hi = std::min(
                    int64_t(std::clamp(double(a + k1 - 1) * factor, 0.0, double(total - 1))) + 1,
                    total - 1);
                fill_run(lo, hi - lo + 1, run);
                for (int64_t k = k0; k < k1; ++k) {
                    const double idx = std::clamp(double(a + k) * factor, 0.0, double(total - 1));
                    const int64_t i0 = int64_t(idx);
                    const int64_t i1 = std::min(i0 + 1, total - 1);
                    const float fr = float(idx - double(i0));
                    for (int ch = 0; ch < 2; ++ch)
                        out[std::size_t(k) * 2 + std::size_t(ch)] =
                            run[std::size_t(i0 - lo) * 2 + std::size_t(ch)] * (1.0f - fr) +
                            run[std::size_t(i1 - lo) * 2 + std::size_t(ch)] * fr;
                }
            }
        } else if (flt) {
            fill_run(a, n, run);
            out.assign(std::size_t(n) * 2, 0.0f);
            for (int ch = 0; ch < 2; ++ch) {
                for (int64_t f = 0; f < n; ++f) {
                    const float v = run[std::size_t(f) * 2 + std::size_t(ch)];
                    y[ch] += a1 * (v - y[ch]);
                    out[std::size_t(f) * 2 + std::size_t(ch)] = hp ? v - y[ch] : y[ch];
                }
            }
        } else {
            fill_run(a, n, out);                 // copy: the run *is* the output
        }
    };

    int64_t written = 0;
    while (written < frames) {
        const int64_t at = head + written;
        // One range at a time, so a chunk is a commit and the commit is a
        // range -- `persist_span` writes each one exactly once.
        const int64_t n = std::min(frames - written, Tape::kRangeFrames - at % Tape::kRangeFrames);
        emit(written, n);
        tape_io_.hold_span(t, at, n);
        int64_t did = tape_.write_frames(t, at, n, out.data());
        if (did < n) {
            // No slot. The I/O thread tops the free list up every turn and
            // `flush` is a turn, so one retry is the difference between "the
            // pool is momentarily empty" and "this edit cannot be written".
            tape_io_.flush();
            did += tape_.write_frames(t, at + did, n - did, out.data() + std::size_t(did) * 2);
        }
        tape_io_.persist_span(t, at, did);
        written += did;
        if (did < n) {
            tape_io_.release_span(t, at + did, n - did);   // never written: not dirty
            break;
        }
    }
    if (written <= 0) {
        code = "tape_full";
        err = "TAPE FULL";
        return nullptr;
    }
    tape_.head[t].store(head + written, std::memory_order_relaxed);
    state_dirty_ = true;
    json j = reply_ok(msg);
    j["type"] = "tape_edit";
    j["track"] = t;
    j["src_offset"] = head;
    j["len"] = written;
    return j;
}

// `tape_compact` (C1 step 8, design § 3.8): the medium is append-only, so a
// retake, a LIFT with no DROP and every `tape_edit` leave audio nothing plays.
// This is how the disk comes back.
//
// **The UI says what to keep and the host moves the bytes**, because the clip
// list is the UI's and it holds two references this process has never seen --
// the lift buffer and the one-level undo record (DDR-054/055). Compact those
// away and undo brings back a clip that plays somebody else's audio. So the
// keep set arrives with the message, the copy keeps exactly those spans, and
// the map goes back for the UI to remap its own geometry through.
//
// **It is two messages on the wire**, not one. A 2 x ruler medium is ~7 s of
// copying on the eMMC, and § 3.8's own rule is that SAVE *starts* a compaction
// rather than waiting for one -- so this replies `started` and the result
// arrives as a push (`tape_compact` with `map`, or with `cancelled`, or with
// `error`), the way `xrun` and `tape_late` do. Offline there is no I/O thread
// and the copy runs inside `compact_start`, but the shape on the wire does not
// change: the push is simply already waiting at the next `poll()`.
json AudioHost::tape_compact(const json& msg, std::string& code, std::string& err) {
    commit_take();
    code = "bad_request";
    const int t = msg.value("track", -1);
    if (t < 0 || t >= Tape::kTracks) { err = "track must be 0..7"; return nullptr; }
    if (!tape_io_.on()) { err = "NO TAPE DIR"; return nullptr; }
    if (recording_) { err = "RECORDING"; return nullptr; }
    // § 3.8: "runs only while the transport is stopped", so the copy never
    // competes with read-ahead for the card.
    if (playing_) { err = "PLAYING"; return nullptr; }
    if (tape_io_.compact_state() != TapeIo::CompactState::kIdle) {
        err = "BUSY";
        return nullptr;
    }
    // #920: a bounce job reads the offsets this copy is about to move.
    if (bounce_) { err = "BUSY"; return nullptr; }
    const json keep = msg.value("keep", json::array());
    if (!keep.is_array()) { err = "tape_compact needs \"keep\""; return nullptr; }
    std::vector<KeepSpan> spans;
    spans.reserve(keep.size());
    for (const json& k : keep) {
        KeepSpan s;
        s.from = k.value("src_offset", int64_t(0));
        s.len = k.value("len", int64_t(0));
        if (s.len > 0) spans.push_back(s);
    }
    // An empty keep set is legal and means it: § 3.8's "a track whose `keep`
    // is empty is truncated to its header". It is what a player who deleted
    // every clip on a track and saved has asked for.
    int64_t dead = 0;
    if (!tape_io_.compact_start(t, std::move(spans), &dead)) {
        code = "compact_failed";
        err = tape_io_.compact_error();
        if (err.empty()) err = "COMPACT FAILED";
        tape_io_.compact_clear();
        return nullptr;
    }
    compacting_ = t;
    json j = reply_ok(msg);
    j["type"] = "tape_compact";
    j["track"] = t;
    j["started"] = true;
    // #167: `dead` came back from `compact_start` itself. Asking
    // `compact_freed()` for it takes `mu_`, which the copy's thread holds
    // between slices -- and that one field is what turned § 3.8's two-message
    // design back into a reply that waited out the whole seven seconds.
    j["dead_frames"] = dead;
    return j;
}

// ---------------------------------------------------------------------------
// where the media live (C1 step 3b, #157): `tape_dir {dir, move}`
// ---------------------------------------------------------------------------
// Elfy's answer on #157 is (a): SAVE **moves** the tape files into the project
// folder and recording carries on there, and SAVE means "save the project I
// have open". This is the audio process's half of that. `move` is SAVE's
// rename; without it this is LOAD's open-in-place, which replaces `tape_load`'s
// read-the-whole-file-into-RAM for a medium that is already one of ours.
//
// Both stop at the same rules as `tape_load`: no take open, and the clip lists
// that read the old media are the caller's to replace -- send `set_clips`
// after an open, as after a `tape_load`. A compaction is cancelled first
// (§ 3.8: SAVE starts one, and the next SAVE may arrive while it runs); its
// `tape_compact` push says so and the UI retries at the next trigger.
json AudioHost::tape_dir(const json& msg, std::string& code, std::string& err) {
    const std::string dir = gets(msg, "dir");
    const bool move = msg.value("move", false);
    if (dir.empty()) {
        code = "bad_request";
        err = "tape_dir needs \"dir\"";
        return nullptr;
    }
    // #920: opening another folder's media takes away the tape a bounce job
    // reads; SAVE's `move` renames the same media, so that one carries on.
    if (!move && bounce_) bounce_end(json{{"type", "bounce"}, {"cancelled", true}});
    if (!tape_io_.on()) {
        code = "bad_request";
        err = "NO TAPE DIR";
        return nullptr;
    }
    commit_take();
    if (recording_) {
        code = "bad_request";
        err = "RECORDING";
        return nullptr;
    }
    cancel_compaction();
    if (!opt_.tape_store_fake) {
        // A fake card keeps its files in RAM and has no folders to make.
        std::error_code ec;
        std::filesystem::create_directories(dir, ec);
        if (ec) {
            code = "write_failed";
            err = "cannot create " + dir + ": " + ec.message();
            return nullptr;
        }
    }
    int64_t frames[Tape::kTracks];
    if (!tape_io_.set_dir(dir, move, frames, code, err)) return nullptr;
    if (!move) adopt_media(frames);
    json fr = json::array();
    json refused = json::array();
    for (int t = 0; t < Tape::kTracks; ++t) {
        fr.push_back(std::max<int64_t>(0, move ? tape_.head[t].load(std::memory_order_relaxed)
                                               : frames[t]));
        if (frames[t] < 0) refused.push_back(t);
    }
    state_dirty_ = true;
    json j = reply_ok(msg);
    j["dir"] = tape_io_.dir();
    j["moved"] = move;
    j["frames"] = fr;
    j["refused"] = refused;
    return j;
}

// The heads after an open-in-place: each track's medium is what its repaired
// header claims, and a track with no file (or a refused one) has none. Every
// generation moves, because the peak cache is keyed by it and every medium
// may be a different file now.
void AudioHost::adopt_media(const int64_t frames[Tape::kTracks]) {
    for (int t = 0; t < Tape::kTracks; ++t) {
        const int64_t f = std::min<int64_t>(std::max<int64_t>(0, frames[t]), tape_.capacity());
        if (f > 0) tape_.ensure(t);
        tape_.head[t].store(f, std::memory_order_relaxed);
        ++medium_gen_[t];
    }
    state_dirty_ = true;
}

// PLAY or REC arriving (§ 3.8). The scratch file goes and the old medium has
// not been touched, so the cost is the copying already done; the retry is the
// next trigger's. Silent when nothing is running, which is every call but one.
void AudioHost::cancel_compaction() {
    if (compacting_ < 0) return;
    tape_io_.compact_cancel();
}

// ---------------------------------------------------------------------------
// the library as files (#62): `library {op}`
// ---------------------------------------------------------------------------
// DDR-055 makes every browser a file manager, and stage J2 built that in the
// UI (`emulator/app/library_ops.py`): the moves are real, `.trash/` is the
// soft delete, and stock content does not move. The companion app talks to
// *this* process, not to the UI, so the same rules have to be reachable from
// the protocol -- that is what these are. The UI keeps its gesture layer
// (lift, drop, the undo group); what lands here is the file operation itself.
namespace {

// A library-relative path made absolute under `root`, or an empty path when it
// would escape. An absolute path and any `..` component are refused; a
// dot-prefixed component is *not*, because `.trash/` is a real folder and
// putting an item back is naming it (the HTTP file server refuses dot
// components at its own edge -- device-os-spec.md § 9.3).
std::filesystem::path under_root(const std::filesystem::path& root, const std::string& rel) {
    if (rel.empty()) return {};
    std::filesystem::path p(rel);
    if (p.is_absolute()) return {};
    for (const std::filesystem::path& part : p)
        if (part == ".." || part == ".") return {};
    return (root / p).lexically_normal();
}

// The path a reply names: back to library-relative, forward slashes.
std::string rel_to(const std::filesystem::path& root, const std::filesystem::path& p) {
    return p.lexically_relative(root).generic_string();
}

// `<root>/stock.json` -- the library-relative paths that came in the image
// (`audio/library/README.md` § Stock, written by `audio/tools/mkstock.py`).
// Nothing on disk tells a shipped patch from one the player recorded, so the
// manifest is the only honest answer; no manifest means nothing is stock.
std::set<std::string> read_stock(const std::filesystem::path& root) {
    std::set<std::string> out;
    std::ifstream f(root / "stock.json");
    if (!f) return out;
    json doc;
    try {
        f >> doc;
    } catch (const std::exception&) {
        return out;
    }
    if (!doc.is_object() || doc.value("format", 0) != 1) return out;
    const json items = doc.value("items", json::array());
    if (!items.is_array()) return out;
    for (const json& it : items)
        if (it.is_string()) out.insert(it.get<std::string>());
    return out;
}

// `name`, then `name-2`, `name-3`, ... inside `dir`. This is `free_path` in
// `library_ops.py`: two lifts of the same file name must both survive in
// `.trash/`, so a trash collision renames rather than refusing.
std::filesystem::path free_path(const std::filesystem::path& dir, const std::string& name) {
    std::error_code ec;
    std::filesystem::path c = dir / name;
    if (!std::filesystem::exists(std::filesystem::symlink_status(c, ec))) return c;
    const std::filesystem::path p(name);
    const std::string stem = p.stem().string(), ext = p.extension().string();
    for (int n = 2; n < 1000; ++n) {
        c = dir / (stem + "-" + std::to_string(n) + ext);
        if (!std::filesystem::exists(std::filesystem::symlink_status(c, ec))) return c;
    }
    return {};
}

}  // namespace

// The last `--library` root: see the declaration for why it is the last.
std::string AudioHost::write_root() const {
    return lib_.roots().empty() ? std::string{} : lib_.roots().back();
}

json AudioHost::library_op(const json& msg, std::string& code, std::string& err) {
    namespace fs = std::filesystem;
    code = "bad_request";
    const std::string op = gets(msg, "op");
    if (op != "rename" && op != "move" && op != "delete" && op != "trash" && op != "mkdir") {
        err = "op must be rename, move, delete, trash or mkdir";
        return nullptr;
    }
    if (lib_.roots().empty()) { err = "NO LIBRARY"; return nullptr; }
    // The writable root -- the same choice `clip_to_asset` makes when it has
    // to invent a path (`write_root`).
    const fs::path root = fs::path(write_root()).lexically_normal();
    std::error_code ec;

    // Every op rescans before it answers, and the reply names where the item
    // is now (empty for `delete`) beside the updated box list.
    auto done = [&](const fs::path& at) {
        lib_.rescan();                // the index and the browser stay in step
        state_dirty_ = true;
        json engines = json::array();
        for (const arb::box::Entry& e : lib_.all())
            engines.push_back(json{{"id", e.id}, {"version", e.version}, {"dir", e.dir}});
        json j = reply_ok(msg);
        j["op"] = op;
        j["path"] = at.empty() ? std::string{} : rel_to(root, at);
        j["engines"] = engines;
        return j;
    };

    // #979 (D104): New folder. `path` is the folder to make; the one above it
    // must already be a folder (no parents invented), and `engines/` holds
    // installed boxes only, `engines/<author>/<name>`, so nothing is made there.
    if (op == "mkdir") {
        const fs::path dir = under_root(root, gets(msg, "path"));
        if (dir.empty() || dir.filename().empty()) { err = "BAD PATH"; return nullptr; }
        const std::string name = dir.filename().string();
        if (name[0] == '.') { err = "BAD PATH"; return nullptr; }  // never indexed
        if (*dir.lexically_relative(root).begin() == "engines") { err = "ENGINES"; return nullptr; }
        if (fs::exists(fs::symlink_status(dir, ec))) { err = "EXISTS"; return nullptr; }
        if (!fs::is_directory(dir.parent_path(), ec)) {
            code = "not_found";
            err = "NOT FOUND";
            return nullptr;
        }
        if (!fs::create_directory(dir, ec)) {
            code = "write_failed";
            err = ec ? ec.message() : "EXISTS";
            return nullptr;
        }
        return done(dir);
    }

    const fs::path src = under_root(root, gets(msg, "path"));
    if (src.empty()) { err = "BAD PATH"; return nullptr; }
    if (!fs::exists(fs::symlink_status(src, ec))) { code = "not_found"; err = "NOT FOUND"; return nullptr; }

    // Every op here changes the item, so stock is refused for all four: a
    // shipped patch that can be deleted but not renamed would be a stranger
    // rule than one that simply does not move.
    const std::set<std::string> stock = read_stock(root);
    if (stock.count(rel_to(root, src))) { err = "STOCK"; return nullptr; }

    // D106: the last engine of a kind does not uninstall. Delete and trash
    // are the two ways one leaves the index; an item that holds no box (a
    // patch, a folder of patches) passes, and so does a box with a twin of
    // its kind outside what is going.
    if (op == "delete" || op == "trash") {
        const std::vector<arb::box::Entry> all = lib_.all();
        const fs::path going_path = fs::weakly_canonical(src, ec);
        auto under = [&](const std::string& dir) {
            const fs::path d = fs::weakly_canonical(fs::path(dir), ec);
            return std::mismatch(going_path.begin(), going_path.end(), d.begin(), d.end()).first ==
                   going_path.end();
        };
        static const char* const kKinds[] = {"synth", "drum", "effect", "sequencer"};
        for (const char* kind : kKinds) {
            bool going = false, staying = false;
            for (const arb::box::Entry& e : all) {
                if (e.kind != kind) continue;
                (under(e.dir) ? going : staying) = true;
            }
            if (going && !staying) {
                code = "last_engine";
                std::string k = kind;
                for (char& c : k) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
                err = "NEEDS ONE " + k + " ENGINE";
                return nullptr;
            }
        }
    }

    // #297: the open project's folder is where the tape is being written (C1
    // step 3b), so the folder, any folder above it and anything in it stay put
    // -- a renamed folder would leave the I/O thread writing to a path that
    // moved. Here rather than in each client, because the UI's browser, the
    // phone's rename and the HTTP DELETE all arrive as this message.
    if (tape_io_.on() && !tape_io_.dir().empty()) {
        const fs::path live = fs::weakly_canonical(fs::path(tape_io_.dir()), ec);
        const fs::path item = fs::weakly_canonical(src, ec);
        const auto [a, b] = std::mismatch(item.begin(), item.end(), live.begin(), live.end());
        const bool above_or_is = a == item.end();
        const bool inside = b == live.end();
        // #821 (D99): the phone renames the open project. The folder itself
        // (not one above it, not a file in it), by `rename` only: the I/O
        // thread renames it under its own lock and re-points, the handles
        // follow, and the UI is told by the `tape_dir_renamed` push so its
        // autosave follows too. Not during a take: its tail and header are
        // still being written.
        if (op == "rename" && above_or_is && b == live.end() && !opt_.tape_store_fake) {
            const std::string name = gets(msg, "name");
            if (name.empty() || name == "." || name == ".." ||
                name.find('/') != std::string::npos || name.find('\\') != std::string::npos) {
                err = "rename needs \"name\", one path component";
                return nullptr;
            }
            commit_take();
            if (recording_) {
                code = "open_project";
                err = "RECORDING";
                return nullptr;
            }
            cancel_compaction();
            const std::string from = tape_io_.dir();
            const fs::path to = src.parent_path() / name;
            if (!tape_io_.rename_dir(to.string(), code, err)) return nullptr;
            events_.push_back(json{{"type", "tape_dir_renamed"},
                                   {"from", from}, {"to", tape_io_.dir()}});
            return done(to);
        }
        if (above_or_is || inside) {
            code = "open_project";
            err = "OPEN ON THE INSTRUMENT";
            return nullptr;
        }
    }

    fs::path dst;
    if (op == "rename") {
        const std::string name = gets(msg, "name");
        if (name.empty() || name == "." || name == ".." ||
            name.find('/') != std::string::npos || name.find('\\') != std::string::npos) {
            err = "rename needs \"name\", one path component";
            return nullptr;
        }
        dst = src.parent_path() / name;
    } else if (op == "move") {
        const fs::path to = under_root(root, gets(msg, "to"));
        // `to` is the destination *folder*: a move is into somewhere, which is
        // what DROP is. The root itself is a legal destination.
        const fs::path dir = gets(msg, "to").empty() ? root : to;
        if (dir.empty()) { err = "BAD PATH"; return nullptr; }
        if (!fs::is_directory(dir, ec)) { code = "not_found"; err = "NOT FOUND"; return nullptr; }
        dst = dir / src.filename();
    } else if (op == "trash") {
        const fs::path dir = root / ".trash";
        fs::create_directories(dir, ec);
        if (ec) { code = "write_failed"; err = "cannot create " + dir.string(); return nullptr; }
        dst = free_path(dir, src.filename().string());
        if (dst.empty()) { code = "write_failed"; err = "no free name in .trash/"; return nullptr; }
    }

    if (op == "delete") {
        fs::remove_all(src, ec);
        if (ec) { code = "write_failed"; err = ec.message(); return nullptr; }
    } else {
        if (dst == src) { err = "EXISTS"; return nullptr; }
        // `trash` already picked a free name; `rename` and `move` refuse
        // rather than invent one, because the caller named the destination.
        if (op != "trash" && fs::exists(fs::symlink_status(dst, ec))) { err = "EXISTS"; return nullptr; }
        fs::rename(src, dst, ec);
        if (ec) {
            // Across devices `rename` fails where a copy would do; the library
            // is one tree, so this is a real failure and not a fallback.
            code = "write_failed";
            err = ec.message();
            return nullptr;
        }
    }

    return done(op == "delete" ? fs::path{} : dst);
}

json AudioHost::handle(const json& msg) {
    if (!msg.is_object()) return reply_err(msg, "bad_request", "message is not an object");
    const std::string type = gets(msg, "type");
    if (type.empty()) return reply_err(msg, "bad_request", "message has no \"type\"");

    auto inst_of = [&]() {
        const std::string s = gets(msg, "instrument");
        if (s == "drum") return 1;
        if (s == "synth") return 0;
        // §15.5: the master bus is a rack and nothing else. Every rack message
        // takes it; the instrument-only ones refuse it (not_master, below).
        if (s == "master") return int(kMasterBus);
        return active_;
    };
    // An instrument-only message naming the master bus is a bug in the client,
    // not a silent fallback to the active instrument. One list, checked once:
    // everything else that takes an `instrument` is a rack message and the
    // master bus answers it like SYNTH or DRUM.
    if (gets(msg, "instrument") == "master") {
        static constexpr const char* kInstrumentOnly[] = {
            "load_engine", "unload_engine", "load_patch", "load_kit", "set_note_param",
            "copy_pad", "waveform", "add_mod_tab", "set_mod_tab_engine", "remove_mod_tab",
            "route", "unroute", "set_route", "set_mod_sidechain", "set_mod_bypass",
            "host_param", "env", "bend", "instrument"};
        for (const char* t : kInstrumentOnly)
            if (type == t)
                return reply_err(msg, "bad_request",
                                 "the master bus is an FX rack only (§15.5)", type);
    }

    // #597: admission. A message that may change the state is refused before
    // it touches the mirror unless the ring to the audio thread has room for
    // every command it can push, so a reply of ok and a pushed state document
    // are never ahead of the audio. The heaviest message pushes ~24 commands
    // (`load_patch`: SetChain, SetCtl, 8 macros, ModCtl, SOURCE's 1 + 3, the
    // envelope's 8); the rest of kAdmitHeadroom is left for the host's own
    // pushes (`poll`, the IMU). A full ring means the audio thread is stalled,
    // or, --offline, that no `render` has drained it. The messages below never
    // push, and `render` is what drains the ring, so they are never refused.
    static constexpr const char* kNeverBusy[] = {
        "hello", "state", "shutdown", "library", "rescan", "waveform",
        "clip_peaks", "tape_dir", "meters", "render", "bounce_cancel"};
    if (std::find(std::begin(kNeverBusy), std::end(kNeverBusy), type) == std::end(kNeverBusy) &&
        cmds_.free_slots() < kAdmitHeadroom)
        return reply_err(msg, "busy", "BUSY", "the audio thread is not taking commands");

    // ---- handshake ---------------------------------------------------------
    if (type == "hello") {
        const int proto = msg.value("proto", 0);
        const int abi = msg.value("abi", 0);
        if (proto != kProtoVersion || abi < kMinClientAbi || abi > ARB_ABI)
            return reply_err(msg, "needs_firmware_update", "NEEDS FIRMWARE UPDATE",
                             "client proto " + std::to_string(proto) + " abi " +
                                 std::to_string(abi) + ", host proto " +
                                 std::to_string(kProtoVersion) + " abi " +
                                 std::to_string(kMinClientAbi) + ".." + std::to_string(ARB_ABI));
        hello_ok_ = true;
        json j = reply_ok(msg);
        j["type"] = "hello";
        j["proto"] = kProtoVersion;
        // `abi` answers with the level the client asked for, which is the one
        // this connection's documents are readable at; `box_abi` is what boxes
        // must be built against. See kMinClientAbi.
        j["abi"] = abi;
        j["box_abi"] = ARB_ABI;
        j["firmware"] = kFirmware;
        j["sample_rate"] = opt_.sr;
        j["block"] = opt_.block;
        j["offline"] = opt_.offline;
        // § 6.5, added field like `box_abi`: a client that does not read it
        // loses nothing. The state document carries the same value, which is
        // the one that can change; this is here so the first frame drawn
        // knows before `state` has been asked for.
        j["motion_sensor"] = motion_sensor_;
        return j;
    }
    if (type == "state") {
        // The pushed document and the requested one are the same object; a
        // request additionally carries "req" back so a client can match it.
        json j = state_doc();
        if (msg.contains("req")) j["req"] = msg["req"];
        return j;
    }
    if (type == "shutdown") { quit_ = true; return reply_ok(msg); }
    if (type == "library") {
        // `library {}` lists the boxes; `library {op}` moves files (#62). One
        // message, because the reply is the same list either way -- a rename
        // can retire a box id and the caller needs the new index anyway.
        if (msg.contains("op")) {
            std::string code, err;
            json j = library_op(msg, code, err);
            if (j.is_null()) return reply_err(msg, code, err);
            return j;
        }
        json v = json::array();
        for (const arb::box::Entry& e : lib_.all())
            v.push_back(json{{"id", e.id}, {"version", e.version}, {"dir", e.dir}});
        json j = reply_ok(msg);
        j["engines"] = v;
        return j;
    }
    if (type == "rescan") { lib_.rescan(); state_dirty_ = true; return reply_ok(msg); }

    // ---- slots -------------------------------------------------------------
    if (type == "load_engine" || type == "unload_engine") {
        const int i = inst_of();
        const int sl = slot_of(gets(msg, "slot", "engine"));
        if (sl == 2) return reply_err(msg, "bad_request", "use add_tab/set_tab_engine for the FX slot");
        if (sl == 3) return reply_err(msg, "bad_request", "use add_mod_tab/set_mod_tab_engine for a MOD tab");
        InstState p = inst_[i];
        SlotState& st = sl == 0 ? p.seq : p.engine;
        if (type == "unload_engine" || gets(msg, "id").empty()) {
            st = SlotState{};
        } else {
            const std::string id = gets(msg, "id");
            // A different engine has different parameter ids and different
            // asset keys; carrying either over would refuse the load (§7.4).
            if (st.id != id) { st.params.clear(); st.assets.clear(); }
            st.loaded = true;
            st.id = id;
            st.min_version = gets(msg, "min_version");
            // Content with the engine (§ Assets, DDR-037): a sequencer arrives
            // with its pattern the way an FX tab arrives with its impulse.
            // Present = the whole map, so `{}` is "no assets"; absent = leave
            // what the slot already had.
            if (msg.contains("assets") && msg["assets"].is_object()) {
                const std::string adir = gets(msg, "dir");
                st.assets.clear();
                for (auto it = msg["assets"].begin(); it != msg["assets"].end(); ++it)
                    if (it.value().is_string())
                        st.assets.push_back(AssetRef{
                            it.key(), resolve_asset(it.value().get<std::string>(), adir)});
            }
        }
        if (Refusal r = apply(i, std::move(p)); !r.ok()) return reply_refusal(msg, r);
        return reply_ok(msg);
    }

    if (type == "load_rack") {
        const int i = inst_of();
        RackDoc d;
        const json& rj = msg.contains("rack") ? msg["rack"] : msg;
        if (Refusal r = rack_from_json(rj, d); !r.ok()) return reply_refusal(msg, r);
        const std::string rdir = gets(msg, "dir", gets(rj, "dir"));
        for (TabDoc& t : d.tabs)
            for (auto& [key, path] : t.assets) path = resolve_asset(path, rdir);
        InstState p = inst_[i];
        p.rack = std::move(d);
        if (Refusal r = apply(i, std::move(p)); !r.ok()) return reply_refusal(msg, r);
        json j = reply_ok(msg);
        j["rack"] = inst_[i].rack.to_json();
        return j;
    }

    if (type == "load_patch") {
        const int i = inst_of();
        const json& pj = msg.contains("patch") ? msg["patch"] : msg;
        if (Refusal r = load_patch(i, pj); !r.ok()) return reply_refusal(msg, r);
        return reply_ok(msg);
    }

    if (type == "load_kit") {
        const int i = msg.contains("instrument") ? inst_of() : 1;   // the DRUM instrument
        const json& kj = msg.contains("kit") ? msg["kit"] : msg;
        if (Refusal r = load_kit(i, kj); !r.ok()) return reply_refusal(msg, r);
        json j = reply_ok(msg);
        j["kit_path"] = inst_[i].kit_path;
        return j;
    }

    if (type == "set_param") {
        const int i = inst_of();
        const int sl = slot_of(gets(msg, "slot", "engine"));
        const int tab = msg.value("tab", 0);
        const int idx = msg.value("idx", -1);
        const float v = arb::clamp01(getf(msg, "value", 0.0f));
        SlotState* st = sl == 0 ? &inst_[i].seq : sl == 1 ? &inst_[i].engine : nullptr;
        if (sl == 2) {
            if (tab < 0 || tab >= int(inst_[i].tabs.size()))
                return reply_err(msg, "not_found", "no such rack tab");
            st = &inst_[i].tabs[std::size_t(tab)];
        }
        if (sl == 3) {
            if (tab < 0 || tab >= int(inst_[i].mod_tabs.size()))
                return reply_err(msg, "not_found", "no such MOD tab");
            st = &inst_[i].mod_tabs[std::size_t(tab)];
        }
        if (!st || !st->loaded) return reply_err(msg, "not_found", "that slot is empty");
        if (idx < 0 || idx >= int(st->pids.size()))
            return reply_err(msg, "not_found", "parameter index out of range");
        st->params[st->pids[std::size_t(idx)]] = v;
        // DDR-063: SOURCE on `arbutus/live-input` is the host's input routing
        // wearing a knob, so the ordinary encoder path moves the routing and
        // the ENGINE page needs no special case for this box.
        if (sl == 1 && st->id == kLiveInputId && st->pids[std::size_t(idx)] == kLiveInputSource)
            set_input_source(int(std::lround(v * float(kNumSources - 1))), st);
        if (sl == 2) {
            TabDoc& td = inst_[i].rack.tabs[std::size_t(tab)];
            td.params.clear();
            for (const auto& [id, val] : st->params) td.params.emplace_back(id, val);
        }
        if (sl == 3) {
            TabDoc& td = inst_[i].mod.tabs[std::size_t(tab)];
            td.params.clear();
            for (const auto& [id, val] : st->params) td.params.emplace_back(id, val);
        }
        Cmd c{};
        c.k = Ck::SetParam;
        c.i0 = i;
        c.i1 = sl;
        c.i2 = tab;
        c.i3 = idx;
        c.f0 = v;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "set_note_param") {
        // §15.3 / §15.6: one pad's value of a per_note param. The kit document
        // holds it (so `state` reports it and a saved kit keeps it); the engine
        // gets it between blocks.
        const int i = inst_of();
        if (slot_of(gets(msg, "slot", "engine")) != 1)
            return reply_err(msg, "bad_request", "set_note_param addresses the ENGINE slot");
        InstState& s = inst_[i];
        if (!s.engine.loaded) return reply_err(msg, "not_found", "that slot is empty");
        int pad = msg.contains("pad") ? msg.value("pad", -1) : msg.value("note", -1) - kKitBaseNote;
        if (pad < 0 || pad >= kKitPads) return reply_err(msg, "bad_request", "pad must be 0..23 (note 53..76)");
        int idx = msg.value("idx", -1);
        if (msg.contains("id")) {
            const auto it = std::find(s.engine.pids.begin(), s.engine.pids.end(), gets(msg, "id"));
            idx = it == s.engine.pids.end() ? -1 : int(it - s.engine.pids.begin());
        }
        if (idx < 0 || idx >= int(s.engine.pids.size()))
            return reply_err(msg, "not_found", "parameter index out of range");
        const json dp = s.engine.manifest.value("params", json::array());
        if (idx >= int(dp.size()) || !dp[std::size_t(idx)].value("per_note", false))
            return reply_err(msg, "bad_request", s.engine.pids[std::size_t(idx)] + " is not a per_note param");
        const std::string key = std::to_string(pad);
        if (!s.kit.is_object() || !s.kit.contains("pads") || !s.kit["pads"].contains(key))
            return reply_err(msg, "not_found", "no kit pad " + key);
        const float v = arb::clamp01(getf(msg, "value", 0.0f));
        json& pj = s.kit["pads"][key];
        if (pj.is_string()) pj = json{{"file", pj}, {"params", json::object()}};
        pj["params"][s.engine.pids[std::size_t(idx)]] = arb::manifest::nice(v);
        Cmd c{};
        c.k = Ck::SetNoteParam;
        c.i0 = i;
        c.i2 = kKitBaseNote + pad;
        c.i3 = idx;
        c.f0 = v;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "copy_pad") {
        // LIFT on a pad, DROP on another (§15.3, DDR-039): file and params. The
        // file changes, so the kit reloads -- crossfaded like any kit change.
        const int i = msg.contains("instrument") ? inst_of() : 1;
        const int from = msg.value("from", -1), to = msg.value("to", -1);
        if (from < 0 || from >= kKitPads || to < 0 || to >= kKitPads)
            return reply_err(msg, "bad_request", "from and to must be pads 0..23");
        const InstState& s = inst_[i];
        if (!s.kit.is_object() || !s.kit.contains("pads") || !s.kit["pads"].contains(std::to_string(from)))
            return reply_err(msg, "not_found", "no kit pad " + std::to_string(from));
        json kit = s.kit;
        kit["pads"][std::to_string(to)] = kit["pads"][std::to_string(from)];
        if (from != to)
            if (Refusal r = load_kit(i, kit); !r.ok()) return reply_refusal(msg, r);
        json j = reply_ok(msg);
        j["kit"] = inst_[i].kit;
        return j;
    }

    if (type == "waveform") {
        // §15.6: by path, by a slot's asset key, or by a kit pad.
        const int i = inst_of();
        std::string path;
        if (msg.contains("path")) {
            path = resolve_asset(gets(msg, "path"), gets(msg, "dir"));
        } else if (msg.contains("pad") || msg.contains("note")) {
            const int pad = msg.contains("pad") ? msg.value("pad", -1) : msg.value("note", -1) - kKitBaseNote;
            path = kit_pad_file(inst_[i], std::to_string(pad));
            if (path.empty()) return reply_err(msg, "not_found", "no kit pad " + std::to_string(pad));
        } else {
            const int sl = slot_of(gets(msg, "slot", "engine"));
            const int tab = msg.value("tab", 0);
            const InstState& s = inst_[i];
            const SlotState* st = sl == 0 ? &s.seq : &s.engine;
            if (sl == 2) st = (tab >= 0 && tab < int(s.tabs.size())) ? &s.tabs[std::size_t(tab)] : nullptr;
            if (sl == 3) st = (tab >= 0 && tab < int(s.mod_tabs.size())) ? &s.mod_tabs[std::size_t(tab)] : nullptr;
            if (!st || !st->loaded) return reply_err(msg, "not_found", "that slot is empty");
            const std::string key = gets(msg, "key");
            for (const AssetRef& a : st->assets)
                if (a.key == key || (key.empty() && st->assets.size() == 1)) path = a.path;
            if (path.empty()) return reply_err(msg, "not_found", "no asset \"" + key + "\" on that slot");
        }
        // 1..4096: a screen is 320 px wide; the top end is for the app.
        const int bins = std::clamp(msg.value("bins", 256), 1, 4096);
        std::string err;
        json j = waveform(msg, path, bins, err);
        if (j.is_null()) return reply_err(msg, "not_found", err);
        return j;
    }

    if (type == "set_macro") {
        const int i = inst_of();
        const int s = msg.value("slot", 0);
        if (s < 0 || s > 7) return reply_err(msg, "bad_request", "macro slot must be 0..7");
        const float v = arb::clamp01(getf(msg, "value", 0.0f));
        inst_[i].macro[std::size_t(s)] = v;
        Cmd c{};
        c.k = Ck::SetMacro;
        c.i0 = i;
        c.i1 = s;
        c.f0 = v;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    // ---- rack edits (§6.3-6.6). Every one replies with the whole document,
    // so the UI never computes rack state itself. -------------------------
    auto rack_reply = [&](int i) {
        json j = reply_ok(msg);
        j["type"] = "rack";
        // the master bus answers rack edits too (§15.5), and must say so:
        // a client that merged this reply under "drum" would lose DRUM's rack.
        j["instrument"] = i == 0 ? "synth" : (i == 1 ? "drum" : "master");
        j["rack"] = inst_[i].rack.to_json();
        j["tabs"] = instrument_doc(i)["tabs"];
        return j;
    };
    auto rebuild_ctl = [&](int i) {
        std::vector<std::vector<std::string>> pids;
        for (const SlotState& t : inst_[i].tabs) pids.push_back(t.pids);
        Cmd c{};
        c.k = Ck::SetCtl;
        c.i0 = i;
        c.p = build_ctl(inst_[i].rack, pids).release();
        push(c);
        state_dirty_ = true;
    };
    auto macro_at = [&](InstState& s, int slot) -> MacroDoc& {
        for (MacroDoc& m : s.rack.macros)
            if (m.slot == slot) return m;
        s.rack.macros.push_back(MacroDoc{slot, "", {}});
        return s.rack.macros.back();
    };

    if (type == "bind") {
        const int i = inst_of();
        const int slot = msg.value("slot", 0);
        if (slot < 0 || slot > 7) return reply_err(msg, "bad_request", "macro slot must be 0..7");
        const std::string instance = gets(msg, "instance");
        const std::string param = gets(msg, "param");
        const int ti = inst_[i].rack.tab_index(instance);
        if (ti < 0) return reply_err(msg, "not_found", "no rack tab named \"" + instance + "\"");
        const std::vector<std::string>& pids = inst_[i].tabs[std::size_t(ti)].pids;
        if (std::find(pids.begin(), pids.end(), param) == pids.end())
            return reply_err(msg, "not_found", instance + " has no parameter \"" + param + "\"");
        MacroTarget t;
        t.instance = instance;
        t.param = param;
        t.lo = getf(msg, "lo", 0.0f);
        t.hi = getf(msg, "hi", 1.0f);
        t.curve = curve_from(gets(msg, "curve", "lin"));
        t.depth = getf(msg, "depth", 1.0f);
        MacroDoc& m = macro_at(inst_[i], slot);
        if (msg.contains("name")) m.name = gets(msg, "name");
        if (m.name.empty()) m.name = param;
        m.targets.push_back(std::move(t));       // a second DROP multi-assigns (DDR-039)
        rebuild_ctl(i);
        return rack_reply(i);
    }

    if (type == "unbind") {
        const int i = inst_of();
        const int slot = msg.value("slot", 0);
        const int target = msg.value("target", -1);
        for (std::size_t k = 0; k < inst_[i].rack.macros.size(); ++k) {
            MacroDoc& m = inst_[i].rack.macros[k];
            if (m.slot != slot) continue;
            if (target >= 0 && target < int(m.targets.size()))
                m.targets.erase(m.targets.begin() + target);
            else
                m.targets.clear();
            if (m.targets.empty()) inst_[i].rack.macros.erase(inst_[i].rack.macros.begin() + long(k));
            break;
        }
        rebuild_ctl(i);
        return rack_reply(i);
    }

    if (type == "set_range") {
        const int i = inst_of();
        const int slot = msg.value("slot", 0);
        const int target = msg.value("target", 0);
        for (MacroDoc& m : inst_[i].rack.macros) {
            if (m.slot != slot) continue;
            if (target < 0 || target >= int(m.targets.size()))
                return reply_err(msg, "not_found", "no such macro target");
            MacroTarget& t = m.targets[std::size_t(target)];
            t.lo = getf(msg, "lo", t.lo);
            t.hi = getf(msg, "hi", t.hi);
            if (msg.contains("curve")) t.curve = curve_from(gets(msg, "curve"));
            t.depth = getf(msg, "depth", t.depth);
            if (msg.contains("name")) m.name = gets(msg, "name");
            rebuild_ctl(i);
            return rack_reply(i);
        }
        return reply_err(msg, "not_found", "macro slot is not bound");
    }

    if (type == "set_sidechain") {
        const int i = inst_of();
        const int idx = msg.value("index", -1);
        if (idx < 0 || idx >= int(inst_[i].rack.tabs.size()))
            return reply_err(msg, "not_found", "no such rack tab");
        const std::string src = gets(msg, "source", "none");
        const int s = side_from(src);
        if (s == kSideNone && src != "none" && !src.empty())
            return reply_err(msg, "bad_request", "source must be none, tape:1..8, input, self or master");
        if (s != kSideNone && !(inst_[i].tabs[std::size_t(idx)].manifest.value("caps", json::array())
                                    .dump().find("sidechain") != std::string::npos))
            return reply_err(msg, "bad_request",
                             inst_[i].rack.tabs[std::size_t(idx)].engine +
                                 " does not declare ARB_CAP_SIDECHAIN (§6.6)");
        inst_[i].rack.tabs[std::size_t(idx)].sidechain = s;
        rebuild_ctl(i);
        return rack_reply(i);
    }

    if (type == "set_bypass") {
        // DDR-049: key 3 on the master tab bypasses the rack, on an engine
        // tab it bypasses only that node. This is a control-table swap, so
        // effect tails and engine state survive the A/B gesture.
        const int i = inst_of();
        const int idx = msg.value("index", -1);
        const bool on = msg.value("bypass", true);
        if (idx < 0) {
            inst_[i].rack.bypass = on;
        } else {
            if (idx >= int(inst_[i].rack.tabs.size()))
                return reply_err(msg, "not_found", "no such rack tab");
            inst_[i].rack.tabs[std::size_t(idx)].bypass = on;
        }
        rebuild_ctl(i);
        return rack_reply(i);
    }

    if (type == "add_tab" || type == "set_tab_engine" || type == "remove_tab") {
        const int i = inst_of();
        InstState p = inst_[i];
        const int idx = msg.value("index", -1);
        if (type == "remove_tab") {
            if (idx < 0 || idx >= int(p.rack.tabs.size()))
                return reply_err(msg, "not_found", "no such rack tab");
            const std::string gone = p.rack.tabs[std::size_t(idx)].instance;
            p.rack.tabs.erase(p.rack.tabs.begin() + idx);
            for (MacroDoc& m : p.rack.macros)
                m.targets.erase(std::remove_if(m.targets.begin(), m.targets.end(),
                                               [&](const MacroTarget& t) { return t.instance == gone; }),
                                m.targets.end());
            p.rack.macros.erase(std::remove_if(p.rack.macros.begin(), p.rack.macros.end(),
                                               [](const MacroDoc& m) { return m.targets.empty(); }),
                                p.rack.macros.end());
        } else if (type == "set_tab_engine") {
            if (idx < 0 || idx >= int(p.rack.tabs.size()))
                return reply_err(msg, "not_found", "no such rack tab");
            TabDoc& td = p.rack.tabs[std::size_t(idx)];
            const std::string want = gets(msg, "engine", td.engine);
            if (want != td.engine) {
                // The tab's name follows its box (#329): reverb scrolled to past
                // biquad is `reverb_1`, not `biquad_1`, and every address that
                // names the tab is carried over to the new name.
                const std::string was = td.instance;
                td.instance.clear();               // its own old name is free again
                td.instance = p.rack.unique_instance(want);
                for (MacroDoc& m : p.rack.macros)
                    for (MacroTarget& t : m.targets)
                        if (t.instance == was) t.instance = td.instance;
                const std::string from = "fx." + was + ".";
                for (RouteDoc& r : p.mod.routes)
                    if (r.target.rfind(from, 0) == 0)
                        r.target = "fx." + td.instance + "." + r.target.substr(from.size());
            }
            td.engine = want;
            td.min_version = gets(msg, "min_version");
            td.params.clear();                     // a different engine, different params
            td.sidechain = kSideNone;
            td.assets.clear();                     // ...and other asset keys
        } else {
            if (p.rack.tabs.size() >= 8)
                return reply_err(msg, "bad_request", "a rack holds at most 8 engines (§12)");
            TabDoc td;
            td.engine = gets(msg, "engine");
            if (td.engine.empty()) return reply_err(msg, "bad_request", "add_tab needs \"engine\"");
            td.instance = gets(msg, "instance");
            if (td.instance.empty() || p.rack.tab_index(td.instance) >= 0)
                td.instance = p.rack.unique_instance(td.engine);
            td.min_version = gets(msg, "min_version");
            const json pj = msg.value("params", json::object());
            for (auto it = pj.begin(); it != pj.end(); ++it)
                if (it.value().is_number()) td.params.emplace_back(it.key(), it.value().get<float>());
            // Content, as a MOD tab takes it (§3.2): resolved like a patch's.
            const json aj = msg.value("assets", json::object());
            if (aj.is_object())
                for (auto it = aj.begin(); it != aj.end(); ++it)
                    if (it.value().is_string())
                        td.assets.emplace_back(it.key(), resolve_asset(it.value().get<std::string>(),
                                                                       gets(msg, "dir")));
            if (idx >= 0 && idx <= int(p.rack.tabs.size()))
                p.rack.tabs.insert(p.rack.tabs.begin() + idx, std::move(td));
            else
                p.rack.tabs.push_back(std::move(td));
        }
        if (Refusal r = apply(i, std::move(p)); !r.ok()) return reply_refusal(msg, r);
        return rack_reply(i);
    }

    // ---- MOD rack edits (modulation-spec §2, §6, §10). Every one replies with
    // the whole §11 document, as the FX rack edits do. ---------------------
    auto mod_reply = [&](int i) {
        json j = reply_ok(msg);
        j["type"] = "mod";
        j["instrument"] = i == 0 ? "synth" : "drum";
        j["mod"] = inst_[i].mod.to_json();
        j["tabs"] = instrument_doc(i)["mod_tabs"];
        return j;
    };
    auto route_refusal = [&](const Refusal& r) {
        if (r.code == "not_yet") return reply_err(msg, "needs_firmware_update", "NEEDS FIRMWARE UPDATE", r.detail);
        return reply_err(msg, r.code, r.detail);
    };
    auto window = [&](RouteDoc& rd) {
        // A number sets the bound; null clears it (§6.2 "unset = the whole range").
        for (const char* k : {"lo", "hi"}) {
            if (!msg.contains(k)) continue;
            const bool set = msg[k].is_number();
            const float v = set ? msg[k].get<float>() : (k[0] == 'l' ? 0.0f : 1.0f);
            if (k[0] == 'l') { rd.has_lo = set; rd.lo = v; }
            else             { rd.has_hi = set; rd.hi = v; }
        }
    };

    if (type == "add_mod_tab" || type == "set_mod_tab_engine" || type == "remove_mod_tab") {
        const int i = inst_of();
        InstState p = inst_[i];
        const int idx = msg.value("index", -1);
        std::string swapped;
        if (type == "remove_mod_tab") {
            if (idx < 0 || idx >= int(p.mod.tabs.size()))
                return reply_err(msg, "not_found", "no such MOD tab");
            const std::string gone = p.mod.tabs[std::size_t(idx)].instance;
            p.mod.tabs.erase(p.mod.tabs.begin() + idx);
            // A route is lifted off with its source (§2.2): nothing left to read.
            p.mod.routes.erase(std::remove_if(p.mod.routes.begin(), p.mod.routes.end(),
                                              [&](const RouteDoc& r) { return r.source == gone; }),
                               p.mod.routes.end());
        } else if (type == "set_mod_tab_engine") {
            if (idx < 0 || idx >= int(p.mod.tabs.size()))
                return reply_err(msg, "not_found", "no such MOD tab");
            TabDoc& td = p.mod.tabs[std::size_t(idx)];
            const std::string want = gets(msg, "engine", td.engine);
            // The same rule `load_engine` uses for a slot: a *different* engine
            // has different parameter ids and different asset keys, so both go;
            // the **same** engine keeps them. That is what makes this message the
            // door content reaches an existing tab through (#60): the UI edits
            // arbutus/stepmod's step values and sends them back without resetting
            // the tab's STEPS/DIV/SLEW/DIR/RANGE or its sidechain.
            if (want != td.engine) {
                td.params.clear();
                td.sidechain = kSideNone;
                td.assets.clear();
                // The name follows the box (#329), and its routes follow the name.
                const std::string was = td.instance;
                td.instance.clear();
                td.instance = tabs_unique_instance(p.mod.tabs, want);
                for (RouteDoc& r : p.mod.routes)
                    if (r.source == was) r.source = td.instance;
            }
            td.engine = want;
            td.min_version = gets(msg, "min_version");
            // Content rides in like a patch's (§3.2), resolved the same way.
            // Present = the whole map, so `{}` is "no assets"; absent = leave
            // what the tab already had.
            if (msg.contains("assets") && msg["assets"].is_object()) {
                const std::string adir = gets(msg, "dir");
                td.assets.clear();
                for (auto it = msg["assets"].begin(); it != msg["assets"].end(); ++it)
                    if (it.value().is_string())
                        td.assets.emplace_back(it.key(),
                                               resolve_asset(it.value().get<std::string>(), adir));
            }
            swapped = td.instance;
        } else {
            if (p.mod.tabs.size() >= std::size_t(kMaxModTabs))
                return reply_err(msg, "mod_rack_full", "MOD RACK FULL",
                                 "a MOD rack holds at most 4 modulators (modulation-spec §2)");
            TabDoc td;
            td.engine = gets(msg, "engine");
            if (td.engine.empty()) return reply_err(msg, "bad_request", "add_mod_tab needs \"engine\"");
            td.instance = gets(msg, "instance");
            if (td.instance.empty() || td.instance == "keys" || p.mod.tab_index(td.instance) >= 0)
                td.instance = tabs_unique_instance(p.mod.tabs, td.engine);
            td.min_version = gets(msg, "min_version");
            const json pj = msg.value("params", json::object());
            for (auto it = pj.begin(); it != pj.end(); ++it)
                if (it.value().is_number()) td.params.emplace_back(it.key(), it.value().get<float>());
            // Content rides in like a patch's (§3.2), resolved the same way.
            const json aj = msg.value("assets", json::object());
            if (aj.is_object())
                for (auto it = aj.begin(); it != aj.end(); ++it)
                    if (it.value().is_string())
                        td.assets.emplace_back(it.key(), resolve_asset(it.value().get<std::string>(),
                                                                       gets(msg, "dir")));
            if (idx >= 0 && idx <= int(p.mod.tabs.size()))
                p.mod.tabs.insert(p.mod.tabs.begin() + idx, std::move(td));
            else
                p.mod.tabs.push_back(std::move(td));
        }
        if (Refusal r = apply(i, std::move(p)); !r.ok()) return reply_refusal(msg, r);
        if (!swapped.empty()) {
            // Routes from outputs the new engine does not have are lifted off;
            // the rest keep reading the same output id.
            InstState& s = inst_[i];
            const int t = s.mod.tab_index(swapped);
            json outs = t >= 0 ? s.mod_tabs[std::size_t(t)].manifest.value("outputs", json::array())
                               : json::array();
            auto has = [&](const std::string& id) {
                for (const json& o : outs) if (o.value("id", std::string{}) == id) return true;
                return false;
            };
            s.mod.routes.erase(std::remove_if(s.mod.routes.begin(), s.mod.routes.end(),
                                              [&](const RouteDoc& r) { return r.source == swapped && !has(r.output); }),
                               s.mod.routes.end());
            push_mod_ctl(i);
        }
        return mod_reply(i);
    }

    if (type == "route") {
        const int i = inst_of();
        // Four routes per rack: what four encoders on the master tab can own
        // (§2.2); a fifth DROP is refused with this strip line (§10).
        if (inst_[i].mod.routes.size() >= std::size_t(kMaxRoutes))
            return reply_err(msg, "mod_rack_full", "MOD RACK FULL",
                             "a MOD rack holds at most 4 routes (modulation-spec §2.2)");
        RouteDoc rd;
        rd.source = gets(msg, "source");
        rd.output = gets(msg, "output");
        rd.target = gets(msg, "target");
        rd.depth = getf(msg, "depth", rd.depth);
        rd.curve = curve_from(gets(msg, "curve", "lin"));
        window(rd);
        CtlRoute probe;
        if (Refusal r = resolve_route(inst_[i].mod, rd, mod_inputs(inst_[i]), probe); !r.ok())
            return route_refusal(r);
        const int at = msg.value("index", int(inst_[i].mod.routes.size()));
        if (at < 0 || at > int(inst_[i].mod.routes.size()))
            return reply_err(msg, "bad_request", "route index is out of range");
        inst_[i].mod.routes.insert(inst_[i].mod.routes.begin() + at,
                                   std::move(rd));
        push_mod_ctl(i);
        return mod_reply(i);
    }

    if (type == "unroute" || type == "set_route") {
        const int i = inst_of();
        std::vector<RouteDoc>& routes = inst_[i].mod.routes;
        int idx = msg.value("index", -1);
        if (!msg.contains("index"))
            for (std::size_t k = 0; k < routes.size(); ++k)
                if (routes[k].source == gets(msg, "source") && routes[k].output == gets(msg, "output") &&
                    routes[k].target == gets(msg, "target")) { idx = int(k); break; }
        if (idx < 0 || idx >= int(routes.size())) return reply_err(msg, "not_found", "no such route");
        if (type == "unroute") {
            routes.erase(routes.begin() + idx);
        } else {
            RouteDoc& rd = routes[std::size_t(idx)];
            rd.depth = getf(msg, "depth", rd.depth);
            if (msg.contains("curve")) rd.curve = curve_from(gets(msg, "curve"));
            window(rd);
        }
        push_mod_ctl(i);
        return mod_reply(i);
    }

    if (type == "set_mod_sidechain") {
        // set_sidechain's MOD twin (modulation-spec §4). Only the route table
        // is swapped, so a follower keeps its envelope across a source change.
        const int i = inst_of();
        const int idx = msg.value("index", -1);
        if (idx < 0 || idx >= int(inst_[i].mod.tabs.size()))
            return reply_err(msg, "not_found", "no such MOD tab");
        const std::string src = gets(msg, "source", "none");
        const int s = side_from(src);
        if (s == kSideNone && src != "none" && !src.empty())
            return reply_err(msg, "bad_request", "source must be none, tape:1..8, input, self or master");
        if (s != kSideNone && !(inst_[i].mod_tabs[std::size_t(idx)].manifest.value("caps", json::array())
                                    .dump().find("sidechain") != std::string::npos))
            return reply_err(msg, "bad_request",
                             inst_[i].mod.tabs[std::size_t(idx)].engine +
                                 " does not declare ARB_CAP_SIDECHAIN (modulation-spec §4)");
        inst_[i].mod.tabs[std::size_t(idx)].sidechain = s;
        push_mod_ctl(i);
        return mod_reply(i);
    }

    if (type == "set_mod_bypass") {
        // §2.3: key 4 on the master tab bypasses the rack, on a modulator tab
        // that tab. No index = the rack.
        const int i = inst_of();
        const int idx = msg.value("index", -1);
        const bool on = msg.value("bypass", true);
        if (idx < 0) {
            inst_[i].mod.bypass = on;
        } else {
            if (idx >= int(inst_[i].mod.tabs.size())) return reply_err(msg, "not_found", "no such MOD tab");
            inst_[i].mod.tabs[std::size_t(idx)].bypass = on;
        }
        push_mod_ctl(i);
        return mod_reply(i);
    }

    if (type == "host_param") {
        // The knobs host.level and host.pan swing around (modulation-spec §5).
        const int i = inst_of();
        InstState& st = inst_[i];
        const auto send = [&](int which, float v) {
            Cmd c{};
            c.k = Ck::HostParam;
            c.i0 = i;
            c.i1 = which;
            c.f0 = v;
            push(c);
        };
        if (msg.contains("level")) { st.host_level = arb::clamp01(getf(msg, "level", 1.0f)); send(0, st.host_level); }
        if (msg.contains("pan")) { st.host_pan = arb::clamp01(getf(msg, "pan", 0.5f)); send(1, st.host_pan); }
        if (msg.contains("pitch")) { st.host_pitch = arb::clamp01(getf(msg, "pitch", 0.5f)); send(2, st.host_pitch); }
        state_dirty_ = true;
        return reply_ok(msg);
    }

    // ---- play, transport, tape --------------------------------------------
    if (type == "note") {
        Cmd c{};
        c.k = Ck::Note;
        c.i0 = msg.value("on", true) ? 1 : 0;
        c.i1 = msg.value("note", 60);
        c.f0 = getf(msg, "vel", 1.0f);
        // `probe` (#73): the audio thread reports the block that carries this
        // note. Stamped here, on arrival, so a client can split its latency
        // into socket + parse and the wait for the next block.
        if (msg.contains("probe") && msg["probe"].is_number_integer()) {
            c.i2 = 1;
            c.i3 = msg["probe"].get<int32_t>();
            c.pos = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count();
        }
        push(c);
        return reply_ok(msg);
    }
    if (type == "panic") { Cmd c{}; c.k = Ck::AllNotesOff; push(c); return reply_ok(msg); }
    if (type == "reset") { Cmd c{}; c.k = Ck::ResetEngines; push(c); return reply_ok(msg); }

    if (type == "softkey") {
        Cmd c{};
        c.k = Ck::Softkey;
        c.i1 = msg.value("key", 0);
        c.i2 = msg.value("down", true) ? 1 : 0;
        c.i3 = msg.value("shift", false) ? 1 : 0;
        push(c);
        return reply_ok(msg);
    }

    if (type == "instrument") {
        const std::string a = gets(msg, "active", "synth");
        active_ = (a == "drum") ? 1 : 0;
        Cmd c{};
        c.k = Ck::Active;
        c.i0 = active_;
        push(c);
        Cmd off{};
        off.k = Ck::AllNotesOff;
        push(off);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "env") {
        const int i = inst_of();
        InstState& s = inst_[i];
        s.env_a = getf(msg, "a", s.env_a);
        s.env_d = getf(msg, "d", s.env_d);
        s.env_s = getf(msg, "s", s.env_s);
        s.env_r = getf(msg, "r", s.env_r);
        if (msg.contains("mode")) s.env_mode = env_mode_from(gets(msg, "mode"));
        s.env_glide = std::clamp(getf(msg, "glide", s.env_glide), 0.0f, kEnvMax[kEnvGlide]);
        s.env_detune = std::clamp(getf(msg, "detune", s.env_detune), 0.0f, kEnvMax[kEnvDetune]);
        s.env_bend_range = std::clamp(getf(msg, "bend_range", s.env_bend_range), 0.0f, kBendRangeMax);
        push_env(i);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "bend") {
        // MIDI bend and SHIFT+‹ › (DDR-052) arrive as one position, -1..1; the
        // host ramps toward it over GLIDE and resolves it into pitch_st. 0 is
        // the release.
        Cmd c{};
        c.k = Ck::Bend;
        c.i0 = inst_of();
        c.f0 = std::clamp(getf(msg, "value", 0.0f), -1.0f, 1.0f);
        push(c);
        return reply_ok(msg);
    }

    if (type == "tempo") {
        tempo_ = getf(msg, "bpm", float(tempo_));
        link_ = msg.value("link", link_);
        // The medium's own tempo. A restart sets it from the saved project;
        // nothing else moves it, because moving it would repitch the tape.
        if (msg.contains("base_bpm"))
            link_bpm_ = std::max(1.0, double(getf(msg, "base_bpm", float(link_bpm_))));
        Cmd c{};
        c.k = Ck::Tempo;
        c.f0 = float(tempo_);
        c.f1 = float(link_factor());
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "transport") {
        const std::string op = gets(msg, "op", "stop");
        if (op == "loop") {
            // IN / OUT / LOOP (story 10). The loop wraps the tape playhead
            // only; the clock keeps counting, as the emulator's did.
            loop_in_ = std::max<int64_t>(0, msg.value("in", loop_in_));
            loop_out_ = std::clamp<int64_t>(msg.value("out", loop_out_), 0, tape_.capacity());
            loop_on_ = msg.value("on", loop_on_);
            Cmd c{};
            c.k = Ck::Loop;
            c.i0 = loop_on_ ? 1 : 0;
            c.pos = loop_in_;
            c.i1 = int32_t(std::min<int64_t>(loop_out_, INT32_MAX));
            push(c);
            state_dirty_ = true;
            return reply_ok(msg);
        }
        Cmd c{};
        c.k = Ck::Transport;
        c.i0 = op == "start" ? 1 : op == "locate" ? 2 : 0;
        c.pos = msg.value("position", int64_t(0));
        if (c.i0 == 2) song_frame_ = c.pos;
        if (c.i0 == 1) { playing_ = true; cancel_compaction(); }
        if (c.i0 == 0) playing_ = false;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "record") {
        const int t = msg.value("track", armed_);
        const bool on = msg.value("on", true);
        if (on && (t < 0 || t >= Tape::kTracks))
            return reply_err(msg, "bad_request", "record needs a track 0..7");
        // Before `space_for_take` and before the head is fetched: a running
        // compaction is holding a scratch file the size of the medium, and the
        // take is about to append to the medium it is replacing (§ 3.8).
        if (on) cancel_compaction();
        // The take writes on the audio thread, which may not allocate, so the
        // medium is claimed here -- a track costs nothing until it is recorded.
        if (on && !tape_.ensure(t))
            return reply_err(msg, "tape_full", "TAPE FULL", "no memory for track " + std::to_string(t));
        // § 3.7 reason 2's prevention (Elfy, § 7 question 4: yes): a card with
        // less than twice the pool free is refused **before** the take rather
        // than ending one halfway through, which is the failure the player
        // cannot undo. No medium on storage, or a filesystem that will not
        // say, is not a refusal.
        if (on && !tape_io_.space_for_take())
            return reply_err(msg, "tape_full", "TAPE FULL", "not enough free space for a take");
        // A take appends at the head, which is usually inside a range that
        // already holds the end of the last one. Bounded, that range is fetched
        // here, on the thread that is allowed to wait for the card — the audio
        // thread would otherwise be handed a zeroed slot and the commit would
        // write those zeroes over the audio in front of the head.
        if (on) tape_prepare_head(t, tape_.head[t].load(std::memory_order_relaxed));
        Cmd c{};
        c.k = Ck::Record;
        c.i0 = on ? 1 : 0;
        c.i1 = t;
        push(c);
        if (on) { armed_ = t; recording_ = true; }
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "arm") {
        const int t = msg.value("track", -1);
        armed_ = (t >= 0 && t < Tape::kTracks) ? t : -1;
        Cmd c{};
        c.k = Ck::Arm;
        c.i0 = armed_;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "set_clips") {
        const int t = msg.value("track", -1);
        if (t < 0 || t >= Tape::kTracks) return reply_err(msg, "bad_request", "track must be 0..7");
        std::vector<Clip> v;
        for (const json& c : msg.value("clips", json::array())) {
            Clip cl;
            cl.start_frame = c.value("start_frame", int64_t(0));
            cl.len = c.value("len", int64_t(0));
            cl.src_offset = c.value("src_offset", cl.start_frame);
            cl.gain = getf(c, "gain", 1.0f);
            cl.src_track = c.value("src_track", t);
            if (cl.src_track < 0 || cl.src_track >= Tape::kTracks)
                return reply_err(msg, "bad_request", "src_track must be 0..7");
            if (cl.src_track == t) cl.src_track = -1;
            if (cl.len > 0) v.push_back(cl);
        }
        clips_[t] = v;
        auto cl = std::make_unique<ClipList>();
        cl->clips = v;
        Cmd c{};
        c.k = Ck::SetClips;
        c.i0 = t;
        c.p = cl.release();
        push(c);
        tape_io_.set_clips(t, v);          // step 5's read-ahead input
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "mixer") {
        auto send = [&](int which, float v) {
            Cmd c{};
            c.k = Ck::Mixer;
            c.i0 = which;
            c.f0 = v;
            push(c);
        };
        if (msg.contains("track")) {
            const int t = msg.value("track", -1);
            if (t < 0 || t >= Tape::kTracks) return reply_err(msg, "bad_request", "track must be 0..7");
            if (msg.contains("gain")) { track_gain_[t] = getf(msg, "gain", 1.0f); send(t, track_gain_[t]); }
            if (msg.contains("pan")) {
                track_pan_[t] = getf(msg, "pan", 0.0f);
                send(Tape::kTracks + t, track_pan_[t]);
            }
            // DDR-061: mute is the latch, solo the hold; both are per track and
            // solo wins wherever any track has it.
            if (msg.contains("mute")) {
                mute_[t] = msg.value("mute", false);
                Cmd c{};
                c.k = Ck::Mixer;
                c.i0 = kMixMute;
                c.i1 = t;
                c.f0 = mute_[t] ? 1.0f : 0.0f;
                push(c);
            }
            if (msg.contains("solo")) {
                solo_[t] = msg.value("solo", false);
                Cmd c{};
                c.k = Ck::Mixer;
                c.i0 = kMixSolo;
                c.i1 = t;
                c.f0 = solo_[t] ? 1.0f : 0.0f;
                push(c);
            }
        }
        if (msg.contains("master")) { master_ = getf(msg, "master", 1.0f); send(kMixMaster, master_); }
        if (msg.contains("limiter")) {
            limiter_ = msg.value("limiter", false);
            send(kMixLimiter, limiter_ ? 1.0f : 0.0f);
        }
        if (msg.contains("comp")) { comp_ = arb::clamp01(getf(msg, "comp", 0.0f)); send(kMixComp, comp_); }
        // The rest of the MASTER page (§15.5, DDR-062). COMP REL is seconds,
        // DRIVE and DRIVE TONE are 0..1, BAL is -1..+1; each default is the
        // value at which the strip does nothing at all.
        if (msg.contains("comp_rel")) {
            comp_rel_ = std::clamp(getf(msg, "comp_rel", 0.120f), 0.02f, 1.0f);
            send(kMixCompRel, comp_rel_);
        }
        // #933, the A/B for Elfy: "block" (today's, the default) or "labelled".
        if (msg.contains("comp_times")) {
            const std::string ct = msg.value("comp_times", std::string("block"));
            if (ct != "block" && ct != "labelled")
                return reply_err(msg, "bad_request", "comp_times is block or labelled");
            comp_labelled_ = ct == "labelled";
            send(kMixCompTimes, comp_labelled_ ? 1.0f : 0.0f);
        }
        if (msg.contains("drive")) {
            drive_ = arb::clamp01(getf(msg, "drive", 0.0f));
            send(kMixDrive, drive_);
        }
        if (msg.contains("drive_tone")) {
            drive_tone_ = arb::clamp01(getf(msg, "drive_tone", 0.5f));
            send(kMixDriveTone, drive_tone_);
        }
        if (msg.contains("bal")) {
            bal_ = std::clamp(getf(msg, "bal", 0.0f), -1.0f, 1.0f);
            send(kMixBal, bal_);
        }
        state_dirty_ = true;
        return reply_ok(msg);
    }

    // ---- per-track EQ (DDR-062) --------------------------------------------
    // `eq {track, low, mid, high, hp, lp, mid_f, mid_q}` (§15.5). Every field
    // is optional and what is not named does not move, so the page can send one
    // encoder. dB for the bands, Hz for the frequencies -- the same units the
    // page prints, not normalised: this is host DSP, not an engine (§3.2).
    if (type == "eq") {
        const int t = msg.value("track", -1);
        if (t < 0 || t >= Tape::kTracks) return reply_err(msg, "bad_request", "track must be 0..7");
        EqSettings& e = eq_[t];
        if (msg.contains("low")) e.low = getf(msg, "low", 0.0f);
        if (msg.contains("mid")) e.mid = getf(msg, "mid", 0.0f);
        if (msg.contains("high")) e.high = getf(msg, "high", 0.0f);
        if (msg.contains("mid_f")) e.mid_f = getf(msg, "mid_f", 1000.0f);
        if (msg.contains("mid_q")) e.mid_q = getf(msg, "mid_q", 0.7f);
        if (msg.contains("hp")) e.hp = getf(msg, "hp", kEqHpOff);
        if (msg.contains("lp")) e.lp = getf(msg, "lp", kEqLpOff);
        e.clamp();
        Cmd c{};
        c.k = Ck::Eq;
        c.i0 = t;
        c.m[0] = e.low;
        c.m[1] = e.mid;
        c.m[2] = e.high;
        c.m[3] = e.mid_f;
        c.m[4] = e.mid_q;
        c.m[5] = e.hp;
        c.f0 = e.lp;
        push(c);
        state_dirty_ = true;
        json j = reply_ok(msg);
        j["eq"] = json{{"low", arb::manifest::nice(e.low)}, {"mid", arb::manifest::nice(e.mid)},
                       {"high", arb::manifest::nice(e.high)},
                       {"mid_f", arb::manifest::nice(e.mid_f)},
                       {"mid_q", arb::manifest::nice(e.mid_q)},
                       {"hp", arb::manifest::nice(e.hp)}, {"lp", arb::manifest::nice(e.lp)}};
        return j;
    }

    if (type == "metronome") {
        click_level_ = arb::clamp01(getf(msg, "level", click_level_));
        click_pitch_ = std::clamp(getf(msg, "pitch", click_pitch_), 0.0f, 20000.0f);
        Cmd c{};
        c.k = Ck::Metronome;
        c.f0 = click_level_;
        c.f1 = click_pitch_;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "input_source") {
        // §15.6. `{source}` is one of mic / line / usb / resample, by name or by
        // index. It is the same value as `arbutus/live-input`'s SOURCE knob and
        // it is reported at `state.input.source`.
        int src = -1;
        if (msg.contains("source") && msg["source"].is_string()) src = source_from(gets(msg, "source"));
        else if (msg.contains("source") && msg["source"].is_number()) src = msg["source"].get<int>();
        if (src < 0 || src >= kNumSources)
            return reply_err(msg, "bad_request", "source is mic | line | usb | resample");
        set_input_source(src);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "tape_speed") {
        speed_ = std::clamp(getf(msg, "speed", speed_), kSpeedMin, kSpeedMax);
        rev_ = msg.value("reverse", rev_);
        Cmd c{};
        c.k = Ck::TapeSpeed;
        c.f0 = speed_;
        c.i0 = rev_ ? 1 : 0;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "tape_filter") {
        tf_on_ = msg.value("on", tf_on_);
        tf_cutoff_ = std::clamp(getf(msg, "cutoff", tf_cutoff_), 1.0f, float(opt_.sr) * 0.5f);
        if (msg.contains("mode")) tf_hp_ = gets(msg, "mode") == "HP";
        Cmd c{};
        c.k = Ck::TapeFilter;
        c.i0 = tf_on_ ? 1 : 0;
        c.f0 = tf_cutoff_;
        c.i1 = tf_hp_ ? 1 : 0;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "scrub") {
        Cmd c{};
        c.k = Ck::Scrub;
        c.pos = std::clamp<int64_t>(msg.value("position", int64_t(0)), 0,
                                    std::max<int64_t>(0, tape_.capacity() - 1));
        c.i0 = int32_t(std::clamp<int64_t>(msg.value("frames", int64_t(0.12 * opt_.sr)), 1, int64_t(opt_.sr) * 10));
        c.i1 = msg.value("reseed", true) ? 1 : 0;
        push(c);
        return reply_ok(msg);
    }

    if (type == "monitor") {
        monitor_on_ = msg.value("on", monitor_on_);
        monitor_gain_ = std::clamp(getf(msg, "gain", monitor_gain_), 0.0f, 16.0f);
        Cmd c{};
        c.k = Ck::Monitor;
        c.i0 = monitor_on_ ? 1 : 0;
        c.f0 = monitor_gain_;
        push(c);
        state_dirty_ = true;
        return reply_ok(msg);
    }

    if (type == "clip_peaks") {
        std::string code, err;
        json j = clip_peaks(msg, code, err);
        if (j.is_null()) return reply_err(msg, code, err);
        return j;
    }

    if (type == "clip_to_asset") {
        std::string code, err;
        json j = clip_to_asset(msg, code, err);
        if (j.is_null()) return reply_err(msg, code, err);
        return j;
    }

    // #920: CANCEL BOUNCE TO PLAY? answered YES. Nothing was written, so
    // there is nothing to delete; `running` says whether there was a job.
    if (type == "bounce_cancel") {
        const bool running = bounce_ != nullptr;
        if (running) bounce_end(json{{"type", "bounce"}, {"cancelled", true}});
        return reply_ok(msg, json{{"running", running}});
    }

    if (type == "tape_edit") {
        std::string code, err;
        json j = tape_edit(msg, code, err);
        if (j.is_null()) return reply_err(msg, code, err.empty() ? "TAPE EDIT FAILED" : err);
        return j;
    }

    if (type == "tape_compact") {
        std::string code, err;
        json j = tape_compact(msg, code, err);
        if (j.is_null()) return reply_err(msg, code, err.empty() ? "COMPACT FAILED" : err);
        return j;
    }

    if (type == "tape_dir") {
        std::string code, err;
        json j = tape_dir(msg, code, err);
        if (j.is_null()) return reply_err(msg, code, err.empty() ? "TAPE DIR FAILED" : err);
        return j;
    }

    if (type == "tape_save" || type == "tape_load") {
        const int t = msg.value("track", -1);
        if (t < 0 || t >= Tape::kTracks) return reply_err(msg, "bad_request", "track must be 0..7");
        const std::string file = gets(msg, "file");
        if (file.empty()) return reply_err(msg, "bad_request", type + " needs \"file\"");
        commit_take();
        if (recording_) return reply_err(msg, "bad_request", "RECORDING", "a take is open");
        // #920: a load rewrites the medium a bounce job is reading.
        if (type == "tape_load" && bounce_)
            bounce_end(json{{"type", "bounce"}, {"cancelled", true}});
        if (type == "tape_save") {
            const int64_t used = tape_.allocated(t) ? tape_.head[t].load(std::memory_order_relaxed) : 0;
            // C1 step 3b (#157): `file` *is* the medium -- the tape is on
            // storage and this is its own path. The file is already the save,
            // so this is a flush and nothing else: every range the take filled
            // reaches the card, and the end of the take already wrote its
            // header exact. Copying here would write a 44-byte-header WAV over
            // the medium through a rename, behind the I/O thread's open handle,
            // and the next open would refuse the result as not one of ours.
            if (tape_io_.on() && same_file(file, tape_io_.medium_path(t))) {
                tape_io_.flush();
                json j = reply_ok(msg);
                j["frames"] = used;
                j["in_place"] = true;
                return j;
            }
            // The used medium, interleaved stereo as float32 WAV. Clips are the
            // UI's to save; this is only what they read.
            arb::io::Audio a;
            a.sample_rate = opt_.sr;
            a.ch.assign(2, std::vector<float>(std::size_t(used), 0.0f));
            for (int64_t f = 0; f < used;) {
                int64_t run = 0;
                const float* src = tape_run(t, f, run);
                if (run <= 0) break;
                const int64_t take = std::min(run, used - f);
                if (src)
                    for (int64_t i = 0; i < take; ++i) {
                        a.ch[0][std::size_t(f + i)] = src[std::size_t(i) * 2];
                        a.ch[1][std::size_t(f + i)] = src[std::size_t(i) * 2 + 1];
                    }
                f += take;
            }
            tape_unpin();
            // Crash-safe (#93, § 7.3): the take in this file is the only
            // copy on disk, and an in-place write leaves a truncated one
            // behind a cut. `write_durable` also returns with the bytes on
            // the medium rather than ~150 MiB of them still dirty.
            if (std::string e = arb::io::write_durable(file, a, 32); !e.empty())
                return reply_err(msg, "write_failed", e);
            json j = reply_ok(msg);
            j["frames"] = used;
            return j;
        }
        // Load replaces the medium from 0 and sets the head past it. Send it
        // before the `set_clips` that read it: a clip already playing from the
        // old medium would hear the new one arrive.
        arb::io::Audio a;
        if (std::string e = arb::io::read(file, a); !e.empty()) return reply_err(msg, "not_found", e);
        if (a.sample_rate != 0 && a.sample_rate != opt_.sr)
            return reply_err(msg, "bad_request", "sample rate " + std::to_string(a.sample_rate) +
                                                     " is not the host's " + std::to_string(opt_.sr));
        const int64_t frames = std::min<int64_t>(int64_t(a.frames()), tape_.capacity());
        if (!tape_.ensure(t)) return reply_err(msg, "tape_full", "TAPE FULL", "no memory for track " + std::to_string(t));
        const std::vector<float>& l = a.ch[0];
        const std::vector<float>& r = a.ch.size() > 1 ? a.ch[1] : a.ch[0];
        // One range at a time through `write_frames`, `tape_edit`'s shape: with
        // a bounded pool a range is resident only once it is acquired, and
        // `run_at_mut` on an absent one is NULL -- which is how the load used
        // to move the head over frames that never reached a slot or the card
        // (#297: the converted take of an old project was gone on reopen).
        std::vector<float> buf(std::size_t(Tape::kRangeFrames) * 2);
        int64_t loaded = 0;
        while (loaded < frames) {
            const int64_t n = std::min<int64_t>(Tape::kRangeFrames - loaded % Tape::kRangeFrames,
                                                frames - loaded);
            for (int64_t i = 0; i < n; ++i) {
                buf[std::size_t(i) * 2] = l[std::size_t(loaded + i)];
                buf[std::size_t(i) * 2 + 1] = r[std::size_t(loaded + i)];
            }
            tape_io_.hold_span(t, loaded, n);   // not evictable until it is durable
            int64_t did = tape_.write_frames(t, loaded, n, buf.data());
            if (did < n) {
                tape_io_.flush();               // one turn tops the free list up
                did += tape_.write_frames(t, loaded + did, n - did, buf.data() + std::size_t(did) * 2);
            }
            tape_io_.persist_span(t, loaded, did);
            loaded += did;
            if (did < n) {
                tape_io_.release_span(t, loaded, n - did);
                break;
            }
        }
        if (loaded <= 0 && frames > 0) return reply_err(msg, "tape_full", "TAPE FULL", "no slot for track " + std::to_string(t));
        tape_.head[t].store(loaded, std::memory_order_relaxed);
        ++medium_gen_[t];
        state_dirty_ = true;
        json j = reply_ok(msg);
        j["frames"] = loaded;
        return j;
    }

    if (type == "bake") {
        std::string err;
        int64_t start = 0, frames = 0;
        const std::string file = bake(msg, err, &start, &frames);
        if (file.empty()) return reply_err(msg, "bake_failed", err);
        json j = reply_ok(msg);
        j["file"] = file;
        j["format"] = gets(msg, "format", "wav24");
        // The window written. Without `trim` it is 0..frames as it always
        // was; with it, the caller did not choose either number.
        j["start_frame"] = start;
        j["frames"] = frames;
        return j;
    }

    if (type == "meters") {
        meters_on_ = msg.value("on", true);
        meters_hz_ = std::clamp(double(getf(msg, "hz", 30.0f)), 1.0, 30.0);
        // The reply carries the rate that was accepted, the way `eq` hands
        // back the clamped block: a client asks for what it wants and reads
        // what it got. Over TCP that is § 9.4's 10 Hz cap, which is the one
        // place a client cannot work the number out for itself.
        json j = reply_ok(msg);
        j["on"] = meters_on_;
        j["hz"] = arb::manifest::nice(float(meters_hz_));
        return j;
    }

    if (type == "motion") {
        // A device *has* an IMU, so a client writing motion there would be a
        // second writer racing the sensor; `--imu` calls `set_motion` instead.
        // arb_host.motion[] for tests (modulation-spec §13). On the device the
        // host reads the IMU itself, so a client never gets to write it there.
        // Held until the next `motion`, as a sensor reading would be.
        if (!opt_.offline)
            return reply_err(msg, "bad_request", "motion is --offline only; the IMU owns it on a device");
        Cmd c{};
        c.k = Ck::Motion;
        for (const char* key : {"accel", "gyro"}) {
            const json v = msg.value(key, json::array());
            if (!v.is_array() || v.size() > 3) return reply_err(msg, "bad_request", std::string(key) + " is [x, y, z]");
            const int base = key[0] == 'a' ? 0 : 3;
            for (std::size_t k = 0; k < v.size(); ++k) {
                if (!v[k].is_number()) return reply_err(msg, "bad_request", std::string(key) + " is [x, y, z]");
                c.m[base + int(k)] = v[k].get<float>();
            }
        }
        push(c);
        return reply_ok(msg);
    }

    if (type == "render") {
        if (!opt_.offline)
            return reply_err(msg, "bad_request", "render is --offline only; a device is driving the clock");
        const int64_t frames = msg.value("frames", int64_t(0));
        if (frames <= 0 || frames > int64_t(opt_.sr) * 3600)
            return reply_err(msg, "bad_request", "render needs 1..3600 s of frames");
        std::vector<float> l, r;
        const std::string file = gets(msg, "file");
        render_offline(uint32_t(frames), file.empty() ? nullptr : &l, file.empty() ? nullptr : &r);
        json j = reply_ok(msg);
        j["frames"] = frames;
        j["song_frame"] = a_song_frame_pub_.load(std::memory_order_relaxed);
        j["tape_pos"] = a_tape_pos_pub_.load(std::memory_order_relaxed);
        // Heap blocks gained inside render_block since start; -1 = cannot count here.
        if (opt_.rt_guard) j["rt_allocs"] = rt_allocs_;
        if (!file.empty()) {
            arb::io::Audio a;
            a.sample_rate = opt_.sr;
            a.ch.assign(2, std::vector<float>{});
            a.ch[0] = std::move(l);
            a.ch[1] = std::move(r);
            float pk = 0.0f;
            for (const std::vector<float>& ch : a.ch)
                for (float v : ch) pk = std::max(pk, std::abs(v));
            const int bits = gets(msg, "format", "f32") == "wav16" ? 16 : 32;
            if (std::string e = arb::io::write(file, a, bits); !e.empty())
                return reply_err(msg, "write_failed", e);
            j["file"] = file;
            j["peak"] = arb::manifest::nice(pk);
        }
        bounce_step();                    // offline, a render is the job's clock
        poll();
        return j;
    }

    return reply_err(msg, "bad_request", "unknown message \"" + type + "\"");
}

}  // namespace arb::host
