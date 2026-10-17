#!/usr/bin/env python3
"""Stage N1: the clip model as geometry — peaks, medium edits, src_track.

The UI owns lift / drop / slice / merge / undo and the samples never leave the
host, so two things have to be true natively: TRACK can draw a clip
(`clip_peaks`), and the ops that change *audio* rather than geometry can be
done in the medium (`tape_edit`). Plus the append-only medium, which is what
makes moved clips safe, and `tape_save`/`tape_load` for user story 18.
"""

import os
import sys

from hostcase import Host, args, db, ok, peak, peak_error, read_wav

TAKE = 12000


def record(h, track, note, frames=TAKE):
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "record", "track": track, "on": True})
    h.note(note, True, 0.9)
    h.render(frames)
    h.note(note, False)
    h.ok({"type": "record", "track": track, "on": False})
    h.render(512)
    h.ok({"type": "transport", "op": "stop"})
    return h.c.state()["tape"]["tracks"][track]["clips"][-1]


def play(h, frames, name):
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.render(frames, h.path(name))
    h.ok({"type": "transport", "op": "stop"})
    return read_wav(h.path(name))[1]


def main():
    a = args()
    with Host(a.exe, [a.boxes], block=128) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "env", "a": 0.005, "d": 0.1, "s": 0.9, "r": 0.05, "mode": "poly"})

        first = record(h, 0, 60)
        assert first["src_offset"] == 0 and first["src_track"] == 0, first
        # A second take appends: the medium never overwrites what a clip reads.
        second = record(h, 0, 72)
        assert second["src_offset"] >= first["len"], (first, second)
        used = h.c.state()["tape"]["tracks"][0]["used_frames"]
        assert used >= second["src_offset"] + second["len"], used
        ok("medium: takes append (%d, then %d) and used_frames follows"
           % (first["src_offset"], second["src_offset"]))
        h.ok({"type": "unload_engine", "slot": "engine"})
        h.render(2048)

        # ---- clip_peaks ---------------------------------------------------
        pk = h.ok({"type": "clip_peaks", "track": 0, "src_offset": first["src_offset"],
                   "len": first["len"], "bins": 16})
        assert pk["type"] == "clip_peaks" and len(pk["min"]) == 16 == len(pk["max"]), pk
        assert max(pk["max"]) > 0.05 and min(pk["min"]) < -0.05, pk
        ok("clip_peaks: 16 bins of min/max over the take (peak %.2f)" % max(pk["max"]))
        # an envelope, not the flat block the native path used to draw
        assert max(pk["max"]) > 1.2 * min(pk["max"]), pk["max"]
        ok("clip_peaks: the bins follow the sound, not the geometry")
        empty = h.ok({"type": "clip_peaks", "track": 3, "src_offset": 0, "len": 480, "bins": 4})
        assert empty["max"] == [0.0] * 4, empty
        ok("clip_peaks: an untouched track reads flat")
        bad = h.req({"type": "clip_peaks", "track": 9, "len": 10})
        assert bad["type"] == "error" and bad["code"] == "bad_request", bad
        ok("clip_peaks: a track outside 0..3 is refused")

        # ---- clip_peaks in a batch (#169) ---------------------------------
        # TRACK draws four lanes at once, so the fetches for a redraw go down
        # as one message. The claim worth testing is that *nothing else*
        # changes: an element of a batch is the answer the same request gets
        # alone. Each check below has the control that would catch the
        # plausible wrong thing -- a reply that echoes element 0, or one whose
        # order is the host's rather than the request's.
        r0 = {"track": 0, "src_offset": first["src_offset"], "len": first["len"], "bins": 16}
        r1 = {"track": 0, "src_offset": second["src_offset"], "len": second["len"], "bins": 8}
        r2 = {"track": 3, "src_offset": 0, "len": 480, "bins": 4}
        alone = [h.ok({"type": "clip_peaks", **r}) for r in (r0, r1, r2)]
        batch = h.ok({"type": "clip_peaks", "reqs": [r0, r1, r2]})
        assert batch["type"] == "clip_peaks" and len(batch["peaks"]) == 3, batch
        for i, (got_b, got_1) in enumerate(zip(batch["peaks"], alone)):
            for k in ("track", "src_track", "src_offset", "len", "bins", "min", "max"):
                assert got_b[k] == got_1[k], (i, k, got_b.get(k), got_1.get(k))
        ok("clip_peaks: three requests in one message, each answer identical to "
           "the same request asked alone")
        # the control for "identical": the three answers are not each other, so
        # a batch that returned element 0 three times would fail above
        assert len({len(p["min"]) for p in batch["peaks"]}) == 3, batch
        assert batch["peaks"][0]["min"] != batch["peaks"][1]["min"], batch
        assert batch["peaks"][2]["max"] == [0.0] * 4, batch
        ok("clip_peaks: the batch is in request order (16, 8, 4 bins; the last flat)")
        # A bad element fails the whole batch and names its index -- nothing
        # partial comes back, because the caller built every element in one
        # pass from its own geometry.
        bad = h.req({"type": "clip_peaks", "reqs": [r0, {"track": 9, "len": 10}]})
        assert bad["type"] == "error" and bad["code"] == "bad_request", bad
        assert "reqs[1]" in bad["message"], bad
        assert "peaks" not in bad, bad
        ok("clip_peaks: one bad element refuses the batch, naming reqs[1]")
        for shape in ([], "nope", [r0, 7]):
            bad = h.req({"type": "clip_peaks", "reqs": shape})
            assert bad["type"] == "error" and bad["code"] == "bad_request", (shape, bad)
        ok("clip_peaks: an empty, non-array or non-object reqs is refused")
        assert len(h.ok({"type": "clip_peaks", "reqs": [r2] * 64})["peaks"]) == 64
        over = h.req({"type": "clip_peaks", "reqs": [r2] * 65})
        assert over["type"] == "error" and over["code"] == "bad_request", over
        ok("clip_peaks: 64 requests answer, 65 are refused (the reply is built whole)")

        # ---- the clip the UI keeps is geometry ----------------------------
        # Put take 1 twice on track 0 and once on track 1, reading track 0's
        # medium (LIFT on one track, DROP on another).
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": first["src_offset"], "gain": 1.0},
            {"start_frame": TAKE, "len": TAKE, "src_offset": first["src_offset"], "gain": 1.0}]})
        h.ok({"type": "set_clips", "track": 1, "clips": [
            {"start_frame": 2 * TAKE, "len": TAKE, "src_offset": first["src_offset"],
             "src_track": 0, "gain": 1.0}]})
        st = h.c.state()["tape"]["tracks"][1]["clips"][0]
        assert st["src_track"] == 0, st
        both = play(h, 3 * TAKE, "moved.wav")
        one = [both[0][f] for f in range(TAKE)]
        two = [both[0][f] for f in range(TAKE, 2 * TAKE)]
        three = [both[0][f] for f in range(2 * TAKE, 3 * TAKE)]
        assert db(peak_error([one], [two])) <= -100.0
        assert db(peak_error([one], [three])) <= -100.0
        ok("clips: the same audio at three places, one of them on another track")

        # ---- tape_edit: REV / SPEED / FILTER tapped while stopped ---------
        rev = h.ok({"type": "tape_edit", "track": 0, "op": "reverse", "len": TAKE,
                    "sources": [{"track": 0, "offset": first["src_offset"], "len": TAKE}]})
        assert rev["len"] == TAKE and rev["src_offset"] >= used, rev
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": rev["src_offset"], "gain": 1.0}]})
        h.ok({"type": "set_clips", "track": 1, "clips": []})
        back = play(h, TAKE, "reversed.wav")
        assert db(peak_error([back[0]], [one[::-1]])) <= -100.0
        ok("tape_edit reverse: the clip plays backwards, sample for sample")

        spd = h.ok({"type": "tape_edit", "track": 0, "op": "speed", "factor": 2.0,
                    "len": TAKE,
                    "sources": [{"track": 0, "offset": first["src_offset"], "len": TAKE}]})
        assert spd["len"] == TAKE // 2, spd
        ok("tape_edit speed: x2 halves the clip (%d frames)" % spd["len"])

        flt = h.ok({"type": "tape_edit", "track": 0, "op": "filter", "cutoff": 300.0,
                    "mode": "LP", "len": TAKE,
                    "sources": [{"track": 0, "offset": first["src_offset"], "len": TAKE}]})
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": flt["len"], "src_offset": flt["src_offset"], "gain": 1.0}]})
        dull = play(h, TAKE, "dull.wav")

        def roughness(ch):
            return max(abs(ch[f + 1] - ch[f]) for f in range(len(ch) - 1))
        assert roughness(dull[0]) < 0.5 * roughness(one), (roughness(dull[0]), roughness(one))
        ok("tape_edit filter: the baked clip is duller than the source")

        # TAPE (merge): two sources into one run, the second gain-scaled.
        mg = h.ok({"type": "tape_edit", "track": 0, "op": "copy", "len": 2 * TAKE,
                   "sources": [{"track": 0, "offset": first["src_offset"], "len": TAKE, "at": 0},
                               {"track": 0, "offset": second["src_offset"], "len": TAKE,
                                "at": TAKE, "gain": 0.5}]})
        assert mg["len"] == 2 * TAKE, mg
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": mg["len"], "src_offset": mg["src_offset"], "gain": 1.0}]})
        joined = play(h, 2 * TAKE, "joined.wav")
        assert db(peak_error([[joined[0][f] for f in range(TAKE)]], [one])) <= -100.0
        ok("tape_edit copy: several sources mix into one run at their own gains")

        # A take still open owns the head, so an edit is refused rather than
        # writing where the take is about to.
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 2, "on": True})
        h.render(512)
        busy = h.req({"type": "tape_edit", "track": 2, "op": "copy", "len": 100,
                      "sources": [{"track": 0, "offset": 0, "len": 100}]})
        assert busy["type"] == "error" and busy["message"] == "RECORDING", busy
        h.ok({"type": "record", "track": 2, "on": False})
        h.render(512)
        h.ok({"type": "transport", "op": "stop"})
        ok("tape_edit: refused while a take is open")

        # ---- tape_save / tape_load (user story 18) ------------------------
        wav = h.path("medium0.f32.wav")
        saved = h.ok({"type": "tape_save", "track": 0, "file": wav})
        assert saved["frames"] > 0 and os.path.exists(wav), saved
        ok("tape_save: %d frames of medium written" % saved["frames"])

    # a fresh host, the medium loaded back, the same clip geometry: the same sound
    with Host(a.exe, [a.boxes], block=128) as h2:
        loaded = h2.ok({"type": "tape_load", "track": 0, "file": wav})
        assert loaded["frames"] == saved["frames"], (loaded, saved)
        h2.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": first["src_offset"], "gain": 1.0}]})
        restored = play(h2, TAKE, "restored.wav")
        assert db(peak_error([restored[0]], [one])) <= -100.0
        ok("tape_load: a restart plays the same take from the same clip")
        st = h2.c.state()["tape"]["tracks"][0]
        assert st["used_frames"] == saved["frames"], st
        ok("tape_load: the head lands past what was loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
