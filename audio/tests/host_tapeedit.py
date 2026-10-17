#!/usr/bin/env python3
"""C1 step 6, #74: `tape_edit` streams its ranges (design § 3.6 "A `tape_edit`").

An edit used to be built whole in memory -- the mixed sources in one buffer, the
op's output in another -- and then written and held resident until the last of
it was on the card. § 3.6 says the working set should be "a handful of ranges,
not the edit's length, so a 5-minute reverse does not need 300 slots".

Two things follow, and both are tested here against the *whole host*:

* **It has to work at all.** With a bounded pool (`--tape-dir` and
  `--tape-pool-seconds`), an edit longer than the window asked for one slot per
  range of its result, got "no slot" after the first, and then committed the
  zeroes of ranges that were never resident: the reply said success and the
  audio was silence. That is the first case below, and the control is the same
  comparison against the un-reversed source, which must fail.
* **It must not have changed the sound.** Every op is streamable, so the
  arithmetic is meant to be identical frame for frame -- including `filter`,
  whose one pole now carries across a range seam, and `speed`, which indexes the
  run by the same global frame it always did. The control for that is a second
  host with **no** `--tape-dir` at all -- the whole-tape-in-RAM configuration the
  emulator and the device still run -- doing the same four edits on the same
  take: the two media must come out sample for sample identical.

The take is three ranges and a bit, so the head is mid-range and the first chunk
of every edit is a partial one; the window is three seconds, so most of what an
edit reads and writes has to be evicted and fetched back while it runs.
"""

import array
import math
import os

from hostcase import Host, args, ok, scratch

RANGE = 49152                      # Tape::kRangeFrames, 1.024 s at 48 kHz
TAKE = RANGE * 3 + 7000            # three whole ranges and a partial fourth


def medium(h, path, track=0):
    """`tape_save` the track's medium and return its interleaved frames.

    Bounded, this reads back through the fetch path -- the medium is on the
    card and the window holds three ranges of it -- so it is also the proof
    that what the edit wrote is what the card has.
    """
    saved = h.ok({"type": "tape_save", "track": track, "file": path})
    with open(path, "rb") as f:
        b = f.read()
    assert b[:4] == b"RIFF", path
    i = b.find(b"data")
    assert i > 0, path
    a = array.array("f")
    a.frombytes(b[i + 8:i + 8 + int.from_bytes(b[i + 4:i + 8], "little")])
    assert len(a) // 2 >= saved["frames"], (len(a), saved)
    return a


