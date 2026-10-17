// arbutus/wavetable — a table scanned, warped and folded.
//
// The table is **content, not a parameter** (DDR-037): `load_asset(h,
// "wavetable", path)` takes a 16-bit mono WAV of `frames x 2048` samples, one
// cycle per frame, written by `audio/tools/mkwavetable.py`. POS scans between
// adjacent frames; everything else on the panel is about what happens to the
// cycle after it is read.
//
// **Aliasing is the whole engineering problem of a wavetable oscillator**, and
// it is solved here at load time, not per sample. A 2048-point cycle can hold
// 1024 harmonics; played at 400 Hz, harmonic 60 is already at Nyquist and
// every one above it folds back as an inharmonic whistle. So `load_asset`
// builds a **mip chain** per frame -- level k is the frame at 2048>>k samples,
// band-limited to its own Nyquist by a half-band FIR before each decimation --
// and the oscillator picks the longest level whose top harmonic still fits
// under the sample rate at the note being played. It costs 2x the table's
// memory and nothing per sample, which is the trade a CM4 wants.
//
// WARP and FOLD both make harmonics that the table did not have, so the level
// choice pretends the note is higher when either is up: less top end, and no
// whistle. That is stated on the panel's behalf in the README -- a folded
// wavetable that is a little duller is a sound; one that is ringing at
// 11 kHz in a different key is not.
//
// Ranges are the author's job (DDR-044); the README says what every one is for.

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
constexpr int kNumParams = 7;
enum Idx { kPos = 0, kSweep, kWarp, kFold, kSpread, kSub, kDrift };

// The file's convention, shared with audio/tools/mkwavetable.py.
constexpr int kFrameLen = 2048;
constexpr int kMaxFrames = 64;          // 64 x 4096 floats = 1 MB of mip chain
constexpr int kMinLevel = 4;            // shortest mip: 4 samples = 2 harmonics
constexpr float kTrim = 0.45f;          // headroom, as the other synth boxes take

// Half-band lowpass for the mip chain: windowed sinc at pi/2, 15 taps. Short
// enough to be honest about (a little aliasing survives into the top octave of
// each level) and long enough that the fold-back is far below the signal.
constexpr int kTaps = 15;

struct Table {
    // level[k] holds `frames` cycles of (kFrameLen >> k) samples, end to end.
    std::vector<float> level[12];
    int levels = 0;
    int frames = 0;

    bool empty() const { return frames == 0; }
    int len(int k) const { return kFrameLen >> k; }

    // The longest level whose top harmonic (len/2) still fits below Nyquist at
    // `hz`. Longer = more harmonics; too long = aliasing.
    int level_for(double hz, double sr) const {
        if (hz <= 0.0) return 0;
        const double want = sr / hz;             // len such that len/2 * hz = sr/2
        int k = 0;
        while (k + 1 < levels && double(len(k)) > want) ++k;
        return k;
    }

    // One cycle, linear-interpolated, at phase p in [0,1).
    float read(int k, int frame, double p) const {
        const int n = len(k);
        const std::vector<float>& v = level[k];
        const double x = p * double(n);
        int i0 = int(x);
        if (i0 >= n) i0 = n - 1;
        const float fr = float(x - double(i0));
        const std::size_t base = std::size_t(frame) * std::size_t(n);
        const float a = v[base + std::size_t(i0)];
        const float b = v[base + std::size_t(i0 + 1 == n ? 0 : i0 + 1)];
        return a + fr * (b - a);
    }
};

struct Params {
    float pos = 0.0f;          // 0..1
    float sweep = 0.0f;        // -1..1, ENV -> POS
    float warp = 0.0f;         // -1..1
    float fold = 0.0f;         // 0..1
    float spread = 10.0f;      // cents, total
    float sub = 0.2f;
    float drift = 0.15f;       // 0..1
    const Table* table = nullptr;
};

