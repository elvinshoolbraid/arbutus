"""C1 step 3b, the UI half (#297): the tape lives in the open project.

Elvin's answer on #157 was (a): SAVE moves the tape files into the project
folder and recording carries on there, and SAVE means "save the project I have
open". So these tests read the **files**, not the UI's opinion of them:

* a take lands in the working folder as it happens, and STOP copies nothing;
* the first SAVE moves it into the new project's folder, the next take records
  there, and a second SAVE writes no new folder and copies no audio;
* save-as-copy makes a second folder of media that open in place, and leaves
  the instrument on the project it had open;
* LOAD opens a folder in place — and a project saved before this change (its
  WAVs in `tape_save`'s 44-byte-header format) is converted on first open and
  plays the same audio;
* the old working autosave (`tape-0.wav`, 0-based) is converted at boot;
* NEW empties the working folder;
* and the three ways #410 found to lose a take: a compaction after SAVE
  rewrites the project's medium, so it must rewrite the project's document
  too; the first boot over a pre-#297 autosave with a project open keeps the
  working folder's old media as the live tape; and a medium longer than the
  ruler keeps its full copy aside instead of losing the tail;
* and #411's two: a crash right after LOAD, NEW or the first SAVE boots the
  folder the tape went to, with its own clips (both working files are written
  at once); a refused `tape_dir` changes nothing, and SAVE never copies over
  a folder's live media.

Every "copies nothing" is counted on `tape_save`, and each has its control: the
same counter goes up for save-as-copy, which is the one path that must flush.
"""

import os
import shutil
import sys
import unittest
import wave

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import project_file as PF                            # noqa: E402
from app.persist import is_old_medium, old_medium_frames      # noqa: E402
from tests.native_support import app_for, host                # noqa: E402
from tests.test_project_file import LIB, STATE, ProjectCase   # noqa: E402


def media(folder):
    """The audio process's files in `folder`, sorted."""
    return sorted(n for n in os.listdir(folder)
                  if n.startswith("tape-") and n != "tape.json")


class TapeDirCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.copies = []
        real = self.native.tape_save
        self.native.tape_save = (lambda t, p: self.copies.append((t, p))
                                 or real(t, p))

    def peaks(self, track=0):
        clip = self.app.tape.tracks[track][0]
        src = clip.src_track if clip.src_track is not None else track
        return self.native.clip_peaks(src, clip.src, clip.length, 16)["max"]

    def used(self, track=0):
        return int(self.native.refresh()["tape"]["tracks"][track]["used_frames"])


class TestTheTapeLivesInTheOpenProject(TapeDirCase):
    def test_a_take_is_on_disk_without_a_save(self):
        self.record_a_clip()
        self.assertIn("tape-1.wav", media(STATE))
        self.assertFalse(is_old_medium(os.path.join(STATE, "tape-1.wav")))
        self.assertEqual(self.copies, [], "STOP copied the take")

    def test_save_moves_the_tape_and_the_next_take_records_there(self):
        self.record_a_clip(track=0)
        first = self.used(0)
        self.do("SAVE")
        folder = PF.open_folder(self.app)
        self.assertIsNotNone(folder)
        self.assertEqual(self.app.project.tape_folder, os.path.abspath(folder))
        self.assertIn("tape-1.wav", media(folder))
        self.assertEqual(media(STATE), [], "the move left a medium behind")
        self.assertEqual(self.copies, [], "SAVE copied audio")

        # the second take goes straight into the project's folder
        self.record_a_clip(track=1)
        self.assertIn("tape-2.wav", media(folder))
        self.assertEqual(self.used(0), first)
        self.assertEqual(media(STATE), [])

        # and a second SAVE is the same project, still with nothing copied
        self.do("SAVE")
        self.assertEqual(sum(PF.list_projects(self.app).values(), []),
                         [os.path.basename(folder)])
        self.assertEqual(self.copies, [])
        doc = PF.read(folder)
        self.assertEqual([t["medium"] for t in doc["tape"]["tracks"][:3]],
                         ["tape-1.wav", "tape-2.wav", None])
        self.assertEqual(doc["tape"]["tracks"][1]["medium_frames"], self.used(1))

    def test_save_as_copy_copies_and_stays(self):
        self.record_a_clip()
        self.do("SAVE")
        opened = self.app.open_project
        here = self.app.project.tape_folder
        sound = self.peaks()

        # SHIFT+ENC3's SAVE COPY went with SAVE (D97, #817); the writer
        # stays for the phone's Duplicate
        copy = PF.save(self.app, copy=True)
        self.assertEqual(copy, PF.project_path(self.app, PF.date_folder(),
                                               "PROJ_0002"))
        self.assertIn("tape-1.wav", media(copy))
        self.assertFalse(is_old_medium(os.path.join(copy, "tape-1.wav")))
        self.assertEqual(self.app.open_project, opened)
        self.assertEqual(self.app.project.tape_folder, here)
        self.assertTrue(self.copies, "the copy did not flush the medium first")

        # the copy opens in place and plays the same audio
        PF.new(self.app)
        self.assertTrue(PF.load(self.app, copy))
        self.assertEqual(self.peaks(), sound)

    def test_new_empties_the_working_folder(self):
        self.record_a_clip()
        self.assertTrue(media(STATE))
        self.do("NEW")
        self.assertEqual(media(STATE), [])
        self.assertEqual(self.used(0), 0)


