// arbutus/drum v2 — 24 pads on chromatic keys, one kit, one voice per pad,
// every parameter per pad (engine-spec §15.3, DDR-053).
//
// **The kit arrives as one asset, not 24.** `load_asset(h, "kit", path)` gets
// the resolved §7.3 `kit.json`; this engine reads the `pads` map and opens the
// WAVs itself. A descriptor can declare at most ARB_MAX_ASSETS (3) keys, and a
// pad map is variable-length content, so one key naming a document is the only
// shape that fits. Pad paths that are relative resolve against the kit.json's
// own folder, so a kit folder is movable; the host hands down absolute ones.
//
// **Every parameter is per_note.** The UI edits the last-hit pad and sends
// set_note_param(note, idx, v); set_param on these slots is ignored (§15.3).
// The values are stored in the kit, because they describe the sample:
//   "pads": {"0": {"file": "kick.wav", "params": {"start": 0.1, ...}}}
// A pad written the v1 way, "0": "kick.wav", is read as that file at defaults
// (§7.1 "older format migrates forward"); both shapes are format 1.
//
// **No PolyHost.** A one-shot pad plays to the end of its sample whether or not
// the key is down, which PolyHost's release is the opposite of. So this engine
// owns its polyphony: 24 voices, one per pad, a re-hit restarting that pad.
//
// **The envelope is an AD from the host block** (DDR-053: DRUM › ENV is ATK ·
// DEC, S and R greyed): honours_env = 1, env_a and env_d are latched at each
// hit. [deviation: DDR-053 says per pad. All eight slots already hold §15.3's
// per-pad list, so ATK/DEC per pad would need a ninth and tenth; the host
// block's AD is used instead and the UI's ENV page edits it for the last-hit
// pad. Logged in engine-spec §15.11.]
// The shape: a linear attack over env_a (under one frame = instant, the v1
// click), then an exponential fall reaching -60 dB at env_d -- exactly v1's
// DECAY, so env_a 0 / env_d 4 s is the v1 sound sample for sample.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <new>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <wav.hpp>

