#!/usr/bin/env python3
"""Stage E1: eight tracks, overdub-only record, LINK, clip_to_asset.

The one thing worth proving about overdub is that a take really contains what
the track already played: record silence over a clip and what lands in the
medium must be that clip. Everything else here is geometry (DDR-056's absorb),
arithmetic (DDR-059's factor) and a file on disk.

The host runs as the device runs it (#575): `--tape-dir`, so the pool is
bounded (`--tape-pool-seconds`, 120 track-seconds, ~45 MB) and the takes stream
to the media. With no `--tape-dir` every track it touches allocates its whole
eight minutes (~180 MB) at once: four tracks drove a 512 MB Pi into the
watchdog's reset (#563, #568), in a mode the instrument no longer uses.
"""

import os
import struct
import sys

from hostcase import Host, args, db, ok, peak, peak_error, read_wav, scratch

TAKE = 24000
SR = 48000


def clips(st, t):
    return st["tape"]["tracks"][t]["clips"]


def spans(st, t):
    return [(int(c["start_frame"]), int(c["len"])) for c in clips(st, t)]


def overlapping(sp):
    s = sorted(sp)
    return any(s[i][0] + s[i][1] > s[i + 1][0] for i in range(len(s) - 1))


def record(h, track, frames, notes=()):
    h.ok({"type": "transport", "op": "stop"})
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "record", "track": track, "on": True})
    for n in notes:
        h.note(n, True, 0.9)
    h.render(frames)
    h.ok({"type": "record", "track": track, "on": False})
    for n in notes:
        h.note(n, False)
    h.render(512)
    h.ok({"type": "transport", "op": "stop"})


def main():
    a = args()
    with scratch("arbutus-tape8-test-") as tmp:
        return run(a, os.path.join(tmp, "media"))


