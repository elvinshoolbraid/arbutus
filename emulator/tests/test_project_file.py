"""Stage J1: FILE › PROJECT — NEW / LOAD / RENAME / BOUNCE, acting on the turn.

The claim a project file has to earn is not "a JSON file appeared". It is
**the sound comes back** — so the round-trip tests here take a fingerprint of
the *host's* `state` (engines, parameters, envelopes, racks, kits, the tape's
clips, the per-track EQ and mute/solo, LINK and `base_bpm`), then NEW the
instrument to prove the fingerprint really moved, then LOAD and compare. The
UI is checked too, but the UI agreeing with itself proves nothing.

The rest is the page: the subpage frame (`PROJECT · MIDI · USB · SYSTEM`,
three of them J2's placeholders), D98's menus (#818: a turn right acts, the
name menu, the red key, the push commits, any other page key cancels),
DDR-020's auto-naming of a session with no folder, and BOUNCE — trimmed to
where clips exist, and carrying the master rack or not according to the
switch.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np                                            # noqa: E402

from app import project_file as PF                            # noqa: E402
from app import keys as K                                     # noqa: E402
from app.keys import ENC_PUSH_KEYS, Key                       # noqa: E402
from app.name_menu import NameMenu, PLACE_ADJECTIVES, PLACES, STRIP  # noqa: E402
from app.native_library import read_doc                       # noqa: E402
from app.tape import Clip, STOPPED                            # noqa: E402
from pages.file import RESET, SYSTEM, USB, USB_MODES          # noqa: E402
from tests.native_support import (SAMPLE_RATE, app_for, host,  # noqa: E402
                                  library_copy, load_wav, state_dir)

STATE = state_dir("project-file")
LIB = os.path.join(HERE, "state", "project-file-library")



class ProjectCase(unittest.TestCase):
    def setUp(self):
        library_copy(LIB)
        self.native = host(self, library_root=LIB)
        self.app, self.audio = app_for(self.native, STATE, fresh=True,
                                       library_root=LIB)
        self.addCleanup(self.audio.stop)
        self.app.goto("FILE")
        self.page = self.app.pages["FILE"]

    # -- gestures ----------------------------------------------------------
    def turn(self, action: str):
        """Turn the action's encoder right: D98, a turn right acts."""
        self.app.on_encoder(PF_ACTIONS.index(action), 1)

    def push(self, enc: int):
        self.app.on_key(ENC_PUSH_KEYS[enc], True)
        self.app.on_key(ENC_PUSH_KEYS[enc], False)

    def tap(self, key: str):
        self.app.on_key(key, True)
        self.app.on_key(key, False)

    def name(self) -> str:
        """The open project's name: its folder's."""
        return (self.app.open_project or "").partition("/")[2]

    def do(self, action: str):
        if action == "SAVE":
            # D97 (#817): there is no SAVE. What these tests mean by it — the
            # session in a folder of its own, its document on the card — is
            # `save`, the writer the autosave and a boot's `ensure_open` use
            # (this harness boots without one, so the session starts as an
            # old unsaved autosave would), and a flush.
            PF.save(self.app)
            self.app.flush()
            self.app.tick()
            self.saved = self.app.open_project
            return
        self.turn(action)
        if action == "NEW":
            self.push(0)                      # the first name, ENC1's push
        elif action == "LOAD":
            # the project the last SAVE left, wherever it sorts
            date, _, name = self.saved.partition("/")
            dates = list(self.page._listing)
            self.app.on_encoder(0, dates.index(date))
            self.app.on_encoder(1, self.page._listing[date].index(name))
            self.push(1)                      # ENC2, the encoder just turned
        elif action == "BOUNCE":
            self.finish_bounce()              # #920: the render is a job
        self.app.tick()

    def finish_bounce(self, most=200):
        """Render until the host's `bounce` push ends the job (#920): offline
        the job takes one chunk a `render`."""
        for _ in range(most):
            if self.app.bounce_job is None:
                return
            self.run_blocks(1)
        self.fail("the bounce job never ended")

    # -- the fingerprint ---------------------------------------------------
    def fingerprint(self) -> dict:
        """What the **host** holds. The UI agreeing with itself proves nothing."""
        st = self.native.refresh()
        inst = {}
        for name, v in (st.get("instruments") or {}).items():
            eng = v.get("engine") or {}
            inst[name] = {
                "engine": eng.get("id"),
                "params": eng.get("params"),
                "assets": eng.get("assets"),
                "env": v.get("env"),
                "fx": v.get("fx"),
                "mod": v.get("mod"),
                "kit": v.get("kit"),
                "seq": (v.get("seq") or {}).get("id"),
            }
        tape = st.get("tape") or {}
        return {
            "instruments": inst,
            "tracks": [{k: t.get(k) for k in
                        ("clips", "gain", "pan", "mute", "solo", "eq",
                         "used_frames")}
                       for t in tape.get("tracks") or []],
            "link": tape.get("link"),
            "tempo": st.get("tempo"),
            "master_fx": (st.get("mixer") or {}).get("fx"),
            "metronome": st.get("metronome"),
        }

    # -- something worth saving -------------------------------------------
    def make_a_song(self):
        """Move every one of the things a project is supposed to carry."""
        app = self.app
        synth = app.pages["SYNTH"]
        names = [f.folder for f in app.library.list("synth")]
        synth.load_patch(names.index("osc"), 0)
        synth.p_atk.set_value(0.4)
        app.instruments["SYNTH"].push_amp_env()

        drum = app.pages["DRUM"]
        dnames = [f.folder for f in app.library.list("drum")]
        drum.load_patch(dnames.index("drum"), 0)

        # a master rack, which is also what BOUNCE's switch decides about
        racks = app.library.find("fx", "racks")
        self.native.load_rack("master", read_doc(racks.item_path("wet-iron.rack")))
        self.native.set_macro("master", 0, 0.7)

        # both sides of the mixer (F2): the UI's gains and the host's strip
        app.mixer.levels[1] = 0.33
        app.mixer.pans[2] = -0.5
        self.native.mixer(track=3, mute=True)
        self.native.eq(2, low=6.0, hp=120.0)

        # tempo, LINK and the click
        app.pages["TEMPO"].p_bpm.set_value(96.0)
        app.pages["TEMPO"].p_level.set_value(0.4)
        app.pages["TEMPO"].p_count.set_value(1)

        # and a tape with audio in it
        self.record_a_clip(track=0)
        app.tick()

    def record_a_clip(self, track=0, blocks=24):
        """REC held + a note: the take starts on the grid, from that note."""
        app = self.app
        app.tape.selected = track
        app.on_key("REC", True)
        app.on_key(K.note_key(0), True, 0.9)
        self.run_blocks(blocks)
        app.on_key(K.note_key(0), False)
        app.on_key("REC", False)
        self.run_blocks(2)
        app.on_key("STOP", True)
        app.on_key("STOP", False)
        self.run_blocks(2)

    def run_blocks(self, n):
        self.audio.pull(n)
        self.app.tick()


