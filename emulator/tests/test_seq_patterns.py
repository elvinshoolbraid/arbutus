"""Stage H2: the SEQ row is the box's, and a pattern is a file (DDR-037).

Two halves.

**The row.** H1 gave the SEQ slot four boxes with seven or eight parameters
each and `native_sequencer.slots()` still returned `endless`'s four, looked up
by attribute — so `arp`, `euclid` and `pattern` drew a blank row and
`endless`'s own GATE / PATTERN / ROT were saved, sent and never shown. Here the
row is asserted per box, from the descriptor alone.

**The pattern.** N1's bridge replayed the mirror into the box as the
REC/REST/UNDO/CLEAR presses that would have entered it. That is gone: the
mirror is written to a file and `load_engine {assets:{"pattern": …}}` hands the
path down, which works on a box that declares no soft-keys at all and is what
makes a `.seq` in the browser the same thing as a `.seq` in a patch.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np                                        # noqa: E402

from app.keys import Key                                  # noqa: E402
from app.native_sequencer import TIE, pattern_doc         # noqa: E402
from tests.native_support import (app_for, host, library_copy,  # noqa: E402
                                  state_dir)

STATE = state_dir("seq-patterns")
LIB = os.path.join(HERE, "state", "seq-patterns-library")

#: the row each H1 box declares, in descriptor order (§15.14)
ROWS = {
    "arp": ["DIV", "MODE", "OCT", "GATE", "SWING", "HOLD", "RATCHET", None],
    "endless": ["DIV", "SWING", "HOLD", "DIR", "GATE", "PATTERN", "ROT", None],
    "euclid": ["STEPS", "PULSES", "ROT", "DIV", "GATE", "SWING", "HOLD",
               "ACCENT"],
    "pattern": ["DIV", "STEPS", "GATE", "DIR", "SWING", "OCT", "HOLD", None],
}


class SeqRig(unittest.TestCase):
    """The rig alone — a host, a throwaway library, the SEQ page — so a second
    case can share it without inheriting (and re-running) the first's tests."""

    def setUp(self):
        library_copy(LIB)
        self.native = host(self, library_root=LIB)
        self.app, self.audio = app_for(self.native, STATE, fresh=True,
                                       library_root=LIB)
        self.addCleanup(self.audio.stop)
        self.page = self.app.pages["SEQ"]

    # -- helpers ----------------------------------------------------------
    def folders(self):
        return {f.folder: f for f in self.app.library.list("seq")}

    def goto_seq(self, arm=True):
        self.app.on_key(Key.SEQ.value, True)
        self.app.on_key(Key.SEQ.value, False)
        if arm and not self.app.sequencer.active:
            self.app.on_key(Key.SEQ.value, True)
            self.app.on_key(Key.SEQ.value, False)
        return self.app.sequencer

    def slot_record(self):
        seq = self.app.sequencer
        return self.native.slot(seq.inst_id, "seq")

    def key(self, name, down=None):
        if down is None:
            self.app.on_key(name, True)
            self.app.on_key(name, False)
        else:
            self.app.on_key(name, down)

    def note(self, n, down=None):
        self.key("NOTE_%d" % n, down)

    def use(self, folder: str, item: str = ""):
        """Pick a box (and optionally an item) the way the browser does."""
        names = [f.folder for f in self.app.library.list("seq")]
        lf = self.folders()[folder]
        items = lf.items or [""]
        idx = items.index(item) if item in items else 0
        self.page.load_engine(names.index(folder), idx)
        return self.app.sequencer

    def enter(self, *notes):
        self.key(Key.K1.value, True)              # hold REC
        for n in notes:
            self.note(n)
        self.key(Key.K1.value, False)


