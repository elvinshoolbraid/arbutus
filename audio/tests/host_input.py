#!/usr/bin/env python3
"""Stage G: the live input as an engine (DDR-063, engine-spec §15.2, §15.6).

Two halves.

**One value, two doors.** `input_source {source}` and `arbutus/live-input`'s
SOURCE knob are the same fact, so each has to move the other, and a document
that carries SOURCE has to win at the moment it lands.

**The audio.** Offline there is no microphone, so MIC is silence and that is the
assertion; `resample` is made inside the host and can be measured exactly. A DC
level is recorded onto tape track 1, and then the same transport is rendered
twice — once with SOURCE=MIC (the tape alone) and once with SOURCE=RSMP. The
second must be the first plus the first delayed by **one block**: `resample` is
the master one block late (modulation-spec §4's rule for `master`), with the
instrument bus taken back out so the box cannot hear itself. Arming the recorded
track must then take the tape out of it too, which is §15.2's "minus the armed
track" measured rather than described.
"""

import sys

from hostcase import Host, args, ok, read_wav

BLOCK = 512
TOL = 1e-5
FLAT = {"a": 0.0, "d": 0.0, "s": 1.0, "r": 0.0, "mode": "poly"}

# arbutus/live-input, normalised (§3.2): SOURCE is an ENUM over 0..3, GAIN is
# -24..+24 dB, so unity is the middle of the range.
MIC, LINE, USB, RSMP = 0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0
UNITY_GAIN = 0.5


def source_of(h):
    return h.c.state()["input"]["source"]


def engine_params(h, inst="synth"):
    return h.c.state()["instruments"][inst]["engine"]["params"]


def set_source(h, norm):
    """Turn the SOURCE knob the way SYNTH › ENGINE's encoder does."""
    return h.ok({"type": "set_param", "instrument": "synth", "slot": "engine",
                 "idx": 0, "value": norm})


def near(a, b, what):
    assert abs(a - b) <= TOL, "%s: got %.6f, want %.6f" % (what, a, b)


# ---------------------------------------------------------------------------
def messages(a):
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        assert source_of(h) == "mic", h.c.state()["input"]
        ok("input: the source is mic until something says otherwise")

        for name in ("line", "usb", "resample", "mic"):
            h.ok({"type": "input_source", "source": name})
            assert source_of(h) == name, name
        ok("input_source: every name in §15.2 round-trips through `state.input`")

        r = h.req({"type": "input_source", "source": "radio"})
        assert r.get("type") == "error" and r.get("code") == "bad_request", r
        ok("input_source: a source the device has not got is refused")

        # The box, and the two directions.
        h.ok({"type": "input_source", "source": "usb"})
        h.ok({"type": "load_engine", "instrument": "synth",
              "slot": "engine", "id": "arbutus/live-input"})
        assert source_of(h) == "mic", "a fresh box brings its own default"
        ok("live-input: loading the box adopts the knob it arrives with (MIC)")

        h.ok({"type": "input_source", "source": "resample"})
        near(engine_params(h)["source"], RSMP, "the knob after input_source")
        ok("input_source: the message writes the box's SOURCE knob")

        set_source(h, LINE)
        assert source_of(h) == "line", h.c.state()["input"]
        ok("live-input: turning SOURCE moves the host's routing")

        # A patch is a document, and a document wins when it lands.
        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "BOUNCE",
            "engine": {"id": "arbutus/live-input",
                       "params": {"source": RSMP, "gain": UNITY_GAIN}},
            "env": FLAT}})
        assert source_of(h) == "resample", h.c.state()["input"]
        ok("live-input: a patch carrying SOURCE selects it on load")

        # The GAIN readout is the box's own (§15.1 format_param).
        disp = h.c.state()["instruments"]["synth"]["engine"].get("display", {})
        assert disp.get("gain") == "+0.0 dB", disp
        ok("live-input: GAIN reads in dB, from the box's format_param")