class TestOldProjects(TapeDirCase):
    def test_an_old_project_is_converted_on_first_open_and_plays(self):
        self.record_a_clip()
        sound = self.peaks()
        self.do("SAVE")
        folder = PF.open_folder(self.app)
        # What every project saved before #297 holds: the copy `tape_save`
        # writes to any path that is not the medium's own.
        old = os.path.join(self.app.state_dir, "old.wav")
        self.assertEqual(self.native.tape_save(0, old).get("type"), "ok")
        self.assertTrue(is_old_medium(old))
        PF.new(self.app)
        shutil.copyfile(old, os.path.join(folder, "tape-1.wav"))

        self.assertTrue(PF.load(self.app, folder))
        medium = os.path.join(folder, "tape-1.wav")
        self.assertFalse(is_old_medium(medium), "the old WAV was not converted")
        self.assertFalse([n for n in os.listdir(folder) if n.endswith(".old.wav")])
        self.assertEqual(self.peaks(), sound)

        # and the second open is in place: nothing left to convert
        PF.new(self.app)
        loads = []
        real = self.native.tape_load
        self.native.tape_load = lambda t, p: loads.append(t) or real(t, p)
        self.assertTrue(PF.load(self.app, folder))
        self.assertEqual(loads, [])
        self.assertEqual(self.peaks(), sound)

    def test_an_old_working_autosave_is_converted_at_boot(self):
        self.record_a_clip()
        clip = self.app.tape.tracks[0][0]
        span = (clip.src, clip.length)
        sound = self.peaks()
        old = os.path.join(self.app.state_dir, "old.wav")
        self.native.tape_save(0, old)
        PF.new(self.app)                  # an empty working folder
        # the old autosave named track 0's file from 0
        shutil.move(old, os.path.join(STATE, "tape-0.wav"))

        native = host(self, library_root=LIB)
        app, audio = app_for(native, STATE, fresh=False, library_root=LIB)
        self.addCleanup(audio.stop)
        self.assertEqual([n for n in media(STATE) if n.endswith(".wav")],
                         ["tape-1.wav"])
        self.assertFalse(is_old_medium(os.path.join(STATE, "tape-1.wav")))
        self.assertEqual(native.clip_peaks(0, span[0], span[1], 16)["max"],
                         sound)



class TestTheSavedDocumentFollowsTheTape(TapeDirCase):
    """#410 (1): SAVE then LOAD after a compaction hears the take."""

    def test_a_compaction_after_save_rewrites_the_projects_clips(self):
        for _ in range(3):                      # two dead takes under the third
            self.record_a_clip(track=0)
        sound = self.peaks()
        tape = self.native.refresh()["tape"]["tracks"][0]
        self.assertGreater(tape["dead_frames"] * 2, tape["used_frames"],
                           "the medium is not over half dead: no compaction")
        self.do("SAVE")
        self.run_blocks(2)                      # the result push and the remap
        folder = PF.open_folder(self.app)
        used = self.used(0)
        self.assertLess(used, tape["used_frames"], "nothing was compacted")
        clip = PF.read(folder)["tape"]["tracks"][0]["clips"][0]
        self.assertLessEqual(clip["src_offset"] + clip["len"], used)

        PF.new(self.app)
        self.assertTrue(PF.load(self.app, folder))
        self.assertEqual(self.peaks(), sound)
        self.assertGreater(max(self.peaks()), 0.0)       # and it is not silence


