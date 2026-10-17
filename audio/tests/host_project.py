#!/usr/bin/env python3
"""Stage J1: the bounce — `clip_to_asset {track:"all", master_fx}`.

A mixdown deliberately leaves COMP and the master FX rack out (PROTOCOL.md
§ "Tape and mixer", stage F1): COMP is a live performance knob, and whether
the rack is printed is a choice the player makes at the moment of the bounce.
FILE › BOUNCE's SHIFT+ENC4 is that choice, and `master_fx` is the message.

What is worth proving:

  * with **no** rack, or a **bypassed** one, `master_fx` is bit-exact identical
    to the ordinary mixdown. The default path must not be coloured by the
    existence of a switch.
  * with a rack, it is the rack — measured as a bin, not as "different".
  * a rack **with latency** still lands on the timeline. `arbutus/drive` is the
    first box in the instrument that declares any (15 frames, §12: the host
    reports it and compensates nothing live), so a bounce that ignored it would
    print the whole song 15 frames late and the file would be 15 frames long
    where it should be `len`.
  * `master_fx` on a single track is ignored: one track hands back what it
    holds (DDR-030's sampler drop), and the master rack is not on it.
  * the project's other half — `tape_save` / `tape_load` — survives a round
    trip through a file, which is how a project's media reach disk.

D2 (#23) adds the *app's* bounce, which is `bake` with the same switch plus a
trim (`companion-app-spec.md` §4): the file starts at the first clip and ends
at the last, so a phone downloading a bounce gets no leading silence and no
tail of nothing. `test_bounce_trims_to_the_clips` pins that.
"""

import cmath
import math
import os
import struct
import sys

from hostcase import Host, args, compare, db, differs, ok, read_wav

SR = 48000
N = 24000                               # 0.5 s; 100 Hz and 5 kHz are exact bins
LOW_HZ, HIGH_HZ = 100.0, 5000.0
IMPULSE_AT = 4000                       # where the click sits on the timeline


def write_f32(path, left, right, rate=SR):
    data = bytearray()
    for l, r in zip(left, right):
        data += struct.pack("<ff", l, r)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, rate, rate * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", len(data)) + bytes(data))


def tones(n=N, amp=0.4):
    x = [amp * math.sin(2 * math.pi * LOW_HZ * i / SR) +
         amp * math.sin(2 * math.pi * HIGH_HZ * i / SR) for i in range(n)]
    return x, list(x)


def click(n=N, amp=0.6):
    x = [0.0] * n
    x[IMPULSE_AT] = amp
    return x, list(x)


def mag(x, hz, rate=SR):
    w = 2 * math.pi * hz / rate
    acc = sum(v * cmath.exp(-1j * w * i) for i, v in enumerate(x))
    return 2.0 * abs(acc) / len(x)


def argpeak(x):
    best, at = 0.0, 0
    for i, v in enumerate(x):
        if abs(v) > best:
            best, at = abs(v), i
    return at


def load_tape(h, track, path, frames=N):
    h.ok({"type": "tape_load", "track": track, "file": path})
    h.ok({"type": "set_clips", "track": track, "clips": [
        {"start_frame": 0, "len": frames, "src_offset": 0, "gain": 1.0}]})


def bounce(h, name, master_fx=False, track="all", start=0, length=N):
    path = h.path(name)
    r = h.ok({"type": "clip_to_asset", "track": track, "start_frame": start,
              "len": length, "file": path, "master_fx": master_fx})
    assert r["path"] == path and r["frames"] == length, r
    return path


LOWPASS = {"format": 1, "kind": "rack", "name": "MASTER",
           "tabs": [{"instance": "lp", "engine": "arbutus/biquad",
                     "params": {"cutoff": 0.35, "q": 0.5}}]}


