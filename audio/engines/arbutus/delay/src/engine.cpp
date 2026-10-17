// arbutus/delay — the echo, and the first box that reads the tempo.
//
// `arbutus/comb` is a delay line short enough to be a pitch; this is the same
// idea long enough to be a rhythm, and that is why it is the one effect that
// has to know what the tempo is. `arb_host.tempo_bpm` reaches every box in a
// rack (the host fills one block and hands it to all of them), so TIME can be
// a note value rather than a number of milliseconds nobody can convert in
// their head while playing.
//
// TIME is `arbutus/lfo`'s bipolar knob in a second place: the centre is the
// *shortest* delay of each kind, right of it are tempo divisions (1/32 up to a
// bar, straight, triplet and dotted), left of it is free time on a log curve
// from 20 ms to 2 s. One slot, both ways of asking, and `format_param` prints
// which one you are on ("1/8", "250 ms", "1.20 s").
//
// Everything else on the panel is what makes a delay musical rather than
// correct: the loop filter (TONE) and the loop saturator (DRIVE) are *inside*
// the feedback path, so each repeat is darker and dirtier than the last the
// way a tape echo is, while the first repeat is still the sound you played.
// PING crossfades the feedback matrix from two independent delays to a true
// ping-pong; WOW is the capstan; DUCK holds the wet path down while you are
// playing and lets it up when you stop, which is the one thing that keeps a
// long delay out of the way of the notes.
//
// Ranges are the author's job (DDR-044); README.md argues every one of them.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>
#include <vector>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr double kPi = 3.14159265358979324;

enum Param : uint32_t {
    kTime, kFeedback, kTone, kMix, kPing, kWow, kDrive, kDuck, kNumParams
};

// ---- TIME ------------------------------------------------------------------
// Divisions in quarter notes, ascending, so turning right is always "longer".
// Straight, triplet and dotted, because the dotted eighth is half of what a
// delay is ever asked for and a triplet is the other half.
struct Division { const char* label; double beats; };
constexpr Division kDivisions[] = {
    {"1/32",  0.125},      {"1/16T", 1.0 / 6.0},  {"1/16",  0.25},
    {"1/8T",  1.0 / 3.0},  {"1/8",   0.5},        {"1/4T",  2.0 / 3.0},
    {"1/8D",  0.75},       {"1/4",   1.0},        {"1/2T",  4.0 / 3.0},
    {"1/4D",  1.5},        {"1/2",   2.0},        {"1/2D",  3.0},
    {"1 BAR", 4.0}};
constexpr int kNumDivisions = int(sizeof kDivisions / sizeof kDivisions[0]);

constexpr double kMsLo = 20.0, kMsHi = 2000.0;
// The line is sized once at create() for this, and a synced time longer than
// it clamps: 1 BAR is 4 s below 60 BPM, and a delay that allocates on a tempo
// change is an xrun (§3.7.1).
constexpr double kMaxDelayS = 4.0;

// FDBK's top position is a hold (#779): the loop's gain is exactly 1, so the
// repeats keep their level for ever and never grow. Below it, unchanged.
// What is already in the line is frozen as it is: TONE and DRIVE leave the
// loop (a filter or a shaper that keeps acting would take the level down
// with every pass), WOW stops where it is and the read lands on a whole
// sample (a linear read between two samples is a lowpass, and 240 passes of
// it took a 440 Hz repeat down 0.56 dB a minute), and what is played on top
// is added in. Notes layered on a gain-1 loop add up, so the line has a
// ceiling: above kHoldCeiling a soft knee (slope 1 at the knee, never past
// twice it) brings a pile-up back down; at or under it nothing is touched.
// The knee limits only what the input adds (#1027): a sample is never
// written smaller than the loop brought it, so a loud loop does not lose
// level pass after pass once the hand stops.
constexpr float kHoldCeiling = 1.0f;

// `loop` is what the line brought round, `sum` that plus the input.
float hold_ceiling(float loop, float sum) {
    const float a = std::fabs(sum);
    if (a <= kHoldCeiling || a <= std::fabs(loop)) return sum;
    const float knee = kHoldCeiling + kHoldCeiling * std::tanh((a - kHoldCeiling) / kHoldCeiling);
    return std::copysign(std::max(knee, std::fabs(loop)), sum);
}

