#!/usr/bin/env python3
"""#827: the key thread's three recoveries (device-os-spec.md § 1.3.4 parts
1–4 and 8), each found missing by t638's review:

- a full ring and a UI disconnect: every held note's off still reaches the
  audio thread once it drains, so the sound stops (row 1, a stuck note);
- a handshake that ran out of attempts, then an ATmega that answers: the
  watchdog's PONG configures it and says KEYBOARD OK, with no boot banner
  (row 2);
- the port gone and a new one at the same path: reopened, PING, configured,
  KEYBOARD OK and a key that sounds (row 3); a port absent at the start too.

The rig is host_keys.py's: a pty plays the ATmega, a socket plays the UI, the
host runs `--offline` so a `render` is what drains the ring. The port is a
symlink here, as `/dev/serial0` is on the device, so a test can repoint it.
"""

import os
import re
import sys
import time

from host_keys import FakeAtmega, Ui, log_has
from hostcase import Host, args, ok, read_wav, scratch

CONFIG = b"B\x04L\xffSE"       # § 6.2.3 step 4 with the default backlight


def point(link, target):
    """Repoint the symlink `link` at `target` in one step (rename is atomic)."""
    tmp = link + ".new"
    os.symlink(target, tmp)
    os.replace(tmp, link)


def full_ring_then_disconnect(a, tmp):
    atm = FakeAtmega()
    with Host(a.exe, [a.boxes], block=256, extra=["--keys", atm.path]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        atm.wait_for(CONFIG)
        ui = Ui(h.path("keys.sock"))
        assert ui.read() == {"t": "status", "text": "KEYBOARD OK"}
        ui.snap(sound=True)
        log_has(h, "keys: the UI's first snapshot (sound on)")
        atm.say("KP:03:00")
        assert ui.read()["s"] is True
        h.render(1024)
        # Nothing renders, so nothing drains the ring (255 places): it fills.
        # Read as it goes: 300 lines unread is a wedged UI, which drops it.
        for _ in range(15):
            for _ in range(20):
                atm.say("KP:03:01")
            for _ in range(20):
                m = ui.read()
                assert (m["r"], m["c"], m["p"]) == (3, 1, True), m
        ui.close()
        log_has(h, "keys: the UI went away")
        # One block drains the ring; the thread's retry (within ~2 ms) puts
        # the offs on it; the long render is the release, then silence.
        h.render(256)
        time.sleep(0.1)
        f = os.path.join(tmp, "after.wav")
        h.render(48000, file=f)
        _, ch = read_wav(f)
        tail = max(abs(v) for c in ch for v in c[-4800:])
        assert tail == 0.0, "still sounding a second after the disconnect: peak %g" % tail
    with open(h.path("host.log")) as f:
        full = re.search(r"(\d+) ring full", f.read())
    assert full and int(full.group(1)) > 0, "the ring never filled: the case tested nothing"
    ok("a full ring and a UI disconnect: the held notes stop (last 100 ms peak 0)")
    atm.unplug()


def pongs_after_a_failed_handshake(a):
    atm = FakeAtmega()
    atm.answering = False
    with Host(a.exe, [a.boxes], block=256, extra=["--keys", atm.path]) as h:
        ui = Ui(h.path("keys.sock"))
        m = ui.until(lambda m: m.get("t") == "status", "a status")
        assert m["text"] == "NO KEYBOARD", m
        log_has(h, "keys: no keyboard")
        atm.answering = True
        before = len(atm.sent())
        # The watchdog PINGs 2 s after the last line; three misses (6 s)
        # would re-init, so 5 s is the PONG's doing alone.
        m = ui.until(lambda m: m.get("t") == "status", "a second status")
        assert m["text"] == "KEYBOARD OK", m
        atm.wait_for(CONFIG, timeout=1)
        assert CONFIG in atm.sent()[before:], atm.sent()[before:]
        assert b"KEYBOARD_READY" not in atm.sent()
        log_has(h, "answered after a failed handshake")
        ui.close()
    ok("NO KEYBOARD, then PONGs: configured and KEYBOARD OK without a boot banner")
    atm.unplug()


def reopen(a, tmp):
    link = os.path.join(tmp, "serial0")
    os.symlink(os.path.join(tmp, "nothing-yet"), link)
    with Host(a.exe, [a.boxes], block=256, extra=["--keys", link]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        log_has(h, "cannot be opened")
        ui = Ui(h.path("keys.sock"))
        assert ui.read() == {"t": "status", "text": "NO KEYBOARD"}
        ui.snap(sound=True)
        log_has(h, "keys: the UI's first snapshot (sound on)")
        first = FakeAtmega()
        point(link, first.path)
        first.wait_for(CONFIG, timeout=5)
        assert first.sent().startswith(b"P"), first.sent()
        m = ui.until(lambda m: m.get("t") == "status", "a status")
        assert m["text"] == "KEYBOARD OK", m
        ok("a port absent at the start: opened when it appears, PING, configured")

        first.unplug()
        m = ui.until(lambda m: m.get("t") == "status", "a status")
        assert m["text"] == "NO KEYBOARD", m
        second = FakeAtmega()
        point(link, second.path)
        second.wait_for(CONFIG, timeout=5)
        assert second.sent().startswith(b"P"), second.sent()
        m = ui.until(lambda m: m.get("t") == "status", "a status")
        assert m["text"] == "KEYBOARD OK", m
        h.render(512)
        second.say("KP:03:07")
        m = ui.until(lambda m: m.get("t") == "key", "a key")
        assert (m["r"], m["c"], m["p"], m["s"]) == (3, 7, True, True), m
        ui.close()
        second.unplug()
    ok("the port closing and a new one at its path: reopened, configured, a key sounds")


def main():
    a = args()
    with scratch("arbutus-keys-recover-") as tmp:
        full_ring_then_disconnect(a, tmp)
        pongs_after_a_failed_handshake(a)
        reopen(a, tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