# ---------------------------------------------------------------------------
def test_switch_is_off_by_default(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)

        dry = bounce(h, "dry.wav")
        same = bounce(h, "no-rack.wav", master_fx=True)
        compare(dry, same, -100.0, "master_fx with no rack")
        ok("bounce: MASTER FX with an empty master rack is the mixdown, sample "
           "for sample")

        h.ok({"type": "load_rack", "instrument": "master", "rack": LOWPASS})
        off = bounce(h, "rack-off.wav")
        compare(dry, off, -100.0, "rack loaded, switch off")
        ok("bounce: with the switch off the rack is not printed, however loud "
           "it is")

        # The field is `bypass`; it defaults to true, so the off case is the
        # one that proves it was read (#599).
        h.ok({"type": "set_bypass", "instrument": "master", "bypass": True})
        assert h.c.state()["mixer"]["fx"]["bypass"] is True
        byp = bounce(h, "rack-bypass.wav", master_fx=True)
        compare(dry, byp, -100.0, "rack bypassed, switch on")
        ok("bounce: a bypassed rack prints nothing — bypass is bypass on both "
           "paths")
        h.ok({"type": "set_bypass", "instrument": "master", "bypass": False})
        assert h.c.state()["mixer"]["fx"]["bypass"] is False, h.c.state()["mixer"]["fx"]
        on = bounce(h, "rack-on.wav", master_fx=True)
        differs(dry, on, -40.0, "rack back in, switch on")
        ok("bounce: bypass off and the switch on prints the rack again")
    return 0


def test_the_rack_is_printed(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)
        h.ok({"type": "load_rack", "instrument": "master", "rack": LOWPASS})

        _, dry = read_wav(bounce(h, "dry.wav"))
        _, wet = read_wav(bounce(h, "wet.wav", master_fx=True))
        d_lo, d_hi = mag(dry[0], LOW_HZ), mag(dry[0], HIGH_HZ)
        w_lo, w_hi = mag(wet[0], LOW_HZ), mag(wet[0], HIGH_HZ)
        assert db(w_hi / d_hi) < -20.0, db(w_hi / d_hi)
        assert abs(db(w_lo / d_lo)) < 3.0, db(w_lo / d_lo)
        ok("bounce: MASTER FX prints the lowpass — 5 kHz down %.1f dB, 100 Hz "
           "within %.1f dB" % (db(w_hi / d_hi), abs(db(w_lo / d_lo))))

        # A macro moved on the live rack is the sound, and a bounce is of the
        # sound: the offline copy is built from the same document and the same
        # macro positions.
        h.ok({"type": "set_param", "instrument": "master", "slot": "fx",
              "tab": 0, "idx": 0, "value": 1.0})
        _, open_ = read_wav(bounce(h, "open.wav", master_fx=True))
        assert abs(db(mag(open_[0], HIGH_HZ) / d_hi)) < 3.0, \
            db(mag(open_[0], HIGH_HZ) / d_hi)
        ok("bounce: the offline rack is the live document — opening the filter "
           "opens it in the bounce")

        # One track is that track's audio, not a mix of it (DDR-030), so the
        # master rack has nothing to do with it and the switch is ignored.
        one = bounce(h, "track1.wav", track=0)
        one_fx = bounce(h, "track1-fx.wav", track=0, master_fx=True)
        compare(one, one_fx, -100.0, "master_fx on one track")
        ok("bounce: MASTER FX on a single track is ignored — one track is not "
           "a mixdown")
    return 0


def test_latency_is_compensated(a):
    """`arbutus/drive` declares 15 frames. The bounce must still line up."""
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        write_f32(src, *click())
        load_tape(h, 0, src)

        _, dry = read_wav(bounce(h, "dry.wav"))
        at_dry = argpeak(dry[0])
        assert at_dry == IMPULSE_AT, at_dry

        st = h.c.state()
        h.ok({"type": "load_rack", "instrument": "master", "rack": {
            "format": 1, "kind": "rack", "name": "MASTER",
            "tabs": [{"instance": "dr", "engine": "arbutus/drive",
                      "params": {"drive": 0.05, "mix": 1.0}}]}})
        st = h.c.state()
        lat = st["mixer"]["tabs"][0]["desc"]["latency_frames"]
        assert lat == 15, "expected arbutus/drive to declare 15 frames, got %r" % lat

        path = bounce(h, "drive.wav", master_fx=True)
        _, wet = read_wav(path)
        assert len(wet[0]) == N, "bounce is %d frames, wanted %d" % (len(wet[0]), N)
        at_wet = argpeak(wet[0])
        assert abs(at_wet - IMPULSE_AT) <= 3, \
            "impulse moved %d frames (rack latency %d)" % (at_wet - IMPULSE_AT, lat)
        ok("bounce: a rack declaring %d frames of latency still lands the "
           "transient at %d (moved %d), and the file is exactly %d frames"
           % (lat, IMPULSE_AT, at_wet - IMPULSE_AT, len(wet[0])))
    return 0


