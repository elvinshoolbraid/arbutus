// arbutus/sampler v2 — one WAV, played chromatically, on PolyHost.
//
// emulator/engines/sampler.py rewritten, with the sample arriving as an asset
// rather than as a library side-channel: `load_asset(h, "sample", path)` on the
// control thread (§3.2), never a parameter, because a file is content
// (DDR-037).
//
// Pitch is resampling, and the resampling is **linear interpolation on play**,
// not a resample on load. Two reasons: a chromatic sampler is already
// resampling for every note except the root, so resampling on load would only
// add a second pass and a second set of artefacts; and the sample stays the
// size it was on disk, which matters on a CM4 with a 24-pad kit. Linear
// interpolation is audibly soft above about an octave up -- that is the trade,
// and it is the same one the emulator makes.
//
// v2 (screen-sketches SYNTH › ENGINE): START · END · LOOP-IN · LOOP-OUT /
// ROOT · TUNE · REV · FADE. v1's LOOP switch over START..END is gone:
//
//   - The loop is LOOP-IN..LOOP-OUT, clamped inside START..END, and it plays
//     while the key is held; on release the voice plays on to END. An empty
//     loop (IN at or after OUT) is no loop. Both default to 1, the end of the
//     sample, so a v1 patch with LOOP OFF sounds as it did. v1's LOOP FWD is
//     LOOP-IN = START, LOOP-OUT = END, except that the loop now lets go at
//     release instead of cycling through the release tail.
//   - REV plays END -> START, the loop reversed with it. It is latched at
//     note-on; flipping it under a ringing note would jump.
//   - FADE crossfades the loop seam over a fraction of the loop, 0 = a hard
//     loop, at most 50 %. The fade reads the material just before LOOP-IN
//     (after LOOP-OUT in reverse), so it is also capped by how much of the file
//     is there: a loop that starts on the file's first frame cannot fade.
//   - LEVEL is gone: the mixer and the record level cover it (screen-sketches).
//     The voice keeps v1's default trim, 0.8, as a constant, so a patch loads
//     at the level it always had; the host drops a patch's `level` like any id
//     the engine does not declare (PROTOCOL.md).

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>
#include <string>
#include <vector>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>
#include <wav.hpp>

namespace {

constexpr std::size_t kVoices = 8;
constexpr int kNumParams = 8;
enum { kStart = 0, kEnd, kLoopIn, kLoopOut, kRoot, kTune, kRev, kFade };

// v1's LEVEL default, kept as the voice trim so removing the knob moves no
// patch and no golden. [default chosen here: audio/CLAUDE.md]
constexpr float kTrim = 0.8f;

struct Params {
    const std::vector<float>* data = nullptr;
    uint32_t data_sr = 48000;
    int   root = 60;            // the key that plays the sample at its own rate
    float start = 0.0f, end = 1.0f;
    float loop_in = 1.0f, loop_out = 1.0f;
    float tune = 0.0f;
    float fade = 0.0f;          // fraction of the loop, 0..0.5
    bool  rev = false;

    // Everything in playback coordinates u: u = frame forward, u = (n-1) - frame
    // in reverse, so one loop below serves both directions.
    struct Bounds { double a, b, li, lo, fade; };
    Bounds bounds(bool reverse) const {
        const int64_t n = data ? int64_t(data->size()) : 0;
        // START/END exactly emulator/engines/sampler.py _bounds().
        const int64_t a = int64_t(double(start) * double(n - 1));
        const int64_t b = std::max(a + 1, int64_t(double(end) * double(n - 1)));
        const int64_t li = std::clamp(int64_t(double(loop_in) * double(n - 1)), a, b);
        const int64_t lo = std::clamp(int64_t(double(loop_out) * double(n - 1)), a, b);
        Bounds r{};
        if (!reverse) r = {double(a), double(b), double(li), double(lo), 0.0};
        else          r = {double(n - 1 - b), double(n - 1 - a), double(n - 1 - lo), double(n - 1 - li), 0.0};
        const double len = r.lo - r.li;
        if (len >= 1.0) r.fade = std::min(std::floor(double(fade) * len), std::max(0.0, r.li));
        return r;
    }
};

struct Voice {
    const Params* p = nullptr;
    double pos = 0.0;                // u, playback coordinates
    double rate = 1.0;
    float  vel = 0.0f;
    bool   playing = false;
    bool   held = false;
    bool   rev = false;
    bool   in_fade = false;          // the last frame was inside the seam crossfade
    bool   wrap_pending = false;     // released mid-seam: finish it, then let go
    float  pitch_cents = 0.0f;       // PolyHost: unison + glide + pitch_st
    float  rate_cents = 0.0f;        // the pitch_cents `rate` was worked out for
    double semis = 0.0;              // note - ROOT + TUNE, latched at note-on
    double sr_ratio = 1.0;           // file rate / host rate

