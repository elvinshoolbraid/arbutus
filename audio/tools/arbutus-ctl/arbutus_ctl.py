"""arbutus_ctl — speak the arbutus-audio protocol (audio/host/PROTOCOL.md).

Standard library only, no venv, no install: stage 4's emulator shim imports
this module and so do the host's tests.

    from arbutus_ctl import connect
    c = connect()                      # $TMPDIR/arbutus-audio.sock
    c.hello()
    c.request({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
    c.request({"type": "note", "on": True, "note": 60, "vel": 0.9})
    c.request({"type": "render", "frames": 48000, "file": "/tmp/a.wav"})

`request` returns the reply to *that* message, matched on an auto-assigned
`req` field ("id" is taken -- engines, patches and kits have ids). Anything
else the host sends -- `state` after an edit, `xrun`, `meters` --
is buffered and available from `pushes()`, so a client never has to guess
whether the next line is its answer.
"""

import json
import os
import socket
import time

__all__ = ["Conn", "connect", "connect_tcp", "default_socket", "ProtocolError"]

PROTO = 1
ABI = 2


class ProtocolError(RuntimeError):
    """The host refused, or the socket died mid-conversation."""


def default_socket() -> str:
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), "arbutus-audio.sock")


class Conn:
    def __init__(self, path=None, timeout=20.0, address=None):
        """`path` is the Unix socket; `address` is `(host, port)` for TCP.

        The message set is the same on both (device-os-spec.md § 9.4) -- what
        differs is that a TCP `hello` has to carry the pairing token.
        """
        self.path = None if address else (path or default_socket())
        self.address = address
        self.timeout = timeout
        if address:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(address if address else self.path)
        except OSError:
            self._sock.close()          # connect() retries; do not leak one each
            raise
        self._buf = b""
        self._pushes = []
        self._next_id = 1

    # -- raw -------------------------------------------------------------
    def send(self, msg: dict) -> None:
        self._sock.sendall((json.dumps(msg) + "\n").encode())

    def recv(self, deadline=None) -> dict:
        """One message off the wire.

        With no `deadline`, each read blocks up to the socket's timeout. With one
        (a `time.monotonic()` value) the whole call does: the time left is worked
        out again before every read, and past it `socket.timeout` is raised. The
        socket's timeout bounds one read, not a line, so without this a line
        arriving in pieces closer together than the timeout held the caller until
        its last piece (#732). Either way a partial line stays in `_buf` for the
        next call: nothing read is lost."""
        try:
            while b"\n" not in self._buf:
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise socket.timeout("timed out")
                    self._sock.settimeout(left)
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise ProtocolError("arbutus-audio closed the connection")
                self._buf += chunk
        finally:
            if deadline is not None:
                self._sock.settimeout(self.timeout)
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line.decode())

    # -- request / response ----------------------------------------------
    def request(self, msg: dict, expect_ok=False) -> dict:
        msg = dict(msg)
        msg["req"] = self._next_id          # "id" is taken: engines have ids
        self._next_id += 1
        self.send(msg)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            m = self.recv(deadline)
            if m.get("req") == msg["req"]:
                if expect_ok and m.get("type") == "error":
                    raise ProtocolError("%s: %s" % (m.get("code"), m.get("message")))
                return m
            self._pushes.append(m)
        raise ProtocolError("no reply to %r" % msg.get("type"))

    def ok(self, msg: dict) -> dict:
        """request(), but a refusal is an exception."""
        return self.request(msg, expect_ok=True)

    def pushes(self, kind=None):
        """Everything the host pushed that was not a reply, oldest first."""
        if kind is None:
            return list(self._pushes)
        return [p for p in self._pushes if p.get("type") == kind]

    def drain(self, seconds=0.2):
        """Collect every push that arrives in the next `seconds`, then stop.

        It waits the whole time, quiet or not (#538): it used to stop at the first
        50 ms with nothing on the wire, so `drain(0.5)` read as a half-second wait
        and was one of 50 ms. A test that means "this push came" waits on it with
        wait_push(); one that means "no push came" drains its full stated time."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self._pushes.append(self.recv(end))
            except (socket.timeout, TimeoutError, BlockingIOError):
                break
        return self._pushes

    def wait_push(self, kind, timeout=5.0, where=None):
        """The first `kind` push since clear_pushes() (and, given `where`, the first
        for which where(push) is true), waiting up to `timeout` s for it.

        The host may hold a state push 50 ms by design (PROTOCOL.md § State, #74),
        longer on a loaded machine, so a test that means "the push for this edit"
        waits here (#536). None if none came. Returns as soon as it arrives: the
        timeout is a ceiling, not a sleep."""
        end = time.monotonic() + timeout
        while True:
            got = [p for p in self.pushes(kind) if where is None or where(p)]
            if got:
                return got[0]
            if time.monotonic() >= end:
                return None
            try:
                self._pushes.append(self.recv(end))
            except (socket.timeout, TimeoutError):
                return None

    def clear_pushes(self):
        self._pushes = []

    # -- conveniences -----------------------------------------------------
    def hello(self, proto=PROTO, abi=ABI, firmware="arbutus-ctl", token=None):
        m = {"type": "hello", "proto": proto, "abi": abi, "firmware": firmware}
        if token is not None:
            m["token"] = token          # required over TCP (device-os-spec § 9.4)
        return self.request(m)

    def state(self) -> dict:
        return self.ok({"type": "state"})

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# Both of the retry loops below keep the *message* of the last failure, never the
# exception object. Python clears an `except ... as e` name at the end of the
# block for a reason: an exception's __traceback__ refers to the frame that
# caught it, so a name that outlives the block is a reference cycle, and every
# frame below it -- including the caller's, with its `self` -- stays reachable
# until the cycle collector happens to run. That is #179: `del h` in a test did
# not remove hostcase.Host's temp directory on the CM4 (5 of 5) and did in WSL
# (0 of 5), for no reason but how many objects each run had allocated by then.
def connect(path=None, timeout=20.0, retries=100) -> Conn:
    """Connect, waiting for the socket to appear (the host may still be booting)."""
    path = path or default_socket()
    last = None
    for _ in range(retries):
        try:
            return Conn(path, timeout)
        except (FileNotFoundError, ConnectionRefusedError) as e:
            last = str(e)               # the message, not the exception (cycle)
            time.sleep(0.05)
    raise ProtocolError("no arbutus-audio on %s (%s)" % (path, last))


def connect_tcp(host, port, timeout=20.0, retries=100) -> Conn:
    """The control protocol over TCP -- the companion app's transport.

    `hello` still has to carry the token; this only opens the socket.
    """
    last = None
    for _ in range(retries):
        try:
            return Conn(timeout=timeout, address=(host, int(port)))
        except (ConnectionRefusedError, OSError) as e:
            last = str(e)               # the message, not the exception (cycle)
            time.sleep(0.05)
    raise ProtocolError("no arbutus-audio on %s:%s (%s)" % (host, port, last))
