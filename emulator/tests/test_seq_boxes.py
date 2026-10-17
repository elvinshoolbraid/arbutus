"""Stage H1: the SEQ slot with four sequencer boxes in the library.

H1 added no UI. What it did add is three more boxes under the `sequencer` cap,
and two places in `emulator/` quietly assumed there was only one:

* `SeqPage._load_default_engine()` took folder 0, and `arp` sorts before
  `endless` — a fresh SEQ slot would have changed engine because a box was
  added, which is not a UI decision anyone made;
* the `.seq` replay bridge drove soft-keys 0–3 as REC/REST/UNDO/CLEAR, and
  `arp` and `euclid` declare none at all.

Stage H2 deleted the bridge — a pattern is an asset now (DDR-037) — so the
second guard became the thing that replaced it: which boxes take a pattern
*file*, and that a box without soft-keys gets one anyway.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.native_support import app_for, host          # noqa: E402

STATE = os.path.join(HERE, "state", "test-seq-boxes")

H1_BOXES = ("arp", "endless", "euclid", "pattern")
ENTRY_KEYS = ("rec", "rest", "undo", "clear")


class SeqBoxCase(unittest.TestCase):
    def setUp(self):
        self.native = host(self)
        self.app, self.audio = app_for(self.native, STATE, fresh=True)
        self.addCleanup(self.audio.stop)

    def folders(self):
        return {f.folder: f for f in self.app.library.list("seq")}

    # -- the library ------------------------------------------------------
    def test_all_four_boxes_are_offered(self):
        """`sequencer` in the caps is the whole rule (native_library.rescan)."""
        got = self.folders()
        for name in H1_BOXES:
            self.assertIn(name, got, "no SEQ folder for arbutus/%s" % name)
            self.assertEqual(got[name].manifest["id"], "arbutus/%s" % name)

    def test_default_engine_is_endless_not_the_first_alphabetically(self):
        """`arp` sorts first; the default must still be `endless`."""
        names = list(self.folders())
        self.assertEqual(names[0], "arp", "the ordering this guards changed")
        seq = self.app.sequencer
        self.assertIsNotNone(seq)
        self.assertEqual(seq.library_folder.folder, "endless")

    # -- soft-keys and content are separate questions (H2) ----------------
    def test_entry_keys_are_declared_only_where_they_exist(self):
        page = self.app.pages["SEQ"]
        for name in H1_BOXES:
            engine = self.app.library.instantiate(self.folders()[name])
            keys = tuple(k.get("id") for k in (engine.desc.get("softkeys") or []))
            want = ENTRY_KEYS if name in ("endless", "pattern") else ()
            self.assertEqual(keys, want, "%s soft-keys" % name)
        self.assertIs(page, self.app.pages["SEQ"])

    def test_the_replay_bridge_is_gone(self):
        """N1's bridge played the mirror in as keystrokes; H2 hands a file."""
        engine = self.app.library.instantiate(self.folders()["endless"])
        self.assertFalse(hasattr(engine, "_replay_pattern"))
        self.assertFalse(hasattr(engine, "_has_entry_keys"))

    def test_only_the_two_pattern_boxes_take_a_pattern_file(self):
        want = {"arp": False, "endless": True, "euclid": False, "pattern": True}
        for name, takes in want.items():
            engine = self.app.library.instantiate(self.folders()[name])
            self.assertEqual(engine.takes_pattern, takes, name)

    def test_a_pattern_never_reaches_a_box_as_soft_keys(self):
        """`arp` has no soft-keys at all; nothing may be pressed on it."""
        engine = self.app.library.instantiate(self.folders()["arp"])
        engine.bind(self.app)
        engine.pattern = [[60], [64]]
        sent = []
        engine.native = _Recorder(self.native, sent)
        engine.set_active(True)
        self.assertEqual([s for s in sent if s[0] == "softkey"], [])
        engine.set_active(False)

    # -- the descriptor drives the slot -----------------------------------
    def test_each_box_builds_its_own_param_row(self):
        want = {"arp": 7, "endless": 7, "euclid": 8, "pattern": 7}
        for name, n in want.items():
            engine = self.app.library.instantiate(self.folders()[name])
            self.assertEqual(len(engine._params), n, "%s params" % name)

    def test_a_new_box_loads_into_the_slot(self):
        for name in ("arp", "euclid", "pattern"):
            engine = self.app.library.instantiate(self.folders()[name])
            engine.bind(self.app)
            engine.set_active(True)
            self.assertTrue(engine.active, "%s did not load" % name)
            st = self.native.state["instruments"][engine.inst_id.lower()]["seq"]
            self.assertEqual(st["id"], "arbutus/%s" % name)
            engine.set_active(False)


class _Recorder:
    """Passes everything through and writes down the two calls that matter."""

    def __init__(self, native, log):
        self._native = native
        self._log = log

    def softkey(self, key, down, shift=False):
        self._log.append(("softkey", key, down))

    def note(self, note, on=True, vel=1.0):
        self._log.append(("note", note, on))

    def __getattr__(self, name):
        return getattr(self._native, name)


if __name__ == "__main__":
    unittest.main()