// Casio-style phase distortion: the cycle's first half plays faster (formant
// up) or slower (formant down) and the second half takes up the slack, so the
// waveform's period is untouched and only its shape moves.
inline double warp_phase(double p, float w) {
    if (w == 0.0f) return p;
    const double d = std::clamp(0.5 - 0.45 * double(w), 0.05, 0.95);
    return p < d ? 0.5 * p / d : 0.5 + 0.5 * (p - d) / (1.0 - d);
}

struct Voice {
    const Params* p = nullptr;
    arb::Adsr env;                    // the ENV page's shape, for SWEEP
    double ph[2]{};                   // the two detuned oscillators
    double sub_ph = 0.0;
    float  dc_x = 0.0f, dc_y = 0.0f;  // DC blocker; WARP is not mean-preserving
    double drift_ph0 = 0.0, drift_rate = 0.1;   // drift phase at note-on, and its rate
    double base = 440.0;
    arb::Smoothed<float> vel;         // a retrigger ramps to its velocity (#828)
    float  cents = 0.0f;
    uint64_t frame = 0;               // frames since note-on: the control grid (#56)
    int    f0 = 0, f1 = 0;            // the table frames POS currently sits between,
    float  fx = 0.0f;                 // and where between them -- one per control cell

    void set_pitch(float c) { cents = c; }
    bool done() const { return true; }        // the amp envelope is the gate
    void stop() { env.gate_off(); }

    void reset() {
        env = arb::Adsr{};
        ph[0] = ph[1] = sub_ph = drift_ph0 = 0.0;
        dc_x = dc_y = 0.0f;
        vel.snap(0.0f);
        cents = 0.0f;
        frame = 0;
        f0 = f1 = 0;
        fx = 0.0f;
    }

    void start(int note, float velocity, const arb_host& host) {
        base = 440.0 * std::pow(2.0, double(note - 69) / 12.0);
        vel.snap(velocity);
        ph[0] = 0.0;
        // Half a cycle apart, so SPREAD 0 is two oscillators beating at zero
        // rather than one at twice the level -- the knob is a beat rate, not a
        // voice count, and the patch's level does not jump when it leaves 0.
        ph[1] = 0.5;
        sub_ph = 0.0;
        dc_x = dc_y = 0.0f;
        // Each note drifts at its own rate: a chord breathes instead of
        // pulsing as one. Deterministic in the note, so a golden repeats.
        drift_rate = 0.06 + 0.11 * std::fmod(double(note) * 0.6180339887, 1.0);
        drift_ph0 = std::fmod(double(note) * 0.381966, 1.0);
        frame = 0;                // the first render() is a cell start
        env.gate_on(host);
    }

    // MONO/LEGATO on a sounding voice (#646): the oscillators, the DC blocker
    // and the drift run on (a new drift rate would jump POS); MONO restarts
    // SWEEP from where it is, a legato change leaves it and the velocity; a
    // retrigger glides to the new velocity.
    void retarget(int note, float velocity, const arb_host& host, bool retrigger) {
        base = 440.0 * std::pow(2.0, double(note - 69) / 12.0);
        if (retrigger) {
            vel.set_ramp_frames(arb::retarget_ramp_frames(host));
            vel.set(velocity);
            env.gate_on(host);
        }
    }

    void render(float* l, float* r, uint32_t n, const arb_host& host) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const Table* t = p->table;
        if (!t || t->empty()) {
            for (uint32_t i = 0; i < n; ++i) { l[i] = 0.0f; r[i] = 0.0f; }
            return;
        }

        const double dt = sr > 0.0 ? 1.0 / sr : 0.0;
        const double f = base * std::pow(2.0, double(cents) / 1200.0);
        const double half = double(p->spread) * 0.5;
        const double hz[2] = {f * std::pow(2.0, -half / 1200.0),
                              f * std::pow(2.0, +half / 1200.0)};
        // WARP and FOLD add harmonics the table has not got, so the level is
        // picked as if the note were higher. Duller, never whistling.
        const double headroom = 1.0 + 2.0 * double(std::fabs(p->warp)) + 2.0 * double(p->fold);
        const int k = t->level_for(std::max(hz[0], hz[1]) * headroom, sr);

