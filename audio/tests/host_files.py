#!/usr/bin/env python3
"""Stage J2: `rescan`, and the dot-prefixed folders the scan must not see.

DDR-055 makes every browser a file manager: LIFT takes an item, DROP moves it,
and an item that is never dropped goes to `.trash/`. The moves happen on the
UI side — it is the UI that owns the library tree — but the *host* holds a box
index built from the same roots (`--library`), so a move has to be followed by
`rescan` or the host's idea of the library and the browser's diverge.

D2 (#23) adds the other half: `library {op}` (#62), the same DDR-055 rules as
a *host* message, because the companion app talks to this process and not to
the UI. `test_library_ops` pins those.

`rescan` has existed since the first protocol (§8.2) and had no coverage. What
this pins:

  * `rescan` really re-reads the roots: a box folder that appears after the
    host started is offered by `library` **after** the message and not before.
  * a dot-prefixed folder is never indexed. `.originals/` (the unconverted
    masters a sample was made from) and `.trash/` (DDR-055's soft delete) both
    live inside `audio/library/`, which is a `--library` root, so without this
    a trashed item would still be offered and an undropped LIFT would not be a
    delete at all.
  * the skip is a *tree* skip: nothing under a dot folder is indexed either.

Nothing here loads a binary. `arb::box::Library` reads `engine.json` only —
`requires` is a set comparison that happens before any loading (§6.7) — so a
manifest with no `.so` beside it is exactly what the index is made of.
"""

import json
import os
import shutil
import sys
import tempfile

from hostcase import Host, args, ok, peak, read_wav

BOX_ID = "arbtest/filesbox"


def manifest(path, box_id=BOX_ID, version="1.0.0"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"format": 1, "kind": "engine", "id": box_id,
                   "version": version, "name": "FILESBOX",
                   "role": "effect", "params": []}, f)


def ids(h):
    reply = h.ok({"type": "library"})
    return {e["id"] for e in reply.get("engines") or []}


def test_rescan_sees_a_new_folder(a):
    root = tempfile.mkdtemp(prefix="arbutus-files-")
    try:
        with Host(a.exe, [a.boxes, root], block=128) as h:
            before = ids(h)
            assert BOX_ID not in before, "the fixture id is already in use"

            manifest(os.path.join(root, "user", "filesbox", "engine.json"))
            assert ids(h) == before, "the index moved without a rescan"

            h.ok({"type": "rescan"})
            after = ids(h)
            assert BOX_ID in after, sorted(after)
            assert before < after, "rescan dropped something it had"
            ok("rescan re-reads the roots: a folder written after the host "
               "started is offered only once the message arrives")

            # DDR-055's soft delete, as the browser performs it: the item is
            # moved into `.trash/` at the library root and nothing else.
            shutil.move(os.path.join(root, "user"),
                        os.path.join(root, ".trash", "user"))
            h.ok({"type": "rescan"})
            assert BOX_ID not in ids(h), "a trashed box is still offered"
            ok(".trash/ is invisible to the scan — an undropped LIFT really is "
               "a delete as far as the instrument is concerned")

            # ...and the skip is the whole subtree, not just the folder itself
            manifest(os.path.join(root, ".originals", "deep", "engine.json"),
                     box_id="arbtest/deepbox")
            h.ok({"type": "rescan"})
            assert "arbtest/deepbox" not in ids(h), "a dot folder was descended"
            ok("nothing under a dot-prefixed folder is indexed either — "
               ".originals/ keeps a sample's masters out of the browser")

            # and a real folder still comes back when it is dropped somewhere
            shutil.move(os.path.join(root, ".trash", "user"),
                        os.path.join(root, "user"))
            h.ok({"type": "rescan"})
            assert BOX_ID in ids(h), "a restored item did not come back"
            ok("a DROP out of .trash/ puts the item back in the index")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


# ---------------------------------------------------------------------------
# `library {op}` (#62): DDR-055's rules as a protocol message
# ---------------------------------------------------------------------------

def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{}")


def err(h, msg):
    """The refusal message for a call that must be refused."""
    reply = h.req(msg)
    assert reply.get("type") == "error", reply
    return reply.get("message"), reply.get("code")


