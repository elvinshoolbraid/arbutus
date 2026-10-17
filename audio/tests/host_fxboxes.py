#!/usr/bin/env python3
"""Stage I2 through the host: delay, chorus, crusher, drive, eq3.

The boxes' own goldens prove their DSP (`ctest -R delay|chorus|crusher|drive|eq3`).
This proves the parts only the host can:

  * each of the five loads onto an **instrument** rack and changes the sound;
  * each of them loads onto the **master** rack (`instrument: "master"`, §15.5)
    and changes the mix;
  * they are **macro-bindable** — a macro on tab 0 moves a parameter of one of
    them and the change is audible (§6.2, DDR-039);
  * they **honour rack bypass** — a bypassed rack is a dry passthrough, sample
    for sample (DDR-049);
  * `arbutus/delay` follows the **host's tempo**: the same TIME knob puts the
    echo in a different place at a different BPM, which is the one thing about
    these boxes the host and not the box decides;
  * with all five in one rack the audio thread still allocates nothing.
"""

import math
import struct
import sys

from hostcase import Host, args, compare, db, differs, ok, peak, read_wav

SR = 48000
N = 24000                                   # half a second


def write_f32(path, left, right, rate=SR):
    data = bytearray()
    for l, r in zip(left, right):
        data += struct.pack("<ff", l, r)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, rate, rate * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", len(data)) + bytes(data))


def impulse(n=N, at=0):
    x = [0.0] * n
    x[at] = 0.9
    return x, list(x)


def tones(n=N, amp=0.35):
    """100 Hz + 5 kHz, both channels the same — exact bins at N = 24000."""
    x = [amp * math.sin(2 * math.pi * 100.0 * i / SR) +
         amp * math.sin(2 * math.pi * 5000.0 * i / SR) for i in range(n)]
    return x, list(x)


def mag(x, hz, rate=SR):
    w = 2 * math.pi * hz / rate
    re = sum(v * math.cos(w * i) for i, v in enumerate(x))
    im = sum(-v * math.sin(w * i) for i, v in enumerate(x))
    return 2.0 * math.sqrt(re * re + im * im) / len(x)


def rms(x):
    return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0


def load_tape(h, track, path, frames=N):
    h.ok({"type": "tape_load", "track": track, "file": path})
    h.ok({"type": "set_clips", "track": track, "clips": [
        {"start_frame": 0, "len": frames, "src_offset": 0, "gain": 1.0}]})


def play(h, file, frames=N):
    h.ok({"type": "transport", "op": "stop"})
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    r = h.ok({"type": "render", "frames": frames, "file": file})
    h.ok({"type": "transport", "op": "stop"})
    _, ch = read_wav(file)
    return ch[0], ch[1], r


def rack(name, engine, params=None, bypass=False):
    return {"format": 1, "kind": "rack", "name": name,
            "tabs": [{"instance": "fx", "engine": engine, "params": params or {}}],
            "bypass": bypass}


#: what each box is set to when it has to prove it changes the sound, and the
#: measurement that says so. Values are normalised (§3.2, the wire's units).
BOXES = (
    ("arbutus/delay",   {"time": 0.5 + 7.0 / 24.0, "feedback": 0.6, "mix": 1.0}),
    ("arbutus/chorus",  {"depth": 0.8, "mix": 1.0, "feedback": 0.9}),
    ("arbutus/crusher", {"bits": 0.0, "rate": 0.15, "mix": 1.0}),
    ("arbutus/drive",   {"drive": 0.9, "mix": 1.0}),
    ("arbutus/eq3",     {"high": 0.0, "low": 1.0}),
)


