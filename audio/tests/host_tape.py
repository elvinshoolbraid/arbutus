#!/usr/bin/env python3
"""Tape: record a take, re-place it with set_clips, bake, compare.

The audio process renders a clip list; the UI owns the editing. So the test
records something, then tells the host where the UI decided it goes, and checks
that playback and the bake both put it there.
"""

import sys

from hostcase import Host, args, db, ok, peak, peak_error, read_wav

TAKE = 24000          # frames of synth recorded onto track 0
PLACE = 48000         # where the UI decides the clip goes
LEN = 12000
OFFSET = 6000
GAIN = 0.5
TOTAL = 96000


def main():
    a = args()
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})

        # --- record the active instrument bus onto track 0 -----------------
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 0, "on": True})
        h.note(60, True, 0.9)
        h.note(64, True, 0.8)
        h.render(TAKE)
        h.ok({"type": "record", "track": 0, "on": False})
        h.render(512)

        st = h.c.state()
        clips = st["tape"]["tracks"][0]["clips"]
        assert len(clips) == 1, clips
        assert clips[0]["start_frame"] == 0 and clips[0]["len"] >= TAKE, clips
        ok("tape: the take became a clip (%d frames)" % clips[0]["len"])

        # --- the UI re-places it ------------------------------------------
        h.ok({"type": "note", "on": False, "note": 60})
        h.ok({"type": "note", "on": False, "note": 64})
        h.ok({"type": "unload_engine", "slot": "engine"})
        h.render(2048)                       # let the swap crossfade finish (§8)
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": PLACE, "len": LEN, "src_offset": OFFSET, "gain": GAIN}]})

        h.ok({"type": "transport", "op": "stop"})
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        direct = h.path("direct.wav")
        h.render(TOTAL, direct)

        baked = h.path("baked.wav")
        r = h.ok({"type": "bake", "tracks": [0], "file": baked, "format": "f32",
                  "frames": TOTAL})
        assert r["file"] == baked, r

    _, d = read_wav(direct)
    _, b = read_wav(baked)
    assert len(d[0]) == TOTAL and len(b[0]) == TOTAL, (len(d[0]), len(b[0]))

    # the clip renders exactly where the UI said, and nowhere else
    before = max(abs(v) for v in d[0][:PLACE])
    inside = max(abs(v) for v in d[0][PLACE:PLACE + LEN])
    after = max(abs(v) for v in d[0][PLACE + LEN:])
    assert before == 0.0 and after == 0.0, (before, after)
    assert inside > 0.01, inside
    ok("tape: src_offset/len/gain land at %d..%d, silence either side" % (PLACE, PLACE + LEN))

    e = peak_error(d, b)
    assert db(e) <= -100.0, "bake != playback (%.1f dBFS)" % db(e)
    ok("tape: bake matches the direct render (%.1f dBFS)" % db(e))

    # gain really is applied: the clip is quieter than the medium it reads from
    # (the medium held a full-level take; 0.5 gain and the -3 dB centre pan law)
    assert inside < 1.0, inside
    return 0


if __name__ == "__main__":
    sys.exit(main())