class TestTheFirstBootOverAnOldAutosave(TapeDirCase):
    """#410 (2): a pre-#297 autosave with a project open. Its working folder's
    old media are the live tape; the project holds only what was last SAVEd."""

    def test_the_working_tape_is_kept_and_the_project_stays_open(self):
        self.record_a_clip(track=0)                             # take A
        self.record_a_clip(track=1)                             # take B
        sounds = [self.peaks(0), self.peaks(1)]
        old = [os.path.join(self.app.state_dir, "old-%d.wav" % t) for t in (0, 1)]
        for t, path in enumerate(old):
            self.assertEqual(self.native.tape_save(t, path).get("type"), "ok")
        self.do("SAVE")
        folder = PF.open_folder(self.app)
        opened = self.app.open_project
        # P as it was saved before B: A's old copy and nothing for track 1
        shutil.copyfile(old[0], os.path.join(folder, "tape-1.wav"))
        os.remove(os.path.join(folder, "tape-2.wav"))
        # the working folder of that session: both takes, named from 0
        self.app.doc.save_working()
        self.app.project.save_audio(force=True)
        shutil.move(old[0], os.path.join(STATE, "tape-0.wav"))
        shutil.move(old[1], os.path.join(STATE, "tape-1.wav"))

        native = host(self, library_root=LIB)
        app, audio = app_for(native, STATE, fresh=False, library_root=LIB)
        self.addCleanup(audio.stop)
        for t in (0, 1):
            clip = app.tape.tracks[t][0]
            self.assertEqual(native.clip_peaks(t, clip.src, clip.length, 16)["max"],
                             sounds[t], "take %s is gone" % "AB"[t])
        self.assertEqual(app.project.tape_folder, os.path.abspath(STATE))
        self.assertEqual(app.open_project, opened)

        # the next SAVE is P's: its old media are replaced by the live tape
        # (the move is refused, P holds media; the copy fallback is #411's)
        PF.save(app)
        PF.new(app)
        self.assertTrue(PF.load(app, folder))
        for t in (0, 1):
            clip = app.tape.tracks[t][0]
            self.assertEqual(native.clip_peaks(t, clip.src, clip.length, 16)["max"],
                             sounds[t], "take %s did not reach P" % "AB"[t])


def write_old_wav(path, seconds, rate=48000):
    """A 16-bit stereo WAV with the 44-byte header every pre-#297 project
    holds (`is_old_medium`), a quiet sine all the way to its last frame."""
    n = int(seconds * rate)
    x = (np.sin(np.arange(n) * 2 * np.pi * 220 / rate) * 8000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(x, 2).tobytes())
    return n


class TestAnOldMediumLongerThanTheRuler(TapeDirCase):
    """#410 (3): the host converts what fits and answers ok; the rest of the
    take must not go with the aside copy."""

    def test_the_full_copy_stays_aside(self):
        self.record_a_clip()
        self.do("SAVE")
        folder = PF.open_folder(self.app)
        PF.new(self.app)
        n = write_old_wav(os.path.join(folder, "tape-1.wav"), 40.0)   # 30 s host
        self.assertTrue(is_old_medium(os.path.join(folder, "tape-1.wav")))

        self.assertTrue(PF.load(self.app, folder))
        aside = os.path.join(folder, "tape-1.old.wav")
        self.assertTrue(os.path.exists(aside), "the only full copy was deleted")
        self.assertEqual(old_medium_frames(aside), n)
        self.assertLess(self.used(0), n)

        # and leaving the project for NEW does not take it either
        PF.new(self.app)
        self.assertTrue(os.path.exists(aside))

    def test_one_that_fits_still_goes(self):
        """The control: a whole conversion deletes its aside, as before."""
        self.record_a_clip()
        self.do("SAVE")
        folder = PF.open_folder(self.app)
        PF.new(self.app)
        n = write_old_wav(os.path.join(folder, "tape-1.wav"), 2.0)
        self.assertTrue(PF.load(self.app, folder))
        self.assertFalse([x for x in os.listdir(folder) if x.endswith(".old.wav")])
        self.assertEqual(self.used(0), n)


def files(folder):
    """`{name: bytes}` of the media in `folder` — to say "byte for byte"."""
    out = {}
    for n in media(folder):
        with open(os.path.join(folder, n), "rb") as f:
            out[n] = f.read()
    return out


