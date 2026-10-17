"""Patches: promote a slot to the library, recall it whole.

Covers both gestures — SHIFT + SYNTH/DRUM saves, SHIFT + slot key browses —
and the rule that a patch carries the entire keyboard (engine, envelope,
effect, LFO, octave and the sequencer riding with it).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.app import App, SAMPLE_RATE                      # noqa: E402
from app.keys import Key                                  # noqa: E402
from app import patch as P                                # noqa: E402
from app import name_menu as NM                           # noqa: E402
from app.keys import ENC_PUSH_KEYS                        # noqa: E402
from tests.native_support import app_for, host, library_copy  # noqa: E402


class TestNames(unittest.TestCase):
    """D98's word lists (#819): each kind draws from its own nouns, keeps the
    patch rule's shape and never offers a name the folder already holds."""

    LISTS = {"patch": NM.PLANTS_AND_LANDMARKS, "rack": NM.TOOLS,
             "pattern": NM.ACTIONS}

    def test_each_list_feeds_its_own_kind(self):
        for kind, nouns in self.LISTS.items():
            names = NM.free_names(kind, [])
            self.assertTrue(names, kind)
            for name in names:
                adjective, _, noun = name.partition("-")
                self.assertIn(adjective, NM.ADJECTIVES, name)
                self.assertIn(noun, nouns, "%s offered %s" % (kind, name))
                self.assertLessEqual(len(name), NM.MAX_NAME, name)
                self.assertRegex(name, r"^[A-Z]+-[A-Z]+$")

    def test_a_taken_name_is_never_offered(self):
        for kind in self.LISTS:
            every = NM.free_names(kind, [])
            keep = every[len(every) // 2]
            # case-blind: the folder's file names come back lower case
            taken = [n.lower() for n in every if n != keep]
            self.assertEqual(NM.free_names(kind, taken), [keep], kind)
            self.assertEqual(NM.first_free(kind, taken), keep, kind)
            self.assertIsNone(NM.first_free(kind, every), kind)

    def test_slug_is_a_safe_filename(self):
        self.assertEqual(P.slug("BIG-CAT"), "big-cat")
        self.assertEqual(P.slug("Wet Moth!"), "wet-moth")


class TestPatchRoundTrip(unittest.TestCase):
    """The gestures, end to end, against a throwaway copy of the library."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arbutus-patch-")
        self.lib = library_copy(os.path.join(self.tmp, "library"))
        self.native = host(self, library_root=self.lib)
        self.app, self.audio = app_for(
            self.native, os.path.join(self.tmp, "state"),
            library_root=self.lib)
        self._tap(Key.SYNTH)
        self.page = self.app.current_page

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def _tap(self, key):
        self.app.on_key(key, True)
        self.app.on_key(key, False)

    def _shift_tap(self, key):
        self.app.on_key(Key.SHIFT, True)
        self._tap(key)
        self.app.on_key(Key.SHIFT, False)

    def _dirty_sound(self):
        self.page.p_atk.set_value(0.5)
        self.page.p_rel.set_value(2.0)
        self.page.p_rate.set_value(9.0)
        self.page.state.octave = 2
        self.app.sequencer.pattern = [[60], [62], [64]]

    def _push(self, enc: int):
        self._tap(ENC_PUSH_KEYS[enc])

    def _save(self, commit=None):
        """SHIFT+SYNTH, then the commit (ENC1's push unless `commit` says
        otherwise); return (folder, the item that appeared).

        Diffing the listing rather than filtering known scaffold names: a
        stale patch left in the folder must not be mistaken for this one.
        """
        lf = self.page.instrument.engine_folder
        before = set(lf.items)
        self._shift_tap(Key.SYNTH)
        self.assertIsInstance(self.app.menu, NM.NameMenu)
        picked = self.app.menu.picked()
        (commit or (lambda: self._push(0)))()
        new_items = [i for i in lf.items if i not in before]
        self.assertEqual(len(new_items), 1, "expected exactly one new item")
        self.assertEqual(new_items[0], P.slug(picked) + P.SUFFIX)
        return lf, new_items[0]

    # -- tests ------------------------------------------------------------
    def test_shift_page_key_writes_a_named_patch(self):
        self._dirty_sound()
        lf, item = self._save()
        with open(lf.item_path(item)) as f:
            data = json.load(f)
        self.assertRegex(data["name"], r"^[A-Z]+-[A-Z]+$")
        self.assertIn(data["name"].partition("-")[2], NM.PLANTS_AND_LANDMARKS)
        self.assertEqual(P.slug(data["name"]) + P.SUFFIX, item)

    def test_shift_synth_opens_the_name_menu_and_saves_only_on_the_commit(self):
        lf = self.page.instrument.engine_folder
        before = set(lf.items)
        self._shift_tap(Key.SYNTH)
        menu = self.app.menu
        self.assertIsInstance(menu, NM.NameMenu)
        self.assertEqual(menu.breadcrumb(), ["SYNTH", "SAVE PATCH", "NAME"])
        self.assertEqual(menu.kind, "patch")
        # the three offered never repeat a name the folder holds
        self.assertFalse(set(menu.names()) & set(P.existing_names(lf)))
        self.assertEqual(set(lf.items), before, "saved before the commit")
        self.app.on_encoder(0, 1)                      # the next name
        picked = menu.picked()
        self._push(0)
        self.assertIsNone(self.app.menu)
        self.assertEqual(set(lf.items) - before, {P.slug(picked) + P.SUFFIX})

    def test_the_red_key_commits_too(self):
        self._save(commit=lambda: self._tap(Key.K1))

    def test_another_number_key_cancels_and_stays_on_the_subpage(self):
        lf = self.page.instrument.engine_folder
        before = set(lf.items)
        sub = self.page.state.subpage
        self._shift_tap(Key.SYNTH)
        self._tap(Key.K2)
        self.assertIsNone(self.app.menu)
        self.assertEqual(set(lf.items), before)
        self.assertEqual(self.page.state.subpage, sub)

    def test_a_dismissed_name_menu_never_saves_later(self):
        """SYNTH again puts the menu away; a browser opened after it and shut
        with its own number key must not commit the old name menu."""
        lf = self.page.instrument.engine_folder
        before = set(lf.items)
        self._shift_tap(Key.SYNTH)
        self._tap(Key.SYNTH)                           # dismissed
        self.assertIsNone(self.app.menu)
        self._shift_tap(Key.K1)                        # the engine browser
        self.assertIsNotNone(self.app.menu)
        self._tap(Key.K1)                              # shut by its key
        self.assertEqual(set(lf.items), before)

    def test_a_pattern_saved_with_a_patch_takes_an_action_noun(self):
        seq = self.app.sequencer
        if not getattr(seq, "takes_pattern", False) or seq.library_folder is None:
            self.skipTest("no pattern library in the scaffold")
        seq.pattern = [[60], [62], [64]]
        if not seq.active:
            seq.toggle_active()
        seq_folder = seq.library_folder
        before = set(seq_folder.items)
        _, item = self._save()
        new = [i for i in seq_folder.items if i not in before]
        self.assertEqual(len(new), 1, new)
        self.assertTrue(new[0].endswith(".seq"), new)
        noun = P.display_name(new[0]).partition("-")[2]
        self.assertIn(noun, NM.ACTIONS, new[0])
        self.assertNotEqual(P.display_name(new[0]), P.display_name(item))

    def test_the_save_is_announced_with_the_word_patch(self):
        self._save()
        long = [t[1] for t in self.app.tickers()][0]
        self.assertIn("SAVED PATCH", long)
        self.assertIn("FM", long)

    def test_a_patch_carries_the_whole_keyboard(self):
        """A patch is a format-1 §7.2 document: the engine, ENV and the rack."""
        self._dirty_sound()
        lf, item = self._save()
        with open(lf.item_path(item)) as f:
            doc = json.load(f)
        self.assertEqual((doc["format"], doc["kind"]), (1, "patch"))
        self.assertAlmostEqual(doc["env"]["a"], 0.5, places=3)   # ADSR
        self.assertEqual(doc["octave"], 2)
        self.assertIn("engine", doc)                   # the engine and its knobs
        self.assertIn("fx", doc)                       # the rack (§6.7)

    def test_shift_slot_key_opens_the_whole_sound_browser(self):
        self._shift_tap(Key.B)
        self.assertIsNotNone(self.app.menu)
        self.assertEqual(self.app.menu.breadcrumb(), ["SYNTH", "SLOT B", "SELECT"])
        self.assertEqual(self.page.state.slot_letter, "B")

    def test_bare_slot_key_still_just_recalls(self):
        self._tap(Key.B)
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.page.state.slot_letter, "B")

    def test_browsing_restores_the_whole_sound(self):
        self._dirty_sound()
        _, item = self._save()

        self._tap(Key.B)                               # a different slot
        self.page.p_atk.set_value(0.01)
        self.page.state.octave = 0
        self.app.sequencer.pattern = []

        self._shift_tap(Key.B)
        menu = self.app.menu
        # the menu shows display names; indices align with the folder's items
        items = menu.source.folder(menu.state.folder_idx).items
        self.app.on_encoder(1, items.index(item) - menu.state.item_idx)

        self.assertEqual(self.page.p_atk.value, 0.5)
        self.assertEqual(self.page.p_rel.value, 2.0)
        self.assertEqual(self.page.state.octave, 2)
        # the pattern is not in a §7.2 patch: a patch is the *sound*, and the
        # SEQ page keeps its own slots (DDR-029)
        self.assertAlmostEqual(
            self.app.native.instrument("SYNTH")["env"]["a"], 0.5, places=3)

    def test_engine_browser_takes_only_the_engine(self):
        """SHIFT+1 is the 'swap one part' path — ENV/FX/LFO must survive."""
        self._dirty_sound()
        _, item = self._save()
        self.page.p_atk.set_value(0.01)                # a different envelope

        self._shift_tap(Key.K1)                        # engine browser
        menu = self.app.menu
        items = menu.source.folder(menu.state.folder_idx).items
        self.app.on_encoder(1, items.index(item) - menu.state.item_idx)

        self.assertEqual(self.page.p_atk.value, 0.01)  # untouched by SHIFT+1

    def test_drum_patches_land_in_the_drum_library(self):
        self._tap(Key.DRUM)
        page = self.app.current_page
        lf = page.instrument.engine_folder
        if lf is None:
            self.skipTest("no drum engine in the scaffold")
        before = set(lf.items)
        self._shift_tap(Key.DRUM)
        self.assertEqual(self.app.menu.breadcrumb(), ["DRUM", "SAVE PATCH", "NAME"])
        self._push(0)
        self.assertTrue(any(i.endswith(P.SUFFIX) for i in set(lf.items) - before))
        self.assertIn(os.path.join("library", "drum"), lf.path)


if __name__ == "__main__":
    unittest.main()
