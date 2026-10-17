// arbutus-run — load a box, feed it, prove it (engine-spec.md §9.3).
//
// One tool, three jobs: render an engine offline, compare the result to a
// golden within a tolerance, and time it. It is how the audio process is tested
// without the UI and what an engine's `ctest` calls. See README.md.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include <arbutus_engine.h>

#include <manifest.hpp>
#include <params.hpp>
#include <poly.hpp>

#include <audiofile.hpp>
#include <box.hpp>
#include <rt_alloc_count.hpp>

namespace {

using arb::box::Box;

struct Options {
    std::string box;
    std::string notes, in_path, side_path, motion_path, out_path, golden_path, events_out;
    std::vector<std::pair<std::string, float>> sets;
    struct At { uint32_t frame; std::string id; float value; };
    std::vector<At> ats;
    std::vector<std::pair<uint32_t, float>> pitches;   // --pitch F:st
    std::vector<std::pair<std::string, std::string>> assets;
    uint32_t block = 512;
    uint32_t sr = 0;
    uint32_t len = 0;
    double   tempo = 120.0;
    float    env[4] = {0.01f, 0.1f, 0.8f, 0.2f};
    uint8_t  env_mode = ARB_ENV_POLY;
    uint32_t seed = 0;
    double   tol_db = -100.0;
    int      wav_bits = 32;
    int      bench_runs = 0;
    bool     describe = false;
    bool     rt_guard = false;
};

int usage(int code) {
    std::fprintf(code ? stderr : stdout,
        "arbutus-run <box> [options]\n"
        "  --notes FILE        event script: '<frame|Nb> on <note> <vel>' / '<t> off <note>'\n"
        "  --in FILE           input audio, .wav or .npy\n"
        "  --side FILE         sidechain audio (engines with ARB_CAP_SIDECHAIN)\n"
        "  --motion FILE       host.motion per block: .npy (6, rows), ax ay az g, gx gy gz deg/s\n"
        "  --set id=value      parameter, in the descriptor's units; repeatable\n"
        "  --at F:id=value     the same, set before the first block starting at or after\n"
        "                      frame F, as the host sets a moved knob; repeatable\n"
        "  --pitch F:st        host.pitch_st (bend + host.pitch, semitones) from the first\n"
        "                      block starting at or after frame F; repeatable (default 0)\n"
        "  --asset key=path    content for a declared asset key; repeatable\n"
        "  --frames N          block size (default 512); the run always ends ragged\n"
        "  --sr N              sample rate (default: the input's, else 48000)\n"
        "  --len N             total frames when there is no --in\n"
        "  --tempo BPM         host tempo, and what 'b' means in a script (default 120)\n"
        "  --env A D S R       ENV page, seconds/level (default 0.01 0.1 0.8 0.2)\n"
        "  --env-mode MODE     poly | mono | legato | unison\n"
        "  --seed N            host rng_seed (default 0)\n"
        "  --out FILE          write the output, .wav or .npy\n"
        "  --events-out FILE   write the events the engine emitted, as JSON\n"
        "  --wav-bits 16|32    output WAV depth (default 32, float)\n"
        "  --golden FILE       compare against a reference\n"
        "  --tol DB            peak-error tolerance in dBFS (default -100)\n"
        "  --bench             time it; --bench-runs N for best-of (default 5)\n"
        "  --describe          print the descriptor and stop\n"
        "  --rt-guard          count allocations made inside process()\n");
    return code;
}

int fail(const std::string& msg) {
    std::fprintf(stderr, "arbutus-run: %s\n", msg.c_str());
    return 1;
}

bool want(int& i, int argc, char** argv, const char* flag, std::string& out) {
    if (std::strcmp(argv[i], flag) || i + 1 >= argc) return false;
    out = argv[++i];
    return true;
}

// ---- event script ----------------------------------------------------------
// '<time> on <note> <vel>' / '<time> off <note>'. Time is a frame, or beats
// with a 'b' suffix (4b = bar two at 4/4). '#' to end of line is a comment.
std::string parse_notes(const std::string& path, uint32_t sr, double tempo,
                        std::vector<arb_event>& out) {
    std::ifstream f(path);
    if (!f) return "cannot open " + path;
    std::string line;
    int lineno = 0;
    while (std::getline(f, line)) {
        ++lineno;
        if (const std::size_t h = line.find('#'); h != std::string::npos) line.resize(h);
        std::istringstream s(line);
        std::string when, what;
        if (!(s >> when >> what)) continue;

        double t = 0;
        try {
            t = std::stod(when);
        } catch (...) {
            return path + ":" + std::to_string(lineno) + ": bad time \"" + when + "\"";
        }
        const bool beats = !when.empty() && (when.back() == 'b' || when.back() == 'B');
        const double at = beats ? t * (60.0 / tempo) * sr : t;
        // A negative or non-finite frame has no uint32_t: the cast was undefined,
        // and in practice wrapped, dropped the note and stretched the run (#626).
        if (!std::isfinite(at) || at < 0.0 || at > double(UINT32_MAX))
            return path + ":" + std::to_string(lineno) + ": time \"" + when + "\" is out of range";
        const uint32_t frame = uint32_t(at);

        arb_event e{};
        e.frame = frame;
        if (what == "on") {
            int note = 0;
            float vel = 1.0f;
            if (!(s >> note >> vel)) return path + ":" + std::to_string(lineno) + ": on needs <note> <vel>";
            e.type = ARB_EV_NOTE_ON;
            e.a = uint16_t(note);
            e.b = vel;
        } else if (what == "off") {
            int note = 0;
            if (!(s >> note)) return path + ":" + std::to_string(lineno) + ": off needs <note>";
            e.type = ARB_EV_NOTE_OFF;
            e.a = uint16_t(note);
        } else if (what == "key") {
            // Soft-keys (§3.6). Only a sequencer engine ever sees them, and
            // this is how a sequencer golden drives one without a host.
            int k = 0, down = 1, shift = 0;
            if (!(s >> k >> down)) return path + ":" + std::to_string(lineno) +
                                          ": key needs <0..3> <1|0> [shift]";
            s >> shift;
            e.type = ARB_EV_SOFTKEY;
            e.a = uint16_t(k);
            e.b = down ? 1.0f : 0.0f;
            e.flags = shift ? 1u : 0u;
        } else {
            return path + ":" + std::to_string(lineno) + ": expected on/off/key, got \"" + what + "\"";
        }
        out.push_back(e);
    }
    std::stable_sort(out.begin(), out.end(),
                     [](const arb_event& a, const arb_event& b) { return a.frame < b.frame; });
    return {};
}

// The block plan. §3.7.2 says an engine handles any frames <= max_block, every
// call, including 0 — so the run opens with a 0-frame block and always ends
// with a short one, even when the length divides evenly.
std::vector<uint32_t> plan_blocks(uint32_t total, uint32_t block) {
    std::vector<uint32_t> v{0};
    uint32_t pos = 0;
    while (total - pos > block) { v.push_back(block); pos += block; }
    const uint32_t rem = total - pos;
    if (rem == block) { v.push_back(rem / 2); v.push_back(rem - rem / 2); }
    else if (rem) v.push_back(rem);
    return v;
}

// macOS's malloc zone, or on Linux/glibc this build's own counting allocator
// once --rt-guard has armed it; -1 where neither exists (#52).
using arb::rt::live_blocks;

struct Plan {
    std::vector<uint32_t> blocks;
    std::vector<std::vector<arb_event>> events;   // per block, frames rebased
    struct Move { uint32_t frame; uint32_t idx; float norm; };
    std::vector<Move> moves;                      // --at, sorted by frame
    std::vector<std::pair<uint32_t, float>> pitches;   // --pitch, sorted by frame
};

// Returns microseconds spent inside process().
double render(const Box& box, void* h, arb_host& host, const Plan& plan,
              const arb::io::Audio* in, const arb::io::Audio* side,
              arb::io::Audio& out, bool rt_guard, long* allocs,
              std::vector<arb_event>* emitted = nullptr,
              const arb::io::Audio* motion = nullptr, uint32_t motion_block = 0,
              long* out_of_block = nullptr) {
    std::vector<arb_event> ev_out(256);
    uint32_t n_ev_out = 0;
    const bool has_in = (box.desc->caps & ARB_CAP_AUDIO_IN) != 0;
    const bool has_side = (box.desc->caps & ARB_CAP_SIDECHAIN) != 0;
    // A modulator writes ctl_out, not audio (modulation-spec §3.1). Its out[]
    // is still valid by contract and goes to scratch; `out` holds one channel
    // per output instead, each block's value held across that block's frames.
    const bool modulator = (box.desc->caps & ARB_CAP_MODULATOR) != 0;
    std::vector<float> scratch(modulator ? 2 * std::size_t(ARB_MAX_BLOCK) : 0);
    static const std::vector<float> kSilence(ARB_MAX_BLOCK, 0.0f);

    double us = 0;
    uint32_t pos = 0;
    std::size_t next_move = 0, next_pitch = 0;
    host.pitch_st = 0.0f;                    // a --bench repeat starts where the first run did
    for (std::size_t b = 0; b < plan.blocks.size(); ++b) {
        const uint32_t n = plan.blocks[b];
        // --pitch: one value per block, as the host hands pitch_st over (#647).
        while (next_pitch < plan.pitches.size() && plan.pitches[next_pitch].first <= pos)
            host.pitch_st = plan.pitches[next_pitch++].second;
        // --at: a knob moves between blocks, never inside one (§3.7.2).
        while (next_move < plan.moves.size() && plan.moves[next_move].frame <= pos) {
            const Plan::Move& m = plan.moves[next_move++];
            box.vt->set_param(h, m.idx, m.norm);
        }
        arb_io io{};
        io.frames = n;
        for (int c = 0; c < 2; ++c) {
            io.out[c] = modulator ? scratch.data() + std::size_t(c) * ARB_MAX_BLOCK
                                  : out.ch[std::size_t(c)].data() + pos;
            if (has_in) {
                const auto& src = in ? in->ch[std::size_t(c) % in->ch.size()] : kSilence;
                io.in[c] = in ? src.data() + pos : kSilence.data();
            }
            if (has_side) {
                const auto& src = side ? side->ch[std::size_t(c) % side->ch.size()] : kSilence;
                io.side[c] = side ? src.data() + pos : kSilence.data();
            }
        }
        io.ev_in = plan.events[b].empty() ? nullptr : plan.events[b].data();
        io.n_ev_in = uint32_t(plan.events[b].size());
        io.ev_out = ev_out.data();
        io.ev_out_cap = uint32_t(ev_out.size());
        n_ev_out = 0;
        io.n_ev_out = &n_ev_out;
        host.song_frame = int64_t(pos);
        // --motion: row r is the IMU reading for the block starting at frame
        // r x --frames, held after the last row, as the host holds a reading
        // until the next one (modulation-spec §13).
        if (motion && motion->frames() && motion_block) {
            const std::size_t row = std::min<std::size_t>(pos / motion_block, motion->frames() - 1);
            for (std::size_t k = 0; k < 6; ++k)
                host.motion[k] = k < motion->ch.size() ? motion->ch[k][row] : 0.0f;
        }

        const long before = rt_guard ? live_blocks() : 0;
        const auto t0 = std::chrono::steady_clock::now();
        box.vt->process(h, &host, &io);
        const auto t1 = std::chrono::steady_clock::now();
        if (rt_guard && allocs) {
            const long after = live_blocks();
            if (before >= 0 && after > before) *allocs += after - before;
        }
        us += std::chrono::duration<double, std::micro>(t1 - t0).count();
        if (modulator)
            for (std::size_t c = 0; c < out.ch.size() && c < ARB_MAX_OUTPUTS; ++c)
                std::fill_n(out.ch[c].data() + pos, n, io.ctl_out[c]);
        // The header's 0 <= frame < frames. Every stock ENGINE clamps, so a
        // late stamp sounds right here and indexes past the block in a box
        // that trusts the header (#631: the sequencers did it at 64 frames).
        if (out_of_block)
            for (uint32_t k = 0; k < n_ev_out && k < io.ev_out_cap; ++k)
                if (ev_out[k].frame >= n) ++*out_of_block;
        // Frames are block-relative on the wire (§3.4); a dump is only useful
        // on the run's own timeline, so rebase as they come out.
        if (emitted)
            for (uint32_t k = 0; k < n_ev_out && k < io.ev_out_cap; ++k) {
                arb_event e = ev_out[k];
                e.frame += pos;
                emitted->push_back(e);
            }
        pos += n;
    }
    return us;
}

void print_describe(const arb_engine_desc& d) {
    std::printf("id            %s\n", arb::manifest::fixed(d.id, sizeof d.id).c_str());
    std::printf("version       %s\n", arb::manifest::fixed(d.version, sizeof d.version).c_str());
    std::printf("abi           %u\n", d.abi);
    std::printf("caps          %s\n", arb::manifest::caps_json(d.caps).dump().c_str());
    std::printf("honours_env   %s\n", d.honours_env ? "yes" : "no");
    std::printf("latency       %u frames\n", d.latency_frames);
    for (uint32_t i = 0; i < d.num_params; ++i) {
        const arb_param_desc& p = d.params[i];
        std::printf("param %u       %-8s %-6s %-10s %-3s %g .. %g def %g %s%s%s\n", i,
                    arb::manifest::fixed(p.id, sizeof p.id).c_str(),
                    arb::manifest::fixed(p.name, sizeof p.name).c_str(),
                    arb::manifest::type_name(p.type), arb::manifest::curve_name(p.curve),
                    double(p.min), double(p.max), double(p.def),
                    arb::manifest::fixed(p.unit, sizeof p.unit).c_str(),
                    p.per_voice ? " per_voice" : "", p.per_note ? " per_note" : "");
    }
    for (uint32_t i = 0; i < d.num_outputs && i < ARB_MAX_OUTPUTS; ++i)
        std::printf("output %u      %-8s %-6s %s\n", i,
                    arb::manifest::fixed(d.outputs[i].id, sizeof d.outputs[i].id).c_str(),
                    arb::manifest::fixed(d.outputs[i].name, sizeof d.outputs[i].name).c_str(),
                    d.outputs[i].bipolar ? "bipolar" : "unipolar");
    for (uint32_t i = 0; i < d.num_softkeys; ++i)
        std::printf("softkey %u     %s %s\n", i,
                    arb::manifest::fixed(d.softkeys[i].id, sizeof d.softkeys[i].id).c_str(),
                    arb::manifest::fixed(d.softkeys[i].label, sizeof d.softkeys[i].label).c_str());
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) return usage(2);

