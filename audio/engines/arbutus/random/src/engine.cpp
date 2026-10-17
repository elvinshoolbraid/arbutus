// arbutus/random — three kinds of chance off one clock (modulation-spec §3.2).
// A modulator: S&H steps, DRIFT is a smooth curve through random points, WALK
// wanders. All deterministic from SEED (or the host's rng_seed), so a patch
// sounds the same twice and a golden is a golden. README.md says what every
// range is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

enum Param : uint32_t { kRate, kSlew, kRange, kSeed, kNumParams };
enum Output : uint32_t { kSh, kDrift, kWalk };

// ---- RATE, the bipolar knob of §3.3 -----------------------------------------
// Copied from arbutus/lfo, not shared: a box is its own source tree and the
// two must read the same off the same knob, so change both or neither.
// Centre is STOP; right, tempo divisions slowest first; left, free Hz on a log
// curve. [default chosen in arbutus/lfo -- audio/CLAUDE.md]
struct Division { const char* label; double beats; };   // beats = quarter notes
constexpr Division kDivisions[] = {
    {"8 BAR", 32.0},     {"4 BAR", 16.0},     {"2 BAR", 8.0},        {"1 BAR", 4.0},
    {"1/2", 2.0},        {"1/2T", 4.0 / 3.0}, {"1/4", 1.0},          {"1/4T", 2.0 / 3.0},
    {"1/8", 0.5},        {"1/8T", 1.0 / 3.0}, {"1/16", 0.25},        {"1/16T", 1.0 / 6.0},
    {"1/32", 0.125},     {"1/32T", 1.0 / 12.0}};
constexpr int kNumDivisions = int(sizeof kDivisions / sizeof kDivisions[0]);

constexpr float  kDead = 0.02f;
constexpr double kHzLo = 0.05, kHzHi = 50.0;

struct Rate {
    enum Kind : uint8_t { Stop, Sync, Free } kind = Stop;
    int    div = 0;
    double hz = 0.0;
};