    void start(int note, float velocity, const arb_host& host) {
        if (!p->data || p->data->size() < 2) { playing = false; return; }
        rev = p->rev;
        pos = p->bounds(rev).a;
        vel = velocity;
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        semis = double(note - p->root) + double(p->tune);
        sr_ratio = double(p->data_sr) / sr;
        retune();
        playing = true;
        held = true;
        in_fade = wrap_pending = false;
    }
    void retune() {
        rate = std::pow(2.0, (semis + double(pitch_cents) / 100.0) / 12.0) * sr_ratio;
        rate_cents = pitch_cents;
    }
    // The key is up: the loop lets go and the voice plays on to END under the
    // envelope's release. Mid-seam it finishes the crossfade first, so letting
    // go never steps the blend.
    void stop() {
        held = false;
        wrap_pending = in_fade;
    }
    bool done() const { return !playing; }

    float read(const std::vector<float>& d, double u) const {
        const double top = double(d.size() - 2);
        const double f = rev ? double(d.size() - 1) - u : u;
        const double clamped = std::clamp(f, 0.0, top);
        const int64_t i0 = int64_t(clamped);
        const float frac = float(clamped - double(i0));
        return d[std::size_t(i0)] * (1.0f - frac) + d[std::size_t(i0) + 1] * frac;
    }

    void render(float* l, float* r, uint32_t n, const arb_host&) {
        const std::vector<float>* d = p->data;
        if (!playing || !d || d->size() < 2) {
            std::fill_n(l, n, 0.0f);
            std::fill_n(r, n, 0.0f);
            return;
        }
        if (pitch_cents != rate_cents) retune();
        const Params::Bounds B = p->bounds(rev);       // live: loop points move a ringing note
        const double len = B.lo - B.li;
        const float g = vel * kTrim;
        for (uint32_t i = 0; i < n; ++i) {
            const bool looping = len >= 1.0 && (held || wrap_pending);
            if (playing && looping && pos >= B.lo) {
                pos = B.li + std::fmod(pos - B.li, len);
                wrap_pending = false;
            }
            if (playing && pos >= B.b) playing = false;
            if (!playing) { l[i] = 0.0f; r[i] = l[i]; in_fade = false; continue; }
            float s = read(*d, pos);
            in_fade = looping && B.fade > 0.0 && pos >= B.lo - B.fade;
            if (in_fade) {
                // Equal gain: the two readings are the same material a loop
                // length apart, so they are correlated at the seam by design.
                const float t = float((pos - (B.lo - B.fade)) / B.fade);
                s = s * (1.0f - t) + read(*d, pos - len) * t;
            }
            l[i] = s * g;
            r[i] = l[i];
            pos += rate;
        }
    }