    Options o;
    o.box = argv[1];
    if (o.box == "-h" || o.box == "--help") return usage(0);

    for (int i = 2; i < argc; ++i) {
        std::string v;
        if (want(i, argc, argv, "--notes", o.notes)) continue;
        if (want(i, argc, argv, "--in", o.in_path)) continue;
        if (want(i, argc, argv, "--side", o.side_path)) continue;
        if (want(i, argc, argv, "--motion", o.motion_path)) continue;
        if (want(i, argc, argv, "--out", o.out_path)) continue;
        if (want(i, argc, argv, "--golden", o.golden_path)) continue;
        if (want(i, argc, argv, "--events-out", o.events_out)) continue;
        if (want(i, argc, argv, "--asset", v)) {
            const std::size_t eq = v.find('=');
            if (eq == std::string::npos) return fail("--asset wants key=path, got " + v);
            o.assets.emplace_back(v.substr(0, eq), v.substr(eq + 1));
            continue;
        }
        if (want(i, argc, argv, "--set", v)) {
            const std::size_t eq = v.find('=');
            if (eq == std::string::npos) return fail("--set wants id=value, got " + v);
            o.sets.emplace_back(v.substr(0, eq), std::stof(v.substr(eq + 1)));
            continue;
        }
        if (want(i, argc, argv, "--at", v)) {
            const std::size_t colon = v.find(':'), eq = v.find('=');
            if (colon == std::string::npos || eq == std::string::npos || eq < colon)
                return fail("--at wants frame:id=value, got " + v);
            o.ats.push_back({uint32_t(std::stoul(v.substr(0, colon))),
                             v.substr(colon + 1, eq - colon - 1), std::stof(v.substr(eq + 1))});
            continue;
        }
        if (want(i, argc, argv, "--pitch", v)) {
            const std::size_t colon = v.find(':');
            if (colon == std::string::npos) return fail("--pitch wants frame:semitones, got " + v);
            o.pitches.emplace_back(uint32_t(std::stoul(v.substr(0, colon))),
                                   std::stof(v.substr(colon + 1)));
            continue;
        }
        if (want(i, argc, argv, "--frames", v)) { o.block = uint32_t(std::stoul(v)); continue; }
        if (want(i, argc, argv, "--sr", v)) { o.sr = uint32_t(std::stoul(v)); continue; }
        if (want(i, argc, argv, "--len", v)) { o.len = uint32_t(std::stoul(v)); continue; }
        if (want(i, argc, argv, "--tempo", v)) { o.tempo = std::stod(v); continue; }
        if (want(i, argc, argv, "--seed", v)) { o.seed = uint32_t(std::stoul(v)); continue; }
        if (want(i, argc, argv, "--tol", v)) { o.tol_db = std::stod(v); continue; }
        if (want(i, argc, argv, "--wav-bits", v)) { o.wav_bits = std::stoi(v); continue; }
        if (want(i, argc, argv, "--bench-runs", v)) { o.bench_runs = std::stoi(v); continue; }
        if (want(i, argc, argv, "--env-mode", v)) {
            if (v == "poly") o.env_mode = ARB_ENV_POLY;
            else if (v == "mono") o.env_mode = ARB_ENV_MONO;
            else if (v == "legato") o.env_mode = ARB_ENV_LEGATO;
            else if (v == "unison") o.env_mode = ARB_ENV_UNISON;
            else return fail("--env-mode wants poly|mono|legato|unison");
            continue;
        }
        if (!std::strcmp(argv[i], "--env")) {
            if (i + 4 >= argc) return fail("--env wants A D S R");
            for (float& e : o.env) e = std::stof(argv[++i]);
            continue;
        }
        if (!std::strcmp(argv[i], "--bench")) { if (!o.bench_runs) o.bench_runs = 5; continue; }
        if (!std::strcmp(argv[i], "--describe")) { o.describe = true; continue; }
        if (!std::strcmp(argv[i], "--rt-guard")) { o.rt_guard = true; continue; }
        if (!std::strcmp(argv[i], "-h") || !std::strcmp(argv[i], "--help")) return usage(0);
        return fail(std::string("unknown option ") + argv[i]);
    }

