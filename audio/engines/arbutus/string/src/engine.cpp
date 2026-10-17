// arbutus/string — a plucked, struck and bowed string, by waveguide.
//
// Two coupled delay lines per voice with a damping filter in the loop: the
// Karplus-Strong/waveguide family, which is the one physical model small
// enough to run eight voices of on a CM4 and still sound like a string rather
// than like a filtered saw. Nothing in the box is a wavetable or a sample --
// the sound is the excitation going round the loop and losing its top end,
// which is why DECAY and DAMP are two knobs and not one.
//
// What each part is for:
//   The loop gain is DECAY, in **seconds to -60 dB**, worked out per note from
//     that note's own period, so a low string and a high one both ring for the
//     time on the screen instead of the low one ringing eight times longer.
//   The one-pole in the loop is DAMP: it makes a note's top end die before its
//     fundamental, which is the whole difference between a string and a
//     resonator. It also delays the loop, so its phase delay is taken out of
//     the line length -- otherwise every setting of DAMP is a new tuning.
//   POS is the pluck position: the excitation minus a copy of itself a
//     fraction of a period later, which notches out the harmonics with a node
//     where you plucked. It is why a bridge pluck is nasal and a twelfth-fret
//     pluck is round, and it costs one subtraction.
//   SPREAD is the second string. A piano's unison pair and a twelve-string are
//     the same trick: two lines a few cents apart, each with its own
//     excitation, beating against each other.
//   BODY is two fixed resonators across the output -- the box the string is
//     mounted on. Fixed, because a body that moves with a knob is a filter and
//     `arbutus/va` already has one.
//
// Ranges are the author's job (DDR-044); the README says what every one is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>
#include <vector>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>

namespace {

constexpr std::size_t kVoices = 8;
constexpr std::size_t kStrings = 2;          // the unison pair
constexpr std::size_t kModes = 2;            // body resonances
constexpr int kNumParams = 8;
enum Idx { kExc = 0, kDecay, kDamp, kPos, kTone, kForce, kBody, kSpread };

enum Exc { kPluck = 0, kBow, kStrike, kNumExc };
const char* const kExcLabels[] = {"PLUCK", "BOW", "STRIKE"};

// The lowest note the line is sized for. MIDI 21 (A0) is 27.5 Hz; 20 Hz leaves
// room for a full bend and a couple of octaves of `‹` underneath it.
constexpr double kMinHz = 20.0;

// Body resonances: the air mode and the first top mode of a smallish wooden
// box. [default chosen here: nothing in the spec names them -- audio/CLAUDE.md]
constexpr double kBodyHz[kModes] = {180.0, 1050.0};
constexpr double kBodyQ[kModes]  = {4.0, 6.0};

constexpr float kTrim = 0.7f;               // headroom; a string peaks well below a saw
constexpr double kPi = 3.14159265358979324;

// How hard a bow pushes the loop each sample. The tanh in the loop is what
// stops it running away, so this sets how far into the limit it sits: too
// little and BOW is inaudible next to PLUCK, too much and the string turns
// into filtered noise. [default chosen here: no spec -- audio/CLAUDE.md]
constexpr float kBowDrive = 0.45f;

// RBJ bandpass, constant 0 dB peak gain. Coefficients only: the state lives in
// the voice, so eight voices share one design.
struct Biquad {
    float b0 = 0, b2 = 0, a1 = 0, a2 = 0;
    void design(double hz, double q, double sr) {
        const double w = 2.0 * kPi * hz / sr;
        const double alpha = std::sin(w) / (2.0 * q);
        const double a0 = 1.0 + alpha;
        b0 = float(alpha / a0);
        b2 = float(-alpha / a0);
        a1 = float(-2.0 * std::cos(w) / a0);
        a2 = float((1.0 - alpha) / a0);
    }
};

struct BiquadState {
    float x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    void clear() { x1 = x2 = y1 = y2 = 0.0f; }
    float run(const Biquad& c, float x) {
        const float y = c.b0 * x + c.b2 * x2 - c.a1 * y1 - c.a2 * y2;
        x2 = x1; x1 = x;
        y2 = y1; y1 = y;
        return y;
    }
};

// One waveguide. The buffer is the engine's; a voice never allocates.
//
// The loop is: an integer delay, a first-order allpass for the fraction of a
// sample, the DAMP one-pole, the gain. The allpass and not a linear read
// (#647): a linear read is a lowpass whose loss depends on the fraction, so
// above about C6 the notes rang shorter than DECAY and unevenly from key to
// key (T60 0.4-1.2 s for 2 s). The allpass loses nothing at any frequency.
struct Line {
    float* buf = nullptr;
    int    cap = 0;
    int    w = 0;
    double delay = 100.0;                   // the note's period in samples
    int    n_int = 99;                      // the integer part of the line
    float  eta = 0.0f;                      // the allpass's coefficient
    float  ap_x = 0.0f, ap_y = 0.0f;        // its state
    float  lp = 0.0f;