#: the encoder each action is on; ENC3 was SAVE until D97 (#817), RENAME
#: since D98 (#818)
PF_ACTIONS = ["NEW", "LOAD", "RENAME", "BOUNCE"]


# ---------------------------------------------------------------------------
# the page frame
# ---------------------------------------------------------------------------
class TestFilePage(ProjectCase):
    def test_subpage_frame(self):
        """`PROJECT · MIDI · USB · SYSTEM` on 1-4 (screen-sketches § FILE)."""
        self.assertEqual(self.page.strip_labels(),
                         ["PROJECT", "MIDI", "USB", "SYSTEM"])
        self.assertEqual(self.page.breadcrumb(), ["USB/FILE", "PROJECT"])
        # ENC3 was SAVE (D97, #817); `›` marks a turn-to-act cell (D98)
        self.assertEqual([s.name if s else None for s in self.page.slots()],
                         ["NEW \u203a", "LOAD \u203a", "RENAME \u203a",
                          "BOUNCE \u203a", None, None, None, "MASTER FX"])

    def test_the_other_three_subpages_are_real(self):
        """MIDI / USB / SYSTEM were J1's placeholders and are J2's pages.

        The deep coverage is `test_file_system.py`; what belongs here is that
        the frame J1 built still navigates to all four and each one draws.
        """
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        want = {"MIDI": ["IN CH", "OUT CH", "CLOCK IN", "CLOCK OUT"],
                # ENC4 was `—` until #103 gave the `.fw` line its confirm
                "USB": ["MODE", "CHARGE", "PAIR", "UPDATE"],
                "SYSTEM": ["SELECT", "VALUE", None, None]}
        for n, key in enumerate(("2", "3", "4")):
            self.app.on_key(key, True)
            self.assertEqual(self.page.state.subpage, n + 1)
            name = ["MIDI", "USB", "SYSTEM"][n]
            self.assertEqual(self.page.breadcrumb()[1], name)
            self.assertEqual([s.name if s else None
                              for s in self.page.slots()][:4], want[name])
            self.app.render(fb)
        self.assertGreater(int(fb.sum()), 0)

    def test_master_fx_is_the_shift_row(self):
        self.assertFalse(self.page.master_fx)
        self.app.current_page.on_encoder(3, 1, True)
        self.assertTrue(self.page.master_fx)
        self.assertIsNone(self.app.menu)       # SHIFT+ENC4 bounces nothing

    def test_a_turn_left_does_nothing(self):
        for enc in range(4):
            self.app.on_encoder(enc, -1)
        self.assertIsNone(self.app.menu)
        self.assertIsNone(self.page._armed)
        self.assertEqual(self.page.bounce_path, "")