    if (o.block == 0 || o.block > ARB_MAX_BLOCK)
        return fail("--frames must be 1.." + std::to_string(ARB_MAX_BLOCK));

    // Arm the heap counter before anything is loaded or rendered (#52). Off
    // macOS it is a no-op until this call, so the guard costs nothing when it
    // was not asked for.
    if (o.rt_guard) arb::rt::enable_alloc_counting();

    Box box;
    if (arb::box::Refusal r = arb::box::open(o.box, box); !r.ok()) {
        std::fprintf(stderr, "arbutus-run: %s\n  %s: %s\n", r.code.c_str(), o.box.c_str(),
                     r.detail.c_str());
        return 1;
    }
    if (o.describe) {
        print_describe(*box.desc);
        arb::box::close(box);
        return 0;
    }

    arb::io::Audio in, side, motion;
    if (!o.motion_path.empty())
        if (std::string e = arb::io::read(o.motion_path, motion); !e.empty()) return fail(e);
    if (!o.in_path.empty())
        if (std::string e = arb::io::read(o.in_path, in); !e.empty()) return fail(e);
    if (!o.side_path.empty())
        if (std::string e = arb::io::read(o.side_path, side); !e.empty()) return fail(e);

    uint32_t sr = o.sr ? o.sr : (in.sample_rate ? in.sample_rate : 48000u);

