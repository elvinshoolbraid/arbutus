#!/usr/bin/env python3
"""#788: #766's three screen-replacement races, tried against the audio host's
key thread (`--keys`, #638) through its own `keys.sock`. In `arbutus-keys`
(keys_service.py) a departing UI's leftovers could act on its replacement:
its cleanup releasing the new UI's notes (row 1), input parsed for it reaching
the new UI (row 2), its command for the ATmega sent after the new UI came
(row 3, 3b). The host's key thread reads the port, accepts, reads the UI and
drops it on one thread (`KeysThread::run`), so none has a window. Rows 1 and 2
check the state a new UI is admitted with (sequential, they are smoke checks);
row 3b is the one that pins an ordering, the accept before the old UI's read.

The rig is host_keys.py's: a pty plays the ATmega, sockets play the UIs.
"""

import sys
import time

from host_keys import FakeAtmega, Ui, log_has, tail_peak
from hostcase import Host, args, ok


def held_line(h, text):
    """The number of notes the drop line `text` (`keys: <why> — N held
    note(s) released`) released, its newest occurrence."""
    with open(h.path("host.log")) as f:
        lines = [ln for ln in f.read().splitlines() if text in ln]
    assert lines, "no %r in the host's log" % text
    return int(lines[-1].split("— ")[1].split(" ")[0])


def connect(h, sock, snaps):
    """A UI that has had its greeting and whose `snap` (sound on) the host
    has read: the `snaps`-th first snapshot in the log."""
    ui = Ui(sock)
    assert ui.read()["t"] == "status"
    ui.snap(sound=True)
    log_has(h, "keys: the UI's first snapshot (sound on)", times=snaps)
    return ui


def main():
    a = args()
    atm = FakeAtmega()
    with Host(a.exe, [a.boxes], block=256, extra=["--keys", atm.path]) as h:
        sock = h.path("keys.sock")
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        atm.wait_for(b"B\x04L\xffSE")

        # Row 1: A holds a note and goes; B comes at once and plays. A's
        # cleanup releases A's note, never B's: B's release still finds its
        # note held (`s`), and B leaves nothing held.
        A = connect(h, sock, 1)
        atm.say("KP:03:00")                       # NOTE_0, sounded for A
        m = A.read()
        assert (m["r"], m["c"], m["s"]) == (3, 0, True), m
        A.close()
        # A's note is let go in the audio, not only counted in the log.
        log_has(h, "keys: the UI went away — 1 held note released")
        peak = tail_peak(h, "row1.wav")
        assert peak == 0.0, "A's note still sounds after A went: %r" % peak
        B = connect(h, sock, 2)
        atm.say("KP:03:01")
        m = B.read()
        assert (m["r"], m["c"], m["p"], m["s"]) == (3, 1, True, True), m
        h.render(256)
        atm.say("KR:03:01")
        m = B.read()
        assert (m["r"], m["c"], m["p"], m["s"]) == (3, 1, False, True), \
            "B's note was released under it: %r" % m
        drops = [ln for ln in open(h.path("host.log")).read().splitlines()
                 if "released" in ln]
        assert len(drops) == 1 and "— 1 held note released" in drops[0], drops
        ok("row 1: a departed UI's cleanup releases its own note, not its replacement's")

        # Row 2: input parsed while A was the UI never reaches B. A's press
        # and turn went to A; B comes (A still open: the newest wins); A's
        # key's release is not B's, and B's first event is its own turn.
        A, B = B, None
        atm.say("KP:03:02")
        atm.say("EN:00:+3")
        m = A.read()
        assert (m["r"], m["c"], m["p"]) == (3, 2, True), m
        assert A.read()["t"] == "enc"
        B = connect(h, sock, 3)
        assert held_line(h, "a new UI connected") == 1   # NOTE_2, A's
        atm.say("KR:03:02")
        atm.say("EN:01:-1")
        m = B.read()
        assert m["t"] == "enc" and (m["i"], m["d"]) == (1, -1), \
            "the replacement inherited the departed UI's input: %r" % m
        A.close()
        ok("row 2: input parsed for a departed UI never reaches the next")

        # Row 3: A's command for the ATmega, half written when B comes, is
        # never sent: A's half line does not join B's first. `H` would halt
        # the keyboard's scan; B's first backlight is the line it would ride.
        A, B = B, None
        # One write: a whole backlight line, then the half line. The
        # backlight reaching the ATmega says the host has read both.
        A.s.sendall(b'{"t":"send","hex":"4c22"}\n{"t":"send","hex":"48"')
        atm.wait_for(b"L\x22")
        before = len(atm.sent())
        B = Ui(sock)                              # B's first line is a command
        assert B.read()["t"] == "status"
        try:
            A.s.sendall(b"}\n")                   # its socket is closed now
        except OSError:
            pass
        B.send({"t": "send", "hex": "4c33"})
        B.send({"t": "send", "hex": "4c44"})      # the second says the first was read
        atm.wait_for(b"L\x44")
        assert b"H" not in atm.sent()[before:], atm.sent()[before:]
        A.close()
        ok("row 3: a departed UI's command never reaches the ATmega")

        # Row 3b: a *whole* command A queued and the thread never read. The
        # thread is held in a failing re-init handshake (3 missed PONGs, then
        # ~1.8 s inline), A writes `send H`, B connects behind it, and both
        # wait for the next poll. Its accept runs before A's read and drops A
        # unread (`KeysThread::run`): swap the two and the `H` that halts the
        # keyboard's scan reaches the ATmega.
        A, B = B, None
        atm.answering = False
        log_has(h, "3 missed PONGs", timeout=15)
        before = len(atm.sent())
        A.send({"t": "send", "hex": "48"})
        B = Ui(sock)                              # waits in the backlog
        assert B.read()["t"] == "status"          # the handshake gave up
        atm.answering = True
        B.send({"t": "send", "hex": "4c55"})
        atm.wait_for(b"L\x55", timeout=10)
        assert b"H" not in atm.sent()[before:], atm.sent()[before:]
        A.close()
        ok("row 3b: a whole command a departed UI queued never reaches the ATmega")

        B.close()
        log_has(h, "keys: the UI went away — 0 held notes released")
        h.close()
    atm.unplug()
    return 0


if __name__ == "__main__":
    sys.exit(main())