    void clear() {
        for (int i = 0; i < cap; ++i) buf[i] = 0.0f;
        w = 0;
        ap_x = ap_y = 0.0f;
        lp = 0.0f;
    }
    int wrap(int i) const { return i >= cap ? i - cap : i; }

    float tick(float in, float g, float a, bool saturate) {
        int rp = w - n_int;
        if (rp < 0) rp += cap;
        const float s = buf[rp];
        const float ap = eta * s + ap_x - eta * ap_y;
        ap_x = s;
        ap_y = ap;
        lp += a * (ap - lp);
        float y = lp * g + in;
        // A bow injects energy every sample, so the loop needs a limit or it
        // runs away; stick-slip has one anyway. PLUCK and STRIKE stay linear,
        // so their decay is exactly the T60 the knob asks for.
        if (saturate) y = std::tanh(y);
        buf[w] = y;
        w = wrap(w + 1);
        return y;
    }
};

// Phase delay, in samples, at `w` radians per sample, of the first-order
// allpass (eta + z^-1) / (1 + eta z^-1) with eta = (1 - d) / (1 + d). It is d
// at DC and drifts from it toward Nyquist, which is why the tuning solves for
// d at the note's own frequency rather than using d.
inline double allpass_delay(double d, double w) {
    const double eta = (1.0 - d) / (1.0 + d);
    const double ph = std::atan2(-std::sin(w), eta + std::cos(w)) -
                      std::atan2(-eta * std::sin(w), 1.0 + eta * std::cos(w));
    return -ph / w;
}

struct Params {
    int   exc = kPluck;
    float decay = 1.6f;        // s to -60 dB
    float damp = 0.35f;
    float pos = 0.22f;         // fraction of a period
    float tone = 0.6f;
    float force = 0.5f;
    float body = 0.3f;
    float spread = 6.0f;       // cents, total
    Biquad body_filter[kModes];   // designed by the engine, once per sample rate
};

struct Voice {
    const Params* p = nullptr;
    Line   line[kStrings];
    BiquadState body_state[kModes];
    uint32_t rng = 1;
    double base = 440.0;
    float  vel = 0.0f;
    float  cents = 0.0f;
    float  bow_z[kStrings] = {};              // each string's last white sample, for the bow's DC block
    bool   held = false;

    float white() {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        return float(int32_t(rng)) * (1.0f / 2147483648.0f);
    }

    void set_pitch(float c) { cents = c; }
    bool done() const { return true; }        // the amp envelope is the gate
    void stop() { held = false; }

    void reset() {
        for (Line& l : line) l.clear();
        for (BiquadState& b : body_state) b.clear();
        vel = 0.0f;
        cents = 0.0f;
        for (float& z : bow_z) z = 0.0f;
        held = false;
    }

    void start(int note, float velocity, const arb_host& host) {
        base = 440.0 * std::pow(2.0, double(note - 69) / 12.0);
        vel = velocity;
        held = true;
        rng = 0x85ebca6bu ^ (uint32_t(note) * 2246822519u) ^ host.rng_seed;
        if (rng == 0) rng = 1;
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        for (BiquadState& b : body_state) b.clear();
        for (std::size_t s = 0; s < kStrings; ++s) {
            line[s].clear();
            tune(s, sr, damp_coef());
            if (p->exc != kBow) excite(s);
        }
    }

    float damp_coef() const { return 1.0f - 0.85f * p->damp; }   // one-pole, 1 = no damping

    void set_delay(std::size_t s, double sr) {
        const double half = double(p->spread) * 0.5;
        const double off = (s == 0 ? -half : +half) + double(cents);
        const double f = std::max(kMinHz, base * std::pow(2.0, off / 1200.0));
        line[s].delay = std::clamp(sr / f, 4.0, double(line[s].cap) - 2.0);
    }

