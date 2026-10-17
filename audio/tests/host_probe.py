#!/usr/bin/env python3
"""`note {probe}` (#73): the audio thread reports the block that carries a note,
and a note's onset is the first frame of that block -- never inside it."""

import sys
import time

from hostcase import Host, args, ok, read_wav

# The host's clock, `steady_clock`: CLOCK_MONOTONIC on Linux, which is Python's
# monotonic_ns there; CLOCK_MONOTONIC_RAW on macOS (libc++), which counts sleep while
# Python's monotonic_ns does not -- on a Mac that has slept they differ by that (#251).
if sys.platform == "darwin":
    def host_now_ns():
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
else:
    host_now_ns = time.monotonic_ns


def probes(h, expect=True):
    """The probe pushes so far: waited on when one is expected, and when none is,
    0.3 s of nothing (#538: drain() used to stop after 50 ms of quiet)."""
    if expect:
        h.c.wait_push("probe")
        h.c.state()                  # any second one is in before this reply
    else:
        h.c.drain(0.3)
    return h.c.pushes("probe")


def first_sound(path):
    _, ch = read_wav(path)
    return next(i for i, v in enumerate(ch[0]) if v != 0.0), ch[0]


def main():
    a = args()
    with Host(a.exe, [a.boxes], block=256) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.render(256 * 3)                             # blocks 0..2
        h.c.clear_pushes()
        t0 = host_now_ns()
        h.ok({"type": "note", "on": True, "note": 60, "vel": 0.9, "probe": 7})
        h.ok({"type": "note", "on": True, "note": 64, "vel": 0.9})   # unprobed
        assert not probes(h, expect=False), "a probe was reported before any block ran"
        h.render(256)
        p = probes(h)
        assert len(p) == 1, p
        p = p[0]
        assert p["id"] == 7 and p["block"] == 3 and p["frame"] == 0, p
        # One clock for the client, the control thread and the audio thread.
        assert t0 <= p["t_ctl_ns"] <= p["t1_ns"] <= host_now_ns(), (t0, p)
        ok("probe: block 3, frame 0, t0 <= t_ctl <= t1 on one clock")

    # Onset: the note starts at frame 0 of the block after it arrived. Offline,
    # a `render` boundary is a block boundary, so a note sent after 300 frames
    # must sound exactly like one sent at frame 0, shifted by 300.
    outs = []
    for lead in (0, 300):
        with Host(a.exe, [a.boxes], block=256) as h:
            h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
            if lead:
                h.render(lead)
            h.ok({"type": "note", "on": True, "note": 60, "vel": 0.9, "probe": 1})
            f = h.path("n.wav")
            h.render(1024, file=f)
            p = probes(h)
            assert p and p[0]["block"] == -(-lead // 256), p      # 300 = 256 + 44: two blocks
            outs.append(first_sound(f))
    (i0, s0), (i1, s1) = outs
    assert i0 == i1 and i0 < 4, (i0, i1)
    assert s0 == s1, "the same note sounded different after a 300-frame lead"
    ok("onset: frame %d of the carrying block, with or without a lead -- block-quantised" % i0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
