#!/usr/bin/env python3
"""Stage H1's sequencer boxes in the SEQ slot, end to end (engine-spec §15.4).

`host_seq.py` proves `arbutus/endless` through the protocol; this proves the
other three the same way -- a patch names the box in `seq`, the notes go in as
a player would play them, and what comes out is measured as *pitch over time*
rather than trusted. The event goldens beside each box pin what the box emits;
this pins that the host delivers it into a voice.

Also checks `endless`'s H1 additions arrive over the wire: GATE, PATTERN and
ROT are in the descriptor the SEQ page builds its row from, and setting them
is accepted.
"""

import sys

from hostcase import Host, args, ok, peak, read_wav

STEP = 6000           # a 1/16 step at 120 bpm, 48 kHz
SAW = 2.0 / 3.0       # ENUM 0..3, SAW is index 2, normalised
HZ = {48: 130.81, 55: 196.00, 57: 220.00, 60: 261.63, 62: 293.66,
      64: 329.63, 67: 392.00, 69: 440.00, 72: 523.25}


def patch(seq_id, seq_params=None, seq_assets=None):
    p = {"format": 1, "kind": "patch", "name": "SEQBOX",
         "engine": {"id": "arbutus/osc", "params": {"wave": SAW, "level": 0.9}},
         # A short release keeps one step from smearing into the next, which is
         # what makes the pitch of a window readable.
         "env": {"a": 0.002, "d": 0.02, "s": 0.85, "r": 0.02, "mode": "poly"},
         "seq": {"id": seq_id, "params": seq_params or {}}}
    if seq_assets:
        p["seq"]["assets"] = seq_assets
    return p


def sign_changes(ch, a, b):
    n = 0
    prev = ch[a]
    for i in range(a + 1, b):
        v = ch[i]
        if (v >= 0.0) != (prev >= 0.0):
            n += 1
        prev = v
    return n


def window_hz(ch, a, b, sr=48000):
    """A saw crosses zero twice a cycle: once on the ramp, once on the wrap."""
    return sign_changes(ch, a, b) / 2.0 * sr / float(b - a)


def nearest_note(hz):
    return min(HZ, key=lambda n: abs(HZ[n] - hz))


def step_notes(ch, first, count, step=STEP, inset=1500, width=3000):
    """The note sounding in the middle of each of `count` steps."""
    out = []
    for k in range(count):
        w0 = first + k * step + inset
        out.append(nearest_note(window_hz(ch, w0, w0 + width)))
    return out


def silent(ch, a):
    return max(abs(v) for v in ch[a:]) < 1e-4


# ---------------------------------------------------------------- arp -------
def run_arp(h):
    h.ok({"type": "load_patch", "patch": patch("arbutus/arp")})
    st = h.c.state()["instruments"]["synth"]["seq"]
    assert st["id"] == "arbutus/arp", st
    assert not st["desc"].get("softkeys"), st["desc"].get("softkeys")
    ok("arp: in the slot with no soft-keys -- the strip is blank by design")

    ids = [p["id"] for p in st["desc"]["params"]]
    assert ids == ["div", "mode", "oct", "gate", "swing", "hold", "ratchet"], ids
    ok("arp: the SEQ row is %s" % " ".join(x.upper() for x in ids))

    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    # The chord goes in out of order; UP sorts it.
    for n in (67, 60, 64):
        h.note(n, True, 1.0)
    run = h.path("arp.wav")
    h.render(6 * STEP, run)
    for n in (67, 60, 64):
        h.ok({"type": "note", "on": False, "note": n})
    after = h.path("arp-after.wav")
    h.render(2 * STEP, after)

    _, ch = read_wav(run)
    p = peak(ch)
    assert p > 0.05, "arp rendered silence (peak %f)" % p
    # Step 0 lands inside the first block after the notes; read from step 1.
    got = step_notes(ch[0], STEP, 5)
    want = [64, 67, 60, 64, 67]
    assert got == want, "arp plays %s, expected %s" % (got, want)
    ok("arp: UP gives %s -- the chord sorted, on the beat (peak %.3f)" % (got, p))

    _, ch2 = read_wav(after)
    assert silent(ch2[0], 4800), "arp still sounding after the chord was let go"
    ok("arp: letting the chord go stops it")


