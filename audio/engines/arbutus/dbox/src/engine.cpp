// arbutus/dbox — a drum box: 24 synthesised pads, no samples anywhere.
//
// `arbutus/drum` plays a kit you recorded. This one *is* the kit: every pad is
// a little analogue-style voice and the eight knobs shape the one you last
// hit. That is the same page and the same gestures (§15.3: every parameter is
// `per_note`, the UI edits the last-hit key and sends `set_note_param`), with
// one difference that goes all the way down to the host --
//
// **a pad here has no file.** Its kit document is `{"params": {...}}` per pad
// and nothing else. §15.3 gives the pad shape `{file, params}` and never says
// a kit must be samples, so `load_kit` was relaxed to accept a pad that is
// parameters only; everything else about a kit -- `set_note_param`, `copy_pad`,
// the document being the save -- works unchanged. [deviation, audio/CLAUDE.md]
//
// **`honours_env` is 0**, and that is the point of the box. DDR-053 asked for a
// per-pad AD; `arbutus/drum` could not give it, because its eight slots were
// already §15.3's sample list, so it took the instrument's global AD instead
// (§15.11's deviation). Here DECAY *is* per pad, so a global envelope on top
// would only be able to make things shorter. DRUM › ENV has nothing to do on
// this box; the README says so.
//
// MODE is what the pad is, and every other knob means the same thing in all
// six voices -- TUNE moves it, DECAY is how long, SNAP is the pitch drop at
// the front, TONE is dark-to-bright. That is deliberate: a drum machine with
// six different panels is six drum machines.
//
// Ranges are the author's job (DDR-044); the README says what every one is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <new>
#include <string>

#include <nlohmann/json.hpp>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr int kPads = 24;
constexpr int kBaseMidi = 53;      // F3, as arbutus/drum (emulator app/keys.py)
constexpr int kNumParams = 8;
constexpr double kPi = 3.14159265358979324;

enum Idx { kMode = 0, kTune, kDecay, kSnap, kTone, kDrive, kLevel, kPan };
enum Mode { kKick = 0, kSnare, kTom, kHat, kClap, kRim, kNumModes };
const char* const kModeLabels[] = {"KICK", "SNARE", "TOM", "HAT", "CLAP", "RIM"};

// What each mode is, before any knob is touched. The decay scale is what makes
// one DECAY knob mean "long" on a kick and "long" on a hat: a hat that rang for
// the kick's 0.35 s would be a cymbal.
struct ModeSpec {
    double hz;            // body pitch at TUNE 0
    double hz2;           // second body partial (0 = none)
    double drop_oct;      // pitch-envelope depth at SNAP 100 %
    double drop_s;        // pitch-envelope time constant
    double decay_scale;   // multiplies DECAY
    double noise;         // noise in the mix at TONE 50 %
    double filt_hz;       // noise filter centre at TONE 50 %
    int    filt;          // 0 = bandpass, 1 = highpass
};

constexpr ModeSpec kSpec[kNumModes] = {
    /* KICK  */ {55.0,    0.0,   3.0, 0.030, 1.00, 0.06,  1800.0, 1},
    /* SNARE */ {180.0, 270.0,   1.0, 0.020, 0.55, 0.65,  1600.0, 0},
    /* TOM   */ {110.0,    0.0,  1.4, 0.060, 0.90, 0.10,  1200.0, 0},
    /* HAT   */ {6000.0,   0.0,  0.0, 0.010, 0.22, 1.00,  7000.0, 1},
    /* CLAP  */ {1000.0,   0.0,  0.0, 0.010, 0.50, 1.00,  1100.0, 0},
    /* RIM   */ {1700.0, 2600.0, 0.6, 0.006, 0.14, 0.35,  2600.0, 0},
};

// The clap's three slaps, in seconds from the hit, then the tail.
constexpr double kClapTaps[3] = {0.0, 0.010, 0.020};
constexpr double kClapSlapS = 0.004;

// Attack. Not a knob: every one of these voices starts at the front, and a
// drum machine with an attack control is a sampler. 1.5 ms is short enough to
// read as instant and long enough not to click. [default chosen here]
constexpr double kAttackS = 0.0015;

// Output trim. A synthesised pad is a full-scale oscillator where a sampled one
// is whatever was recorded, so the box needs one number to land in the same
// place as `arbutus/drum`: a kick at the default LEVEL and full velocity peaks
// near 0.44, where a kit pad peaks near 0.5. [default chosen here]
constexpr float kTrim = 0.55f;

// The HAT choke's fade: 5 ms linear, arbutus/drum's kCutFadeS (#647). It used
// to cut in one sample, a click on any open hat still ringing.
constexpr double kCutFadeS = 0.005;

