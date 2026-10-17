#!/usr/bin/env python3
"""The sidechain path, end to end (engine-spec.md §6.6).

Record a pulse onto tape track 1, put arbutus/duck in the rack with
`sidechain: "tape:1"`, hold a note, and check the note gets out of the way
exactly where the pulse is -- and does not when no source is selected.

This is the first test of `side[]`: the host wires it (rack.cpp `side_of`), the
engine reads it, and nothing else in the chain knows it happened.

Measured as RMS over a window, not peak: ducking is a level change over time
and a window peak can easily sit in the part of the window that has recovered.
"""

import math
import sys

from hostcase import Host, args, db, ok, read_wav

PULSE = 6000          # frames of recorded tone on the tape
TOTAL = 48000         # one second of held note

# arbutus/duck's parameters, normalised 0..1 as §3.2 requires:
#   threshold -36 dB of -60..0 lin      -> 0.40
#   depth     0.95
#   attack    3 ms of 0.5..100 log      -> 0.3382
#   release   60 ms of 10..1000 log     -> 0.3891
DUCK = {"threshold": 0.40, "depth": 0.95, "attack": 0.3382, "release": 0.3891}

UNDER = (500, 5500)          # inside the pulse
AFTER = (20000, 30000)       # long past it


def rms(ch, w):
    seg = ch[w[0]:w[1]]
    return math.sqrt(sum(v * v for v in seg) / len(seg))


def patch(source):
    tab = {"instance": "duck", "engine": "arbutus/duck", "params": DUCK}
    if source:
        tab["sidechain"] = source
    return {"format": 1, "kind": "patch", "name": "DUCKED",
            "engine": {"id": "arbutus/example", "params": {}},
            "env": {"a": 0.005, "d": 0.05, "s": 0.9, "r": 0.2, "mode": "poly"},
            "fx": {"format": 1, "kind": "rack", "name": "DUCKED", "tabs": [tab]}}


def held_note(h, source, path):
    """Load the duck with `source`, hold one note from frame 0, render."""
    h.ok({"type": "load_patch", "patch": patch(source)})
    h.ok({"type": "panic"})
    h.ok({"type": "transport", "op": "stop"})
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.note(60, True, 0.9)
    h.render(TOTAL, path)
    h.ok({"type": "note", "on": False, "note": 60})
    return path


def main():
    a = args()
    with Host(a.exe, [a.boxes]) as h:
        # --- a pulse onto tape track 1 -------------------------------------
        # Sustained, not a blip: the point is a stretch of tape that is loud
        # and a stretch that is not.
        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "BLIP",
            "engine": {"id": "arbutus/example", "params": {}},
            "env": {"a": 0.002, "d": 0.01, "s": 0.95, "r": 0.01, "mode": "poly"}}})
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 0, "on": True})
        h.note(48, True, 1.0)
        h.render(PULSE)
        h.ok({"type": "note", "on": False, "note": 48})
        h.ok({"type": "record", "track": 0, "on": False})
        h.render(512)

        clips = h.c.state()["tape"]["tracks"][0]["clips"]
        assert len(clips) == 1 and clips[0]["start_frame"] == 0, clips
        ok("sidechain: a %d-frame pulse is on tape track 1" % clips[0]["len"])

        # The tape still feeds side[]; it just must not reach the master, or
        # the measurement would be of the pulse rather than of the ducking.
        h.ok({"type": "mixer", "track": 0, "gain": 0.0})

        _, dch = read_wav(held_note(h, "tape:1", h.path("ducked.wav")))
        _, och = read_wav(held_note(h, None, h.path("open.wav")))

    assert len(dch[0]) == TOTAL and len(och[0]) == TOTAL

    # With no source the duck is a wire: the held note is the same level either
    # side of where the pulse would have been.
    flat = db(rms(och[0], UNDER) / rms(och[0], AFTER))
    assert abs(flat) <= 6.0, "no-source run is not flat: %.1f dB" % flat
    ok("sidechain: source \"none\" leaves the signal alone (%.1f dB across the run)" % flat)

    cut = db(rms(dch[0], UNDER) / rms(och[0], UNDER))
    back = db(rms(dch[0], AFTER) / rms(och[0], AFTER))
    assert cut <= -10.0, "not ducked under the pulse: %.1f dB" % cut
    assert back >= -0.5, "did not recover after the pulse: %.1f dB" % back
    ok("sidechain: %.1f dB under the pulse, %.1f dB after it" % (cut, back))
    return 0


if __name__ == "__main__":
    sys.exit(main())
