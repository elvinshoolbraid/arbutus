// arbutus/lfo — the stock LFO (modulation-spec §3.2). A modulator: no audio,
// four control outputs per block, all bipolar and all read off one phase, so
// SINE and SQR from the same tab stay locked and two tabs a PHASE apart are in
// quadrature. README.md says what every range is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

enum Param : uint32_t { kRate, kMorph, kPhase, kRetrig, kFadeIn, kSym, kNumParams };
enum Output : uint32_t { kSine, kTri, kSqr, kSh };

// ---- RATE, the bipolar knob of §3.3 -----------------------------------------
// Centre is STOP. Right of centre: tempo divisions, slowest beside STOP so the
// knob speeds up as it leaves the centre either way. Straight and triplet from
// the half note down; bars above it, where a triplet is not a thing anyone
// counts. [default chosen here: the spec names "8 bars ... 1/32T" and no list --
// audio/CLAUDE.md]
struct Division { const char* label; double beats; };   // beats = quarter notes
constexpr Division kDivisions[] = {
    {"8 BAR", 32.0},     {"4 BAR", 16.0},     {"2 BAR", 8.0},        {"1 BAR", 4.0},
    {"1/2", 2.0},        {"1/2T", 4.0 / 3.0}, {"1/4", 1.0},          {"1/4T", 2.0 / 3.0},
    {"1/8", 0.5},        {"1/8T", 1.0 / 3.0}, {"1/16", 0.25},        {"1/16T", 1.0 / 6.0},
    {"1/32", 0.125},     {"1/32T", 1.0 / 12.0}};
constexpr int kNumDivisions = int(sizeof kDivisions / sizeof kDivisions[0]);

// |RATE| under this is STOP: two detents of the 0.01 step, so the centre can
// be found by feel. Left of centre is free Hz, 0.05 .. 50 on a log curve (§3.3's
// range): equal turns are equal ratios, which is how a rate is heard.
constexpr float  kDead = 0.02f;
constexpr double kHzLo = 0.05, kHzHi = 50.0;

struct Rate {
    enum Kind : uint8_t { Stop, Sync, Free } kind = Stop;
    int    div = 0;
    double hz = 0.0;
};

Rate rate_of(float x) {
    Rate r;
    // The first detent past the dead zone must not read STOP after a trip
    // through norm() and back, hence the float-sized allowance.
    if (std::fabs(x) < kDead - 1e-4f) return r;
    const double t = std::max(0.0, (double(std::fabs(x)) - kDead) / (1.0 - kDead));
    if (x > 0.0f) {
        r.kind = Rate::Sync;
        r.div = std::clamp(int(std::lround(t * (kNumDivisions - 1))), 0, kNumDivisions - 1);
    } else {
        r.kind = Rate::Free;
        r.hz = kHzLo * std::pow(kHzHi / kHzLo, std::clamp(t, 0.0, 1.0));
    }
    return r;
}

// Default RATE is 1/4: moving the moment a route is made, and in time.
constexpr float kRateDefault = kDead + (1.0f - kDead) * 6.0f / float(kNumDivisions - 1);

