#!/usr/bin/env python3
"""Pipeline + the host-vs-arbutus-run conformance test (engine-spec.md §5, §9.3).

The host must not colour the sound. Running the template synth into the biquad
through the *rack* has to produce the same samples as running the two boxes
back to back in arbutus-run, within the golden tolerance of -100 dBFS. If it
does not, the host is an effect nobody asked for.
"""

import os
import subprocess
import sys

from hostcase import Host, args, compare, ok, peak, read_wav

# The template's golden script, replayed as protocol messages:
# (events to send, frames to render after them). Rendering in chunks is what
# puts a note on an exact frame, because a `note` message lands at the start of
# the next block (PROTOCOL.md, "Timing"). The note frames of the
# template's golden script fall out of the running total: 0, 12000, 14000,
# 20000, 26000, end at 32768.
SCRIPT = [
    ([(True, 60, 0.90), (True, 64, 0.75), (True, 67, 0.60)], 12000),
    ([(False, 60, 0.0)],                                      2000),
    ([(True, 72, 1.00)],                                      6000),
    ([(False, 64, 0.0), (False, 67, 0.0)],                    6000),
    ([(False, 72, 0.0)],                                      6768),
]
TOTAL = 32768


def patch(fx):
    """A patch that installs the engine, the ENV page and the rack in one go.

    The ENV values are arbutus-run's defaults so the two runs hear the same
    envelope.
    """
    p = {"type": "load_patch", "patch": {
        "format": 1, "kind": "patch", "name": "CONFORM",
        "engine": {"id": "arbutus/example", "params": {}},
        "env": {"a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"}}}
    if fx:
        p["patch"]["fx"] = fx
    return p


def main():
    a = args()
    example = os.path.join(a.boxes, "arbutus", "example")
    biquad = os.path.join(a.boxes, "arbutus", "biquad")

    # --- 1. engine alone: non-silent, the length that was asked for ---------
    # One `load_patch` builds the whole chain once. Loading the engine and then
    # the rack would build it twice, and the second build crossfades (§8) -- the
    # first 256 frames would be part old chain, which is correct behaviour and
    # useless for a sample-exact comparison.
    with Host(a.exe, [a.boxes]) as h:
        h.ok(patch(None))
        dry = h.path("dry.wav")
        chunks = []
        for notes, frames in SCRIPT:
            for on, note, vel in notes:
                h.note(note, on, vel)
            p = h.path("dry_%d.wav" % len(chunks))
            h.render(frames, p)
            chunks.append(p)
        join(chunks, dry)
    rate, ch = read_wav(dry)
    assert rate == 48000, rate
    assert len(ch) == 2 and len(ch[0]) == TOTAL, (len(ch), len(ch[0]))
    assert peak(ch) > 0.01, "the ENGINE slot rendered silence"
    ok("pipeline: %d frames at %d Hz, peak %.3f" % (len(ch[0]), rate, peak(ch)))

    # --- 2. the same, through a rack holding the biquad ---------------------
    with Host(a.exe, [a.boxes]) as h:
        h.ok(patch({"format": 1, "kind": "rack", "name": "ONE",
                    "tabs": [{"instance": "lp", "engine": "arbutus/biquad", "params": {}}]}))
        chunks = []
        for notes, frames in SCRIPT:
            for on, note, vel in notes:
                h.note(note, on, vel)
            p = h.path("fx_%d.wav" % len(chunks))
            h.render(frames, p)
            chunks.append(p)
        wet = h.path("wet.wav")
        join(chunks, wet)
    _, wch = read_wav(wet)
    assert len(wch[0]) == TOTAL
    diff = max(abs(x - y) for x, y in zip(ch[0], wch[0]))
    assert diff > 0.01, "the FX rack did nothing: peak difference %.6f" % diff
    ok("rack: the biquad changed the output (peak difference %.3f)" % diff)

    # --- 3. the same two boxes back to back in arbutus-run ------------------
    tmp = os.path.dirname(wet)
    a1 = os.path.join(tmp, "run_engine.wav")
    a2 = os.path.join(tmp, "run_fx.wav")
    notes_txt = os.path.join(a.audio_root, "sdk", "template", "tests", "notes.txt")
    subprocess.run([a.run, example, "--notes", notes_txt, "--frames", "512", "--sr", "48000",
                    "--len", str(TOTAL), "--out", a1, "--wav-bits", "32"], check=True)
    subprocess.run([a.run, biquad, "--in", a1, "--sr", "48000", "--frames", "512",
                    "--out", a2, "--wav-bits", "32"], check=True)
    compare(a2, wet, -100.0, "host vs arbutus-run")
    ok("conformance: the host is transparent")
    return 0


def join(parts, out):
    """Concatenate float32 stereo WAVs written by the host into one file."""
    import struct
    frames = []
    rate = 48000
    for p in parts:
        rate, ch = read_wav(p)
        frames.append(ch)
    n = sum(len(f[0]) for f in frames)
    data = bytearray()
    for f in frames:
        for i in range(len(f[0])):
            data += struct.pack("<ff", f[0][i], f[1][i])
    with open(out, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, rate, rate * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", len(data)) + bytes(data))


if __name__ == "__main__":
    sys.exit(main())
