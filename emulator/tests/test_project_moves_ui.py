"""D99 (#821): the UI's half of moving the open project.

The phone renames the project the instrument has open (`library {op:
rename}` straight to the audio process): the host renames the folder with
the tape in it and pushes `tape_dir_renamed`. The UI must follow, or its next
autosave would write the document into a folder that no longer exists, and a
power cut would boot the old name. Read off the card, as #817's tests are.
"""

import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import file_jobs                                     # noqa: E402
from app import keys as K                                     # noqa: E402
from app import project_file as PF                            # noqa: E402
from tests.test_always_saved import AlwaysSavedCase           # noqa: E402
from tests.test_project_file import LIB                       # noqa: E402


class TestTheOpenProjectIsRenamed(AlwaysSavedCase):
    def setUp(self):
        super().setUp()
        self.folder = PF.ensure_open(self.app)
        self.record_a_clip(track=0)
        self.app.flush()

    def rename(self, name):
        rel = os.path.relpath(self.folder, LIB)
        reply = self.app.native.send({"type": "library", "op": "rename",
                                      "path": rel, "name": name})
        self.app.tick()                    # the push arrives on the next poll
        return reply

    def test_the_autosave_writes_into_the_renamed_folder(self):
        reply = self.rename("Night Drive")
        self.assertEqual(reply.get("type"), "ok", reply)
        new = os.path.join(os.path.dirname(self.folder), "Night Drive")
        date = os.path.basename(os.path.dirname(self.folder))
        self.assertEqual(self.app.open_project, "%s/Night Drive" % date)
        self.assertEqual(self.app.project.tape_folder, os.path.abspath(new))
        self.set_bpm(97.0)
        store = self.app.doc
        store.tick(store.checked + PF.DOC_INTERVAL)
        doc = PF.read(new)
        self.assertEqual((doc["bpm"], doc["name"]), (97.0, "Night Drive"))
        self.assertFalse(os.path.exists(self.folder), "the old folder came back")

    def test_a_power_cut_after_it_boots_the_new_name(self):
        self.rename("Night Drive")
        app = self.boot()
        date = os.path.basename(os.path.dirname(self.folder))
        self.assertEqual(app.open_project, "%s/Night Drive" % date)
        self.assertTrue(app.tape.tracks[0], "the take did not come back")

    def test_another_folders_rename_is_not_followed(self):
        """A push naming a folder this UI's tape is not in moves nothing."""
        before = (self.app.open_project, self.app.project.tape_folder)
        self.app.tape_dir_renamed({"type": "tape_dir_renamed",
                                  "from": "/nowhere/a", "to": "/nowhere/b"})
        self.assertEqual((self.app.open_project, self.app.project.tape_folder),
                         before)


JOB = {"id": "j1", "kind": "offload", "project": "2026-10-12/P"}


class TestAFileJobHoldsTheKeys(AlwaysSavedCase):
    """Item 6: while the phone's job runs the audio stops, and a played key
    says what the instrument is doing instead of sounding."""

    def setUp(self):
        super().setUp()
        PF.ensure_open(self.app)

    def strip(self):
        return [t[1] for t in self.app.tickers()]

    def test_the_tape_stops_and_the_notes_go_quiet(self):
        app = self.app
        app.on_key("PLAY", True)
        app.on_key("PLAY", False)
        app.on_key(K.note_key(0), True, 0.9)
        self.run_blocks(4)
        self.assertTrue(app.tape.playing)
        app.set_file_job(JOB)
        self.run_blocks(2)
        self.assertFalse(app.tape.playing)
        self.assertEqual(app._note_owner, {})

    def test_a_played_key_says_the_job_and_plays_nothing(self):
        app = self.app
        app.set_file_job(JOB)
        app.on_key(K.note_key(0), True, 0.9)
        self.assertIn("OFFLOADING TO PHONE...", self.strip())
        self.assertEqual(app._note_owner, {})
        app.on_key("PLAY", True)
        app.on_key("PLAY", False)
        self.run_blocks(2)
        self.assertFalse(app.tape.playing)
        page = app.page_id
        app.on_key("TRACK", True)
        self.assertEqual(app.page_id, page)

    def test_each_kind_has_its_words(self):
        for kind, words in (("export", "WORKING ON EXPORT · HOLD TIGHT"),
                            ("duplicate", "DUPLICATING PROJECT · HOLD TIGHT"),
                            ("restore", "RESTORING FROM PHONE...")):
            self.app.set_file_job(None)
            self.app.set_file_job({"id": "x", "kind": kind})
            self.app.on_encoder(0, 1)
            self.assertIn(words, self.strip(), kind)

    def test_the_end_of_the_job_gives_the_keys_back(self):
        app = self.app
        app.set_file_job(JOB)
        app.set_file_job(None)
        app.on_key(K.note_key(0), True, 0.9)
        self.assertNotEqual(app._note_owner, {})
        app.on_key(K.note_key(0), False)


