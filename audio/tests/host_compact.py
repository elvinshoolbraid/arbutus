#!/usr/bin/env python3
"""`tape_compact` through the whole host (C1 step 8, #74,
tape-streaming-design.md § 3.8).

test_tape_io.cpp proves the copy against `FakeStore`. This proves the wiring
and the *contract at the wire*, which is the half a UI has to build against:

  * the medium is append-only, so two takes on one track leave the first one's
    audio on the card even after the clip list stops naming it — `state`'s new
    `dead_frames` is the host's suggestion that a compaction is worth doing;
  * `tape_compact` takes the **UI's** keep set (its clips, its lift buffer, its
    one undo record — the host has never seen the last two) and replies
    `started`; the map arrives as a **push**, because the copy is seconds long
    on a real card and § 3.8's rule is that SAVE starts a compaction rather
    than waiting for one;
  * the audio a kept span pointed at is the audio it points at afterwards, at
    the new offset the map gives — checked against a `tape_save` of the take
    taken *before* the compaction, which is an independent copy;
  * the refusals: `RECORDING`, `PLAYING`, and no `--tape-dir` at all.

Offline there is no I/O thread, so the copy runs inside the request and the
push is simply already waiting — the wire shape is the same either way, which
is the point of testing it here rather than only in C++. The one thing that
cannot be reached from here is **cancellation**: a job that finishes before
its request returns has no window for PLAY or REC to arrive in, so the cancel
path is `test_tape_io.cpp`'s `test_compact_cancelled_live`, on a real thread.
"""

import os
import sys

from hostcase import Host, args, ok, read_wav, scratch

RANGE = 49152                      # Tape::kRangeFrames, 1.024 s at 48 kHz
TAKE = RANGE * 2 + 5000


def record(h, track, frames, note):
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "record", "track": track, "on": True})
    h.note(note, True, 0.9)
    h.render(frames)
    h.ok({"type": "note", "on": False, "note": note})
    h.ok({"type": "record", "track": track, "on": False})
    h.ok({"type": "transport", "op": "stop"})
    h.render(512)


def compaction_push(h):
    """The `tape_compact` push, after a request that gave the host a poll."""
    h.c.state()                                  # any round trip drains the socket
    pushes = [p for p in h.c.pushes("tape_compact")]
    assert pushes, "no tape_compact push arrived"
    return pushes[-1]


def main():
    with scratch("arbutus-compact-test-") as tmp:
        return run(tmp)


