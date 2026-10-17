#!/usr/bin/env python3
"""D2 (#23): the control protocol over TCP, behind the pairing token.

`device-os-spec.md` § 9.4 is the authority. The message set is unchanged --
that is the whole point, because the companion app and the instrument's own UI
then speak one protocol -- so what this pins is the *transport*:

  * the listener is bound **only when a token exists**. An instrument nobody
    has paired with has no open port at all, rather than one that answers and
    refuses; that is what makes `PAIR` mean something.
  * a TCP connection says `hello` with the token before anything else. Any
    other message first, or a wrong token, is `unauthorized`; three refusals
    and the connection goes.
  * an unauthenticated peer is told nothing: the pushed `state` document is
    the whole instrument, so it waits behind the token like everything else.
  * `shutdown` and `render` are the local instrument's. A phone that could
    stop the audio process, or drive the offline renderer, would be a phone
    that can silence the instrument mid-set.
  * `meters` is capped at 10 Hz over the network (§ 9.4), whatever was asked.
  * the Unix socket is unchanged: it needs no token and keeps `shutdown`.

The port is chosen by binding one and letting go, which is the ordinary way to
ask the kernel for a free port from a test.
"""

import os
import shutil
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402
from arbutus_ctl import ProtocolError, connect_tcp                 # noqa: E402

TOKEN = "0123456789abcdef0123456789abcdef"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def token_file(path, value=TOKEN):
    with open(path, "w") as f:
        f.write(value + "\n")          # the trailing newline is not the token
    return path


def test_no_token_no_listener(a):
    """§ 9.4: bound only when `token` exists."""
    port = free_port()
    with Host(a.exe, [a.boxes], block=128,
              extra=["--tcp-port", str(port)]) as h:
        assert h.ok({"type": "state"})["type"] == "state", "the unix socket died"
        try:
            connect_tcp("127.0.0.1", port, timeout=1.0, retries=3)
            raise AssertionError("TCP was bound with no token")
        except ProtocolError:
            pass
    ok("with no --token-file the TCP port is never bound: an unpaired "
       "instrument has no network surface at all")
    return 0


def test_token_gates_the_connection(a):
    port = free_port()
    d = tempfile.mkdtemp(prefix="arbutus-token-")
    try:
        return _token_gates(a, port, d)
    finally:
        shutil.rmtree(d, ignore_errors=True)      # #105: /tmp is RAM on the CM4


def _token_gates(a, port, d):
    tok = token_file(os.path.join(d, "token"))
    with Host(a.exe, [a.boxes], block=128,
              extra=["--tcp-port", str(port), "--token-file", tok]) as h:
        # -- a message before `hello` ------------------------------------
        c = connect_tcp("127.0.0.1", port)
        r = c.request({"type": "state"})
        assert r["type"] == "error" and r["code"] == "unauthorized", r
        assert r["for"] == "state", r
        ok("a TCP peer that sends anything before `hello {token}` is "
           "refused `unauthorized`, and the refusal names the message")

        # -- and it is told nothing else ---------------------------------
        # An edit on the UI's socket makes a push to go round; the UI gets it
        # (the control), and the TCP peer has the whole 0.3 s after that to
        # not get it (#538: drain() used to stop after 50 ms of quiet).
        h.c.clear_pushes()
        h.ok({"type": "tempo", "bpm": 131})
        assert h.c.wait_push("state"), "the control: the UI got no push either"
        c.drain(0.3)
        assert not c.pushes("state"), c.pushes()
        ok("nothing is pushed to an unauthenticated peer — `state` is the "
           "whole instrument and it waits behind the token")

        # -- three refusals and the connection goes ----------------------
        r = c.request({"type": "hello", "proto": 1, "abi": 2, "token": "wrong"})
        assert r["code"] == "unauthorized", r
        try:
            c.request({"type": "hello", "proto": 1, "abi": 2, "token": "wrong"})
            c.recv()
            raise AssertionError("the connection survived three refusals")
        except ProtocolError:
            pass
        c.close()
        ok("three refusals close the connection — a wrong token is a stale "
           "phone or a guess, and both should have to reconnect")

        # -- the right token, with the file's newline not part of it -----
        c = connect_tcp("127.0.0.1", port)
        r = c.hello(token=TOKEN)
        assert r["type"] == "hello" and r["proto"] == 1, r
        assert h.ok({"type": "state"})["type"] == "state"
        ok("`hello` with the token is the handshake, and the token file's "
           "trailing newline is not part of the token")

        # -- now it is an ordinary client --------------------------------
        st = c.ok({"type": "state"})
        assert st["type"] == "state", st
        ok("after the handshake it is the same protocol as the Unix socket — "
           "the app and the instrument's own UI speak one message set")

        # -- except for the three transport rules ------------------------
        for t in ("shutdown", "render"):
            r = c.request({"type": t, "frames": 128})
            assert r["type"] == "error" and r["code"] == "bad_request", (t, r)
            assert "local only" in r["message"], r
        ok("`shutdown` and `render` are refused over TCP: a phone cannot "
           "stop the audio process or drive the offline renderer")

        # The cap is a clamp, not a refusal: the app asks for what it wants
        # and reads back what it got. The reply's `hz` is the accepted rate,
        # which is the only place the app could learn the cap -- the pushes
        # themselves are paced by `render` under `--offline`.
        assert c.ok({"type": "meters", "on": True, "hz": 30})["hz"] == 10, \
            "the 10 Hz cap did not bite over TCP"
        assert h.ok({"type": "meters", "on": True, "hz": 30})["hz"] == 30, \
            "the cap leaked onto the Unix socket"
        ok("`meters` at 30 Hz is clamped to § 9.4's 10 Hz over TCP and left "
           "alone on the Unix socket; the reply carries the accepted rate")

        # The local socket still has `shutdown`: `Host.close()` uses it, and
        # a rule that leaked onto the Unix socket would wedge every test.
        c.close()
    ok("the Unix socket is untouched: no token, and `shutdown` still ends "
       "the process")
    return 0