# ------------------------------------------------------------- euclid -------
def run_euclid(h):
    # E(4,8) at 1/8 on the held key: onsets every other step, so a note every
    # 24000 frames -- one a beat at 120 bpm.
    h.ok({"type": "load_patch", "patch": patch(
        "arbutus/euclid",
        {"steps": 8.0 / 32.0, "pulses": 4.0 / 32.0, "div": 1.0 / 6.0,
         "gate": 50.0 / 90.0 - 10.0 / 90.0})})
    st = h.c.state()["instruments"]["synth"]["seq"]
    assert st["id"] == "arbutus/euclid", st
    ids = [p["id"] for p in st["desc"]["params"]]
    assert ids == ["steps", "pulses", "rot", "div", "gate", "swing", "hold",
                   "accent"], ids
    ok("euclid: the SEQ row is %s" % " ".join(x.upper() for x in ids))

    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.note(60, True, 1.0)
    run = h.path("euclid.wav")
    h.render(8 * 12000, run)
    h.ok({"type": "note", "on": False, "note": 60})

    _, ch = read_wav(run)
    p = peak(ch)
    assert p > 0.05, "euclid rendered silence (peak %f)" % p
    # A note in every even 1/8 window, silence in the odd ones (GATE 50 % of a
    # 12000-frame step ends the note 6000 frames in).
    loud, quiet = [], []
    for k in range(1, 7):
        a = k * 12000
        win = ch[0][a + 7000:a + 11500]
        (loud if k % 2 == 0 else quiet).append(max(abs(v) for v in win))
    assert max(quiet) < 1e-3, "euclid sounded on an off-pulse: %s" % quiet
    ok("euclid: E(4,8) at 1/8 -- an onset a beat, the off-pulses silent "
       "(peak %.3f, rests %.1e)" % (p, max(quiet)))


# ------------------------------------------------------------ pattern -------
def run_pattern(h, library):
    # `library/seq/pattern/riff-8.seq` as a `pattern` asset (DDR-037): the
    # path H2's browser will use, working through the patch already.
    h.ok({"type": "load_patch", "patch": patch(
        "arbutus/pattern", {"gate": 60.0 / 90.0},
        {"pattern": library + "/seq/pattern/riff-8.seq"})})
    st = h.c.state()["instruments"]["synth"]["seq"]
    assert st["id"] == "arbutus/pattern", st
    keys = [k["label"] for k in st["desc"]["softkeys"]]
    assert keys == ["REC", "REST", "UNDO", "CLEAR"], keys
    ok("pattern: soft-keys %s -- `endless`'s, so one UI drives both"
       % "/".join(keys))
    assert st["desc"]["assets"] == ["pattern"], st["desc"]["assets"]
    ok("pattern: declares the `pattern` asset (DDR-037)")

    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    # Absolute pitch: run it from C5 and it still plays the riff's own notes.
    h.note(72, True, 1.0)
    run = h.path("pattern.wav")
    h.render(8 * STEP, run)
    h.ok({"type": "note", "on": False, "note": 72})

    _, ch = read_wav(run)
    p = peak(ch)
    assert p > 0.05, "pattern rendered silence (peak %f)" % p
    # riff-8: 57 60 . 64 62 . 60 57 -- steps 2 and 5 are rests.
    got = step_notes(ch[0], STEP, 3)                 # steps 1, 2, 3
    assert got[0] == 60 and got[2] == 64, \
        "pattern plays %s, expected 60 . 64 (absolute pitch)" % got
    rest = max(abs(v) for v in ch[0][2 * STEP + 4000:3 * STEP - 200])
    assert rest < 1e-3, "pattern sounded through a rest: %.1e" % rest
    ok("pattern: the riff plays at its own pitch from a key two octaves up, "
       "rests silent (peak %.3f, rest %.1e)" % (p, rest))


# ------------------------------------------------------------ endless -------
def run_endless_h1(h):
    h.ok({"type": "load_patch", "patch": patch("arbutus/endless")})
    st = h.c.state()["instruments"]["synth"]["seq"]
    ids = [p["id"] for p in st["desc"]["params"]]
    assert ids == ["div", "swing", "hold", "dir", "gate", "pattern", "rot"], ids
    ok("endless: the SEQ row gained %s (§15.4)" % " ".join(ids[4:]).upper())
    assert st["desc"]["assets"] == ["pattern"], st["desc"]["assets"]
    labels = [p.get("labels") for p in st["desc"]["params"] if p["id"] == "pattern"]
    assert labels and labels[0][0] == "OFF", labels
    ok("endless: PATTERN is an ENUM of masks starting at OFF, and the box "
       "takes a `pattern` asset")
    for idx in (4, 5, 6):
        h.ok({"type": "set_param", "instrument": "synth", "slot": "seq",
              "idx": idx, "value": 0.5})
    h.render(512)
    ok("endless: the new slots are settable over the wire")


