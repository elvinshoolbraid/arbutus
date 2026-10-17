#!/usr/bin/env python3
"""mkkit — turn an emulator drum-kit folder into a format-1 `kit.json` (§7.3).

The emulator's kits are *folders of WAVs mapped to the chromatic keys in
sorted filename order* — an implicit contract that breaks the moment someone
renames a file. §7.3 replaces it with an explicit `pads` map, so this tool
reads the old folder in exactly the old order and writes the new one down.

    python3 audio/tools/mkkit.py emulator/library/drum/sampler/TR_707 \\
            --out audio/library/drum/drum/TR_707 --name TR-707

`--copy` (the default) copies the WAVs next to the kit.json, which makes the
kit folder self-contained and shareable — a kit is a *thing you send someone*
(§10), and a kit.json whose pads point back into another tree is not.
`--reference` writes relative paths instead and copies nothing, for when the
duplication costs more than it is worth.

Pads are written in the §15.3 shape, `{"file": ..., "params": {}}`: empty params
are arbutus/drum's defaults, which are the v1 sound. `--v1-shape` writes the
old `"0": "kick.wav"` strings, which every host still reads.

Standard library only: this runs from a bare checkout, like everything else in
audio/tests.
"""

import argparse
import json
import os
import shutil
import sys

MAX_PADS = 24          # 24 chromatic keys from F3 (arbutus/drum kBaseMidi = 53)


def kit_files(src):
    """The old implicit order: every .wav in the folder, sorted by name."""
    return sorted(f for f in os.listdir(src) if f.lower().endswith(".wav"))


def build(src, out, name=None, mode="copy", v1_shape=False):
    files = kit_files(src)
    if not files:
        raise SystemExit("%s holds no WAVs" % src)
    if len(files) > MAX_PADS:
        print("note: %d WAVs, only the first %d fit the 24 pads" %
              (len(files), MAX_PADS), file=sys.stderr)
    files = files[:MAX_PADS]

    os.makedirs(out, exist_ok=True)
    pads = {}
    for i, f in enumerate(files):
        if mode == "copy":
            shutil.copyfile(os.path.join(src, f), os.path.join(out, f))
            file = f
        else:
            file = os.path.relpath(os.path.join(src, f), out)
        pads[str(i)] = file if v1_shape else {"file": file, "params": {}}

    doc = {"format": 1, "kind": "kit",
           "name": (name or os.path.basename(src.rstrip("/"))).upper()[:12],
           "pads": pads}
    path = os.path.join(out, "kit.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    return path, len(pads)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="emulator/library/drum/sampler/<kit>")
    ap.add_argument("--out", required=True, help="kit folder to write")
    ap.add_argument("--name", default=None, help="kit name (<=12, UPPERCASE)")
    ap.add_argument("--reference", action="store_true",
                    help="point at the source WAVs instead of copying them")
    ap.add_argument("--v1-shape", action="store_true",
                    help='write pads as "0": "file.wav" (still read everywhere)')
    a = ap.parse_args(argv)
    path, n = build(a.src, a.out, a.name, "reference" if a.reference else "copy", a.v1_shape)
    print("%s — %d pads" % (path, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