# ---------------------------------------------------------------------------
# D98's menus (#818): the name menu, the red key, the push
# ---------------------------------------------------------------------------
class TestNameMenu(ProjectCase):
    def setUp(self):
        super().setUp()
        self.do("SAVE")                 # a project open, as a boot leaves one
        self.before = PF.list_projects(self.app)

    def test_new_opens_the_name_menu_and_makes_nothing_yet(self):
        self.turn("NEW")
        menu = self.app.menu
        self.assertIsInstance(menu, NameMenu)
        names = menu.names()
        self.assertEqual(len(set(names)), 3)
        for n in names:
            adjective, place = n.split(" ")
            self.assertIn(adjective, PLACE_ADJECTIVES)
            self.assertIn(place, PLACES)
        self.assertEqual(menu.picked(), names[0])     # the first highlighted
        # the strip: PICK A NAME, and one red key, under the cell turned
        self.assertIn(STRIP, [t[1] for t in self.app.tickers()])
        self.assertEqual(menu.strip_labels(), ["PICK", None, None, None])
        self.assertEqual(menu.red_strip_cell(), 0)
        self.assertEqual(PF.list_projects(self.app), self.before)

    def test_new_commits_on_its_push(self):
        self.turn("NEW")
        first = self.app.menu.names()[0]
        self.push(0)
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.name(), first)
        self.assertTrue(os.path.isdir(PF.open_folder(self.app)))

    def test_new_commits_on_the_red_key(self):
        self.turn("NEW")
        self.app.on_encoder(0, 1)                     # the second name
        second = self.app.menu.names()[1]
        self.tap("1")
        self.assertEqual(self.name(), second)

    def test_turning_past_the_third_rolls_three_more_and_back(self):
        self.turn("NEW")
        menu = self.app.menu
        first = menu.names()
        self.app.on_encoder(0, 3)
        rolled = menu.names()
        self.assertEqual(menu.sel, 0)
        self.assertFalse(set(first) & set(rolled))
        self.app.on_encoder(0, -1)
        self.assertEqual((menu.names(), menu.sel), (first, 2))
        self.app.on_encoder(0, -5)                     # nothing before the first
        self.assertEqual((menu.names(), menu.sel), (first, 0))

    def test_another_number_key_cancels_and_stays_on_project(self):
        self.turn("NEW")
        self.tap("3")
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.app.open_project, self.saved)
        self.assertEqual(PF.list_projects(self.app), self.before)
        self.assertEqual(self.page.state.subpage, 0)

    def test_another_encoders_push_cancels(self):
        self.turn("NEW")
        self.push(1)
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.app.open_project, self.saved)

    def test_the_push_follows_the_encoder_just_turned(self):
        self.turn("NEW")
        self.app.on_encoder(2, 1)                      # ENC3 moved it last
        pick = self.app.menu.picked()
        self.push(2)
        self.assertEqual(self.name(), pick)

    def test_any_other_page_key_cancels(self):
        for key in ("A", "LEFT", "SLICE"):
            self.turn("NEW")
            self.tap(key)
            self.assertIsNone(self.app.menu, key)
        self.turn("NEW")
        self.tap("SYNTH")
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.app.open_project, self.saved)

    def test_a_name_on_the_card_is_never_offered(self):
        every = {"%s %s" % (a, p) for a in PLACE_ADJECTIVES for p in PLACES}
        free = {"QUIET HARBOUR", "SALT COVE", "AMBER BAY"}
        menu = NameMenu(self.app, self.page, "project", every - free,
                        lambda n: None, 0)
        self.assertEqual(set(menu.names()), free)
        menu.on_encoder(0, 3, False)                   # nothing left to roll
        self.assertEqual(set(menu.names()), free)
        # and the menu NEW opens reads the card
        self.turn("NEW")
        taken = set(n for v in PF.list_projects(self.app).values() for n in v)
        self.assertFalse(taken & set(self.app.menu.names()))

    def test_a_full_card_still_offers_three_and_a_push_commits(self):
        # #1180: 40 adjectives x 40 places; with every pair taken the menu
        # numbers them, rather than showing nothing and crashing on a push
        every = ["%s %s" % (a, p) for a in PLACE_ADJECTIVES for p in PLACES]
        for left in (2, 0):
            free = every[:left]
            taken = every[left:]
            picked = []
            menu = NameMenu(self.app, self.page, "project", taken,
                            picked.append, 0)
            names = menu.names()
            self.assertEqual(len(set(names)), 3, left)
            self.assertEqual(set(names[:left]), set(free))
            self.assertFalse(set(names) & set(taken))
            for n in names[left:]:
                self.assertRegex(n, r"^[A-Z]+ [A-Z]+ \d+$")
            menu.on_encoder(0, 2, False)
            menu.on_key(ENC_PUSH_KEYS[0], True, False)
            self.assertEqual(picked, [names[2]])

    def test_the_same_card_offers_the_same_names(self):
        self.turn("NEW")
        first = self.app.menu.names()
        self.tap("3")
        self.turn("NEW")
        self.assertEqual(self.app.menu.names(), first)

    def test_the_name_menu_draws_the_red_key(self):
        from ui import palette, zones
        self.turn("NEW")
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        x, y, w, h = zones.key_cell(0)
        self.assertEqual(tuple(fb[y + h - 2, x + w - 3]), palette.RED)
        x, y, w, h = zones.key_cell(1)
        self.assertNotEqual(tuple(fb[y + h - 2, x + w - 3]), palette.RED)

    def test_the_red_keys_label_reads_in_both_modes(self):
        # #1180 (t818 row 3): the label on the red cell is the panel colour,
        # not red-on-red; the fill test above cannot see the text at all
        from ui import palette, zones
        self.turn("NEW")
        was = palette.mode()
        self.addCleanup(palette.set_mode, was)
        for mode in palette.MODES:
            palette.set_mode(mode)
            fb = np.zeros((240, 320, 3), dtype=np.uint8)
            self.app.render(fb)
            x, y, w, h = zones.key_cell(0)
            cell = fb[y + 1:y + h, x:x + w - 1].reshape(-1, 3)
            ink = sum(1 for px in map(tuple, cell) if px == palette.PANEL)
            self.assertGreater(ink, 20, "%s: no label on the red key" % mode)
            red = sum(1 for px in map(tuple, cell) if px == palette.RED)
            self.assertGreater(red, ink, mode)       # and still a red cell


