// arbutus/plate — a big, clean plate reverb (#930).
//
// Jon Dattorro's plate ("Effect Design, Part 1", JAES 45(9), 1997): the input
// is band-limited, pre-delayed and smeared by four series allpasses, then fed
// into a "figure eight" tank -- two halves, each a modulated allpass, a delay,
// a damping lowpass, a second allpass and a second delay, each half feeding
// the other. The stereo outputs are seven taps each, summed from all over the
// tank, which is what gives a plate its dense, even wash instead of the
// discrete echoes of a comb room (arbutus/reverb).
//
// Where this departs from the paper, and why:
//   * DECAY is a time, not a gain. The loop gain is worked out from the tank's
//     length so the knob reads in seconds and SIZE does not change the tail.
//   * SIZE scales every delay and tap (×0.4 … ×1.6 of the paper's lengths).
//     A SIZE or PREDELAY move glides (50 ms one-pole) and every read is
//     interpolated, so it bends the tail like tape instead of clicking.
//   * A fixed 40 Hz highpass on the input: with a 30 s tail, low rumble and DC
//     pile up into mud; a real plate does not ring below that either.
//   * The modulation is half the paper's excursion and slower: enough to stop
//     the tail ringing metallic, not enough to hear as chorus ("clean").
//
// Mono into the tank (the paper's), stereo out. Every recursive state carries
// a -360 dB DC offset so a tail decaying to silence never goes denormal (no
// FTZ is set on the Pi's audio thread); MIX 0 is still the input bit for bit.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <exception>
#include <new>
#include <vector>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr double kPaperSr = 29761.0;      // the rate the paper's lengths are in
constexpr double kPi2 = 6.283185307179586;

// Input diffusers (paper: 142, 107, 379, 277; coefficients 0.75, 0.625).
constexpr double kDiff[4] = {142, 107, 379, 277};
constexpr float  kDiffG[4] = {0.75f, 0.75f, 0.625f, 0.625f};

// The tank, per half: modulated allpass, delay, allpass, delay.
constexpr double kTank[2][4] = {{672, 4453, 1800, 3720},
                                {908, 4217, 2656, 3163}};
constexpr float kDecayDiff1 = 0.70f;

// The paper's figure-eight length, both halves: the decay gain is applied
// four times per trip round it.
constexpr double kLoopLen = 672 + 4453 + 1800 + 3720 + 908 + 4217 + 2656 + 3163;

// Output taps (paper, table 2): {half, element (1 = first delay, 2 = second
// allpass, 3 = second delay), offset, sign}. Left reads mostly the right
// half and right the left, which is the paper's decorrelation.
struct Tap { int half, elem; double at; float sign; };
constexpr Tap kTapsL[7] = {{1, 1, 266, +1}, {1, 1, 2974, +1}, {1, 2, 1913, -1},
                           {1, 3, 1996, +1}, {0, 1, 1990, -1}, {0, 2, 187, -1},
                           {0, 3, 1066, -1}};
constexpr Tap kTapsR[7] = {{0, 1, 353, +1}, {0, 1, 3627, +1}, {0, 2, 1228, -1},
                           {0, 3, 2673, +1}, {1, 1, 2111, -1}, {1, 2, 335, -1},
                           {1, 3, 121, -1}};
constexpr float kOutGain = 0.6f;

