#!/usr/bin/env python3
"""`--keys` (#638, device-os-spec.md § 1.3.4): the host reads the ATmega's port
on a thread of its own, sounds a chromatic key on its own ring, and serves the
UI `keys.sock` with § 1.3.3's protocol. A pty plays the ATmega (it answers `P`
with `PONG` and says what it was sent), a Unix socket plays the UI, and the
host runs `--offline`, so a `render` is what drains the ring.

Linux and macOS have ptys; nothing here needs a board.
"""

import json
import os
import pty
import select
import socket
import sys
import threading
import time
import tty

from hostcase import Host, args, ok, read_wav


class FakeAtmega:
    """The master side of a pty: answers `P` while `answering`, keeps every
    byte the host sent in `got`."""

    def __init__(self):
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.answering = True
        self.got = bytearray()
        self.pings = 0
        self._lock = threading.Lock()
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        # select() with a timeout, never a bare blocking read: a close() of
        # an fd another thread is blocked reading does not release it (so the
        # host would not see the hangup) until that read returns.
        while not self._stop:
            try:
                if not select.select([self.master], [], [], 0.02)[0]:
                    continue
                data = os.read(self.master, 256)
            except (OSError, ValueError):
                return
            if not data:
                return
            with self._lock:
                self.got += data
                for b in data:
                    if b == ord("P"):
                        self.pings += 1
                        if self.answering and not self._stop:
                            try:
                                os.write(self.master, b"PONG\r\n")
                            except OSError:
                                return

    def say(self, line):
        os.write(self.master, line.encode() + b"\r\n")

    def sent(self):
        with self._lock:
            return bytes(self.got)

    def wait_for(self, what, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if what in self.sent():
                return
            time.sleep(0.01)
        raise AssertionError("the ATmega never got %r; it got %r" % (what, self.sent()))

    def unplug(self):
        self._stop = True
        self._t.join()
        os.close(self.master)
        os.close(self.slave)


class Ui:
    """keys.sock's client, as `drivers/keys_client.py` speaks it."""

    def __init__(self, path):
        self.s = None
        end = time.monotonic() + 5
        while self.s is None:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(path)
                self.s = s
            except OSError:
                s.close()
                if time.monotonic() > end:
                    raise
                time.sleep(0.02)
        self.s.settimeout(12)               # the watchdog needs ~8 s
        self.buf = b""

    def send(self, msg):
        self.s.sendall((json.dumps(msg, separators=(",", ":")) + "\n").encode())

    def snap(self, sound=True, transpose=0):
        self.send({"t": "snap", "sound": sound, "transpose": transpose, "velocity": 1.0})

    def read(self):
        while b"\n" not in self.buf:
            data = self.s.recv(4096)
            if not data:
                raise AssertionError("keys.sock closed")
            self.buf += data
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line)

    def until(self, pred, what):
        for _ in range(50):
            m = self.read()
            if pred(m):
                return m
        raise AssertionError("never got " + what)

    def close(self):
        self.s.close()


