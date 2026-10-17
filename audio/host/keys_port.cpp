// keys_port.cpp — see keys_port.hpp.
#include "keys_port.hpp"

#include <cerrno>
#include <cstring>

#include <fcntl.h>
#include <unistd.h>

#if defined(__linux__)
// termios2 and BOTHER: the only way to ask Linux for a rate with no B
// constant (pyserial does the same). <asm/termbits.h> redefines what
// <termios.h> and <sys/ioctl.h> define, so neither is included here.
#include <asm/ioctls.h>
#include <asm/termbits.h>
#include <linux/gpio.h>
extern "C" int ioctl(int fd, unsigned long request, ...);
#elif defined(__APPLE__)
#include <IOKit/serial/ioss.h>
#include <sys/ioctl.h>
#include <termios.h>
#endif

namespace arb::host {

int keys_open_port(const char* path, std::string* why) {
    const int fd = ::open(path, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        if (why) *why = std::string(path) + ": " + std::strerror(errno);
        return -1;
    }
#if defined(__linux__)
    struct termios2 t {};
    if (ioctl(fd, TCGETS2, &t) != 0) {
        if (why) *why = std::string(path) + ": not a tty: " + std::strerror(errno);
        ::close(fd);
        return -1;
    }
    // cfmakeraw, by hand on the termios2 fields.
    t.c_iflag &= ~tcflag_t(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON | IXOFF | IXANY);
    t.c_oflag &= ~tcflag_t(OPOST);
    t.c_lflag &= ~tcflag_t(ECHO | ECHONL | ICANON | ISIG | IEXTEN);
    t.c_cflag &= ~tcflag_t(CSIZE | PARENB | CSTOPB | CRTSCTS | CBAUD | (CBAUD << IBSHIFT));
    t.c_cflag |= tcflag_t(CS8 | CLOCAL | CREAD | BOTHER | (BOTHER << IBSHIFT));
    t.c_ispeed = t.c_ospeed = kKeysBaud;
    t.c_cc[VMIN] = 0;
    t.c_cc[VTIME] = 0;
    if (ioctl(fd, TCSETS2, &t) != 0 && why)
        *why = std::string("the port refused 250000 baud: ") + std::strerror(errno);
    ioctl(fd, TCFLSH, TCIFLUSH);   // nothing that arrived before the open is parsed
#elif defined(__APPLE__)
    struct termios t {};
    if (tcgetattr(fd, &t) != 0) {
        if (why) *why = std::string(path) + ": not a tty: " + std::strerror(errno);
        ::close(fd);
        return -1;
    }
    cfmakeraw(&t);
    t.c_cflag &= ~tcflag_t(PARENB | CSTOPB | CRTSCTS);
    t.c_cflag |= tcflag_t(CS8 | CLOCAL | CREAD);
    t.c_cc[VMIN] = 0;
    t.c_cc[VTIME] = 0;
    tcsetattr(fd, TCSANOW, &t);
    speed_t speed = kKeysBaud;
    if (ioctl(fd, IOSSIOSPEED, &speed) != 0 && why)
        *why = std::string("the port refused 250000 baud: ") + std::strerror(errno);
    tcflush(fd, TCIFLUSH);
#endif
    return fd;
}

int keys_open_reset_line(std::string* why) {
#if defined(__linux__)
    std::string last = "no /dev/gpiochip0 or /dev/gpiochip4";
    for (const char* chip : {"/dev/gpiochip0", "/dev/gpiochip4"}) {
        const int cfd = ::open(chip, O_RDWR | O_CLOEXEC);
        if (cfd < 0) continue;
        gpio_v2_line_request req{};
        req.offsets[0] = kResetGpio;
        req.num_lines = 1;
        std::strncpy(req.consumer, "arbutus-audio", sizeof req.consumer - 1);
        req.config.flags = GPIO_V2_LINE_FLAG_OUTPUT;
        req.config.num_attrs = 1;
        req.config.attrs[0].attr.id = GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES;
        req.config.attrs[0].attr.values = 1;       // idle high: RESET# released
        req.config.attrs[0].mask = 1;
        const int r = ioctl(cfd, GPIO_V2_GET_LINE_IOCTL, &req);
        const int e = errno;
        ::close(cfd);
        if (r == 0 && req.fd >= 0) return req.fd;
        last = std::string(chip) + ": " + std::strerror(e);
    }
    if (why) *why = last;
    return -1;
#else
    if (why) *why = "no GPIO character device on this platform";
    return -1;
#endif
}

bool keys_set_reset_line(int fd, bool high) {
#if defined(__linux__)
    if (fd < 0) return false;
    gpio_v2_line_values v{};
    v.bits = high ? 1 : 0;
    v.mask = 1;
    return ioctl(fd, GPIO_V2_LINE_SET_VALUES_IOCTL, &v) == 0;
#else
    (void)fd;
    (void)high;
    return false;
#endif
}

}  // namespace arb::host
