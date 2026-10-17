#!/usr/bin/env python3
"""Socket -> audio thread latency of a `note` on a live host (#73, P7).

Connects to a running `arbutus-audio` (live, not --offline) and plays PRESSES
probed notes, each written at a CLOCK_MONOTONIC stamp t0 and spaced by a random
15-45 ms so they land at random phases of the block clock. The host answers
each with a `probe` push (PROTOCOL.md § Playing › Timing): `t_ctl_ns` when the
control thread parsed it, `t1_ns` when the audio thread started the block that
carries it. One clock throughout (steady_clock is CLOCK_MONOTONIC on Linux).

    t1 - t0      the number: key write -> the block that plays it
    t_ctl - t0   socket + poll wake-up + JSON parse
    t1 - t_ctl   the wait in the command ring for the next block

--load plays the heaviest host scenario from bench-host.py (va glass-reed +
colourbass.rack, an 8-note chord held), rolls the transport, records onto tape
track 0 and subscribes to 30 Hz meters, as the UI does; the probed notes go
to the same instrument.

    audio/tools/latency-probe.py --socket /run/arbutus/audio.sock [--load] [--presses 400]
"""
import argparse
import json
import os
import random
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


def pct(v, p):
    s = sorted(v)
    return s[min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--presses", type=int, default=400)
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--seed", type=int, default=73)
    ap.add_argument("--json", help="also write the raw samples here")
    a = ap.parse_args()
    rng = random.Random(a.seed)

    with connect(a.socket, timeout=20.0) as c:
        hello = c.hello()
        if hello.get("offline"):
            sys.exit("latency-probe: the host is --offline; this measures a live one")
        st = c.state()
        sr, block = st.get("sample_rate", 48000), st.get("block", 128)
        if a.load:
            c.ok({"type": "load_patch", "instrument": "synth",
                  "patch": doc("synth/va/glass-reed.patch")})
            c.ok({"type": "load_rack", "instrument": "synth",
                  "rack": doc("fx/racks/colourbass.rack")})
            c.ok({"type": "instrument", "active": "synth"})
            for n in CHORD:
                c.ok({"type": "note", "on": True, "note": n, "vel": 0.9})
            c.ok({"type": "meters", "on": True, "hz": 30})
            c.ok({"type": "transport", "op": "start", "position": 0})
            c.ok({"type": "arm", "track": 0})
            c.ok({"type": "record", "track": 0, "on": True})
        else:
            c.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        time.sleep(0.5)                               # crossfades, first take
        c.clear_pushes()

        sent = {}
        for i in range(1, a.presses + 1):
            time.sleep(rng.uniform(0.015, 0.045))
            note = 84 + (i % 12)
            t0 = time.monotonic_ns()
            c.ok({"type": "note", "on": True, "note": note, "vel": 0.8, "probe": i})
            sent[i] = t0
            c.ok({"type": "note", "on": False, "note": note, "vel": 0.0})
        c.drain(0.5)
        probes = {p["id"]: p for p in c.pushes("probe")}
        xruns = c.pushes("xrun")

        if a.load:
            c.ok({"type": "record", "track": 0, "on": False})
            c.ok({"type": "transport", "op": "stop"})
            c.ok({"type": "meters", "on": False})
            for n in CHORD:
                c.ok({"type": "note", "on": False, "note": n, "vel": 0.0})
            c.ok({"type": "arm", "track": -1})

    return summarise(sent, probes, xruns, block, sr, a.load, a.json)


def summarise(sent, probes, xruns, block, sr, load, json_path=None):
    """Print the probe's numbers and write --json; the exit status. `sent` maps a
    probe id to its t0, `probes` an id to the host's `probe` push. With no probe
    answered (the failure this tool exists to catch) it says so and still writes
    the JSON: `pct` and `max` of nothing raised IndexError before either (#626)."""
    missing = [i for i in sent if i not in probes]
    rows = [(probes[i]["t1_ns"] - sent[i], probes[i]["t_ctl_ns"] - sent[i],
             probes[i]["t1_ns"] - probes[i]["t_ctl_ns"]) for i in sent if i in probes]
    per_block_ms = 1000.0 * block / sr
    print(f"block {block} @ {sr} Hz = {per_block_ms:.3f} ms; {'load' if load else 'idle'}; "
          f"{len(rows)}/{len(sent)} probes answered; xrun pushes {len(xruns)}"
          + (f", the host's lifetime total {xruns[-1].get('total')}" if xruns else ""))
    missed = 0
    if rows:
        for k, name in enumerate(("t1-t0 (total)", "t_ctl-t0 (socket)", "t1-t_ctl (ring)")):
            v = [r[k] / 1e6 for r in rows]
            print(f"  {name:18s} p50 {pct(v, 50):6.3f}  p99 {pct(v, 99):6.3f}  max {max(v):6.3f} ms"
                  f"   (p99 = {pct(v, 99) / per_block_ms:.2f} blocks)")
        # The bug #73 asks about: a note parsed before a block started that still
        # missed it. By construction the ring wait is < one period; a longer one
        # is a late callback or a stalled control thread, not quantisation.
        missed = sum(1 for r in rows if r[2] / 1e6 > per_block_ms)
        print(f"  ring wait over one block (a missed block): {missed}")
    else:
        print("  no probe answered: nothing to measure")
    if json_path:
        with open(json_path, "w") as f:
            json.dump({"block": block, "sr": sr, "load": load, "missing": missing,
                       "rows_ns": rows}, f)
    return 1 if missing or missed or not rows else 0

if __name__ == "__main__":
    sys.exit(main())