# ---------------------------------------------------------------------------
def audio(a):
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        # --- a DC level onto tape track 1 ---------------------------------
        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "DC",
            "engine": {"id": "arbtest/abi2", "params": {"level": 0.4}},
            "env": FLAT}})
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 0, "on": True})
        h.note(60, True, 1.0)
        h.render(4 * BLOCK)
        h.ok({"type": "note", "on": False, "note": 60})
        h.ok({"type": "record", "track": 0, "on": False})
        h.render(BLOCK)
        h.ok({"type": "transport", "op": "stop"})
        h.ok({"type": "arm", "track": -1})
        ok("tape: a DC level recorded onto track 1")

        # --- the box in the ENGINE slot -----------------------------------
        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "IN",
            "engine": {"id": "arbutus/live-input",
                       "params": {"source": MIC, "gain": UNITY_GAIN}},
            "env": FLAT}})

        def play(path):
            h.ok({"type": "transport", "op": "stop"})
            h.ok({"type": "transport", "op": "locate", "position": 0})
            h.ok({"type": "panic"})
            h.ok({"type": "transport", "op": "start"})
            h.render(4 * BLOCK, path)
            h.ok({"type": "transport", "op": "stop"})
            return read_wav(path)[1][0]

        base = play(h.path("mic.wav"))
        assert max(abs(v) for v in base) > 0.05, "the tape should be audible"
        ok("SOURCE=MIC: offline there is no microphone, so the master is the tape alone")

        set_source(h, RSMP)
        assert source_of(h) == "resample"
        rsmp = play(h.path("rsmp.wav"))

        # The first block of each run is skipped everywhere below: the delay
        # line is one continuous line and does not restart with the transport,
        # so block 0 still carries the tail of the previous render.
        for k in range(BLOCK, 4 * BLOCK):
            near(rsmp[k], base[k] + base[k - BLOCK], "resample at frame %d" % k)
        ok("SOURCE=RSMP: the master, one block late, added back through the ENGINE slot")

        # The box does not hear itself: were the instrument bus left in the tap,
        # this would be a unity-gain loop and the third block would be 3x, not 2x.
        assert abs(rsmp[3 * BLOCK + 10]) < 2.5 * abs(base[10]) + TOL
        ok("SOURCE=RSMP: the instrument bus is out of the tap — no feedback")

        # --- minus the armed track (§15.2) --------------------------------
        h.ok({"type": "arm", "track": 0})
        armed = play(h.path("armed.wav"))
        for k in range(BLOCK, 4 * BLOCK):
            near(armed[k], base[k], "armed track excluded at frame %d" % k)
        ok("SOURCE=RSMP: arming the recorded track takes it out of the resample bus")

        h.ok({"type": "arm", "track": -1})

        # --- GAIN is the box's trim ---------------------------------------
        h.ok({"type": "set_param", "instrument": "synth", "slot": "engine",
              "idx": 1, "value": 1.0})              # +24 dB
        loud = play(h.path("loud.wav"))
        g = 10.0 ** (24.0 / 20.0)
        near(loud[3 * BLOCK + 100], base[3 * BLOCK + 100] + base[2 * BLOCK + 100] * g,
             "GAIN +24 dB on the resample bus")
        ok("live-input: GAIN is a dB trim on whatever SOURCE selected")


# ---------------------------------------------------------------------------
def sidechain(a):
    """modulation-spec §4: the `input` source is whatever SOURCE is set to."""
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "DC",
            "engine": {"id": "arbtest/abi2", "params": {"level": 0.4}},
            "env": FLAT}})
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 0, "on": True})
        h.note(60, True, 1.0)
        h.render(4 * BLOCK)
        h.ok({"type": "note", "on": False, "note": 60})
        h.ok({"type": "record", "track": 0, "on": False})
        h.render(BLOCK)
        h.ok({"type": "transport", "op": "stop"})
        h.ok({"type": "arm", "track": -1})

        # `arbtest/sidemod` reports side[0][0] as FIRST; route it somewhere
        # inaudible (abi2's RATE) so the reading cannot feed back.
        tab = {"instance": "s", "engine": "arbtest/sidemod", "params": {},
               "sidechain": "input"}
        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "SIDE",
            "engine": {"id": "arbtest/abi2", "params": {"level": 0.0}}, "env": FLAT,
            "mod": {"format": 1, "kind": "modrack", "tabs": [tab], "bypass": False,
                    "routes": [{"source": "s", "output": "first",
                                "target": "engine.rate", "depth": 1.0,
                                "curve": "lin"}]}}})
        h.ok({"type": "meters", "on": True, "hz": 30})

        def live(frames):
            # Back to the top of the clip each time: the take is four blocks
            # long and a run that walks off the end is measuring silence.
            h.ok({"type": "transport", "op": "stop"})
            h.ok({"type": "transport", "op": "locate", "position": 0})
            h.ok({"type": "transport", "op": "start"})
            h.c.clear_pushes()
            h.render(frames)
            h.c.wait_push("meters")    # one per offline render; not 50 ms of quiet (#538)
            h.c.state()                # and in before this reply, so [-1] is the render's
            ms = h.c.pushes("meters")
            assert ms, "no meters push"
            return ms[-1].get("mod", {}).get("engine.rate", 0.0)

        h.ok({"type": "input_source", "source": "mic"})
        near(live(3 * BLOCK), 0.5, "`input` at MIC, offline")      # rate knob 0.5 + 0
        ok("sidechain `input`: MIC offline is silence, so the route reads its knob")

        h.ok({"type": "input_source", "source": "resample"})
        assert live(3 * BLOCK) > 0.55, "resample should reach the MOD tab"
        ok("sidechain `input`: it follows SOURCE — RSMP puts the master on the tab")


def main():
    a = args()
    messages(a)
    audio(a)
    sidechain(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