class PatternCase(SeqRig):
    # -- the row ----------------------------------------------------------
    def test_every_box_draws_its_whole_row(self):
        for folder, want in ROWS.items():
            self.use(folder)
            got = [s.name if s else None for s in self.page.slots()]
            self.assertEqual(got, want, folder)

    def test_the_row_carries_the_boxs_own_readout(self):
        """`arbutus/pattern` prints `AUTO <n>` for STEPS 0 (§15.14)."""
        seq = self.use("pattern")
        seq.bind(self.app)
        seq.set_active(True)
        steps = seq.param("steps")
        self.assertIsNotNone(steps)
        self.assertTrue(steps.format_value().startswith("AUTO"),
                        steps.format_value())

    def test_the_encoders_reach_the_boxs_own_parameters(self):
        seq = self.goto_seq()
        self.app.on_key(Key.SHIFT.value, True)
        self.app.on_encoder(1, +2)                # SHIFT+ENC2 = PATTERN
        self.app.on_key(Key.SHIFT.value, False)
        self.assertEqual(seq.param("pattern").format_value(), "8THS")
        self.assertAlmostEqual(self.slot_record()["params"]["pattern"],
                               seq.param("pattern").get_normalized(), places=6)

    # -- a library pattern is the asset -----------------------------------
    def test_picking_a_seq_file_hands_the_file_down(self):
        seq = self.goto_seq()
        self.use("endless", "penta-7.seq")
        self.assertEqual(len(seq.pattern), 7)
        got = self.slot_record().get("assets", {}).get("pattern", "")
        self.assertTrue(got.endswith("penta-7.seq"), got)
        # the browser hands over the library's own document, not a copy
        self.assertTrue(got.startswith(LIB), got)

    def test_the_handed_down_pattern_sounds(self):
        self.goto_seq()
        self.use("endless", "penta-5.seq")
        self.app.on_key("NOTE_4", True)           # hold a key: the box runs
        audio = self.audio.pull(48000 // 2)
        self.app.on_key("NOTE_4", False)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-3)

    def test_an_empty_item_empties_the_box(self):
        """`init.seq` is the blank one and is every folder's first item."""
        seq = self.goto_seq()
        self.use("endless", "penta-7.seq")
        self.assertEqual(len(seq.pattern), 7)
        self.use("endless", "init.seq")
        self.assertEqual(seq.pattern, [])
        self.assertTrue(
            self.slot_record()["assets"]["pattern"].endswith("init.seq"))

    def test_a_box_that_takes_no_pattern_is_sent_none(self):
        seq = self.goto_seq()
        self.use("endless", "penta-7.seq")
        self.use("euclid")
        seq = self.app.sequencer
        self.assertFalse(seq.takes_pattern)
        seq.set_active(True)
        self.assertFalse(self.slot_record().get("assets"))
        self.assertEqual(self.slot_record()["id"], "arbutus/euclid")

    # -- an entered pattern rides on a scratch file ------------------------
    def test_entry_ends_with_the_mirror_as_the_slots_asset(self):
        """Inspection 2026-09-18: `apply` rebuilds the box from its assets on
        any patch, rack or MOD edit, so an entered pattern the slot's asset
        does not name is lost on the next SHIFT+3 while the row keeps drawing
        it. REC's release hands the mirror down; CLEAR hands down nothing."""
        seq = self.goto_seq()
        self.enter(0, 4, 7)
        path = self.slot_record().get("assets", {}).get("pattern", "")
        self.assertTrue(path.endswith(".json"), path)
        with open(path) as f:
            self.assertEqual(len(json.load(f)["pattern"]), 3)
        # a rack edit rebuilds the chain; the box comes back with the riff
        rack = self.app.pages["SYNTH"].rack_view
        self.app.goto("SYNTH")
        rack.set_tab_engine("arbutus/biquad")
        self.assertEqual(self.slot_record().get("assets", {}).get("pattern"), path)
        self.app.on_key("NOTE_4", True)
        audio = self.audio.pull(48000 // 2)
        self.app.on_key("NOTE_4", False)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-3)
        self.app.goto("SEQ")
        self.key(Key.K4.value)                    # CLEAR
        self.assertEqual(seq.pattern, [])
        self.assertFalse(self.slot_record().get("assets"))

    def test_re_arming_hands_the_mirror_back(self):
        seq = self.goto_seq()
        self.enter(0, 4, 7)
        self.assertEqual(len(seq.pattern), 3)
        seq.set_active(False)
        seq.set_active(True)
        path = self.slot_record().get("assets", {}).get("pattern", "")
        self.assertTrue(path.endswith(".json"), path)
        with open(path) as f:
            doc = json.load(f)
        self.assertEqual(doc["kind"], "pattern")
        self.assertEqual(len(doc["pattern"]), 3)
        self.assertEqual(doc["root"], 53)         # NOTE_0 = F3

    def test_the_mirror_beats_a_file_it_no_longer_matches(self):
        seq = self.goto_seq()
        self.use("endless", "penta-7.seq")
        self.enter(0, 4)                          # played over it
        seq.set_active(False)
        seq.set_active(True)
        path = self.slot_record()["assets"]["pattern"]
        self.assertFalse(path.endswith("penta-7.seq"), path)
        with open(path) as f:
            self.assertEqual(len(json.load(f)["pattern"]), 2)

    # -- ties (§15.4) ------------------------------------------------------
    def test_rest_with_keys_held_is_a_tie(self):
        seq = self.goto_seq()
        self.key(Key.K1.value, True)
        self.note(0)
        self.note(4, True)                        # still holding
        self.key(Key.K2.value)                    # REST -> tie
        self.assertEqual(seq.pattern[-1], TIE)
        self.note(4, False)
        self.key(Key.K2.value)                    # nothing held -> a rest
        self.assertEqual(seq.pattern[-1], [])
        self.key(Key.K1.value, False)

    def test_a_tie_survives_the_file_and_the_slot(self):
        seq = self.goto_seq()
        seq.pattern = [[60], TIE, []]
        seq.root = 60
        doc = pattern_doc(seq.pattern, seq.root)
        self.assertEqual(doc["pattern"], [[60], "tie", []])
        snap = seq.snapshot()
        seq.pattern = []
        seq.restore(snap)
        self.assertEqual(seq.pattern, [[60], TIE, []])

    def test_the_arrows_press_the_same_soft_keys(self):
        """control-inventory § arrow keys: ‹ undoes, › rests / ties."""
        seq = self.goto_seq()
        self.key(Key.K1.value, True)
        self.note(0)
        self.key(Key.RIGHT.value)
        self.assertEqual(seq.pattern[-1], [])
        self.key(Key.LEFT.value)
        self.assertEqual(len(seq.pattern), 1)
        self.key(Key.K1.value, False)

    # -- saving a pattern --------------------------------------------------
    def test_promoting_a_patch_writes_the_pattern_into_the_library(self):
        seq = self.goto_seq()
        self.enter(0, 4, 7, 12)
        folder = seq.library_folder
        before = set(folder.items)

        self.app.on_key(Key.SHIFT.value, True)
        self.app.on_key(Key.SYNTH.value, True)
        self.app.on_key(Key.SYNTH.value, False)
        self.app.on_key(Key.SHIFT.value, False)
        self.app.on_key("ENC1_PUSH", True)        # the name menu's commit (#819)
        self.app.on_key("ENC1_PUSH", False)

        folder.rescan_items()
        new = [i for i in folder.items if i not in before and i.endswith(".seq")]
        self.assertEqual(len(new), 1, folder.items)
        with open(os.path.join(folder.path, new[0])) as f:
            doc = json.load(f)
        self.assertEqual(doc["format"], 1)
        self.assertEqual(doc["kind"], "pattern")
        self.assertEqual(len(doc["pattern"]), 4)
        # ...and the slot now names that file, so the patch beside it does too
        self.assertTrue(
            self.slot_record()["assets"]["pattern"].endswith(new[0]))
        lf = self.app.instrument.engine_folder
        with open(os.path.join(lf.path, self.app.instrument.item)) as f:
            saved = json.load(f)
        self.assertTrue(saved["seq"]["assets"]["pattern"].endswith(new[0]),
                        saved.get("seq"))

    def test_the_browser_lists_pattern_files(self):
        items = self.folders()["endless"].items
        self.assertIn("init.seq", items)
        self.assertIn("penta-7.seq", items)
        # a box with nothing to load still offers one selectable row
        self.assertEqual(self.folders()["euclid"].items, ["use"])