def test_tape_round_trip(a):
    """The project's media: `tape_save` out, `tape_load` back, same audio."""
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)
        before = bounce(h, "before.wav", track=0)

        saved = h.path("medium-1.wav")
        r = h.ok({"type": "tape_save", "track": 0, "file": saved})
        assert r["frames"] == N, r
        assert os.path.exists(saved)

        with Host(a.exe, [a.boxes], block=128) as g:
            g.ok({"type": "tape_load", "track": 0, "file": saved})
            st = g.c.state()
            assert st["tape"]["tracks"][0]["used_frames"] == N, st["tape"]["tracks"][0]
            g.ok({"type": "set_clips", "track": 0, "clips": [
                {"start_frame": 0, "len": N, "src_offset": 0, "gain": 1.0}]})
            after = g.path("after.wav")
            g.ok({"type": "clip_to_asset", "track": 0, "start_frame": 0,
                  "len": N, "file": after})
            compare(before, after, -80.0, "tape_save -> tape_load")
        ok("project media: a medium saved and loaded into a second host is the "
           "same audio, with the head put back past it")
    return 0


def test_tape_save_is_crash_safe(a):
    """#93 / § 7.3: a refused `tape_save` leaves the previous save whole.

    The medium a `tape_save` writes is the only on-disk copy of that take, so
    the write is temp-then-rename (`hostlib/audiofile.cpp::write_durable`,
    proved on its own in `ctest -R audiofile`). What is proved *here* is the
    wiring — that this message really takes that path — because a unit test of
    a function nobody calls proves nothing.

    The fault is a directory sitting where the temp file has to go: no
    permissions to arrange, no full disk, and the same shape as ENOSPC.
    """
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)

        saved = h.path("durable.wav")
        h.ok({"type": "tape_save", "track": 0, "file": saved})
        assert not os.path.exists(saved + ".tmp"), "a .tmp survived a good save"
        with open(saved, "rb") as f:
            before = f.read()

        os.mkdir(saved + ".tmp")
        r = h.req({"type": "tape_save", "track": 0, "file": saved})
        assert r.get("type") == "error", r
        with open(saved, "rb") as f:
            assert f.read() == before, "a refused tape_save damaged the save"
        os.rmdir(saved + ".tmp")
        ok("tape_save is temp-then-rename: a write it cannot start refuses "
           "(%s) and the previous %d-byte medium is untouched"
           % (r.get("code", "?"), len(before)))
    return 0


# ---------------------------------------------------------------------------
# D2 (#23): `bake {trim, master_fx}` — the companion app's bounce
# ---------------------------------------------------------------------------

def test_bounce_trims_to_the_clips(a):
    """companion-app-spec §4: first clip start to last clip end, no silence."""
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        at = 3 * SR                     # three seconds of nothing in front
        h.ok({"type": "tape_load", "track": 0, "file": src})
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": at, "len": N, "src_offset": 0, "gain": 1.0}]})

        # Untrimmed, `bake` is what it has always been: frame 0 to the end of
        # the last clip, silence and all.
        whole = h.path("whole.wav")
        r = h.ok({"type": "bake", "file": whole, "format": "wav24"})
        assert r["start_frame"] == 0 and r["frames"] == at + N, r
        ok("bake without `trim` is unchanged: frame 0 to the last clip end, "
           "and the reply now says which window that was")

        cut = h.path("cut.wav")
        r = h.ok({"type": "bake", "file": cut, "format": "wav24", "trim": True})
        assert r["start_frame"] == at, r
        assert r["frames"] == N, r
        _, audio = read_wav(cut)
        assert len(audio[0]) == N, len(audio[0])
        # The first sample is the clip's first sample, not silence.
        assert abs(audio[0][0]) < 0.01 and abs(audio[0][120]) > 0.05, \
            (audio[0][0], audio[0][120])
        assert mag(audio[0], LOW_HZ) > 0.2, mag(audio[0], LOW_HZ)
        ok("bake {trim} starts at the first clip (%d) and is exactly the clip "
           "span (%d frames) — a phone downloading a bounce gets no leading "
           "silence" % (at, N))

        assert h.req({"type": "bake", "file": cut, "trim": True,
                      "frames": 100})["type"] == "error"
        ok("`trim` and `frames` together is refused rather than one quietly "
           "winning")

        # MASTER FX on a trimmed bounce: the rack rings on past the last clip,
        # so the file grows by exactly the tail §4 asks for (latency + 2 s).
        h.ok({"type": "load_rack", "instrument": "master", "rack": {
            "format": 1, "kind": "rack", "name": "MASTER",
            "tabs": [{"instance": "dr", "engine": "arbutus/drive",
                      "params": {"drive": 0.05, "mix": 1.0}}]}})
        lat = h.c.state()["mixer"]["tabs"][0]["desc"]["latency_frames"]
        fx = h.path("fx.wav")
        r = h.ok({"type": "bake", "file": fx, "format": "wav24",
                  "trim": True, "master_fx": True})
        assert r["start_frame"] == at, r
        assert r["frames"] == N + lat + 2 * SR, (r, lat)
        _, wet = read_wav(fx)
        assert len(wet[0]) == N + lat + 2 * SR, len(wet[0])
        # Latency-compensated, so the clip still starts where it starts.
        assert abs(wet[0][0]) < 0.01 and abs(wet[0][120]) > 0.05, \
            (wet[0][0], wet[0][120])
        ok("bake {trim, master_fx} keeps the rack's tail — %d frames of "
           "latency plus two seconds — and still lands the clip on frame 0"
           % lat)
    return 0