def test_library_ops(a):
    root = tempfile.mkdtemp(prefix="arbutus-libop-")
    try:
        touch(os.path.join(root, "synth", "mine.patch"))
        touch(os.path.join(root, "synth", "taken.patch"))
        touch(os.path.join(root, "drum", "shipped.kit"))
        with open(os.path.join(root, "stock.json"), "w") as f:
            json.dump({"format": 1, "items": ["drum/shipped.kit"]}, f)

        # `root` **last**, the way every real invocation is written: the
        # read-only boxes first, the player's writable library after them
        # (`--library <slot>/boxes --library /data`). An op is relative to
        # that last root (#62 item 3) — against the first it would have
        # resolved into the slot a `.fw` replaces.
        with Host(a.exe, [a.boxes, root], block=128) as h:
            # -- rename ---------------------------------------------------
            r = h.ok({"type": "library", "op": "rename",
                      "path": "synth/mine.patch", "name": "yours.patch"})
            assert r["path"] == "synth/yours.patch", r
            assert os.path.exists(os.path.join(root, "synth", "yours.patch"))
            assert "engines" in r, "an op must answer with the library list"
            ok("library {op:rename} renames in place and answers with the "
               "library list, so one round trip both moves and re-indexes")

            # -- the three named refusals ---------------------------------
            assert err(h, {"type": "library", "op": "rename",
                           "path": "synth/nope.patch", "name": "x"}) == \
                ("NOT FOUND", "not_found")
            assert err(h, {"type": "library", "op": "rename",
                           "path": "synth/yours.patch", "name": "taken.patch"})[0] == "EXISTS"
            assert err(h, {"type": "library", "op": "rename",
                           "path": "drum/shipped.kit", "name": "x.kit"})[0] == "STOCK"
            assert os.path.exists(os.path.join(root, "drum", "shipped.kit"))
            ok("NOT FOUND / EXISTS / STOCK are the three refusals, and a "
               "refused op moved nothing")

            # Stock is the manifest, so it is not a guess: `taken.patch` sits
            # in the same folder, was written by the same code, and moves.
            h.ok({"type": "library", "op": "move", "path": "synth/taken.patch",
                  "to": "drum"})
            assert os.path.exists(os.path.join(root, "drum", "taken.patch"))
            ok("stock is `stock.json` and nothing else — a user file beside a "
               "shipped one moves")

            # -- which root an op is relative to (#62 item 3) -------------
            # `a.boxes` is the *first* `--library` root here, as it is in
            # every real invocation. An op that resolved against it would
            # find this file; against the writable root it must not.
            in_boxes = sorted(
                f for f in os.listdir(a.boxes)
                if os.path.isdir(os.path.join(a.boxes, f)))
            assert in_boxes, "the boxes root is empty -- build the boxes"
            assert err(h, {"type": "library", "op": "trash",
                           "path": in_boxes[0]})[0] == "NOT FOUND", in_boxes[0]
            ok("an op is relative to the **last** `--library` root: a folder "
               "that exists only in the read-only boxes root is NOT FOUND, "
               "so a phone cannot move a file inside the running slot")

            # -- escaping the root ----------------------------------------
            for bad in ("../evil", "/etc/passwd", "synth/../../evil"):
                assert err(h, {"type": "library", "op": "rename",
                               "path": bad, "name": "x"})[0] == "BAD PATH", bad
            ok("an absolute path or a `..` component is BAD PATH — the op "
               "cannot reach outside the library root")

            # -- trash, and out of it again -------------------------------
            r = h.ok({"type": "library", "op": "trash", "path": "synth/yours.patch"})
            assert r["path"] == ".trash/yours.patch", r
            assert not os.path.exists(os.path.join(root, "synth", "yours.patch"))
            # A second file of the same name must also survive: `.trash/` is
            # where every undropped LIFT goes, so a collision renames.
            touch(os.path.join(root, "synth", "yours.patch"))
            r = h.ok({"type": "library", "op": "trash", "path": "synth/yours.patch"})
            assert r["path"] == ".trash/yours-2.patch", r
            ok("trash is a move into `.trash/`, and a name already there gets "
               "`-2` rather than a refusal (two lifts of one name)")

            r = h.ok({"type": "library", "op": "move",
                      "path": ".trash/yours.patch", "to": "synth"})
            assert r["path"] == "synth/yours.patch", r
            ok("a move out of `.trash/` puts it back — which is what the UI's "
               "undo group replays")

            # -- delete ---------------------------------------------------
            h.ok({"type": "library", "op": "delete", "path": ".trash/yours-2.patch"})
            assert not os.path.exists(os.path.join(root, ".trash", "yours-2.patch"))
            assert err(h, {"type": "library", "op": "delete",
                           "path": "drum/shipped.kit"})[0] == "STOCK"
            ok("delete removes it for good, and stock cannot be deleted "
               "either — a shipped patch that deletes but does not rename "
               "would be the stranger rule")

            # -- the index really moved -----------------------------------
            manifest(os.path.join(root, "user", "filesbox", "engine.json"))
            h.ok({"type": "rescan"})
            assert BOX_ID in ids(h)
            r = h.ok({"type": "library", "op": "trash", "path": "user"})
            assert BOX_ID not in {e["id"] for e in r["engines"]}, r["engines"]
            ok("an op rescans before it answers: the box it trashed is gone "
               "from the list that comes back, with no second message")

            # -- mkdir (#979, D104: the app's New folder) -----------------
            r = h.ok({"type": "library", "op": "mkdir", "path": "synth/Pads"})
            assert r["path"] == "synth/Pads" and "engines" in r, r
            assert os.path.isdir(os.path.join(root, "synth", "Pads"))
            for path, want in (("synth/Pads", "EXISTS"),
                               ("drum/taken.patch/x", "NOT FOUND"),
                               ("drum/taken.patch", "EXISTS"),
                               ("nope/Pads", "NOT FOUND"),
                               ("synth/.hidden", "BAD PATH"),
                               ("../evil", "BAD PATH"),
                               ("engines", "ENGINES"),
                               ("engines/me", "ENGINES")):
                assert err(h, {"type": "library", "op": "mkdir",
                               "path": path})[0] == want, path
            assert not os.path.exists(os.path.join(root, "engines"))
            ok("mkdir makes one empty folder, refusing a taken name, a "
               "missing parent, a dot name and anything under engines/")

            # -- a bad op is a bad request, not a guess -------------------
            assert err(h, {"type": "library", "op": "sort"})[1] == "bad_request"
            # ...and `library {}` still lists boxes, unchanged since J2
            assert "engines" in h.ok({"type": "library"})
            ok("`library {}` is untouched — `op` is what turns the listing "
               "into a file operation")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


