// host/imu_lsm6dso.hpp — the LSM6DSO driver against a fake register map.
// device-os-spec.md § 6.5's "fake test", because no P3 board exists: no
// framework (test_events.cpp is the pattern), and the same `operator new`
// counter, because the values this driver produces reach the audio thread
// through `Ck::Motion` and the path into the audio thread does not allocate.
//
// What a fake bus can actually prove, and what it cannot:
//
//   proved   the init register writes and their order, that a wrong WHO_AM_I
//            or a reset that never clears refuses (#598), the +-4 g / +-500 dps
//            scaling to g and deg/s, the gyro-first-in-registers /
//            accel-first-in-motion[] swap, the data-ready gating, that a bus
//            error is sticky until re-init and hands the engine zeros once
//            (#598), that sampling allocates nothing, the two messages a
//            register read hands the kernel in one I2C_RDWR (#418), and that
//            each board's mount turns the chip's axes into the instrument
//            frame (#1048, modulation-spec § 13)
//   not      that 0x6A is the address on the board, that the bus is at 400 kHz,
//            that INT1 does what io-map says, that the chip's axes point where
//            ST's Fig. 3 and the footprint say. Those are P3's arrival (the
//            bring-up's stage 9 tilts the board, #1048).

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <map>
#include <new>
#include <vector>

#include <imu_lsm6dso.hpp>

namespace {

std::atomic<long> g_news{0};
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

using namespace arb::host;
using namespace arb::host::lsm6dso;

/// A register map in memory. `writes` is the order they arrived in, which is
/// half of what this test is about: I3C off before anything else claims the
/// bus (DS9), and the soft reset before the configuration.
struct FakeBus : I2cBus {
    std::map<uint8_t, uint8_t> regs;
    std::vector<std::pair<uint8_t, uint8_t>> writes;
    int fail_after = -1;            // -1 = never; else fail once the count hits
    int transfers = 0;
    bool stuck_reset = false;       // SW_RESET never clears (#598)

    FakeBus() {
        regs[kWhoAmI] = kWhoAmIValue;
        regs[kStatusReg] = 0;
        for (uint8_t r = kOutxLG; r < kOutxLG + 12; ++r) regs[r] = 0;
    }

    bool tick() {
        if (fail_after >= 0 && transfers >= fail_after) return false;
        ++transfers;
        return true;
    }

    bool write(uint8_t reg, uint8_t value) override {
        if (!tick()) return false;
        // The real part clears SW_RESET itself once the reset is done.
        regs[reg] = (reg == kCtrl3C && (value & kSwReset) && !stuck_reset) ? uint8_t(0) : value;
        writes.push_back({reg, value});
        return true;
    }

    bool read(uint8_t reg, uint8_t* dst, std::size_t n) override {
        if (!tick()) return false;
        for (std::size_t i = 0; i < n; ++i) dst[i] = regs[uint8_t(reg + i)];
        return true;
    }

    void set16(uint8_t reg, int16_t v) {
        regs[reg] = uint8_t(uint16_t(v) & 0xFF);
        regs[uint8_t(reg + 1)] = uint8_t((uint16_t(v) >> 8) & 0xFF);
    }
    /// gyro x/y/z then accel x/y/z, the register order (datasheet § 9.16).
    void set_sample(int16_t gx, int16_t gy, int16_t gz,
                    int16_t ax, int16_t ay, int16_t az) {
        set16(kOutxLG + 0, gx);
        set16(kOutxLG + 2, gy);
        set16(kOutxLG + 4, gz);
        set16(kOutxLG + 6, ax);
        set16(kOutxLG + 8, ay);
        set16(kOutxLG + 10, az);
        regs[kStatusReg] = kStatusXlda | kStatusGda;
    }

