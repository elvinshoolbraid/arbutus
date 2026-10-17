#!/usr/bin/env python3
"""`tape_late`, the three `TAPE FULL` reasons and `tape.pool` through the whole
host (C1 step 7, #74, tape-streaming-design.md § 3.7 and § 4's T10).

`test_tape_readahead.cpp` proves the *counter* against `TapeIo`'s own fakes.
This proves what the player gets: the pushes, the flag, the document FILE ›
SYSTEM reads, and that a take which ends early leaves a clip of what really
survived and does not stop the transport.

What each claim's negative control is, since a test that has never been seen to
fail is not evidence (#56):

  * **`tape_late` is the card, not the tape.** With `--tape-readahead-seconds 0`
    playback of a take three times the RAM window goes late on nearly every
    block and the flag comes up; with the horizon back on, **the same take, the
    same window, the same playback** goes late at most once — the first block,
    before a horizon has been planned from a playhead that has not moved yet.
    That pair is the whole mechanism's *before* switch.
  * **a scrub is silent and uncounted** (Elvin, § 7 question 2). Dragging the
    cursor over five ranges that are not resident adds nothing to the count;
    *playing* those same five ranges adds ~194. Without the control the first
    half would pass on a host that had stopped counting altogether.
  * **`tape_full {reason:"format"}`** — the medium's own end. Before step 7 the
    take stopped growing here and said nothing at all, so the control is the
    clip: it is exactly `capacity_frames` long, which is the take having really
    been ended rather than the push having been invented.
  * **T10, `tape_full {reason:"disk"}`** — storage refuses mid-take. The clip is
    the **committed prefix** (8 ranges of a 12-range take), not the 12 ranges
    the pool still happened to hold; the transport is still rolling. Control:
    the same take with room on the card leaves all 12.
  * **REC refused under 2 x the pool** (§ 7 question 4). Control: one byte over
    the line and the same `record` is accepted.
"""

import os
import sys

from hostcase import Host, args, ok, scratch

RANGE = 49152                      # Tape::kRangeFrames, 1.024 s at 48 kHz
POOL_SLOTS = 3                     # --tape-pool-seconds 3, and the floor is 2
POOL_BYTES = POOL_SLOTS * RANGE * 8


def _take(h, frames, track=0):
    """Record `frames` into `track` from 0, and leave the transport rolling."""
    h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
    h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "record", "track": track, "on": True})
    h.note(60, True, 0.9)
    h.render(frames)
    # A second render so the audio thread publishes whatever ended the take and
    # `poll` turns it into a clip: offline the audio thread only runs inside a
    # `render`, so a take ended by the *control* thread needs one more.
    h.render(512)


def main():
    with scratch("arbutus-tapefull-test-") as tmp:
        return run(tmp)