def box(root, box_id, caps, version="1.0.0"):
    path = os.path.join(root, "engines", box_id, "engine.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"format": 1, "kind": "engine", "id": box_id,
                   "version": version, "caps": caps, "params": []}, f)


SYNTH = ["audio_out", "events_in"]
EFFECT = ["audio_in", "audio_out"]


def test_engines_on_the_card(a):
    """#839 (D106, D125): the card is the one root, the slot its fallback.

    The card's copy of an engine wins; one the owner uninstalled (named in
    `engines/.stock.json`, then gone) never comes back from the slot; and
    the last engine of a kind refuses to go, by trash or delete, the box or
    any folder holding it.
    """
    card = tempfile.mkdtemp(prefix="arbutus-card-")
    slot = tempfile.mkdtemp(prefix="arbutus-slot-")
    try:
        box(card, "t/one", SYNTH)
        box(card, "t/two", SYNTH)
        box(card, "t/wet", EFFECT)
        box(slot, "t/two", SYNTH, version="2.0.0")
        box(slot, "t/spare", SYNTH)
        touch(os.path.join(card, "synth", "two", "a.patch"))
        with open(os.path.join(card, "engines", ".stock.json"), "w") as f:
            json.dump({"format": 1, "installed": {"t/two": ""}, "removed": []}, f)
        slot_boxes = os.path.join(slot, "engines")

        with Host(a.exe, [card], block=128,
                  extra=["--library-fallback", slot_boxes]) as h:
            def listed():
                return {e["id"]: e["dir"] for e in
                        h.ok({"type": "library"}).get("engines") or []}
            got = listed()
            assert got["t/two"].startswith(card), got
            assert got["t/spare"].startswith(slot), got
            ok("the card's copy wins over the slot's newer one; the slot "
               "offers what the card never had")

            h.ok({"type": "library", "op": "trash", "path": "engines/t/two"})
            got = listed()
            assert "t/two" not in got, got
            ok("an uninstalled stock engine does not come back from the slot")

            h.ok({"type": "library", "op": "delete", "path": "engines/t/one"})
            msg, code = err(h, {"type": "library", "op": "trash",
                                "path": "engines/t/spare"})
            assert "t/spare" in listed()
            # The slot's copy is not on the card: it is not the card's to
            # trash, so the refusal there is NOT FOUND, not the rule.
            assert code == "not_found", (msg, code)
            box(card, "t/last", SYNTH)
            h.ok({"type": "rescan"})
            # t/last and the slot's t/spare are both synths: one may go.
            h.ok({"type": "library", "op": "trash", "path": "engines/t/last"})
            box(card, "t/last", SYNTH)
            h.ok({"type": "rescan"})

            msg, code = err(h, {"type": "library", "op": "trash",
                                "path": "engines/t/wet"})
            assert (msg, code) == ("NEEDS ONE EFFECT ENGINE", "last_engine"), \
                (msg, code)
            msg, code = err(h, {"type": "library", "op": "delete",
                                "path": "engines"})
            assert code == "last_engine", (msg, code)
            assert os.path.isdir(os.path.join(card, "engines", "t", "wet"))
            ok("the last effect engine refuses trash and delete, and so does "
               "a folder holding it")

            h.ok({"type": "library", "op": "trash",
                  "path": "synth/two/a.patch"})
            ok("a patch is not an engine: it trashes as before")
    finally:
        shutil.rmtree(card, ignore_errors=True)
        shutil.rmtree(slot, ignore_errors=True)
    return 0


