// arbutus/motion — the IMU as a modulator (modulation-spec §3.2, §13). Reads
// arb_host.motion[6] (accel in g, gyro in deg/s; zeros without a sensor) and
// never touches I2C: the host samples the part on the control thread. All-zero
// motion gives all-zero outputs, which is how "no sensor" looks to a route.
// README.md says what every range is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

enum Param : uint32_t { kSmooth, kShakeSens, kSpinSens, kNumParams };
enum Output : uint32_t { kTiltX, kTiltY, kShake, kSpin };

// SHAKE: gravity (and tilt) is whatever is left after a 20 ms low-pass per
// axis. Short enough that turning the case over in half a second reads under
// 0.1 at the default scale; long enough to pass a tap, which is 5-20 ms. A
// jolt then falls to 5 % in 150 ms (§13 "~150 ms").
constexpr double kGravityS = 0.020;
constexpr double kShakeTauS = 0.050;

// The two SENS knobs are one log curve: centre is the stated full scale, each
// end is 8x either way. [default chosen here: §13 gives no scale; 250 deg/s
// is the LSM6DSO's most sensitive gyro range, and 0.5 g is a firm tap]
constexpr double kShakeMidG = 0.5, kSpinMidDps = 250.0, kSensSpan = 8.0;
double full_scale(double mid, float sens) { return mid * std::pow(kSensSpan, 1.0 - 2.0 * double(sens)); }

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/motion");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_MODULATOR;
    d.num_params = kNumParams;                         // slots 3-7 empty
    d.params[kSmooth]    = arb::param_log("smooth", "SMOOTH", 1.0f, 2000.0f, 50.0f, 0.0f, "ms");
    d.params[kShakeSens] = arb::param_lin("shake_sens", "SHK-SENS", 0.0f, 1.0f, 0.5f, 0.01f, "");
    d.params[kSpinSens]  = arb::param_lin("spin_sens", "SPN-SENS", 0.0f, 1.0f, 0.5f, 0.01f, "");
    arb::set_outputs(d, {arb::output("tilt_x", "TILT-X", true), arb::output("tilt_y", "TILT-Y", true),
                         arb::output("shake", "SHAKE", false), arb::output("spin", "SPIN", true)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float  norm[kNumParams]{};
    double smooth_s = 0.05, shake_fs = kShakeMidG, spin_fs = kSpinMidDps;

    double tx = 0.0, ty = 0.0, spin = 0.0, shake = 0.0;
    double grav[3]{};
    bool   primed = false;        // gravity starts at the first reading, not at 0
    bool   absent = false;        // the last block's feed was all-zero, "no sensor"

    Engine() {
        for (uint32_t i = 0; i < kNumParams; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void reset() {
        tx = ty = spin = shake = 0.0;
        grav[0] = grav[1] = grav[2] = 0.0;
        primed = false;
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= kNumParams) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kSmooth:    smooth_s = v / 1000.0; break;
        case kShakeSens: shake_fs = full_scale(kShakeMidG, v); break;
        case kSpinSens:  spin_fs = full_scale(kSpinMidDps, v); break;
        default: break;
        }
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const double dt = double(io.frames) / sr;
        const float* m = host.motion;

        // Without this the case sitting flat (az = 1 g) would read as a 1 g
        // jolt on the first block after load. The same holds when the sensor
        // drops out or comes back (#598): the host's step to or from all-zero
        // motion is not a movement of the case, so gravity starts again there.
        bool none = true;
        for (int k = 0; k < 6; ++k) none = none && m[k] == 0.0f;
        if (!primed || none != absent) {
            for (int k = 0; k < 3; ++k) grav[k] = m[k];
            primed = true;
            absent = none;
        }

        // One-pole at block rate, so the time constant holds at any block size.
        const double a = std::exp(-dt / smooth_s);
        tx = std::clamp(double(m[0]), -1.0, 1.0) + a * (tx - std::clamp(double(m[0]), -1.0, 1.0));
        ty = std::clamp(double(m[1]), -1.0, 1.0) + a * (ty - std::clamp(double(m[1]), -1.0, 1.0));
        // SMOOTH applies to SPIN too: gyro noise is the same jitter as tilt's.
        const double sp = std::clamp(double(m[5]) / spin_fs, -1.0, 1.0);
        spin = sp + a * (spin - sp);

        const double g = std::exp(-dt / kGravityS);
        double mag2 = 0.0;
        for (int k = 0; k < 3; ++k) {
            grav[k] = double(m[k]) + g * (grav[k] - double(m[k]));
            const double hp = double(m[k]) - grav[k];
            mag2 += hp * hp;
        }
        // Peak-hold with exponential decay: a jolt reads at once, then fades.
        const double in = std::min(1.0, std::sqrt(mag2) / shake_fs);
        shake = std::max(in, shake * std::exp(-dt / kShakeTauS));
        for (double* v : {&tx, &ty, &spin, &shake})
            if (std::fabs(*v) < 1e-9) *v = 0.0;

        io.ctl_out[kTiltX] = float(tx);
        io.ctl_out[kTiltY] = float(ty);
        io.ctl_out[kShake] = float(shake);
        io.ctl_out[kSpin] = float(spin);
    }
};

// SENS knobs read as the full scale they give: "0.50 g", "250 dps".
int format(uint32_t idx, float n, char* buf, uint32_t size) {
    const float v = arb::denorm(desc().params[idx], n);
    int w = 0;
    if (idx == kShakeSens) {
        const double fs = full_scale(kShakeMidG, v);
        w = std::snprintf(buf, size, fs < 1.0 ? "%.2f g" : "%.1f g", fs);
    } else if (idx == kSpinSens) {
        w = std::snprintf(buf, size, "%.0f dps", full_scale(kSpinMidDps, v));
    }
    return w > 0;
}

// ---- ABI shims -------------------------------------------------------------
void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
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
    e_format_param,          // SENS readouts as full scale
    nullptr,                 // set_voice_mod: a modulator has no voices
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