constexpr double kSizeMin = 0.4, kSizeSpan = 1.2;   // scale = 0.4 + 1.2*SIZE
constexpr double kMaxPredelayMs = 250.0;
constexpr double kExcursion = 8.0;                  // paper's units; it says 16
constexpr double kModHz = 0.5;
constexpr float  kAntiDenormal = 1e-18f;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/plate");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = 0;
    d.num_params = 5;
    // [TBD-Elfy: the defaults are by ear and his. Chosen here: a 3.5 s plate,
    // larger than the paper's, a little dark, a 20 ms predelay that keeps the
    // attack out of the wash -- the classic vocal plate.] The four that get
    // played come first; PREDELAY is set once.
    d.params[0] = arb::param_log("decay", "DECAY", 0.4f, 30.0f, 3.5f, 0.0f, "s");
    d.params[1] = arb::param_lin("mix",   "MIX",   0.0f, 1.0f, 0.3f, 0.01f, "");
    d.params[2] = arb::param_lin("size",  "SIZE",  0.0f, 1.0f, 0.7f, 0.01f, "");
    d.params[3] = arb::param_lin("damp",  "DAMP",  0.0f, 1.0f, 0.35f, 0.01f, "");
    d.params[4] = arb::param_lin("predelay", "PRE", 0.0f, float(kMaxPredelayMs), 20.0f, 1.0f, "ms");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// A power-of-two ring, written at the engine's one running frame counter and
// read any (fractional) distance behind it.
struct Line {
    std::vector<float> buf;
    uint32_t mask = 0;

    void alloc(double max_len) {
        uint32_t n = 1;
        while (n < uint32_t(max_len) + 4) n <<= 1;
        buf.assign(n, 0.0f);
        mask = n - 1;
    }
    void clear() { std::fill(buf.begin(), buf.end(), 0.0f); }
    void write(uint32_t n, float v) { buf[n & mask] = v; }
    // A distance split once into whole frames and a fraction, so a read in the
    // loop is two loads and a lerp. d >= 1; clamped to the ring, which only
    // bites after a rate change above the create() rate (no re-allocation in
    // process(), §3.7.1): the plate gets smaller instead of reading garbage.
    struct Dist { uint32_t i = 1; float f = 0.0f; };
    Dist dist(double d) const {
        d = std::clamp(d, 1.0, double(mask) - 1.0);
        const double fl = std::floor(d);
        return {uint32_t(fl), float(d - fl)};
    }
    float read(uint32_t n, Dist d) const {
        const float a = buf[(n - d.i) & mask], b = buf[(n - d.i - 1) & mask];
        return a + d.f * (b - a);
    }
};

// w = x + g*v is stored; y = v - g*w comes out. The paper's sign convention
// is the caller's: a negative g is its "-0.70" decay diffusion.
inline float allpass(Line& l, uint32_t n, Line::Dist d, float g, float x) {
    const float v = l.read(n, d);
    const float w = x + g * v;
    l.write(n, w);
    return v - g * w;
}

struct Engine {
    float  norm[5]{};
    double sr = 48000.0;
    float  decay_s = 3.5f, mix = 0.3f, size = 0.7f, damp = 0.35f, pre_ms = 20.0f;

    arb::Smoothed<float> mix_s, gain_s, damp_s;
    double scale_cur = 0.0, pre_cur = 0.0;   // glide state, in frames / ratio
    double glide = 0.0;                      // the 50 ms one-pole's coefficient
    bool   primed = false;                   // first block snaps the glides

    Line pre, diff[4], tank[2][4];
    // Every fixed read's distance at the current SIZE and PREDELAY; worked
    // out again only while one of them glides.
    Line::Dist pre_d, diff_d[4], tank_d[2][4], tap_d[2][7];
    double base0[2]{};                       // the modulated allpasses' centre
    float lp_in = 0.0f, hp_in = 0.0f, damp_z[2]{}, fb[2]{};
    float in_lp_c = 0.0f, in_hp_c = 0.0f;
    double mod_c = 1.0, mod_s = 0.0, mod_rc = 1.0, mod_rs = 0.0;   // quadrature LFO
    uint32_t n = 0;