def test_a_fresh_card_gets_the_boxes_and_plays(a):
    """#839 item 1: `arbutus_os/engines.py` copies the slot's boxes onto an
    empty card, and a host given the card alone (no fallback) plays one."""
    repo = os.path.dirname(os.path.abspath(a.audio_root))
    if not os.path.isfile(os.path.join(repo, "arbutus_os", "engines.py")):
        # The public export (#1299): the device runtime does not cross (only a
        # data file of arbutus_os/ does).
        print("skip: a fresh card gets the boxes (arbutus_os/engines.py is not in this repo)")
        return 0
    sys.path.insert(0, repo)
    from arbutus_os import engines
    card = tempfile.mkdtemp(prefix="arbutus-fresh-")
    try:
        done = engines.sync(a.boxes, card, log=lambda *_: None)
        assert "arbutus/va" in done["copied"], done
        with Host(a.exe, [card], block=128) as h:
            got = {e["id"]: e["dir"] for e in
                   h.ok({"type": "library"}).get("engines") or []}
            assert got.get("arbutus/va", "").startswith(card), got
            h.ok({"type": "load_patch", "instrument": "synth", "patch": {
                "format": 1, "kind": "patch", "name": "fresh",
                "engine": {"id": "arbutus/va", "params": {}},
                "env": {"a": 0.005, "d": 0.2, "s": 0.9, "r": 0.2,
                        "mode": "poly"}}})
            h.note(60, True, 0.9)
            path = h.path("fresh.wav")
            h.render(24000, path)
            p = peak(read_wav(path)[1])
            assert p > 0.01, "silent from the card's copy: peak %.4f" % p
        ok("a fresh card gets the slot's boxes, and the card's va plays "
           "(%d copied, peak %.3f)" % (len(done["copied"]), p))
    finally:
        shutil.rmtree(card, ignore_errors=True)
    return 0


def main():
    a = args()
    for t in (test_rescan_sees_a_new_folder, test_library_ops,
              test_engines_on_the_card,
              test_a_fresh_card_gets_the_boxes_and_plays):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