# ---------------------------------------------------------------------------
# #920: BOUNCE as a job — `clip_to_asset {job: true}`, `bounce_cancel`
# ---------------------------------------------------------------------------
DRIVE_LP = {"format": 1, "kind": "rack", "name": "MASTER",
            "tabs": [{"instance": "dr", "engine": "arbutus/drive",
                      "params": {"drive": 0.4, "mix": 1.0}},
                     {"instance": "lp", "engine": "arbutus/biquad",
                      "params": {"cutoff": 0.35, "q": 0.5}}]}
CHUNK = SR // 4                         # the host renders this much a turn


def start_job(h, name, master_fx=True):
    path = h.path(name)
    r = h.ok({"type": "clip_to_asset", "track": "all", "start_frame": 0,
              "len": N, "file": path, "master_fx": master_fx, "job": True})
    assert r.get("status") == "started" and r["path"] == path, r
    return path


def test_bounce_as_a_job(a):
    """The render a chunk per `render` offline; the file only at its end.

    The same mixdown as the one-call bounce, through a rack with latency
    (`arbutus/drive`, 15 frames) so the chunked rack and the dropped frames
    are both in it; nothing on the card until the last chunk; the result as a
    `bounce` push. Then a cancel: nothing written, ever, and a second job
    refused while one runs.
    """
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)
        h.ok({"type": "load_rack", "instrument": "master", "rack": DRIVE_LP})
        whole = bounce(h, "whole.wav", master_fx=True)

        h.c.clear_pushes()
        path = start_job(h, "job.wav")
        h.render(128)
        h.c.drain(0.1)
        assert not os.path.exists(path), "a job wrote its file after one chunk"
        assert not h.c.pushes("bounce"), h.c.pushes("bounce")
        busy = h.req({"type": "clip_to_asset", "track": "all", "start_frame": 0,
                      "len": N, "file": h.path("two.wav"), "job": True})
        assert busy.get("type") == "error" and busy.get("message") == "BUSY", busy
        turns = 1
        while not h.c.pushes("bounce") and turns < 10:
            h.render(128)
            h.c.drain(0.05)
            turns += 1
        done = h.c.wait_push("bounce", timeout=2.0)
        assert done and done.get("path") == path and done.get("frames") == N, done
        assert turns == -(-(N + 15) // CHUNK), "took %d renders" % turns
        compare(whole, path, -100.0, "bounce job against the one-call bounce")
        ok("bounce job: %d renders of a %d-frame chunk, the file only at the "
           "end, the same mixdown as one call through a rack with latency, "
           "and a second job refused BUSY" % (turns, CHUNK))

        h.c.clear_pushes()
        path = start_job(h, "cancelled.wav")
        h.render(128)
        r = h.ok({"type": "bounce_cancel"})
        assert r.get("running") is True, r
        got = h.c.wait_push("bounce", timeout=2.0)
        assert got and got.get("cancelled") is True, got
        for _ in range(4):
            h.render(128)
        assert not os.path.exists(path), "a cancelled job wrote its file"
        r = h.ok({"type": "bounce_cancel"})
        assert r.get("running") is False, r
        ok("bounce job: bounce_cancel pushes `cancelled`, writes nothing and "
           "says `running: false` when there is no job")
    return 0


def main():
    a = args()
    for t in (test_switch_is_off_by_default, test_the_rack_is_printed,
              test_latency_is_compensated, test_tape_round_trip,
              test_tape_save_is_crash_safe, test_bounce_trims_to_the_clips,
              test_bounce_as_a_job):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