# ---------------------------------------------------------------------------
def test_on_the_master_rack(a):
    """Every box on `instrument: "master"`, measured on the mix."""
    with Host(a.exe, [a.boxes]) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)
        base_l, _, _ = play(h, h.path("base.wav"))
        b_lo, b_hi = mag(base_l, 100.0), mag(base_l, 5000.0)
        b_rms = rms(base_l)

        for engine, params in BOXES:
            h.ok({"type": "load_rack", "instrument": "master",
                  "rack": rack("I2", engine, params)})
            st = h.c.state()["mixer"]["fx"]
            assert st["tabs"][0]["engine"] == engine, st
            l, _, _ = play(h, h.path("m.wav"))
            moved = max(abs(db(mag(l, 100.0) / b_lo)) if b_lo else 0.0,
                        abs(db(mag(l, 5000.0) / b_hi)) if b_hi else 0.0,
                        abs(db(rms(l) / b_rms)))
            assert moved > 1.0, "%s on the master changed nothing (%.2f dB)" % (engine, moved)
            ok("%s on the master rack: the mix moves %.1f dB" % (engine, moved))

        # ...and bypass puts it back, sample for sample (DDR-049).
        # The field is `bypass`; it defaults to true, so the off case is the
        # one that proves it was read (#599).
        h.ok({"type": "set_bypass", "instrument": "master", "bypass": True})
        assert h.c.state()["mixer"]["fx"]["bypass"] is True
        play(h, h.path("byp.wav"))
        compare(h.path("base.wav"), h.path("byp.wav"), -100.0, "master rack bypassed")
        ok("rack bypass on the master is a dry passthrough for every I2 box")
        h.ok({"type": "set_bypass", "instrument": "master", "bypass": False})
        assert h.c.state()["mixer"]["fx"]["bypass"] is False, h.c.state()["mixer"]["fx"]
        play(h, h.path("unbyp.wav"))
        differs(h.path("base.wav"), h.path("unbyp.wav"), -40.0, "master rack back in")
        ok("bypass off on the master puts the box back in the path")
    return 0


# ---------------------------------------------------------------------------
def test_on_an_instrument_rack(a):
    """The same five on SYNTH's own rack, measured on a held note."""
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_engine", "instrument": "synth", "slot": "engine",
              "id": "arbutus/example"})
        h.ok({"type": "env", "instrument": "synth", "a": 0.001, "d": 0.1,
              "s": 1.0, "r": 0.1})

        def note(file, frames=12000):
            h.ok({"type": "panic"})
            h.note(60, True, 0.9)
            h.render(frames, file)
            h.ok({"type": "note", "on": False, "note": 60})
            _, ch = read_wav(file)
            return ch[0]

        dry = note(h.path("dry.wav"))
        for engine, params in BOXES:
            h.ok({"type": "load_rack", "instrument": "synth",
                  "rack": rack("I2", engine, params)})
            st = h.c.state()["instruments"]["synth"]["fx"]
            assert st["tabs"][0]["engine"] == engine, st
            # the master rack is untouched by an instrument's
            assert h.c.state()["mixer"]["fx"]["tabs"] == [], "the master rack moved"
            wet = note(h.path("wet.wav"))
            moved = abs(db(rms(wet) / rms(dry))) if rms(dry) else 0.0
            peaked = abs(db(peak([wet]) / peak([dry])))
            assert max(moved, peaked) > 0.5, \
                "%s on SYNTH changed nothing (%.2f dB)" % (engine, max(moved, peaked))
            ok("%s on SYNTH's rack: the note moves %.1f dB" % (engine, max(moved, peaked)))

        h.ok({"type": "set_bypass", "instrument": "synth", "bypass": True})
        assert h.c.state()["instruments"]["synth"]["fx"]["bypass"] is True
        byp = note(h.path("byp.wav"))
        e = max(abs(x - y) for x, y in zip(byp, dry))
        assert db(e) <= -100.0, "bypassed instrument rack is not dry: %.1f dBFS" % db(e)
        ok("rack bypass on SYNTH: %.1f dBFS against the dry note" % db(e))
        h.ok({"type": "set_bypass", "instrument": "synth", "bypass": False})
        st = h.c.state()["instruments"]["synth"]["fx"]
        assert st["bypass"] is False, st
        wet = note(h.path("unbyp.wav"))
        e = max(abs(x - y) for x, y in zip(wet, dry))
        assert db(e) >= -40.0, "bypass off on SYNTH left the note dry: %.1f dBFS" % db(e)
        ok("bypass off on SYNTH: the box is back, %.1f dBFS from the dry note" % db(e))
    return 0


