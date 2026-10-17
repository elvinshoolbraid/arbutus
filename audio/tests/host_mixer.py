#!/usr/bin/env python3
"""Stage F1: the mixer host — per-track EQ, mute/solo, MASTER, the master rack.

The source is a file, not a synth: two sine tones an octave-and-a-half apart
(100 Hz and 5 kHz) loaded straight into a track's medium, so every claim here
is one number — the magnitude of a bin — instead of "it sounds different".

What is actually worth proving:

  * flat EQ, no drive, no BAL is **bit-exact bypass**. The host must not colour
    the sound (audio/CLAUDE.md), and an EQ on every track is the easiest place
    to break that.
  * the EQ is between the tape and the mixer (§15.5): muting or EQing a track
    does not touch the medium, so `clip_to_asset` on it still hands back what
    was recorded.
  * the master rack is the §6 rack (DDR-062): load, bypass, remove, and every
    instrument-only message refused on it.
"""

import cmath
import math
import struct
import sys

from hostcase import Host, args, compare, db, differs, ok, peak, read_wav

SR = 48000
N = 24000                               # 0.5 s: 100 Hz and 5 kHz are exact bins
LOW_HZ, HIGH_HZ = 100.0, 5000.0


# ---------------------------------------------------------------------------
def write_f32(path, left, right, rate=SR):
    data = bytearray()
    for l, r in zip(left, right):
        data += struct.pack("<ff", l, r)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, rate, rate * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", len(data)) + bytes(data))


def tones(n=N, amp=0.4):
    """100 Hz + 5 kHz, both channels the same."""
    x = [amp * math.sin(2 * math.pi * LOW_HZ * i / SR) +
         amp * math.sin(2 * math.pi * HIGH_HZ * i / SR) for i in range(n)]
    return x, list(x)


def mag(x, hz, rate=SR):
    """Amplitude at `hz` (a Goertzel over the whole buffer; exact bins only)."""
    w = 2 * math.pi * hz / rate
    acc = sum(v * cmath.exp(-1j * w * i) for i, v in enumerate(x))
    return 2.0 * abs(acc) / len(x)


def rms(x):
    return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0


def play(h, frames=N, file=None):
    """Render `frames` of tape from position 0 -> (left, right)."""
    h.ok({"type": "transport", "op": "stop"})
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    r = h.ok({"type": "render", "frames": frames, "file": file})
    h.ok({"type": "transport", "op": "stop"})
    _, ch = read_wav(file)
    return ch[0], ch[1], r


def load_tape(h, track, path, frames=N):
    h.ok({"type": "tape_load", "track": track, "file": path})
    h.ok({"type": "set_clips", "track": track, "clips": [
        {"start_frame": 0, "len": frames, "src_offset": 0, "gain": 1.0}]})


def flat(h, track):
    h.ok({"type": "eq", "track": track, "low": 0, "mid": 0, "high": 0,
          "mid_f": 1000, "mid_q": 0.7, "hp": 20, "lp": 20000})