const char* const kRetrigLabels[] = {"FREE", "NOTE"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/lfo");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_MODULATOR | ARB_CAP_EVENTS_IN;   // events: RETRIG NOTE and FADE-IN
    d.num_params = kNumParams;                         // slots 6-7 empty
    d.params[kRate]   = arb::param_lin("rate", "RATE", -1.0f, 1.0f, kRateDefault, 0.01f, "");
    d.params[kMorph]  = arb::param_lin("morph", "MORPH", 0.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[kPhase]  = arb::param_lin("phase", "PHASE", 0.0f, 360.0f, 0.0f, 5.0f, "deg");
    d.params[kRetrig] = arb::param_enum("retrig", "RETRIG", kRetrigLabels, 2, 0);
    d.params[kFadeIn] = arb::param_lin("fade_in", "FADE-IN", 0.0f, 5.0f, 0.0f, 0.05f, "s");
    d.params[kSym]    = arb::param_lin("sym", "SYM", 5.0f, 95.0f, 50.0f, 1.0f, "%");
    arb::set_outputs(d, {arb::output("sine", "SINE", true), arb::output("tri", "TRI", true),
                         arb::output("sqr", "SQR", true), arb::output("sh", "S&H", true)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

constexpr double kTwoPi = 6.283185307179586;

// xorshift32: S&H is deterministic from rng_seed (§3.3), for goldens and for a
// patch that should sound the same twice.
uint32_t xorshift(uint32_t& s) {
    s ^= s << 13;
    s ^= s >> 17;
    s ^= s << 5;
    return s;
}

struct Engine {
    float  norm[kNumParams]{};
    float  rate_x = kRateDefault, morph = 0.0f;
    double phase_off = 0.0, sym = 0.5, fade_s = 0.0;
    bool   retrig_note = false;

    uint32_t seed = 1, rng = 1;
    double   phase = 0.0;            // 0..1, before PHASE
    int64_t  cycle = 0;              // whole cycles run; with phase, an unwrapped position
    int64_t  sh_key = 0;             // the cycle the held S&H value belongs to
    bool     sh_stale = false;       // a retrigger: draw at the next value
    float    sh_prev = 0.0f, sh_cur = 0.0f;
    double   since_note = 1e9;       // seconds; "long ago" = fully faded in

    explicit Engine(const arb_host* host) {
        seed = (host ? host->rng_seed : 0u) * 2654435761u ^ 0x9E3779B9u;
        if (!seed) seed = 1;
        for (uint32_t i = 0; i < kNumParams; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
        reset();
    }

    void reset() {
        rng = seed;
        phase = 0.0;
        cycle = 0;
        sh_key = 0;
        sh_stale = false;
        sh_cur = draw();
        sh_prev = sh_cur;
        since_note = 1e9;
    }

    float draw() { return float(xorshift(rng) >> 8) / float(1u << 23) - 1.0f; }   // -1 .. 1

    void set_param(uint32_t idx, float n) {
        if (idx >= kNumParams) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        // No smoothing (rule 7): an LFO's own knobs are read once per block,
        // which is its output's resolution anyway, and every target smooths
        // what the route sends it.
        switch (idx) {
        case kRate:   rate_x = v; break;
        case kMorph:  morph = v; break;
        case kPhase:  phase_off = double(v) / 360.0; break;
        case kRetrig: retrig_note = v >= 0.5f; break;
        case kFadeIn: fade_s = v; break;
        case kSym:    sym = double(v) / 100.0; break;
        default: break;
        }
    }

    void advance(double inc, uint32_t frames) {
        phase += inc * double(frames);
        if (phase >= 1.0) {
            const double w = std::floor(phase);
            cycle += int64_t(w);
            phase -= w;
        }
    }

    // A new cycle, so S&H draws a new value. Not through the key alone: with
    // PHASE past the wrap, `cycle + floor(phase + PHASE)` can be the same
    // number before and after, and the draw never came (#631).
    void retrigger() {
        phase = 0.0;
        ++cycle;
        sh_stale = true;
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const Rate r = rate_of(rate_x);
        double hz = 0.0;
        if (r.kind == Rate::Free) hz = r.hz;
        else if (r.kind == Rate::Sync && host.tempo_bpm > 0.0)
            hz = host.tempo_bpm / 60.0 / kDivisions[r.div].beats;
        // Synced rates run from the tempo but not from the song position: the
        // phase is the LFO's own, restarted by RETRIG NOTE. [default chosen
        // here: §7 leaves the timing model to the box]
        const double inc = hz / sr;

        // Note-ons at frame 0 land before this block's value is taken; a
        // later one lands after, and the next block starts from it.
        int64_t last_on = -1;
        const uint32_t nev = io.ev_in ? io.n_ev_in : 0u;
        for (uint32_t k = 0; k < nev; ++k)
            if (io.ev_in[k].type == ARB_EV_NOTE_ON && io.ev_in[k].b > 0.0f)
                last_on = std::max<int64_t>(last_on, int64_t(std::min(io.ev_in[k].frame, io.frames)));
        for (uint32_t k = 0; k < nev; ++k)
            if (io.ev_in[k].type == ARB_EV_NOTE_ON && io.ev_in[k].b > 0.0f && io.ev_in[k].frame == 0) {
                if (retrig_note) retrigger();
                since_note = 0.0;
                break;
            }

        // ---- this block's value, at the block's first frame -----------------
        const double pos = phase + phase_off;
        const double p = pos - std::floor(pos);
        if (const int64_t key = cycle + int64_t(std::floor(pos)); key != sh_key || sh_stale) {
            sh_prev = sh_cur;
            sh_cur = draw();
            sh_key = key;
            sh_stale = false;
        }
        // SYM moves where the first half-cycle ends, so one knob is TRI -> saw,
        // SQR pulse width and a leaning SINE together.
        const double s = std::clamp(sym, 0.05, 0.95);
        const double w = p < s ? 0.5 * p / s : 0.5 + 0.5 * (p - s) / (1.0 - s);
        const float sine = float(std::sin(kTwoPi * w));
        const float tri = float(w < 0.5 ? -1.0 + 4.0 * w : 3.0 - 4.0 * w);
        // MORPH rounds the two stepped shapes: SQR from hard square (0) to the
        // sine (1); S&H from steps to a smoothstep glide over MORPH of the cycle.
        float sqr, gl;
        if (morph <= 0.0f) {
            sqr = w < 0.5 ? 1.0f : -1.0f;
            gl = 1.0f;
        } else {
            sqr = std::clamp(sine / morph, -1.0f, 1.0f);
            const float x = std::min(1.0f, float(p) / morph);
            gl = x * x * (3.0f - 2.0f * x);
        }
        const float sh = sh_prev + (sh_cur - sh_prev) * gl;
        const float fade = fade_s > 0.0 ? float(std::min(1.0, since_note / fade_s)) : 1.0f;

        io.ctl_out[kSine] = sine * fade;
        io.ctl_out[kTri] = tri * fade;
        io.ctl_out[kSqr] = sqr * fade;
        io.ctl_out[kSh] = sh * fade;

        // ---- then run the block ----------------------------------------------
        if (last_on > 0) {
            advance(inc, uint32_t(last_on));
            if (retrig_note) retrigger();
            advance(inc, io.frames - uint32_t(last_on));
            since_note = double(io.frames - uint32_t(last_on)) / sr;
        } else {
            advance(inc, io.frames);
            since_note = std::min(since_note + double(io.frames) / sr, 1e9);
        }
    }
};

// "STOP", "1/8", "3.2 Hz". Pure in (idx, normalised), as the ABI requires.
int format_rate(float n, char* buf, uint32_t size) {
    const Rate r = rate_of(arb::denorm(desc().params[kRate], n));
    int w = 0;
    switch (r.kind) {
    case Rate::Stop: w = std::snprintf(buf, size, "STOP"); break;
    case Rate::Sync: w = std::snprintf(buf, size, "%s", kDivisions[r.div].label); break;
    case Rate::Free:
        if (r.hz < 1.0)       w = std::snprintf(buf, size, "%.2f Hz", r.hz);
        else if (r.hz < 10.0) w = std::snprintf(buf, size, "%.1f Hz", r.hz);
        else                  w = std::snprintf(buf, size, "%.0f Hz", r.hz);
        break;
    }
    return w > 0;
}

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
int   e_format_param(void*, uint32_t idx, float n, char* buf, uint32_t size) {
    return (idx == kRate && buf && size) ? format_rate(n, buf, size) : 0;   // the rest: host default
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets
    nullptr,                 // set_note_param: no per_note params
    e_format_param,          // RATE's readout (§3.3)
    nullptr,                 // set_voice_mod: a modulator has no voices
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