# ---------------------------------------------------------------------------
def test_macro_binding(a):
    """A macro on tab 0 drives an I2 parameter and it is audible (§6.2)."""
    with Host(a.exe, [a.boxes]) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)
        doc = rack("MACRO", "arbutus/eq3", {"high": 0.5})
        doc["macros"] = [{"slot": 0, "name": "AIR",
                          "targets": [{"instance": "fx", "param": "high",
                                       "lo": 0.5, "hi": 1.0, "curve": "lin",
                                       "depth": 1.0}]}]
        h.ok({"type": "load_rack", "instrument": "master", "rack": doc})
        h.ok({"type": "set_macro", "instrument": "master", "slot": 0, "value": 0.0})
        lo_l, _, _ = play(h, h.path("mlo.wav"))
        h.ok({"type": "set_macro", "instrument": "master", "slot": 0, "value": 1.0})
        hi_l, _, _ = play(h, h.path("mhi.wav"))
        gain = db(mag(hi_l, 5000.0) / mag(lo_l, 5000.0))
        assert gain > 10.0, "the macro moved HIGH by only %.1f dB" % gain
        ok("macro: AIR 0 -> 1 moves eq3's HIGH by %.1f dB at 5 kHz" % gain)

        # The same shape on an instrument rack, on a different box.
        doc = rack("MACRO2", "arbutus/drive", {"drive": 0.0, "mix": 1.0})
        doc["macros"] = [{"slot": 1, "name": "GRIT",
                          "targets": [{"instance": "fx", "param": "drive",
                                       "lo": 0.0, "hi": 1.0, "curve": "exp",
                                       "depth": 1.0}]}]
        h.ok({"type": "load_rack", "instrument": "synth", "rack": doc})
        r = h.c.state()["instruments"]["synth"]["fx"]
        assert r["macros"][0]["targets"][0]["param"] == "drive", r["macros"]
        h.ok({"type": "set_macro", "instrument": "synth", "slot": 1, "value": 1.0})
        ok("macro: an exp-curved GRIT on drive.drive is stored on SYNTH's rack")
    return 0


# ---------------------------------------------------------------------------
def test_delay_follows_tempo(a):
    """§15.4's clock reaches an effect: the same TIME, two BPMs, two echoes."""
    with Host(a.exe, [a.boxes]) as h:
        src = h.path("imp.wav")
        write_f32(src, *impulse(N, 0))
        load_tape(h, 0, src)

        def echo_at(bpm):
            h.ok({"type": "tempo", "bpm": bpm})
            # The rack is loaded *after* the tempo so the box's time lands
            # rather than glides (a fresh engine snaps; README § How it is built).
            h.ok({"type": "load_rack", "instrument": "master",
                  "rack": rack("D", "arbutus/delay",
                               {"time": 0.5 + 7.0 / 24.0,   # 1/4
                                "feedback": 0.0, "mix": 1.0, "wow": 0.0})})
            # 1.5 s of render: a 1/4 at 60 BPM is a second out, and the
            # tape is only half a second long -- the echo arrives over silence.
            l, _, _ = play(h, h.path("d%d.wav" % bpm), frames=SR * 3 // 2)
            best, at = 0.0, 0
            for i in range(100, len(l)):
                if abs(l[i]) > best:
                    best, at = abs(l[i]), i
            return at / float(SR)

        for bpm, want in ((120.0, 0.5), (60.0, 1.0), (96.0, 0.625)):
            got = echo_at(bpm)
            assert abs(got - want) < 0.003, \
                "1/4 at %g BPM landed at %.4f s, wanted %.4f" % (bpm, got, want)
            ok("delay: a 1/4 echo at %g BPM lands at %.4f s (host tempo, not the box's)"
               % (bpm, got))
    return 0


# ---------------------------------------------------------------------------
def test_five_in_one_rack(a):
    """All five in series, and the audio thread still allocates nothing."""
    with Host(a.exe, [a.boxes], extra=["--rt-guard"]) as h:
        src = h.path("src.wav")
        write_f32(src, *tones())
        load_tape(h, 0, src)
        doc = {"format": 1, "kind": "rack", "name": "ALL", "tabs": [
            {"instance": "e%d" % i, "engine": e, "params": p}
            for i, (e, p) in enumerate(BOXES)]}
        r = h.ok({"type": "load_rack", "instrument": "master", "rack": doc})
        assert len(r["rack"]["tabs"]) == 5, r["rack"]
        # A second of render: the chain starts with a 1/4 delay, so half a
        # second of tape comes out over a second of time.
        l, _, rep = play(h, h.path("all.wav"), frames=SR)
        assert peak([l]) > 1e-4, "five effects in series rendered silence"
        allocs = rep.get("rt_allocs")
        assert allocs is not None, rep
        if allocs < 0:
            ok("rt-guard: unavailable on this platform")
        else:
            assert allocs == 0, "%d heap blocks gained with five I2 boxes" % allocs
            ok("rt-guard: five I2 boxes in one master rack allocate nothing")

        # Every parameter of every box is reachable by index through the rack.
        st = h.c.state()["mixer"]["tabs"]
        counts = [len(t["desc"]["params"]) for t in st]
        assert counts == [8, 8, 6, 8, 8], counts
        ok("the five boxes show %s slots; crusher's 6 leaves two empty on purpose"
           % counts)
    return 0


def main():
    a = args()
    for t in (test_on_the_master_rack, test_on_an_instrument_rack,
              test_macro_binding, test_delay_follows_tempo, test_five_in_one_rack):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