    // Sets string s's line length and allpass for its note and returns its
    // loop gain (#647). Both are taken at the note's own frequency w0, not at
    // DC: the DAMP one-pole delays and attenuates a C7 far less than its DC
    // figures say, so the DC phase delay (1-a)/a over-shortened the line (C7
    // +15 ct at DAMP 0.75), and its loss at w0 went uncounted, so DAMP also
    // shortened the fundamental's ring. Now the loop's phase delay at w0 is the
    // period, so DAMP does not detune, and its gain at w0 is the DECAY T60, up
    // to the 0.9999 ceiling: the loop's gain at DC is `g` itself and may not
    // reach 1, so where DAMP's loss at w0 is more than the ceiling can make up
    // (high notes with DAMP high, README) the note rings shorter than DECAY.
    float tune(std::size_t s, double sr, float a) {
        set_delay(s, sr);
        Line& L = line[s];
        const double period = L.delay;
        const double w0 = 2.0 * kPi / period;
        const double b = 1.0 - double(a);
        const double lp_delay = std::atan2(b * std::sin(w0), 1.0 - b * std::cos(w0)) / w0;
        const double lp_gain = double(a) / std::sqrt(1.0 - 2.0 * b * std::cos(w0) + b * b);
        // What is left for the line and the allpass, never under 2.5 samples:
        // at DAMP 1 the one-pole alone is longer than the top notes' periods
        // (README: those notes go flat, and are gone within milliseconds).
        const double rest = std::clamp(period - lp_delay, 2.5, double(L.cap) - 2.0);
        // The fraction is kept in [0.5, 1.5), where the allpass is well away
        // from its pole at -1; three steps of the fixed point land it to well
        // under a cent (its phase delay moves about one for one with d).
        const int ni = int(std::floor(rest - 0.5));
        const double want = rest - double(ni);
        double d = want;
        for (int k = 0; k < 3; ++k)
            d = std::clamp(d + (want - allpass_delay(d, w0)), 0.1, 2.5);
        L.n_int = ni;
        L.eta = float((1.0 - d) / (1.0 + d));
        const double t60 = std::pow(10.0, -3.0 * (period / sr) / double(p->decay));
        return float(std::min(t60 / lp_gain, 0.9999));
    }

    // PLUCK fills one period, STRIKE an eighth of one: the excitation *is* the
    // note's spectrum, so its length is what makes one woody and one hard.
    // "One period" is the line's integer part, n_int (tune() has run): the
    // samples the loop really holds. Filled any longer, the excess is
    // overwritten unread, and the mean taken out below is no longer the mean
    // of what goes round -- a DC offset that rang under the top notes for the
    // whole of a high-DAMP note (#647).
    void excite(std::size_t s) {
        Line& L = line[s];
        const int n = std::clamp(L.n_int, 2, L.cap);
        const int burst = p->exc == kStrike ? std::max(2, n / 8) : n;
        const float a = 0.05f + 0.95f * p->tone;      // TONE: dark at 0, raw at 1
        // A strike puts its energy into an eighth of the period; without the
        // 1/sqrt(duty) it would simply be quieter than a pluck rather than
        // harder, and a knob that changes the volume is not a timbre control.
        const float amp = 0.8f * vel * (0.25f + 0.75f * p->force) *
                          std::sqrt(float(n) / float(burst));
        // The excitation goes *behind* the write pointer, over the period the
        // loop is about to read: [w - n, w). Writing it ahead of w would put it
        // where the loop overwrites it before it is ever read.
        auto at = [&](int i) { return (L.w - n + i + 2 * L.cap) % L.cap; };
        float z = 0.0f;
        for (int i = 0; i < n; ++i) {
            const float raw = i < burst ? white() : 0.0f;
            z += a * (raw - z);
            L.buf[at(i)] = z * amp;
        }
        // POS, in place and backwards so each sample still reads the un-combed
        // value below it.
        const int lag = std::clamp(int(double(p->pos) * double(n)), 1, n - 1);
        for (int i = n - 1; i >= lag; --i) L.buf[at(i)] -= L.buf[at(i - lag)];
        // The loop is a lowpass with gain, so it barely damps DC: whatever
        // mean the noise burst has sits under the note for its whole life.
        // Taking it out here is the only place it is cheap.
        double sum = 0.0;
        for (int i = 0; i < n; ++i) sum += double(L.buf[at(i)]);
        const float mean = float(sum / double(n));
        for (int i = 0; i < n; ++i) L.buf[at(i)] -= mean;
    }

    void render(float* l, float* r, uint32_t n, const arb_host& host) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const float a = damp_coef();
        const bool bow = p->exc == kBow;
        const float bow_in = bow && held ? kBowDrive * vel * (0.2f + 0.8f * p->force) : 0.0f;
        const float body = p->body;

