// hostlib/audiofile.cpp — `write_durable`, the crash-safe half of the writer
// (#93, device-os-spec.md § 7.3). No framework; test_tape_io.cpp is the pattern.
//
// The claim is not "a file appeared" — `write` already does that. It is that a
// write which *fails* leaves the previous contents of the path whole, because
// on the device that path holds the only copy of a take. So every assertion
// here is paired with the same assertion against plain `write`, which is the
// negative control (#56): the comparison has to be shown going the other way,
// or "the old file survived" could just mean the check is blind.
//
//   * a durable write round-trips, and leaves no `.tmp` beside the file
//   * a durable write that cannot create its temp file refuses **and** leaves
//     the old file byte for byte -- where plain `write` to the same path in
//     the same state replaces it
//   * a refused durable write leaves no `.tmp` behind either, so the next one
//     is not poisoned by the last one's wreckage
//   * a write whose *last* buffer the medium refuses (a card that fills up
//     while the stream's buffer is flushed on close) is refused too, and
//     leaves the old file whole (#670: the writer tested the stream before
//     closing it, so the final flush's failure was never seen)
#include <csignal>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

#include <sys/resource.h>
#include <unistd.h>

#include <audiofile.hpp>

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

namespace fs = std::filesystem;

arb::io::Audio tone(std::size_t frames, float k) {
    arb::io::Audio a;
    a.sample_rate = 48000;
    a.ch.assign(2, std::vector<float>(frames, 0.0f));
    for (std::size_t i = 0; i < frames; ++i) {
        a.ch[0][i] = k * float(i % 97) / 97.0f;
        a.ch[1][i] = -k * float(i % 31) / 31.0f;
    }
    return a;
}

std::vector<uint8_t> slurp(const fs::path& p) {
    std::vector<uint8_t> b;
    std::FILE* f = std::fopen(p.string().c_str(), "rb");
    if (!f) return b;
    uint8_t buf[4096];
    for (std::size_t n; (n = std::fread(buf, 1, sizeof buf, f)) > 0;)
        b.insert(b.end(), buf, buf + n);
    std::fclose(f);
    return b;
}

struct Dir {
    fs::path p;
    Dir() {
        p = fs::temp_directory_path() /
            ("arbutus-audiofile-" + std::to_string(::getpid()));
        fs::remove_all(p);
        fs::create_directories(p);
    }
    ~Dir() { fs::remove_all(p); }
};

// ---------------------------------------------------------------------------

void test_round_trip(const Dir& d) {
    const fs::path path = d.p / "m.wav";
    const arb::io::Audio a = tone(5000, 0.8f);
    CHECK(arb::io::write_durable(path.string(), a, 32).empty(), "durable write refused");
    CHECK(!fs::exists(path.string() + ".tmp"), "a .tmp survived a successful write");

    arb::io::Audio back;
    CHECK(arb::io::read(path.string(), back).empty(), "cannot read it back");
    CHECK(back.frames() == a.frames(), "frames %u vs %u", back.frames(), a.frames());
    CHECK(back.ch.size() == 2, "channels %zu", back.ch.size());
    bool same = back.frames() == a.frames() && back.ch.size() == a.ch.size();
    for (std::size_t c = 0; same && c < a.ch.size(); ++c)
        for (std::size_t i = 0; i < a.ch[c].size(); ++i)
            if (a.ch[c][i] != back.ch[c][i]) { same = false; break; }
    CHECK(same, "float32 did not survive the round trip");
}

// The fault: a *directory* where the temp file has to go. Portable, needs no
// permissions and no full disk, and it is the same failure shape as ENOSPC —
// the temp file cannot be created, so nothing should touch the destination.
void test_failed_write_keeps_the_old_file(const Dir& d) {
    const fs::path path = d.p / "keep.wav";
    CHECK(arb::io::write_durable(path.string(), tone(3000, 0.5f), 32).empty(),
          "the first write refused");
    const std::vector<uint8_t> before = slurp(path);
    CHECK(!before.empty(), "nothing was written");

    fs::create_directories(path.string() + ".tmp");
    const std::string e = arb::io::write_durable(path.string(), tone(9000, 0.1f), 32);
    CHECK(!e.empty(), "a write that cannot create its temp file reported success");
    CHECK(slurp(path) == before, "a refused durable write damaged the previous save");
    fs::remove_all(path.string() + ".tmp");

    // ...and the control. Plain `write` in exactly the same state replaces the
    // file, which is what the old `tape_save` did on every STOP: if this line
    // ever passes, the comparison above has stopped meaning anything.
    CHECK(arb::io::write(path.string(), tone(9000, 0.1f), 32).empty(),
          "the control write refused");
    CHECK(slurp(path) != before, "control: plain write left the file unchanged");
}

void test_failed_write_leaves_no_tmp(const Dir& d) {
    // A path whose directory does not exist: the temp file cannot be opened.
    const fs::path path = d.p / "gone" / "x.wav";
    CHECK(!arb::io::write_durable(path.string(), tone(100, 0.5f), 32).empty(),
          "a write into a missing directory reported success");
    CHECK(!fs::exists(path), "the destination appeared anyway");
    CHECK(!fs::exists(path.string() + ".tmp"), "a .tmp was left behind");
}

// The fault: the card fills while the stream's last buffer is written out.
// RLIMIT_FSIZE stands in for the full card: a write past it fails with EFBIG
// (SIGXFSZ ignored, or it would kill the test), and the file keeps only what
// fitted. The take is smaller than the stream's buffer, so every byte is still
// buffered when the writer returns: only `close` (or the destructor) writes
// them, which is where the old code stopped looking.
void test_failed_final_flush_keeps_the_old_file(const Dir& d) {
    for (const char* name : {"flush.wav", "flush.npy"}) {
        const fs::path path = d.p / name;
        CHECK(arb::io::write_durable(path.string(), tone(600, 0.5f), 32).empty(),
              "%s: the first write refused", name);
        const std::vector<uint8_t> before = slurp(path);
        CHECK(before.size() > 2048, "%s: the first take is only %zu bytes", name, before.size());

        rlimit was{};
        ::getrlimit(RLIMIT_FSIZE, &was);
        rlimit cap = was;
        cap.rlim_cur = 1024;                   // the card has 1 KiB left
        std::signal(SIGXFSZ, SIG_IGN);
        ::setrlimit(RLIMIT_FSIZE, &cap);
        const std::string e = arb::io::write_durable(path.string(), tone(700, 0.1f), 32);
        ::setrlimit(RLIMIT_FSIZE, &was);
        std::signal(SIGXFSZ, SIG_DFL);

        CHECK(!e.empty(), "%s: a write the card cut short reported success", name);
        CHECK(slurp(path) == before, "%s: a refused durable write replaced the previous save "
              "(%zu bytes, was %zu)", name, slurp(path).size(), before.size());
        CHECK(!fs::exists(path.string() + ".tmp"), "%s: a .tmp was left behind", name);
    }
}

}  // namespace

int main() {
    Dir d;
    test_round_trip(d);
    test_failed_write_keeps_the_old_file(d);
    test_failed_write_leaves_no_tmp(d);
    test_failed_final_flush_keeps_the_old_file(d);
    if (failures) {
        std::printf("audiofile: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("audiofile: ok\n");
    return 0;
}