class TestRename(ProjectCase):
    def setUp(self):
        super().setUp()
        self.record_a_clip()
        self.do("SAVE")
        self.old = PF.open_folder(self.app)

    def test_rename_lists_the_current_name_first(self):
        self.turn("RENAME")
        menu = self.app.menu
        self.assertEqual(menu.names()[0], "PROJ_0001")
        self.assertEqual(menu.red_strip_cell(), 2)
        self.assertEqual(menu.breadcrumb(), ["USB/FILE", "RENAME", "NAME"])

    def test_rename_moves_the_folder_and_the_tape_plays_from_it(self):
        clip = self.app.tape.tracks[0][0]
        before = self.native.clip_peaks(0, clip.src, clip.length, 32)
        self.turn("RENAME")
        self.app.on_encoder(2, 1)
        new = self.app.menu.picked()
        self.push(2)
        self.assertEqual(self.name(), new)
        folder = PF.open_folder(self.app)
        self.assertEqual(os.path.dirname(folder), os.path.dirname(self.old))
        self.assertFalse(os.path.exists(self.old))
        self.assertTrue(os.path.exists(os.path.join(folder, "tape-1.wav")))
        self.assertEqual(self.app.project.tape_folder, os.path.abspath(folder))
        with open(os.path.join(folder, PF.PROJECT_DOC)) as f:
            self.assertEqual(json.load(f)["name"], new)
        after = self.native.clip_peaks(0, clip.src, clip.length, 32)
        self.assertEqual(after["max"], before["max"])
        # the next take records into the renamed folder
        self.record_a_clip(track=1)
        self.app.flush()
        self.assertTrue(os.path.exists(os.path.join(folder, "tape-2.wav")))
        self.assertEqual(sum(PF.list_projects(self.app).values(), []), [new])

    def test_picking_the_current_name_changes_nothing(self):
        self.turn("RENAME")
        self.push(2)
        self.assertEqual(self.name(), "PROJ_0001")
        self.assertTrue(os.path.exists(self.old))


class TestLoadMenu(ProjectCase):
    def setUp(self):
        super().setUp()
        self.record_a_clip()
        self.do("SAVE")
        self.do("NEW")

    def test_the_browser_has_one_red_key_open(self):
        self.turn("LOAD")
        self.assertEqual(self.app.menu.strip_labels(),
                         [None, "OPEN", None, None])
        self.assertEqual(self.app.menu.red_strip_cell(), 1)
        self.assertNotEqual(self.app.open_project, self.saved)

    def test_load_opens_on_the_red_key(self):
        self.turn("LOAD")
        date, _, name = self.saved.partition("/")
        self.app.on_encoder(1, self.page._listing[date].index(name))
        self.tap("2")
        self.assertEqual(self.app.open_project, self.saved)
        self.assertEqual(len(self.app.tape.tracks[0]), 1)

    def test_another_key_closes_the_list_and_loads_nothing(self):
        for key in ("1", "4", "A"):
            new = self.app.open_project
            self.turn("LOAD")
            self.tap(key)
            self.assertIsNone(self.app.menu, key)
            self.assertEqual(self.app.open_project, new, key)
        self.turn("LOAD")
        self.push(0)                          # ENC1 was not turned
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.app.open_project, new)