# ---------------------------------------------------------------------------
def test_defaults_and_eq(a):
    with Host(a.exe, [a.boxes]) as h:
        st = h.c.state()
        mx = st["mixer"]
        assert mx["master"] == 1.0 and mx["comp"] == 0.0, mx
        assert abs(mx["comp_rel"] - 0.12) < 1e-6, mx
        assert mx["drive"] == 0.0 and mx["drive_tone"] == 0.5 and mx["bal"] == 0.0, mx
        assert mx["fx"]["tabs"] == [] and mx["tabs"] == [], mx
        for t in st["tape"]["tracks"]:
            assert t["mute"] is False and t["solo"] is False, t
            e = t["eq"]
            assert (e["low"], e["mid"], e["high"], e["hp"], e["lp"]) == (0, 0, 0, 20, 20000), e
        ok("mixer: the defaults are a strip that does nothing (%d tracks)" % len(st["tape"]["tracks"]))

        src = h.path("src.wav")
        left, right = tones()
        write_f32(src, left, right)
        load_tape(h, 0, src)

        base_l, base_r, _ = play(h, file=h.path("base.wav"))
        b_lo, b_hi = mag(base_l, LOW_HZ), mag(base_l, HIGH_HZ)
        # The track pan law is equal power, -3 dB at centre (audio/CLAUDE.md),
        # so the mix of one centred track is the file at 1/sqrt(2). Every
        # "bypass" check below is against this render, not against the file.
        want = 0.4 / math.sqrt(2.0)
        assert abs(b_lo - want) < 0.01 and abs(b_hi - want) < 0.01, (b_lo, b_hi)
        ok("mixer: the mix of a flat strip is the medium at the pan law's centre")

        # --- the three bands ------------------------------------------------
        h.ok({"type": "eq", "track": 0, "low": 12})
        l, _, _ = play(h, file=h.path("low.wav"))
        assert db(mag(l, LOW_HZ) / b_lo) > 10.0, db(mag(l, LOW_HZ) / b_lo)
        assert abs(db(mag(l, HIGH_HZ) / b_hi)) < 1.0, db(mag(l, HIGH_HZ) / b_hi)
        ok("eq: LOW +12 dB lifts 100 Hz by %.1f dB and leaves 5 kHz alone" %
           db(mag(l, LOW_HZ) / b_lo))

        h.ok({"type": "eq", "track": 0, "low": 0})
        l, _, _ = play(h, file=h.path("flat2.wav"))
        compare(h.path("base.wav"), h.path("flat2.wav"), -100.0, "back to flat")
        ok("eq: a band returned to 0 dB is bypass again, not a filter at unity")

        h.ok({"type": "eq", "track": 0, "high": -12})
        l, _, _ = play(h, file=h.path("high.wav"))
        assert db(mag(l, HIGH_HZ) / b_hi) < -9.0, db(mag(l, HIGH_HZ) / b_hi)
        assert abs(db(mag(l, LOW_HZ) / b_lo)) < 1.0, db(mag(l, LOW_HZ) / b_lo)
        ok("eq: HIGH -12 dB cuts 5 kHz by %.1f dB" % db(mag(l, HIGH_HZ) / b_hi))
        h.ok({"type": "eq", "track": 0, "high": 0})

        h.ok({"type": "eq", "track": 0, "mid": -12, "mid_f": 5000, "mid_q": 4})
        l, _, _ = play(h, file=h.path("mid.wav"))
        assert db(mag(l, HIGH_HZ) / b_hi) < -9.0, db(mag(l, HIGH_HZ) / b_hi)
        assert abs(db(mag(l, LOW_HZ) / b_lo)) < 0.5, db(mag(l, LOW_HZ) / b_lo)
        ok("eq: MID -12 dB at MID-F 5 kHz, Q 4, cuts %.1f dB and misses 100 Hz" %
           db(mag(l, HIGH_HZ) / b_hi))
        h.ok({"type": "eq", "track": 0, "mid": 0})

        # --- HP / LP ---------------------------------------------------------
        h.ok({"type": "eq", "track": 0, "hp": 1000})
        l, _, _ = play(h, file=h.path("hp.wav"))
        assert db(mag(l, LOW_HZ) / b_lo) < -30.0, db(mag(l, LOW_HZ) / b_lo)
        assert abs(db(mag(l, HIGH_HZ) / b_hi)) < 1.5, db(mag(l, HIGH_HZ) / b_hi)
        ok("eq: HP 1 kHz drops 100 Hz by %.1f dB" % db(mag(l, LOW_HZ) / b_lo))
        flat(h, 0)

        h.ok({"type": "eq", "track": 0, "lp": 500})
        l, _, _ = play(h, file=h.path("lp.wav"))
        assert db(mag(l, HIGH_HZ) / b_hi) < -30.0, db(mag(l, HIGH_HZ) / b_hi)
        ok("eq: LP 500 Hz drops 5 kHz by %.1f dB" % db(mag(l, HIGH_HZ) / b_hi))

        # --- the knobs' limits and the document ------------------------------
        r = h.ok({"type": "eq", "track": 0, "low": 99, "mid_q": 99, "hp": 5, "lp": 99999})
        assert r["eq"]["low"] == 12 and r["eq"]["mid_q"] == 8, r["eq"]
        assert r["eq"]["hp"] == 20 and r["eq"]["lp"] == 20000, r["eq"]
        st = h.c.state()
        assert st["tape"]["tracks"][0]["eq"]["low"] == 12, st["tape"]["tracks"][0]["eq"]
        assert h.req({"type": "eq", "track": 8})["type"] == "error"
        assert h.req({"type": "eq"})["type"] == "error"
        ok("eq: values clamp to the page's ranges, land in `state`, track 8 refused")

        # --- the EQ is after the medium, not in it ---------------------------
        flat(h, 0)
        h.ok({"type": "eq", "track": 0, "lp": 500})
        r = h.ok({"type": "clip_to_asset", "track": 0, "start_frame": 0, "len": N,
                  "file": h.path("bounced.wav")})
        _, ch = read_wav(r["path"])
        assert db(mag(ch[0], HIGH_HZ) / b_hi) > -1.0, db(mag(ch[0], HIGH_HZ) / b_hi)
        ok("eq: one track's bounce is the medium, unfiltered (§15.5 puts EQ after the tape)")
        flat(h, 0)
    return 0


