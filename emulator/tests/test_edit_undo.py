"""DDR-054 — SHIFT+STOP on SYNTH/DRUM undoes the last edit there.

One level, not a revert: a run of turns on one param is one step, the next
param is a new step and the old one is gone, and a second press has nothing
left. Every assertion about a value reads the host's own document, because
the page's `Param` could hold anything — what the instrument sounds like is
what the audio process was told.

The tape's undo is the control: SHIFT+STOP on SYNTH must never reach it.
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.edit_undo import EditUndo                         # noqa: E402
from app import keys as K                                  # noqa: E402
from app.keys import Key                                   # noqa: E402
from tests.native_support import app_for, host, library_copy  # noqa: E402


class TestEditUndoUnit(unittest.TestCase):
    """The holder on its own — one level, groups, rollback."""

    def setUp(self):
        self.state = {"v": 0}
        self.undo = EditUndo(lambda: dict(self.state),
                             lambda d: self.state.update(d))

    def test_one_level_and_no_repeat(self):
        self.undo.push()
        self.state["v"] = 1
        self.assertTrue(self.undo.undo())
        self.assertEqual(self.state["v"], 0)
        self.assertFalse(self.undo.undo())

    def test_a_run_on_one_group_is_one_step(self):
        for v in (1, 2, 3):
            self.undo.push(("param", "ATK"))
            self.state["v"] = v
        self.undo.undo()
        self.assertEqual(self.state["v"], 0)        # back to before the run

    def test_a_different_group_drops_the_older_entry(self):
        self.undo.push(("param", "ATK"))
        self.state["v"] = 1
        self.undo.push(("param", "DEC"))
        self.state["v"] = 2
        self.undo.undo()
        self.assertEqual(self.state["v"], 1)        # one step, not a revert
        self.assertFalse(self.undo.can_undo)

    def test_rollback_puts_the_previous_entry_back(self):
        self.undo.push(("param", "ATK"))
        self.state["v"] = 1
        token = self.undo.push(("param", "DEC"))    # an edit that never lands
        self.undo.rollback(token)
        self.undo.undo()
        self.assertEqual(self.state["v"], 0)

    def test_rollback_inside_a_run_keeps_the_run(self):
        token = self.undo.push(("param", "ATK"))
        self.state["v"] = 1
        self.assertIsNone(self.undo.push(("param", "ATK")))
        self.undo.rollback(None)
        self.undo.undo()
        self.assertEqual(self.state["v"], 0)
        self.assertIsNotNone(token)

    def test_amend_moves_the_held_snapshot_on(self):
        """#152: a gesture that is not an edit but rides in the snapshot."""
        self.state["oct"] = 0
        self.undo.push(("param", "ATK"))
        self.state["v"] = 1                         # the edit
        self.state["oct"] = 1                       # not an edit
        self.undo.amend(oct=1)
        self.undo.undo()
        self.assertEqual(self.state["v"], 0)        # the edit went back
        self.assertEqual(self.state["oct"], 1)      # the gesture stayed

    def test_amend_with_no_entry_open_records_nothing(self):
        self.undo.amend(v=9)
        self.assertFalse(self.undo.can_undo)
        self.assertFalse(self.undo.undo())

    def test_clear_forgets_the_entry(self):
        self.undo.push()
        self.state["v"] = 1
        self.undo.clear()
        self.assertFalse(self.undo.undo())
        self.assertEqual(self.state["v"], 1)