class SeqSeamCase(SeqRig):
    """Inspection 2026-09-18: the UI sequencer and the host's SEQ slots (§15.4).

    The host keeps a SEQ slot per instrument and clocks only the active one.
    When these were written the UI had **one** sequencer object armed on one
    instrument; since S13 (#59) it has one per instrument and `app.sequencer`
    is the active one's, which is what every assertion below already meant.
    Each reads the host's slots back, because the bugs they pin were all the
    picture disagreeing with the slot — a ticker naming an instrument no box
    was on, an ACTIVE badge over a slot a patch had emptied, a project putting
    the box back on the wrong instrument.
    """

    def host_seq(self, inst: str):
        return (self.native.slot(inst, "seq") or {}).get("id")

    def tickers(self):
        return " ".join(t[1] for t in self.app.tickers())

    def test_the_picture_follows_the_active_instrument(self):
        seq = self.goto_seq()
        self.enter(0, 4, 7)
        self.assertEqual(seq.inst_id, "SYNTH")
        self.app.goto("DRUM")                      # DRUM has no sequencer
        self.note(0, True)
        self.audio.pull(2)
        self.app.tick()
        self.assertIsNone(self.host_seq("DRUM"))
        self.assertFalse(seq.running, "a parked box claimed to be running")
        self.assertNotIn("SEQUENCER", self.tickers())
        self.note(0, False)
        self.app.goto("SYNTH")
        self.note(0, True)
        self.assertTrue(seq.running)
        self.assertIn("SEQUENCER → SYNTH", self.tickers())
        self.note(0, False)

    def test_a_whole_sound_load_with_no_sequencer_disarms_it(self):
        """control-inventory § Patches: a patch carries the sequencer — so a
        patch without one is a sound without one, and the badge says so."""
        seq = self.goto_seq()
        self.enter(0, 4)
        self.assertEqual(self.host_seq("SYNTH"), "arbutus/endless")
        names = [f.folder for f in self.app.library.list("synth")]
        self.app.pages["SYNTH"].load_patch(names.index("osc"), 0)   # osc/init
        self.assertIsNone(self.host_seq("SYNTH"))
        self.assertFalse(seq.active)
        self.assertFalse(seq.running)
        self.assertNotIn("SEQUENCER", self.tickers())

    def test_a_whole_sound_load_with_a_sequencer_arms_it(self):
        seq = self.goto_seq()
        self.use("endless", "penta-7.seq")
        # promote: the patch written beside it names the box and its pattern
        self.app.on_key(Key.SHIFT.value, True)
        self.app.on_key(Key.SYNTH.value, True)
        self.app.on_key(Key.SYNTH.value, False)
        self.app.on_key(Key.SHIFT.value, False)
        self.app.on_key("ENC1_PUSH", True)        # the name menu's commit (#819)
        self.app.on_key("ENC1_PUSH", False)
        lf = self.app.instrument.engine_folder
        item = self.app.instrument.item
        names = [f.folder for f in self.app.library.list("synth")]
        page = self.app.pages["SYNTH"]
        page.load_patch(names.index("osc"), 0)          # a sound with none
        self.assertFalse(self.app.sequencer.active)
        page.load_patch(names.index(lf.folder), lf.items.index(item))
        seq = self.app.sequencer
        self.assertTrue(seq.active)
        self.assertEqual(seq.inst_id, "SYNTH")
        self.assertEqual(self.host_seq("SYNTH"), "arbutus/endless")
        self.assertEqual(len(seq.pattern), 7)
        self.assertEqual(
            os.path.realpath(seq._pattern_file),
            os.path.realpath(self.slot_record()["assets"]["pattern"]))

    def test_slot_recall_carries_the_sequencer_with_the_sound(self):
        seq = self.goto_seq()
        self.enter(0, 4)                                 # armed, SYNTH slot A
        self.app.goto("SYNTH")
        self.app.on_key("B", True)                       # B = a copy, armed
        self.app.on_key("B", False)
        self.app.goto("SEQ")
        self.key(Key.SEQ.value)                          # disarm on B
        self.assertIsNone(self.host_seq("SYNTH"))
        self.app.goto("SYNTH")
        self.app.tick()
        self.app.on_key("A", True)                       # A still has it
        self.app.on_key("A", False)
        self.assertEqual(self.host_seq("SYNTH"), "arbutus/endless")
        self.assertTrue(self.app.sequencer.active)
        self.assertEqual(len(self.app.sequencer.pattern), 2)
        self.app.on_key("B", True)                       # and B has not
        self.app.on_key("B", False)
        self.assertIsNone(self.host_seq("SYNTH"))
        self.assertFalse(self.app.sequencer.active)

    def test_a_project_puts_the_box_back_on_its_own_instrument(self):
        from app import project_file as PF

        self.app.goto("DRUM")                            # DRUM is active…
        seq = self.goto_seq()                            # …so it is DRUM's
        self.enter(0, 7)
        self.assertEqual(seq.inst_id, "DRUM")
        self.app.goto("SYNTH")                           # saved with SYNTH active
        self.app.tick()
        folder = PF.save(self.app)
        PF.new(self.app)
        self.assertIsNone(self.host_seq("DRUM"))
        self.assertFalse(self.app.sequencers["DRUM"].active)
        PF.load(self.app, folder)
        # S13: the box goes back on DRUM, and the page — which is showing
        # SYNTH, the instrument the project was saved on — shows SYNTH's own
        # sequencer, which is the empty one. Before, there was one object and
        # `app.sequencer` *was* DRUM's however far away the player was.
        seq = self.app.sequencers["DRUM"]
        self.assertTrue(seq.active)
        self.assertEqual(seq.inst_id, "DRUM")
        self.assertEqual(self.host_seq("DRUM"), "arbutus/endless")
        self.assertIsNone(self.host_seq("SYNTH"))
        self.assertEqual(len(seq.pattern), 2)
        self.assertEqual(self.app.active_instrument_id, "SYNTH")
        self.assertFalse(self.app.sequencer.active)


