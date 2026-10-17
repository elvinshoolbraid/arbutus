#!/usr/bin/env python3
"""Write `audio/library/stock.json` — what the instrument ships with.

DDR-055: "Stock content does not lift." Nothing on disk distinguishes a patch
the player promoted from one that came in the image — both are a `.patch`
written by the same code — so the shipped list has to be written down, and
this writes it.

    python3 audio/tools/mkstock.py                 # rewrite the manifest
    python3 audio/tools/mkstock.py --check         # is it still accurate?

An item is what a **browser** offers: one row in the right-hand pane. That is
a `.patch` / `.rack` / `.seq` / `.wav` inside a box folder, or a kit *folder*
(`item_kind: "folder"`, §7.3) — not the box folders themselves (they are not
liftable; you cannot take `SAMPLER` out of the synth list) and not the loose
files inside a kit, which move with it.

Run it after adding stock content to the library. Content the player makes —
`mic-NN.wav`, a promoted patch, a project — is deliberately absent, which is
exactly what makes it liftable.

Standard library only: this runs from a bare checkout, like everything else in
`audio/tools/`.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY = os.path.join(os.path.dirname(HERE), "library")

#: the categories a browser walks (`emulator/app/library.py` CATEGORIES)
CATEGORIES = ("synth", "drum", "fx", "mod", "seq")
ITEM_SUFFIXES = (".patch", ".rack", ".seq", ".wav")
STOCK_FORMAT = 1


def scan(root: str):
    """-> sorted library-relative item paths, '/'-separated."""
    items = []
    for cat in CATEGORIES:
        cdir = os.path.join(root, cat)
        if not os.path.isdir(cdir):
            continue
        for box in sorted(os.listdir(cdir)):
            bdir = os.path.join(cdir, box)
            if box.startswith(".") or not os.path.isdir(bdir):
                continue
            for entry in sorted(os.listdir(bdir)):
                if entry.startswith("."):
                    continue
                path = os.path.join(bdir, entry)
                if os.path.isdir(path):
                    items.append("%s/%s/%s" % (cat, box, entry))
                elif entry.lower().endswith(ITEM_SUFFIXES):
                    items.append("%s/%s/%s" % (cat, box, entry))
    return sorted(items)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default=LIBRARY)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the manifest does not match the tree")
    a = ap.parse_args(argv)

    items = scan(a.library)
    path = os.path.join(a.library, "stock.json")

    if a.check:
        try:
            with open(path) as f:
                have = json.load(f).get("items") or []
        except (OSError, ValueError):
            print("stock.json missing or unreadable: %s" % path)
            return 1
        missing = [i for i in items if i not in set(have)]
        extra = [i for i in have if i not in set(items)]
        for i in missing:
            print("not listed: %s" % i)
        for i in extra:
            print("listed but gone: %s" % i)
        return 1 if (missing or extra) else 0

    with open(path, "w") as f:
        json.dump({"format": STOCK_FORMAT, "kind": "stock", "items": items},
                  f, indent=1)
        f.write("\n")
    print("%s — %d items" % (path, len(items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