# ---------------------------------------------------------------------------
# save / load / new
# ---------------------------------------------------------------------------
class TestSaveLoad(ProjectCase):
    def test_save_writes_a_dated_folder_and_auto_names(self):
        """DDR-020: projects auto-name; the browser's folder is the date."""
        self.record_a_clip()
        self.do("SAVE")
        listing = PF.list_projects(self.app)
        self.assertEqual(list(listing.values()), [["PROJ_0001"]])
        date = list(listing)[0]
        folder = PF.project_path(self.app, date, "PROJ_0001")
        with open(os.path.join(folder, PF.PROJECT_DOC)) as f:
            doc = json.load(f)
        self.assertEqual((doc["format"], doc["kind"]), (1, "project"))
        # the medium is a WAV beside the document, and only for a track in use
        self.assertTrue(os.path.exists(os.path.join(folder, "tape-1.wav")))
        self.assertIsNone(doc["tape"]["tracks"][1]["medium"])

        # DDR-020 as amended by #297: SAVE is the open project, so a second
        # SAVE rewrites PROJ_0001 rather than inventing PROJ_0002
        self.do("SAVE")
        self.assertEqual(sorted(sum(PF.list_projects(self.app).values(), [])),
                         ["PROJ_0001"])

    def test_new_really_clears_and_load_really_restores(self):
        """The proof: the **host's** document, before / cleared / after."""
        self.make_a_song()
        before = self.fingerprint()
        self.do("SAVE")
        name = self.app.open_project

        self.do("NEW")
        cleared = self.fingerprint()
        self.assertNotEqual(cleared["instruments"], before["instruments"])
        self.assertEqual([t["clips"] for t in cleared["tracks"]], [[]] * 8)
        self.assertEqual(cleared["master_fx"]["tabs"], [])
        self.assertEqual(cleared["tempo"], 120.0)
        self.assertFalse(any(t["mute"] or t["solo"] for t in cleared["tracks"]))
        self.assertEqual(cleared["metronome"]["level"], 0.0)

        self.do("LOAD")
        self.assertEqual(self.app.open_project, name)
        after = self.fingerprint()
        for key in before:
            self.assertEqual(after[key], before[key], key)

    def test_the_ui_side_comes_back_too(self):
        self.make_a_song()
        levels = list(self.app.mixer.levels)
        pans = list(self.app.mixer.pans)
        self.do("SAVE")
        self.do("NEW")
        self.assertNotEqual(list(self.app.mixer.levels), levels)
        self.do("LOAD")
        self.assertEqual(list(self.app.mixer.levels), levels)
        self.assertEqual(list(self.app.mixer.pans), pans)
        self.assertEqual(self.app.bpm, 96.0)
        self.assertEqual(self.app.pages["TEMPO"].count_in_bars, 1)

    def test_base_bpm_moves_only_on_a_project_load(self):
        """E1 / stage H2: `base_bpm` is nobody's but this one's."""
        link = lambda: (self.native.state.get("tape") or {}).get("link") or {}
        self.native.tempo(bpm=120.0, base_bpm=120.0)
        self.app.pages["TEMPO"].p_bpm.set_value(150.0)
        self.app.tick()
        self.assertEqual(link()["base_bpm"], 120.0)      # the page never moves it
        self.do("SAVE")
        self.native.tempo(base_bpm=90.0)                 # somebody else's tape
        self.assertEqual(link()["base_bpm"], 90.0)
        self.do("LOAD")
        self.assertEqual(link()["base_bpm"], 120.0)
        self.assertEqual(self.app.bpm, 150.0)

    def test_the_six_slots_on_every_page_are_in_it(self):
        synth = self.app.pages["SYNTH"]
        names = [f.folder for f in self.app.library.list("synth")]
        self.app.goto("SYNTH")
        synth.load_patch(names.index("osc"), 0)
        self.app.on_key("B", True)                       # slot B
        synth.load_patch(names.index("noise"), 0)
        self.app.on_key("A", True)
        self.app.tick()
        self.app.goto("FILE")
        self.do("SAVE")
        self.do("NEW")
        self.do("LOAD")
        store = self.app.pages["SYNTH"].slot_store
        self.assertEqual(store.active, "A")
        self.assertEqual(
            store.records["B"]["native_patch"]["engine"]["id"], "arbutus/noise")
        self.assertEqual(
            self.native.slot("SYNTH", "engine")["id"], "arbutus/osc")

    def test_an_entered_pattern_is_promoted_and_comes_back(self):
        """Stage H2's warning: a scratch pattern does not survive the session."""
        app = self.app
        app.goto("SEQ")
        app.on_key(Key.SEQ.value, True)                  # arm (DDR-035)
        app.on_key("1", True)                            # REC: step entry
        for n in (0, 4, 7):
            app.on_key(K.note_key(n), True, 0.9)
            app.on_key(K.note_key(n), False)
        app.on_key("1", False)
        app.tick()
        pattern = list(app.sequencer.pattern)
        self.assertEqual(len(pattern), 3)
        # the mirror is dirty: it is no longer the `init.seq` it was loaded
        # from, and nothing in the library holds it
        self.assertIsNone(app.sequencer._pattern_file)

        app.goto("FILE")
        self.do("SAVE")
        name = self.app.open_project.split("/")[1]
        from app import patch as P
        # S13: one sequencer per instrument, so SAVE writes one `.seq` per
        # instrument that has a riff and each is named for the instrument it
        # came off — two patterns cannot share the project's own name.
        promoted = os.path.join(LIB, "seq", "endless",
                                "%s.seq" % P.slug("%s SYNTH" % name))
        self.assertTrue(os.path.exists(promoted), promoted)
        self.assertFalse(
            os.path.exists(os.path.join(LIB, "seq", "endless",
                                        "%s.seq" % P.slug("%s DRUM" % name))),
            "DRUM had no pattern; nothing should have been written for it")

        self.do("NEW")
        self.assertEqual(app.sequencer.pattern, [])
        self.do("LOAD")
        self.assertEqual(app.sequencer.pattern, pattern)
        asset = (self.native.slot("SYNTH", "seq").get("assets") or {})["pattern"]
        self.assertEqual(os.path.realpath(asset), os.path.realpath(promoted))

    def test_the_tape_audio_comes_back(self):
        self.record_a_clip()
        clip = self.app.tape.tracks[0][0]
        before = self.native.clip_peaks(0, clip.src, clip.length, 32)
        self.do("SAVE")
        self.do("NEW")
        self.assertEqual(self.app.tape.tracks[0], [])
        self.do("LOAD")
        self.assertEqual(len(self.app.tape.tracks[0]), 1)
        back = self.app.tape.tracks[0][0]
        self.assertEqual((back.start, back.length, back.src),
                         (clip.start, clip.length, clip.src))
        after = self.native.clip_peaks(0, back.src, back.length, 32)
        self.assertEqual(after["max"], before["max"])

    def test_loading_a_folder_that_is_not_a_project(self):
        self.assertFalse(PF.load(self.app, os.path.join(LIB, "synth")))