// Zero-delay state-variable filter: lowpass, bandpass and highpass from one
// pair of integrators, coefficients fixed at the hit.
struct Svf {
    float g = 0.0f, k = 1.0f, a1 = 0, a2 = 0, a3 = 0;
    float ic1 = 0.0f, ic2 = 0.0f;

    void set(double hz, double q, double sr) {
        const double gg = std::tan(kPi * std::clamp(hz, 20.0, sr * 0.45) / sr);
        const double kk = 1.0 / std::max(0.05, q);
        const double d = 1.0 / (1.0 + gg * (gg + kk));
        g = float(gg); k = float(kk);
        a1 = float(d); a2 = float(gg * d); a3 = float(gg * gg * d);
    }
    void clear() { ic1 = ic2 = 0.0f; }
    // Returns bandpass and highpass; the caller picks.
    void run(float x, float& bp, float& hp) {
        const float v3 = x - ic2;
        const float v1 = a1 * ic1 + a2 * v3;
        const float v2 = ic2 + a2 * ic1 + a3 * v3;
        ic1 = 2.0f * v1 - ic1;
        ic2 = 2.0f * v2 - ic2;
        bp = v1;
        hp = x - k * v1 - v2;
    }
};

struct Pad {
    float norm[kNumParams]{};
    float val[kNumParams]{};

    // latched at the hit
    bool   playing = false;
    int    mode = kKick;
    double phase = 0.0, phase2 = 0.0;
    double inc = 0.0, inc2 = 0.0;
    double pitch_env = 0.0, pitch_k = 0.0, pitch_mul = 1.0;
    float  env = 0.0f, att_step = 0.0f, dec_k = 1.0f;
    float  noise_mix = 0.0f, body_mix = 1.0f;
    float  drive_g = 1.0f, drive_norm = 1.0f;
    float  vel = 0.0f;
    float  fade = 1.0f, fade_step = 0.0f;   // the HAT choke
    double t = 0.0;                 // seconds since the hit, for the clap
    uint32_t rng = 1;
    Svf    filt;

    void set(int idx, float n);
    void defaults();
    int  mode_of() const { return std::clamp(int(val[kMode]), 0, kNumModes - 1); }
    void cut(double sr) {
        if (!playing || fade_step > 0.0f) return;
        fade_step = float(1.0 / (kCutFadeS * sr));
    }

    float white() {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        return float(int32_t(rng)) * (1.0f / 2147483648.0f);
    }
};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/dbox");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    // See the header: DECAY is per pad, so there is nothing for the
    // instrument's AD to add.
    d.honours_env = 0;
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kMode]  = arb::per_note(arb::param_enum("mode", "MODE", kModeLabels, kNumModes, kKick));
    d.params[kTune]  = arb::per_note(arb::param_lin("tune", "TUNE", -24.0f, 24.0f, 0.0f, 0.5f, "st"));
    d.params[kDecay] = arb::per_note(arb::param_log("decay", "DECAY", 0.02f, 2.5f, 0.35f, 0.0f, "s"));
    d.params[kSnap]  = arb::per_note(arb::param_lin("snap", "SNAP", 0.0f, 100.0f, 45.0f, 1.0f, "%"));
    d.params[kTone]  = arb::per_note(arb::param_lin("tone", "TONE", 0.0f, 100.0f, 50.0f, 1.0f, "%"));
    d.params[kDrive] = arb::per_note(arb::param_lin("drive", "DRIVE", 0.0f, 100.0f, 15.0f, 1.0f, "%"));
    d.params[kLevel] = arb::per_note(arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.8f, 0.01f, ""));
    // Balance, not a pan law: unity at centre, cos toward the far side, like
    // arbutus/drum's PAN and the host's.
    d.params[kPan]   = arb::per_note(arb::param_lin("pan", "PAN", -1.0f, 1.0f, 0.0f, 0.02f, ""));
    arb::set_assets(d, {"kit"});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

void Pad::set(int idx, float n) {
    norm[idx] = arb::clamp01(n);
    val[idx] = arb::denorm(desc().params[idx], norm[idx]);
}
void Pad::defaults() {
    for (int i = 0; i < kNumParams; ++i)
        set(i, arb::norm(desc().params[i], desc().params[i].def));
}

int param_index(const std::string& id) {
    for (int i = 0; i < kNumParams; ++i)
        if (std::strncmp(desc().params[i].id, id.c_str(), sizeof desc().params[i].id) == 0) return i;
    return -1;
}

struct Engine {
    Pad pads[kPads];
    int sel = 0;                        // the pad get_param reports
    uint32_t seed = 0x243f6a88u;

    Engine() { for (Pad& p : pads) p.defaults(); }

