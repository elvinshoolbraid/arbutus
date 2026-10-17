#!/usr/bin/env python3
"""The write-behind and the bounded pool through the whole host (C1 steps 2a and
2b, #74).

test_tape_io.cpp and test_tape_pool.cpp prove the I/O thread against its own
fakes. This proves the wiring: that a take recorded through the protocol reaches
`--tape-dir` as a WAV, byte for byte the same medium the RAM tape holds, that
with no `--tape-dir` none of it happens, and that a take **longer than the RAM
window** lands identically — which is the whole claim of step 2b, made against
an independent copy rather than against itself.

Deliberately small on purpose: the first take is two and a half ranges, which is
the only size that exercises a whole-range commit, a second one, and the partial
tail with its exact header. The pool case is eight ranges against a
three-second window, so most of the medium has to be evicted and fetched back.
"""

import os
import shutil
import sys
import tempfile

from hostcase import Host, args, ok, read_wav_arrays

RANGE = 49152                      # Tape::kRangeFrames, 1.024 s at 48 kHz
TAKE = RANGE * 2 + 12000           # two whole ranges and a partial third


def main():
    a = args()
    tmp = tempfile.mkdtemp(prefix="arbutus-tapeio-test-")
    try:
        media = os.path.join(tmp, "media")
        with Host(a.exe, [a.boxes], extra=["--tape-dir", media]) as h:
            st = h.c.state()
            io = st["tape"]["io"]
            assert io is not None, st["tape"]
            assert io["dir"] == media, io
            assert io["range_frames"] == RANGE, io
            assert io["committed"] == 0 and io["queued"] == 0, io
            ok("tape io: --tape-dir is reported in state before anything is recorded")

            h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
            h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})
            h.ok({"type": "transport", "op": "locate", "position": 0})
            h.ok({"type": "transport", "op": "start"})
            h.ok({"type": "record", "track": 0, "on": True})
            h.note(60, True, 0.9)
            h.note(67, True, 0.8)
            h.render(TAKE)
            h.ok({"type": "record", "track": 0, "on": False})
            h.render(512)

            st = h.c.state()
            used = st["tape"]["tracks"][0]["used_frames"]
            io = st["tape"]["io"]
            assert used >= TAKE, (used, TAKE)
            # Two full ranges plus the tail commit that ends the take.
            assert io["committed"] == 3, io
            assert io["queued"] == io["committed"], io
            assert io["dropped"] == 0 and io["errors"] == 0 and io["absent"] == 0, io
            assert io["syncs"] == 3, io
            ok("tape io: %d ranges committed, %d syncs, nothing dropped"
               % (io["committed"], io["syncs"]))

            # What the bench reads on the CM4 (design § 6.2): the commit's own
            # wall time, from the production path. Here it only has to be
            # present, counted and ordered -- the number is the eMMC's.
            cu = io["commit_us"]
            assert cu["count"] == io["committed"], cu
            assert cu["p50"] <= cu["p99"] <= cu["max"], cu
            # The counts are what a short take can actually support (#158):
            # `p99` of 121 commits is the second-largest sample, and reading it
            # as a percentile is what made step 2 blame the growing file.
            assert 0 <= cu["over_100ms"] <= cu["over_20ms"] <= cu["count"], cu
            # The `fdatasync` alone, out of the same commits: it is inside the
            # commit, so it cannot cost more than one.
            su = io["sync_us"]
            assert su["count"] == cu["count"], (su, cu)
            assert su["max"] <= cu["max"], (su, cu)
            assert su["p50"] <= su["p99"] <= su["max"], su
            ok("tape io: commit p50 %d us, max %d us, of which fdatasync p50 %d us "
               "(a tmpfs here)" % (cu["p50"], cu["max"], su["p50"]))

            # The medium on disk, against the medium in RAM. `tape_save` writes
            # what the RAM tape holds through the old path, so the two files
            # agreeing is the write-behind having written the take and not
            # something adjacent to it.
            live = os.path.join(media, "tape-1.wav")
            assert os.path.exists(live), os.listdir(media)
            saved = h.path("saved.wav")
            r = h.ok({"type": "tape_save", "track": 0, "file": saved})
            assert r["frames"] == used, (r, used)

            rate_a, live_ch = read_wav_arrays(live)
            rate_b, saved_ch = read_wav_arrays(saved)
            assert rate_a == rate_b == 48000, (rate_a, rate_b)
            assert len(live_ch) == 2, len(live_ch)
            assert len(live_ch[0]) == used, (len(live_ch[0]), used)
            wrong = sum(1 for c in range(2) for i in range(used)
                        if live_ch[c][i] != saved_ch[c][i])
            assert wrong == 0, "%d frames differ between the card and RAM" % wrong
            ok("tape io: tape-1.wav is the medium, %d frames, sample-exact" % used)

            # A track never recorded has no file, as `audio/library/README.md`
            # § Projects says. The recorded one has two: the medium and the
            # peaks sidecar TRACK draws from (#163), written by the commit that
            # ended the take.
            assert sorted(os.listdir(media)) == ["tape-1.peaks", "tape-1.wav"], \
                os.listdir(media)
            ok("tape io: only the recorded track has a medium, and it has its sidecar")
            pk = io["peaks"]
            assert pk["on"] is True, pk
            assert pk["bins_per_range"] * pk["bin_frames"] == io["range_frames"], pk
            # One range of bins for each range the take committed.
            assert pk["ranges"] == 3, pk
            # 32 B of header, 16 min/max pairs a range as float32, one validity
            # byte a range -- so the file's size is a statement about its shape.
            want = 32 + 3 * pk["bins_per_range"] * 2 * 4 + 3
            got = os.path.getsize(os.path.join(media, "tape-1.peaks"))
            assert got == want, (got, want)
            ok("tape io: the sidecar is %d bytes for 3 ranges of bins" % got)

            # And the wiring: `clip_peaks` answers out of those bins when the
            # question is at or below their resolution, and says which path it
            # took. Two whole ranges in 32 bins is exactly one sidecar bin each.
            span = 2 * RANGE
            coarse = h.ok({"type": "clip_peaks", "track": 0, "src_offset": 0,
                           "len": span, "bins": 32})
            assert coarse["source"] == "sidecar", coarse["source"]
            fine = h.ok({"type": "clip_peaks", "track": 0, "src_offset": 0,
                         "len": span, "bins": 1024})
            assert fine["source"] == "audio", fine["source"]
            pk = h.c.state()["tape"]["io"]["peaks"]
            assert pk["hit"] == 1 and pk["miss"] == 1, pk
            ok("tape io: clip_peaks answers 32 bins from the sidecar and 1024 from the audio")
            sidecar_answer = (coarse["min"], coarse["max"])

            # The lead (§ 3.4, #158): the filesystem is asked for blocks a few
            # ranges past the range being committed, so the commit writes into
            # space that already exists. Here it only has to be on, to have
            # been asked for, and to have left the file alone -- the number it
            # is there to move is the eMMC's p99, which is the bench's.
            pa = io["prealloc"]
            assert pa["on"] is True, pa
            assert pa["lead_ranges"] >= 1, pa
            if not sys.platform.startswith("linux"):
                # `fallocate` is Linux's (tape_store.cpp): elsewhere the lead is
                # refused and the take grows the file as before -- the byte
                # comparison below is what says that cost nothing.
                assert pa["refused"] >= 1 and pa["bytes"] == 0, pa
                ok("tape io: the lead is refused off Linux, as it must be")
            else:
                assert pa["refused"] == 0, pa
                # One call per commit that carried audio -- all three here --
                # and the last of them reached `lead_ranges` past the last
                # range written, which is the claim: the `pwrite` lands in
                # blocks that already exist.
                assert pa["calls"] == io["committed"], (pa, io["committed"])
                assert pa["bytes"] == 4096 + (io["committed"] + pa["lead_ranges"]) * RANGE * 8, pa
                ok("tape io: the lead asked for %d ranges of blocks in %d calls"
                   % (pa["bytes"] // (RANGE * 8), pa["calls"]))
            lead_on_bytes = open(live, "rb").read()

        # The *before* switch, which is the negative control at the host's own
        # level: `--no-tape-prealloc` never asks, and the medium it leaves is
        # the same file byte for byte. If the lead could change the take, this
        # is where it would show.
        media_np = os.path.join(tmp, "noprealloc")
        with Host(a.exe, [a.boxes],
                  extra=["--tape-dir", media_np, "--no-tape-prealloc"]) as h:
            h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
            h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})
            h.ok({"type": "transport", "op": "locate", "position": 0})
            h.ok({"type": "transport", "op": "start"})
            h.ok({"type": "record", "track": 0, "on": True})
            h.note(60, True, 0.9)
            h.note(67, True, 0.8)
            h.render(TAKE)
            h.ok({"type": "record", "track": 0, "on": False})
            h.render(512)
            pa = h.c.state()["tape"]["io"]["prealloc"]
            assert pa["on"] is False, pa
            assert pa["calls"] == 0 and pa["bytes"] == 0 and pa["refused"] == 0, pa
            same = open(os.path.join(media_np, "tape-1.wav"), "rb").read()
            assert same == lead_on_bytes, (len(same), len(lead_on_bytes))
            ok("tape io: --no-tape-prealloc asks for nothing and leaves the same medium")

        # #163's *before* switch, and the agreement that matters: the same
        # question asked of a host that keeps **no** sidecar is answered by
        # scanning the samples, and it has to come back with the same numbers.
        # The bins line up with the sidecar's own, so "the same" means equal and
        # not close -- and this is the one check the sidecar cannot satisfy by
        # reading itself back.
        media_pk = os.path.join(tmp, "nopeaks")
        with Host(a.exe, [a.boxes],
                  extra=["--tape-dir", media_pk, "--no-tape-peaks"]) as h:
            h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
            h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})
            h.ok({"type": "transport", "op": "locate", "position": 0})
            h.ok({"type": "transport", "op": "start"})
            h.ok({"type": "record", "track": 0, "on": True})
            h.note(60, True, 0.9)
            h.note(67, True, 0.8)
            h.render(TAKE)
            h.ok({"type": "record", "track": 0, "on": False})
            h.render(512)
            pk = h.c.state()["tape"]["io"]["peaks"]
            assert pk["on"] is False and pk["ranges"] == 0, pk
            assert not os.path.exists(os.path.join(media_pk, "tape-1.peaks")), media_pk
            got = h.ok({"type": "clip_peaks", "track": 0, "src_offset": 0,
                        "len": 2 * RANGE, "bins": 32})
            assert got["source"] == "audio", got["source"]
            bad = [b for b in range(32)
                   if got["min"][b] != sidecar_answer[0][b]
                   or got["max"][b] != sidecar_answer[1][b]]
            assert not bad, (bad, [(got["min"][b], got["max"][b],
                                    sidecar_answer[0][b], sidecar_answer[1][b])
                                   for b in bad[:3]])
            # Nothing counted either way: the *before* side of the switch has to
            # read as zero of both, not as a cold cache.
            assert pk["hit"] == 0 and pk["miss"] == 0, pk
            ok("tape io: --no-tape-peaks scans the samples and gets the sidecar's "
               "32 bins exactly")

        # The other half of the switch: no --tape-dir, no thread, nothing on
        # disk, and `state.tape.io` says so rather than reading as an idle one.
        with Host(a.exe, [a.boxes]) as h:
            st = h.c.state()
            assert st["tape"]["io"] is None, st["tape"]
            ok("tape io: without --tape-dir state.tape.io is null")

        # ---- step 2b: the same take against a RAM window a third its length --
        # The medium a bounded pool leaves on the card must be the medium an
        # unbounded one leaves. Rendering is deterministic (same boxes, same
        # notes, same `--seed`), so the two files are comparable, and comparing
        # them is the one check that cannot be satisfied by reading the answer
        # back out of the file under test.
        big = RANGE * 8
        media_a = os.path.join(tmp, "whole")   # the default 120 s window
        media_b = os.path.join(tmp, "window")
        stats = {}
        saved = {}
        for name, extra in (("whole", ["--tape-dir", media_a]),
                            ("window", ["--tape-dir", media_b,
                                        "--tape-pool-seconds", "3"])):
            with Host(a.exe, [a.boxes], extra=extra) as h:
                h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
                h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2,
                      "mode": "poly"})
                h.ok({"type": "transport", "op": "locate", "position": 0})
                h.ok({"type": "transport", "op": "start"})
                h.ok({"type": "record", "track": 0, "on": True})
                h.note(60, True, 0.9)
                h.note(67, True, 0.8)
                h.render(big)
                h.ok({"type": "record", "track": 0, "on": False})
                h.render(512)
                # `tape_save` reads the whole medium back. With a three-slot
                # window most of it is no longer in RAM, so this is the fetch
                # path through the real host — and the file it writes is what
                # the two runs are compared on.
                saved[name] = os.path.join(tmp, name + "-saved.wav")
                r = h.ok({"type": "tape_save", "track": 0, "file": saved[name]})
                assert r["frames"] >= big, (r, big)
                stats[name] = h.c.state()["tape"]

        io = stats["window"]["io"]
        pool = io["pool"]
        assert pool["slots"] == 3, pool
        assert pool["resident"] <= 3, pool
        # Vacuously green otherwise: without both of these the window was never
        # smaller than the take.
        assert pool["evicted"] > 0, pool
        assert pool["fetched"] > 0, pool
        assert io["absent"] == 0 and io["errors"] == 0, io
        assert stats["window"]["tracks"][0]["used_frames"] == \
            stats["whole"]["tracks"][0]["used_frames"], stats
        # The contrast: the default window (120 track-seconds = 117 slots) is
        # wider than this take, so it evicts nothing and fetches nothing, and the
        # two runs still leave the same medium.
        whole_pool = stats["whole"]["io"]["pool"]
        assert whole_pool["slots"] == 117, whole_pool
        assert whole_pool["evicted"] == 0 and whole_pool["fetched"] == 0, whole_pool
        ok("tape io: a 3-slot window recorded 8 ranges — %d evicted, %d fetched back"
           % (pool["evicted"], pool["fetched"]))

        # Both the medium the I/O thread left on the card and the copy
        # `tape_save` read back out of it.
        for name, media in (("whole", media_a), ("window", media_b)):
            rate_l, live_ch = read_wav_arrays(os.path.join(media, "tape-1.wav"))
            rate_s, save_ch = read_wav_arrays(saved[name])
            assert rate_l == rate_s == 48000, (rate_l, rate_s)
            n = min(len(live_ch[0]), len(save_ch[0]))
            assert n >= big, (n, big)
            bad = sum(1 for c in range(2) for i in range(n)
                      if live_ch[c][i] != save_ch[c][i])
            assert bad == 0, "%s: %d frames differ between the card and tape_save" % (name, bad)
        ok("tape io: tape_save reads the medium back through the window, sample-exact")

        rate_a, whole_ch = read_wav_arrays(os.path.join(media_a, "tape-1.wav"))
        rate_b, window_ch = read_wav_arrays(os.path.join(media_b, "tape-1.wav"))
        assert rate_a == rate_b == 48000, (rate_a, rate_b)
        assert len(whole_ch[0]) == len(window_ch[0]) >= big, \
            (len(whole_ch[0]), len(window_ch[0]), big)
        wrong = sum(1 for c in range(2) for i in range(len(whole_ch[0]))
                    if whole_ch[c][i] != window_ch[c][i])
        assert wrong == 0, "%d frames differ between the window and the whole tape" % wrong
        ok("tape io: the windowed medium is the whole medium, %d frames, sample-exact"
           % len(whole_ch[0]))

        # ---- `tape_late`: the counter step 5's measurement reads (#160) ------
        # The host counts the blocks in which some track played silence for a
        # range that was not resident, and reports the total as
        # `state.tape.pool.late_total` (built by step 7; this pair of cells is
        # what #160's bench measurement reads). One script, two hosts: the
        # default window, where everything the take just wrote is still in RAM
        # and nothing can be late, and a three-slot window with the horizon off,
        # where every range past the third is a demand miss. The second is what
        # stops "late is 0" also being what a counter that never fires would
        # say.
        late = {}
        for name, extra in (
                ("resident", ["--tape-dir", os.path.join(tmp, "late-resident")]),
                ("starved", ["--tape-dir", os.path.join(tmp, "late-starved"),
                             "--tape-pool-seconds", "3",
                             "--tape-readahead-seconds", "0"])):
            with Host(a.exe, [a.boxes], extra=extra) as h:
                h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
                h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2,
                      "mode": "poly"})
                h.ok({"type": "transport", "op": "locate", "position": 0})
                h.ok({"type": "transport", "op": "start"})
                h.ok({"type": "record", "track": 0, "on": True})
                h.note(60, True, 0.9)
                h.render(big)
                h.ok({"type": "record", "track": 0, "on": False})
                h.render(512)
                used = h.c.state()["tape"]["tracks"][0]["used_frames"]
                # Play the take back from the top: `set_clips` rather than the
                # clip the take leaves, so the read is the same on both hosts.
                h.ok({"type": "set_clips", "track": 0,
                      "clips": [{"start_frame": 0, "len": used,
                                 "src_offset": 0, "gain": 1.0}]})
                h.ok({"type": "transport", "op": "locate", "position": 0})
                h.render(used)
                late[name] = h.c.state()["tape"]["pool"]["late_total"]
                # The mask is sticky and the host is what clears it, so a
                # sampler that read it without clearing would count every block
                # after the first miss for ever. Stopped, nothing reads the
                # tape: the counter must stand still.
                h.ok({"type": "transport", "op": "stop"})
                h.render(48000)
                still = h.c.state()["tape"]["pool"]["late_total"]
                assert still == late[name], (name, still, late[name])
        assert late["resident"] == 0, late
        # Vacuously green otherwise: a counter that cannot fire reports 0 too.
        assert late["starved"] > 0, late
        ok("tape io: tape_late is 0 over a resident medium and %d blocks over a "
           "3-slot window with the horizon off" % late["starved"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
