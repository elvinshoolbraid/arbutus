// arbutus/pattern — the fixed-grid step sequencer (engine-spec.md §15.4).
//
// `endless`'s sibling and its opposite. `endless` follows your hand: free
// length, and the phrase transposes onto whatever key you hold. This box is a
// riff **written down** -- a fixed STEPS-long grid at absolute pitch, so the
// notes are the ones in the pattern and the key only starts it. That is the
// shape a pattern *file* wants (DDR-037), which is why this is the box stage
// H2's browser hands one to.
//
// Everything else is deliberately `endless`'s and should stay that way: the
// same four soft-keys (REC / REST / UNDO / CLEAR) with the same held-REC entry
// gesture, the same three kinds of step (notes, rest, tie), the same `pattern`
// asset, the same clock (frames against `host.tempo_bpm` -- a tick at ppqn 24
// is coarser than a 1/32 triplet) and the same swing. A phrase moves between
// the two boxes without changing meaning.

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <fstream>
#include <new>
#include <random>
#include <string>

#include <nlohmann/json.hpp>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr int kMaxSteps = 32;       // the grid: two bars of sixteenths
constexpr int kMaxChord = 8;
constexpr int kMaxSounding = kMaxChord;
constexpr int kMaxDown = 16;        // keys down at once: ten fingers, and room
constexpr int kStepGuard = 64;      // the Python's guard: never spin on a tiny step

// (label, length in beats) -- screen-sketches § SEQ params, via app/sequencer.py
constexpr float kDivBeats[] = {1.0f, 0.5f, 1.0f / 3.0f, 0.25f, 1.0f / 6.0f,
                               0.125f, 1.0f / 12.0f};
const char* const kDivLabels[] = {"1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32", "1/32T"};
constexpr int kNumDivs = int(sizeof kDivBeats / sizeof kDivBeats[0]);

const char* const kDirLabels[] = {"FWD", "REV", "PNG", "RAND"};
const char* const kOffOn[] = {"OFF", "ON"};
enum Dir { kFwd = 0, kRev, kPing, kRand, kNumDirs };