namespace {

constexpr int kPads = 24;
constexpr int kBaseMidi = 53;      // F3, emulator/app/keys.py NOTE_BASE_MIDI
constexpr int kNumParams = 8;

enum Idx { kStart = 0, kEnd, kPitch, kLevel, kPlay, kRev, kPan, kChoke };
enum Play { kOneShot = 0, kGate, kLoop, kNumPlay };

// Choke and gate release: 5 ms linear. Long enough not to click on a kick's
// body, short enough that an open hat choked by a closed one still reads as
// cut. [default chosen here: audio/CLAUDE.md]
constexpr double kCutFadeS = 0.005;

const char* const kPlayLabels[] = {"ONE-SHOT", "GATE", "LOOP"};
const char* const kRevLabels[] = {"OFF", "ON"};
const char* const kChokeLabels[] = {"OFF", "1", "2", "3", "4"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/drum");
    arb::set_field(d.version, sizeof d.version, "2.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;                  // ENV = ATK · DEC (DDR-053), S/R unused
    d.latency_frames = 0;
    d.num_params = kNumParams;
    // Defaults are v1's sound: the whole sample, no transposition, v1's LEVEL.
    d.params[kStart] = arb::per_note(arb::param_lin("start", "START", 0.0f, 1.0f, 0.0f, 0.005f, ""));
    d.params[kEnd]   = arb::per_note(arb::param_lin("end",   "END",   0.0f, 1.0f, 1.0f, 0.005f, ""));
    // +/- an octave: a kick into a tom, a hat into a tick, not the kit into an effect.
    d.params[kPitch] = arb::per_note(arb::param_lin("pitch", "PITCH", -12.0f, 12.0f, 0.0f, 0.1f, "st"));
    d.params[kLevel] = arb::per_note(arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.8f, 0.01f, ""));
    d.params[kPlay]  = arb::per_note(arb::param_enum("play", "PLAY", kPlayLabels, kNumPlay, kOneShot));
    d.params[kRev]   = arb::per_note(arb::param_enum("rev", "REV", kRevLabels, 2, 0));
    // Balance, not a pan law: centre is unity on both sides (the v1 sound, and
    // host.pan's shape), cos toward the far side.
    d.params[kPan]   = arb::per_note(arb::param_lin("pan", "PAN", -1.0f, 1.0f, 0.0f, 0.02f, ""));
    d.params[kChoke] = arb::per_note(arb::param_enum("choke", "CHOKE", kChokeLabels, 5, 0));
    arb::set_assets(d, {"kit"});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

int param_index(const std::string& id) {
    for (int i = 0; i < kNumParams; ++i)
        if (std::strncmp(desc().params[i].id, id.c_str(), sizeof desc().params[i].id) == 0) return i;
    return -1;
}

struct Pad {
    arb::wav::Sample sample;
    float norm[kNumParams]{};
    float val[kNumParams]{};

    // playback, latched at the hit
    double pos = 0.0, rate = 1.0;
    double lo = 0.0, hi = 0.0;          // region, frames; hi already clamped for interpolation
    float  vel = 0.0f;
    float  env = 0.0f;
    float  att_step = 0.0f;             // > 0 while attacking
    float  k = 1.0f;                    // decay multiplier per frame
    float  fade = 1.0f, fade_step = 0.0f;   // choke / gate release
    int    play = kOneShot;
    bool   rev = false;
    bool   held = false;
    bool   playing = false;

    bool loaded() const { return sample.data.size() > 1; }

    void set(int idx, float n) {
        norm[idx] = arb::clamp01(n);
        val[idx] = arb::denorm(desc().params[idx], norm[idx]);
    }
    void defaults() {
        for (int i = 0; i < kNumParams; ++i) set(i, arb::norm(desc().params[i], desc().params[i].def));
    }
    int choke() const { return int(val[kChoke]); }
    void cut(double sr) {
        if (!playing || fade_step > 0.0f) return;
        fade_step = float(1.0 / (kCutFadeS * sr));
    }
};

struct Engine {
    Pad  pads[kPads];
    int  sel = 0;                        // the pad get_param reports: the last one set
    std::string kit_name;

    Engine() { for (Pad& p : pads) p.defaults(); }

    // Control thread, before this engine is in the graph (§3.2). Allocation and
    // file I/O are allowed here and nowhere else.
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

        const std::string dir = [&] {
            const std::string s(path);
            const std::size_t k = s.find_last_of('/');
            return k == std::string::npos ? std::string(".") : s.substr(0, k);
        }();

        for (Pad& pad : pads) { pad = Pad{}; pad.defaults(); }
        kit_name = j.value("name", std::string{});
        int loaded = 0;
        for (auto it = p.begin(); it != p.end(); ++it) {
            const int idx = std::atoi(it.key().c_str());
            if (idx < 0 || idx >= kPads) continue;
            // v1 shape "0": "kick.wav", or v2 {"file": ..., "params": {...}}.
            std::string file;
            nlohmann::json params = nlohmann::json::object();
            if (it.value().is_string()) {
                file = it.value().get<std::string>();
            } else if (it.value().is_object()) {
                file = it.value().value("file", std::string{});
                params = it.value().value("params", nlohmann::json::object());
            }
            if (file.empty()) continue;
            if (file[0] != '/') file = dir + "/" + file;
            if (!arb::wav::load_mono(file, pads[idx].sample).empty()) return 7;
            if (params.is_object())
                for (auto pit = params.begin(); pit != params.end(); ++pit) {
                    const int k = param_index(pit.key());
                    // Keyed by id (§7.1); an id this version does not have is
                    // dropped, as the host drops one from a patch.
                    if (k >= 0 && pit.value().is_number()) pads[idx].set(k, pit.value().get<float>());
                }
            ++loaded;
        }
        // A kit with nothing in it is a refusal, not a silent instrument: the
        // host turns a non-zero return into ENGINE BROKEN and loads nothing.
        return loaded > 0 ? 0 : 8;
    }

    // §15.3: per_note slots take their values from set_note_param only.
    void set_param(uint32_t, float) {}

    void set_note_param(uint8_t note, uint32_t idx, float n) {
        const int i = int(note) - kBaseMidi;
        if (i < 0 || i >= kPads || idx >= uint32_t(kNumParams)) return;
        pads[i].set(int(idx), n);
        sel = i;
    }

    void reset() {
        for (Pad& p : pads) { p.playing = false; p.held = false; p.pos = 0.0; p.env = 0.0f; }
    }

    void note_on(int note, float vel, const arb_host& host, double sr) {
        const int i = note - kBaseMidi;
        if (i < 0 || i >= kPads || !pads[i].loaded()) return;
        Pad& p = pads[i];

        if (const int g = p.choke(); g > 0)
            for (int j = 0; j < kPads; ++j)
                if (j != i && pads[j].choke() == g) pads[j].cut(sr);

        const double n = double(p.sample.data.size());
        const double top = n - 2.0;                 // last frame with a right neighbour
        p.lo = double(p.val[kStart]) * (n - 1.0);
        // END never sits before START: the region is at least one frame, so
        // every position of both knobs plays something (DDR-044).
        p.hi = std::min(std::max(double(p.val[kEnd]) * (n - 1.0), p.lo + 1.0), top);
        p.lo = std::min(p.lo, p.hi);
        p.play = std::clamp(int(p.val[kPlay]), 0, kNumPlay - 1);
        p.rev = p.val[kRev] >= 0.5f;
        p.pos = p.rev ? p.hi : p.lo;
        p.rate = std::pow(2.0, double(p.val[kPitch]) / 12.0) * (double(p.sample.sample_rate) / sr);
        p.vel = vel;
        const double att = double(host.env_a) * sr;
        if (att >= 1.0) { p.att_step = float(1.0 / att); p.env = p.att_step; }
        else            { p.att_step = 0.0f; p.env = 1.0f; }
        // -60 dB by the end of DEC, which is what a drum envelope means (v1's DECAY).
        const double dec = std::max(double(host.env_d), 1.0e-4);
        p.k = float(std::pow(10.0, -3.0 / (dec * sr)));
        p.fade = 1.0f;
        p.fade_step = 0.0f;
        p.held = true;
        p.playing = true;          // a re-hit restarts this pad and nothing else
    }

    void note_off(int note, double sr) {
        const int i = note - kBaseMidi;
        if (i < 0 || i >= kPads) return;
        Pad& p = pads[i];
        p.held = false;
        // One-shot ignores the key: letting go must not cut it.
        if (p.play != kOneShot) p.cut(sr);
    }

    // `bend` is 2^(pitch_st / 12): the instrument's bend and a MOD route to
    // host.pitch move every sounding pad's playback rate, live (#647; §15.10
    // lets an engine off PolyHost read pitch_st itself). 1 when there is none,
    // so every pad plays at exactly the rate it always did.
    void render(uint32_t n, float* l, float* r, double bend) {
        std::fill_n(l, n, 0.0f);
        std::fill_n(r, n, 0.0f);
        for (Pad& p : pads) {
            if (!p.playing) continue;
            const std::vector<float>& d = p.sample.data;
            const float level = p.val[kLevel];
            const float pan = p.val[kPan];
            constexpr float kHalfPi = 1.5707963267948966f;
            const float gl = pan > 0.0f ? std::cos(pan * kHalfPi) : 1.0f;
            const float gr = pan < 0.0f ? std::cos(-pan * kHalfPi) : 1.0f;
            const double len = p.hi - p.lo;
            for (uint32_t i = 0; i < n; ++i) {
                const bool loop = p.play == kLoop && p.held && len >= 1.0;
                if (!p.rev && p.pos > p.hi) {
                    if (!loop) { p.playing = false; break; }
                    p.pos = p.lo + std::fmod(p.pos - p.hi, len);
                } else if (p.rev && p.pos < p.lo) {
                    if (!loop) { p.playing = false; break; }
                    p.pos = p.hi - std::fmod(p.lo - p.pos, len);
                }
                if (p.att_step == 0.0f && p.env < 1.0e-5f) { p.playing = false; break; }
                const int64_t i0 = int64_t(p.pos);
                const float frac = float(p.pos - double(i0));
                const float s = d[std::size_t(i0)] * (1.0f - frac) +
                                d[std::size_t(i0) + 1] * frac;
                const float v = s * p.env * p.vel * level * p.fade;
                l[i] += v * gl;
                r[i] += v * gr;
                if (p.att_step > 0.0f) {
                    p.env += p.att_step;
                    if (p.env >= 1.0f) { p.env = 1.0f; p.att_step = 0.0f; }
                } else {
                    p.env *= p.k;
                }
                if (p.fade_step > 0.0f) {
                    p.fade -= p.fade_step;
                    if (p.fade <= 0.0f) { p.playing = false; break; }
                }
                p.pos += (p.rev ? -p.rate : p.rate) * bend;
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
                if (e.type == ARB_EV_NOTE_ON && e.b > 0.0f) note_on(int(e.a), e.b, host, sr);
                else if (e.type == ARB_EV_NOTE_OFF || e.type == ARB_EV_NOTE_ON) note_off(int(e.a), sr);
            }
            if (pos >= frames) break;
            const uint32_t next = (ei < nev) ? std::min(frames, ev[ei].frame) : frames;
            render(next - pos, io.out[0] + pos, io.out[1] + pos, bend);
            pos = next;
        }
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
// The last pad set_note_param touched: get_param has no note argument, and
// that pad is the one the UI is editing.
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
    e_load_asset,
    e_set_note_param,        // every param is per pad (§15.3)
    nullptr,                 // format_param: enums carry labels; the host draws the rest
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
