#!/usr/bin/env python3
"""arbutus-ctl — one-shot client for arbutus-audio (audio/host/PROTOCOL.md).

    arbutus-ctl.py '{"type":"state"}'
    arbutus-ctl.py state
    arbutus-ctl.py note on=true note=60 vel=0.9
    arbutus-ctl.py --socket /tmp/a.sock load_engine slot=engine id=arbutus/example
    arbutus-ctl.py --follow meters on=true

A bare word is the message type; `key=value` pairs are its fields, parsed as
JSON when they parse and left as strings when they do not. `--follow` keeps
printing what the host pushes (state, xrun, meters) until interrupted.

Exit status is 1 when the host replies with `error`.
"""

import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arbutus_ctl import Conn, ProtocolError, connect, default_socket  # noqa: E402


def build_message(words):
    if not words:
        raise SystemExit("arbutus-ctl: nothing to send")
    first = words[0]
    if first.lstrip().startswith("{"):
        return json.loads(" ".join(words))
    msg = {"type": first}
    for w in words[1:]:
        if "=" not in w:
            raise SystemExit("arbutus-ctl: expected key=value, got %r" % w)
        k, v = w.split("=", 1)
        try:
            msg[k] = json.loads(v)
        except json.JSONDecodeError:
            msg[k] = v
    return msg


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--socket", default=os.environ.get("ARBUTUS_SOCKET", default_socket()))
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-hello", action="store_true", help="skip the handshake")
    ap.add_argument("--follow", action="store_true", help="keep printing pushed messages")
    ap.add_argument("--compact", action="store_true")
    ap.add_argument("words", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    msg = build_message([w for w in a.words if w != "--"])
    indent = None if a.compact else 2

    try:
        c = connect(a.socket, a.timeout, retries=1)
    except ProtocolError as e:
        print(e, file=sys.stderr)
        return 2

    with c:
        if not a.no_hello:
            h = c.hello()
            if h.get("type") == "error":
                print(json.dumps(h, indent=indent))
                return 1
        reply = c.request(msg)
        print(json.dumps(reply, indent=indent))
        for p in c.pushes():
            print(json.dumps(p, indent=indent))
        if a.follow:
            # A quiet host is not an end: each recv waits up to --timeout, and
            # a timeout (socket.timeout is TimeoutError since Python 3.10) means
            # nothing was pushed, so wait again. It used to escape as a
            # traceback the first time the host went quiet for 20 s (#626).
            try:
                while True:
                    try:
                        m = c.recv()
                    except (socket.timeout, TimeoutError):
                        continue
                    print(json.dumps(m, indent=indent), flush=True)
            except (KeyboardInterrupt, ProtocolError):
                pass
        return 1 if reply.get("type") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