    // Control thread (§3.2). The pads map is params only -- a `file` on a pad
    // is simply ignored here, so a kit written for arbutus/drum still loads
    // and plays as synthesised pads at whatever params it carried.
    int load_asset(const char* key, const char* path) {
        if (!key || std::string(key) != "kit" || !path) return 1;
        std::ifstream f(path);
        if (!f) return 2;
        nlohmann::json j;
        try {
            f >> j;
        } catch (const std::exception&) {
            return 3;
        }
        if (!j.is_object() || j.value("kind", std::string{}) != "kit") return 4;
        if (j.value("format", 0) != 1) return 5;
        const nlohmann::json p = j.value("pads", nlohmann::json::object());
        if (!p.is_object()) return 6;

        for (Pad& pad : pads) { pad = Pad{}; pad.defaults(); }
        for (auto it = p.begin(); it != p.end(); ++it) {
            const int idx = std::atoi(it.key().c_str());
            if (idx < 0 || idx >= kPads || !it.value().is_object()) continue;
            const nlohmann::json params = it.value().value("params", nlohmann::json::object());
            if (!params.is_object()) continue;
            for (auto pit = params.begin(); pit != params.end(); ++pit) {
                const int k = param_index(pit.key());
                // Keyed by id (§7.1); an id this version has not got is
                // dropped, as the host drops one from a patch.
                if (k >= 0 && pit.value().is_number())
                    pads[idx].set(k, pit.value().get<float>());
            }
        }
        return 0;
    }

    void set_param(uint32_t, float) {}          // §15.3: per_note slots only

    void set_note_param(uint8_t note, uint32_t idx, float n) {
        const int i = int(note) - kBaseMidi;
        if (i < 0 || i >= kPads || idx >= uint32_t(kNumParams)) return;
        pads[i].set(int(idx), n);
        sel = i;
    }

    void reset() {
        for (Pad& p : pads) { p.playing = false; p.env = 0.0f; p.filt.clear(); }
    }

    void note_on(int note, float vel, const arb_host& host, double sr) {
        const int i = note - kBaseMidi;
        if (i < 0 || i >= kPads) return;
        Pad& p = pads[i];
        const int m = p.mode_of();
        const ModeSpec& s = kSpec[m];

        // A hat is a hat: a second one cuts the first, which is what the CHOKE
        // knob does on arbutus/drum and what there is no ninth slot for here.
        if (m == kHat)
            for (int j = 0; j < kPads; ++j)
                if (j != i && pads[j].playing && pads[j].mode_of() == kHat) pads[j].cut(sr);

        const double tune = std::pow(2.0, double(p.val[kTune]) / 12.0);
        const double f1 = std::clamp(s.hz * tune, 10.0, sr * 0.45);
        const double f2 = s.hz2 > 0.0 ? std::clamp(s.hz2 * tune, 10.0, sr * 0.45) : 0.0;
        p.mode = m;
        p.phase = p.phase2 = 0.0;
        p.inc = f1 / sr;
        p.inc2 = f2 / sr;

        const double snap = double(p.val[kSnap]) / 100.0;
        p.pitch_mul = std::pow(2.0, snap * s.drop_oct);
        p.pitch_env = 1.0;
        p.pitch_k = std::exp(-1.0 / (std::max(1.0e-4, s.drop_s) * sr));

        const double dec = std::max(1.0e-3, double(p.val[kDecay]) * s.decay_scale);
        p.dec_k = float(std::pow(10.0, -3.0 / (dec * sr)));    // -60 dB at DECAY
        p.att_step = float(1.0 / std::max(1.0, kAttackS * sr));
        p.env = p.att_step;

        // TONE is one knob doing the same job in every mode: it slides the
        // body/noise balance and opens the noise filter together, so the
        // bottom of the knob is dull and the top is bright in all six.
        const double tone = double(p.val[kTone]) / 100.0;
        const double nz = std::clamp(s.noise * (0.3 + 1.4 * tone), 0.0, 1.0);
        p.noise_mix = float(nz);
        p.body_mix = float(1.0 - 0.6 * nz);
        p.filt.clear();
        p.filt.set(s.filt_hz * std::pow(2.0, (tone - 0.5) * 2.0), s.filt == 1 ? 0.7 : 1.4, sr);

        const double drive = double(p.val[kDrive]) / 100.0;
        p.drive_g = float(1.0 + 9.0 * drive);
        p.drive_norm = drive > 0.0 ? float(1.0 / std::tanh(1.0 + 9.0 * drive)) : 1.0f;

        p.vel = vel;
        p.fade = 1.0f;
        p.fade_step = 0.0f;
        p.t = 0.0;
        p.rng = seed ^ (uint32_t(note) * 2654435761u) ^ host.rng_seed;
        if (p.rng == 0) p.rng = 1;
        p.playing = true;              // a re-hit restarts this pad and no other
    }