def run(tmp):
    a = args()

    # -- `tape_late`: the push, the flag, the mask, and the before switch -----
    late = {}
    for name, ahead in (("off", "0"), ("on", "4")):
        media = os.path.join(tmp, "late-" + name)
        with Host(a.exe, [a.boxes],
                  extra=["--tape-dir", media, "--tape-store", "fake",
                         "--tape-pool-seconds", str(POOL_SLOTS),
                         "--tape-readahead-seconds", ahead]) as h:
            _take(h, RANGE * 8)
            h.ok({"type": "record", "track": 0, "on": False})
            h.render(512)
            h.ok({"type": "transport", "op": "locate", "position": 0})
            h.c.clear_pushes()
            h.render(RANGE * 8)              # play the whole take back
            if name == "off":
                h.c.wait_push("tape_late")   # the push it asserts, not 50 ms (#538)
            else:
                h.c.drain(0.5)               # the claim is "never flagged": wait it out
            # The state reply comes after every push the render queued, so the
            # last `tape_late` here is the render's last.
            pool = h.c.state()["tape"]["pool"]
            late[name] = (h.c.pushes("tape_late"), pool)

    pushes, pool = late["off"]
    assert pushes, "a take 3x the window, played with no read-ahead, was never late"
    last = pushes[-1]
    assert last["count"] >= 3 and last["flag"] is True, last
    assert last["window_s"] > 0 and last["total"] >= last["count"], last
    assert last["tracks"] == 1, last          # bit 0, and only bit 0
    assert pool["late_total"] == last["total"], (pool, last)
    assert pool["ranges"] == POOL_SLOTS and pool["resident"] <= POOL_SLOTS, pool
    ok("tape_late: %d late blocks with the horizon off, flag up, tracks 0b%s"
       % (pool["late_total"], format(last["tracks"], "08b")))

    on_pushes, on_pool = late["on"]
    # At most the first block, before a horizon has been planned from a
    # playhead that has not moved yet. Anything more is read-ahead not working.
    assert on_pool["late_total"] <= 1, on_pool
    assert not any(p["flag"] for p in on_pushes), on_pushes
    assert pool["late_total"] > 20 * max(1, on_pool["late_total"]), (pool, on_pool)
    ok("tape_late: the same playback with read-ahead on is late %d times, flag never up"
       % on_pool["late_total"])

    # -- a scrub over an unloaded range is silent and uncounted ---------------
    with Host(a.exe, [a.boxes],
              extra=["--tape-dir", os.path.join(tmp, "scrub"), "--tape-store", "fake",
                     "--tape-pool-seconds", str(POOL_SLOTS),
                     "--tape-readahead-seconds", "0"]) as h:
        _take(h, RANGE * 8)
        h.ok({"type": "record", "track": 0, "on": False})
        h.render(512)
        h.ok({"type": "transport", "op": "stop"})
        h.render(512)
        before = h.c.state()["tape"]["pool"]["late_total"]
        for k in range(5):
            h.ok({"type": "scrub", "position": RANGE * k, "frames": 2048, "reseed": True})
            h.render(4096)
        scrubbed = h.c.state()["tape"]["pool"]["late_total"]
        assert scrubbed == before, (before, scrubbed)
        # The control: the same five ranges played rather than scrubbed.
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.render(RANGE * 5)
        played = h.c.state()["tape"]["pool"]["late_total"]
        assert played > scrubbed + 20, (scrubbed, played)
        ok("tape_late: a scrub over 5 absent ranges counts 0; playing them counts %d"
           % (played - scrubbed))

    # -- `tape_full {reason:"format"}`: the medium's own end ------------------
    # No `--tape-dir`: this is the RAM tape every caller still uses, and the
    # bound is `--tape-seconds`, far under the WAV format's 10,922 ranges.
    with Host(a.exe, [a.boxes], extra=["--tape-seconds", "2"]) as h:
        h.c.clear_pushes()
        _take(h, 48000 * 3)                  # three seconds into a two-second tape
        h.c.wait_push("tape_full")           # (#538)
        st = h.c.state()                     # and any second one, queued before this reply
        full = h.c.pushes("tape_full")
        assert len(full) == 1 and full[0]["reason"] == "format", full
        assert st["playing"] is True, st["playing"]          # the transport rolls
        assert st["tape"]["recording"] is False, st["tape"]
        clips = st["tape"]["tracks"][0]["clips"]
        assert len(clips) == 1, clips
        assert clips[0]["len"] == st["tape"]["capacity_frames"], (clips, st["tape"])
        # Unbounded, `tape.pool` is still there and says there is no window.
        assert st["tape"]["pool"]["ranges"] == 0, st["tape"]["pool"]
        assert st["tape"]["io"] is None, st["tape"]["io"]
        ok("tape_full format: the take ends at the medium's end (%d frames), "
           "transport still rolling" % clips[0]["len"])

    # -- T10: `tape_full {reason:"disk"}` ------------------------------------
    # Room for exactly eight ranges and the 4 KiB header, which is over the
    # REC floor (2 x the 3-slot pool) so the take is allowed to start.
    room8 = 4096 + 8 * RANGE * 8
    assert room8 > 2 * POOL_BYTES, (room8, POOL_BYTES)
    disk = {}
    for name, free in (("full", room8), ("roomy", 64 << 20)):
        with Host(a.exe, [a.boxes],
                  extra=["--tape-dir", os.path.join(tmp, "disk-" + name),
                         "--tape-store", "fake",
                         "--tape-pool-seconds", str(POOL_SLOTS),
                         "--tape-free-bytes", str(free)]) as h:
            h.c.clear_pushes()
            _take(h, RANGE * 12)
            # The I/O thread finds the card full, so the push is waited on; on
            # the roomy card the claim is that none comes, so the wait runs out (#538).
            h.c.wait_push("tape_full", 5.0 if name == "full" else 0.5)
            rolling = h.c.state()["tape"]["recording"]
            if rolling:
                # Nothing ended this take, so STOP does -- which is the control's
                # own claim, and is why it is read before the clip.
                h.ok({"type": "record", "track": 0, "on": False})
                h.render(512)
            disk[name] = (h.c.pushes("tape_full"), h.c.state(), rolling)

    full, st, rolling = disk["full"]
    assert rolling is False, "a card that ran out did not end the take"
    assert len(full) == 1 and full[0]["reason"] == "disk", full
    assert full[0]["total"] >= 1, full
    assert st["playing"] is True, st["playing"]              # the transport rolls
    assert st["tape"]["recording"] is False, st["tape"]
    clips = st["tape"]["tracks"][0]["clips"]
    assert len(clips) == 1, clips
    # The committed prefix, not the twelve ranges the pool still held: those
    # slots are evicted soon and would then read as silence.
    assert clips[0]["len"] == 8 * RANGE, (clips[0]["len"] / RANGE)
    assert st["tape"]["io"]["errors"] >= 1, st["tape"]["io"]
    ok("tape_full disk: a 12-range take on a card with room for 8 leaves an "
       "8-range clip, transport still rolling")

    none, st_ok, rolling_ok = disk["roomy"]
    assert none == [], none
    assert rolling_ok is True, "the take ended on a card with 64 MB free"
    clips_ok = st_ok["tape"]["tracks"][0]["clips"]
    # >= rather than ==: this take was still rolling through `_take`'s trailing
    # render, so it is the twelve ranges plus that block.
    assert len(clips_ok) == 1 and clips_ok[0]["len"] >= 12 * RANGE, clips_ok
    assert st_ok["tape"]["io"]["errors"] == 0, st_ok["tape"]["io"]
    ok("tape_full disk: the same take with room on the card keeps all 12 ranges")

    # -- REC refused under 2 x the pool (§ 7 question 4) ---------------------
    for free, accept in ((2 * POOL_BYTES - 1, False), (2 * POOL_BYTES, True)):
        with Host(a.exe, [a.boxes],
                  extra=["--tape-dir", os.path.join(tmp, "rec-%d" % free),
                         "--tape-store", "fake",
                         "--tape-pool-seconds", str(POOL_SLOTS),
                         "--tape-free-bytes", str(free)]) as h:
            h.ok({"type": "transport", "op": "start"})
            r = h.req({"type": "record", "track": 0, "on": True})
            if accept:
                assert r["type"] == "ok", r
            else:
                assert r["type"] == "error" and r["code"] == "tape_full", r
                assert r["message"] == "TAPE FULL", r
    ok("tape_full: REC is refused under 2 x the pool (%d B) and accepted on it"
       % (2 * POOL_BYTES))

    # -- and none of it happens without a medium on storage -------------------
    with Host(a.exe, [a.boxes]) as h:
        st = h.c.state()
        assert st["tape"]["io"] is None, st["tape"]["io"]
        assert st["tape"]["pool"]["ranges"] == 0, st["tape"]["pool"]
        assert st["tape"]["pool"]["late_total"] == 0, st["tape"]["pool"]
        h.ok({"type": "transport", "op": "start"})
        assert h.req({"type": "record", "track": 0, "on": True})["type"] == "ok"
        ok("tape_full: with no --tape-dir there is no floor to refuse REC under")
    return 0


if __name__ == "__main__":
    sys.exit(main())