def run(tmp):
    a = args()
    media = os.path.join(tmp, "media")
    with Host(a.exe, [a.boxes], extra=["--tape-dir", media]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})
        h.ok({"type": "transport", "op": "locate", "position": 0})

        # Two takes on one track. The medium is append-only (DDR-056), so the
        # second lands *after* the first and both are on the card.
        record(h, 0, TAKE, 60)
        first = h.c.state()["tape"]["tracks"][0]["used_frames"]
        h.ok({"type": "transport", "op": "locate", "position": 0})
        record(h, 0, TAKE, 67)
        used = h.c.state()["tape"]["tracks"][0]["used_frames"]
        assert used > first, (used, first)
        ok("compact: two takes, %d frames of medium behind %d of clips"
           % (used, used - first))

        # An independent copy of the whole medium, taken before anything moves.
        before = h.path("before.wav")
        h.ok({"type": "tape_save", "track": 0, "file": before})
        _, before_ch = read_wav(before)

        # The UI keeps only the second take. `dead_frames` is what the host can
        # see of that on its own: the clip list after the retake names the
        # second take alone, so the first is dead.
        keep = [{"src_offset": first, "len": used - first}]
        dead = h.c.state()["tape"]["tracks"][0]["dead_frames"]
        assert dead == first, (dead, first)
        ok("compact: state.tape.tracks[0].dead_frames is %d, the first take" % dead)

        r = h.ok({"type": "tape_compact", "track": 0, "keep": keep})
        assert r.get("started") is True, r
        assert r["track"] == 0, r
        assert r["dead_frames"] == first, (r, first)

        push = compaction_push(h)
        assert push["track"] == 0, push
        assert "error" not in push and "cancelled" not in push, push
        rows = push["map"]
        assert len(rows) == 1, rows
        assert rows[0]["from"] == first, (rows, first)
        assert rows[0]["to"] == 0, rows            # the first kept span starts the medium
        assert rows[0]["len"] == used - first, (rows, used - first)
        assert push["frames"] == used - first, (push, used - first)
        assert push["freed"] == first, (push, first)
        ok("compact: the map moved %d frames from %d to 0, freeing %d"
           % (rows[0]["len"], rows[0]["from"], push["freed"]))

        # The head follows the compacted medium, and nothing is dead any more
        # once the UI has remapped (which here is the keep set itself).
        st = h.c.state()["tape"]["tracks"][0]
        assert st["used_frames"] == used - first, (st, used - first)

        # The audio: every kept frame is the frame it was, at its new offset,
        # against the copy taken before the compaction. This is the contract a
        # UI's remap depends on and it cannot be satisfied by reading the file
        # under test back out of itself.
        after = h.path("after.wav")
        h.ok({"type": "tape_save", "track": 0, "file": after})
        _, after_ch = read_wav(after)
        assert len(after_ch[0]) == used - first, (len(after_ch[0]), used - first)
        wrong = sum(1 for c in range(2) for i in range(used - first)
                    if after_ch[c][i] != before_ch[c][first + i])
        assert wrong == 0, "%d frames of the kept take moved wrong" % wrong
        # The same compare one frame out must fail, or "it matched" says
        # nothing (#56: a test that has never been seen to fail is not
        # evidence). Silence at the very start of a take would match anything,
        # so the window is taken from the middle, where the note is sounding.
        mid = (used - first) // 2
        shifted = sum(1 for c in range(2) for i in range(mid, mid + 2000)
                      if after_ch[c][i] != before_ch[c][first + i + 1])
        assert shifted > 0, "the shifted compare matched — the check proves nothing"
        ok("compact: %d kept frames are sample-exact against the pre-compaction copy"
           % (used - first))

        # The file on the card is the compacted one and the scratch is gone.
        assert sorted(os.listdir(media)) == ["tape-1.wav"], os.listdir(media)
        rate, live_ch = read_wav(os.path.join(media, "tape-1.wav"))
        assert rate == 48000 and len(live_ch[0]) == used - first, (rate, len(live_ch[0]))
        ok("compact: the medium on the card is the compacted one, no .compact left")

        # § 3.8's refusals. PLAYING first, because the copy would otherwise
        # compete with read-ahead for the card.
        h.ok({"type": "transport", "op": "start"})
        r = h.req({"type": "tape_compact", "track": 0, "keep": []})
        assert r["type"] == "error" and r["message"] == "PLAYING", r
        h.ok({"type": "transport", "op": "stop"})
        h.render(512)
        # And while a take is open, like `tape_edit` and `tape_save`: the take
        # appends to the very medium a compaction would replace.
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 0, "on": True})
        h.render(1024)
        r = h.req({"type": "tape_compact", "track": 0, "keep": []})
        assert r["type"] == "error" and r["message"] == "RECORDING", r
        h.ok({"type": "record", "track": 0, "on": False})
        h.ok({"type": "transport", "op": "stop"})
        h.render(512)
        r = h.req({"type": "tape_compact", "track": 9, "keep": []})
        assert r["type"] == "error", r
        ok("compact: refused while PLAYING, while RECORDING, and off track 0..7")

        # An empty keep set means it: the track is truncated to its header.
        h.ok({"type": "tape_compact", "track": 0, "keep": []})
        push = compaction_push(h)
        assert push["map"] == [], push
        assert push["frames"] == 0, push
        assert h.c.state()["tape"]["tracks"][0]["used_frames"] == 0, h.c.state()["tape"]
        assert os.path.getsize(os.path.join(media, "tape-1.wav")) == 4096, media
        ok("compact: an empty keep set truncates the medium to its 4 KiB header")

    # No --tape-dir: there is no medium to compact and the host says so rather
    # than pretending it did one.
    with Host(a.exe, [a.boxes]) as h:
        r = h.req({"type": "tape_compact", "track": 0, "keep": []})
        assert r["type"] == "error" and r["message"] == "NO TAPE DIR", r
        ok("compact: without --tape-dir tape_compact is refused")

    print("host_compact: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
