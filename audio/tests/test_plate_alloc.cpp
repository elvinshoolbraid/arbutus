// #1215: arbutus/plate's create() under a failed allocation is the host's
// refusal, never an abort.
//
// The plate sizes its delay rings in its constructor (a throwing
// std::vector::assign); `new (std::nothrow) Engine` guards only the Engine
// itself. This loads the real box through the host's own path (rack.cpp's
// make_instance, linked here as test_tape links tape.cpp) with an operator new
// that throws for any block of 32 KiB or more -- the rings, nothing the loader
// or the Engine object asks for -- and wants the "create() returned NULL"
// refusal (ENGINE BROKEN). Before the fix the bad_alloc left create() and the
// process aborted. A negative control: disarmed, the same load succeeds.

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <string>

#include <arbutus_engine.h>

#include <library.hpp>

#include "rack.hpp"

namespace {

std::atomic<bool> g_armed{false};
std::atomic<long> g_refused{0};
constexpr std::size_t kBig = 32 * 1024;

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

}  // namespace

// test_tape_pool.cpp's pattern: the scalar forms only. The executable's
// definition interposes on the dlopen'd box's too (libstdc++'s nothrow form
// calls this one), so the box's rings come through here.
[[gnu::noinline]] void* operator new(std::size_t n) {
    if (n >= kBig && g_armed.load(std::memory_order_relaxed)) {
        g_refused.fetch_add(1, std::memory_order_relaxed);
        throw std::bad_alloc();
    }
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: test_plate_alloc <box root>\n");
        return 2;
    }
    arb::box::Library lib;
    lib.add_root(argv[1]);
    lib.rescan();
    CHECK(lib.has("arbutus/plate"), "no arbutus/plate under %s", argv[1]);

    arb_host hb{};
    hb.sample_rate = 48000;
    hb.max_block = 128;

    {
        arb::host::InstancePtr inst;
        g_armed = true;
        const arb::host::Refusal r = arb::host::make_instance(lib, "arbutus/plate", "", hb, {}, inst);
        g_armed = false;
        CHECK(g_refused.load() >= 1, "the plate asked for no block of %zu bytes or more", kBig);
        CHECK(r.code == "ENGINE BROKEN", "a failed allocation: got \"%s\" (%s)",
              r.code.c_str(), r.detail.c_str());
        CHECK(r.detail.find("create() returned NULL") != std::string::npos,
              "not the create() refusal: %s", r.detail.c_str());
        CHECK(!inst, "a refused load left an instance");
    }
    {
        arb::host::InstancePtr inst;
        const arb::host::Refusal r = arb::host::make_instance(lib, "arbutus/plate", "", hb, {}, inst);
        CHECK(r.ok() && inst, "disarmed, the plate does not load: \"%s\" (%s)",
              r.code.c_str(), r.detail.c_str());
    }

    if (failures) {
        std::printf("test_plate_alloc: %d failed\n", failures);
        return 1;
    }
    std::printf("test_plate_alloc: all pass\n");
    return 0;
}