# ---------------------------------------------------------------------------
def stopped_after(h, name):
    """Play half the tones, stop mid-signal, render 0.1 s stopped -> its left channel."""
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "render", "frames": N // 2, "file": h.path(name + "-play.wav")})
    h.ok({"type": "transport", "op": "stop"})
    h.ok({"type": "render", "frames": SR // 10, "file": h.path(name + ".wav")})
    _, ch = read_wav(h.path(name + ".wav"))
    return ch[0]


def test_eq_tail_after_stop(a):
    """A stopped tape skips its tracks in the mix (#984, the idle audio thread),
    but not an EQ'd one: a resonant MID still rings out the note it was given
    when STOP cut the tape, as it did before the skip."""
    with Host(a.exe, [a.boxes]) as h:
        src = h.path("src.wav")
        left, right = tones()
        write_f32(src, left, right)
        load_tape(h, 0, src)

        dry = stopped_after(h, "dry")
        assert peak([dry]) == 0.0, peak([dry])
        ok("stop: a flat track is silence the block after STOP")

        h.ok({"type": "eq", "track": 0, "mid": 12, "mid_f": 100, "mid_q": 8})
        wet = stopped_after(h, "wet")
        head, tail = peak([wet[:SR // 100]]), peak([wet[-SR // 100:]])
        assert head > 0.05, head
        assert tail < head / 2, (tail, head)
        ok("stop: an EQ'd track rings out its tail (%.3f in the first 10 ms, then decays)" % head)
        flat(h, 0)
    return 0


# ---------------------------------------------------------------------------
def test_mute_solo(a):
    with Host(a.exe, [a.boxes]) as h:
        src = h.path("src.wav")
        left, right = tones()
        write_f32(src, left, right)
        load_tape(h, 0, src)
        load_tape(h, 1, src)

        l, _, _ = play(h, file=h.path("both.wav"))
        both = rms(l)
        assert both > 0.1, both

        h.ok({"type": "mixer", "track": 0, "mute": True})
        assert h.c.state()["tape"]["tracks"][0]["mute"] is True
        l, _, _ = play(h, file=h.path("mute0.wav"))
        assert abs(rms(l) - both / 2.0) < 0.02, (rms(l), both)
        ok("mute: track 1 muted halves the mix (DDR-061)")

        h.ok({"type": "mixer", "track": 1, "mute": True})
        l, r_, _ = play(h, file=h.path("muteboth.wav"))
        assert peak([l, r_]) == 0.0, peak([l, r_])
        ok("mute: both muted is silence")

        # Solo wins over mute: a soloed track plays even though it is muted.
        h.ok({"type": "mixer", "track": 0, "solo": True})
        l, _, _ = play(h, file=h.path("solo0.wav"))
        assert abs(rms(l) - both / 2.0) < 0.02, (rms(l), both)
        ok("solo: a soloed track plays through its own mute, the rest do not")

        h.ok({"type": "mixer", "track": 0, "solo": False, "mute": False})
        h.ok({"type": "mixer", "track": 1, "mute": False})
        l, _, _ = play(h, file=h.path("back.wav"))
        assert abs(rms(l) - both) < 1e-6, (rms(l), both)
        ok("mute/solo: clearing both restores the mix exactly")

        # The gate is on the mix: the medium and the per-track bounce are not
        # muted, which is what makes mute a performance gesture and not an edit.
        h.ok({"type": "mixer", "track": 0, "mute": True})
        r = h.ok({"type": "clip_to_asset", "track": 0, "start_frame": 0, "len": N,
                  "file": h.path("muted_bounce.wav")})
        _, ch = read_wav(r["path"])
        assert peak(ch) > 0.5, peak(ch)
        ok("mute: a muted track still bounces its audio")
    return 0


# ---------------------------------------------------------------------------
def test_master_strip(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        src = h.path("src.wav")
        left, right = tones()
        write_f32(src, left, right)
        load_tape(h, 0, src)
        base_l, _, _ = play(h, file=h.path("base.wav"))
        b_lo, b_hi = mag(base_l, LOW_HZ), mag(base_l, HIGH_HZ)

        # LEVEL and BAL.
        h.ok({"type": "mixer", "master": 0.5})
        l, r_, _ = play(h, file=h.path("half.wav"))
        assert abs(mag(l, LOW_HZ) / b_lo - 0.5) < 0.01, mag(l, LOW_HZ) / b_lo
        h.ok({"type": "mixer", "master": 1.0, "bal": -1.0})
        l, r_, _ = play(h, file=h.path("bal.wav"))
        assert abs(mag(l, LOW_HZ) / b_lo - 1.0) < 0.01, mag(l, LOW_HZ) / b_lo
        assert peak([r_]) < 1e-6, peak([r_])
        ok("master: LEVEL scales the mix, BAL -1 is all left and unity there")
        h.ok({"type": "mixer", "bal": 0.0})
        l, _, _ = play(h, file=h.path("balback.wav"))
        compare(h.path("base.wav"), h.path("balback.wav"), -100.0, "BAL back to centre")

        # DRIVE: a soft clipper, so it makes odd harmonics the source has not.
        assert mag(base_l, 3 * LOW_HZ) < 1e-4, mag(base_l, 3 * LOW_HZ)
        h.ok({"type": "mixer", "drive": 0.8})
        l, _, _ = play(h, file=h.path("drive.wav"))
        assert mag(l, 3 * LOW_HZ) > 0.004, mag(l, 3 * LOW_HZ)
        ok("drive: 0.8 puts %.3f at the third harmonic where there was none" %
           mag(l, 3 * LOW_HZ))

        # DRIVE TONE tilts what is driven: dark keeps the lows, bright the highs.
        h.ok({"type": "mixer", "drive_tone": 0.0})
        dark, _, _ = play(h, file=h.path("dark.wav"))
        h.ok({"type": "mixer", "drive_tone": 1.0})
        bright, _, _ = play(h, file=h.path("bright.wav"))
        assert mag(dark, LOW_HZ) > mag(bright, LOW_HZ), (mag(dark, LOW_HZ), mag(bright, LOW_HZ))
        assert mag(bright, HIGH_HZ) > mag(dark, HIGH_HZ), (mag(bright, HIGH_HZ), mag(dark, HIGH_HZ))
        ok("drive: TONE 0 keeps 100 Hz (%.3f vs %.3f), TONE 1 keeps 5 kHz (%.3f vs %.3f)" %
           (mag(dark, LOW_HZ), mag(bright, LOW_HZ), mag(bright, HIGH_HZ), mag(dark, HIGH_HZ)))

        h.ok({"type": "mixer", "drive": 0.0, "drive_tone": 0.5})
        l, _, _ = play(h, file=h.path("nodrive.wav"))
        compare(h.path("base.wav"), h.path("nodrive.wav"), -100.0, "DRIVE 0")
        ok("drive: 0 is bit-exact bypass, and it lets go of its filter state")
    return 0


# ---------------------------------------------------------------------------
def test_comp_release(a):
    """COMP REL: a loud burst, then a quiet tail, and how fast it comes back."""
    with Host(a.exe, [a.boxes], block=128) as h:
        burst = int(0.2 * SR)
        tail = int(0.5 * SR)
        x = [(0.9 if i < burst else 0.15) * math.sin(2 * math.pi * 220.0 * i / SR)
             for i in range(burst + tail)]
        src = h.path("burst.wav")
        write_f32(src, x, list(x))
        load_tape(h, 0, src, frames=burst + tail)

        h.ok({"type": "mixer", "comp": 0.9, "comp_rel": 0.02})
        fast, _, _ = play(h, frames=burst + tail, file=h.path("fast.wav"))
        h.ok({"type": "mixer", "comp_rel": 1.0})
        slow, _, _ = play(h, frames=burst + tail, file=h.path("slow.wav"))
        a_rms, b_rms = rms(fast[burst:]), rms(slow[burst:])
        assert a_rms > b_rms * 1.05, (a_rms, b_rms)
        ok("comp: COMP REL 20 ms recovers louder than 1 s (%.4f vs %.4f RMS)" % (a_rms, b_rms))

        st = h.c.state()["mixer"]
        assert st["comp_rel"] == 1.0, st
        h.ok({"type": "mixer", "comp_rel": 99})
        assert h.c.state()["mixer"]["comp_rel"] == 1.0
        h.ok({"type": "mixer", "comp": 0.0, "comp_rel": 0.12})
        ok("comp: COMP REL clamps to 0.02..1 s and lands in `state`")
    return 0


# ---------------------------------------------------------------------------
def comp_tc(flat, comped, comp, t1, t2, target):
    """The envelope's time constant between frames t1 and t2, in seconds.

    The input is a step of DC, so the compressor's gain at frame t is
    comped[t] / (flat[t] * makeup), and its gain law inverts to the envelope;
    `target` is the level the envelope is heading for (the bus after the
    step). None when the envelope sits under the threshold (no gain to read).
    """
    thr = 1.0 - 0.85 * comp
    ratio = 1.0 + 7.0 * comp
    makeup = 1.0 + 0.6 * comp
    d = []
    for t in (t1, t2):
        g = comped[t] / (flat[t] * makeup)
        if g >= 1.0 - 1e-6:
            return None
        env = thr * g ** (1.0 / (1.0 / ratio - 1.0))
        d.append(abs(target - env))
    if d[1] <= 0.0 or d[1] >= d[0]:
        return None
    return (t2 - t1) / SR / math.log(d[0] / d[1])


def test_comp_times(a):
    """#933: `comp_times: "labelled"` runs COMP's 5 ms attack and COMP REL at
    their labels, at 64 and at 256 frames; "block" (the default) is today's."""
    q, loud, tail = 4096, 14336, 28672
    x = [0.05] * q + [0.9] * loud + [0.05] * tail
    for block in (64, 256):
        with Host(a.exe, [a.boxes], block=block) as h:
            assert h.c.state()["mixer"]["comp_times"] == "block"
            src = h.path("step.wav")
            write_f32(src, x, list(x))
            load_tape(h, 0, src, frames=len(x))
            flat, _, _ = play(h, frames=len(x), file=h.path("flat.wav"))
            up = next(i for i, v in enumerate(flat) if v > 0.5)
            down = next(i for i in range(up, len(flat)) if flat[i] < 0.5)
            hi, lo = flat[up + 1024], flat[down + 1024]

            for rel in (0.12, 0.5):
                h.ok({"type": "mixer", "comp": 1.0, "comp_rel": rel, "comp_times": "labelled"})
                assert h.c.state()["mixer"]["comp_times"] == "labelled"
                c, _, _ = play(h, frames=len(x), file=h.path("comp.wav"))
                att = comp_tc(flat, c, 1.0, up + 64, up + 320, hi)
                assert att is not None and abs(att - 0.005) < 0.0005, (block, att)
                r = comp_tc(flat, c, 1.0, down + 512, down + 512 + 4096, lo)
                assert r is not None and abs(r - rel) < 0.1 * rel, (block, rel, r)
                ok("comp: labelled times at block %d: attack %.2f ms (5), release %.3f s (%.2f)"
                   % (block, att * 1e3, r, rel))

            err = h.req({"type": "mixer", "comp_times": "fast"})
            assert err.get("type") == "error", err
            h.ok({"type": "mixer", "comp": 0.0, "comp_rel": 0.12, "comp_times": "block"})
            assert h.c.state()["mixer"]["comp_times"] == "block"
    ok("comp: comp_times defaults to block, refuses anything but block|labelled")
    return 0


# ---------------------------------------------------------------------------
def test_master_rack(a):
    with Host(a.exe, [a.boxes], block=128, extra=["--rt-guard"]) as h:
        src = h.path("src.wav")
        left, right = tones()
        write_f32(src, left, right)
        load_tape(h, 0, src)
        base_l, _, _ = play(h, file=h.path("base.wav"))
        b_lo, b_hi = mag(base_l, LOW_HZ), mag(base_l, HIGH_HZ)

        rack = {"format": 1, "kind": "rack", "name": "MASTER",
                "tabs": [{"instance": "lp", "engine": "arbutus/biquad",
                          "params": {"cutoff": 0.35, "q": 0.5}}]}
        r = h.ok({"type": "load_rack", "instrument": "master", "rack": rack})
        assert r["rack"]["tabs"][0]["instance"] == "lp", r["rack"]
        st = h.c.state()
        assert st["mixer"]["fx"]["tabs"][0]["engine"] == "arbutus/biquad", st["mixer"]["fx"]
        assert st["mixer"]["tabs"][0]["id"] == "arbutus/biquad", st["mixer"]["tabs"]
        # the instruments' own racks are untouched by it
        assert st["instruments"]["synth"]["fx"]["tabs"] == [], st["instruments"]["synth"]["fx"]

        l, _, _ = play(h, file=h.path("rack.wav"))
        assert db(mag(l, HIGH_HZ) / b_hi) < -20.0, db(mag(l, HIGH_HZ) / b_hi)
        assert abs(db(mag(l, LOW_HZ) / b_lo)) < 3.0, db(mag(l, LOW_HZ) / b_lo)
        ok("master rack: a lowpass on the master takes 5 kHz down %.1f dB" %
           db(mag(l, HIGH_HZ) / b_hi))

        # The same rack mechanism, so the same edits work on it (DDR-062).
        h.ok({"type": "set_param", "instrument": "master", "slot": "fx", "tab": 0,
              "idx": 0, "value": 1.0})
        l, _, _ = play(h, file=h.path("open.wav"))
        assert abs(db(mag(l, HIGH_HZ) / b_hi)) < 3.0, db(mag(l, HIGH_HZ) / b_hi)
        ok("master rack: set_param on the master opens the filter again")

        # The field is `bypass` (PROTOCOL.md § set_bypass); it defaults to
        # true, so the off case is the one that proves it was read (#599).
        h.ok({"type": "set_bypass", "instrument": "master", "bypass": True})
        assert h.c.state()["mixer"]["fx"]["bypass"] is True
        l, _, _ = play(h, file=h.path("bypass.wav"))
        compare(h.path("base.wav"), h.path("bypass.wav"), -100.0, "master rack bypassed")
        ok("master rack: bypass is a dry passthrough, sample for sample")
        h.ok({"type": "set_bypass", "instrument": "master", "bypass": False})
        assert h.c.state()["mixer"]["fx"]["bypass"] is False, h.c.state()["mixer"]["fx"]
        h.ok({"type": "set_param", "instrument": "master", "slot": "fx", "tab": 0,
              "idx": 0, "value": 0.0})
        l, _, _ = play(h, file=h.path("unbypass.wav"))
        differs(h.path("base.wav"), h.path("unbypass.wav"), -40.0, "master rack back in")
        ok("master rack: bypass off puts the filter back in the path")

        r = h.ok({"type": "add_tab", "instrument": "master", "engine": "arbutus/biquad"})
        assert len(r["rack"]["tabs"]) == 2, r["rack"]
        r = h.ok({"type": "remove_tab", "instrument": "master", "index": 1})
        assert len(r["rack"]["tabs"]) == 1, r["rack"]
        ok("master rack: add_tab / remove_tab address it like any rack")

        # It is a rack and nothing else: an instrument message on it is a bug.
        for m in ({"type": "load_engine", "slot": "engine", "id": "arbutus/example"},
                  {"type": "load_patch", "patch": {}},
                  {"type": "env", "a": 0.1},
                  {"type": "host_param", "level": 0.5},
                  {"type": "add_mod_tab", "engine": "arbutus/lfo"},
                  {"type": "instrument"}):
            m["instrument"] = "master"
            assert h.req(m)["type"] == "error", m
        ok("master rack: the instrument-only messages refuse `instrument: master`")

        # It is the *mix* that reaches it, instrument included.
        h.ok({"type": "load_rack", "instrument": "master", "rack": {
            "format": 1, "kind": "rack", "name": "-", "tabs": []}})
        h.ok({"type": "set_clips", "track": 0, "clips": []})   # the instrument alone
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.note(60, True, 0.9)
        dry, _, _ = play(h, frames=8192, file=h.path("dry.wav"))
        h.ok({"type": "load_rack", "instrument": "master", "rack": {
            "format": 1, "kind": "rack", "name": "M",
            "tabs": [{"instance": "lp", "engine": "arbutus/biquad",
                      "params": {"cutoff": 0.0, "q": 0.5}}]}})
        wet, _, _ = play(h, frames=8192, file=h.path("wet.wav"))
        # RMS of the tail, not the peak: the note's attack transient is wide
        # band and gets through any filter.
        assert rms(wet[4096:]) < rms(dry[4096:]) * 0.5, (rms(wet[4096:]), rms(dry[4096:]))
        ok("master rack: the instrument bus goes through it too (%.3f -> %.3f)" %
           (rms(dry[4096:]), rms(wet[4096:])))

        # Everything F1 added, on at once, for the allocation count.
        h.ok({"type": "eq", "track": 0, "low": 6, "mid": -4, "high": 3, "hp": 60, "lp": 9000})
        h.ok({"type": "mixer", "track": 0, "mute": False, "solo": True})
        h.ok({"type": "mixer", "comp": 0.5, "comp_rel": 0.3, "drive": 0.4, "bal": 0.2})
        _, _, r = play(h, frames=16384, file=h.path("all.wav"))
        allocs = r.get("rt_allocs")
        assert allocs is not None, r
        if allocs < 0:
            ok("rt-guard: unavailable on this platform")
        else:
            assert allocs == 0, "%d heap blocks gained with the master rack and the EQ" % allocs
            ok("rt-guard: the master rack, the EQ and the strip allocate nothing")
    return 0


def main():
    a = args()
    for t in (test_defaults_and_eq, test_mute_solo, test_master_strip,
              test_comp_release, test_comp_times, test_master_rack, test_eq_tail_after_stop):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
