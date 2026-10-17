#!/usr/bin/env python3
"""#596: no message a client can send takes the audio process down, or holds
its memory.

PROTOCOL.md: every client message gets exactly one reply. Two ways that was
not true, both found by the m-audio-host-core review:

  * valid JSON with a field of the wrong type (`{"type":"hello","proto":"1"}`)
    made the JSON library throw from inside `handle()`, and `main.cpp` caught
    only parse errors, so the host aborted -- the instrument's sound with it;
  * a peer that never sends a newline grew its input buffer without bound, and
    on TCP that was before the token check: anyone on the LAN.

Pinned here: a wrong-typed field in each message family is one `error` reply
and the connection and the host carry on; a line past the cap is one
`MESSAGE TOO LONG` and a close, and the cap on a TCP peer that has not passed
the token check is small.
"""

import json
import os
import shutil
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402

TOKEN = "0123456789abcdef0123456789abcdef"
MAX_LINE = 8 << 20            # main.cpp kMaxLine
MAX_LINE_UNAUTHED = 64 << 10  # main.cpp kMaxLineUnauthed

# One per message family, each with a field the host reads as a number, a
# string or an object given as something else. The hello is the review's.
WRONG_TYPES = [
    {"type": "hello", "proto": "1", "abi": 2},
    {"type": "hello", "proto": 1, "abi": [2]},
    {"type": "tempo", "bpm": "fast"},
    {"type": "set_param", "slot": "engine", "idx": "one", "value": 0.5},
    {"type": "set_param", "slot": "engine", "idx": 1, "value": "loud"},
    {"type": "note", "note": "C4", "on": True},
    {"type": "mixer", "track": "one", "gain": 0.5},
    {"type": "mixer", "track": 1, "gain": {"db": 0}},
    {"type": "transport", "op": 7},
    {"type": "load_engine", "slot": "engine", "id": 5},
    {"type": "set_clips", "track": 1, "clips": "none"},
    {"type": "set_clips", "track": 1, "clips": [{"start_frame": "0", "len": 10}]},
    {"type": "clip_peaks", "track": 1, "start": "0", "frames": 100},
    {"type": "env", "a": "slow"},
    {"type": "meters", "on": True, "hz": "fast"},
    {"type": "bind", "slot": "zero", "instance": "lp", "param": "q"},
    {"type": "route", "source": 3, "target": None},
]


def read_to_eof(s, timeout=5.0):
    """Every line the host sends before it closes, parsed."""
    s.settimeout(timeout)
    buf = b""
    while True:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            raise AssertionError("the host never closed the connection; got %r" % buf[:400])
        if not chunk:
            break
        buf += chunk
    return [json.loads(l) for l in buf.split(b"\n") if l.strip()]


def send_unterminated(s, n):
    """n bytes of a JSON string that never ends, and no newline. Exactly n:
    the host crosses its cap on the last byte, so nothing is left unread in
    its socket when it closes (an unread byte would make the close a reset,
    which can throw away the reply before this side reads it)."""
    s.sendall(b'{"type":"hello","pad":"' + b"x" * (n - 23))


def test_wrong_types_are_refused(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        caught = 0
        for m in WRONG_TYPES:
            # One reply, whatever it is: some readers already fall back to a
            # default on a wrong type (`tempo {bpm:"fast"}` is ok); the rest
            # threw, and before #596 that was the end of the process.
            r = h.req(dict(m))
            assert r.get("req") is not None, (m, r)
            caught += r.get("message") == "BAD MESSAGE"
            # The connection and the host are both still there.
            assert h.c.state()["type"] == "state", m
        assert caught >= 2, caught
        ok("%d wrong-typed messages, one per family, each answered once and the "
           "host still answering (%d reached the new boundary)" % (len(WRONG_TYPES), caught))

        r = h.req({"type": "hello", "proto": "1", "abi": 2})
        assert r["code"] == "bad_request" and r["for"] == "hello", r
        ok("the review's case, hello {proto:\"1\"}, is bad_request "
           "(it aborted the host before #596): %s" % r.get("detail", "")[:60])

        r = h.req({"type": "hello", "proto": 1, "abi": 2, "firmware": "arbutus-ctl"})
        assert r["type"] == "hello", r
        ok("a good hello after the refusals is answered")
    return 0


def test_a_line_without_end_is_capped(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(h.sock)
        send_unterminated(s, MAX_LINE + 1)
        got = read_to_eof(s, timeout=30.0)
        s.close()
        assert len(got) == 1 and got[0]["code"] == "bad_request" \
            and got[0]["message"] == "MESSAGE TOO LONG", got
        assert h.c.state()["type"] == "state"
        ok("a client past %d MiB with no newline is told MESSAGE TOO LONG and "
           "closed; the other clients carry on" % (MAX_LINE >> 20))

        # The control: one byte under the cap is still a line in the making.
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(h.sock)
        send_unterminated(s, MAX_LINE)
        s.sendall(b'"}\n')
        got = []
        f = s.makefile("rb")
        got.append(json.loads(f.readline()))
        s.close()
        assert got[0]["type"] == "error" and got[0]["code"] == "needs_firmware_update", got
        ok("the control: a line just under the cap, ended, is read as a message")
    return 0


def test_an_unpaired_peer_gets_a_small_cap(a):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    d = tempfile.mkdtemp(prefix="arbutus-token-")
    try:
        tok = os.path.join(d, "token")
        with open(tok, "w") as f:
            f.write(TOKEN + "\n")
        with Host(a.exe, [a.boxes], block=128,
                  extra=["--tcp-port", str(port), "--token-file", tok]) as h:
            # connect() retries until the host is up; the unix client is.
            s = socket.create_connection(("127.0.0.1", port), timeout=5.0)
            send_unterminated(s, MAX_LINE_UNAUTHED + 1)
            got = read_to_eof(s)
            s.close()
            assert len(got) == 1 and got[0]["message"] == "MESSAGE TOO LONG", got
            assert h.c.state()["type"] == "state"
            ok("a TCP peer that has not given the token is closed past %d KiB "
               "without a newline, not %d MiB" % (MAX_LINE_UNAUTHED >> 10, MAX_LINE >> 20))
    finally:
        shutil.rmtree(d, ignore_errors=True)      # #105: /tmp is RAM on the CM4
    return 0


def main():
    a = args()
    for t in (test_wrong_types_are_refused, test_a_line_without_end_is_capped,
              test_an_unpaired_peer_gets_a_small_cap):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