class TestTheWorkingFilesFollowTheTape(TapeDirCase):
    """#411 (1): when the tape changes folders, the two working files a boot
    trusts (`project.json`'s `open_project`, `tape.json`'s clips) are written
    at once. A crash used to boot one folder's media under another session's
    clips: `open_project` waited for the 2 s tick, `tape.json` for a STOP."""

    def crash(self):
        """A new App on the same state, with no tick, STOP or exit between."""
        native = host(self, library_root=LIB)
        app, audio = app_for(native, STATE, fresh=False, library_root=LIB)
        self.addCleanup(audio.stop)
        return native, app

    @staticmethod
    def clips(app, track):
        return [(c.start, c.length, c.src) for c in app.tape.tracks[track]]

    def test_a_crash_right_after_load_boots_the_loaded_project(self):
        self.record_a_clip(track=0)
        PF.save(self.app)
        folder, opened = PF.open_folder(self.app), self.app.open_project
        saved, sound = self.clips(self.app, 0), self.peaks(0)
        PF.new(self.app)
        self.record_a_clip(track=0, blocks=40)    # an unsaved session, autosaved
        self.record_a_clip(track=1, blocks=40)
        self.app.doc.flush()

        self.assertTrue(PF.load(self.app, folder))
        native, app = self.crash()
        self.assertEqual(app.project.tape_folder, os.path.abspath(folder))
        self.assertEqual(app.open_project, opened)
        self.assertEqual(self.clips(app, 0), saved)
        self.assertEqual(self.clips(app, 1), [])
        clip = app.tape.tracks[0][0]
        self.assertEqual(native.clip_peaks(0, clip.src, clip.length, 16)["max"],
                         sound)

    def test_a_crash_right_after_the_first_save_boots_the_project(self):
        self.record_a_clip(track=0)
        self.app.doc.flush()
        recorded, sound = self.clips(self.app, 0), self.peaks(0)
        PF.save(self.app)
        folder, opened = PF.open_folder(self.app), self.app.open_project

        native, app = self.crash()
        self.assertEqual(app.project.tape_folder, os.path.abspath(folder))
        self.assertEqual(app.open_project, opened)
        self.assertEqual(self.clips(app, 0), recorded)
        clip = app.tape.tracks[0][0]
        self.assertEqual(native.clip_peaks(0, clip.src, clip.length, 16)["max"],
                         sound)

    def test_a_crash_right_after_new_boots_the_new_empty_project(self):
        self.record_a_clip(track=0)
        PF.save(self.app)
        self.app.doc.flush()
        new = PF.new(self.app)                # D97: its folder exists at once
        _native, app = self.crash()
        self.assertEqual(app.project.tape_folder, os.path.abspath(new))
        self.assertEqual(app.open_project, PF.project_id(new))
        self.assertEqual(self.clips(app, 0), [])


class TestARefusedFolderChangeMovesNothing(TapeDirCase):
    """#411 (2): a refused `tape_dir` used to be ignored. LOAD went on with
    the project's clips over the old media and the project marked open; the
    next SAVE's move was refused `exists` and the copy fallback overwrote the
    project's media with the other folder's."""

    def refuse_opens(self):
        real = self.native.tape_dir
        self.native.tape_dir = lambda folder, move=False: (
            real(folder, move) if move else
            {"type": "error", "code": "exists", "message": "refused"})
        return real

    def test_a_refused_load_leaves_the_session_and_the_project_alone(self):
        self.record_a_clip(track=0)
        PF.save(self.app)
        folder = PF.open_folder(self.app)
        new = PF.new(self.app)
        opened = self.app.open_project
        self.record_a_clip(track=0, blocks=40)
        session = self.peaks(0)

        self.refuse_opens()
        self.assertFalse(PF.load(self.app, folder))
        self.assertEqual(self.app.open_project, opened)
        self.assertEqual(self.app.project.tape_folder, os.path.abspath(new))
        self.assertEqual(self.peaks(0), session, "the session lost its take")

    def test_after_a_refused_load_save_makes_a_new_project(self):
        self.record_a_clip(track=0)
        PF.save(self.app)
        folder = PF.open_folder(self.app)
        before = files(folder)
        PF.new(self.app)
        self.record_a_clip(track=0, blocks=40)

        real = self.refuse_opens()
        PF.load(self.app, folder)
        self.native.tape_dir = real
        saved = PF.save(self.app)
        self.assertNotEqual(os.path.abspath(saved), os.path.abspath(folder))
        self.assertEqual(files(folder), before, "the project's media changed")

    def test_save_into_a_folder_of_other_media_is_refused(self):
        """The guard behind it: whatever leaves `open_project` naming a folder
        the tape is not in, SAVE never copies over that folder's media."""
        self.record_a_clip(track=0)
        PF.save(self.app)
        folder, opened = PF.open_folder(self.app), self.app.open_project
        before = files(folder)
        new = PF.new(self.app)
        self.record_a_clip(track=0, blocks=40)
        self.app.open_project = opened        # the state the old LOAD left

        self.assertIsNone(PF.save(self.app))
        self.assertEqual(files(folder), before, "the project's media changed")
        self.assertEqual(self.app.project.tape_folder, os.path.abspath(new))
        self.assertTrue(media(new), "the session's take left its folder")

        # and the autosave (D97) writes no document over that folder either
        self.assertFalse(PF.autosave(self.app))
        self.app.flush()
        self.assertEqual(files(folder), before)


if __name__ == "__main__":
    unittest.main()