class TestAJobWritesTheOpenProjectFirst(AlwaysSavedCase):
    """#956 (t821 row 3): a Duplicate copies the card, which § 7.3's clock
    leaves up to 10 s behind the UI. The job's edge writes the document,
    and only then the job's id into the working state, which is the word
    `arbutus-net` waits for (`httpd.FLUSHED_KEY`)."""

    def setUp(self):
        super().setUp()
        self.folder = PF.ensure_open(self.app)
        self.set_bpm(120.0)
        self.app.flush()

    def working_state(self) -> dict:
        with open(self.app.doc.path) as f:
            return json.load(f)

    def test_a_tempo_inside_the_autosave_window_is_on_the_card(self):
        self.set_bpm(97.0)                 # ticked, inside the 10 s window
        self.assertEqual(PF.read(self.folder)["bpm"], 120.0)
        self.assertIsNone(self.working_state()["flushed_job"])
        dup = {"id": "j3-1a2b", "kind": "duplicate",
               "project": PF.project_id(self.folder)}
        self.app.set_file_job(dup)
        self.assertEqual(PF.read(self.folder)["bpm"], 97.0)
        self.assertEqual(self.working_state()["flushed_job"], "j3-1a2b")

    def test_a_refused_write_gives_no_word(self):
        """The card refused the document: the copy would be stale, so the
        UI does not say it wrote it (and the Duplicate is refused)."""
        self.set_bpm(97.0)
        store = self.app.project
        real = PF._write_text

        def refuse(folder, text):
            raise OSError(28, "No space left on device")
        PF._write_text = refuse
        self.addCleanup(setattr, PF, "_write_text", real)
        self.app.set_file_job({"id": "j4-1a2b", "kind": "duplicate"})
        self.assertIsNone(self.working_state()["flushed_job"])
        self.assertTrue(store.refusing)


class TestAnOffloadOfTheOpenProject(AlwaysSavedCase):
    """Item 1: `arbutus-net` asks the UI to leave the project it is
    deleting; the UI leaves it as NEW does, then says it is on the phone."""

    def setUp(self):
        super().setUp()
        self.folder = PF.ensure_open(self.app)
        self.record_a_clip(track=0)
        self.app.flush()

    def test_release_leaves_it_whole_and_opens_a_new_one(self):
        app = self.app
        old = app.open_project
        self.assertTrue(app.release_project(old))
        self.assertNotEqual(app.open_project, old)
        self.assertNotEqual(app.project.tape_folder, os.path.abspath(self.folder))
        #: the folder is the phone's now: left whole, with its take
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "tape-1.wav")))
        self.assertTrue(PF.read(self.folder)["tape"]["tracks"][0]["clips"])
        #: and the boot pointer already names the new one (`arbutus-net` waits
        #: on exactly this before it deletes)
        with open(app.doc.path) as f:
            self.assertEqual(json.load(f)["open_project"], app.open_project)

    def test_another_project_is_not_released(self):
        old = self.app.open_project
        self.assertFalse(self.app.release_project("2026-01-01/OTHER"))
        self.assertEqual(self.app.open_project, old)

    def test_offloaded_is_said(self):
        self.app.project_offloaded("2026-10-12/P")
        self.assertIn(file_jobs.OFFLOADED[0],
                      [t[1] for t in self.app.tickers()])
        self.assertIsNone(self.app.menu, "not the open one: no name asked")

    def test_the_open_ones_offload_asks_the_new_ones_name(self):
        """#818: screen-sketches § FILE › PROJECT, "the name menu for a new
        project"; its push names the project the release opened."""
        app = self.app
        old = app.open_project
        app.goto("SYNTH")
        self.assertTrue(app.release_project(old))
        app.project_offloaded(old)
        self.assertEqual(app.page_id, "FILE")
        menu = app.menu
        self.assertEqual(menu.breadcrumb(), ["USB/FILE", "NEW", "NAME"])
        pick = menu.picked()
        app.on_key(K.ENC_PUSH_KEYS[0], True)
        self.assertEqual(app.open_project.partition("/")[2], pick)
        self.assertTrue(os.path.isdir(PF.open_folder(app)))