    std::vector<arb_event> events;
    if (!o.notes.empty())
        if (std::string e = parse_notes(o.notes, sr, o.tempo, events); !e.empty()) return fail(e);

    uint32_t total = o.len;
    if (!total && in.frames()) total = in.frames();
    if (!total) total = events.empty() ? sr : (events.back().frame + 2 * sr);
    if (!total) return fail("nothing to render: give --in, --notes or --len");

    // --len may run past the input so an effect's tail can ring out; pad rather
    // than read off the end of the file.
    for (arb::io::Audio* a : {&in, &side})
        for (std::vector<float>& c : a->ch)
            if (c.size() < total) c.resize(total, 0.0f);

    arb_host host{};
    host.sample_rate = sr;
    host.max_block = o.block;
    host.tempo_bpm = o.tempo;
    host.ppqn = 24;
    host.playing = 1;
    host.env_a = o.env[0];
    host.env_d = o.env[1];
    host.env_s = o.env[2];
    host.env_r = o.env[3];
    host.env_mode = o.env_mode;
    // ENV SHIFT row at the host's defaults: no glide, unison at its old fixed
    // spread, no bend. A zeroed env_detune would collapse unison to one pitch.
    host.env_glide = 0.0f;
    host.env_detune = arb::kUnisonSpreadCents;
    host.bend_range = 2.0f;
    host.pitch_st = 0.0f;
    host.rng_seed = o.seed;