def run(a, media):
    with Host(a.exe, [a.boxes], extra=["--tape-dir", media]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})

        # --- 1. the tape is eight tracks wide (DDR-060) --------------------
        st = h.c.state()
        assert len(st["tape"]["tracks"]) == 8, len(st["tape"]["tracks"])
        ok("tape: eight tracks in `state`")

        # a track costs nothing until it is recorded: bank B is still unused
        assert all(t["used_frames"] == 0 for t in st["tape"]["tracks"]), st["tape"]["tracks"]

        # --- 2. bank B records like bank A --------------------------------
        record(h, 7, TAKE, notes=(60,))
        st = h.c.state()
        assert len(clips(st, 7)) == 1 and clips(st, 7)[0]["len"] >= TAKE, clips(st, 7)
        assert st["tape"]["tracks"][7]["used_frames"] >= TAKE
        ok("tape: track 8 records (%d frames)" % clips(st, 7)[0]["len"])

        # the sidechain names all eight, and only eight
        h.ok({"type": "load_rack", "rack": {
            "format": 1, "kind": "rack", "name": "D",
            "tabs": [{"instance": "d", "engine": "arbutus/duck", "params": {}}]}})
        h.ok({"type": "set_sidechain", "index": 0, "source": "tape:8"})
        r = h.req({"type": "set_sidechain", "index": 0, "source": "tape:9"})
        assert r.get("type") == "error", r
        h.ok({"type": "set_sidechain", "index": 0, "source": "none"})
        h.ok({"type": "load_rack", "rack": {"format": 1, "kind": "rack",
                                            "name": "-", "tabs": []}})
        ok("tape: sidechain takes tape:1..8 and refuses tape:9")

        # --- 3. overdub: the take holds what the track played --------------
        record(h, 0, TAKE, notes=(64,))
        st = h.c.state()
        first = clips(st, 0)[0]
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": first["src_offset"]}]})
        h.ok({"type": "unload_engine", "slot": "engine"})
        h.render(2048)                      # let the hot-swap crossfade finish

        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        before = h.path("before.wav")
        h.render(TAKE, before)
        h.ok({"type": "transport", "op": "stop"})

        # now record *silence* over it: overdub must capture the playback
        h.c.clear_pushes()
        record(h, 0, TAKE)
        h.c.drain(0.3)
        st = h.c.state()
        assert len(clips(st, 0)) == 1, clips(st, 0)     # the take absorbed the clip
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        after = h.path("after.wav")
        h.render(TAKE, after)
        h.ok({"type": "transport", "op": "stop"})

        takes = [p for p in h.c.pushes("take") if p.get("track") == 0]
        assert takes, "no `take` push for the overdub"
        assert takes[-1]["len"] >= TAKE, takes[-1]
        ok("tape: the finished take arrives as a `take` push")

        # --- 4. absorb: a take splits what it covers, nothing overlaps -----
        h.ok({"type": "set_clips", "track": 1, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": first["src_offset"],
             "src_track": 0}]})
        h.ok({"type": "transport", "op": "stop"})
        h.ok({"type": "transport", "op": "locate", "position": TAKE // 4})
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 1, "on": True})
        h.render(TAKE // 4)
        h.ok({"type": "record", "track": 1, "on": False})
        h.render(512)
        h.ok({"type": "transport", "op": "stop"})
        sp = spans(h.c.state(), 1)
        assert len(sp) == 3, sp
        assert not overlapping(sp), sp
        assert sorted(sp)[0] == (0, TAKE // 4), sp
        ok("tape: the take split the clip it covered into head + take + tail")

        # --- 5. LINK (DDR-059) --------------------------------------------
        st = h.c.state()
        assert st["tape"]["link"]["on"] is True, st["tape"]["link"]
        assert abs(st["tape"]["link"]["factor"] - 1.0) < 1e-6, st["tape"]["link"]
        h.ok({"type": "tempo", "bpm": 240})
        st = h.c.state()
        assert abs(st["tape"]["link"]["factor"] - 2.0) < 1e-6, st["tape"]["link"]

        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.render(1024)
        pos = h.c.state()["tape"]["position"]
        h.ok({"type": "transport", "op": "stop"})
        assert abs(pos - 2048) <= 8, pos
        ok("tape: LINK at 2x moves the playhead two frames per rendered frame")

        # a take under LINK is stored at the tape's rate, so its clip covers
        # the timeline it was played over, not the frames that were rendered
        record(h, 2, 4096)
        c = clips(h.c.state(), 2)[0]
        assert abs(int(c["len"]) - 2 * 4096) <= 64, c
        ok("tape: a take under LINK 2x is %d frames of timeline" % c["len"])

        # LINK off returns the tape to its own pitch -- that is what the off
        # state is for: a second song at a new BPM over the first (DDR-059)
        h.ok({"type": "tempo", "link": False})
        st = h.c.state()
        assert st["tape"]["link"]["on"] is False, st["tape"]["link"]
        assert abs(st["tape"]["link"]["factor"] - 1.0) < 1e-6, st["tape"]["link"]
        h.ok({"type": "tempo", "link": True})
        assert abs(h.c.state()["tape"]["link"]["factor"] - 2.0) < 1e-6
        h.ok({"type": "tempo", "bpm": 120})
        assert abs(h.c.state()["tape"]["link"]["factor"] - 1.0) < 1e-6
        ok("tape: LINK off plays the tape at its own rate, on follows the BPM")

        # --- 6. clip_to_asset (§15.6) -------------------------------------
        r = h.ok({"type": "clip_to_asset", "track": 0, "start_frame": 0, "len": TAKE})
        one = r["path"]
        assert r["frames"] == TAKE, r
        assert os.path.exists(one), one

        full = h.ok({"type": "clip_to_asset", "track": "all",
                     "start_frame": 0, "len": TAKE})["path"]
        h.ok({"type": "mixer", "master": 0.5})
        half = h.ok({"type": "clip_to_asset", "track": "all",
                     "start_frame": 0, "len": TAKE})["path"]
        # one track hands back what it holds, master or no master
        quiet = h.ok({"type": "clip_to_asset", "track": 0,
                      "start_frame": 0, "len": TAKE})["path"]
        h.ok({"type": "mixer", "master": 1.0})

        bad = h.req({"type": "clip_to_asset", "track": 8, "start_frame": 0, "len": 10})
        assert bad.get("type") == "error", bad
        bad = h.req({"type": "clip_to_asset", "track": 0, "start_frame": 0, "len": 0})
        assert bad.get("type") == "error", bad
        ok("clip_to_asset: refuses track 8 and an empty length")

    # --- the files, off the wire ------------------------------------------
    _, b = read_wav(before)
    _, af = read_wav(after)
    e = peak_error(b, af)
    assert db(e) <= -100.0, "overdub did not capture the playback (%.1f dBFS)" % db(e)
    ok("tape: recording silence over a clip re-records the clip (%.1f dBFS)" % db(e))

    with open(one, "rb") as f:
        head = f.read(64)
    assert struct.unpack_from("<H", head, head.index(b"fmt ") + 22)[0] == 16, "not 16-bit"
    ok("clip_to_asset: the file engines can read is 16-bit WAV")

    _, single = read_wav(one)
    _, f = read_wav(full)
    _, hf = read_wav(half)
    _, q = read_wav(quiet)
    assert len(single[0]) == TAKE and len(f[0]) == TAKE
    assert peak(single) > 0.01, peak(single)
    # "all" goes through the mixer: halving the master halves the bounce. One
    # track's own bounce is its audio and the master never touches it.
    assert abs(peak(hf) - peak(f) * 0.5) < 0.01, (peak(hf), peak(f))
    assert peak_error(single, q) == 0.0, "master leaked into a single-track bounce"
    ok("clip_to_asset: a track bounces raw, \"all\" bounces through the mixer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
