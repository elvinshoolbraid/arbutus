"""D97 (#817): the instrument is always saved, and there is no SAVE.

Elvin's call in Play date 1's grill (`device-os-spec.md` § 7.3, *Decided
2026-10-12*): NEW makes the project's folder at once, the autosave writes the
open project's document into that folder, SAVE goes, and a project nothing
was done to since NEW is deleted when it is left (NEW, LOAD, power-off). An
old working state still opens.

These read the **folder on the card**, not the UI's opinion of it: a change
reaches `project.json` in the open folder on the autosave's clock with no
gesture; a power cut after that boots the same project; an untouched NEW
leaves no folder however it is left, and a touched one keeps its; an old
unsaved session's take moves into a project of its own at boot.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import project_file as PF                            # noqa: E402
from tests.native_support import app_for, host                # noqa: E402
from tests.test_project_file import LIB, STATE, ProjectCase   # noqa: E402
from tests.test_tape_dir import media                         # noqa: E402


class AlwaysSavedCase(ProjectCase):
    def boot(self):
        """A new App on the same state, as a power cut leaves it, opened the
        way both `main`s open one (`ensure_open`)."""
        native = host(self, library_root=LIB)
        app, audio = app_for(native, STATE, fresh=False, library_root=LIB)
        self.addCleanup(audio.stop)
        PF.ensure_open(app)
        return app

    def doc(self, app=None):
        return PF.read(PF.open_folder(app or self.app))

    def projects(self, app=None):
        return sorted("%s/%s" % (d, n) for d, names in
                      PF.list_projects(app or self.app).items() for n in names)

    def set_bpm(self, bpm):
        self.app.pages["TEMPO"].p_bpm.set_value(bpm)
        self.app.tick()


class TestTheFolderIsTheProject(AlwaysSavedCase):
    def setUp(self):
        super().setUp()
        self.folder = PF.ensure_open(self.app)

    def test_a_boot_always_has_a_project_open(self):
        self.assertIsNotNone(self.folder)
        self.assertEqual(self.app.open_project, PF.project_id(self.folder))
        self.assertEqual(self.app.project.tape_folder,
                         os.path.abspath(self.folder))
        self.assertIsNotNone(self.doc())

    def test_a_change_reaches_the_folder_on_the_autosave_clock(self):
        store = self.app.doc
        self.app.tick()
        t0 = store.checked
        self.set_bpm(96.0)
        store.tick(t0 + PF.DOC_INTERVAL / 2)
        self.assertEqual(self.doc()["bpm"], 120.0, "written before its time")
        store.tick(t0 + PF.DOC_INTERVAL)
        self.assertEqual(self.doc()["bpm"], 96.0)

    def test_an_unchanged_instrument_writes_nothing_on_the_clock(self):
        """#624's rule, kept: a check with nothing changed writes neither the
        document nor the slot files it reads the slots from."""
        store = self.app.doc
        self.app.flush()
        doc = os.path.join(self.folder, PF.PROJECT_DOC)
        before = os.stat(doc).st_mtime_ns
        store.tick(store.checked + PF.DOC_INTERVAL)
        self.assertEqual(os.stat(doc).st_mtime_ns, before)
        for pid in ("SYNTH", "DRUM", "SEQ"):
            self.assertFalse(self.app.pages[pid].slot_store._dirty, pid)

    def test_the_first_tick_after_a_boot_writes_nothing_unchanged(self):
        """#955 (t817 rows 9–10): the comparison starts from the document
        the folder already holds. It started from nothing, so the first
        check after every boot rewrote an unchanged `project.json` (and
        could start compaction). No flush in the second instrument."""
        self.set_bpm(96.0)
        self.app.flush()
        app = self.boot()
        doc = os.path.join(self.folder, PF.PROJECT_DOC)
        os.utime(doc, ns=(1, 1))
        app.doc.tick(0.0)
        self.assertEqual(os.stat(doc).st_mtime_ns, 1, "rewritten unchanged")
        app.pages["TEMPO"].p_bpm.set_value(100.0)
        app.tick()
        app.doc.tick(PF.DOC_INTERVAL)
        self.assertEqual(PF.read(self.folder)["bpm"], 100.0)

    def test_a_take_reaches_the_folder_with_no_save(self):
        self.record_a_clip(track=0)
        self.app.doc.tick(self.app.doc.checked + PF.DOC_INTERVAL)
        doc = self.doc()
        self.assertEqual(len(doc["tape"]["tracks"][0]["clips"]), 1)
        self.assertEqual(doc["tape"]["tracks"][0]["medium"], "tape-1.wav")
        self.assertIn("tape-1.wav", media(self.folder))

    def test_while_rolling_the_clock_is_two_minutes(self):
        store = self.app.doc
        self.app.tick()
        t0 = store.checked
        self.app.on_key("PLAY", True)
        self.app.on_key("PLAY", False)
        self.run_blocks(2)
        self.assertTrue(self.app.tape.playing)
        self.set_bpm(96.0)
        store.tick(t0 + PF.DOC_INTERVAL)
        self.assertEqual(self.doc()["bpm"], 120.0)
        store.tick(t0 + PF.DOC_ROLLING_INTERVAL)
        self.assertEqual(self.doc()["bpm"], 96.0)

    def test_a_tick_exactly_on_time_writes_whatever_the_clock_reads(self):
        """#1069: the check compared `now - last` with the interval, and that
        difference rounds a hair under it at some clock values; the tests
        above, which tick at `last + interval`, then failed on whatever
        `time.monotonic()` happened to read (a fresh container's, mostly).
        502.0002 rounds under for both the 10 s and the 2 min."""
        store = self.app.doc
        bpm = self.app.pages["TEMPO"].p_bpm     # no `App.tick`: its real
        self.app.tick()                         # clock would write first
        t0 = store.checked = 502.0002
        bpm.set_value(96.0)
        store.tick(t0 + PF.DOC_INTERVAL / 2)
        self.assertEqual(self.doc()["bpm"], 120.0, "written before its time")
        store.tick(t0 + PF.DOC_INTERVAL)
        self.assertEqual(self.doc()["bpm"], 96.0, "stopped: the 10 s")
        self.app.on_key("PLAY", True)
        self.app.on_key("PLAY", False)
        self.run_blocks(2)
        self.assertTrue(self.app.tape.playing)
        t1 = store.checked = 502.0002
        bpm.set_value(100.0)
        store.tick(t1 + PF.DOC_INTERVAL)
        self.assertEqual(self.doc()["bpm"], 96.0, "rolling wrote at 10 s")
        store.tick(t1 + PF.DOC_ROLLING_INTERVAL)
        self.assertEqual(self.doc()["bpm"], 100.0, "rolling: the 2 min")

    def test_a_power_cut_after_it_boots_the_project_as_it_was(self):
        self.record_a_clip(track=0)
        self.set_bpm(96.0)
        self.app.doc.tick(self.app.doc.checked + PF.DOC_INTERVAL)
        opened = self.app.open_project
        clips = [(c.start, c.length, c.src) for c in self.app.tape.tracks[0]]

        app = self.boot()
        self.assertEqual(app.open_project, opened)
        self.assertEqual(app.project.tape_folder, os.path.abspath(self.folder))
        self.assertEqual([(c.start, c.length, c.src)
                          for c in app.tape.tracks[0]], clips)
        self.assertEqual(app.bpm, 96.0)
        self.assertEqual(self.projects(app), [opened], "the boot made another")

    def test_the_file_page_has_no_save(self):
        names = [s.name if s else None for s in self.page.slots()]
        self.assertNotIn("SAVE", names)


class TestAnUntouchedNewLeavesNothing(AlwaysSavedCase):
    def setUp(self):
        super().setUp()
        PF.ensure_open(self.app)
        self.record_a_clip(track=0)          # a project worth keeping
        self.kept = self.app.open_project

    def test_new_then_new(self):
        first = PF.new(self.app)
        self.assertTrue(os.path.isdir(first), "NEW made no folder")
        second = PF.new(self.app)
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.isdir(second))
        self.assertEqual(self.projects(), sorted([self.kept,
                                                  PF.project_id(second)]))

    def test_new_then_load(self):
        first = PF.new(self.app)
        self.assertTrue(PF.load(self.app, PF.project_path(
            self.app, *self.kept.split("/"))))
        self.assertFalse(os.path.exists(first))
        self.assertEqual(self.projects(), [self.kept])

    def test_new_then_power_off(self):
        first = PF.new(self.app)
        PF.power_off(self.app)
        self.app.flush()
        self.assertFalse(os.path.exists(first))
        self.assertEqual(self.projects(), [self.kept])
        # the next boot opens a project of its own, as untouched as that one
        app = self.boot()
        self.assertIsNotNone(PF.open_folder(app))
        self.assertTrue(PF.untouched(app))

    def test_the_device_powers_off_twice_and_the_next_boot_is_new(self):
        """#955 (t817 rows 7–8): the device calls `power_off` from
        `PowerPump` on the announcement and again in `main`'s `finally` on
        the SIGTERM after it. The second `leave` cleared the mark, so the
        next boot's project was not a NEW and outlived the instrument."""
        first = PF.new(self.app)
        for _call_site in ("PowerPump", "main's finally"):
            PF.power_off(self.app)
            self.app.flush()
            self.assertFalse(os.path.exists(first))
        app = self.boot()
        self.assertTrue(PF.untouched(app))
        second = PF.open_folder(app)
        PF.power_off(app)
        self.assertFalse(os.path.exists(second))
        self.assertEqual(self.projects(app), [self.kept])

    def test_a_restart_keeps_it_untouched(self):
        """A cut right after NEW, no tick and no flush (#955, t817 rows
        3–4): the mark was set after the commit that a boot reads."""
        first = PF.new(self.app)
        app = self.boot()
        self.assertEqual(app.open_project, PF.project_id(first))
        PF.new(app)
        self.assertFalse(os.path.exists(first))

    def test_the_first_tick_after_a_load_writes_nothing_unchanged(self):
        """#955: LOAD starts the comparison from the loaded folder's own
        document, as a boot does, not from the project it left."""
        self.app.flush()
        PF.new(self.app)
        kept = PF.project_path(self.app, *self.kept.split("/"))
        self.assertTrue(PF.load(self.app, kept))
        doc = os.path.join(kept, PF.PROJECT_DOC)
        os.utime(doc, ns=(1, 1))
        self.app.doc.tick(self.app.doc.checked
                              + PF.DOC_INTERVAL)
        self.assertEqual(os.stat(doc).st_mtime_ns, 1, "rewritten unchanged")

    def test_a_turned_knob_keeps_it(self):
        first = PF.new(self.app)
        self.set_bpm(96.0)
        PF.new(self.app)
        self.assertTrue(os.path.isdir(first))
        self.assertEqual(PF.read(first)["bpm"], 96.0, "left without its last write")

    def test_a_take_keeps_it(self):
        first = PF.new(self.app)
        self.record_a_clip(track=1)
        PF.new(self.app)
        self.assertIn("tape-2.wav", media(first))

    def test_a_page_change_is_not_a_change(self):
        first = PF.new(self.app)
        self.app.on_key("2", True)            # FILE › MIDI: a subpage is a place
        self.app.on_key("2", False)
        self.app.goto("SYNTH")                # and so is a page
        self.app.tick()
        PF.new(self.app)
        self.assertFalse(os.path.exists(first))


class TestARefusedLastWriteKeepsTheProject(AlwaysSavedCase):
    """#954 (t817 rows 5–6): leaving a touched project writes its last
    document; a card that refuses it (full, read-only) used to be a journal
    line, and NEW and LOAD went on into another project with the change in
    neither folder. Now the old project stays open, changed, until a write
    succeeds; power-off keeps it too and still shuts down."""

    def setUp(self):
        super().setUp()
        self.folder = PF.ensure_open(self.app)
        self.record_a_clip(track=0)
        self.app.flush()
        self.kept = self.app.open_project
        self.other = PF.new(self.app)           # a project LOAD can open
        self.set_bpm(84.0)
        PF.load(self.app, self.folder)
        self.assertEqual(self.app.open_project, self.kept)
        self.set_bpm(96.0)                      # the change the card refuses
        self.real = PF._write_text
        self.addCleanup(setattr, PF, "_write_text", self.real)

        def refuse(folder, text):
            raise OSError(28, "No space left on device")
        PF._write_text = refuse

    def assert_kept(self):
        self.assertEqual(self.app.open_project, self.kept)
        self.assertEqual(self.app.project.tape_folder,
                         os.path.abspath(self.folder))
        self.assertEqual(self.app.bpm, 96.0, "the change was walked away")
        self.assertTrue(PF.not_saved(self.app))

    def test_new_does_not_go_on(self):
        before = self.projects()
        self.assertIsNone(PF.new(self.app))
        self.assert_kept()
        self.assertEqual(self.projects(), before, "NEW made a folder anyway")

    def test_load_does_not_go_on(self):
        self.assertFalse(PF.load(self.app, self.other))
        self.assert_kept()

    def test_the_file_page_says_so(self):
        page = self.app.pages["FILE"]
        page._new("QUIET HARBOUR")
        self.assert_kept()
        self.assertEqual(self.app._flash[1], "NOT SAVED")

    def test_a_retry_that_succeeds_lets_new_go_on(self):
        PF.new(self.app)
        PF._write_text = self.real
        folder = PF.new(self.app)
        self.assertIsNotNone(folder)
        self.assertFalse(PF.not_saved(self.app))
        self.assertEqual(PF.read(self.folder)["bpm"], 96.0)

    def test_an_offload_does_not_release_it(self):
        """#821's release: False keeps the folder from the phone's DELETE."""
        self.assertFalse(self.app.release_project(self.kept))
        self.assert_kept()

    def test_power_off_keeps_it(self):
        PF.power_off(self.app)
        self.assertTrue(os.path.isdir(self.folder))
        self.assertEqual(self.app.open_project, self.kept)


class TestAnOldWorkingStateMigrates(AlwaysSavedCase):
    """An autosave from before D97: a session never saved, its take in the
    working folder (`/data/.state`), `open_project` null. This harness boots
    without `ensure_open`, so its sessions are exactly that."""

    def test_the_take_moves_into_a_project_of_its_own(self):
        self.record_a_clip(track=0)
        self.app.flush()
        sound = self.native.clip_peaks(0, self.app.tape.tracks[0][0].src,
                                       self.app.tape.tracks[0][0].length,
                                       16)["max"]
        self.assertIsNone(self.app.open_project)
        self.assertIn("tape-1.wav", media(STATE))

        app = self.boot()
        folder = PF.open_folder(app)
        self.assertIsNotNone(folder)
        self.assertEqual(app.project.tape_folder, os.path.abspath(folder))
        self.assertIn("tape-1.wav", media(folder))
        self.assertEqual(media(STATE), [])
        clip = app.tape.tracks[0][0]
        self.assertEqual(app.native.clip_peaks(0, clip.src, clip.length,
                                               16)["max"], sound)
        PF.new(app)
        self.assertTrue(os.path.isdir(folder), "the migrated take was deleted")

    def test_an_old_session_with_only_a_knob_turned_is_kept(self):
        self.set_bpm(96.0)
        self.app.flush()
        app = self.boot()
        folder = PF.open_folder(app)
        self.assertEqual(PF.read(folder)["bpm"], 96.0)
        PF.new(app)
        self.assertTrue(os.path.isdir(folder))

    def test_a_first_boot_is_a_new(self):
        """No working state at all: the boot's project is an untouched NEW."""
        folder = PF.ensure_open(self.app)
        self.assertTrue(PF.untouched(self.app))
        PF.new(self.app)
        self.assertFalse(os.path.exists(folder))


if __name__ == "__main__":
    unittest.main()