    // `bend` is 2^(pitch_st / 12): the instrument's bend and a MOD route to
    // host.pitch move every sounding pad's body, live (#647; §15.10 lets an
    // engine off PolyHost read pitch_st itself). The noise filters stay put:
    // they are the pad's colour, set by TONE, and a bend is a pitch.
    void render(uint32_t n, float* l, float* r, double sr, double bend) {
        std::fill_n(l, n, 0.0f);
        std::fill_n(r, n, 0.0f);
        const double dt = sr > 0.0 ? 1.0 / sr : 0.0;
        for (Pad& p : pads) {
            if (!p.playing) continue;
            const ModeSpec& s = kSpec[p.mode];
            const float level = p.val[kLevel];
            const float pan = p.val[kPan];
            constexpr float kHalfPi = 1.5707963267948966f;
            const float gl = pan > 0.0f ? std::cos(pan * kHalfPi) : 1.0f;
            const float gr = pan < 0.0f ? std::cos(-pan * kHalfPi) : 1.0f;
            const float drive = p.val[kDrive] / 100.0f;
            const bool hp = s.filt == 1;

            for (uint32_t i = 0; i < n; ++i) {
                if (p.att_step == 0.0f && p.env < 1.0e-5f) { p.playing = false; break; }

                // Body: one or two sines, swept by the pitch envelope.
                const double m = 1.0 + (p.pitch_mul - 1.0) * p.pitch_env;
                float body = float(std::sin(2.0 * kPi * p.phase));
                p.phase += p.inc * m * bend;
                if (p.phase >= 1.0) p.phase -= 1.0;
                if (p.inc2 > 0.0) {
                    body = 0.65f * body + 0.35f * float(std::sin(2.0 * kPi * p.phase2));
                    p.phase2 += p.inc2 * m * bend;
                    if (p.phase2 >= 1.0) p.phase2 -= 1.0;
                }
                p.pitch_env *= p.pitch_k;

                // Noise, filtered. A clap is three slaps and a tail, which is
                // the one thing about it that is not a shorter snare.
                float nz = p.white();
                if (p.mode == kClap) {
                    float gate = 0.25f;                 // the tail
                    for (const double tap : kClapTaps)
                        if (p.t >= tap && p.t < tap + kClapSlapS) gate = 1.0f;
                    nz *= gate;
                }
                float bp, hpv;
                p.filt.run(nz, bp, hpv);
                nz = hp ? hpv : bp;

                float x = p.body_mix * body + p.noise_mix * nz;
                if (drive > 0.0f) {
                    const float wet = std::tanh(p.drive_g * x) * p.drive_norm;
                    x += drive * (wet - x);             // 0 = bit-exact bypass
                }
                const float v = x * p.env * p.vel * level * kTrim * p.fade;
                l[i] += v * gl;
                r[i] += v * gr;

                if (p.att_step > 0.0f) {
                    p.env += p.att_step;
                    if (p.env >= 1.0f) { p.env = 1.0f; p.att_step = 0.0f; }
                } else {
                    p.env *= p.dec_k;
                }
                p.t += dt;
                if (p.fade_step > 0.0f) {
                    p.fade -= p.fade_step;
                    if (p.fade <= 0.0f) { p.playing = false; break; }
                }
            }
        }
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const double bend = host.pitch_st != 0.0f ? std::pow(2.0, double(host.pitch_st) / 12.0) : 1.0;
        const uint32_t frames = io.frames;
        const arb_event* ev = io.ev_in;
        const uint32_t nev = ev ? io.n_ev_in : 0u;
        uint32_t pos = 0, ei = 0;
        for (;;) {
            while (ei < nev && std::min(ev[ei].frame, frames) <= pos) {
                const arb_event& e = ev[ei++];
                // Note-off does nothing: every voice here is a one-shot, and
                // letting go of a kick must not cut it.
                if (e.type == ARB_EV_NOTE_ON && e.b > 0.0f) note_on(int(e.a), e.b, host, sr);
            }
            if (pos >= frames) break;
            const uint32_t next = (ei < nev) ? std::min(frames, ev[ei].frame) : frames;
            render(next - pos, io.out[0] + pos, io.out[1] + pos, sr, bend);
            pos = next;
        }
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
// The last pad set_note_param touched: get_param has no note argument, and
// that pad is the one the UI is editing (as arbutus/drum does).
float e_get_param(void* h, uint32_t idx) {
    const Engine* e = static_cast<Engine*>(h);
    return idx < uint32_t(kNumParams) ? e->pads[e->sel].norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}
int   e_load_asset(void* h, const char* key, const char* path) {
    return static_cast<Engine*>(h)->load_asset(key, path);
}
void  e_set_note_param(void* h, uint8_t note, uint32_t idx, float n) {
    static_cast<Engine*>(h)->set_note_param(note, idx, n);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    e_load_asset,            // the kit, params only -- a pad here has no file
    e_set_note_param,        // every slot is per_note (§15.3)
    nullptr,                 // format_param: every slot has a unit or labels
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
