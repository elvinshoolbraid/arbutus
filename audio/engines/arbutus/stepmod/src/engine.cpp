// arbutus/stepmod — a step sequence of control values (modulation-spec §3.2).
// A modulator with a sequencer's clock and none of its soft-keys (DDR-041).
// The values are content, not parameters (DDR-037): they arrive as the
// `pattern` asset. Entering them on the device is the UI's job. README.md says
// what every range is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <exception>
#include <fstream>
#include <new>
#include <string>

#include <nlohmann/json.hpp>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

enum Param : uint32_t { kSteps, kDiv, kSlew, kDir, kRange, kNumParams };
enum Output : uint32_t { kStep, kGate };
enum Dir : int { kFwd = 0, kRev, kPing, kRand };

constexpr int kMaxSteps = 16;

// arbutus/endless's divisions, with 1 BAR and 1/2 in front: a sequencer steps
// notes, a step modulator also steps a filter across a bar.
constexpr double kDivBeats[] = {4.0, 2.0, 1.0, 0.5, 1.0 / 3.0, 0.25, 1.0 / 6.0, 0.125, 1.0 / 12.0};
const char* const kDivLabels[] = {"1 BAR", "1/2", "1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32", "1/32T"};
constexpr int kNumDivs = int(sizeof kDivBeats / sizeof kDivBeats[0]);
const char* const kDirLabels[] = {"FWD", "REV", "PING", "RAND"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/stepmod");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    // events: a note-on restarts the free-running clock (README "Timing").
    d.caps = ARB_CAP_MODULATOR | ARB_CAP_EVENTS_IN;
    d.num_params = kNumParams;                         // slots 5-7 empty
    d.params[kSteps] = arb::param("steps", "STEPS", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                                  1.0f, float(kMaxSteps), 8.0f, 1.0f, "");
    d.params[kDiv]   = arb::param_enum("div", "DIV", kDivLabels, kNumDivs, 5);   // 1/16
    d.params[kSlew]  = arb::param_lin("slew", "SLEW", 0.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[kDir]   = arb::param_enum("dir", "DIR", kDirLabels, 4, kFwd);
    d.params[kRange] = arb::param_lin("range", "RANGE", 0.0f, 1.0f, 1.0f, 0.01f, "");
    arb::set_assets(d, {"pattern"});
    arb::set_outputs(d, {arb::output("step", "STEP", true), arb::output("gate", "GATE", false)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

uint32_t hash(uint32_t x) {
    x ^= x >> 16; x *= 0x7feb352dU; x ^= x >> 15; x *= 0x846ca68bU; x ^= x >> 16;
    return x;
}

struct Engine {
    float  norm[kNumParams]{};
    int    steps = 8, div = 5, dir = kFwd;
    float  slew = 0.0f, range = 1.0f;

    float  pattern[kMaxSteps]{};
    int    pattern_len = 0;           // 0 = no asset: the default ramp
    uint32_t seed = 0;

    double free_beats = 0.0;          // the clock while the transport is stopped
    bool   was_playing = false;

    explicit Engine(const arb_host* host) {
        seed = host ? host->rng_seed : 0u;
        for (uint32_t i = 0; i < kNumParams; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void reset() {
        free_beats = 0.0;
        was_playing = false;
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= kNumParams) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kSteps: steps = std::clamp(int(v), 1, kMaxSteps); break;
        case kDiv:   div = std::clamp(int(v), 0, kNumDivs - 1); break;
        case kSlew:  slew = v; break;
        case kDir:   dir = std::clamp(int(v), 0, 3); break;
        case kRange: range = v; break;
        default: break;
        }
    }

    // Control thread only, before the graph (§3.2).
    // {"format":1, "kind":"stepmod", "values":[-1..1, ... up to 16]}
    int load_asset(const char* key, const char* path) {
        if (!key || std::string(key) != "pattern" || !path) return 1;
        std::ifstream f(path);
        if (!f) return 2;
        nlohmann::json j;
        try {
            f >> j;
        } catch (const std::exception&) {
            return 3;
        }
        if (!j.is_object() || j.value("kind", std::string{}) != "stepmod") return 4;
        if (j.value("format", 0) != 1) return 5;
        const nlohmann::json v = j.value("values", nlohmann::json::array());
        if (!v.is_array() || v.empty() || v.size() > std::size_t(kMaxSteps)) return 6;
        for (std::size_t i = 0; i < v.size(); ++i) {
            if (!v[i].is_number()) return 7;
            pattern[i] = std::clamp(v[i].get<float>(), -1.0f, 1.0f);
        }
        pattern_len = int(v.size());
        return 0;
    }

    // Which step the n-th step of time plays. Pure in n, so the song position
    // alone says where the pattern is and a locate lands on the right step.
    int index(int64_t n) const {
        const int64_t s = steps;
        switch (dir) {
        case kRev: return int(s - 1 - (n % s));
        case kPing: {
            if (s == 1) return 0;
            const int64_t r = n % (2 * s - 2);       // 0 1 2 3 2 1 | 0 ...: ends not repeated
            return int(r < s ? r : 2 * s - 2 - r);
        }
        case kRand: return int(hash(uint32_t(n) * 2654435761u ^ hash(seed ^ 0x53544550u)) % uint32_t(s));
        default: return int(n % s);
        }
    }

    // No asset: a ramp from -1 to +1 across STEPS, so a fresh tab already
    // moves and its shape is obvious on the ghost marker.
    float value(int i) const {
        if (pattern_len > 0) return pattern[i % pattern_len];
        return steps > 1 ? -1.0f + 2.0f * float(i) / float(steps - 1) : 0.0f;
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const double bps = host.tempo_bpm > 0.0 ? host.tempo_bpm / 60.0 : 0.0;
        const bool playing = host.playing != 0;

        // Stopped: a note-on restarts the pattern, sample-placed as the lfo's
        // RETRIG is. Playing: notes do nothing, the song owns the position.
        int64_t last_on = -1;
        const uint32_t nev = io.ev_in ? io.n_ev_in : 0u;
        for (uint32_t k = 0; k < nev; ++k)
            if (io.ev_in[k].type == ARB_EV_NOTE_ON && io.ev_in[k].b > 0.0f)
                last_on = std::max<int64_t>(last_on, int64_t(std::min(io.ev_in[k].frame, io.frames)));
        if (!playing && last_on == 0) free_beats = 0.0;
        // Stopping continues from where the song was, not from an old count.
        if (was_playing && !playing) free_beats = double(host.song_frame) / sr * bps;
        was_playing = playing;

        const double beats = playing ? double(host.song_frame) / sr * bps : free_beats;
        const double pos = std::max(0.0, beats) / kDivBeats[div];
        const int64_t n = int64_t(std::floor(pos));
        const float frac = float(pos - double(n));

        const float cur = value(index(n));
        const float prev = n > 0 ? value(index(n - 1)) : cur;
        float gl = 1.0f;
        if (slew > 0.0f) {
            const float x = std::min(1.0f, frac / slew);
            gl = x * x * (3.0f - 2.0f * x);
        }
        io.ctl_out[kStep] = range * (prev + (cur - prev) * gl);
        io.ctl_out[kGate] = frac < 0.5f ? 1.0f : 0.0f;

        if (!playing) {
            if (last_on > 0) free_beats = double(io.frames - uint32_t(last_on)) / sr * bps;
            else free_beats += double(io.frames) / sr * bps;
        }
    }
};

// ---- ABI shims -------------------------------------------------------------
void* e_create(const arb_host* host, const char*) { return new (std::nothrow) Engine(host); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < kNumParams ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}
int   e_load_asset(void* h, const char* key, const char* path) {
    return static_cast<Engine*>(h)->load_asset(key, path);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    e_load_asset,            // the pattern (§3.2)
    nullptr,                 // set_note_param: no per_note params
    nullptr,                 // format_param: STEPS is a number; DIV and DIR are labelled ENUMs
    nullptr,                 // set_voice_mod: a modulator has no voices
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
