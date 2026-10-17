#!/usr/bin/env python3
"""#673: a box with a wrongly typed `engine.json` is refused, never a crash.

The m-audio-sdk review's two manifests: `"version": 1` (a number where the
semver string goes) and `"format": "1"` (a string where the number goes). Either
one anywhere under a `--library` root made nlohmann's typed reads throw, and
nothing caught it, so the host aborted at start-up (exit 134) and again on
every `rescan` and `load_engine`: on the device, a crash loop with no sound.

Pinned here, with one good box beside the two bad ones in the same root: the
host starts, `library` and `rescan` answer, `load_engine` on either bad box is
`ENGINE BROKEN`, and the good box still loads.

#786 adds the boxes installed *after* the host started, so only a `rescan`
can find them: one per hole the review found (no `kind`, a parameter's `min`
as a string, `format: 1.5`, `abi: 2.5`) and one good box. Each bad one is
ENGINE BROKEN (not NEEDS ENGINE, not loaded) and the good one loads; a no-op
`rescan` leaves all five NEEDS ENGINE and fails here.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402

GOOD = "arbutus/va"
BAD = {                       # id -> the one field made the wrong type
    "arbutus/osc": ("version", 1),
    "arbutus/fm": ("format", "1"),
}


LATE_GOOD = "arbutus/eq3"
LATE_BAD = {                  # id -> (what is wrong, how the manifest is made so)
    "arbutus/noise": ("no kind", lambda m: m.pop("kind")),
    "arbutus/drive": ("params[0].min as a string",
                      lambda m: m["params"][0].update(min=str(m["params"][0]["min"]))),
    "arbutus/crusher": ("format 1.5", lambda m: m.update(format=1.5)),
    "arbutus/delay": ("abi + 0.5", lambda m: m.update(abi=m["abi"] + 0.5)),
}


def copy_box(a, root, box_id, field=None, value=None, edit=None):
    src = os.path.join(a.boxes, box_id)
    dst = os.path.join(root, box_id)
    shutil.copytree(src, dst)
    if field is not None:
        edit = lambda m: m.__setitem__(field, value)              # noqa: E731
    if edit is not None:
        path = os.path.join(dst, "engine.json")
        with open(path) as f:
            m = json.load(f)
        edit(m)
        with open(path, "w") as f:
            json.dump(m, f)


def refused(h, msg):
    reply = h.req(msg)
    assert reply.get("type") == "error", reply
    return reply.get("code"), reply.get("message")


def main():
    a = args()
    root = tempfile.mkdtemp(prefix="arbutus-badbox-")
    try:
        copy_box(a, root, GOOD)
        for box_id, (field, value) in BAD.items():
            copy_box(a, root, box_id, field, value)

        with Host(a.exe, [root]) as h:
            ok("the host starts with a wrongly typed engine.json in its library")

            listed = {e["id"] for e in h.ok({"type": "library"})["engines"]}
            assert GOOD in listed, sorted(listed)
            h.ok({"type": "rescan"})
            listed = {e["id"] for e in h.ok({"type": "library"})["engines"]}
            assert GOOD in listed, sorted(listed)
            ok("`library` and `rescan` answer, and the good box is still indexed")

            for box_id, (field, value) in BAD.items():
                code, message = refused(h, {"type": "load_engine", "slot": "engine",
                                            "id": box_id})
                assert code == "engine_broken", (box_id, code, message)
                assert "ENGINE BROKEN" in message, (box_id, message)
                ok("%s with %s = %r is ENGINE BROKEN" % (box_id, field, json.dumps(value)))

            h.ok({"type": "load_engine", "slot": "engine", "id": GOOD})
            h.ok({"type": "rescan"})
            ok("the good box still loads, and the host is still up after both refusals")

            # #786: installed after start, so only the rescan can find them.
            copy_box(a, root, LATE_GOOD)
            for box_id, (_, edit) in LATE_BAD.items():
                copy_box(a, root, box_id, edit=edit)
            code, _ = refused(h, {"type": "load_engine", "slot": "engine", "id": LATE_GOOD})
            assert code == "needs_engine", code
            h.ok({"type": "rescan"})
            wrong = []                    # every row, not only the first to fail
            for box_id, (what, _) in LATE_BAD.items():
                reply = h.req({"type": "load_engine", "slot": "engine", "id": box_id})
                if reply.get("code") != "engine_broken":
                    wrong.append((box_id, what, reply))
                    print("FAIL installed after start, %s with %s: %r" % (box_id, what, reply))
                else:
                    ok("installed after start, %s with %s is ENGINE BROKEN" % (box_id, what))
            assert not wrong, wrong
            h.ok({"type": "load_engine", "slot": "engine", "id": LATE_GOOD})
            ok("installed after start, %s loads after the rescan" % LATE_GOOD)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