# ---------------------------------------------------------------------------
# bounce
# ---------------------------------------------------------------------------
class TestBounce(ProjectCase):
    def test_bounce_is_trimmed_to_where_clips_exist(self):
        self.record_a_clip()
        clips = self.app.tape.tracks[0]
        start = clips[0].start
        end = clips[0].end
        self.do("BOUNCE")
        path = self.page.bounce_path
        self.assertTrue(path.endswith(".wav"), path)
        self.assertTrue(os.path.exists(path))
        mono, sr = load_wav(path)
        self.assertEqual(sr, SAMPLE_RATE)
        self.assertEqual(len(mono), end - start)
        self.assertGreater(float(np.max(np.abs(mono))), 1e-4)
        # and it lands where the library looks (§7 / DDR-030)
        self.assertTrue(path.startswith(os.path.join(LIB, "synth", "sampler")))

    def test_nothing_to_bounce_is_a_flash_not_a_file(self):
        self.do("BOUNCE")
        self.assertEqual(self.page.bounce_path, "")
        self.assertIn("NOTHING", " ".join(t[1] for t in self.app.tickers()))

    def test_master_fx_switch_decides_whether_the_rack_is_printed(self):
        racks = self.app.library.find("fx", "racks")
        self.native.load_rack("master", read_doc(racks.item_path("low-pass.rack")))
        self.record_a_clip()

        self.do("BOUNCE")
        dry, _ = load_wav(self.page.bounce_path)
        self.app.current_page.on_encoder(3, 1, True)      # SHIFT+ENC4: MASTER FX
        self.assertTrue(self.page.master_fx)
        self.do("BOUNCE")
        wet, _ = load_wav(self.page.bounce_path)
        self.assertEqual(len(wet), len(dry))
        self.assertGreater(float(np.max(np.abs(dry - wet))), 1e-3)


