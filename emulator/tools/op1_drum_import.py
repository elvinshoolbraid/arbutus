#!/usr/bin/env python3
"""Split an OP-1 drum patch (`.aif` from op1.fun) into a kit folder of WAVs.

An OP-1 drum patch is one long AIFF carrying all 24 pads, with the slice
points stored in an `APPL` chunk as JSON tagged `op-1`. So there is nothing to
detect: `start[]` and `end[]` give exact sample offsets, in units of 1/4096th
of a frame. Transient detection would only be guessing at numbers the file
already states.

    venv/bin/python tools/op1_drum_import.py <patch.aif> [-o library/drum/sampler/<kit>]
    venv/bin/python tools/op1_drum_import.py --info <patch.aif>

The kit folder is what `engines.drum_sampler.DrumSamplerEngine.load()` wants:
up to 24 WAVs, mapped to the chromatic keys in sorted order. Slices are
written as 16-bit mono WAV — the only format the engines read.
"""

import argparse
import json
import os
import struct
import sys
import wave

#: OP-1 stores slice offsets in 1/4096th of a frame.
OP1_FIXED = 4096

#: the drum engine maps at most this many files to keys.
NUM_KEYS = 24


def _read_f80(b: bytes) -> float:
    """AIFF sample rate is an 80-bit IEEE extended float."""
    expon = struct.unpack(">H", b[:2])[0]
    mant = struct.unpack(">Q", b[2:10])[0]
    sign = -1 if expon & 0x8000 else 1
    expon &= 0x7FFF
    if expon == 0 and mant == 0:
        return 0.0
    return sign * mant * 2.0 ** (expon - 16383 - 63)


def read_op1_patch(path: str) -> dict:
    """Pull the COMM/SSND/APPL content out of an OP-1 AIFF."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"FORM" or d[8:12] != b"AIFF":
        raise ValueError(f"{path}: not an AIFF file")

    out = {"meta": None, "pcm": None, "frames": 0, "channels": 1,
           "width": 2, "rate": 44100}
    i = 12
    while i < len(d) - 8:
        cid = d[i:i + 4]
        size = struct.unpack(">I", d[i + 4:i + 8])[0]
        body = d[i + 8:i + 8 + size]
        if cid == b"COMM":
            out["channels"] = struct.unpack(">H", body[0:2])[0]
            out["frames"] = struct.unpack(">I", body[2:6])[0]
            out["width"] = struct.unpack(">H", body[6:8])[0] // 8
            out["rate"] = int(round(_read_f80(body[8:18])))
        elif cid == b"SSND":
            offset = struct.unpack(">I", body[0:4])[0]
            out["pcm"] = body[8 + offset:]
        elif cid == b"APPL" and body[:4] == b"op-1":
            out["meta"] = json.loads(body[4:].split(b"\x00")[0].decode("latin1"))
        i += 8 + size + (size & 1)

    if out["meta"] is None:
        raise ValueError(f"{path}: no op-1 APPL chunk — not an OP-1 patch")
    if out["meta"].get("type") != "drum":
        raise ValueError(f"{path}: op-1 type is {out['meta'].get('type')!r}, not 'drum'")
    if out["pcm"] is None:
        raise ValueError(f"{path}: no SSND chunk")
    if out["width"] != 2:
        raise ValueError(f"{path}: {out['width'] * 8}-bit source; only 16-bit is handled")
    return out


def slices(meta: dict, frames: int) -> list:
    """(start, end) frame pairs, dropping empties and exact repeats.

    OP-1 pads the unused tail of a kit by repeating the last offsets, so the
    24 slots are an upper bound, not a count.
    """
    out, seen = [], set()
    for s, e in zip(meta.get("start", []), meta.get("end", [])):
        s, e = s // OP1_FIXED, min(e // OP1_FIXED, frames)
        if e <= s or (s, e) in seen:
            continue
        seen.add((s, e))
        out.append((s, e))
    return out[:NUM_KEYS]


def split(path: str, out_dir: str) -> list:
    p = read_op1_patch(path)
    cuts = slices(p["meta"], p["frames"])
    if not cuts:
        raise ValueError(f"{path}: no non-empty slices")

    os.makedirs(out_dir, exist_ok=True)
    step = p["channels"] * p["width"]
    written = []
    for n, (s, e) in enumerate(cuts, 1):
        # AIFF is big-endian, WAV is little — flip each frame's bytes.
        raw = p["pcm"][s * step:e * step]
        pcm = bytearray(len(raw))
        pcm[0::2], pcm[1::2] = raw[1::2], raw[0::2]
        name = os.path.join(out_dir, f"{n:02d}.wav")
        with wave.open(name, "wb") as w:
            w.setnchannels(p["channels"])
            w.setsampwidth(p["width"])
            w.setframerate(p["rate"])
            w.writeframes(bytes(pcm))
        written.append((name, (e - s) / p["rate"]))
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("patch", help="OP-1 drum patch (.aif)")
    ap.add_argument("-o", "--out", help="kit folder to write (default: "
                                        "library/drum/sampler/<patch name>)")
    ap.add_argument("--info", action="store_true",
                    help="print the slice table and write nothing")
    a = ap.parse_args(argv)

    if a.info:
        p = read_op1_patch(a.patch)
        cuts = slices(p["meta"], p["frames"])
        print(f"{a.patch}: {p['meta'].get('name')!r}, {p['frames']} frames "
              f"@ {p['rate']} Hz, {len(cuts)} slices")
        for n, (s, e) in enumerate(cuts, 1):
            print(f"  {n:2d}  {s:8d}-{e:8d}  {(e - s) / p['rate']:.3f}s")
        return 0

    out = a.out
    if out is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        stem = os.path.splitext(os.path.basename(a.patch))[0]
        out = os.path.join(here, "library", "drum", "sampler", stem)
    written = split(a.patch, out)
    print(f"{out}: {len(written)} slices")
    for name, secs in written:
        print(f"  {os.path.basename(name):10s} {secs:.3f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
