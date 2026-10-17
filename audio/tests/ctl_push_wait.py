#!/usr/bin/env python3
"""arbutus_ctl's waits for a push outlast the host's hold of it (#536, #538),
and keep their stated time when a line arrives in pieces (#732).

The host holds a state push until requests settle, up to 50 ms (PROTOCOL.md § State),
and later still on a loaded machine. A fake host on a Unix socket replies to each
request and pushes `state` HOLD seconds later. drain(s) used to stop at the first
50 ms of quiet and miss it; now it waits its whole `s` and has it, and wait_push()
returns it as soon as it lands. No arbutus-audio needed."""

import json
import os
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "arbutus-ctl"))
from arbutus_ctl import Conn, ProtocolError  # noqa: E402

HOLD = 0.12     # past the old drain()'s 50 ms of quiet, well inside wait_push()'s 5 s


def fake_host(server):
    peer, _ = server.accept()
    with peer:
        buf = b""
        while True:
            while b"\n" not in buf:
                chunk = peer.recv(4096)
                if not chunk:
                    return
                buf += chunk
            line, buf = buf.split(b"\n", 1)
            req = json.loads(line)["req"]
            peer.sendall(json.dumps({"type": "ok", "req": req}).encode() + b"\n")
            time.sleep(HOLD)
            peer.sendall(json.dumps({"type": "state", "tempo": req}).encode() + b"\n")


def with_host(check):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        t = threading.Thread(target=fake_host, args=(server,), daemon=True)
        t.start()
        c = Conn(path, timeout=5.0)
        try:
            check(c)
        finally:
            c.close()
            server.close()


def drain_waits_its_whole_time(c):
    # The #538 bug: drain(0.3) gave up after 50 ms of quiet and missed this push.
    c.clear_pushes()
    c.request({"type": "tempo", "bpm": 90})
    t0 = time.monotonic()
    c.drain(0.3)
    dt = time.monotonic() - t0
    assert c.pushes("state"), "drain(0.3) missed a push held %d ms" % (HOLD * 1e3)
    assert dt >= 0.29, "drain(0.3) returned after %.0f ms" % (dt * 1e3)
    print("ok: drain(0.3) waits %.0f ms and collects a push held %d ms"
          % (dt * 1e3, HOLD * 1e3))


def wait_push_gets_it(c):
    c.clear_pushes()
    r = c.request({"type": "tempo", "bpm": 91})
    t0 = time.monotonic()
    got = c.wait_push("state")
    dt = time.monotonic() - t0
    assert got and got["tempo"] == r["req"], (got, r)
    assert dt < 2.0, "wait_push slept %.2f s past the push" % dt
    print("ok: wait_push() returns the push held %d ms, %.0f ms after the reply"
          % (HOLD * 1e3, dt * 1e3))


def wait_push_where_skips_an_earlier_push(c):
    c.clear_pushes()
    c.request({"type": "tempo", "bpm": 92})
    r = c.request({"type": "tempo", "bpm": 93})
    got = c.wait_push("state", 2.0, where=lambda p: p["tempo"] == r["req"])
    assert got and got["tempo"] == r["req"], (got, r, c.pushes())
    assert c.pushes("state")[0]["tempo"] != r["req"], c.pushes()   # the one it skipped
    print("ok: wait_push(where=) passes over the earlier push for the one asked for")


def wait_push_gives_none_when_nothing_comes(c):
    c.clear_pushes()
    t0 = time.monotonic()
    assert c.wait_push("xrun", 0.2) is None
    assert time.monotonic() - t0 < 1.0
    print("ok: wait_push() gives None at its timeout")


# -- #732: a push that arrives in fragments --------------------------------------
# The socket's timeout bounds one read, not the wait: each fragment restarted it, so a
# line whose pieces came closer together than the timeout held wait_push() and drain()
# until its last piece (Codex: a 0.1 s wait returned after 0.30 s). FRAGS pieces GAP s
# apart make one push that lands FRAGS*GAP s after the go; every wait below asks for
# CEIL s and must be back well before the line is whole.
# The line is long so LATE can be loose: under the parallel gate at a load of ~120
# a 0.1 s wait came back after 253-324 ms, past the old 0.25 s bound (#973).
FRAGS = 40
GAP = 0.05      # under CEIL, so every read inside the line finds a piece in time
CEIL = 0.1
LINE = FRAGS * GAP  # 2.0 s: when the old code returned
LATE = LINE / 2     # back by here or the deadline was not kept
# The follow-up wait only proves the pieces read so far stayed buffered: it returns the
# moment the line is whole, so its bound is loose. Twice the line was not: in a background
# session on the Mac each 50 ms sleep above took ~190 ms and the line ~7.6 s (#973).
WHOLE = 30.0


