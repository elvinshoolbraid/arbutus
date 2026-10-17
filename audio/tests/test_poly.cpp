// PolyHost unit tests. No framework: a check macro and a main.
//
// The voice here renders a constant 1.0, so whatever comes out of PolyHost *is*
// the envelope. That makes the per-voice claim testable arithmetically rather
// than by ear.

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

#include <arbutus_engine.h>
#include <poly.hpp>

namespace {

int failures = 0;

#define CHECK(cond, ...)                                                        \
    do {                                                                        \
        if (!(cond)) {                                                          \
            std::printf("FAIL %s:%d  " #cond "\n      ", __FILE__, __LINE__);   \
            std::printf(__VA_ARGS__);                                           \
            std::printf("\n");                                                  \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

struct FlatVoice {
    int   note = -1;
    float vel = 0.0f;
    float detune = 0.0f;
    int   starts = 0;

    void start(int n, float v, const arb_host&) { note = n; vel = v; ++starts; }
    void stop() {}
    bool done() const { return true; }
    void render(float* l, float* r, uint32_t n, const arb_host&) {
        for (uint32_t i = 0; i < n; ++i) { l[i] = 1.0f; r[i] = 1.0f; }
    }
    void set_detune(float cents) { detune = cents; }
    void reset() { note = -1; vel = 0.0f; detune = 0.0f; }
};

// Renders its pitch offset as DC, in semitones: with a flat envelope the
// output of a mono voice *is* the glide curve.
struct PitchVoice {
    int   note = -1;
    float cents = 0.0f;
    int   starts = 0;

    void start(int n, float, const arb_host&) { note = n; ++starts; }
    void stop() {}
    bool done() const { return true; }
    void render(float* l, float* r, uint32_t n, const arb_host&) {
        for (uint32_t i = 0; i < n; ++i) { l[i] = cents / 100.0f; r[i] = l[i]; }
    }
    void set_pitch(float c) { cents = c; }
    void reset() { *this = PitchVoice{}; }       // no pointers: a fresh one is a reset
};

// A sine at the note plus set_pitch, the way arbutus/osc applies it.
struct SineVoice {
    double freq = 440.0, phase = 0.0;
    float  cents = 0.0f;
    void start(int n, float, const arb_host&) { freq = 440.0 * std::pow(2.0, (n - 69) / 12.0); phase = 0.0; }
    void stop() {}
    bool done() const { return true; }
    void render(float* l, float* r, uint32_t n, const arb_host& h) {
        const double inc = freq * std::pow(2.0, double(cents) / 1200.0) / double(h.sample_rate);
        for (uint32_t i = 0; i < n; ++i) {
            l[i] = r[i] = float(std::sin(6.283185307179586 * phase));
            phase += inc;
            if (phase >= 1.0) phase -= 1.0;
        }
    }
    void set_pitch(float c) { cents = c; }
    void reset() { *this = SineVoice{}; }
};

// The shape poly.hpp advises: a pointer to the engine's parameters, handed in
// once at create() (#675, review m-audio-sdk row 4).
struct PtrVoice {
    const float* level = nullptr;
    void start(int, float, const arb_host&) {}
    void stop() {}
    bool done() const { return true; }
    void render(float* l, float* r, uint32_t n, const arb_host&) {
        for (uint32_t i = 0; i < n; ++i) { l[i] = *level; r[i] = *level; }
    }
    void reset() {}                              // nothing of the sound to clear
};

// The same voice without reset(): PolyHost used to fall back to `= V{}`,
// which nulled `level`, and the next render() dereferenced NULL.
struct NoResetVoice {
    const float* level = nullptr;
    void start(int, float, const arb_host&) {}
    void stop() {}
    bool done() const { return true; }
    void render(float* l, float* r, uint32_t n, const arb_host&) {
        for (uint32_t i = 0; i < n; ++i) { l[i] = *level; r[i] = *level; }
    }
};
static_assert(!arb::VoiceLike<NoResetVoice>,
              "a Voice without reset() must not compile against PolyHost (#675)");

constexpr uint32_t kSR = 48000;

arb_host make_host(uint8_t mode = ARB_ENV_POLY) {
    arb_host h{};
    h.sample_rate = kSR;
    h.max_block = 512;
    h.tempo_bpm = 120.0;
    h.ppqn = 24;
    h.env_a = 0.0f;
    h.env_d = 0.0f;
    h.env_s = 1.0f;
    h.env_r = 0.1f;
    h.env_mode = mode;
    h.env_detune = arb::kUnisonSpreadCents;      // the host's default
    return h;
}

// One block of `n` frames with the given events; returns channel 0.
template <class P>
std::vector<float> block(P& poly, const arb_host& host, uint32_t n,
                         const std::vector<arb_event>& evs = {}) {
    std::vector<float> l(n ? n : 1, 0.0f), r(n ? n : 1, 0.0f);
    arb_io io{};
    io.out[0] = l.data();
    io.out[1] = r.data();
    io.frames = n;
    io.ev_in = evs.empty() ? nullptr : evs.data();
    io.n_ev_in = uint32_t(evs.size());
    poly.process(host, io);
    l.resize(n);
    return l;
}

arb_event note_on(uint32_t frame, int note, float vel) {
    arb_event e{};
    e.frame = frame;
    e.type = ARB_EV_NOTE_ON;
    e.a = uint16_t(note);
    e.b = vel;
    return e;
}

arb_event note_off(uint32_t frame, int note) {
    arb_event e{};
    e.frame = frame;
    e.type = ARB_EV_NOTE_OFF;
    e.a = uint16_t(note);
    return e;
}

// ---------------------------------------------------------------------------

void test_stealing_prefers_free_then_release_then_oldest() {
    arb::PolyHost<FlatVoice, 3> poly;
    arb_host host = make_host();

    block(poly, host, 64, {note_on(0, 60, 1.0f)});
    block(poly, host, 64, {note_on(0, 62, 1.0f)});
    CHECK(poly.active_count() == 2, "two notes should hold two voices, got %zu", poly.active_count());

    // The free voice goes first, not one of the two held.
    block(poly, host, 64, {note_on(0, 64, 1.0f)});
    CHECK(poly.note_of(0) == 60 && poly.note_of(1) == 62 && poly.note_of(2) == 64,
          "third note should take the free slot: %d %d %d", poly.note_of(0), poly.note_of(1),
          poly.note_of(2));

    // Release 60 and 62, let 60 decay further, then steal: the quietest of the
    // two in release must go, not the oldest.
    block(poly, host, 64, {note_off(0, 60)});
    block(poly, host, 1024, {note_off(0, 62)});   // 60 has been releasing 1088 frames longer
    CHECK(poly.env_value(0) < poly.env_value(1), "voice 0 should be quieter: %f vs %f",
          double(poly.env_value(0)), double(poly.env_value(1)));
    block(poly, host, 64, {note_on(0, 67, 1.0f)});
    CHECK(poly.note_of(0) == 67, "quietest-in-release should have been stolen, took slot with %d",
          poly.note_of(0));

    // Now every voice is held: the oldest one goes.
    arb::PolyHost<FlatVoice, 2> full;
    block(full, host, 8, {note_on(0, 60, 1.0f)});
    block(full, host, 8, {note_on(0, 62, 1.0f)});
    block(full, host, 8, {note_on(0, 64, 1.0f)});
    CHECK(full.note_of(0) == 64 && full.note_of(1) == 62, "oldest should be stolen: %d %d",
          full.note_of(0), full.note_of(1));
}

void test_envelope_is_per_voice() {
    arb::PolyHost<FlatVoice, 4> poly;
    arb_host host = make_host();
    host.env_a = 0.0f;
    host.env_d = 0.0f;
    host.env_s = 1.0f;

    // Note A is started with a 1 s release, note B with 0.1 s. The ADSR is
    // snapshotted at note-on, so they must release at different rates.
    host.env_r = 1.0f;
    block(poly, host, 16, {note_on(0, 60, 1.0f)});
    host.env_r = 0.1f;
    block(poly, host, 16, {note_on(0, 64, 1.0f)});

    std::vector<float> held = block(poly, host, 16);
    CHECK(std::abs(held.back() - 2.0f) < 1e-5f, "two voices at sustain should sum to 2.0, got %f",
          double(held.back()));

    block(poly, host, 8, {note_off(0, 60), note_off(0, 64)});
    block(poly, host, 4800);                       // 0.1 s: B is gone, A is 90% of the way up

    CHECK(poly.env_value(1) == 0.0f, "short release should be finished, at %f",
          double(poly.env_value(1)));
    CHECK(poly.env_value(0) > 0.85f && poly.env_value(0) < 0.95f,
          "long release should be near 0.9, at %f", double(poly.env_value(0)));

    // The mixed output is never gated: A's tail is A's own envelope alone.
    std::vector<float> tail = block(poly, host, 16);
    CHECK(std::abs(tail.back() - poly.env_value(0)) < 1e-6f,
          "output should equal the surviving envelope: %f vs %f", double(tail.back()),
          double(poly.env_value(0)));

    // ...and a new note must not disturb it. This is the bug the DDR is about.
    const float before = poly.env_value(0);
    std::vector<float> with_new = block(poly, host, 1, {note_on(0, 67, 1.0f)});
    CHECK(poly.env_value(0) < before, "the old voice must keep falling, %f -> %f", double(before),
          double(poly.env_value(0)));
    CHECK(with_new[0] > 1.0f, "the new note adds to the old tail, got %f", double(with_new[0]));
}

void test_mono_retriggers_and_legato_does_not() {
    for (uint8_t mode : {uint8_t(ARB_ENV_MONO), uint8_t(ARB_ENV_LEGATO)}) {
        arb::PolyHost<FlatVoice, 4> poly;
        arb_host host = make_host(mode);
        // A decay down to a low sustain is what makes a retrigger visible: at
        // sustain 1.0 an envelope that restarts looks exactly like one that did
        // not, because the attack climbs from where it already is.
        host.env_a = 0.0f;
        host.env_d = 0.1f;
        host.env_s = 0.2f;

        // 0.1 s of decay, plus the one sample a zero-length attack spends at
        // full (emulator/core/synth_engine.py, tests/golden/adsr_zero.npy).
        block(poly, host, 4801, {note_on(0, 60, 1.0f)});
        CHECK(std::abs(poly.env_value(0) - 0.2f) < 1e-5f, "mode %u: should be at sustain, at %f",
              mode, double(poly.env_value(0)));

        block(poly, host, 48, {note_on(0, 64, 1.0f)});
        if (mode == ARB_ENV_MONO)
            CHECK(poly.env_value(0) > 0.9f, "mono should retrigger the envelope, at %f",
                  double(poly.env_value(0)));
        else
            CHECK(std::abs(poly.env_value(0) - 0.2f) < 1e-5f, "legato must not retrigger, at %f",
                  double(poly.env_value(0)));

        CHECK(poly.active_count() == 1, "mode %u should hold one voice, holds %zu", mode,
              poly.active_count());
        CHECK(poly.voice(0).note == 64, "mode %u: voice should be on the new note, is %d", mode,
              poly.voice(0).note);

        // Release the new note; the held one underneath comes back.
        block(poly, host, 48, {note_off(0, 64)});
        CHECK(poly.voice(0).note == 60, "mode %u: should fall back to the held note, is %d", mode,
              poly.voice(0).note);

        block(poly, host, 48, {note_off(0, 60)});
        block(poly, host, 9600);
        CHECK(poly.active_count() == 0, "mode %u: everything should be silent", mode);
    }
}

void test_unison_stacks_detuned_voices() {
    // FlatVoice has only the older set_detune: it still gets the spread.
    arb::PolyHost<FlatVoice, 8> poly;
    arb_host host = make_host(ARB_ENV_UNISON);

    block(poly, host, 64, {note_on(0, 60, 1.0f)});
    CHECK(poly.active_count() == std::size_t(arb::kUnisonVoices),
          "unison should stack %d voices, stacked %zu", arb::kUnisonVoices, poly.active_count());

    float lo = 1e9f, hi = -1e9f;
    for (int i = 0; i < arb::kUnisonVoices; ++i) {
        CHECK(poly.voice(std::size_t(i)).note == 60, "unison voice %d is on note %d", i,
              poly.voice(std::size_t(i)).note);
        lo = std::min(lo, poly.voice(std::size_t(i)).detune);
        hi = std::max(hi, poly.voice(std::size_t(i)).detune);
    }
    CHECK(std::abs((hi - lo) - host.env_detune) < 1e-4f,
          "spread should be %f cents, is %f", double(host.env_detune), double(hi - lo));
    CHECK(std::abs(lo + hi) < 1e-4f, "spread should be centred, is %f..%f", double(lo), double(hi));

    // Level is compensated, so unison is not four times as loud as one voice.
    std::vector<float> out = block(poly, host, 16);
    CHECK(std::abs(out.back() - std::sqrt(float(arb::kUnisonVoices))) < 1e-4f,
          "unison sum should be sqrt(n), is %f", double(out.back()));
}

// Mono: note 60, then 72 with GLIDE 0.1 s. The voice starts at -12 st from its
// new note and climbs in a straight line to 0 over 4800 frames, in steps no
// bigger than one kPitchStep's worth.
std::vector<float> glide_run(uint32_t block_size) {
    arb::PolyHost<PitchVoice, 4> poly;
    arb_host host = make_host(ARB_ENV_MONO);
    host.env_s = 1.0f;
    host.env_glide = 0.1f;
    block(poly, host, 100, {note_on(0, 60, 1.0f)});
    std::vector<float> out;
    std::vector<arb_event> ev = {note_on(0, 72, 1.0f)};
    while (out.size() < 6000) {
        std::vector<float> b = block(poly, host, block_size, ev);
        ev.clear();
        out.insert(out.end(), b.begin(), b.end());
    }
    out.resize(6000);
    return out;
}

void test_glide_in_mono_is_continuous_and_takes_glide_seconds() {
    const std::vector<float> g = glide_run(128);
    CHECK(std::abs(g[0] + 12.0f) < 1e-4f, "glide should start 12 st down, at %f", double(g[0]));
    CHECK(std::abs(g[2400] + 6.0f) < 0.1f, "halfway should be near -6 st, at %f", double(g[2400]));
    CHECK(g[4799] < 0.0f && g[4799] > -0.1f, "just before the end, nearly there: %f", double(g[4799]));
    CHECK(g[4800 + arb::kPitchStep] == 0.0f, "arrived after 0.1 s, at %f", double(g[4832]));
    float worst = 0.0f;
    bool monotonic = true;
    for (std::size_t i = 1; i < g.size(); ++i) {
        worst = std::max(worst, g[i] - g[i - 1]);
        if (g[i] < g[i - 1]) monotonic = false;
    }
    const float step = 12.0f * float(arb::kPitchStep) / 4800.0f;
    CHECK(monotonic, "a glide up never goes down");
    CHECK(worst <= step + 1e-4f, "largest step %f st, a continuous glide steps <= %f", double(worst),
          double(step));

    // The glide is on the frame grid, not the host's block grid.
    const std::vector<float> ragged = glide_run(97);
    CHECK(ragged == g, "glide at blocks of 97 differs from blocks of 128");

    // GLIDE 0 is a jump.
    arb::PolyHost<PitchVoice, 4> poly;
    arb_host host = make_host(ARB_ENV_MONO);
    block(poly, host, 100, {note_on(0, 60, 1.0f)});
    std::vector<float> jump = block(poly, host, 16, {note_on(0, 72, 1.0f)});
    CHECK(jump[0] == 0.0f, "glide 0 should not slide, at %f", double(jump[0]));
}

void test_legato_glides_without_retrigger() {
    arb::PolyHost<PitchVoice, 4> poly;
    arb_host host = make_host(ARB_ENV_LEGATO);
    host.env_d = 0.1f;
    host.env_s = 0.2f;
    host.env_glide = 0.05f;                        // 2400 frames
    block(poly, host, 4801, {note_on(0, 60, 1.0f)});
    block(poly, host, 1200, {note_on(0, 64, 1.0f)});
    CHECK(std::abs(poly.env_value(0) - 0.2f) < 1e-5f, "legato must not retrigger, at %f",
          double(poly.env_value(0)));
    const float mid = poly.pitch_cents(0);
    CHECK(mid < -150.0f && mid > -250.0f, "halfway through a 4 st glide should be near -200 ct, at %f",
          double(mid));
    block(poly, host, 1300);
    CHECK(poly.pitch_cents(0) == 0.0f, "glide should have arrived, at %f", double(poly.pitch_cents(0)));

    // Falling back to the held note glides back down.
    block(poly, host, 64, {note_off(0, 64)});
    CHECK(poly.voice(0).note == 60 && poly.pitch_cents(0) > 300.0f,
          "return to 60 should start from 64's pitch: note %d, %f ct", poly.voice(0).note,
          double(poly.pitch_cents(0)));
}

void test_poly_ignores_glide() {
    arb::PolyHost<PitchVoice, 4> poly;
    arb_host host = make_host(ARB_ENV_POLY);
    host.env_glide = 0.5f;
    block(poly, host, 256, {note_on(0, 60, 1.0f)});
    block(poly, host, 256, {note_on(0, 72, 1.0f)});
    for (std::size_t i = 0; i < 2; ++i)
        CHECK(poly.pitch_cents(i) == 0.0f, "poly voice %zu glides: %f ct", i,
              double(poly.pitch_cents(i)));
}

void test_detune_spread_follows_env_detune() {
    arb::PolyHost<PitchVoice, 8> poly;
    arb_host host = make_host(ARB_ENV_UNISON);
    host.env_detune = 30.0f;
    block(poly, host, 64, {note_on(0, 60, 1.0f)});
    const float want[4] = {-15.0f, -5.0f, 5.0f, 15.0f};
    for (std::size_t i = 0; i < 4; ++i)
        CHECK(std::abs(poly.pitch_cents(i) - want[i]) < 1e-4f, "voice %zu at %f ct, want %f", i,
              double(poly.pitch_cents(i)), double(want[i]));

    // Read every block: a held stack follows the knob (and an env.detune route).
    host.env_detune = 20.0f;
    block(poly, host, 64);
    CHECK(std::abs(poly.pitch_cents(0) + 10.0f) < 1e-4f && std::abs(poly.pitch_cents(3) - 10.0f) < 1e-4f,
          "held stack should spread to +/-10 ct: %f %f", double(poly.pitch_cents(0)),
          double(poly.pitch_cents(3)));

    // The default is the spread unison had when it was fixed.
    CHECK(arb::kUnisonSpreadCents == 12.0f, "default spread moved: %f", double(arb::kUnisonSpreadCents));
}

void test_pitch_st_moves_every_voice() {
    arb::PolyHost<PitchVoice, 4> poly;
    arb_host host = make_host(ARB_ENV_POLY);
    block(poly, host, 128, {note_on(0, 60, 1.0f), note_on(0, 64, 1.0f), note_on(0, 67, 1.0f)});

    // Ramped across the block, landing on the value at its end.
    host.pitch_st = 3.0f;
    std::vector<float> ramp = block(poly, host, 128);
    CHECK(ramp[0] > 0.0f && ramp[0] < 3.0f * 3.0f * 0.5f, "block start should be early in the ramp: %f",
          double(ramp[0]));
    CHECK(std::abs(ramp[127] - 9.0f) < 1e-4f, "three voices at +3 st sum to 9 by the block end: %f",
          double(ramp[127]));
    for (std::size_t i = 0; i < 3; ++i)
        CHECK(poly.pitch_cents(i) == 300.0f, "voice %zu at %f ct, want 300", i,
              double(poly.pitch_cents(i)));

    // Unison adds its spread on top.
    arb::PolyHost<PitchVoice, 8> uni;
    arb_host uh = make_host(ARB_ENV_UNISON);
    uh.pitch_st = -2.0f;                            // first block: lands, no ramp
    block(uni, uh, 64, {note_on(0, 60, 1.0f)});
    CHECK(std::abs(uni.pitch_cents(0) - (-6.0f - 200.0f)) < 1e-3f, "unison voice 0 at %f ct",
          double(uni.pitch_cents(0)));

    // And the ratio is right: A4 up an octave is 880 Hz, counted in zero crossings.
    arb::PolyHost<SineVoice, 1> sine;
    arb_host sh = make_host(ARB_ENV_POLY);
    sh.pitch_st = 12.0f;
    block(sine, sh, 1, {note_on(0, 69, 1.0f)});
    std::vector<float> s = block(sine, sh, kSR);
    int ups = 0;
    for (std::size_t i = 1; i < s.size(); ++i)
        if (s[i - 1] < 0.0f && s[i] >= 0.0f) ++ups;
    CHECK(std::abs(ups - 880) <= 1, "A4 + 12 st should be 880 Hz, counted %d", ups);
}

void test_env_mode_change_releases_the_held_voices() {
    // #674, review m-audio-sdk row 3. Poly -> mono while a note sounds: the
    // poly voice must still end at its note-off, which in mono only pops the
    // held-note stack poly never pushed.
    {
        arb::PolyHost<FlatVoice, 4> poly;
        arb_host host = make_host(ARB_ENV_POLY);
        block(poly, host, 48, {note_on(0, 60, 1.0f)});
        host.env_mode = ARB_ENV_MONO;
        block(poly, host, 48, {note_off(0, 60)});
        block(poly, host, 9600);
        std::size_t held = 0;
        for (std::size_t i = 0; i < 4; ++i) held += poly.held(i);
        CHECK(poly.active_count() == 0 && held == 0,
              "poly -> mono: the chord must end, active %zu held %zu", poly.active_count(), held);
    }
    // Mono -> poly -> mono: the stack left from the first mono spell must not
    // retarget a later mono release to a key nobody holds.
    {
        arb::PolyHost<FlatVoice, 4> poly;
        arb_host host = make_host(ARB_ENV_MONO);
        block(poly, host, 48, {note_on(0, 60, 1.0f), note_on(0, 64, 1.0f)});
        host.env_mode = ARB_ENV_POLY;
        block(poly, host, 48, {note_off(0, 60), note_off(0, 64)});
        host.env_mode = ARB_ENV_MONO;
        block(poly, host, 48, {note_on(0, 67, 1.0f)});
        block(poly, host, 48, {note_off(0, 67)});
        block(poly, host, 9600);
        CHECK(poly.active_count() == 0, "mono -> poly -> mono: nothing held, yet %zu voice(s) sound",
              poly.active_count());
        for (std::size_t i = 0; i < 4; ++i)
            CHECK(!poly.held(i), "voice %zu still held on note %d", i, poly.note_of(i));
    }
}

void test_events_split_the_block() {
    // A note that starts mid-block must sound only from its own frame on, or
    // the output would depend on the host's block size.
    arb::PolyHost<FlatVoice, 4> poly;
    arb_host host = make_host();
    host.env_a = 0.0f;
    host.env_d = 0.0f;
    host.env_s = 1.0f;

    std::vector<float> out = block(poly, host, 64, {note_on(32, 60, 1.0f)});
    CHECK(out[31] == 0.0f, "frame 31 should be silent, is %f", double(out[31]));
    CHECK(out[32] == 1.0f, "frame 32 should be the note, is %f", double(out[32]));
}

void test_zero_frame_block_and_reset() {
    arb::PolyHost<FlatVoice, 4> poly;
    arb_host host = make_host();

    block(poly, host, 0, {note_on(0, 60, 1.0f)});      // §3.7.2
    CHECK(poly.active_count() == 1, "a 0-frame block still delivers its events");

    poly.reset();
    CHECK(poly.active_count() == 0, "reset should silence everything");
    CHECK(poly.voice(0).note == -1, "reset should reach the voice, note is %d", poly.voice(0).note);

    std::vector<float> out = block(poly, host, 16);
    CHECK(out.back() == 0.0f, "reset output should be silence, is %f", double(out.back()));

    // §3.7.8: inserting a 0-frame block must not change the sound. A pitch_st
    // move across it still ramps over the next real block (#674, row 10).
    arb::PolyHost<PitchVoice, 1> a, b;
    arb_host ph = make_host(ARB_ENV_POLY);
    block(a, ph, 128, {note_on(0, 60, 1.0f)});
    block(b, ph, 128, {note_on(0, 60, 1.0f)});
    ph.pitch_st = 2.0f;
    block(b, ph, 0);
    std::vector<float> ra = block(a, ph, 128), rb = block(b, ph, 128);
    CHECK(ra == rb, "a 0-frame block moved the pitch: first frame %f, without it %f",
          double(rb[0]), double(ra[0]));
}

void test_reset_keeps_what_create_handed_the_voice() {
    // #675: a host reset (panic, transport) is followed by more blocks; the
    // voice must still hold the parameter pointer it was given at create().
    const float level = 0.25f;
    arb::PolyHost<PtrVoice, 4> poly;
    for (std::size_t i = 0; i < 4; ++i) poly.voice(i).level = &level;
    arb_host host = make_host();
    block(poly, host, 32, {note_on(0, 60, 1.0f)});
    poly.reset();
    for (std::size_t i = 0; i < 4; ++i)
        CHECK(poly.voice(i).level == &level, "reset dropped voice %zu's parameter pointer", i);
    std::vector<float> out = block(poly, host, 32, {note_on(0, 62, 1.0f)});
    CHECK(out[31] == 0.25f, "the voice renders its level after a reset, got %f", double(out[31]));
}

void test_attack_climbs_from_the_current_level() {
    // engine-spec §14: the attack "climbs from the current value", so a key
    // struck again while its voice is still releasing does not drop to 0 and
    // click (#675, review row 9). One slot, so the new note takes that voice.
    arb::PolyHost<FlatVoice, 1> poly;
    arb_host host = make_host();
    host.env_a = 0.01f;                          // 480 frames
    host.env_r = 0.1f;
    block(poly, host, 960, {note_on(0, 60, 1.0f)});            // through the attack to sustain 1
    std::vector<float> rel = block(poly, host, 2400, {note_off(0, 60)});   // half the release
    const float level = rel.back();
    CHECK(level > 0.4f && level < 0.6f, "half-way into the release, at %f", double(level));
    std::vector<float> again = block(poly, host, 16, {note_on(0, 60, 1.0f)});
    CHECK(again[0] >= level, "the attack started at %f, under the release level %f",
          double(again[0]), double(level));
    for (std::size_t k = 1; k < again.size(); ++k)
        CHECK(again[k] >= again[k - 1], "the attack must climb, frame %zu fell", k);
}

void test_velocity_zero_note_on_is_a_note_off() {
    // The host sends `note {on:true, vel:0}` as a NOTE_ON with b = 0 (MIDI's
    // running-status release). Read as a note-on it would hold the note for
    // ever: a stuck note (#675, review row 9).
    arb::PolyHost<FlatVoice, 4> poly;
    arb_host host = make_host();
    block(poly, host, 32, {note_on(0, 60, 1.0f)});
    block(poly, host, 32, {note_on(0, 60, 0.0f)});
    for (std::size_t i = 0; i < 4; ++i)
        CHECK(!poly.held(i), "voice %zu still held (note %d) after a velocity-0 note-on", i,
              poly.note_of(i));
    block(poly, host, 9600);
    CHECK(poly.active_count() == 0, "the note must release, %zu voice(s) sound",
          poly.active_count());
}

void test_voice_mod_is_stored_and_snapshotted_per_voice() {
    // The ABI 2 default path (modulation-spec §8): depth is stored per param
    // and source, and each voice keeps depth x source from its own note-on.
    arb::PolyHost<FlatVoice, 4> poly;
    arb_host host = make_host();
    CHECK(poly.voice_mod_depth(2, ARB_VSRC_VEL) == 0.0f, "no route by default");

    poly.set_voice_mod(2, ARB_VSRC_VEL, 0.5f);
    poly.set_voice_mod(2, ARB_VSRC_NOTE, -0.25f);
    poly.set_voice_mod(ARB_MAX_PARAMS, ARB_VSRC_VEL, 1.0f);   // out of range: ignored
    poly.set_voice_mod(0, 7, 1.0f);                          // unknown source: ignored
    CHECK(poly.voice_mod_depth(2, ARB_VSRC_VEL) == 0.5f, "VEL depth stored");
    CHECK(poly.voice_mod_depth(2, ARB_VSRC_NOTE) == -0.25f, "NOTE depth stored");
    CHECK(poly.voice_mod_depth(0, ARB_VSRC_VEL) == 0.0f, "a bad source touches nothing");

    block(poly, host, 8, {note_on(0, 60, 0.8f), note_on(0, 120, 0.2f)});
    std::size_t a = 4, b = 4;
    for (std::size_t i = 0; i < 4; ++i) {
        if (poly.note_of(i) == 60) a = i;
        if (poly.note_of(i) == 120) b = i;
    }
    CHECK(a < 4 && b < 4, "both notes found a voice");
    if (a < 4 && b < 4) {
        CHECK(std::abs(poly.voice_mod(a, 2) - 0.4f) < 1e-6f,
              "C4 at vel 0.8: 0.5*0.8 + -0.25*0, is %f", double(poly.voice_mod(a, 2)));
        CHECK(std::abs(poly.voice_mod(b, 2) - (0.1f - 0.25f)) < 1e-6f,
              "note 120 at vel 0.2: 0.5*0.2 + -0.25*1, is %f", double(poly.voice_mod(b, 2)));
        CHECK(poly.voice_mod(a, 1) == 0.0f, "an unrouted param has no offset");

        // For its own lifetime: changing the route does not move ringing voices.
        poly.set_voice_mod(2, ARB_VSRC_VEL, 1.0f);
        CHECK(std::abs(poly.voice_mod(a, 2) - 0.4f) < 1e-6f, "ringing voice keeps its offset");
    }

    poly.reset();
    CHECK(poly.voice_mod(0, 2) == 0.0f, "reset clears the per-voice offsets");
    CHECK(poly.voice_mod_depth(2, ARB_VSRC_VEL) == 1.0f, "...but keeps the routes, like params");
}

}  // namespace

int main() {
    test_stealing_prefers_free_then_release_then_oldest();
    test_envelope_is_per_voice();
    test_mono_retriggers_and_legato_does_not();
    test_unison_stacks_detuned_voices();
    test_glide_in_mono_is_continuous_and_takes_glide_seconds();
    test_legato_glides_without_retrigger();
    test_poly_ignores_glide();
    test_detune_spread_follows_env_detune();
    test_pitch_st_moves_every_voice();
    test_env_mode_change_releases_the_held_voices();
    test_events_split_the_block();
    test_zero_frame_block_and_reset();
    test_voice_mod_is_stored_and_snapshotted_per_voice();
    test_reset_keeps_what_create_handed_the_voice();
    test_attack_climbs_from_the_current_level();
    test_velocity_zero_note_on_is_a_note_off();

    std::printf("%s\n", failures ? "poly: FAILED" : "poly: all checks passed");
    return failures ? 1 : 0;
}
