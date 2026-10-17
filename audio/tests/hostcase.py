"""Shared scaffolding for the arbutus-audio tests: launch, talk, read WAVs.

Standard library only (there is no numpy on this Mac's python3 and the tests
must run from a bare checkout). Every test runs the host `--offline`, so none
of them needs an audio device.
"""

import array
import contextlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import weakref

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "arbutus-ctl"))

from arbutus_ctl import Conn, ProtocolError, connect  # noqa: E402


# ---------------------------------------------------------------------------
# the host under test
# ---------------------------------------------------------------------------
#: The RAM-only tape's ruler for a test host, in seconds (see Host.__init__).
TEST_TAPE_SECONDS = 60


def die_with_parent():
    """A `preexec_fn` that ends a spawned host with the process that spawned it.

    A test process killed outright (ctest's timeout, CI cancelling a gate)
    never reaches `close()`, and its host, a socket server nobody tells to
    stop, ran on for a day under the bench host's runner (#1226; the
    emulator's twin is `native_audio._die_with_parent`, #517). On Linux
    `prctl(PR_SET_PDEATHSIG, SIGTERM)` has the kernel send SIGTERM when the
    parent dies, and the host exits cleanly on it. None elsewhere: macOS has
    no such call; and None off the main thread, as the signal follows the
    *thread* that forked.
    """
    import threading
    if not sys.platform.startswith("linux") \
            or threading.current_thread() is not threading.main_thread():
        return None
    import ctypes
    import signal

    libc = ctypes.CDLL(None, use_errno=True)
    parent = os.getpid()

    def hook():
        # Runs in the child between fork and exec. PR_SET_PDEATHSIG is 1.
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)
        if os.getppid() != parent:       # the parent died before the line above
            os._exit(1)
    return hook