struct Time {
    bool   sync = true;
    int    div  = 0;
    double ms   = kMsLo;
};

Time time_of(float x) {
    Time t;
    const double m = std::clamp(double(std::fabs(x)), 0.0, 1.0);
    if (x >= 0.0f) {
        t.sync = true;
        t.div = std::clamp(int(std::lround(m * (kNumDivisions - 1))), 0, kNumDivisions - 1);
    } else {
        t.sync = false;
        t.ms = kMsLo * std::pow(kMsHi / kMsLo, m);
    }
    return t;
}

// Default 1/8: in time the moment the box lands on a tab, and short enough
// that the first thing you hear is a delay and not a reverb.
constexpr float kTimeDefault = 4.0f / float(kNumDivisions - 1);

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/delay");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;                       // an effect: the ENV page is inert
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kTime]     = arb::param_lin("time",     "TIME",  -1.0f, 1.0f, kTimeDefault, 0.01f, "");
    d.params[kFeedback] = arb::param_lin("feedback", "FDBK",   0.0f, 0.95f, 0.35f, 0.01f, "");
    d.params[kTone]     = arb::param_lin("tone",     "TONE",   0.0f, 1.0f,  0.5f,  0.01f, "");
    d.params[kMix]      = arb::param_lin("mix",      "MIX",    0.0f, 1.0f,  0.3f,  0.01f, "");
    d.params[kPing]     = arb::param_lin("ping",     "PING",   0.0f, 1.0f,  0.0f,  0.01f, "");
    d.params[kWow]      = arb::param_lin("wow",      "WOW",    0.0f, 1.0f,  0.1f,  0.01f, "");
    d.params[kDrive]    = arb::param_lin("drive",    "DRIVE",  0.0f, 1.0f,  0.0f,  0.01f, "");
    d.params[kDuck]     = arb::param_lin("duck",     "DUCK",   0.0f, 1.0f,  0.0f,  0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// One channel: the line, the loop filters and the loop saturator's state.
struct Line {
    std::vector<float> buf;
    int   wpos = 0;
    float lp = 0.0f;                 // one-pole lowpass state, in the loop
    float hp_x1 = 0.0f, hp_y1 = 0.0f;

    void clear() {
        std::fill(buf.begin(), buf.end(), 0.0f);
        wpos = 0;
        lp = hp_x1 = hp_y1 = 0.0f;
    }

    // Linear interpolation, like arbutus/comb: sweeping TIME then glides
    // instead of stepping, which is what makes WOW a pitch wobble and not a
    // stutter.
    float read(double delay) const {
        const int cap = int(buf.size());
        double rp = double(wpos) - delay;
        while (rp < 0.0) rp += double(cap);
        const int i0 = int(rp) % cap;
        const int i1 = (i0 + 1) % cap;
        const float f = float(rp - std::floor(rp));
        return buf[std::size_t(i0)] * (1.0f - f) + buf[std::size_t(i1)] * f;
    }

    void write(float v) {
        buf[std::size_t(wpos)] = v;
        wpos = (wpos + 1) % int(buf.size());
    }
};

struct Engine {
    float  norm[kNumParams]{};
    double sr = 48000.0;
    float  time_x = kTimeDefault, fb = 0.35f, tone = 0.5f, mix = 0.3f;
    float  ping = 0.0f, wow = 0.1f, drive = 0.0f, duck = 0.0f;
    bool   hold = false;             // FDBK at its top (#779)
    Line   ch[2];
    double cur_delay = 0.0;          // glided toward the target, in samples
    bool   glide_armed = false;      // the first block after reset lands
    double wow_p1 = 0.0, wow_p2 = 0.25;
    float  env = 0.0f;               // DUCK's follower on the dry input

    explicit Engine(double rate) : sr(rate > 0.0 ? rate : 48000.0) {
        const std::size_t cap = std::size_t(sr * kMaxDelayS) + 8;
        for (Line& l : ch) {
            l.buf.assign(cap, 0.0f);       // create() is where allocation lives
            l.clear();
        }
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    // A macro may drive any of these every block from the audio thread (§6.2),
    // so nothing here allocates or clears a buffer.
    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kTime:     time_x = v; break;
        case kFeedback: fb = v; hold = norm[idx] >= 1.0f; break;
        case kTone:     tone = v; break;
        case kMix:      mix = v; break;
        case kPing:     ping = v; break;
        case kWow:      wow = v; break;
        case kDrive:    drive = v; break;
        default:        duck = v; break;
        }
    }

    void reset() {
        for (Line& l : ch) l.clear();
        glide_armed = false;
        wow_p1 = 0.0;
        wow_p2 = 0.25;
        env = 0.0f;
    }

    void process(const arb_host& host, arb_io& io) {
        const double rate = host.sample_rate ? double(host.sample_rate) : sr;
        const double cap = double(ch[0].buf.size());

        // ---- the target delay, in samples ---------------------------------
        const Time t = time_of(time_x);
        double target;
        if (t.sync) {
            const double bpm = host.tempo_bpm > 1.0 ? host.tempo_bpm : 120.0;
            target = kDivisions[t.div].beats * 60.0 / bpm * rate;
        } else {
            target = t.ms * rate / 1000.0;
        }
        target = std::clamp(target, 2.0, cap - 4.0);
        if (!glide_armed) {
            cur_delay = target;
            glide_armed = true;
        }
        // 80 ms toward the new time: a TIME change is a tape sweep, not a
        // click. Per sample, so the sweep is the same at every block size.
        const double glide = 1.0 - std::exp(-1.0 / (0.080 * rate));

        // ---- the loop's filters -------------------------------------------
        // TONE is one knob and two filters, both bypassed at the centre, so
        // 0.5 is bit-exact: below it a lowpass closes over the repeats, above
        // it a highpass opens under them.
        // At the hold both leave the loop (#779): the level must not fall.
        // They still run on what goes round and their output is dropped, so
        // leaving the hold resumes them where the signal is, not where it was
        // when the hold began (a click that repeated, #1027).
        const bool use_lp = tone < 0.5f, use_hp = tone > 0.5f;
        float a_lp = 1.0f, a_hp = 0.0f;
        if (use_lp) {
            const double hz = 16000.0 * std::pow(400.0 / 16000.0, double(1.0f - tone * 2.0f));
            a_lp = float(1.0 - std::exp(-2.0 * kPi * hz / rate));
        }
        if (use_hp) {
            const double hz = 20.0 * std::pow(800.0 / 20.0, double((tone - 0.5f) * 2.0f));
            a_hp = float(std::exp(-2.0 * kPi * hz / rate));
        }

        const bool  use_drive = !hold && drive > 0.0f;
        const float dg = 1.0f + 6.0f * drive;
        const float dnorm = use_drive ? 1.0f / std::tanh(dg) : 1.0f;
        // The shaper's slope at 0 is (1-drive) + drive*dg*dnorm, up to 7: with
        // it in the loop, FDBK 0.35 at DRIVE 0.6 repeated for ever (#630).
        // Dividing by it keeps the loop's gain <= 1 at every level (tanh lies
        // under its tangent), so DRIVE adds grit and never gain.
        const float dslope = use_drive ? 1.0f / ((1.0f - drive) + drive * dg * dnorm) : 1.0f;

        // ---- WOW: two slow tones whose ratio is not a whole number ---------
        // One sine is a vibrato; two at 0.37 and 1.73 Hz never line up inside a
        // phrase, which is what a capstan sounds like.
        const double inc1 = 0.37 / rate, inc2 = 1.73 / rate;
        const double wow_depth = double(wow) * 0.004;            // of the delay
        const double wow_fixed = double(wow) * (0.00012 * rate); // and ~0.12 ms

        // ---- DUCK ----------------------------------------------------------
        const float atk = float(1.0 - std::exp(-1000.0 / (5.0 * rate)));
        const float rel = float(1.0 - std::exp(-1000.0 / (250.0 * rate)));
        constexpr float kDuckKnee = 0.08f;        // ~-22 dBFS half-way point

        const float dry_g = 1.0f - mix;
        const float wet_g = mix;
        const float cross = ping, direct = 1.0f - ping;
        // At the hold the matrix goes to PING's nearer end (#1027): between
        // them the side-to-side part of the repeats has gain 1 - 2p and would
        // fade; identity and swap pass every part whole. The input's routing
        // keeps PING as set.
        const float mcross = hold ? (ping < 0.5f ? 0.0f : 1.0f) : cross;
        const float mdirect = 1.0f - mcross;
        const float loop_g = hold ? 1.0f : fb;

        const float* il = io.in[0];
        const float* ir = io.in[1] ? io.in[1] : io.in[0];
        float* ol = io.out[0];
        float* orr = io.out[1];
        if (!il) {                       // §3.7.3: write both channels, always
            std::fill_n(ol, io.frames, 0.0f);
            std::fill_n(orr, io.frames, 0.0f);
            return;
        }

        for (uint32_t i = 0; i < io.frames; ++i) {
            cur_delay += (target - cur_delay) * glide;
            if (!hold) {                 // at the hold the capstan stops
                wow_p1 += inc1;
                wow_p2 += inc2;
                if (wow_p1 >= 1.0) wow_p1 -= 1.0;
                if (wow_p2 >= 1.0) wow_p2 -= 1.0;
            }
            const double lfo = 0.6 * std::sin(2.0 * kPi * wow_p1) +
                               0.4 * std::sin(2.0 * kPi * wow_p2);
            double d = std::clamp(cur_delay * (1.0 + wow_depth * lfo) +
                                  wow_fixed * lfo, 2.0, cap - 4.0);
            if (hold) d = std::round(d); // a whole sample: no lowpass per pass

            const float rl = ch[0].read(d);
            const float rr = ch[1].read(d);

            // The feedback matrix: [[1-p, p], [p, 1-p]]. Its largest gain is 1
            // at every p, so PING cannot make a stable setting run away.
            float bl = mdirect * rl + mcross * rr;
            float br = mdirect * rr + mcross * rl;

            for (int c = 0; c < 2; ++c) {
                float& v = c == 0 ? bl : br;
                Line& l = ch[c];
                if (use_lp) {
                    l.lp += a_lp * (v - l.lp);
                    if (!hold) v = l.lp;
                }
                if (use_hp) {
                    const float y = a_hp * (l.hp_y1 + v - l.hp_x1);
                    l.hp_x1 = v;
                    l.hp_y1 = y;
                    if (!hold) v = y;
                }
                if (use_drive) {
                    const float sat = std::tanh(dg * v) * dnorm;
                    v = (v + drive * (sat - v)) * dslope;
                }
            }

            const float xl = il[i], xr = ir[i];      // in and out may alias
            // PING also routes the input: at 1 the mid goes into the left line
            // only, so a centred source (every synth) starts left and the
            // matrix bounces it. With L = R in both lines the swap was
            // silent (#630).
            const float mid = 0.5f * (xl + xr);
            float wl = direct * xl + cross * mid + loop_g * bl;
            float wr = direct * xr + loop_g * br;
            if (hold) {
                wl = hold_ceiling(bl, wl);
                wr = hold_ceiling(br, wr);
            }
            ch[0].write(wl);
            ch[1].write(wr);

            // DUCK reads the *dry* input, so the wet path gets out of the way
            // of what is being played and comes back when the hand stops.
            const float mag = std::max(std::fabs(xl), std::fabs(xr));
            env += (mag - env) * (mag > env ? atk : rel);
            const float g = duck > 0.0f ? 1.0f - duck * (env / (env + kDuckKnee)) : 1.0f;

            ol[i] = dry_g * xl + wet_g * g * rl;
            orr[i] = dry_g * xr + wet_g * g * rr;
        }
    }
};

// "1/8", "250 ms", "1.20 s". Pure in (idx, normalised), as the ABI requires.
int format_time(float n, char* buf, uint32_t size) {
    const Time t = time_of(arb::denorm(desc().params[kTime], n));
    int w;
    if (t.sync)             w = std::snprintf(buf, size, "%s", kDivisions[t.div].label);
    else if (t.ms < 1000.0) w = std::snprintf(buf, size, "%.0f ms", t.ms);
    else                    w = std::snprintf(buf, size, "%.2f s", t.ms / 1000.0);
    return w > 0;
}

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host ? double(host->sample_rate) : 48000.0);
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
int   e_format_param(void*, uint32_t idx, float n, char* buf, uint32_t size) {
    if (!buf || !size) return 0;
    if (idx == kTime) return format_time(n, buf, size);
    if (idx == kFeedback && n >= 1.0f)               // the top is the hold (#779)
        return std::snprintf(buf, size, "HOLD") > 0;
    return 0;
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    e_format_param,          // TIME's readout, FDBK's HOLD (§15.1)
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