def test_pushes_reach_an_authenticated_peer(a):
    port = free_port()
    d = tempfile.mkdtemp(prefix="arbutus-token-")
    try:
        return _pushes_reach(a, port, d)
    finally:
        shutil.rmtree(d, ignore_errors=True)      # #105


def _pushes_reach(a, port, d):
    tok = token_file(os.path.join(d, "token"))
    with Host(a.exe, [a.boxes], block=128,
              extra=["--tcp-port", str(port), "--token-file", tok]) as h:
        c = connect_tcp("127.0.0.1", port)
        c.hello(token=TOKEN)
        c.clear_pushes()
        h.ok({"type": "tempo", "bpm": 132})      # a state change on the UI's socket
        assert c.wait_push("state"), "no state reached the TCP client"   # (#538)
        states = c.pushes("state")
        assert abs(states[-1]["tempo"] - 132) < 0.01, states[-1]["tempo"]
        ok("pushes reach a paired phone exactly as they reach the UI — the "
           "app's status mirror is the same document the instrument draws")
    return 0


def test_appearance_reaches_the_ui(a):
    """#334: the phone's light or dark reaches the instrument's own UI.

    PROTOCOL.md § The phone's appearance. The phone sends it over TCP; the
    host holds it and pushes it to **every** client on a change -- the UI is
    not a `feed` subscriber, so the D17 rule would never reach it.
    """
    port = free_port()
    d = tempfile.mkdtemp(prefix="arbutus-token-")
    try:
        return _appearance(a, port, d)
    finally:
        shutil.rmtree(d, ignore_errors=True)      # #105


def _appearance(a, port, d):
    tok = token_file(os.path.join(d, "token"))
    with Host(a.exe, [a.boxes], block=128,
              extra=["--tcp-port", str(port), "--token-file", tok]) as h:
        r = h.req({"type": "appearance"})
        assert r["type"] == "appearance" and r["mode"] is None, r
        ok("before any phone has said, `appearance` answers null")

        phone = connect_tcp("127.0.0.1", port)
        phone.hello(token=TOKEN)
        h.c.clear_pushes()
        r = phone.request({"type": "appearance", "mode": "dark"})
        assert r["type"] == "appearance" and r["mode"] == "dark", r
        h.c.wait_push("appearance")          # (#538)
        h.c.state()                          # a second push would be in before this reply
        got = h.c.pushes("appearance")
        assert [p["mode"] for p in got] == ["dark"], got
        ok("a paired phone's `appearance dark` is pushed to the UI's socket")

        h.c.clear_pushes()
        phone.request({"type": "appearance", "mode": "dark"})
        h.c.drain(0.3)                       # all of it: the claim is "none" (#538)
        assert not h.c.pushes("appearance"), h.c.pushes("appearance")
        ok("the same mode again is answered and not pushed")

        assert h.req({"type": "appearance"})["mode"] == "dark"
        ok("a UI that connects later can ask for the mode the phone last sent")

        for bad in ("sepia", 1, None):
            r = phone.request({"type": "appearance", "mode": bad})
            assert r["type"] == "error" and r["code"] == "bad_request", (bad, r)
        assert h.req({"type": "appearance"})["mode"] == "dark"
        ok("a mode that is not light or dark is refused and changes nothing")

        # The control: a set to light *is* pushed, so the silence above was
        # the unchanged value and not a push path that never fires.
        h.c.clear_pushes()
        phone.request({"type": "appearance", "mode": "light"})
        h.c.wait_push("appearance")          # (#538)
        h.c.state()
        assert [p["mode"] for p in h.c.pushes("appearance")] == ["light"]
        ok("a change back to light is pushed")
        phone.close()
    return 0


def main():
    a = args()
    for t in (test_no_token_no_listener, test_token_gates_the_connection,
              test_pushes_reach_an_authenticated_peer, test_appearance_reaches_the_ui):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
