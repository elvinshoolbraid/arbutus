// keys_port.hpp — the ATmega's serial port and its RESET# line, for the key
// thread (#638, device-os-spec.md § 1.3.4 part 1). Its own file because the
// Linux way to set 250 000 baud (`termios2`, <asm/termbits.h>) cannot share a
// translation unit with <termios.h>.
#pragma once

#include <string>

namespace arb::host {

inline constexpr int kKeysBaud = 250000;   // the P3 firmware's UBRR = 3 (#489)
inline constexpr int kResetGpio = 22;      // io-map § P3: GPIO22 -> ATmega RESET#

// open(O_RDWR | O_NOCTTY | O_NONBLOCK), raw 8N1, no flow control, CLOCAL |
// CREAD, 250 000 baud, input flushed. -1 and `why` on failure. A speed the
// driver refuses is not a failure (a pty ignores speed): `why` says so and
// the handshake decides.
int keys_open_port(const char* path, std::string* why);

// The RESET# line through the GPIO character device (uAPI v2), an output
// idling high, consumer `arbutus-audio`, trying /dev/gpiochip0 then
// /dev/gpiochip4. -1 and `why` when there is none (a pty, the bench, macOS):
// the handshake then runs PING-only.
int keys_open_reset_line(std::string* why);
bool keys_set_reset_line(int fd, bool high);

}  // namespace arb::host
