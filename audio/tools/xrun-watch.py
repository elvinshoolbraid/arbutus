#!/usr/bin/env python3
"""Count a live host's xruns under the heaviest library load (audio/PI.md § 3).

Connects to a running `arbutus-audio` (live, not --offline), loads an 8-voice
`va` patch through `colourbass.rack`, holds the chord, and for SECONDS sweeps
the filter at 30 Hz -- control traffic standing in for an encoder being turned
while the screen redraws -- and counts what the host pushes. An xrun is the
host's own §6.8 count (a block that took longer than its period), the number
the CPU flag is raised from.

    audio/tools/xrun-watch.py --socket /tmp/a.sock [--seconds 300]

C2 (#75) adds the rest of the heaviest scenario: `--tape` first records half a
second on each of the eight tracks, so every medium is allocated (and, under
`mlockall`, resident — the RAM worst case), then rolls the transport and
records onto track 0 for the whole run with 30 Hz meters on, as P7's
`latency-probe.py --load` does. `--no-sweep` holds the chord without turning
the filter (no state push per step), and `--json` writes the counts.
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "arbutus-ctl"))
from arbutus_ctl import connect  # noqa: E402

LIB = os.path.normpath(os.path.join(HERE, "..", "library"))
CHORD = [48, 52, 55, 59, 60, 64, 67, 71]


def doc(rel):
    with open(os.path.join(LIB, rel)) as f:
        return json.load(f)


def run_xruns(base_total, pushes):
    """(this run's xruns, the host's lifetime total, CPU-flag pushes) from the
    `xrun` pushes received during the run and the state document's
    `xrun.total` when it began. A push's `total` counts since the host started,
    and on the device the host runs from boot, so the last push alone is the
    lifetime count, not the run's (#626)."""
    last, flagged = base_total, 0
    for p in pushes:
        if p.get("type") == "xrun":
            last = max(last, p.get("total", 0))
            flagged += bool(p.get("flag"))
    return last - base_total, last, flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--tape", action="store_true",
                    help="allocate all eight media, then record track 0 throughout")
    ap.add_argument("--no-sweep", action="store_true",
                    help="hold the chord without the 30 Hz filter sweep")
    ap.add_argument("--json", help="also write the counts here")
    a = ap.parse_args()
    with connect(a.socket, timeout=20.0) as c:
        hello = c.hello()
        if hello.get("offline"):
            sys.exit("xrun-watch: the host is --offline; this counts a live device")
        c.ok({"type": "instrument", "active": "synth"})
        c.ok({"type": "load_patch", "instrument": "synth", "patch": doc("synth/va/glass-reed.patch")})
        c.ok({"type": "load_rack", "instrument": "synth", "rack": doc("fx/racks/colourbass.rack")})
        idx = next(i for i, p in enumerate(c.state()["instruments"]["synth"]["engine"]["desc"]["params"])
                   if p["id"] == "cutoff")
        # A UI attached to the same host mirrors the engine change and answers
        # it with `panic` (`Instrument.set_engine` -> all notes off), which would
        # kill a chord struck at once — C2's first loaded runs held no chord at
        # all. Let it settle first.
        time.sleep(1.0)
        for n in CHORD:
            c.ok({"type": "note", "on": True, "note": n, "vel": 0.9})
        c.ok({"type": "meters", "on": True, "hz": 30 if a.tape else 2})
        if a.tape:
            c.ok({"type": "transport", "op": "start", "position": 0})
            for tr in range(7, -1, -1):           # track 0 last: it keeps recording
                c.ok({"type": "arm", "track": tr})
                c.ok({"type": "record", "track": tr, "on": True})
                if tr:
                    time.sleep(0.5)
                    c.ok({"type": "record", "track": tr, "on": False})
        base = c.state()["xrun"].get("total")
        if base is None:
            sys.exit("xrun-watch: this host's state has no xrun.total (older than #626); "
                     "deploy the same checkout as this tool")
        c.clear_pushes()
        t0 = time.monotonic()
        xpushes, meters, loud = [], 0, 0
        struck = 0.0
        while (t := time.monotonic() - t0) < a.seconds:
            # An attached UI silences notes it does not own when an instrument
            # key is pressed (C2's page walk: SYNTH killed the chord), so under
            # --tape the chord is struck again every 2 s, as a player would.
            if a.tape and t - struck >= 2.0:
                struck = t
                for n in CHORD:
                    c.request({"type": "note", "on": True, "note": n, "vel": 0.9})
            if not a.no_sweep:
                v = 0.5 + 0.35 * math.sin(2 * math.pi * t / 4.0)
                c.request({"type": "set_param", "instrument": "synth", "slot": "engine",
                           "idx": idx, "value": v})
            c.drain(1 / 30)
            for p in c.pushes():
                if p.get("type") == "xrun":
                    xpushes.append(p)
                elif p.get("type") == "meters":
                    meters += 1
                    # the instrument's own meter: master carries the tape too
                    loud += max(p.get("instrument", p.get("master", [0, 0]))) > 0.01
            c.clear_pushes()
        if a.tape:
            c.ok({"type": "record", "track": 0, "on": False})
            c.ok({"type": "transport", "op": "stop"})
            c.ok({"type": "arm", "track": -1})
        c.ok({"type": "meters", "on": False})
        for n in CHORD:
            c.ok({"type": "note", "on": False, "note": n, "vel": 0.0})
    total, lifetime, flagged = run_xruns(base, xpushes)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"seconds": a.seconds, "xruns": total, "host_lifetime_xruns": lifetime,
                       "cpu_flag_pushes": flagged,
                       "meter_pushes": meters, "loud": loud, "tape": a.tape,
                       "sweep": not a.no_sweep}, f)
    print(f"{a.seconds:.0f} s live: {total} xruns, {flagged} CPU-flag pushes, "
          f"{meters} meter pushes ({loud} with sound); the host's lifetime total {lifetime}")
    return 0 if meters and loud >= meters - 1 else 1   # the first push can precede the attack


if __name__ == "__main__":
    sys.exit(main())