    int index_of(uint8_t reg) const {
        for (std::size_t i = 0; i < writes.size(); ++i)
            if (writes[i].first == reg) return int(i);
        return -1;
    }
    uint8_t written(uint8_t reg) const {
        for (auto it = writes.rbegin(); it != writes.rend(); ++it)
            if (it->first == reg) return it->second;
        return 0xFF;
    }
};

bool near(float a, float b, float eps = 1e-5f) {
    const float d = a - b;
    return (d < 0 ? -d : d) <= eps;
}

void test_init_writes_the_spec_registers_in_order() {
    FakeBus bus;
    Lsm6dso imu;
    const char* why = "x";
    CHECK(imu.init(bus, &why), "init refused a good part: %s", why);
    CHECK(imu.healthy(), "healthy() is false after a good init");

    CHECK(bus.written(kCtrl1Xl) == kCtrl1XlValue,
          "CTRL1_XL is 0x%02x, want 0x%02x (208 Hz, +-4 g)",
          bus.written(kCtrl1Xl), kCtrl1XlValue);
    CHECK(bus.written(kCtrl2G) == kCtrl2GValue,
          "CTRL2_G is 0x%02x, want 0x%02x (208 Hz, +-500 dps)",
          bus.written(kCtrl2G), kCtrl2GValue);
    CHECK(bus.written(kCtrl3C) == kCtrl3CValue,
          "CTRL3_C is 0x%02x, want 0x%02x (BDU | IF_INC)",
          bus.written(kCtrl3C), kCtrl3CValue);
    CHECK(bus.written(kInt1Ctrl) == kInt1CtrlValue,
          "INT1_CTRL is 0x%02x, want 0x%02x (DRDY_XL | DRDY_G)",
          bus.written(kInt1Ctrl), kInt1CtrlValue);

    // DS9: I3C must be disabled before the sensor is configured, and the soft
    // reset must be the first write of all — a half-configured IMU from a
    // previous run of the host reads plausible numbers, which is worse.
    CHECK(bus.writes.size() >= 2 && bus.writes[0].first == kCtrl3C &&
              (bus.writes[0].second & kSwReset),
          "the first write is not the soft reset");
    const int i3c = bus.index_of(kCtrl9Xl);
    CHECK(i3c >= 0, "CTRL9_XL (I3C_disable) was never written");
    CHECK(i3c < bus.index_of(kCtrl1Xl) && i3c < bus.index_of(kCtrl2G),
          "I3C was disabled after the sensor was configured (DS9)");
}

void test_a_wrong_who_am_i_is_refused() {
    FakeBus bus;
    bus.regs[kWhoAmI] = 0x69;          // an LSM6DS3, say
    Lsm6dso imu;
    const char* why = "";
    CHECK(!imu.init(bus, &why), "init accepted the wrong part");
    CHECK(why != nullptr && std::strstr(why, "WHO_AM_I") != nullptr,
          "the refusal does not name WHO_AM_I: %s", why ? why : "(null)");
    CHECK(!imu.healthy(), "healthy() is true after a refused init");
}

void test_scaling_and_the_accel_first_convention() {
    FakeBus bus;
    Lsm6dso imu;
    imu.set_mount(kMountChip);              // the chip's own axes: scaling only
    CHECK(imu.init(bus, nullptr), "init");

    // 1 g on Z, nothing else; 100 dps on X. In raw LSBs at the configured
    // ranges: 1 / 0.000122 = 8196.7 -> 8197, and 100 / 0.0175 = 5714.3 -> 5714.
    bus.set_sample(/*gx*/ 5714, 0, 0, /*ax*/ 0, 0, 8197);
    float m[6] = {9, 9, 9, 9, 9, 9};
    CHECK(imu.sample(m), "sample failed on a good bus");
    CHECK(near(m[0], 0.0f) && near(m[1], 0.0f),
          "accel x/y should be zero, got %.4f %.4f", m[0], m[1]);
    CHECK(near(m[2], 1.0f, 1e-3f), "accel z should be ~1 g, got %.4f g", m[2]);
    CHECK(near(m[3], 100.0f, 0.02f), "gyro x should be ~100 dps, got %.3f", m[3]);
    CHECK(near(m[4], 0.0f) && near(m[5], 0.0f),
          "gyro y/z should be zero, got %.4f %.4f", m[4], m[5]);

    // Negative values are two's complement, and the sign must survive.
    bus.set_sample(0, 0, -5714, 0, 0, -8197);
    CHECK(imu.sample(m), "sample failed on the negative case");
    CHECK(near(m[2], -1.0f, 1e-3f), "accel z should be ~-1 g, got %.4f", m[2]);
    CHECK(near(m[5], -100.0f, 0.02f), "gyro z should be ~-100 dps, got %.3f", m[5]);
}

void test_ready_gates_on_both_status_bits() {
    FakeBus bus;
    Lsm6dso imu;
    CHECK(imu.init(bus, nullptr), "init");
    bus.regs[kStatusReg] = 0;
    CHECK(!imu.ready(), "ready() with no data-ready bit set");
    bus.regs[kStatusReg] = kStatusXlda;
    CHECK(!imu.ready(), "ready() with only the accelerometer ready");
    bus.regs[kStatusReg] = kStatusGda;
    CHECK(!imu.ready(), "ready() with only the gyroscope ready");
    bus.regs[kStatusReg] = kStatusXlda | kStatusGda;
    CHECK(imu.ready(), "ready() false with both bits set");
}

void test_a_bus_error_is_sticky_until_re_init() {
    FakeBus bus;
    Lsm6dso imu;
    CHECK(imu.init(bus, nullptr), "init");
    bus.set_sample(0, 0, 0, 0, 0, 8197);
    float m[6] = {0, 0, 0, 0, 0, 0};
    CHECK(imu.sample(m), "the first sample");
    bus.fail_after = bus.transfers;                 // the bus dies now
    CHECK(!imu.sample(m), "sample succeeded on a dead bus");
    CHECK(!imu.healthy(), "a failed transfer left the driver healthy");
    CHECK(imu.errors() == 1, "errors() is %ld, want 1", imu.errors());
    CHECK(!imu.sample(m), "an unhealthy driver kept sampling");
    CHECK(imu.errors() == 1, "an unhealthy driver counted a second error");
    bus.fail_after = -1;                            // § 6.5's 5 s retry
    CHECK(imu.init(bus, nullptr), "re-init after the bus came back");
    CHECK(imu.sample(m), "sample after re-init");
}

// #598: a part stuck in reset was configured and marked healthy.
void test_a_stuck_reset_is_refused() {
    FakeBus bus;
    bus.stuck_reset = true;
    Lsm6dso imu;
    const char* why = "";
    CHECK(!imu.init(bus, &why), "init accepted a part whose reset never cleared");
    CHECK(!imu.healthy(), "healthy() is true after a stuck reset");
    CHECK(why != nullptr && std::strstr(why, "reset") != nullptr,
          "the refusal does not name the reset: %s", why ? why : "(null)");
    CHECK(bus.index_of(kCtrl1Xl) < 0, "a part stuck in reset was configured");
}

// #598: the turn the bus fails hands the engine six zeros, once, so the last
// tilt is not held through the outage; a healthy turn with no data hands
// nothing.
void test_feed_zeros_motion_once_on_a_failure() {
    FakeBus bus;
    Lsm6dso imu;
    CHECK(imu.init(bus, nullptr), "init");
    float m[6] = {9, 9, 9, 9, 9, 9};
    CHECK(!imu.feed(m), "feed() handed a value with no data ready");
    CHECK(m[0] == 9.0f, "feed() touched out with nothing to hand");
    bus.set_sample(0, 0, 0, 4098, 0, 0);            // ~0.5 g on x: a tilt
    CHECK(imu.feed(m) && near(m[0], 0.5f, 1e-3f), "feed() the tilt, got %.4f", m[0]);
    bus.fail_after = bus.transfers;                 // the bus dies now
    CHECK(imu.feed(m), "feed() handed nothing on the turn the bus failed");
    bool zero = true;
    for (float v : m) zero = zero && v == 0.0f;
    CHECK(zero, "the failure turn handed %.4f %.4f %.4f, not zeros", m[0], m[1], m[2]);
    CHECK(!imu.healthy(), "the driver is healthy on a dead bus");
    m[0] = 9.0f;
    CHECK(!imu.feed(m) && m[0] == 9.0f, "feed() handed a second value while unhealthy");
}

// #1048: each board's mount puts the chip's axes in the instrument frame
// (modulation-spec § 13). Each gesture is a chip reading worked out from where
// the part's axes point on that board (the header's tables say how), and the
// test asks for the frame's sign: TILT-X + with the right edge down, TILT-Y +
// with the front edge down, z +1 g flat, SPIN + clockwise from above, gyro x +
// while the right edge goes down, gyro y + while the front edge goes down.
// In raw LSBs: 1 g = 8197, 100 dps = 5714.
struct Gesture {
    const char* what;
    int16_t gx, gy, gz, ax, ay, az;     // the chip's reading
    int slot;                           // the motion[] slot it must move
    float want;                         // ... to this, in g or deg/s
};

void check_mount(const ImuMount& mount, const Gesture* g, int n) {
    FakeBus bus;
    Lsm6dso imu;
    imu.set_mount(mount);
    CHECK(imu.init(bus, nullptr), "init");
    for (int i = 0; i < n; ++i) {
        bus.set_sample(g[i].gx, g[i].gy, g[i].gz, g[i].ax, g[i].ay, g[i].az);
        float m[6] = {9, 9, 9, 9, 9, 9};
        CHECK(imu.sample(m), "%s: sample", mount.name);
        const float eps = g[i].slot < 3 ? 1e-3f : 0.02f;
        CHECK(near(m[g[i].slot], g[i].want, eps),
              "%s, %s: motion[%d] is %.3f, want %.3f", mount.name, g[i].what,
              g[i].slot, m[g[i].slot], g[i].want);
        for (int k = 0; k < 6; ++k)
            if (k != g[i].slot)
                CHECK(near(m[k], 0.0f, 1e-3f), "%s, %s: motion[%d] is %.3f, want 0",
                      mount.name, g[i].what, k, m[k]);
    }
}

void test_p3_mount_reads_the_instrument_frame() {
    // P3's U12, bottom side, rotation 0: chip X points left, Y to the rear,
    // Z down. Flat, gravity's reaction points up, along -Z: the chip reads -1 g.
    const Gesture g[] = {
        {"flat, face up",                   0, 0, 0,  0, 0, -8197, 2, 1.0f},
        {"right edge down (chip X up)",     0, 0, 0,  8197, 0, 0,  0, 1.0f},
        {"front edge down (chip Y up)",     0, 0, 0,  0, 8197, 0,  1, 1.0f},
        // Clockwise from above is right-handed about down, i.e. chip +Z.
        {"turning clockwise from above",    0, 0, 5714,  0, 0, 0,  5, 100.0f},
        // Right edge going down is right-handed about the rear: chip +Y.
        {"right edge going down",           0, 5714, 0,  0, 0, 0,  3, 100.0f},
        // Front edge going down is right-handed about the right: chip -X.
        {"front edge going down",           -5714, 0, 0, 0, 0, 0,  4, 100.0f},
    };
    check_mount(kMountP3, g, int(sizeof g / sizeof g[0]));
}

void test_top_mount_reads_the_instrument_frame() {
    // A top-side part at rotation 0: chip X right, Y to the rear, Z up.
    const Gesture g[] = {
        {"flat, face up",                   0, 0, 0,  0, 0, 8197,  2, 1.0f},
        {"right edge down (chip X down)",   0, 0, 0,  -8197, 0, 0, 0, 1.0f},
        {"front edge down (chip Y up)",     0, 0, 0,  0, 8197, 0,  1, 1.0f},
        {"turning clockwise from above",    0, 0, -5714, 0, 0, 0,  5, 100.0f},
        {"right edge going down",           0, 5714, 0,  0, 0, 0,  3, 100.0f},
        {"front edge going down",           5714, 0, 0,  0, 0, 0,  4, 100.0f},
    };
    check_mount(kMountTop, g, int(sizeof g / sizeof g[0]));
}

void test_the_driver_defaults_to_p3_and_mounts_resolve_by_name() {
    Lsm6dso imu;
    CHECK(&imu.mount() == &kMountP3, "the default mount is %s, not p3",
          imu.mount().name);
    CHECK(imu_mount("p3") == &kMountP3 && imu_mount("top") == &kMountTop &&
              imu_mount("chip") == &kMountChip,
          "a mount's name does not find it");
    CHECK(imu_mount("bottom") == nullptr && imu_mount(nullptr) == nullptr,
          "an unknown name found a mount");
    // Every table is a signed permutation within its triple: no axis twice,
    // no gyro reading landing in an accel slot.
    for (const ImuMount* m : {&kMountP3, &kMountTop, &kMountChip}) {
        bool seen[6] = {};
        for (int k = 0; k < 6; ++k) {
            CHECK(m->src[k] / 3 == k / 3, "%s: slot %d reads across triples", m->name, k);
            CHECK(m->sign[k] == 1 || m->sign[k] == -1, "%s: slot %d's sign", m->name, k);
            CHECK(m->src[k] < 6 && !seen[m->src[k]], "%s: chip axis %d used twice",
                  m->name, m->src[k]);
            if (m->src[k] < 6) seen[m->src[k]] = true;
        }
    }
}

void test_sampling_allocates_nothing() {
    FakeBus bus;
    Lsm6dso imu;
    CHECK(imu.init(bus, nullptr), "init");
    bus.set_sample(1, 2, 3, 4, 5, 6);
    float m[6];
    CHECK(imu.sample(m), "warm-up sample");         // the fake map is populated
    const long before = g_news.load();
    for (int i = 0; i < 208; ++i) {                 // one second at the ODR
        imu.ready();
        imu.sample(m);
    }
    const long after = g_news.load();
    CHECK(after == before,
          "a second of sampling called operator new %ld time(s)", after - before);
}

// #418: a register read is one I2C_RDWR transfer — the address written, a
// repeated START, the burst read — never a write() and a read() with a STOP
// between. This is the shape LinuxI2c::read hands the kernel.
void test_a_register_read_is_one_combined_transfer() {
    uint8_t reg = kOutxLG;
    uint8_t dst[12];
    I2cMsg m[2];
    i2c_register_read(kAddress, &reg, dst, sizeof dst, m);
    CHECK(m[0].addr == kAddress && m[1].addr == kAddress,
          "both messages go to 0x%02x, got 0x%02x / 0x%02x", kAddress,
          m[0].addr, m[1].addr);
    CHECK(m[0].flags == 0 && m[0].len == 1 && m[0].buf == &reg,
          "the first message writes the one register byte");
    CHECK(m[1].flags == kI2cMsgRead && m[1].len == sizeof dst && m[1].buf == dst,
          "the second message reads %zu bytes into dst", sizeof dst);
    CHECK(kI2cMsgRead == 0x0001, "I2C_M_RD is 0x0001 in linux/i2c.h");
}

}  // namespace

// Same replacement as test_events.cpp, and the same reason for [[gnu::noinline]]
// (GCC 14 on the CM4 reports a -Wmismatched-new-delete false positive without it).
[[gnu::noinline]] void* operator new(std::size_t n) {
    g_news.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main() {
    test_init_writes_the_spec_registers_in_order();
    test_a_wrong_who_am_i_is_refused();
    test_scaling_and_the_accel_first_convention();
    test_p3_mount_reads_the_instrument_frame();
    test_top_mount_reads_the_instrument_frame();
    test_the_driver_defaults_to_p3_and_mounts_resolve_by_name();
    test_ready_gates_on_both_status_bits();
    test_a_bus_error_is_sticky_until_re_init();
    test_a_stuck_reset_is_refused();
    test_feed_zeros_motion_once_on_a_failure();
    test_sampling_allocates_nothing();
    test_a_register_read_is_one_combined_transfer();
    if (failures) {
        std::printf("test_imu: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("test_imu: ok\n");
    return 0;
}