def record(h, frames=TAKE, track=0, note=60):
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "record", "track": track, "on": True})
    h.note(note, True, 0.9)
    h.render(frames // 2)
    h.note(note + 7, True, 0.8)
    h.render(frames - frames // 2)
    h.ok({"type": "record", "track": track, "on": False})
    h.render(512)
    h.ok({"type": "transport", "op": "stop"})
    return h.c.state()["tape"]["tracks"][track]["clips"][-1]


def edits(h, clip):
    """The four ops, each spanning several ranges. Returns the replies."""
    src = clip["src_offset"]
    rev = h.ok({"type": "tape_edit", "track": 0, "op": "reverse", "len": TAKE,
                "sources": [{"track": 0, "offset": src, "len": TAKE}]})
    spd = h.ok({"type": "tape_edit", "track": 0, "op": "speed", "factor": 2.0, "len": TAKE,
                "sources": [{"track": 0, "offset": src, "len": TAKE}]})
    flt = h.ok({"type": "tape_edit", "track": 0, "op": "filter", "cutoff": 300.0,
                "mode": "LP", "len": TAKE,
                "sources": [{"track": 0, "offset": src, "len": TAKE}]})
    # The merge: two sources, one of them the reverse this same edit list just
    # made, at its own gain -- so the run is read from two places in the medium.
    mrg = h.ok({"type": "tape_edit", "track": 0, "op": "copy", "len": 2 * TAKE,
                "sources": [{"track": 0, "offset": src, "len": TAKE, "at": 0},
                            {"track": 0, "offset": rev["src_offset"], "len": TAKE,
                             "at": TAKE, "gain": 0.5}]})
    return {"reverse": rev, "speed": spd, "filter": flt, "copy": mrg}


def main():
    with scratch("arbutus-tapeedit-test-") as tmp:
        return run(tmp)


def run(tmp):
    a = args()

    # ---- the bounded host: the medium is on the card, the window is 3 s -----
    with Host(a.exe, [a.boxes],
              extra=["--tape-dir", os.path.join(tmp, "media"),
                     "--tape-pool-seconds", "3"]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})
        clip = record(h)
        assert clip["len"] >= TAKE, clip
        ranges = (TAKE + RANGE - 1) // RANGE
        slots = h.c.state()["tape"]["pool"]["ranges"]
        assert 0 < slots < ranges, (slots, ranges)
        ok("bounded: a %d-range take against a %d-slot window" % (ranges, slots))

        before = h.c.state()["tape"]["io"]["pool"]
        reps = edits(h, clip)
        after = h.c.state()["tape"]["io"]["pool"]
        for name, r in reps.items():
            want = TAKE // 2 if name == "speed" else (2 * TAKE if name == "copy" else TAKE)
            assert r["len"] == want, (name, r, want)
        assert after["starved"] == 0, after
        # Streaming: the ranges an edit wrote were evicted while it ran, which
        # is how it fitted. Held to the end -- the old behaviour -- there would
        # be nothing to evict and the write would have come back short.
        assert after["evicted"] > before["evicted"] + slots, (before, after)
        assert after["resident"] <= slots, after
        ok("four edits of 3-13 ranges each ran inside a %d-slot window "
           "(%d evictions, 0 starved)" % (slots, after["evicted"] - before["evicted"]))

        bounded = medium(h, os.path.join(tmp, "bounded.wav"))
        rev = reps["reverse"]
        src = clip["src_offset"]
        # Sample for sample: the reverse of the take is the take backwards.
        # This is the assertion that failed before the edit streamed -- the
        # destination was pure silence and the reply said `len` all the same.
        bad = worst = 0
        for i in range(0, TAKE, 37):
            for c in range(2):
                d = bounded[(rev["src_offset"] + i) * 2 + c]
                s = bounded[(src + TAKE - 1 - i) * 2 + c]
                if d != s:
                    bad += 1
                    worst = max(worst, abs(d - s))
        assert bad == 0, (bad, worst)
        ok("bounded reverse: %d probes of a %d-frame edit are sample-exact"
           % (len(range(0, TAKE, 37)) * 2, TAKE))

        # The control for that assertion: compared against the take *forwards*
        # the same probes must differ, or the test above proves nothing.
        same = sum(1 for i in range(0, TAKE, 37)
                   if bounded[(rev["src_offset"] + i) * 2] == bounded[(src + i) * 2])
        assert same < len(range(0, TAKE, 37)) // 2, same
        ok("control: the same probes against the take forwards do not match")

        # ---- the seams, against a reference that is not this host ---------
        # The RAM control below runs the same streaming code, so it cannot see
        # a bug in how a chunk joins the one before it. These two can: one
        # `filter` pole and one `speed` index, computed here from the take.
        src_ch = [bounded[(src + i) * 2] for i in range(TAKE)]
        a1 = min(max(1.0 - math.exp(-2.0 * math.pi * 300.0 / 48000.0), 1e-5), 1.0)

        def one_pole(reset_every=0):
            y, out = 0.0, []
            for i, v in enumerate(src_ch):
                if reset_every and i % reset_every == 0:
                    y = 0.0
                y += a1 * (v - y)
                out.append(y)
            return out

        want = one_pole()
        off = reps["filter"]["src_offset"]
        worst = max(abs(bounded[(off + i) * 2] - want[i]) for i in range(0, TAKE, 29))
        assert worst < 1e-4, worst
        ok("filter: the pole carries across %d range seams (worst %.2e off a "
           "reference computed here)" % (TAKE // RANGE, worst))

        # The control: a pole that restarted at every range boundary -- the
        # bug a chunked filter would have -- is a different signal, and the
        # probes just after a seam are where it shows.
        reset = one_pole(RANGE)
        gap = max(abs(reset[RANGE + i] - want[RANGE + i]) for i in range(0, 400, 7))
        assert gap > 1e-3, gap
        ok("control: a pole restarted at each seam differs by %.3f there" % gap)

        # `speed` reads the run by a global frame index, so a chunk that
        # indexed its own window would drift by a whole chunk. Checked against
        # the same arithmetic done here.
        off = reps["speed"]["src_offset"]
        worst = 0.0
        for k in range(0, reps["speed"]["len"], 23):
            idx = min(max(float(k) * 2.0, 0.0), float(TAKE - 1))
            i0 = int(idx)
            i1 = min(i0 + 1, TAKE - 1)
            fr = idx - i0
            want_k = src_ch[i0] * (1.0 - fr) + src_ch[i1] * fr
            worst = max(worst, abs(bounded[(off + k) * 2] - want_k))
        assert worst < 1e-5, worst
        ok("speed: every probed frame interpolates the same two source frames "
           "the whole-buffer version did (worst %.2e)" % worst)

        # An edit does not read its own output back. The whole-buffer version
        # read every source before it wrote anything; streaming interleaves the
        # two, so a source running past the destination track's head -- into
        # the frames this very edit is writing -- has to be silence, and it is:
        # past the head is what the next take overwrites, not audio the tape
        # claims. The case that shows it is a source that **straddles** the
        # head, so the first half is real audio and the second half is the
        # edit's own first chunk.
        head = h.c.state()["tape"]["tracks"][0]["used_frames"]
        over = h.ok({"type": "tape_edit", "track": 0, "op": "copy", "len": 2 * RANGE,
                     "sources": [{"track": 0, "offset": head - RANGE, "len": 2 * RANGE}]})
        m = medium(h, os.path.join(tmp, "straddle.wav"))
        assert any(m[(over["src_offset"] + i) * 2] != 0.0
                   for i in range(0, RANGE, 17)), "the half inside the head came back silent"
        assert all(m[(over["src_offset"] + RANGE + i) * 2] == 0.0
                   for i in range(0, RANGE, 17)), "the edit read its own output back"
        ok("an edit reading across its own head takes the audio in front of it "
           "and silence after it")

    # ---- the control host: no --tape-dir, the whole tape in RAM -------------
    # Same take, same four edits, nothing streamed off a card. The two media
    # must agree sample for sample, which is what says the streaming did not
    # change the arithmetic of any op -- including `filter`'s pole across a
    # range seam and `speed`'s interpolation across one.
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})
        clip = record(h)
        assert h.c.state()["tape"]["io"] is None, "the control host must have no tape io"
        assert h.c.state()["tape"]["pool"]["ranges"] == 0, "the control host must be unbounded"
        edits(h, clip)
        plain = medium(h, os.path.join(tmp, "plain.wav"))

    n = min(len(plain), len(bounded))
    assert n >= (TAKE * 5) * 2, n
    diff = sum(1 for i in range(0, n, 11) if plain[i] != bounded[i])
    assert diff == 0, diff
    ok("the same four edits on a card and in RAM agree in %d probed samples"
       % len(range(0, n, 11)))

    # And the control for *that*: the two arrays are not trivially equal
    # because one of them is empty or silent.
    assert sum(1 for i in range(0, n, 11) if plain[i] != 0.0) > len(range(0, n, 11)) // 4, \
        "the compared media are mostly silence"
    ok("control: the compared media are audio, not zeros")
    print("host_tapeedit: OK")


main()