        // The loop's tuning and gain hold still for a block, so they are
        // worked out once here and not per sample.
        float g[kStrings];
        for (std::size_t s = 0; s < kStrings; ++s) g[s] = tune(s, sr, a);

        for (uint32_t i = 0; i < n; ++i) {
            float mix = 0.0f;
            for (std::size_t s = 0; s < kStrings; ++s) {
                // A first difference of the noise: the loop hardly damps DC,
                // so unfiltered noise parks an offset under a bowed note for
                // as long as it is held.
                float in = 0.0f;
                if (bow_in != 0.0f) {
                    const float w = white();
                    in = bow_in * 0.5f * (w - bow_z[s]);
                    bow_z[s] = w;
                }
                mix += line[s].tick(in, g[s], a, bow);
            }
            mix *= 0.5f;
            if (body > 0.0f) {
                const float wet = body_state[0].run(p->body_filter[0], mix) +
                                  body_state[1].run(p->body_filter[1], mix);
                mix = mix * (1.0f - 0.3f * body) + body * wet;
            }
            l[i] = mix * kTrim;
            r[i] = l[i];                               // mono voice (§3.7.3)
        }
    }
};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/string");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kExc]    = arb::param_enum("exc", "EXCITE", kExcLabels, kNumExc, kPluck);
    d.params[kDecay]  = arb::param_log("decay", "DECAY", 0.05f, 8.0f, 1.6f, 0.0f, "s");
    d.params[kDamp]   = arb::param_lin("damp", "DAMP", 0.0f, 1.0f, 0.35f, 0.01f, "");
    d.params[kPos]    = arb::param_lin("pos", "POS", 2.0f, 50.0f, 22.0f, 1.0f, "%");
    d.params[kTone]   = arb::param_lin("tone", "TONE", 0.0f, 1.0f, 0.6f, 0.01f, "");
    d.params[kForce]  = arb::param_lin("force", "FORCE", 0.0f, 1.0f, 0.5f, 0.01f, "");
    d.params[kBody]   = arb::param_lin("body", "BODY", 0.0f, 1.0f, 0.3f, 0.01f, "");
    d.params[kSpread] = arb::param_lin("spread", "SPREAD", 0.0f, 20.0f, 6.0f, 0.5f, "ct");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    Params params;
    float  norm[kNumParams]{};
    std::vector<float> lines;               // every voice's delay memory, once
    uint32_t sr = 0;
    arb::PolyHost<Voice, kVoices> poly;

    explicit Engine(uint32_t sample_rate) {
        // Sized at create(), which is the control thread: §3.7.1 forbids the
        // allocation, not the memory. 154 kB at 48 kHz, for eight voices of
        // two strings that can each reach 20 Hz.
        const uint32_t rate = sample_rate ? sample_rate : 48000;
        const int cap = int(double(rate) / kMinHz) + 4;
        lines.assign(std::size_t(cap) * kVoices * kStrings, 0.0f);
        for (std::size_t v = 0; v < kVoices; ++v) {
            poly.voice(v).p = &params;
            for (std::size_t s = 0; s < kStrings; ++s) {
                Line& L = poly.voice(v).line[s];
                L.buf = lines.data() + (v * kStrings + s) * std::size_t(cap);
                L.cap = cap;
            }
        }
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
        design(rate);
    }

    void design(uint32_t rate) {
        sr = rate;
        for (std::size_t i = 0; i < kModes; ++i)
            params.body_filter[i].design(kBodyHz[i], kBodyQ[i], double(rate));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kExc:    params.exc = std::clamp(int(v), 0, kNumExc - 1); break;
        case kDecay:  params.decay = v; break;
        case kDamp:   params.damp = v; break;
        case kPos:    params.pos = v / 100.0f; break;
        case kTone:   params.tone = v; break;
        case kForce:  params.force = v; break;
        case kBody:   params.body = v; break;
        default:      params.spread = v; break;
        }
    }

    // The body design depends on the sample rate, which create() may not have
    // had (arbutus-run passes one; a host that did not would design at 48 k).
    void process(const arb_host& host, arb_io& io) {
        if (host.sample_rate && host.sample_rate != sr) design(host.sample_rate);
        poly.process(host, io);
    }
};

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host ? host->sample_rate : 48000u);
}
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->poly.reset(); }
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
    nullptr,                 // format_param: every slot has a unit the host can draw
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
