#!/usr/bin/env python3
"""#612: the audio process exits 0 on every clean path, and a stop keeps the take.

The device stops `arbutus-audio` with SIGTERM (`systemctl stop`); a client can
also send `shutdown`, and a terminal sends SIGINT. All three exited 139, a
segfault: `~TapeIo` called `stop()`, which drained through a `Tape*` that only
`--tape-dir` ever sets. No test looked at the exit status, so the bench host's
kernel log filled with it every gate run.

Pinned here: each path exits 0, with and without `--tape-dir`; and with
`--tape-dir`, each of the three in the middle of a take leaves the whole take
on the card, partial last range included, sample for sample what REC off would
have left (#992) -- the stop is a stop, not a crash.
"""

import os
import shutil
import signal
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402

RANGE = 49152                      # tape.hpp kRangeFrames
TAKE = RANGE + 20000               # one whole range and part of the next
HEADER = 4096                      # the live medium's header (design § 3.5)
FRAME = 8                          # stereo float32


def stop(h, how):
    """End the host by `how` and return its exit status (negative = a signal)."""
    if how == "shutdown":
        h.c.request({"type": "shutdown"})
    else:
        h.proc.send_signal(signal.SIGINT if how == "sigint" else signal.SIGTERM)
    try:
        return h.proc.wait(timeout=10)
    finally:
        h.close()                  # kills a host that hung (#1226); harmless on a dead one


def log_of(h):
    with open(os.path.join(h.dir, "host.log")) as f:
        return f.read()


def test_every_path_exits_0(a):
    d = tempfile.mkdtemp(prefix="arbutus-exit-")
    try:
        for extra in ([], ["--tape-dir", os.path.join(d, "tape")]):
            for how in ("shutdown", "sigint", "sigterm"):
                with Host(a.exe, [a.boxes], extra=extra) as h:
                    h.render(512)
                    rc = stop(h, how)
                assert rc == 0, "%s %s: exit %d\n%s" % (how, extra, rc, log_of(h))
            ok("exit 0 after shutdown, SIGINT and SIGTERM, %s" %
               ("with --tape-dir" if extra else "without --tape-dir"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return 0


def start_take(a, d):
    """A host on `d` with a take open: one whole range and part of the next."""
    h = Host(a.exe, [a.boxes], extra=["--tape-dir", d])
    try:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 0, "on": True})
        h.note(60, True, 0.9)
        h.render(TAKE)
    except BaseException:
        h.close()
        raise
    return h


def reference(a, d):
    """The same take ended by REC off, as STOP ends it: its length and its
    audio as the RAM tape holds them (the bytes after `tape_save`'s 44-byte
    header). Offline is deterministic, so an exit that keeps the take must
    leave exactly this on the card."""
    h = start_take(a, d)
    try:
        h.ok({"type": "record", "track": 0, "on": False})
        h.render(512)                  # the command lands on the next block
        used = h.c.state()["tape"]["tracks"][0]["used_frames"]
        ref = h.path("ref.wav")
        r = h.ok({"type": "tape_save", "track": 0, "file": ref})
        assert r["frames"] == used, (r, used)
        with open(ref, "rb") as f:
            audio = f.read()[44:]
    finally:
        h.close()
    assert len(audio) == used * FRAME, (len(audio), used)
    assert audio.count(0) < len(audio), "the reference take is silent"
    return used, audio


def test_every_exit_mid_take_keeps_it_exactly(a):
    """#992: each exit keeps the whole take, its partial last range included.

    `used >= RANGE` alone passed with that last range thrown away (0.4 s of a
    player's recording): the reopened length and the tail's samples are what
    `end_take`'s exact header and `main.cpp`'s `end_open_take` decide."""
    d = tempfile.mkdtemp(prefix="arbutus-exit-")
    try:
        used, audio = reference(a, os.path.join(d, "ref"))
        assert used > RANGE and used % RANGE, used     # a partial last range
        tail = (used // RANGE) * RANGE                  # where that range starts
        for how in ("shutdown", "sigint", "sigterm"):
            tape = os.path.join(d, how)
            with start_take(a, tape) as h:
                rc = stop(h, how)      # the take still open, as a power-off would
            assert rc == 0, "%s: exit %d\n%s" % (how, rc, log_of(h))

            with Host(a.exe, [a.boxes], extra=["--tape-dir", tape]) as h2:
                got = h2.c.state()["tape"]["tracks"][0]["used_frames"]
            assert got == used, "%s: the next host opens %d frames, the take was %d" \
                % (how, got, used)
            with open(os.path.join(tape, "tape-1.wav"), "rb") as f:
                medium = f.read()
            assert len(medium) == HEADER + used * FRAME, (how, len(medium))
            assert medium[HEADER + tail * FRAME:] == audio[tail * FRAME:], \
                "%s: the partial last range differs from the take" % how
            assert medium[HEADER:] == audio, "%s: the medium differs from the take" % how
        ok("exit mid-take by shutdown, SIGINT and SIGTERM: exit 0, and the next "
           "host opens all %d frames, the last %d of them a partial range, "
           "sample-exact" % (used, used - tail))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return 0


def main():
    a = args()
    for t in (test_every_path_exits_0, test_every_exit_mid_take_keeps_it_exactly):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