        const double inc[2] = {hz[0] / sr, hz[1] / sr};
        const double sub_inc = f * 0.5 / sr;
        const float fold_g = 1.0f + 5.0f * p->fold;
        const float warp = p->warp;
        const float sub = p->sub;

        const bool ramp = !vel.settled();
        const float g = kTrim * (ramp ? 1.0f : vel.advance(n)) / (1.0f + 0.7f * sub);   // SUB is a balance

        // SWEEP's envelope and DRIFT are read on the SDK's control grid, not
        // once per call (#56). Once per call is one POS per *block*, and the
        // block is the host's to choose; this box moved by 60 dBFS between 512
        // frames and 1, which `tests/ragged.sh` exists to catch and could not,
        // because its own exit status was `sed`'s. DRIFT is computed from the
        // absolute frame rather than accumulated, so cutting a block into cells
        // cannot move even its last bit.
        for (uint32_t done = 0; done < n; ) {
            const uint32_t m = arb::ctrl_run(frame, n - done);
            if (arb::ctrl_cell_start(frame)) {
                double dph = drift_ph0 + drift_rate * double(frame) * dt;
                dph -= std::floor(dph);
                const float drift = p->drift * 0.25f * float(std::sin(6.283185307 * dph));
                const float pos = std::clamp(p->pos + p->sweep * env.value + drift, 0.0f, 1.0f);
                const double fpos = double(pos) * double(t->frames - 1);
                f0 = int(fpos);
                if (f0 > t->frames - 1) f0 = t->frames - 1;
                f1 = std::min(f0 + 1, t->frames - 1);
                fx = float(fpos - double(f0));
            }

            for (uint32_t i = done; i < done + m; ++i) {
                float x = 0.0f;
                for (int o = 0; o < 2; ++o) {
                    const double wp = warp_phase(ph[o], warp);
                    const float a = t->read(k, f0, wp);
                    const float b = f1 == f0 ? a : t->read(k, f1, wp);
                    x += a + fx * (b - a);
                    ph[o] += inc[o];
                    if (ph[o] >= 1.0) ph[o] -= 1.0;
                }
                x *= 0.5f;
                // Phase distortion is not mean-preserving: warping a sine hard
                // enough parks 0.23 of DC under the note, which a resonant filter
                // or a reverb downstream would turn into a thump. One-pole
                // blocker, -3 dB at ~15 Hz ((1 - 0.998) * 48000 / 2pi; it was
                // written "~32 Hz" until #647), always on so the knob has no
                // seam at zero.
                dc_y = x - dc_x + 0.998f * dc_y;
                dc_x = x;
                x = dc_y;
                if (p->fold > 0.0f) {
                    // Sine folding, crossfaded by the knob, so 0 is bit-exact
                    // bypass and the top of the knob is a fold and not a clip.
                    const float wet = std::sin(1.5707963f * fold_g * x);
                    x += p->fold * (wet - x);
                }
                if (sub > 0.0f) {
                    x += sub * float(std::sin(6.283185307 * sub_ph));
                    sub_ph += sub_inc;
                    if (sub_ph >= 1.0) sub_ph -= 1.0;
                }
                l[i] = ramp ? x * (g * vel.next()) : x * g;
                r[i] = l[i];                      // mono voice (§3.7.3)
            }

            for (uint32_t i = 0; i < m; ++i) env.step(dt);
            frame += m;
            done += m;
        }
    }
};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/wavetable");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kPos]    = arb::param_lin("pos", "POS", 0.0f, 100.0f, 0.0f, 1.0f, "%");
    d.params[kSweep]  = arb::param_lin("sweep", "SWEEP", -100.0f, 100.0f, 0.0f, 1.0f, "%");
    d.params[kWarp]   = arb::param_lin("warp", "WARP", -100.0f, 100.0f, 0.0f, 1.0f, "%");
    d.params[kFold]   = arb::param_lin("fold", "FOLD", 0.0f, 100.0f, 0.0f, 1.0f, "%");
    d.params[kSpread] = arb::param_lin("spread", "SPREAD", 0.0f, 30.0f, 10.0f, 0.5f, "ct");
    d.params[kSub]    = arb::param_lin("sub", "SUB", 0.0f, 1.0f, 0.2f, 0.01f, "");
    d.params[kDrift]  = arb::param_lin("drift", "DRIFT", 0.0f, 100.0f, 15.0f, 1.0f, "%");
    arb::set_assets(d, {"wavetable"});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    Params params;
    Table  table;
    float  norm[kNumParams]{};
    arb::PolyHost<Voice, kVoices> poly;

    Engine() {
        for (std::size_t i = 0; i < kVoices; ++i) poly.voice(i).p = &params;
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    // Control thread, before this engine is in the graph (§3.2): allocation
    // and file I/O are allowed here and nowhere else.
    int load_asset(const char* key, const char* path) {
        if (!key || std::string(key) != "wavetable" || !path) return 1;
        arb::wav::Sample s;
        if (!arb::wav::load_mono(path, s).empty()) return 2;
        const int frames = int(s.data.size() / std::size_t(kFrameLen));
        if (frames < 1) return 3;
        if (s.data.size() % std::size_t(kFrameLen) != 0) return 4;

        Table t;
        t.frames = std::min(frames, kMaxFrames);
        t.level[0].assign(s.data.begin(),
                          s.data.begin() + std::size_t(t.frames) * kFrameLen);
        t.levels = 1;
        // Half-band window, built once: sinc(pi/2) x Hann.
        float h[kTaps];
        float sum = 0.0f;
        for (int i = 0; i < kTaps; ++i) {
            const double x = double(i - kTaps / 2);
            const double sinc = x == 0.0 ? 0.5 : std::sin(3.14159265358979324 * x * 0.5) /
                                                 (3.14159265358979324 * x);
            const double win = 0.5 - 0.5 * std::cos(2.0 * 3.14159265358979324 *
                                                    double(i) / double(kTaps - 1));
            h[i] = float(sinc * win);
            sum += h[i];
        }
        for (float& v : h) v /= sum;

        while (t.levels < 12 && t.len(t.levels - 1) > kMinLevel) {
            const int src = t.len(t.levels - 1);
            const int dst = src / 2;
            const std::vector<float>& in = t.level[t.levels - 1];
            std::vector<float>& out = t.level[t.levels];
            out.assign(std::size_t(t.frames) * std::size_t(dst), 0.0f);
            for (int fr = 0; fr < t.frames; ++fr) {
                const std::size_t ib = std::size_t(fr) * std::size_t(src);
                const std::size_t ob = std::size_t(fr) * std::size_t(dst);
                for (int i = 0; i < dst; ++i) {
                    float acc = 0.0f;
                    for (int j = 0; j < kTaps; ++j) {
                        // Circular: a cycle has no edges, so the filter wraps.
                        int q = 2 * i + j - kTaps / 2;
                        q %= src;
                        if (q < 0) q += src;
                        acc += h[j] * in[ib + std::size_t(q)];
                    }
                    out[ob + std::size_t(i)] = acc;
                }
            }
            ++t.levels;
        }
        table = std::move(t);
        params.table = &table;
        return 0;
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kPos:    params.pos = v / 100.0f; break;
        case kSweep:  params.sweep = v / 100.0f; break;
        case kWarp:   params.warp = v / 100.0f; break;
        case kFold:   params.fold = v / 100.0f; break;
        case kSpread: params.spread = v; break;
        case kSub:    params.sub = v; break;
        default:      params.drift = v / 100.0f; break;
        }
    }

    // POS as a percentage says nothing about a table with sixteen frames in
    // it. The frame it lands on does. The table is fixed once load_asset has
    // returned, so this depends only on (idx, normalised) and load-time state.
    int format_param(uint32_t idx, float n, char* buf, uint32_t cap) const {
        if (!buf || cap == 0 || idx != kPos || table.empty()) return 0;
        const float v = arb::denorm(desc().params[idx], n) / 100.0f;
        std::snprintf(buf, cap, "%.1f/%d", double(v) * double(table.frames - 1),
                      table.frames - 1);
        return 1;
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
    e_format_param,          // POS as the frame it lands on
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