class TestBounceJob(ProjectCase):
    """#920 (D98): BOUNCE starts the render and returns; a played key asks
    `CANCEL BOUNCE TO PLAY? · 1 YES · 2 NO`.

    Offline the host renders a quarter of a second of tape a `render`, so a
    clip of 200 blocks (25,600 frames) is three renders long and the test
    decides where in it the key lands.
    """

    BLOCKS = 200

    def start(self):
        self.record_a_clip(blocks=self.BLOCKS)
        self.run_blocks(100)                       # the take's last note rings out
        _, self.length = PF.bounce_span(self.app)
        self.assertGreater(self.length, SAMPLE_RATE // 4)   # more than a chunk
        self.turn("BOUNCE")
        job = self.app.bounce_job
        self.assertIsNotNone(job, "BOUNCE did not start a job")
        self.path = job["path"]
        self.run_blocks(1)                         # one chunk in: mid-render
        self.assertIsNotNone(self.app.bounce_job, "the job ended in one chunk")
        self.assertFalse(os.path.exists(self.path), "a file before the end")

    def strip(self) -> str:
        return " ".join(t[1] for t in self.app.tickers())

    def peak(self, blocks=8) -> float:
        out = self.audio.pull(blocks)
        self.app.tick()
        return float(np.max(np.abs(out))) if len(out) else 0.0

    def test_bounce_returns_and_the_wav_arrives_by_push(self):
        self.start()
        self.assertIn("BOUNCING", self.strip())
        self.finish_bounce()
        self.assertEqual(self.page.bounce_path, self.path)
        mono, _ = load_wav(self.path)
        self.assertEqual(len(mono), self.length)
        self.assertGreater(float(np.max(np.abs(mono))), 1e-4)
        self.assertIn("BOUNCED", self.strip())

    def test_a_note_asks_and_yes_cancels_then_the_note_sounds(self):
        self.start()
        note = K.note_key(0)
        self.app.on_key(note, True, 0.9)
        self.assertIsNotNone(self.app.prompt)
        self.assertEqual(self.app.prompt.choices, ["YES", "NO"])
        self.assertIn("CANCEL BOUNCE TO PLAY?", self.strip())
        self.assertLess(self.peak(), 1e-4, "the note sounded before the answer")
        self.assertIsNotNone(self.app.bounce_job, "the question stopped the render")
        self.app.on_key(K.note_key(4), True, 0.9)  # another played key waits
        self.app.on_key(K.note_key(4), False)
        self.assertIsNotNone(self.app.prompt, "a second note took the question away")

        self.tap(Key.K1.value)                     # 1 YES
        self.assertIsNone(self.app.prompt)
        self.assertIsNone(self.app.bounce_job)
        self.assertGreater(self.peak(), 1e-3, "YES: the note did not sound")
        self.app.on_key(note, False)
        for _ in range(4):
            self.run_blocks(1)
        self.assertFalse(os.path.exists(self.path), "YES: a file was written")
        self.assertEqual(self.page.bounce_path, "")

    def test_a_note_let_go_before_yes_still_plays(self):
        self.start()
        note = K.note_key(0)
        self.app.on_key(note, True, 0.9)
        self.app.on_key(note, False)               # the question's
        self.assertIsNotNone(self.app.prompt)
        self.tap(Key.K1.value)
        self.assertGreater(self.peak(2), 1e-4, "YES: the let-go note never sounded")
        self.run_blocks(100)
        self.assertLess(self.peak(), 1e-4, "YES: the let-go note never ended")

    def test_no_keeps_rendering_and_the_wav_arrives_whole(self):
        self.start()
        note = K.note_key(0)
        self.app.on_key(note, True, 0.9)
        self.tap(Key.K2.value)                     # 2 NO
        self.assertIsNone(self.app.prompt)
        self.assertIsNotNone(self.app.bounce_job, "NO: the render stopped")
        self.assertLess(self.peak(1), 1e-4, "NO: the note sounded")
        self.app.on_key(note, False)               # its release is the question's
        self.finish_bounce()
        self.assertEqual(self.page.bounce_path, self.path)
        mono, _ = load_wav(self.path)
        self.assertEqual(len(mono), self.length)

    def test_the_render_ending_takes_the_question_away(self):
        self.start()
        self.app.on_key(K.note_key(0), True, 0.9)
        self.assertIsNotNone(self.app.prompt)
        self.finish_bounce()
        self.assertIsNone(self.app.prompt, "the question outlived the bounce")
        self.assertEqual(self.page.bounce_path, self.path)
        self.app.on_key(K.note_key(0), False)
        self.assertLess(self.peak(), 1e-4, "the asking note sounded on its release")

    def test_play_asks_and_yes_rolls_the_tape(self):
        self.start()
        self.app.on_key("PLAY", True)
        self.assertIsNotNone(self.app.prompt)
        self.assertEqual(self.app.tape.state, STOPPED)
        self.tap(Key.K1.value)
        self.app.on_key("PLAY", False)
        self.assertNotEqual(self.app.tape.state, STOPPED, "YES: PLAY did nothing")
        self.assertIsNone(self.app.bounce_job)

    def test_a_page_key_answers_no_and_moves(self):
        self.start()
        self.app.on_key(K.note_key(0), True, 0.9)
        self.tap("TRACK")                          # not a played key
        self.assertIsNone(self.app.prompt)
        self.assertEqual(self.app.page_id, "TRACK")
        self.assertIsNotNone(self.app.bounce_job, "LATER is NO: it renders on")
        self.app.on_key(K.note_key(0), False)
        self.finish_bounce()
        self.assertTrue(os.path.exists(self.path))


# ---------------------------------------------------------------------------
# which page the player is on (#173)
# ---------------------------------------------------------------------------
class TestPageInTheSnapshot(ProjectCase):
    """`page` in the autosave document — the field `GET /status` mirrors.

    The UI has always written it (it is what the instrument reopens on) and
    since #173 it is also the app's *current page* row, read off the file by
    `arbutus-net` for the same reason as `open_project`: § 4.3's device sockets
    run the other way, so the service cannot ask. That makes a key the UI used
    to own alone into a contract with another process, and these are the tests
    that say so — a refactor that renames or drops it would otherwise take out
    a row on the phone with nothing here going red.
    """

    def test_the_snapshot_carries_the_page(self):
        doc = self.app.doc.snapshot()
        self.assertEqual(doc["page"], self.app.page_id)
        self.assertEqual(doc["page"], "FILE")

    def test_it_follows_the_player(self):
        self.app.goto("TRACK")
        self.assertEqual(self.app.doc.snapshot()["page"], "TRACK")

    def test_a_menu_does_not_move_it(self):
        """The row says which *page* the player is on. A menu drawn over TRACK
        is still TRACK — `App.page_id` is the page behind it, and that is the
        behaviour the phone inherits rather than a second kind of answer."""
        self.app.goto("TRACK")
        self.app.menu = object()
        self.addCleanup(setattr, self.app, "menu", None)
        self.assertEqual(self.app.doc.snapshot()["page"], "TRACK")

    def test_the_autosave_file_carries_it(self):
        """The file is the channel — `arbutus-net` reads bytes off disk, not a
        dict in this process."""
        self.app.goto("MIXER")
        self.app.doc.save_working()
        with open(self.app.doc.path) as f:
            self.assertEqual(json.load(f)["page"], "MIXER")


# ---------------------------------------------------------------------------
# which project is open (#95)
# ---------------------------------------------------------------------------
class TestOpenProject(ProjectCase):
    """`app.open_project` — the id of the saved project the working state came
    from, as `"<date>/<name>"`.

    It exists because `arbutus-net` cannot ask the UI anything (the device
    sockets run the other way, `device-os-spec.md` § 4.3), so the only way it
    can tell whether the phone asked to bounce the project the instrument
    actually has open is to read the field off the autosave document. The
    tests that matter here are therefore the ones about *who* sets it: every
    route into and out of a project, not only the FILE page's buttons.
    """

    def saved_id(self):
        listing = PF.list_projects(self.app)
        date = list(listing)[0]
        return "%s/%s" % (date, sorted(listing[date])[-1])

    def test_a_fresh_instrument_has_no_open_project(self):
        self.assertIsNone(self.app.open_project)

    def test_save_makes_that_project_the_open_one(self):
        self.record_a_clip()
        self.do("SAVE")
        self.assertEqual(self.app.open_project, self.saved_id())

    def test_the_id_is_date_slash_name(self):
        """The same two components `GET /projects/<id>` names."""
        self.record_a_clip()
        self.do("SAVE")
        date, name = self.app.open_project.split("/")
        self.assertEqual(name, "PROJ_0001")
        self.assertEqual(date, PF.date_folder())

    def test_a_second_save_keeps_it(self):
        """#297: SAVE means the project that is open (Elfy, (a) on #157)."""
        self.record_a_clip()
        self.do("SAVE")
        first = self.app.open_project
        self.do("SAVE")
        self.assertEqual(self.app.open_project, first)

    def test_a_copy_does_not_move_it(self):
        self.record_a_clip()
        self.do("SAVE")
        first = self.app.open_project
        self.assertTrue(PF.save(self.app, copy=True).endswith("/PROJ_0002"))
        self.assertEqual(self.app.open_project, first)

    def test_new_opens_a_new_one(self):
        """D97 (#817): NEW makes the project's folder at once and it is the
        open one; the project left behind keeps its folder."""
        self.record_a_clip()
        self.do("SAVE")
        first = self.app.open_project
        folder = PF.new(self.app)
        self.assertEqual(self.app.open_project, PF.project_id(folder))
        self.assertTrue(self.app.open_project.endswith("/PROJ_0002"))
        self.assertTrue(os.path.isdir(folder))
        self.assertTrue(os.path.isdir(PF.project_path(self.app,
                                                      *first.split("/"))))

    def test_load_sets_it(self):
        self.record_a_clip()
        self.do("SAVE")
        saved = self.app.open_project
        PF.new(self.app)
        date, name = saved.split("/")
        self.assertTrue(PF.load(self.app, PF.project_path(self.app, date, name)))
        self.assertEqual(self.app.open_project, saved)

    def test_a_folder_that_is_not_a_project_leaves_it_alone(self):
        """`load` returns False before it applies anything; the id must not
        move either, or a mistyped path would make the device disown the tape
        it has."""
        self.record_a_clip()
        self.do("SAVE")
        saved = self.app.open_project
        self.assertFalse(PF.load(self.app, os.path.join(LIB, "synth")))
        self.assertEqual(self.app.open_project, saved)

    def test_a_host_restart_does_not_move_it(self):
        """`rewalk` (§ 4.2) applies a document the UI captured from its own
        state, with no folder behind it. The instrument has not changed
        project, so neither has the id."""
        self.record_a_clip()
        self.do("SAVE")
        saved = self.app.open_project
        self.assertTrue(PF.rewalk(self.app))
        self.assertEqual(self.app.open_project, saved)

    def test_the_usb_autosave_sets_it_too(self):
        """DDR-021: entering USB STORAGE saves first, with no prompt. Since
        D97 (#817) that is a flush of the open project: the take recorded
        since the last autosave is in its document before `/data` goes."""
        self.do("SAVE")
        self.record_a_clip()
        self.page.state.subpage = USB
        self.page._set_usb_mode(float(USB_MODES.index("STORAGE")))
        self.assertEqual(self.app.open_project, self.saved_id())
        doc = PF.read(PF.open_folder(self.app))
        self.assertEqual(len(doc["tape"]["tracks"][0]["clips"]), 1)

    def test_reset_moves_it(self):
        """RESET goes through `new`, so the id cannot survive it (DDR-064)."""
        self.record_a_clip()
        self.do("SAVE")
        first = self.app.open_project
        self.page.state.subpage = SYSTEM
        self.page._run(RESET)
        self.assertNotEqual(self.app.open_project, first)
        self.assertTrue(self.app.open_project.endswith("/PROJ_0002"))

    # -- the autosave document --------------------------------------------
    def test_it_rides_in_the_autosave_snapshot(self):
        self.record_a_clip()
        self.do("SAVE")
        doc = self.app.doc.snapshot()
        self.assertEqual(doc["open_project"], self.app.open_project)

    def test_the_snapshot_says_null_when_nothing_is_open(self):
        """`null`, not a missing key and not `""`: the reader has to be able
        to tell "never saved" from "the field is not in this build"."""
        doc = self.app.doc.snapshot()
        self.assertIn("open_project", doc)
        self.assertIsNone(doc["open_project"])

    def test_the_autosave_file_carries_it(self):
        self.record_a_clip()
        self.do("SAVE")
        self.app.doc.save_working()
        with open(self.app.doc.path) as f:
            self.assertEqual(json.load(f)["open_project"],
                             self.app.open_project)

    def test_it_comes_back_off_disk(self):
        self.record_a_clip()
        self.do("SAVE")
        saved = self.app.open_project
        self.app.doc.save_working()
        self.app.open_project = "wrong/WRONG"
        self.assertTrue(self.app.doc.load_working())
        self.assertEqual(self.app.open_project, saved)

    def test_an_autosave_from_before_this_field_reads_as_nothing_open(self):
        """A `project.json` written by an older build has no `open_project`.
        That has to read as "the instrument does not know", which is what
        makes the bounce route fall back to its pre-#95 behaviour rather than
        refusing every request."""
        self.record_a_clip()
        self.do("SAVE")
        self.app.doc.save_working()
        with open(self.app.doc.path) as f:
            doc = json.load(f)
        doc.pop("open_project")
        with open(self.app.doc.path, "w") as f:
            json.dump(doc, f)
        self.assertTrue(self.app.doc.load_working())
        self.assertIsNone(self.app.open_project)

    def test_a_non_string_id_reads_as_nothing_open(self):
        self.app.doc.save_working()
        with open(self.app.doc.path) as f:
            doc = json.load(f)
        for junk in (17, "", {"date": "x"}, []):
            doc["open_project"] = junk
            with open(self.app.doc.path, "w") as f:
                json.dump(doc, f)
            self.app.open_project = "stale/STALE"
            self.assertTrue(self.app.doc.load_working())
            self.assertIsNone(self.app.open_project, junk)

    # -- the id itself -----------------------------------------------------
    def test_project_id_is_the_two_components_under_projects(self):
        self.assertEqual(PF.project_id("/data/projects/2026-09-19/PROJ_0007"),
                         "2026-09-19/PROJ_0007")

    def test_project_id_ignores_a_trailing_slash(self):
        self.assertEqual(PF.project_id("/data/projects/2026-09-19/PROJ_0007/"),
                         "2026-09-19/PROJ_0007")

if __name__ == "__main__":
    unittest.main()