def fragmenting_host(server, go):
    """On each `go`, one `state` push, sent in FRAGS pieces GAP s apart."""
    peer, _ = server.accept()
    with peer:
        n = 0
        while go.wait(10.0):
            go.clear()
            n += 1
            line = json.dumps({"type": "state", "seq": n, "pad": "x" * 400}).encode() + b"\n"
            step = -(-len(line) // FRAGS)       # ceil division: FRAGS pieces at most
            for i in range(0, len(line), step):
                peer.sendall(line[i:i + step])
                time.sleep(GAP)


def with_fragments(check):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "f.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        go = threading.Event()
        threading.Thread(target=fragmenting_host, args=(server, go), daemon=True).start()
        c = Conn(path, timeout=5.0)
        try:
            check(c, go)
        finally:
            c.close()
            server.close()


def wait_push_keeps_its_ceiling_on_fragments(c, go):
    c.clear_pushes()
    go.set()
    t0 = time.monotonic()
    got = c.wait_push("state", CEIL)
    dt = time.monotonic() - t0
    assert dt < LATE, "wait_push(%.1f) returned after %.0f ms" % (CEIL, dt * 1e3)
    assert got is None, "the line was whole before its last piece? %r" % (got,)
    # The pieces read so far stay buffered: a later wait has the whole line.
    got = c.wait_push("state", WHOLE)
    assert got and got["seq"] == 1, (got, c.pushes())
    assert c._sock.gettimeout() == c.timeout, c._sock.gettimeout()
    print("ok: wait_push(%.1f) gives None after %.0f ms on a line in %d pieces %d ms "
          "apart; the next wait has it" % (CEIL, dt * 1e3, FRAGS, GAP * 1e3))


def drain_keeps_its_window_on_fragments(c, go):
    c.clear_pushes()
    go.set()
    t0 = time.monotonic()
    c.drain(CEIL)
    dt = time.monotonic() - t0
    assert dt < LATE, "drain(%.1f) returned after %.0f ms" % (CEIL, dt * 1e3)
    assert not c.pushes("state"), c.pushes()
    assert c.wait_push("state", WHOLE, where=lambda p: p["seq"] == 2), c.pushes()
    assert c._sock.gettimeout() == c.timeout, c._sock.gettimeout()
    print("ok: drain(%.1f) stops after %.0f ms on a line in %d pieces; the next wait "
          "has it" % (CEIL, dt * 1e3, FRAGS))


def request_keeps_its_timeout_on_fragments():
    # request() waits the connection timeout for its reply; a push in pieces must
    # not stretch that either. Here the timeout is CEIL and no reply ever comes.
    def check(c, go):
        c.timeout = CEIL
        c._sock.settimeout(CEIL)
        go.set()
        t0 = time.monotonic()
        try:
            c.request({"type": "tempo", "bpm": 94})
            raise AssertionError("a reply nobody sent")
        except (ProtocolError, socket.timeout, TimeoutError):
            pass
        dt = time.monotonic() - t0
        assert dt < LATE, "request() with a %.1f s timeout gave up after %.0f ms" % (
            CEIL, dt * 1e3)
        print("ok: request() gives up at its %.1f s timeout (%.0f ms) on a push in "
              "pieces" % (CEIL, dt * 1e3))
    with_fragments(check)


def main():
    with_host(lambda c: (drain_waits_its_whole_time(c), wait_push_gets_it(c),
                         wait_push_where_skips_an_earlier_push(c),
                         wait_push_gives_none_when_nothing_comes(c)))
    with_fragments(lambda c, go: (wait_push_keeps_its_ceiling_on_fragments(c, go),
                                  drain_keeps_its_window_on_fragments(c, go)))
    request_keeps_its_timeout_on_fragments()
    return 0


if __name__ == "__main__":
    sys.exit(main())