    void* h = box.vt->create(&host, box.dir.c_str());
    if (!h) return fail("create() returned NULL");

    // §3.2: control thread, after create(), before the engine renders anything.
    for (const auto& [key, path] : o.assets) {
        if (!arb::manifest::declares_asset(*box.desc, key))
            return fail("this engine declares no asset \"" + key + "\"");
        if (!box.vt->load_asset) return fail("this engine has no load_asset");
        if (box.vt->load_asset(h, key.c_str(), path.c_str()) != 0)
            return fail("load_asset(\"" + key + "\") refused " + path);
    }

    auto param_index = [&](const std::string& id) {
        uint32_t idx = box.desc->num_params;
        for (uint32_t i = 0; i < box.desc->num_params; ++i)
            if (arb::manifest::fixed(box.desc->params[i].id, sizeof box.desc->params[i].id) == id)
                idx = i;
        return idx;
    };
    for (const auto& [id, value] : o.sets) {
        const uint32_t idx = param_index(id);
        if (idx == box.desc->num_params) return fail("no parameter \"" + id + "\" on this engine");
        box.vt->set_param(h, idx, arb::norm(box.desc->params[idx], value));
    }

    Plan plan;
    for (const Options::At& a : o.ats) {
        const uint32_t idx = param_index(a.id);
        if (idx == box.desc->num_params) return fail("no parameter \"" + a.id + "\" on this engine");
        plan.moves.push_back({a.frame, idx, arb::norm(box.desc->params[idx], a.value)});
    }
    std::stable_sort(plan.moves.begin(), plan.moves.end(),
                     [](const Plan::Move& a, const Plan::Move& b) { return a.frame < b.frame; });
    plan.pitches = o.pitches;
    std::stable_sort(plan.pitches.begin(), plan.pitches.end(),
                     [](const auto& a, const auto& b) { return a.first < b.first; });
    plan.blocks = plan_blocks(total, o.block);
    plan.events.resize(plan.blocks.size());
    {
        std::size_t e = 0;
        uint32_t pos = 0;
        for (std::size_t b = 0; b < plan.blocks.size(); ++b) {
            const uint32_t n = plan.blocks[b];
            while (e < events.size() && events[e].frame < pos + n) {
                arb_event ev = events[e++];
                ev.frame -= pos;
                plan.events[b].push_back(ev);
            }
            pos += n;
        }
    }

