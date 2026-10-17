"""#853 — the live stream's situation document, played through *Sketch a song*.

`PROTOCOL.md` § The live stream: a phone (or a language model) that subscribes
with `live {on:true}` reads one document saying what the instrument is doing —
the screen, the keys, the transport, which tracks hold audio, the project — the
host's half (`AudioHost::live_doc`) with the UI's half (`ui_feed.situation`)
merged over it. `audio/host/live-schema.json` gives every field a sentence and
a unit; those sentences are the glossary a language model reads.

The test plays the *Sketch a song* lesson's script (`test_lessons.SCRIPTS`) on
a fresh instrument, the real UI on a real offline host, and after every step
asks the host for the document as a subscriber would have it. The record is
the first document and then one merge patch per step (RFC 7396, what the
stream pushes):

    app/ios/ArbutusTests/sketch-a-song-situations.json

so a change to what the instrument publishes reads as a short diff, and the
lessons that turn green by themselves (#854) and the app (#855) build on the
same file.

    python3 -m unittest tests.test_live_situation        checks the record
    ARBUTUS_WRITE_SITUATIONS=1 python3 -m unittest tests.test_live_situation
                                                         rewrites it (read the diff)

Every document of the run is also checked against the schema: a field the
instrument publishes that has no sentence there fails (`test_every_field_...`).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # emulator/
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.native_support import BOXES, library_copy                    # noqa: E402
from tests.test_lessons import NO_APP, SCRIPTS, Player, bare                # noqa: E402

SCHEMA = os.path.join(REPO, "audio", "host", "live-schema.json")
RECORD = os.path.join(REPO, "app", "ios", "ArbutusTests", "sketch-a-song-situations.json")
LESSON = "sketch-a-song"


def merge_diff(a, b):
    """The RFC 7396 patch from `a` to `b` (main.cpp's `merge_diff`, in Python)."""
    out = {}
    for k in a:
        if k not in b:
            out[k] = None
    for k, v in b.items():
        if k not in a:
            out[k] = v
        elif a[k] != v:
            out[k] = merge_diff(a[k], v) if isinstance(a[k], dict) and isinstance(v, dict) else v
    return out


def merge(doc, patch):
    """RFC 7396 applied: what a subscriber does with each push."""
    if not isinstance(patch, dict):
        return patch
    out = dict(doc) if isinstance(doc, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge(out.get(k), v)
    return out


def schema_faults(doc, schema, root, path="situation"):
    """-> [str]: every field of `doc` the schema does not describe, or types wrong.

    A small walker for the subset `live-schema.json` uses (type, properties,
    additionalProperties false, items, enum, $ref), so the check needs no
    package the gate does not have."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = dict(root["$defs"][name], **{k: v for k, v in schema.items() if k != "$ref"})
    faults = []
    if not str(schema.get("description", "")).strip():
        faults.append("%s: no description in live-schema.json" % path)
    t = schema.get("type")
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool}
    if t in kinds and not isinstance(doc, kinds[t]):
        return faults + ["%s: %r is not %s" % (path, doc, t)]
    if t == "integer" and (isinstance(doc, bool) or not isinstance(doc, int)):
        return faults + ["%s: %r is not an integer" % (path, doc)]
    if t == "number" and (isinstance(doc, bool) or not isinstance(doc, (int, float))):
        return faults + ["%s: %r is not a number" % (path, doc)]
    if "enum" in schema and doc not in schema["enum"]:
        faults.append("%s: %r is not one of %s" % (path, doc, schema["enum"]))
    if t == "object":
        props = schema.get("properties", {})
        for k, v in doc.items():
            if k not in props:
                faults.append("%s.%s: published, but live-schema.json has no sentence for it"
                              % (path, k))
            else:
                faults += schema_faults(v, props[k], root, "%s.%s" % (path, k))
    if t == "array":
        for i, v in enumerate(doc):
            faults += schema_faults(v, schema.get("items", {}), root, "%s[%d]" % (path, i))
    return faults


def schema_undescribed(schema, path="situation"):
    """-> [str]: every place in the schema itself with no sentence."""
    out = []
    if not str(schema.get("description", "")).strip():
        out.append(path)
    for k, v in schema.get("properties", {}).items():
        out += schema_undescribed(v, "%s.%s" % (path, k))
    if isinstance(schema.get("items"), dict):
        out += schema_undescribed(schema["items"], path + "[]")
    for k, v in schema.get("$defs", {}).items():
        out += schema_undescribed(v, "$defs." + k)
    return out


class Situations(Player):
    """The lesson player, its UI half really reaching the host, and the
    document asked for after every step."""

    def __init__(self, lib_root, state):
        super().__init__(lib_root, state)     # the UI's half reaches the host (#854)
        from app import project_file
        project_file.ensure_open(self.app)    # the shipping boot's call (#1179)
        self.docs = []                    # [(step, document)]

    def play(self, script):
        self.tick(4)
        self.docs.append(("(start)", self.situation()))
        for s in script:
            self.step(s)
            self.tick(4)
            self.docs.append((bare(s), self.situation()))
        return self.docs


def anonymise(docs):
    """Project ids carry today's date and an auto-name: number them instead,
    so the record does not change with the calendar."""
    seen = {}
    out = []
    for step, d in docs:
        d = json.loads(json.dumps(d))
        p = d.get("project", {})
        if "id" in p:
            n = seen.setdefault(p["id"], len(seen) + 1)
            p["id"] = "<project %d>" % n
            p["name"] = "<name %d>" % n
        out.append((step, d))
    return out


def as_record(docs):
    rows = [{"step": docs[0][0], "situation": docs[0][1]}]
    for (_, a), (step, b) in zip(docs, docs[1:]):
        rows.append({"step": step, "patch": merge_diff(a, b)})
    return rows


def write_record(rows):
    """One step a line, so a change reads as a short diff."""
    with open(RECORD, "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join("  " + json.dumps(r, sort_keys=True, ensure_ascii=False)
                                   for r in rows) + "\n]\n")


@NO_APP
class SketchASong(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BOXES):
            raise unittest.SkipTest("no audio/build: cmake --build audio/build")
        os.environ["ARB_NO_BATTERY"] = "1"          # the same record on every machine
        from app import battery, ui_feed
        battery.reset_cache()
        with open(SCHEMA, encoding="utf-8") as f:
            cls.schema = json.load(f)
        tmp = tempfile.mkdtemp(prefix="arbutus-situations-")
        try:
            lib = os.path.join(tmp, "library")
            library_copy(lib)
            p = Situations(lib, os.path.join(tmp, "state"))
            try:
                cls.docs = anonymise(p.play(SCRIPTS[LESSON]))
                # a device with a battery publishes other fields (#1179, t853
                # row 5): the same instrument, its gauge read, asked once more
                gauge = {"percent": 80, "charging": True}
                with mock.patch.object(battery, "read", return_value=gauge):
                    for _ in range(ui_feed.REMAP_TICKS + 1):   # the half's idle cadence
                        p.tick()
                    cls.battery_doc = p.situation()
            finally:
                p.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            battery.reset_cache()
        cls.rows = as_record(cls.docs)
        if os.environ.get("ARBUTUS_WRITE_SITUATIONS"):
            write_record(cls.rows)

    def test_the_recorded_situations_are_current(self):
        with open(RECORD, encoding="utf-8") as f:
            committed = json.load(f)
        self.assertEqual(len(committed), len(self.rows), "the script changed length")
        for want, got in zip(committed, self.rows):
            self.assertEqual(want, got, "step %r publishes differently now; rerun with "
                             "ARBUTUS_WRITE_SITUATIONS=1 and read the diff" % got["step"])

    def test_the_patches_rebuild_every_document(self):
        doc = self.rows[0]["situation"]
        for row, (_, want) in zip(self.rows[1:], self.docs[1:]):
            doc = merge(doc, row["patch"])
            self.assertEqual(doc, want, row["step"])

    def test_every_field_published_has_a_sentence_in_the_schema(self):
        for step, d in self.docs:
            self.assertEqual(schema_faults(d, self.schema, self.schema), [], step)

    def test_a_battery_present_document_has_a_sentence_for_every_field(self):
        self.assertEqual(self.battery_doc["power"], {"battery_percent": 80, "charging": True})
        self.assertEqual(schema_faults(self.battery_doc, self.schema, self.schema), [])

    def test_the_schema_describes_every_field_it_names(self):
        self.assertEqual(schema_undescribed(self.schema), [])

    def test_a_negative_control_a_field_with_no_sentence_fails(self):
        d = dict(self.docs[-1][1], mystery=1)
        self.assertIn("situation.mystery: published, but live-schema.json has no sentence "
                      "for it", schema_faults(d, self.schema, self.schema))

    def test_the_song_is_there_by_the_end(self):
        """What *Sketch a song* asks for, read off the document alone."""
        after = dict(self.docs)
        # FILE › PROJECT › NEW (the gesture is #818's; the effect is the test)
        made = [s for s, d in self.docs if d.get("project", {}).get("new")]
        self.assertTrue(made, "no step made a new project")
        # the boot's own project is open, and it is not one NEW made (#1179)
        first = self.docs[0][1].get("project", {})
        self.assertEqual(first.get("id"), "<project 1>", "no project open at boot")
        self.assertEqual(first.get("new"), False, "the boot's project published as NEW")
        self.assertTrue(after["ENC2+4"]["tempo"]["click"]["on"], "the click")
        end = self.docs[-1][1]
        self.assertEqual([end["tracks"][str(n)]["has_clip"] for n in range(1, 5)],
                         [True, True, True, False], end["tracks"])
        self.assertFalse(end["transport"]["playing"])
        self.assertEqual(end["screen"]["page"], "TRACK")
        self.assertEqual(end["instruments"]["front"], "synth")
        mixer = [d for s, d in self.docs if s == "ENC1-2"][-1]
        before = [d for s, d in self.docs if s == "MIXER"][-1]
        self.assertLess(mixer["tracks"]["1"]["level"], before["tracks"]["1"]["level"],
                        "the bass was turned down")


if __name__ == "__main__":
    unittest.main()