constexpr int kTie = -1;            // len[] sentinel: this step holds the last one

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/pattern");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_EVENTS_IN | ARB_CAP_EVENTS_OUT | ARB_CAP_SEQUENCER;
    d.honours_env = 0;               // it makes no sound, so it has no envelope
    d.latency_frames = 0;
    d.num_params = 7;
    d.params[0] = arb::param_enum("div", "DIV", kDivLabels, kNumDivs, 3);   // 1/16
    // 0 is AUTO -- the grid is however long the pattern is. format_param()
    // draws that, because "0 STEPS" on the screen would be a lie.
    d.params[1] = arb::param("steps", "STEPS", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             0.0f, float(kMaxSteps), 0.0f, 1.0f, "");
    d.params[2] = arb::param("gate", "GATE", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             10.0f, 100.0f, 80.0f, 1.0f, "%");
    d.params[3] = arb::param_enum("dir", "DIR", kDirLabels, kNumDirs, kFwd);
    // ---- the SHIFT row ----
    d.params[4] = arb::param("swing", "SWING", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             50.0f, 75.0f, 50.0f, 1.0f, "%");
    d.params[5] = arb::param("oct", "OCT", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             -2.0f, 2.0f, 0.0f, 1.0f, "");
    d.params[6] = arb::param_enum("hold", "HOLD", kOffOn, 2, 0);

    // §3.6, <=4, labelled. The host maps them to 1-4 on the SEQ page and draws
    // these labels on the strip.
    d.num_softkeys = 4;
    d.softkeys[0] = arb::softkey("rec", "REC");
    d.softkeys[1] = arb::softkey("rest", "REST");
    d.softkeys[2] = arb::softkey("undo", "UNDO");
    d.softkeys[3] = arb::softkey("clear", "CLEAR");

    // DDR-037: a pattern is content, so it can arrive as a file. Stage H2 is
    // what puts one on the browser; the box is ready for it either way.
    arb::set_assets(d, {"pattern"});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    // ---- parameters ----
    float norm[ARB_MAX_PARAMS]{};
    int   div = 3;
    int   grid = 0;                  // STEPS; 0 = AUTO, the pattern's own length
    int   gate = 80;
    int   dir = kFwd;
    int   swing = 50;
    int   oct = 0;
    bool  hold = false;

    // ---- pattern ----
    int8_t notes[kMaxSteps][kMaxChord]{};
    int    len[kMaxSteps]{};
    int    n_steps = 0;
    // n_steps as format_param may read it: AUTO's count is drawn on the control
    // thread while process() moves n_steps on the audio thread (step entry,
    // UNDO). Published at the end of every process() and load_asset(); relaxed,
    // because it is one display number and orders nothing else (#864).
    std::atomic<int> shown_steps{0};

    // ---- playback ----
    int    step_index = 0;
    int64_t step_count = 0;      // steps fired since the run started
    double acc = 0.0;
    bool   gate_done = true;     // the early note-off for this step has happened
    bool   first = true;
    int    trigger_note = -1;
    int    sounding[kMaxSounding]{};
    int    n_sounding = 0;
    int    down[kMaxDown]{};     // run keys physically down, oldest first
    int    n_down = 0;

    // ---- entry ----
    bool entry = false;
    int  chord_held = 0;
    bool pending_clear = false;

    std::mt19937 rng{0};
    uint32_t seed = 0;

    // ---- emission ----
    arb_event* ev_out = nullptr;
    uint32_t   ev_cap = 0;
    uint32_t*  n_ev = nullptr;
    int        deferred[kMaxSounding]{};   // note-offs waiting for the next block
    int        n_deferred = 0;

    explicit Engine(uint32_t rng_seed) : seed(rng_seed) {
        rng.seed(seed);
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: div = std::clamp(int(v), 0, kNumDivs - 1); break;
        case 1: grid = std::clamp(int(v), 0, kMaxSteps); break;
        case 2: gate = std::clamp(int(v), 10, 100); break;
        case 3: dir = std::clamp(int(v), 0, kNumDirs - 1); break;
        case 4: swing = int(v); break;
        case 5: oct = std::clamp(int(v), -2, 2); break;
        default: {
            const bool on = v >= 0.5f;
            if (on != hold) { hold = on; hold_changed(0); }
            break;
        }
        }
    }

    // ---- emission ------------------------------------------------------
    //
    // A note-off asked for outside process() (HOLD turned off between blocks)
    // has no buffer to go to, so it waits for frame 0 of the next block.
    // Dropping it left the notes stuck in the ENGINE (#631).
    void emit(uint32_t frame, uint16_t type, int note, float vel) {
        if (!ev_out || !n_ev) {
            if (type == ARB_EV_NOTE_OFF && n_deferred < kMaxSounding) deferred[n_deferred++] = note;
            return;
        }
        if (*n_ev >= ev_cap) return;
        arb_event& e = ev_out[(*n_ev)++];
        e = arb_event{};
        e.frame = frame;
        e.type = type;
        e.a = uint16_t(note);
        e.b = vel;
    }

    void flush_deferred() {
        const int n = n_deferred;
        n_deferred = 0;
        for (int i = 0; i < n; ++i) emit(0, ARB_EV_NOTE_OFF, deferred[i], 0.0f);
    }

    void silence(uint32_t frame) {
        for (int i = 0; i < n_sounding; ++i) emit(frame, ARB_EV_NOTE_OFF, sounding[i], 0.0f);
        n_sounding = 0;
    }

    // ---- clock ---------------------------------------------------------
    double step_samples(const arb_host& h, int64_t index) const {
        const double bpm = std::max(1.0, h.tempo_bpm);
        const double sr = h.sample_rate ? double(h.sample_rate) : 48000.0;
        const double base = double(kDivBeats[div]) * 60.0 / bpm * sr;
        const double sw = double(swing - 50) / 100.0;        // 0 .. 0.25
        if (sw <= 0.0) return base;
        return (index % 2 == 0) ? base * (1.0 + sw) : base * (1.0 - sw);
    }

    bool running() const { return n_steps > 0 && (trigger_note >= 0 || hold); }

    // The grid. STEPS 0 is AUTO -- the loop is however long the pattern is.
    // Set above the pattern the tail is rests; set below, the pattern is
    // truncated. Both are useful: five notes on a sixteen-step grid is a riff
    // with space after it, and three steps of a sixteen-step pattern is a
    // stutter.
    int grid_len() const { return grid > 0 ? grid : n_steps; }

    void next_index() {
        const int g = grid_len();
        if (g <= 0) return;
        switch (dir) {
        case kRev:  step_index = (step_index - 1 + g) % g; break;
        case kPing: {
            if (g == 1) { step_index = 0; break; }
            // The ends are not repeated: 0 1 2 3 2 1 | 0 ..., as stepmod's.
            // step_count is already the step about to fire, so this is pure
            // in it: 0 1 2 3 2 1 | 0 ...
            const int64_t r = step_count % (2 * g - 2);
            step_index = int(r < g ? r : 2 * g - 2 - r);
            break;
        }
        case kRand: step_index = int(rng() % uint32_t(g)); break;
        default:    step_index = (step_index + 1) % g; break;
        }
    }

    // Is the step that follows this one a tie? A tie holds what is sounding,
    // so the step before it must not close its gate. Ties are a FWD idea: in
    // REV, PNG or RAND "the step before this one" is not the one the player
    // entered, so a tie is played as a rest there (README).
    bool next_is_tie() const {
        const int g = grid_len();
        if (g <= 0 || dir != kFwd) return false;
        const int nxt = (step_index + 1) % g;
        return nxt < n_steps && len[nxt] == kTie;
    }

    void fire(uint32_t frame) {
        const int g = grid_len();
        if (g <= 0) { silence(frame); return; }
        const int s = step_index % g;
        // Inside the grid but off the end of the pattern: a rest.
        if (s >= n_steps) { silence(frame); return; }
        // A tie emits nothing and silences nothing: the last step keeps going.
        if (len[s] == kTie && dir == kFwd) return;
        silence(frame);
        // Absolute pitch -- the difference from `endless`. The notes are the
        // ones written down, moved only by OCT; the key that started it is a
        // run key, not a transpose.
        const int sh = 12 * oct;
        for (int i = 0; i < len[s]; ++i) {
            const int n = int(notes[s][i]) + sh;
            if (n < 0 || n > 127 || n_sounding >= kMaxSounding) continue;
            emit(frame, ARB_EV_NOTE_ON, n, 1.0f);
            sounding[n_sounding++] = n;
        }
    }

    // Advance the step clock over `n` frames starting at block offset `off`.
    void advance(const arb_host& h, uint32_t off, uint32_t n) {
        if (!running()) return;
        if (first) {
            first = false;
            step_count = 0;
            fire(off);
            acc = 0.0;
            gate_done = false;
        }
        // An event's frame is where the clock stands in this call, rounded down
        // once. Adding up each step's truncated length instead lost a frame per
        // step whenever several fell in one block, so the timing moved with the
        // block size (#631, found by ragged_events at 2048).
        double remaining = double(n);
        uint32_t at = off;
        for (int guard = 0; guard < kStepGuard; ++guard) {
            const double step = step_samples(h, step_count);
            // GATE: the note-off inside the step. At 100 % -- and under a tie
            // -- there is none, and the arithmetic below is skipped entirely,
            // so the ungated path is frame for frame what it was before H1.
            if (!gate_done) {
                const double g = step * double(gate) / 100.0;
                if (gate < 100 && g < step && !next_is_tie()) {
                    const double need = std::max(0.0, g - acc);
                    if (need >= remaining) { acc += remaining; return; }
                    remaining -= need;
                    at = off + uint32_t(double(n) - remaining);
                    acc = std::max(acc, g);
                    silence(at);
                }
                gate_done = true;
            }
            // `>=`, not `>`: a boundary exactly at the block's end is frame 0 of the
            // next block. Firing it here stamped it `frame == frames`, which the
            // header forbids (#631).
            const double need = std::max(0.0, step - acc);
            if (need >= remaining) { acc += remaining; return; }
            remaining -= need;
            at = off + uint32_t(double(n) - remaining);
            acc = 0.0;
            gate_done = false;
            ++step_count;
            next_index();
            fire(at);
        }
        acc += remaining;
    }

    // ---- transport -----------------------------------------------------
    //
    // The key is a run key, not a transpose. Pressing a second one while the
    // first is down changes nothing and the phrase keeps its timing.
    // Every run key down is remembered, so letting go of the newest falls back
    // to one still held rather than stopping under it (#631): the last key up
    // is the one that stops.
    void key_down(int note) {
        key_up(note);
        if (n_down < kMaxDown) down[n_down++] = note;
    }

    bool key_up(int note) {
        for (int i = 0; i < n_down; ++i)
            if (down[i] == note) {
                for (int j = i; j + 1 < n_down; ++j) down[j] = down[j + 1];
                --n_down;
                return true;
            }
        return false;
    }

    void trigger(int note) {
        const bool already = trigger_note >= 0;
        key_down(note);
        trigger_note = note;
        if (already) return;
        step_index = 0;
        step_count = 0;
        acc = 0.0;
        gate_done = true;
        first = true;
    }

    void release(int note, uint32_t frame) {
        if (!key_up(note)) return;
        // Another key still down keeps it running.
        if (n_down > 0) { trigger_note = down[n_down - 1]; return; }
        trigger_note = -1;
        if (!hold) stop(frame);
    }

    void stop(uint32_t frame) {
        silence(frame);
        trigger_note = -1;
        n_down = 0;
        step_index = 0;
        acc = 0.0;
        gate_done = true;
        step_count = 0;
        first = true;
    }

    // HOLD alone runs it: the key is a run key, so there is nothing to latch --
    // running() already answers yes with HOLD on and no key down.
    void hold_changed(uint32_t frame) {
        if (hold) {
            if (n_steps > 0 && first) { step_index = 0; step_count = 0; acc = 0.0; }
        } else if (trigger_note < 0) {
            stop(frame);
        }
    }

    // ---- entry ---------------------------------------------------------
    void begin_entry(uint32_t frame) {
        stop(frame);
        entry = true;
        chord_held = 0;
        pending_clear = true;      // the first step wipes it, not REC itself
        step_index = 0;
    }

    void end_entry() {
        entry = false;
        chord_held = 0;
        pending_clear = false;
        step_index = 0;
    }

    void clear_if_pending() {
        if (!pending_clear) return;
        pending_clear = false;
        n_steps = 0;
        step_index = 0;
    }

    void record(int note) {
        clear_if_pending();
        if (chord_held > 0 && n_steps > 0 && len[n_steps - 1] > 0) {  // inside a chord
            int& l = len[n_steps - 1];
            for (int i = 0; i < l; ++i) if (notes[n_steps - 1][i] == int8_t(note)) return;
            if (l < kMaxChord) notes[n_steps - 1][l++] = int8_t(note);
            return;
        }
        if (n_steps >= kMaxSteps) return;
        notes[n_steps][0] = int8_t(note);
        len[n_steps] = 1;
        ++n_steps;
        step_index = n_steps - 1;
    }

    // REST is also the tie key (§15.4: "tied notes = hold keys + >"). With
    // nothing held it inserts a rest, exactly as it always did; with keys still
    // down it inserts a **tie**, which holds the step just entered across one
    // more step instead of retriggering it. One key, two meanings, told apart
    // by whether your hand is still on the note -- which is the gesture the
    // spec names, and it costs no fifth soft-key (DDR-041 allows only four).
    void insert_rest() {
        if (!entry) return;
        clear_if_pending();
        if (n_steps >= kMaxSteps) return;
        const bool tie = chord_held > 0 && n_steps > 0 && len[n_steps - 1] != 0;
        len[n_steps] = tie ? kTie : 0;
        ++n_steps;
        chord_held = 0;
        step_index = n_steps - 1;
    }

    void undo_step() {
        if (!entry || pending_clear || n_steps == 0) return;
        --n_steps;
        chord_held = 0;
        step_index = std::max(0, n_steps - 1);
    }

    void clear_all(uint32_t frame) {
        stop(frame);
        n_steps = 0;
        pending_clear = false;
        chord_held = 0;
    }

    // ---- the pattern as content (DDR-037, §15.4) -----------------------
    //
    // `{"format":1,"kind":"pattern","pattern":[[60,64],[],"tie"]}` -- the same
    // file `endless` reads, so one pattern plays on either box.
    // A step is an array of MIDI notes, `[]` is a rest and the string "tie"
    // is a tie -- the same three things entry can make, written down. Control
    // thread only (§3.2): it allocates, and it must not run while a block does.
    int load_asset(const char* key, const char* path) {
        if (!key || std::string(key) != "pattern" || !path) return 1;
        std::ifstream f(path);
        if (!f) return 2;
        nlohmann::json j;
        try {
            f >> j;
        } catch (const std::exception&) {
            return 3;
        }
        if (!j.is_object() || j.value("kind", std::string{}) != "pattern") return 4;
        if (j.value("format", 0) != 1) return 5;
        const nlohmann::json steps = j.value("pattern", nlohmann::json());
        if (!steps.is_array() || steps.size() > std::size_t(kMaxSteps)) return 6;

        int8_t buf[kMaxSteps][kMaxChord]{};
        int    blen[kMaxSteps]{};
        int    n = 0;
        for (const auto& st : steps) {
            if (st.is_string()) {
                if (st.get<std::string>() != "tie") return 7;
                blen[n++] = kTie;
                continue;
            }
            if (!st.is_array() || st.size() > std::size_t(kMaxChord)) return 7;
            int c = 0;
            for (const auto& note : st) {
                if (!note.is_number_integer()) return 7;
                const int v = note.get<int>();
                if (v < 0 || v > 127) return 7;
                buf[n][c++] = int8_t(v);
            }
            blen[n++] = c;
        }
        // `root` is ignored on purpose: this box plays absolute pitch, so a
        // file's root is `endless`'s business and not this one's.
        stop(0);
        std::copy(&buf[0][0], &buf[0][0] + kMaxSteps * kMaxChord, &notes[0][0]);
        std::copy(blen, blen + kMaxSteps, len);
        n_steps = n;
        shown_steps.store(n, std::memory_order_relaxed);
        entry = false;
        chord_held = 0;
        pending_clear = false;
        return 0;
    }

    // ---- events --------------------------------------------------------
    void handle(const arb_event& e, uint32_t frame) {
        switch (e.type) {
        case ARB_EV_NOTE_ON:
            if (e.b <= 0.0f) { handle_off(int(e.a), frame); break; }
            if (entry) { record(int(e.a)); ++chord_held; }
            else       trigger(int(e.a));
            break;
        case ARB_EV_NOTE_OFF:
            handle_off(int(e.a), frame);
            break;
        case ARB_EV_SOFTKEY:
            softkey(int(e.a), e.b > 0.5f, frame);
            break;
        case ARB_EV_TRANSPORT:
            if (e.a == 0) stop(frame);          // STOP silences a running pattern
            break;
        default:
            break;                              // clock: the host's, not ours
        }
    }

    void handle_off(int note, uint32_t frame) {
        if (entry) { chord_held = std::max(0, chord_held - 1); return; }
        release(note, frame);
    }

    void softkey(int key, bool down, uint32_t frame) {
        switch (key) {
        case 0:                                  // REC: held, exactly as SHIFT was
            if (down) begin_entry(frame);
            else if (entry) end_entry();
            break;
        case 1: if (down) insert_rest(); break;
        case 2: if (down) undo_step(); break;
        case 3: if (down) clear_all(frame); break;
        default: break;
        }
    }

    void reset() {
        stop(0);
        entry = false;
        chord_held = 0;
        pending_clear = false;
        rng.seed(seed);                          // §3.7.5: an RNG's position is state
    }

    void process(const arb_host& host, arb_io& io) {
        // A sequencer fills no audio port, but §3.7.3 is unconditional.
        for (int c = 0; c < 2; ++c)
            if (io.out[c]) std::fill_n(io.out[c], io.frames, 0.0f);

        ev_out = io.ev_out;
        ev_cap = io.ev_out ? io.ev_out_cap : 0u;
        n_ev = io.n_ev_out;
        flush_deferred();

        const uint32_t frames = io.frames;
        const arb_event* ev = io.ev_in;
        const uint32_t nev = ev ? io.n_ev_in : 0u;
        uint32_t pos = 0, ei = 0;
        for (;;) {
            while (ei < nev && std::min(ev[ei].frame, frames) <= pos) handle(ev[ei++], pos);
            if (pos >= frames) break;
            const uint32_t next = (ei < nev) ? std::min(frames, ev[ei].frame) : frames;
            advance(host, pos, next - pos);
            pos = next;
        }
        ev_out = nullptr;
        n_ev = nullptr;
        shown_steps.store(n_steps, std::memory_order_relaxed);
    }
};

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host ? host->rng_seed : 0u);
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
int   e_load_asset(void* h, const char* key, const char* path) {
    return static_cast<Engine*>(h)->load_asset(key, path);
}
// STEPS 0 is AUTO, and only the engine knows what AUTO currently is, so it
// prints the length too: "AUTO 5". Pure in `n` apart from that length, which
// it reads from the published copy, so it is safe while process() runs (§15.1).
int   e_format_param(void* h, uint32_t idx, float n, char* buf, uint32_t size) {
    if (idx != 1 || !buf || !size) return 0;                  // the rest: host default
    const int v = int(arb::denorm(desc().params[1], n));
    if (v > 0) return std::snprintf(buf, size, "%d", v);
    // AUTO also prints what it currently resolves to, because only the engine
    // knows. `h` may be null (a descriptor read with no instance behind it).
    const int n_steps =
        h ? static_cast<Engine*>(h)->shown_steps.load(std::memory_order_relaxed) : 0;
    return std::snprintf(buf, size, "AUTO %d", n_steps);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    e_load_asset,            // the pattern, as a file (DDR-037)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    e_format_param,          // STEPS 0 reads AUTO, with the length it resolves to
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