class S13Case(SeqRig):
    """S13 (#59): one sequencer **state** per instrument (DDR-034 as amended).

    The host has kept a SEQ slot per instrument since §15.4; the UI kept one
    object with an `inst_id` on it, so arming on DRUM moved SYNTH's sequencer
    across and the riff left on SYNTH was gone — there was nowhere for it to
    be. Every assertion reads the host's slot back as well as the mirror,
    because "two states" is only true if the host holds two.
    """

    def host_seq(self, inst: str):
        return (self.native.slot(inst, "seq") or {}).get("id")

    def tickers(self):
        return " ".join(t[1] for t in self.app.tickers())

    def arm_on(self, inst: str, folder: str, *notes):
        """Arm `folder` on `inst`'s sequencer and enter `notes` into it."""
        self.app.goto(inst)
        self.app.goto("SEQ")
        self.use(folder)
        if not self.app.sequencer.active:
            self.key(Key.SEQ.value)
        self.enter(*notes)
        return self.app.sequencer

    # -- the ticket's first sentence --------------------------------------
    def test_each_instrument_keeps_its_own_sequencer(self):
        synth = self.arm_on("SYNTH", "endless", 0, 4, 7)
        self.assertEqual(synth.inst_id, "SYNTH")

        drum = self.arm_on("DRUM", "pattern", 2, 5)
        self.assertIsNot(drum, synth, "one object is the bug S13 fixes")
        self.assertEqual(drum.inst_id, "DRUM")

        # neither instrument's box, pattern or row moved onto the other
        self.assertEqual(self.host_seq("SYNTH"), "arbutus/endless")
        self.assertEqual(self.host_seq("DRUM"), "arbutus/pattern")
        self.assertEqual(len(synth.pattern), 3)
        self.assertEqual(len(drum.pattern), 2)

        # ...and coming back to SYNTH finds its sequence as it was left
        self.app.goto("SYNTH")
        self.assertIs(self.app.sequencer, synth)
        self.assertEqual(len(self.app.sequencer.pattern), 3)
        self.assertEqual(self.app.sequencer.box_id, "arbutus/endless")

    def test_only_the_active_instruments_sequencer_runs(self):
        """A parked sequencer does not play while the other one is active
        (§15.4: the host clocks the active slot alone)."""
        self.arm_on("SYNTH", "endless", 0, 4)
        self.arm_on("DRUM", "endless", 2, 5)
        self.app.goto("SYNTH")
        self.note(0, True)
        self.app.tick()
        self.assertTrue(self.app.sequencers["SYNTH"].running)
        self.assertFalse(self.app.sequencers["DRUM"].running)
        self.assertIn("SEQUENCER \u2192 SYNTH", self.tickers())
        self.note(0, False)

        self.app.goto("DRUM")
        self.note(0, True)
        self.app.tick()
        self.assertTrue(self.app.sequencers["DRUM"].running)
        self.assertFalse(self.app.sequencers["SYNTH"].running)
        self.assertIn("SEQUENCER \u2192 DRUM", self.tickers())
        self.note(0, False)

    # -- the ticket's second sentence: the instrument's slots carry it -----
    def test_a_slot_recall_restores_that_instruments_sequencer(self):
        for inst in ("SYNTH", "DRUM"):
            with self.subTest(inst=inst):
                self.arm_on(inst, "endless", 0, 4)
                self.app.goto(inst)
                self.app.tick()
                page = self.app.pages[inst]
                self.app.on_key("B", True)             # slot B: a copy, armed
                self.app.on_key("B", False)
                self.app.goto("SEQ")
                self.key(Key.SEQ.value)                # disarm, on B only
                self.assertIsNone(self.host_seq(inst))
                self.app.goto(inst)
                self.app.tick()
                self.app.on_key("A", True)             # A still has the box
                self.app.on_key("A", False)
                self.assertEqual(self.host_seq(inst), "arbutus/endless")
                self.assertTrue(self.app.sequencers[inst].active)
                self.assertEqual(len(self.app.sequencers[inst].pattern), 2)
                self.assertEqual(page.slot_store.active, "A")

    def test_a_recall_on_one_instrument_leaves_the_other_alone(self):
        self.arm_on("SYNTH", "endless", 0, 4, 7)
        self.arm_on("DRUM", "endless", 2, 5)
        self.app.goto("SYNTH")
        self.app.tick()
        self.app.on_key("B", True)                     # an empty SYNTH slot
        self.app.on_key("B", False)
        self.assertEqual(self.host_seq("DRUM"), "arbutus/endless")
        self.assertTrue(self.app.sequencers["DRUM"].active)
        self.assertEqual(len(self.app.sequencers["DRUM"].pattern), 2)

    # -- the ticket's acceptance: a project round-trips both ---------------
    def test_a_project_round_trips_both_sequencers(self):
        from app import project_file as PF

        self.arm_on("SYNTH", "endless", 0, 4, 7)
        self.arm_on("DRUM", "endless", 2, 5)
        self.app.goto("SYNTH")
        self.app.tick()
        folder = PF.save(self.app)

        PF.new(self.app)
        self.assertIsNone(self.host_seq("SYNTH"))
        self.assertIsNone(self.host_seq("DRUM"))

        PF.load(self.app, folder)
        self.assertEqual(self.host_seq("SYNTH"), "arbutus/endless")
        self.assertEqual(self.host_seq("DRUM"), "arbutus/endless")
        self.assertEqual(len(self.app.sequencers["SYNTH"].pattern), 3)
        self.assertEqual(len(self.app.sequencers["DRUM"].pattern), 2)

    # -- #874: a boot puts each pattern back on its own instrument ---------
    def test_a_boot_left_on_drum_keeps_each_instruments_sequence(self):
        """t624 row 1: the boot restored the SEQ page's slot record before the
        project said DRUM was active, so DRUM's bank landed on SYNTH."""
        self.arm_on("SYNTH", "endless", 0, 4, 7)
        self.arm_on("DRUM", "pattern", 2, 5)
        self.app.goto("DRUM")                      # left on DRUM
        self.app.tick()
        self.app.flush()
        native = host(self, library_root=LIB)      # a new audio process...
        app, audio = app_for(native, STATE, fresh=False, library_root=LIB)
        self.addCleanup(audio.stop)                # ...and a new App: a boot
        self.assertEqual(app.active_instrument_id, "DRUM")
        for inst, box, steps in (("SYNTH", "arbutus/endless", 3),
                                 ("DRUM", "arbutus/pattern", 2)):
            with self.subTest(inst=inst):
                seq = app.sequencers[inst]
                self.assertEqual(seq.box_id, box)
                self.assertEqual(len(seq.pattern), steps)
                self.assertEqual((native.slot(inst, "seq") or {}).get("id"),
                                 box, "the host's slot has the other's box")

    def test_both_patterns_are_promoted_under_their_own_names(self):
        """DDR-037: an entered pattern rides on a scratch file, so SAVE has to
        write **both** into the library — and they may not share a name."""
        from app import patch as P
        from app import project_file as PF

        self.arm_on("SYNTH", "endless", 0, 4, 7)
        self.arm_on("DRUM", "endless", 2, 5)
        self.app.goto("SYNTH")
        self.app.tick()
        folder = PF.save(self.app)
        name = os.path.basename(folder)

        seq_dir = os.path.join(LIB, "seq", "endless")
        for inst, steps in (("SYNTH", 3), ("DRUM", 2)):
            path = os.path.join(seq_dir,
                                "%s.seq" % P.slug("%s %s" % (name, inst)))
            self.assertTrue(os.path.exists(path), path)
            with open(path) as f:
                self.assertEqual(len(json.load(f)["pattern"]), steps, inst)


if __name__ == "__main__":
    unittest.main()