Rate rate_of(float x) {
    Rate r;
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

// Default RATE is 1/8: a new value every eighth is "random" the moment a route
// is made, and still slower than the lfo's 1/4 is heard to move.
constexpr float kRateDefault = kDead + (1.0f - kDead) * 8.0f / float(kNumDivisions - 1);
constexpr float kSeedMax = 999.0f;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/random");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_MODULATOR;
    d.num_params = kNumParams;                         // slots 4-7 empty
    d.params[kRate]  = arb::param_lin("rate", "RATE", -1.0f, 1.0f, kRateDefault, 0.01f, "");
    d.params[kSlew]  = arb::param_lin("slew", "SLEW", 0.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[kRange] = arb::param_lin("range", "RANGE", 0.0f, 1.0f, 1.0f, 0.01f, "");
    // §3.2: an INTEGER, so a patch names one sequence exactly.
    d.params[kSeed]  = arb::param("seed", "SEED", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                                  0.0f, kSeedMax, 0.0f, 1.0f, "");
    // Three, not four: a fourth kind of chance would be one of these with a
    // different knob, and a slot that is a copy is a slot a player must learn.
    arb::set_outputs(d, {arb::output("sh", "S&H", true), arb::output("drift", "DRIFT", true),
                         arb::output("walk", "WALK", true)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

uint32_t xorshift(uint32_t& s) {
    s ^= s << 13;
    s ^= s >> 17;
    s ^= s << 5;
    return s;
}

uint32_t mix(uint32_t x) {           // splits one seed into independent streams
    x ^= x >> 16; x *= 0x7feb352dU; x ^= x >> 15; x *= 0x846ca68bU; x ^= x >> 16;
    return x ? x : 1u;
}

struct Stream {
    uint32_t s = 1;
    float uni() { return float(xorshift(s) >> 8) / float(1u << 23) - 1.0f; }   // -1 .. 1
};

// A new value no further than 2·RANGE from the last, uniform over what is left
// of -1..1: RANGE 1 is classic S&H, a small RANGE a jitter that stays put.
float next_value(Stream& st, float last, float range) {
    const float lo = std::max(-1.0f, last - 2.0f * range);
    const float hi = std::min(1.0f, last + 2.0f * range);
    return lo + (hi - lo) * 0.5f * (st.uni() + 1.0f);
}

struct Engine {
    float  norm[kNumParams]{};
    float  rate_x = kRateDefault, slew = 0.0f, range = 1.0f;
    int    seed_param = 0;
    uint32_t host_seed = 0;

    Stream sh_rng, drift_rng, walk_rng;
    double phase = 0.0;               // 0..1 within the current step
    float  sh_prev = 0.0f, sh_cur = 0.0f;
    float  pts[4]{};                  // DRIFT's points: before, from, to, after
    float  walk = 0.0f;

    explicit Engine(const arb_host* host) {
        host_seed = host ? host->rng_seed : 0u;
        for (uint32_t i = 0; i < kNumParams; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
        reset();
    }

    uint32_t seed() const { return seed_param ? uint32_t(seed_param) : host_seed; }

    void reset() {
        const uint32_t s = seed();
        sh_rng.s = mix(s ^ 0x5348u);
        drift_rng.s = mix(s ^ 0x4452u);
        walk_rng.s = mix(s ^ 0x574Bu);
        phase = 0.0;
        // DRIFT and WALK leave from 0, so a route made on a new tab does not
        // jump its target. S&H is a held value by definition: it holds its
        // first one from the first block, drawn near 0 by the same RANGE rule.
        sh_cur = next_value(sh_rng, 0.0f, range);
        sh_prev = sh_cur;
        pts[0] = pts[1] = 0.0f;
        pts[2] = next_value(drift_rng, pts[1], range);
        pts[3] = next_value(drift_rng, pts[2], range);
        walk = 0.0f;
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= kNumParams) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kRate:  rate_x = v; break;
        case kSlew:  slew = v; break;
        case kRange: range = v; break;
        case kSeed: {
            // A new SEED is a new sequence from its start, which is what makes
            // a seed worth writing down. Integer work only: safe at block start.
            const int s = int(v);
            if (s != seed_param) { seed_param = s; reset(); }
            break;
        }
        default: break;
        }
    }

    void step() {
        sh_prev = sh_cur;
        sh_cur = next_value(sh_rng, sh_cur, range);
        pts[0] = pts[1];
        pts[1] = pts[2];
        pts[2] = pts[3];
        pts[3] = next_value(drift_rng, pts[2], range);
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const Rate r = rate_of(rate_x);
        double hz = 0.0;
        if (r.kind == Rate::Free) hz = r.hz;
        else if (r.kind == Rate::Sync && host.tempo_bpm > 0.0)
            hz = host.tempo_bpm / 60.0 / kDivisions[r.div].beats;

        // ---- this block's value, at the block's first frame (§7) -------------
        const float p = float(phase);
        float gl = 1.0f;
        if (slew > 0.0f) {
            const float x = std::min(1.0f, p / slew);
            gl = x * x * (3.0f - 2.0f * x);
        }
        io.ctl_out[kSh] = sh_prev + (sh_cur - sh_prev) * gl;
        // Catmull-Rom: continuous slope through every point, so DRIFT never
        // pauses on a value the way a glided S&H does. It can overshoot a
        // point a little, hence the clamp.
        const float t = p, t2 = t * t, t3 = t2 * t;
        const float c = 0.5f * ((2.0f * pts[1]) + (-pts[0] + pts[2]) * t +
                                (2.0f * pts[0] - 5.0f * pts[1] + 4.0f * pts[2] - pts[3]) * t2 +
                                (-pts[0] + 3.0f * pts[1] - 3.0f * pts[2] + pts[3]) * t3);
        io.ctl_out[kDrift] = std::clamp(c, -1.0f, 1.0f);
        io.ctl_out[kWalk] = walk;

        // ---- then run the block ----------------------------------------------
        const double dt = double(io.frames) / sr;
        phase += hz * dt;
        while (phase >= 1.0) {       // a free 50 Hz at a long block is several steps
            phase -= 1.0;
            step();
        }
        // WALK is Brownian: an increment scaled by sqrt(time), so its wander per
        // step is the same at any block size on average (std ~0.58·RANGE per
        // step), and it reflects off ±1 instead of sticking there.
        if (hz > 0.0) {
            float w = walk + range * walk_rng.uni() * float(std::sqrt(hz * dt));
            if (w > 1.0f) w = 2.0f - w;
            if (w < -1.0f) w = -2.0f - w;
            walk = std::clamp(w, -1.0f, 1.0f);
        }
    }
};

// "STOP", "1/8", "3.2 Hz"; SEED 0 is "HOST". Pure in (idx, normalised).
int format(uint32_t idx, float n, char* buf, uint32_t size) {
    int w = 0;
    if (idx == kSeed) {
        const int s = int(arb::denorm(desc().params[kSeed], n));
        w = s ? std::snprintf(buf, size, "%d", s) : std::snprintf(buf, size, "HOST");
        return w > 0;
    }
    if (idx != kRate) return 0;
    const Rate r = rate_of(arb::denorm(desc().params[kRate], n));
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
    return (buf && size) ? format(idx, n, buf, size) : 0;
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets
    nullptr,                 // set_note_param: no per_note params
    e_format_param,          // RATE and SEED readouts
    nullptr,                 // set_voice_mod: a modulator has no voices
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
