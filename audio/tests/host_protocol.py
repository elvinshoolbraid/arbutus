#!/usr/bin/env python3
"""The protocol itself (§8.2): handshake, state, and surviving rubbish."""

import gc
import json
import os
import sys
import tempfile
import weakref

from hostcase import Host, args, ok
from arbutus_ctl import ProtocolError, connect


def strip(doc):
    d = dict(doc)
    d.pop("req", None)
    return d


def main():
    a = args()

    # --- hello mismatch is refused, and the connection survives it ----------
    with Host(a.exe, [a.boxes]) as h:
        r = h.req({"type": "hello", "proto": 2, "abi": 2, "firmware": "future-ui"})
        assert r["type"] == "error" and r["message"] == "NEEDS FIRMWARE UPDATE", r
        ok("hello: proto 2 refused with %s" % r["message"])
        r = h.req({"type": "hello", "proto": 1, "abi": 9, "firmware": "future-ui"})
        assert r["type"] == "error" and r["message"] == "NEEDS FIRMWARE UPDATE", r
        ok("hello: abi 9 refused with %s" % r["message"])
        r = h.req({"type": "hello", "proto": 1, "abi": 2, "firmware": "arbutus-ctl"})
        assert r["type"] == "hello", r
        ok("hello: the connection survived two refusals")

        # --- `motion_sensor` (#116, device-os-spec § 6.5) -------------------
        # The UI draws `NO SENSOR` and hides `KEY VEL` from this. It is in the
        # handshake so the first frame knows, and in the state document
        # because it changes when a fitted sensor stops answering.
        assert r["motion_sensor"] is False, r
        ok("hello: motion_sensor is %r with no --imu" % r["motion_sensor"])
        st = h.c.state()
        assert st["motion_sensor"] is False, st
        ok("state: motion_sensor is %r and matches the handshake" % st["motion_sensor"])

    # --imu on a path that is not an I2C bus is the "no sensor" case, which is
    # the one every board but P3 is in. The true case needs an LSM6DSO.
    with Host(a.exe, [a.boxes], extra=["--imu", "/nonexistent/i2c-9"]) as h:
        r = h.req({"type": "hello", "proto": 1, "abi": 2, "firmware": "arbutus-ctl"})
        assert r["motion_sensor"] is False, r
        assert h.c.state()["motion_sensor"] is False
        ok("hello: --imu on a bus that will not open still reports no sensor")

    with Host(a.exe, [a.boxes]) as h:

        # --- malformed JSON is an error, not a dropped connection ----------
        h.c.send_raw = None
        h.c._sock.sendall(b"{this is not json\n")
        m = h.c.recv()
        assert m["type"] == "error" and m["code"] == "bad_json", m
        ok("error: malformed JSON -> %s" % m["message"])
        assert h.c.state()["type"] == "state"
        ok("error: the connection still works")

        # --- unknown message ------------------------------------------------
        r = h.req({"type": "wibble"})
        assert r["type"] == "error" and r["code"] == "bad_request", r
        ok("error: unknown message refused")

    # --- the pushed state is the state a fresh request returns -------------
    with Host(a.exe, [a.boxes]) as h:
        edits = [
            {"type": "load_engine", "slot": "engine", "id": "arbutus/example"},
            {"type": "set_param", "slot": "engine", "idx": 1, "value": 0.42},
            {"type": "add_tab", "engine": "arbutus/biquad", "instance": "lp"},
            {"type": "bind", "slot": 0, "instance": "lp", "param": "cutoff",
             "lo": 0.1, "hi": 0.9, "curve": "s", "depth": 1.0},
            {"type": "set_macro", "slot": 0, "value": 0.7},
            {"type": "set_range", "slot": 0, "target": 0, "lo": 0.2, "hi": 0.6},
            {"type": "set_sidechain", "index": 0, "source": "none"},
            {"type": "mixer", "track": 1, "gain": 0.5, "pan": -0.3},
            {"type": "env", "a": 0.02, "d": 0.3, "s": 0.6, "r": 0.5, "mode": "mono"},
            {"type": "tempo", "bpm": 96.0},
            {"type": "instrument", "active": "drum"},
            {"type": "instrument", "active": "synth"},
            {"type": "set_clips", "track": 2,
             "clips": [{"start_frame": 100, "len": 200, "src_offset": 0, "gain": 0.8}]},
        ]
        for e in edits:
            h.c.clear_pushes()
            r = h.req(e)
            assert r["type"] in ("ok", "rack"), (e, r)
            # Wait on the push itself: drain() alone gave up after 50 ms of quiet,
            # which is the host's own longest hold (#536). Then collect any after it.
            assert h.c.wait_push("state"), "no state push after %s" % e["type"]
            h.c.drain(0.3)
            pushed = [p for p in h.c.pushes("state")]
            fresh = h.c.state()
            assert strip(pushed[-1]) == strip(fresh), (
                e["type"], json.dumps(strip(pushed[-1]))[:400],
                json.dumps(strip(fresh))[:400])
        ok("state: pushed after every edit and equal to a fresh request (%d edits)" % len(edits))

        # the rack-edit replies carry the whole document, so the UI never
        # computes rack state itself (§8.2)
        r = h.req({"type": "bind", "slot": 1, "instance": "lp", "param": "q",
                   "lo": 0.0, "hi": 1.0})
        assert r["type"] == "rack" and r["rack"]["kind"] == "rack", r
        assert r["rack"]["macros"][-1]["targets"][0]["param"] == "q", r["rack"]
        assert r["rack"]["requires"] == ["arbutus/biquad"], r["rack"]
        ok("rack: every edit replies with the whole §6.7 document")

    # --- connect() leaves no reference cycle behind (#179) ------------------
    # connect() waits for the socket to appear, so its early attempts always
    # raise, and an `except ... as e` value that outlives the block is a cycle:
    # the exception's traceback refers to the frame that caught it, so that
    # frame -- and, through f_back, its caller's, with all of the caller's
    # locals -- stays reachable until the cycle collector happens to run. The
    # caller is usually hostcase.Host.__init__, whose local `self` is the host,
    # which is how a test host's temp directory outlived `del h` on the CM4 and
    # not in WSL (5 runs of 5 each way) with nothing but allocation counts
    # between them. A canary in the calling frame, with the collector off, says
    # it in one assertion on any machine.
    absent = os.path.join(tempfile.gettempdir(), "arbutus-absent-%d.sock" % os.getpid())
    assert not os.path.exists(absent), absent

    class Canary:
        pass

    def attempt(_canary_of_this_frame):
        try:
            connect(absent, retries=2)
        except ProtocolError:
            return
        raise AssertionError("something answered on %s" % absent)

    gc.collect()
    gc.disable()
    try:
        canary = Canary()
        flown = weakref.ref(canary)
        attempt(canary)
        del canary
        assert flown() is None, "connect() kept its caller's frame alive"
        # The control (#56): the same object in a cycle survives the same `del`,
        # so the assertion above is not passing for want of anything to hold.
        caged = Canary()
        caged.itself = caged
        held = weakref.ref(caged)
        del caged
        assert held() is not None, "the control was freed with no collection"
    finally:
        gc.enable()
    gc.collect()
    assert held() is None, "the control survived a collection"
    ok("connect: a failed attempt keeps no exception, so the caller's frame dies with the call")
    return 0


if __name__ == "__main__":
    sys.exit(main())