class LowStorageCase(AlwaysSavedCase):
    """A library with a trash to fill and a card whose free space is set."""

    def setUp(self):
        super().setUp()
        PF.ensure_open(self.app)
        self.free = 10 * 1000 ** 3
        self.app.storage_watch.free = lambda root: self.free
        self.trash = os.path.join(LIB, ".trash")
        os.makedirs(self.trash, exist_ok=True)
        for n in os.listdir(self.trash):
            shutil.rmtree(os.path.join(self.trash, n), ignore_errors=True)

    def trash_a_project(self, name, size=1500):
        folder = os.path.join(self.trash, name)
        os.makedirs(folder)
        with open(os.path.join(folder, "project.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(folder, "tape-1.wav"), "wb") as f:
            f.write(b"\0" * size)

    def check(self):
        self.app.storage_watch._next = 0.0
        self.app.tick()


class TestLowStorage(LowStorageCase):
    """Item 7: under 2 GB free, never during a take, once per boot or
    offload: empty the trash, or offload with the app."""

    def test_plenty_of_room_asks_nothing(self):
        self.trash_a_project("A")
        self.check()
        self.assertIsNone(self.app.prompt)

    def test_low_with_trashed_projects_asks_to_empty_it(self):
        self.trash_a_project("A")
        self.trash_a_project("B")
        self.free = 1 * 1000 ** 3
        self.check()
        prompt = self.app.prompt
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt.choices, ["EMPTY", "LATER"])
        self.assertEqual(self.app.tickers()[0][1],
                         "2 TRASHED PROJECTS · 3.0 KB · EMPTY TRASH?")
        self.app.on_key("1", True)
        self.app.on_key("1", False)
        self.assertIsNone(self.app.prompt)
        self.assertEqual(os.listdir(self.trash), [])

    def test_later_keeps_the_trash_and_does_not_ask_again(self):
        self.trash_a_project("A")
        self.free = 1 * 1000 ** 3
        self.check()
        self.app.on_key("2", True)
        self.app.on_key("2", False)
        self.assertIsNone(self.app.prompt)
        self.assertEqual(os.listdir(self.trash), ["A"])
        self.check()
        self.assertIsNone(self.app.prompt, "asked twice in one boot")

    def test_any_other_key_is_later_and_still_plays(self):
        self.trash_a_project("A")
        self.free = 1 * 1000 ** 3
        self.check()
        self.app.on_key(K.note_key(0), True, 0.9)
        self.assertIsNone(self.app.prompt)
        self.assertNotEqual(self.app._note_owner, {})
        self.app.on_key(K.note_key(0), False)
        self.assertEqual(os.listdir(self.trash), ["A"])

    def test_a_turn_is_later_and_still_turns(self):
        """D111 (#932, B3): a turn under the question answers LATER and moves
        the value, as a key does (D107: the question never costs a move)."""
        self.trash_a_project("A")
        self.free = 1 * 1000 ** 3
        self.app.goto("TEMPO")
        bpm = self.app.bpm
        self.check()
        prompt = self.app.prompt
        self.assertIsNotNone(prompt)
        # the storage question's LATER does nothing a test can see, so the
        # answer itself is recorded (#1137, t932 row 2)
        answers, real = [], prompt.on_answer
        prompt.on_answer = lambda c: (answers.append(c), real(c))
        self.app.on_encoder(0, 1)
        self.assertIsNone(self.app.prompt)
        self.assertEqual(answers, [1], "the question was not answered LATER")
        self.assertNotEqual(self.app.bpm, bpm, "the turn was lost")
        self.assertEqual(os.listdir(self.trash), ["A"])

    def test_a_power_off_outranks_the_question(self):
        """#932 (B2), D100: § 7.4's power-off line stands alone in the strip,
        a question up at the moment included."""
        self.trash_a_project("A")
        self.free = 1 * 1000 ** 3
        self.check()
        self.assertIsNotNone(self.app.prompt)
        self.app.powering_off = "critical"
        self.app.flash("POWERING OFF — WAIT", "OFF: WAIT")
        self.assertEqual(self.app.tickers(),
                         [(self.app.tickers()[0][0], "POWERING OFF — WAIT",
                           "OFF: WAIT")])

    def test_an_empty_trash_says_offload(self):
        self.free = 1 * 1000 ** 3
        self.check()
        self.assertIsNone(self.app.prompt)
        self.assertIn(file_jobs.STORAGE_LOW[0],
                      [t[1] for t in self.app.tickers()])

    def test_never_during_a_take(self):
        self.trash_a_project("A")
        self.free = 1 * 1000 ** 3
        app = self.app
        app.tape.selected = 0
        app.on_key("REC", True)
        app.on_key(K.note_key(0), True, 0.9)
        self.run_blocks(2)
        self.assertTrue(app.tape.recording)
        self.check()
        self.assertIsNone(app.prompt)
        app.on_key(K.note_key(0), False)
        app.on_key("REC", False)
        app.on_key("STOP", True)
        app.on_key("STOP", False)
        self.run_blocks(2)
        self.check()
        self.assertIsNotNone(app.prompt)

    def test_an_offload_lets_it_ask_again(self):
        self.free = 1 * 1000 ** 3
        self.check()
        self.app.project_offloaded("2026-10-12/P")
        self.trash_a_project("A")
        self.check()
        self.assertIsNotNone(self.app.prompt)

    def test_the_prompt_draws_its_answers_on_the_keys(self):
        """The frame: the question over the main area, its first line red,
        and `1 EMPTY` `2 LATER` on the key strip, in both appearances. The
        text calls are recorded (#957: a frame's shape alone stayed green
        with the question never drawn)."""
        from unittest import mock
        from ui import draw, frame, palette
        self.trash_a_project("A")
        self.free = 1 * 1000 ** 3
        self.check()
        question = self.app.prompt.lines[0]
        real = draw.text
        try:
            for mode in palette.MODES:
                palette.set_mode(mode)
                drawn = []

                def text(fb, x, y, txt, color, *a, **kw):
                    drawn.append((txt, tuple(color)))
                    return real(fb, x, y, txt, color, *a, **kw)

                with mock.patch.object(draw, "text", text):
                    fb = frame.render_frame(self.app)
                self.assertEqual(fb.shape[:2], (240, 320))
                self.assertIn((question, tuple(palette.RED)), drawn, mode)
                self.assertIn(("1 EMPTY", tuple(palette.INK)), drawn, mode)
                self.assertIn(("2 LATER", tuple(palette.INK)), drawn, mode)
        finally:
            palette.set_mode("light")


class TestARefusedAutosaveIsSaid(LowStorageCase):
    """D117 (#1006), Elvin's words: while the card refuses the autosave the
    strip says `NOT SAVED, CARD FULL` (`NOT SAVED` for any other refusal) on
    every page until a write succeeds, and a full card asks the trash
    question again, asked this boot or not."""

    def setUp(self):
        super().setUp()
        self.real = PF._write_text
        self.addCleanup(setattr, PF, "_write_text", self.real)
        self.errno = 28                          # ENOSPC

        def refuse(folder, text):
            raise OSError(self.errno, os.strerror(self.errno))
        self.refuse = refuse

    def autosave(self, bpm):
        """A change, then the autosave's clock comes round."""
        self.app.pages["TEMPO"].p_bpm.set_value(bpm)
        self.app.doc.checked = float("-inf")
        self.app.tick()

    def strip(self):
        from ui import frame
        return frame._strip_text(self.app)

    def test_the_mark_comes_and_goes_with_the_write(self):
        self.assertEqual(self.strip(), "")
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.app._flash = None                   # STORAGE LOW: an empty trash
        for page in ("FILE", "TEMPO", "SYNTH"):
            self.app.goto(page)
            self.assertIn("NOT SAVED, CARD FULL",
                          [t[1] for t in self.app.tickers()], page)
        self.assertEqual(self.strip(), "NOT SAVED, CARD FULL")
        PF._write_text = self.real
        self.autosave(97.0)
        self.assertEqual(self.strip(), "")
        self.assertEqual(PF.read(PF.open_folder(self.app))["bpm"], 97.0)

    def test_another_refusal_is_not_called_full(self):
        self.errno = 30                          # EROFS: a read-only mount
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.assertEqual(self.strip(), "NOT SAVED")
        self.assertIsNone(self.app.prompt, "a read-only card has room")

    def test_a_full_card_asks_again_after_later(self):
        self.trash_a_project("A")
        self.free = 1 * 1000 ** 3
        self.check()
        self.app.on_key("2", True)               # LATER: asked this boot
        self.app.on_key("2", False)
        self.check()
        self.assertIsNone(self.app.prompt)
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.assertIsNotNone(self.app.prompt, "the full card asked nothing")
        self.assertEqual(self.app.prompt.choices, ["EMPTY", "LATER"])
        self.app.on_key("2", True)               # LATER again
        self.app.on_key("2", False)
        self.autosave(97.0)                      # still full: once a fill
        self.assertIsNone(self.app.prompt, "asked at every refused write")

    def test_a_second_fill_asks_again(self):
        # #1180 (t1006 row 1): a good write between two fills re-arms it
        self.trash_a_project("A")
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.assertIsNotNone(self.app.prompt)
        self.app.on_key("2", True)               # LATER
        self.app.on_key("2", False)
        PF._write_text = self.real
        self.autosave(97.0)                      # room again: the fill is over
        self.assertFalse(PF.not_saved(self.app))
        PF._write_text = self.refuse
        self.autosave(98.0)                      # the card fills again
        self.assertIsNotNone(self.app.prompt, "second fill did not ask")
        self.assertEqual(self.app.prompt.choices, ["EMPTY", "LATER"])

    def test_the_short_form_beside_another_ticker(self):
        # #1180 (t1006 row 2): beside STORAGE LOW the strip has room for the
        # short form only, and it must still say NOT SAVED
        self.free = 1 * 1000 ** 3
        self.check()                             # STORAGE LOW, on the strip
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.assertIn(file_jobs.STORAGE_LOW[0],
                      [t[1] for t in self.app.tickers()])
        self.assertIn("NOT SAVED", self.strip())
        self.assertIn("STORAGE LOW", self.strip())

    def test_empty_then_a_good_write_clears_the_mark(self):
        self.trash_a_project("A")
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.assertIsNotNone(self.app.prompt)
        PF._write_text = self.real               # the room the trash held
        self.app.on_key("1", True)
        self.app.on_key("1", False)
        self.assertEqual(os.listdir(self.trash), [])
        self.app.tick()                          # EMPTY retries the write at once
        self.assertFalse(PF.not_saved(self.app))
        self.assertEqual(PF.read(PF.open_folder(self.app))["bpm"], 96.0)
        self.app._flash = None
        self.assertEqual(self.strip(), "")

    def test_an_empty_trash_says_offload_again(self):
        self.free = 1 * 1000 ** 3
        self.check()                             # STORAGE LOW, asked this boot
        self.app._flash = None
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.assertIn(file_jobs.STORAGE_LOW[0],
                      [t[1] for t in self.app.tickers()])

    def test_never_during_a_take(self):
        self.trash_a_project("A")
        app = self.app
        app.tape.selected = 0
        app.on_key("REC", True)
        app.on_key(K.note_key(0), True, 0.9)
        self.run_blocks(2)
        self.assertTrue(app.tape.recording)
        PF._write_text = self.refuse
        self.autosave(96.0)
        self.assertTrue(PF.not_saved(app))
        self.assertIsNone(app.prompt, "asked during a take")
        app.on_key(K.note_key(0), False)
        app.on_key("REC", False)
        app.on_key("STOP", True)
        app.on_key("STOP", False)
        self.run_blocks(2)
        self.assertIsNotNone(app.prompt)


if __name__ == "__main__":
    unittest.main()