class Host:
    def __init__(self, exe, libraries, block=512, sr=48000, extra=()):
        self.dir = tempfile.mkdtemp(prefix="arbutus-audio-test-")
        # One of these per test host, and on the CM4 /tmp is a tmpfs, so a gate
        # run left 176 of them in RAM (#105). The directory belongs to the
        # *object*, not to `close()`: host_clips.py writes a WAV under one
        # host, closes it, and loads that file into the next one, so tying the
        # removal to shutdown would delete a file the test still needs.
        # weakref.finalize is the destructor C++ would give us -- it runs when
        # this object is collected or at interpreter exit, whichever comes
        # first, and at most once (__del__ is not guaranteed to run at
        # shutdown). A `with Host(...)` in a function therefore keeps its
        # directory until the function returns, and a traceback that still
        # references the frame keeps it for the post-mortem.
        # ARBUTUS_KEEP_TMP=1 keeps it for good, host.log and all.
        self._rm = None
        if not os.environ.get("ARBUTUS_KEEP_TMP"):
            self._rm = weakref.finalize(self, shutil.rmtree, self.dir, True)
        self.sock = os.path.join(self.dir, "s")
        cmd = [exe, "--offline", "--quiet", "--socket", self.sock, "--sr", str(sr)]
        if block is not None:               # None: the host's own default
            cmd += ["--block", str(block)]
        for lib in libraries:
            cmd += ["--library", lib]
        extra = list(extra)
        # A host with no --tape-dir runs the RAM-only tape, and every track it
        # touches allocates its whole ruler at once: the 8-minute default is
        # 184 MB a track, which reset a 512 MB board under ctest (#1125). The
        # device always passes a --tape-dir; a RAM-only test host gets a
        # 60 s ruler (23 MB a track) unless it asks for its own.
        if "--tape-dir" not in extra and "--tape-seconds" not in extra:
            extra += ["--tape-seconds", str(TEST_TAPE_SECONDS)]
        cmd += extra
        self.log = open(os.path.join(self.dir, "host.log"), "w+")
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT,
                                     preexec_fn=die_with_parent())
        try:
            self.c = connect(self.sock)
        except ProtocolError:
            self.proc.kill()               # a host that never answered still runs
            self.proc.wait()
            self.log.seek(0)
            raise ProtocolError("arbutus-audio did not start:\n" + self.log.read())
        h = self.c.hello()
        assert h.get("type") == "hello", h

    def path(self, name):
        return os.path.join(self.dir, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.c.request({"type": "shutdown"})
        except Exception:
            pass
        try:
            self.c.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait()
        self.log.close()

    # -- convenience -------------------------------------------------------
    def ok(self, msg):
        return self.c.ok(msg)

    def req(self, msg):
        return self.c.request(msg)

    def render(self, frames, file=None):
        m = {"type": "render", "frames": frames}
        if file:
            m["file"] = file
        return self.c.ok(m)

    def note(self, note, on=True, vel=0.9):
        return self.c.ok({"type": "note", "on": on, "note": note, "vel": vel})


# ---------------------------------------------------------------------------
# WAV, by hand (audio/hostlib/audiofile.cpp writes these)
# ---------------------------------------------------------------------------
def read_wav(path):
    """-> (sample_rate, [channel, ...]) of python floats."""
    with open(path, "rb") as f:
        b = f.read()
    assert b[:4] == b"RIFF" and b[8:12] == b"WAVE", path
    p, fmt, ch, bits, rate, data = 12, None, None, None, None, None
    while p + 8 <= len(b):
        cid = b[p:p + 4]
        n = struct.unpack_from("<I", b, p + 4)[0]
        body = p + 8
        if cid == b"fmt ":
            fmt, ch, rate = struct.unpack_from("<HHI", b, body)
            bits = struct.unpack_from("<H", b, body + 14)[0]
        elif cid == b"data":
            data = b[body:body + n]
        p = body + n + (n & 1)
    assert data is not None and ch, path
    stride = ch * (bits // 8)
    frames = len(data) // stride
    out = [[0.0] * frames for _ in range(ch)]
    for i in range(frames):
        for c in range(ch):
            o = i * stride + c * (bits // 8)
            if fmt == 3:
                v = struct.unpack_from("<f", data, o)[0]
            elif bits == 16:
                v = struct.unpack_from("<h", data, o)[0] / 32768.0
            else:                                        # 24-bit PCM
                q = data[o] | (data[o + 1] << 8) | (data[o + 2] << 16)
                if q & 0x800000:
                    q -= 0x1000000
                v = q / 8388608.0
            out[c][i] = v
    return rate, out


def read_wav_arrays(path):
    """read_wav() for a long float32 WAV: -> (sample_rate, [channel, ...]),
    each channel an array('f'), 4 bytes a sample where read_wav's lists take
    ~32 (a 62 s stereo render: 24 MB, not ~190; #1125). Index, slice, zip and
    compare it as a list; `+` and `==` only with another array('f')."""
    fmt = None
    with open(path, "rb") as f:
        head = f.read(12)
        assert head[:4] == b"RIFF" and head[8:12] == b"WAVE", path
        while True:
            ck = f.read(8)
            assert len(ck) == 8, path
            cid, n = ck[:4], struct.unpack("<I", ck[4:])[0]
            if cid == b"data":
                break
            body = f.read(n + (n & 1))
            if cid == b"fmt ":
                fmt, ch, rate = struct.unpack_from("<HHI", body)
                bits = struct.unpack_from("<H", body, 14)[0]
        assert fmt == 3 and bits == 32, (path, fmt)
        buf = array.array("f")
        buf.fromfile(f, n // 4)             # straight from the file, no bytes copy
    if sys.byteorder != "little":
        buf.byteswap()
    return rate, [buf[c::ch] for c in range(ch)]   # [c::ch]: every ch-th sample


def peak(chans):
    return max((max(abs(v) for v in c) if c else 0.0) for c in chans)


def peak_error(a, b):
    """Largest absolute sample difference between two channel lists."""
    assert len(a) == len(b), "channel count differs"
    worst = 0.0
    for ca, cb in zip(a, b):
        assert len(ca) == len(cb), "length differs: %d vs %d" % (len(ca), len(cb))
        for x, y in zip(ca, cb):
            d = abs(x - y)
            if d > worst:
                worst = d
    return worst


def db(x):
    return 20.0 * math.log10(x) if x > 0 else float("-inf")


def compare(a_path, b_path, tol_db=-100.0, label=""):
    _, a = read_wav(a_path)
    _, b = read_wav(b_path)
    e = peak_error(a, b)
    line = "%s: peak error %.1f dBFS over %d frames (tol %.1f) %s" % (
        label or "compare", db(e), len(a[0]), tol_db, "PASS" if db(e) <= tol_db else "FAIL")
    print(line)
    assert db(e) <= tol_db, line
    return e


def differs(a_path, b_path, min_db=-40.0, label=""):
    """compare()'s mirror: the two renders must differ by at least `min_db`
    peak, so a processor that should be in the path is audibly there."""
    _, a = read_wav(a_path)
    _, b = read_wav(b_path)
    e = peak_error(a, b)
    line = "%s: peak difference %.1f dBFS (min %.1f) %s" % (
        label or "differs", db(e), min_db, "PASS" if db(e) >= min_db else "FAIL")
    print(line)
    assert db(e) >= min_db, line
    return e


# ---------------------------------------------------------------------------
# a test's own scratch folder (#657)
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def scratch(prefix):
    """`with scratch("arbutus-x-test-") as tmp:` -- a temp folder that is gone
    when the block ends. A failing block (an exception, `assert` included)
    keeps it as the evidence and prints where; ARBUTUS_KEEP_TMP=1 keeps it
    always. A tape test's folder is ~27 MB, and every gate run made three."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    try:
        yield tmp
    except BaseException:
        print("kept for the post-mortem: %s" % tmp, file=sys.stderr)
        raise
    if not os.environ.get("ARBUTUS_KEEP_TMP"):
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# argument plumbing: CTest hands every test the same four paths
# ---------------------------------------------------------------------------
def args():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True, help="arbutus-audio")
    ap.add_argument("--run", default="", help="arbutus-run")
    ap.add_argument("--boxes", required=True, help="the box library root")
    ap.add_argument("--audio-root", default=os.path.join(HERE, ".."))
    return ap.parse_args()


def ok(msg):
    print("ok - %s" % msg)