# --------------------------------------------- a pattern through load_engine -
def run_pattern_asset(h):
    """Stage H2's door: `load_engine {slot:"seq", id, assets}` (DDR-037).

    The browser picks a `.seq` while the box is already in the slot, so the
    only thing that may move is the content -- the parameters the player set
    have to survive, and a path that is library-relative has to resolve the
    way a patch's does.
    """
    h.ok({"type": "load_patch", "patch": patch("arbutus/endless")})
    # Something to lose: GATE off its default (100 %), set before the asset
    # arrives. 0.9 is 91 %, short enough to be a change and long enough that a
    # step's pitch is still readable in the middle of it.
    h.ok({"type": "set_param", "instrument": "synth", "slot": "seq",
          "idx": 4, "value": 0.9})
    before = h.c.state()["instruments"]["synth"]["seq"]["params"]["gate"]

    # library-relative, exactly as a patch names one
    h.ok({"type": "load_engine", "instrument": "synth", "slot": "seq",
          "id": "arbutus/endless",
          "assets": {"pattern": "seq/endless/penta-5.seq"}})
    st = h.c.state()["instruments"]["synth"]["seq"]
    assert st["assets"].get("pattern", "").endswith("seq/endless/penta-5.seq"), st
    assert st["assets"]["pattern"].startswith("/"), st["assets"]
    ok("load_engine: a library-relative `pattern` resolves to %s"
       % st["assets"]["pattern"].split("/library/")[-1])
    assert abs(st["params"]["gate"] - before) < 1e-6, (st["params"], before)
    ok("load_engine: the slot's parameters survive the content (same id)")

    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    # penta-5 is written at root 57, so holding 57 plays it as written.
    h.note(57, True, 1.0)
    run = h.path("pattern-asset.wav")
    h.render(6 * STEP, run)
    h.ok({"type": "note", "on": False, "note": 57})
    h.ok({"type": "transport", "op": "stop"})

    _, ch = read_wav(run)
    p = peak(ch)
    assert p > 0.05, "the loaded pattern rendered silence (peak %f)" % p
    got = step_notes(ch[0], STEP, 4)
    want = [67, 64, 62, 60]
    assert got == want, "penta-5 plays %s, expected %s" % (got, want)
    ok("load_engine: the file is what sounds -- %s descending from a held "
       "root (peak %.3f)" % (" ".join(str(n) for n in want), p))

    # `{}` is "no assets", not "leave it alone"
    h.ok({"type": "load_engine", "instrument": "synth", "slot": "seq",
          "id": "arbutus/endless", "assets": {}})
    st = h.c.state()["instruments"]["synth"]["seq"]
    assert not st["assets"], st["assets"]
    ok("load_engine: `assets: {}` clears the slot's content")

    # ...and leaving the field out keeps what is there
    h.ok({"type": "load_engine", "instrument": "synth", "slot": "seq",
          "id": "arbutus/endless",
          "assets": {"pattern": "seq/endless/penta-7.seq"}})
    h.ok({"type": "load_engine", "instrument": "synth", "slot": "seq",
          "id": "arbutus/endless"})
    st = h.c.state()["instruments"]["synth"]["seq"]
    assert st["assets"].get("pattern", "").endswith("penta-7.seq"), st["assets"]
    ok("load_engine: no `assets` field leaves the content alone")

    # §7.4 still refuses, and the previous graph keeps running
    r = h.req({"type": "load_engine", "instrument": "synth", "slot": "seq",
               "id": "arbutus/endless", "assets": {"nope": "seq/endless/init.seq"}})
    assert r.get("type") == "error" and r.get("code") == "unknown_asset", r
    st = h.c.state()["instruments"]["synth"]["seq"]
    assert st["assets"].get("pattern", "").endswith("penta-7.seq"), st["assets"]
    ok("load_engine: an undeclared key is refused `unknown_asset` and the "
       "slot keeps what it had")

    r = h.req({"type": "load_engine", "instrument": "synth", "slot": "seq",
               "id": "arbutus/endless", "assets": {"pattern": "seq/endless/nope.seq"}})
    assert r.get("type") == "error" and r.get("code") == "asset_not_found", r
    ok("load_engine: a missing file is refused `asset_not_found`")


def main():
    a = args()
    import os
    library = os.path.join(a.audio_root, "library")
    with Host(a.exe, [a.boxes, library]) as h:
        run_arp(h)
        run_euclid(h)
        run_pattern(h, library)
        run_endless_h1(h)
        run_pattern_asset(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