    explicit Engine(const arb_host& h) {
        sr = h.sample_rate ? double(h.sample_rate) : 48000.0;
        alloc();
        mix_s.prepare(h);
        gain_s.prepare(h);
        damp_s.prepare(h);
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    double paper(double len) const { return len * sr / kPaperSr; }

    // Sized once, at create(), for the largest SIZE and the modulation.
    void alloc() {
        const double smax = kSizeMin + kSizeSpan;
        pre.alloc(sr * kMaxPredelayMs / 1000.0 + 2);
        for (int i = 0; i < 4; ++i) diff[i].alloc(paper(kDiff[i]) * smax + 2);
        for (auto& half : tank) {
            const int h = int(&half - tank);
            for (int e = 0; e < 4; ++e)
                half[e].alloc(paper(kTank[h][e]) * smax + paper(kExcursion) + 4);
        }
        coefs();
        clear();
    }

    // Everything that follows the sample rate but allocates nothing.
    void coefs() {
        in_lp_c = float(1.0 - std::exp(-kPi2 * std::min(13000.0, 0.45 * sr) / sr));
        in_hp_c = float(1.0 - std::exp(-kPi2 * 40.0 / sr));
        glide = 1.0 - std::exp(-1.0 / (0.050 * sr));
        mod_rc = std::cos(kPi2 * kModHz / sr);
        mod_rs = std::sin(kPi2 * kModHz / sr);
    }

    void clear() {
        pre.clear();
        for (Line& l : diff) l.clear();
        for (auto& half : tank) for (Line& l : half) l.clear();
        lp_in = hp_in = 0.0f;
        damp_z[0] = damp_z[1] = fb[0] = fb[1] = 0.0f;
        mod_c = 1.0; mod_s = 0.0;
        n = 0;
        primed = false;
    }

    void lengths() {
        pre_d = pre.dist(pre_cur);
        for (int j = 0; j < 4; ++j) diff_d[j] = diff[j].dist(paper(kDiff[j]) * scale_cur);
        for (int h = 0; h < 2; ++h) {
            base0[h] = paper(kTank[h][0]) * scale_cur + paper(kExcursion);
            for (int e = 1; e < 4; ++e) tank_d[h][e] = tank[h][e].dist(paper(kTank[h][e]) * scale_cur);
        }
        for (int j = 0; j < 7; ++j) {
            tap_d[0][j] = tank[kTapsL[j].half][kTapsL[j].elem].dist(paper(kTapsL[j].at) * scale_cur);
            tap_d[1][j] = tank[kTapsR[j].half][kTapsR[j].elem].dist(paper(kTapsR[j].at) * scale_cur);
        }
    }

    double scale() const { return kSizeMin + kSizeSpan * double(size); }

    // The loop gain that brings the figure eight down 60 dB in DECAY seconds:
    // four multiplies per trip, a trip of kLoopLen paper frames at this SIZE.
    float loop_gain() const {
        const double trip_s = kLoopLen * scale() / kPaperSr;
        return float(std::pow(10.0, -3.0 * trip_s / (4.0 * double(decay_s))));
    }

    // DAMP 0 is 18 kHz (open), 1 is 1 kHz, log between: the tail darkens as
    // it decays, the way a steel plate does.
    float damp_coef() const {
        const double fc = std::min(18000.0 * std::pow(1000.0 / 18000.0, double(damp)), 0.45 * sr);
        return float(1.0 - std::exp(-kPi2 * fc / sr));
    }

    void set_param(uint32_t idx, float v01) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(v01);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: decay_s = v; break;
        case 1: mix = v; break;
        case 2: size = v; break;
        case 3: damp = v; break;
        default: pre_ms = v; break;
        }
        // DECAY's gain depends on SIZE, so both re-target it.
        mix_s.set(mix);
        gain_s.set(loop_gain());
        damp_s.set(damp_coef());
    }

    void reset() {
        clear();
        mix_s.reset(); gain_s.reset(); damp_s.reset();
    }

    void process(const arb_host& host, arb_io& io) {
        const double rate = host.sample_rate ? double(host.sample_rate) : sr;
        if (rate != sr) {
            sr = rate;
            coefs();
            clear();
            gain_s.snap(loop_gain());
            damp_s.snap(damp_coef());
        }
        const double scale_t = scale();
        const double pre_t = std::max(1.0, sr * double(pre_ms) / 1000.0);
        if (!primed) { scale_cur = scale_t; pre_cur = pre_t; primed = true; lengths(); }

        const float* inl = io.in[0];
        const float* inr = io.in[1] ? io.in[1] : io.in[0];
        float* outl = io.out[0];
        float* outr = io.out[1];
        const double exc = paper(kExcursion);

        for (uint32_t i = 0; i < io.frames; ++i) {
            if (scale_cur != scale_t || pre_cur != pre_t) {
                scale_cur += glide * (scale_t - scale_cur);
                pre_cur += glide * (pre_t - pre_cur);
                // Land exactly, so a settled plate stops recomputing.
                if (std::abs(scale_t - scale_cur) < 1e-7) scale_cur = scale_t;
                if (std::abs(pre_t - pre_cur) < 1e-4) pre_cur = pre_t;
                lengths();
            }
            const float m = mix_s.next(), g = gain_s.next(), dc = damp_s.next();
            const float dd2 = std::clamp(g + 0.15f, 0.25f, 0.5f);   // the paper's rule

            // The quadrature LFO: a rotation per frame, renormalised so it
            // neither grows nor fades over an hour of tail.
            const double c = mod_c * mod_rc - mod_s * mod_rs;
            const double s = mod_s * mod_rc + mod_c * mod_rs;
            const double k = 1.5 - 0.5 * (c * c + s * s);
            mod_c = c * k; mod_s = s * k;

            const float xl = inl ? inl[i] : 0.0f, xr = inr ? inr[i] : 0.0f;
            const float x = 0.5f * (xl + xr);

            // Band-limit, then pre-delay.
            lp_in += in_lp_c * (x + kAntiDenormal - lp_in);
            hp_in += in_hp_c * (lp_in - hp_in);
            pre.write(n, lp_in - hp_in + kAntiDenormal);
            float d = pre.read(n, pre_d);

            for (int j = 0; j < 4; ++j) d = allpass(diff[j], n, diff_d[j], kDiffG[j], d);

            // The tank. Each half takes the diffused input plus the other
            // half's last output from the previous frame.
            float half_out[2];
            for (int h = 0; h < 2; ++h) {
                Line* t = tank[h];
                const double mod = (h == 0 ? mod_s : mod_c) * exc;
                float v = d + fb[1 - h];
                v = allpass(t[0], n, t[0].dist(base0[h] + mod), -kDecayDiff1, v);
                t[1].write(n, v);
                v = t[1].read(n, tank_d[h][1]);
                damp_z[h] += dc * (v - damp_z[h]);
                v = damp_z[h] * g;
                v = allpass(t[2], n, tank_d[h][2], dd2, v);
                t[3].write(n, v);
                half_out[h] = t[3].read(n, tank_d[h][3]) * g;
            }
            fb[0] = half_out[0];
            fb[1] = half_out[1];

            float wl = 0.0f, wr = 0.0f;
            for (int j = 0; j < 7; ++j) {
                const Tap& a = kTapsL[j];
                wl += a.sign * tank[a.half][a.elem].read(n, tap_d[0][j]);
                const Tap& b = kTapsR[j];
                wr += b.sign * tank[b.half][b.elem].read(n, tap_d[1][j]);
            }
            ++n;

            const float dry = 1.0f - m;
            outl[i] = xl * dry + kOutGain * wl * m;
            outr[i] = xr * dry + kOutGain * wr * m;
        }
    }
};

void* e_create(const arb_host* host, const char*) {
    arb_host h{};
    if (host) h = *host;
    if (!h.sample_rate) h.sample_rate = 48000;
    if (!h.max_block) h.max_block = 512;
    // nothrow guards only the Engine itself: the rings' buf.assign in its
    // constructor throws, and an exception out of create() aborts the host.
    // A failed allocation is NULL, the host's refusal (§14; #1215).
    try {
        return new (std::nothrow) Engine(h);
    } catch (const std::exception&) {
        return nullptr;
    }
}
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < desc().num_params ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