    arb::io::Audio out;
    out.sample_rate = sr;
    const bool modulator = (box.desc->caps & ARB_CAP_MODULATOR) != 0;
    out.ch.assign(modulator ? std::max<std::size_t>(1, box.desc->num_outputs) : 2,
                  std::vector<float>(total, 0.0f));

    long allocs = 0, out_of_block = 0;
    std::vector<arb_event> emitted;
    double us = render(box, h, host, plan, in.frames() ? &in : nullptr,
                       side.frames() ? &side : nullptr, out, o.rt_guard, &allocs,
                       o.events_out.empty() ? nullptr : &emitted,
                       motion.frames() ? &motion : nullptr, o.block, &out_of_block);

    int rc = 0;
    // NaN or inf out of a box is a fault with or without a golden (#626): an
    // unstable filter or a divide by zero is what a test run is there to catch.
    long bad = 0;
    for (const auto& ch : out.ch)
        for (float s : ch)
            if (!std::isfinite(s)) ++bad;
    if (bad) {
        std::printf("output: %ld non-finite samples FAIL\n", bad);
        rc = 1;
    }
    if (out_of_block) {
        std::printf("events: %ld emitted at frame >= the block's frames FAIL\n", out_of_block);
        rc = 1;
    }

    // A sequencer's golden is its event list, not audio (§9.4). One object per
    // line so a diff points at the step that moved.
    if (!o.events_out.empty()) {
        std::ofstream f(o.events_out);
        if (!f) return fail("cannot write " + o.events_out);
        f << "[\n";
        for (std::size_t i = 0; i < emitted.size(); ++i) {
            const arb_event& e = emitted[i];
            char buf[160];
            std::snprintf(buf, sizeof buf,
                          "  {\"frame\": %u, \"type\": %u, \"a\": %u, \"b\": %.6f, \"flags\": %u}%s\n",
                          e.frame, unsigned(e.type), unsigned(e.a), double(e.b), e.flags,
                          i + 1 < emitted.size() ? "," : "");
            f << buf;
        }
        f << "]\n";
        std::printf("events: %zu emitted -> %s\n", emitted.size(), o.events_out.c_str());
    }