class TestUndoOnTheInstrumentPages(unittest.TestCase):
    """The gesture, against a running host."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arbutus-undo-")
        # Here, not in tearDown: a setUp that fails or skips never reaches it.
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = library_copy(os.path.join(self.tmp, "library"))
        self.native = host(self, library_root=self.lib)
        self.app, self.audio = app_for(
            self.native, os.path.join(self.tmp, "state"),
            library_root=self.lib)
        self._tap(Key.SYNTH)
        self.page = self.app.current_page

    # -- helpers ----------------------------------------------------------
    def _tap(self, key):
        self.app.on_key(key, True)
        self.app.on_key(key, False)

    def _shift_tap(self, key):
        self.app.on_key(Key.SHIFT, True)
        self._tap(key)
        self.app.on_key(Key.SHIFT, False)

    def _undo(self):
        self._shift_tap(Key.STOP)

    def _env(self, field="a", inst="SYNTH"):
        return float(self.app.native.instrument(inst)["env"][field])

    def _to_env(self):
        self._tap(Key.K2)

    # -- a run of turns is one step ---------------------------------------
    def test_a_run_of_turns_on_one_param_undoes_as_one(self):
        self._to_env()
        before = self._env()
        for _ in range(5):
            self.app.on_encoder(0, 3)
        self.assertNotAlmostEqual(self._env(), before, places=3)
        self._undo()
        self.assertAlmostEqual(self._env(), before, places=3)

    def test_the_next_param_is_a_new_step_and_the_old_one_is_gone(self):
        self._to_env()
        a0, d0 = self._env("a"), self._env("d")
        self.app.on_encoder(0, 5)                   # ATK
        a1 = self._env("a")
        self.app.on_encoder(1, 5)                   # DEC — a new step
        self._undo()
        self.assertAlmostEqual(self._env("d"), d0, places=3)
        self.assertAlmostEqual(self._env("a"), a1, places=3)  # not reverted
        self.assertNotAlmostEqual(a1, a0, places=3)

    def test_a_second_press_has_nothing_left(self):
        self._to_env()
        a0 = self._env()
        self.app.on_encoder(0, 5)
        self._undo()
        self.assertAlmostEqual(self._env(), a0, places=3)
        self._undo()                                 # not a revert
        self.assertAlmostEqual(self._env(), a0, places=3)
        self.assertIn("NOTHING TO UNDO", " ".join(t[1] for t in self.app.tickers()))

    def test_undo_keeps_the_subpage_you_are_looking_at(self):
        self._to_env()
        self.app.on_encoder(0, 5)
        self._tap(Key.K1)                            # wander to ENGINE
        self._undo()
        self.assertEqual(self.page.state.subpage, 0)

    # -- the octave, and what is not an edit ------------------------------
    def test_the_octave_is_not_an_edit(self):
        """#152: a move records no step and undo puts back the one before it.

        The octave rides in the page's snapshot, so the danger is the other
        half: undoing the edit before it must not drag the octave back.
        """
        self._to_env()
        a0 = self._env()
        self.app.on_encoder(0, 5)                    # the edit before it
        a1 = self._env()
        self._tap(Key.RIGHT)
        self.assertEqual(self.page.state.octave, 1)
        self._undo()
        self.assertAlmostEqual(self._env(), a0, places=3)
        self.assertNotAlmostEqual(a1, a0, places=3)
        self.assertEqual(self.page.state.octave, 1)  # where the player put it
        self.assertEqual(self.page.instrument.octave, 1)

    def test_an_octave_move_on_its_own_leaves_nothing_to_undo(self):
        self._tap(Key.RIGHT)
        self._tap(Key.LEFT)
        self._undo()
        self.assertEqual(self.page.state.octave, 0)
        self.assertIn("NOTHING TO UNDO",
                      " ".join(t[1] for t in self.app.tickers()))

    def test_the_bend_is_not_an_edit(self):
        """DDR-052's SHIFT+‹ › is a performance gesture: the control."""
        self._to_env()
        a0 = self._env()
        self.app.on_encoder(0, 5)
        a1 = self._env()
        self._shift_tap(Key.RIGHT)                   # bend and release
        self.assertEqual(self.page.state.octave, 0)  # not an octave move
        self._undo()
        self.assertAlmostEqual(self._env(), a0, places=3)
        self.assertNotAlmostEqual(a1, a0, places=3)

    def test_playing_a_note_is_not_an_edit(self):
        self._to_env()
        a0 = self._env()
        self.app.on_encoder(0, 5)
        a1 = self._env()
        self._tap(K.note_key(0))                     # a chromatic key
        self._undo()
        self.assertAlmostEqual(self._env(), a0, places=3)
        self.assertNotAlmostEqual(a1, a0, places=3)

    # -- browsers ----------------------------------------------------------
    def test_a_whole_browse_undoes_to_the_sound_before_it(self):
        """The browser applies as you scroll; the undo entry is the sound from
        before it opened, not each step of the scroll."""
        before = self.page.instrument.engine_folder.folder
        self._shift_tap(Key.K1)                      # SHIFT+1: engine browser
        menu = self.app.menu
        folders = [f.folder for f in
                   self.app.library.list(self.page.library_category)]
        if len(folders) < 3:
            self.skipTest("need three engine folders to scroll through")
        # scroll two folders on: two live applies inside one browse
        for _ in range(2):
            self.app.on_encoder(0, 1)
        self.assertNotEqual(self.page.instrument.engine_folder.folder, before)
        self._tap(Key.SYNTH)                         # close the browser
        self.assertIsNone(self.app.menu)
        self._undo()
        self.assertEqual(self.page.instrument.engine_folder.folder, before)
        self.assertIsNotNone(menu)

    def test_the_engine_browser_undo_puts_the_envelope_back_too(self):
        self._to_env()
        self.app.on_encoder(0, 6)
        edited = self._env()
        self._shift_tap(Key.B)                       # whole-sound browser
        self.app.on_encoder(1, 1)                    # a different patch/item
        self._tap(Key.SYNTH)
        self._undo()
        self.assertAlmostEqual(self._env(), edited, places=3)

    # -- the tape is never touched ----------------------------------------
    def test_shift_stop_on_synth_never_reaches_the_tape(self):
        tp = self.app.tape
        tp.push_undo()
        self.assertTrue(tp.can_undo)
        self._to_env()
        self.app.on_encoder(0, 5)
        self._undo()
        self.assertTrue(tp.can_undo, "the tape's undo was spent by SYNTH")

    def test_shift_stop_with_nothing_to_undo_still_spares_the_tape(self):
        tp = self.app.tape
        tp.push_undo()
        self._undo()
        self.assertTrue(tp.can_undo)

    def test_shift_stop_does_not_stop_the_transport(self):
        self.app.tape.play()
        self.assertTrue(self.app.tape.playing)
        self._to_env()
        self.app.on_encoder(0, 5)
        self._undo()
        self.assertTrue(self.app.tape.playing)

    def test_track_keeps_the_tape_undo(self):
        self._tap(Key.TRACK)
        tp = self.app.tape
        tp.push_undo()
        self._undo()
        self.assertFalse(tp.can_undo)                # TRACK is unchanged

    def test_track_undo_never_stops_the_transport(self):
        """#932 (B1): off SYNTH/DRUM the key undoes the tape's last edit and
        does nothing else; it used to fall through to STOP as well."""
        self._tap(Key.TRACK)
        tp = self.app.tape
        tp.selected = 0
        # a real take (#1137, t932 row 3: an undo of no edit proved only
        # that the entry was spent), then a slice under the playhead
        self.app.on_key(Key.REC, True)
        self.app.on_key(K.note_key(7), True, 0.9)
        self._blocks(24)
        self.app.on_key(K.note_key(7), False)
        self.app.on_key(Key.REC, False)
        self._blocks(2)
        self._tap(Key.STOP)
        self._blocks(2)
        take = self._host_clips()
        self.assertEqual(len(take), 1, "no take to edit")
        tp.seek(take[0][0])
        self._tap(Key.PLAY)
        self._blocks(4)                              # the head inside the clip
        self.assertTrue(tp.playing)
        tp.do_slice()
        self._blocks(1)
        self.assertEqual(len(self._host_clips()), 2, "the slice did not land")
        self._undo()
        self._blocks(1)
        self.assertFalse(tp.can_undo, "the tape's undo did not run")
        self.assertEqual(self._host_clips(), take, "the host kept the slice")
        self.assertTrue(tp.playing, "SHIFT+STOP stopped the tape")
        self.app.native.refresh()                    # the host's own word
        self.assertTrue(self.app.native.state.get("playing"),
                        "SHIFT+STOP stopped the host's transport")

    def _blocks(self, n):
        self.audio.pull(n)
        self.app.tick()

    def _host_clips(self, track=0):
        host = self.app.native.send({"type": "state"})["tape"]["tracks"]
        return [(c["start_frame"], c["len"], c["src_offset"])
                for c in host[track]["clips"]]

    # -- a slot recall is not undoable ------------------------------------
    def test_a_slot_recall_forgets_the_entry(self):
        """A whole snapshot arriving replaces the sound the entry was taken
        from, so the entry goes with it."""
        self._to_env()
        self.app.on_encoder(0, 5)
        self._tap(Key.B)                             # another slot, then back:
        self._tap(Key.A)                             # A has a record to recall
        self._undo()
        self.assertIn("NOTHING TO UNDO",
                      " ".join(t[1] for t in self.app.tickers()))

    # -- DRUM --------------------------------------------------------------
    def test_drum_has_its_own_entry(self):
        self._tap(Key.DRUM)
        drum = self.app.current_page
        self._to_env()
        d0 = self._env("d", "DRUM")
        self.app.on_encoder(1, 4)
        self.assertNotAlmostEqual(self._env("d", "DRUM"), d0, places=3)
        self._undo()
        self.assertAlmostEqual(self._env("d", "DRUM"), d0, places=3)
        self.assertEqual(drum.key_id, "DRUM")

    def test_the_two_pages_do_not_share_an_entry(self):
        self._to_env()
        self.app.on_encoder(0, 5)                    # an edit on SYNTH
        a1 = self._env()
        self._tap(Key.DRUM)
        self._undo()                                 # DRUM has nothing
        self._tap(Key.SYNTH)
        self._undo()                                 # SYNTH still has its own
        self.assertNotAlmostEqual(self._env(), a1, places=4)


if __name__ == "__main__":
    unittest.main()