def log_has(h, text, timeout=5.0, times=1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with open(h.path("host.log")) as f:
            if f.read().count(text) >= times:
                return True
        time.sleep(0.02)
    with open(h.path("host.log")) as f:
        raise AssertionError("not in the host's log: %r\n%s" % (text, f.read()))


def tail_peak(h, name):
    """Render one second and return the peak of its last block: 0.0 once every
    note the host let go has rung out. A release is heard, not taken from the
    drop line, whose count is made before the release (t788 row 2)."""
    f = h.path(name)
    h.render(48000, file=f)
    _, ch = read_wav(f)
    return max(abs(x) for x in ch[0][-256:])


def main():
    a = args()
    atm = FakeAtmega()
    with Host(a.exe, [a.boxes], block=256, extra=["--keys", atm.path, "--key-probe"]) as h:
        keys_sock = h.path("keys.sock")
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})

        # PING first, then step 4 with the default backlight.
        atm.wait_for(b"B\x04L\xffSE")
        assert atm.sent().startswith(b"P"), atm.sent()
        ok("start: PING first, then B 4, L 255, S, E")

        ui = Ui(keys_sock)
        assert ui.read() == {"t": "status", "text": "KEYBOARD OK"}
        log_has(h, "keys: the UI connected")

        # No snapshot yet: forwarded, not sounded.
        atm.say("KP:03:00")
        m = ui.read()
        assert m["t"] == "key" and (m["r"], m["c"], m["p"], m["s"]) == (3, 0, True, False), m
        atm.say("KR:03:00")
        assert ui.read()["p"] is False
        ok("before the first snap: forwarded unsounded")

        # The first snap; then a press sounds on the next block, with its probe.
        ui.snap(sound=True)
        log_has(h, "keys: the UI's first snapshot (sound on)")
        h.render(256 * 2)
        h.c.clear_pushes()
        atm.say("KP:03:07")                       # NOTE_7, MIDI 60
        m = ui.read()
        assert m["s"] is True and isinstance(m["probe"], int), m
        f = h.path("k.wav")
        h.render(1024, file=f)
        p = h.c.wait_push("probe")
        assert p["id"] == m["probe"] and p["block"] == 2 and p["frame"] == 0, (p, m)
        assert p["t_ctl_ns"] <= p["t1_ns"], p
        from hostcase import read_wav
        _, ch = read_wav(f)
        first = next(i for i, v in enumerate(ch[0]) if v != 0.0)
        assert first < 4, first
        ok("a press: sounded at frame %d of block 2, probe %d reported" % (first, p["id"]))

        # A non-note key and a spare: forwarded / dropped, never sounded.
        atm.say("KP:00:02")                       # PLAY
        m = ui.read()
        assert (m["r"], m["c"], m["s"]) == (0, 2, False), m
        atm.say("KP:02:06")                       # a spare position: nothing
        atm.say("EN:01:-2")
        m = ui.read()
        assert m == {"t": "enc", "i": 1, "d": -2, "at": m["at"]}, m
        ok("PLAY forwarded unsounded, the spare dropped, a turn forwarded")

        # The UI's backlight goes to the ATmega and is remembered.
        ui.send({"t": "send", "hex": "4c40"})
        atm.wait_for(b"L\x40")

        # The UI goes: the held note is released.
        ui.close()
        log_has(h, "keys: the UI went away — 1 held note released")
        peak = tail_peak(h, "drop.wav")
        assert peak == 0.0, "the dropped UI's note still sounds: %r" % peak
        ok("a UI drop releases the held note")

        # A release whose press went to the old UI is not forwarded to the new.
        ui = Ui(keys_sock)
        assert ui.read()["t"] == "status"
        ui.snap(sound=True)
        # The port and the socket are two fds on one poll(): a press may be
        # read before the snap that lets it sound, as on the device (#578).
        log_has(h, "keys: the UI's first snapshot (sound on)", times=2)
        atm.say("KR:03:07")
        atm.say("KP:03:01")
        m = ui.read()
        assert (m["r"], m["c"], m["p"]) == (3, 1, True), m
        ok("the old UI's release is not the new UI's")

        # Three unanswered PINGs: the held key released, the reset handshake,
        # step 4 with the remembered backlight.
        atm.answering = False
        before = len(atm.sent())
        m = ui.until(lambda m: m.get("t") == "key" and m["p"] is False, "the watchdog's release")
        assert (m["r"], m["c"]) == (3, 1), m
        log_has(h, "3 missed PONGs")
        atm.answering = True
        atm.wait_for(b"B\x04L\x40SE", timeout=5)
        assert b"B\x04L\x40SE" in atm.sent()[before:], atm.sent()[before:]
        ok("watchdog: three missed PONGs, the key released, re-init with L 64")

        # The far end goes: NO KEYBOARD.
        t = time.monotonic()
        atm.unplug()
        m = ui.until(lambda m: m.get("t") == "status", "a status")
        assert m["text"] == "NO KEYBOARD", m
        # At once, not at the first 2 s reopen that fails.
        assert time.monotonic() - t < 1.0, time.monotonic() - t
        log_has(h, "reopening every 2 s")
        ok("the port closing says NO KEYBOARD and reopens")
        ui.close()
        h.close()
        log_has(h, "keys fast notes: 3 sounded")
    ok("the exit line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