    if (o.rt_guard) {
        if (live_blocks() < 0) std::printf("rt-guard: unavailable on this platform\n");
        else if (allocs) { std::printf("rt-guard: %ld allocations inside process() FAIL\n", allocs); rc = 1; }
        else std::printf("rt-guard: no allocations inside process()\n");
    }

    if (!o.out_path.empty())
        if (std::string e = arb::io::write(o.out_path, out, o.wav_bits); !e.empty()) return fail(e);

    if (!o.golden_path.empty()) {
        arb::io::Audio ref;
        if (std::string e = arb::io::read(o.golden_path, ref); !e.empty()) return fail(e);
        if (ref.frames() != total)
            return fail("golden is " + std::to_string(ref.frames()) + " frames, run is " +
                        std::to_string(total));
        // A non-finite difference (NaN or inf on either side) is a failure, never
        // skipped: std::max(peak, NaN) returns `peak`, so before #626 an engine
        // that output nothing but NaN passed every golden at -inf dBFS.
        double peak = 0.0;
        long nonfinite = 0;
        for (std::size_t c = 0; c < ref.ch.size() && c < out.ch.size(); ++c)
            for (uint32_t i = 0; i < total; ++i) {
                const double d = std::abs(double(out.ch[c][i]) - double(ref.ch[c][i]));
                if (!std::isfinite(d)) ++nonfinite;
                else peak = std::max(peak, d);
            }
        const double db = nonfinite ? std::numeric_limits<double>::infinity()
                        : peak > 0.0 ? 20.0 * std::log10(peak)
                                     : -std::numeric_limits<double>::infinity();
        const bool pass = db <= o.tol_db;
        if (nonfinite)
            std::printf("golden %s: %ld non-finite differences over %u frames x %zu ch FAIL\n",
                        o.golden_path.c_str(), nonfinite, total, ref.ch.size());
        else
            std::printf("golden %s: peak error %.1f dBFS over %u frames x %zu ch (tol %.1f) %s\n",
                        o.golden_path.c_str(), db, total, ref.ch.size(), o.tol_db,
                        pass ? "PASS" : "FAIL");
        if (!pass) rc = 1;
    }

    if (o.bench_runs > 0) {
        double best = us;
        for (int i = 1; i < o.bench_runs; ++i) {
            box.vt->reset(h);
            best = std::min(best, render(box, h, host, plan, in.frames() ? &in : nullptr,
                                         side.frames() ? &side : nullptr, out, false, nullptr,
                                         nullptr, motion.frames() ? &motion : nullptr, o.block));
        }
        const std::size_t nblocks = plan.blocks.size() - 1;      // the 0-frame block is not one
        const double per = nblocks ? best / double(nblocks) : 0.0;
        const double budget = double(o.block) / double(sr) * 1e6;
        std::printf("bench %s: %.2f us/block (%u frames @ %u Hz, best of %d) = %.2f%% of a core\n",
                    arb::manifest::fixed(box.desc->id, sizeof box.desc->id).c_str(), per, o.block,
                    sr, o.bench_runs, 100.0 * per / budget);
    }

    box.vt->destroy(h);
    arb::box::close(box);
    return rc;
}
