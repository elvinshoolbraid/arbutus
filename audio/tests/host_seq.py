#!/usr/bin/env python3
"""arbutus/endless in the SEQ slot, driving arbutus/osc (engine-spec.md §5).

The pattern is entered through the protocol the way a player would enter it:
hold the REC soft-key, play three notes, let go. Then hold the root and let it
run. What comes out has to *be* the pattern -- so this measures the pitch in
each step window by counting sign changes of a saw wave, and checks the
sequence is C-E-G repeating, on the beat, and that letting go stops it.
"""

import sys

from hostcase import Host, args, ok, peak, read_wav

STEP = 6000           # a 1/16 step at 120 bpm, 48 kHz
TOTAL = 48000
SAW = 2.0 / 3.0       # ENUM 0..3, SAW is index 2, normalised
NOTES = (60, 64, 67)
HZ = {60: 261.63, 64: 329.63, 67: 392.00}


def patch():
    return {"format": 1, "kind": "patch", "name": "SEQTEST",
            "engine": {"id": "arbutus/osc", "params": {"wave": SAW, "level": 0.9}},
            # A short release keeps one step from smearing into the next, which
            # is what makes the pitch of a window readable.
            "env": {"a": 0.002, "d": 0.02, "s": 0.85, "r": 0.02, "mode": "poly"},
            "seq": {"id": "arbutus/endless", "params": {}}}


def sign_changes(ch, a, b):
    n = 0
    prev = ch[a]
    for i in range(a + 1, b):
        v = ch[i]
        if (v >= 0.0) != (prev >= 0.0):
            n += 1
        prev = v
    return n


def window_hz(ch, a, b, sr=48000):
    """A saw crosses zero twice a cycle: once on the ramp, once on the wrap."""
    return sign_changes(ch, a, b) / 2.0 * sr / float(b - a)


def nearest_note(hz):
    return min(HZ, key=lambda n: abs(HZ[n] - hz))


def main():
    a = args()
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_patch", "patch": patch()})
        st = h.c.state()["instruments"]["synth"]["seq"]
        assert st["id"] == "arbutus/endless", st
        keys = [k["label"] for k in st["desc"]["softkeys"]]
        assert keys == ["REC", "REST", "UNDO", "CLEAR"], keys
        ok("seq: %s declares soft-keys %s (§3.6)" % (st["id"], "/".join(keys)))

        # --- enter the pattern: hold REC, play C E G, let go ----------------
        # Every message lands at the start of the next block (PROTOCOL.md
        # "Timing"), so each one needs a block rendered after it.
        h.ok({"type": "softkey", "key": 0, "down": True, "shift": False})
        h.render(512)
        for n in NOTES:
            h.note(n, True, 1.0)
            h.render(512)
            h.ok({"type": "note", "on": False, "note": n})
            h.render(512)
        h.ok({"type": "softkey", "key": 0, "down": False, "shift": False})
        h.render(512)

        # --- hold the root and let it run -----------------------------------
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.note(60, True, 1.0)
        run = h.path("run.wav")
        h.render(TOTAL, run)
        h.ok({"type": "note", "on": False, "note": 60})

        # --- let go: it stops ------------------------------------------------
        after = h.path("after.wav")
        h.render(24000, after)

    _, ch = read_wav(run)
    assert len(ch[0]) == TOTAL
    p = peak(ch)
    assert p > 0.05, "the sequencer rendered silence (peak %f)" % p
    ok("seq: the pattern renders (peak %.3f)" % p)

    # Read the pitch in the middle of each step. The first step starts inside
    # the first block after the note lands, so windows are taken well inside.
    got = []
    for k in range(1, 7):
        w0 = k * STEP + 1500
        hz = window_hz(ch[0], w0, w0 + 3000)
        got.append(nearest_note(hz))
    want = [NOTES[k % 3] for k in range(1, 7)]
    assert got == want, "pattern is %s, expected %s" % (got, want)
    ok("seq: steps 1-6 are %s -- the pattern, on the beat" % got)

    _, ch2 = read_wav(after)
    tail = max(abs(v) for v in ch2[0][4800:])     # past one release
    assert tail < 1e-4, "still sounding after the key was let go: %f" % tail
    ok("seq: letting the key go stops it (tail %.2e)" % tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