    void set_pitch(float cents) { pitch_cents = cents; }     // before every render
    void reset() {
        playing = held = in_fade = wrap_pending = false;
        pos = 0.0; vel = 0.0f; pitch_cents = rate_cents = 0.0f;
    }
};

const char* const kRevLabels[] = {"OFF", "ON"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/sampler");
    arb::set_field(d.version, sizeof d.version, "2.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kStart]   = arb::param_lin("start",    "START",    0.0f, 1.0f, 0.0f, 0.005f, "");
    d.params[kEnd]     = arb::param_lin("end",      "END",      0.0f, 1.0f, 1.0f, 0.005f, "");
    // Default 1 and 1: an empty loop, which is no loop -- v1's LOOP OFF.
    d.params[kLoopIn]  = arb::param_lin("loop_in",  "LOOP-IN",  0.0f, 1.0f, 1.0f, 0.005f, "");
    d.params[kLoopOut] = arb::param_lin("loop_out", "LOOP-OUT", 0.0f, 1.0f, 1.0f, 0.005f, "");
    d.params[kRoot]    = arb::param("root", "ROOT", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                                    0.0f, 127.0f, 60.0f, 1.0f, "");
    // +/- an octave. Beyond that the key already exists on the keyboard, and
    // linear interpolation is not worth listening to two octaves up.
    d.params[kTune]    = arb::param_lin("tune", "TUNE", -12.0f, 12.0f, 0.0f, 0.1f, "st");
    d.params[kRev]     = arb::param_enum("rev", "REV", kRevLabels, 2, 0);
    // A fraction of the loop, not milliseconds: every position fits whatever
    // loop is set (DDR-044). Half the loop is the most a seam can borrow.
    d.params[kFade]    = arb::param_lin("fade", "FADE", 0.0f, 50.0f, 0.0f, 1.0f, "%");
    arb::set_assets(d, {"sample"});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    Params params;
    arb::wav::Sample sample;
    float norm[kNumParams]{};
    arb::PolyHost<Voice, kVoices> poly;

    Engine() {
        for (std::size_t i = 0; i < kVoices; ++i) poly.voice(i).p = &params;
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    // Control thread, before this engine is in the graph (§3.2).
    int load_asset(const char* key, const char* path) {
        if (!key || std::string(key) != "sample" || !path) return 1;
        arb::wav::Sample s;
        if (!arb::wav::load_mono(path, s).empty()) return 2;
        if (s.empty()) return 3;
        sample = std::move(s);
        params.data = &sample.data;
        params.data_sr = sample.sample_rate;
        return 0;
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        // START can never pass END, as the Python does it: one knob pushes the
        // other rather than producing an empty region that plays silence.
        case kStart:   params.start = std::min(v, params.end); break;
        case kEnd:     params.end = std::max(v, params.start); break;
        case kLoopIn:  params.loop_in = v; break;
        case kLoopOut: params.loop_out = v; break;
        case kRoot:    params.root = int(v); break;
        case kTune:    params.tune = v; break;
        case kRev:     params.rev = v >= 0.5f; break;
        default:       params.fade = v / 100.0f; break;
        }
    }

    // Positions read in seconds of the loaded file, ROOT as a note name. The
    // sample is fixed once load_asset returns, before the host ever asks, so
    // this depends only on (idx, normalised) and load-time state (the ABI rule).
    int format_param(uint32_t idx, float n, char* buf, uint32_t cap) const {
        if (!buf || cap == 0 || idx >= desc().num_params) return 0;
        const float v = arb::denorm(desc().params[idx], n);
        if (idx == kRoot) {
            static const char* const names[] = {"C", "C#", "D", "D#", "E", "F",
                                                "F#", "G", "G#", "A", "A#", "B"};
            const int note = std::clamp(int(v), 0, 127);
            std::snprintf(buf, cap, "%s%d", names[note % 12], note / 12 - 1);   // 60 = C4
            return 1;
        }
        if (idx <= kLoopOut && sample.data.size() > 1 && sample.sample_rate > 0) {
            const double s = double(v) * double(sample.data.size() - 1) / double(sample.sample_rate);
            std::snprintf(buf, cap, s < 10.0 ? "%.2f s" : "%.1f s", s);
            return 1;
        }
        return 0;
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->poly.reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < desc().num_params ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->poly.process(*host, *io);
}
int   e_load_asset(void* h, const char* key, const char* path) {
    return static_cast<Engine*>(h)->load_asset(key, path);
}
int   e_format_param(void* h, uint32_t idx, float n, char* buf, uint32_t cap) {
    return static_cast<const Engine*>(h)->format_param(idx, n, buf, cap);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    e_load_asset,
    nullptr,                 // set_note_param: no per_note params (§15.3)
    e_format_param,          // seconds for positions, a note name for ROOT
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
