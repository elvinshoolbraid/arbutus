"""STOP writes the clip list, and no audio at all (#297; #93 before it).

Until C1 step 3b `persist.save_audio` wrote each dirty track's medium out of
the audio process with `tape_save` — ~0.9 s per 120 s track on the CM4's eMMC,
the screen frozen for all of it (C6 #79), which is why #93 made it dirty
tracks only. Since #297 the audio process writes every take into the folder
the tape lives in as it happens, so STOP has nothing to copy: these tests say
`tape_save` is never called and `tape.json` (the clip geometry) is still
written when, and only when, the layout moved.

The negative control (#56) is `test_a_changed_layout_rewrites_the_clip_list`:
the same store has to write again when a clip moves, or "nothing was written"
would pass against a `save_audio` that does nothing at all.
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

from app import tape as T                                   # noqa: E402
from app.persist import ProjectStore                        # noqa: E402
from app.project_file import Document                       # noqa: E402
from app.tape import Clip                                   # noqa: E402


class FakeTape:
    def __init__(self):
        self.tracks = [[] for _ in range(T.NUM_TRACKS)]


class FakeNative:
    """Counts `tape_save` (which must stay at zero) and answers `state`."""

    def __init__(self):
        self.saves = [0] * T.NUM_TRACKS
        self.frames = [0] * T.NUM_TRACKS
        self.state = {"tape": {"tracks": [{"used_frames": 0}
                                          for _ in range(T.NUM_TRACKS)]}}

    def used(self, track, frames):
        self.state["tape"]["tracks"][track]["used_frames"] = frames

    def tape_save(self, track, path):
        self.saves[track] += 1
        return {"type": "ok", "frames": 0}


class FakeApp:
    def __init__(self):
        self.tape = FakeTape()
        self.native = FakeNative()


class ClipListCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="arbutus-persist-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = FakeApp()
        self.store = ProjectStore(self.app, self.dir)

    def put(self, track, start, length, gain=1.0):
        self.app.tape.tracks[track] = [Clip(start, length, gain)]

    def doc(self):
        with open(os.path.join(self.dir, "tape.json")) as f:
            return json.load(f)

    # -- the claim ---------------------------------------------------------
    def test_stop_writes_no_audio(self):
        for t in range(4):
            self.put(t, 0, 1000 * (t + 1))
        self.assertTrue(self.store.save_audio())
        self.assertEqual(self.app.native.saves, [0] * T.NUM_TRACKS,
                         "STOP copied a medium the take already wrote")
        self.assertEqual([len(tr["clips"]) for tr in self.doc()["tracks"]][:4],
                         [1, 1, 1, 1])
        self.assertFalse([n for n in os.listdir(self.dir) if n.endswith(".wav")])

    def test_a_changed_layout_rewrites_the_clip_list(self):
        """The control: the store has to be able to write again."""
        self.put(0, 0, 1000)
        self.store.save_audio()
        self.put(0, 0, 2000)                      # the take grew
        self.assertTrue(self.store.save_audio())
        self.assertEqual(self.doc()["tracks"][0]["clips"][0]["len"], 2000)

    def test_a_gain_change_is_a_change(self):
        self.put(0, 0, 1000)
        self.store.save_audio()
        self.put(0, 0, 1000, gain=0.5)
        self.assertTrue(self.store.save_audio())

    def test_nothing_changed_writes_nothing_at_all(self):
        self.put(0, 0, 1000)
        self.store.save_audio()
        self.assertFalse(self.store.save_audio(), "an unchanged tape was re-saved")

    def test_force_writes(self):
        self.put(0, 0, 1000)
        self.store.save_audio()
        self.assertTrue(self.store.save_audio(force=True))

    def test_medium_frames_are_the_audio_process_s(self):
        self.app.native.used(0, 4321)
        self.put(0, 0, 1000)
        self.store.save_audio()
        self.assertEqual(self.doc()["tracks"][0]["medium_frames"], 4321)
        self.assertEqual(self.doc()["tracks"][1]["medium_frames"], 0)

    def test_forget_media_writes_the_clip_list_again(self):
        """LOAD, NEW and a restart replace the clip lists wholesale;
        `project_file.resync` calls this so the next STOP writes them."""
        self.put(0, 0, 1000)
        self.store.save_audio()
        self.store.forget_media()
        self.assertTrue(self.store.save_audio(),
                        "the clip list was not re-written after a wholesale change")

    # -- a LOAD is not a change -------------------------------------------
    def test_a_loaded_project_is_not_dirty(self):
        self.put(0, 0, 1000)
        self.put(2, 0, 3000)
        self.store.save_audio()

        fresh = ProjectStore(FakeApp(), self.dir)
        self.assertTrue(fresh.load_audio())
        self.assertFalse(fresh.save_audio(),
                         "a STOP straight after a LOAD wrote the clip list again")


class _ExportableApp(FakeApp):
    """`FakeApp` plus what `save_working()` reads, and the § 11.1 flag."""

    def __init__(self):
        super().__init__()
        self.usb_exported = False
        self.pages = {}
        self.bpm = 120.0
        self.page_id = "SYNTH"
        self.active_instrument_id = "SYNTH"
        self.open_project = None
        self.tape.snapshot = lambda: {}
        self.mixer = type("M", (), {"snapshot": staticmethod(lambda: {})})()


class WhileExported(unittest.TestCase):
    """Q3 audit (#31): `/data` is unmounted while USB storage mode is on
    (`device-os-spec.md` § 11.1) and this store's directory is under it, so a
    write then lands on the bare mount point of the OS partition and is hidden
    by the remount. While `app.usb_exported` is set nothing here writes; the
    moment it clears, what was pending lands. The second half of each test is
    the negative control (#56): the same call, the flag down, must write."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="arbutus-persist-usb-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = _ExportableApp()
        self.store = ProjectStore(self.app, self.dir)
        self.app.project = self.store
        self.doc = Document(self.app, self.dir)
        self.app.tape.tracks[0] = [Clip(0, 1000, 1.0)]

    def test_stop_writes_nothing_while_exported_and_does_after(self):
        self.app.usb_exported = True
        self.assertFalse(self.store.save_audio(),
                         "save_audio wrote with /data in a computer's hands")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "tape.json")))
        # the flag clears (the remount) and the clip list is still pending
        self.app.usb_exported = False
        self.assertTrue(self.store.save_audio())
        self.assertTrue(os.path.exists(os.path.join(self.dir, "tape.json")))

    def test_the_timer_writes_no_project_json_while_exported(self):
        self.app.usb_exported = True
        self.doc.tick(100.0)
        self.doc.save_working()
        self.assertFalse(os.path.exists(self.doc.path),
                         "project.json written while /data was exported")
        self.app.usb_exported = False
        self.doc.tick(100.5)          # the remount's first tick, not 2 s later
        self.assertTrue(os.path.exists(self.doc.path))


class RefusedWrites(unittest.TestCase):
    """Q3 audit (#31): a card that refuses the autosave — `/data` full or
    read-only — is a journal line and a retry, never an exception out of
    `App.tick`. The state directory is made unwritable by putting a *file*
    where the directory should be (works as root too); the control is the
    same store once a directory is there."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="arbutus-persist-refused-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.state = os.path.join(self.dir, "state")
        with open(self.state, "w") as f:
            f.write("not a directory")
        self.app = _ExportableApp()
        self.store = ProjectStore(self.app, self.state)
        self.app.project = self.store
        self.doc = Document(self.app, self.state)
        self.app.tape.tracks[0] = [Clip(0, 1000, 1.0)]

    def _unblock(self):
        os.remove(self.state)
        os.makedirs(self.state)

    def test_project_json_refused_is_not_an_exception(self):
        self.doc.tick(100.0)                 # would raise on the old code
        self.doc.save_working()
        self.assertFalse(os.path.isdir(self.state))
        self._unblock()
        self.doc.tick(102.5)
        self.assertTrue(os.path.exists(self.doc.path))

    def test_tape_json_refused_stays_pending(self):
        self.assertFalse(self.store.save_audio())
        self._unblock()
        self.assertTrue(self.store.save_audio())
        self.assertTrue(os.path.exists(self.store.audio_path))

    def test_a_slot_store_refused_stays_dirty_and_retries(self):
        from app.slots import SlotStore
        store = SlotStore("SYNTH", self.state, debounce=0.0)
        store.bind(lambda: {"x": 1})
        store.touch(now=1.0)
        store.tick(now=2.0)                    # would raise on the old code
        self.assertFalse(os.path.exists(store.path))
        self.assertTrue(store._dirty)
        self._unblock()
        store.tick(now=3.0)
        self.assertTrue(os.path.exists(store.path))
        self.assertFalse(store._dirty)



class OnlyOnChange(unittest.TestCase):
    """#624 (risk 10 of the m-emu-app review): the 2 s timer rewrote
    `project.json` whether or not anything changed, ~43 000 create+rename
    cycles a day on the device's card. The timer now writes only a snapshot
    that differs from the last one written; an explicit `save_working()`
    (SAVE's `commit`, `flush`) still always writes."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="arbutus-persist-change-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = _ExportableApp()
        self.store = ProjectStore(self.app, self.dir)
        self.app.project = self.store
        self.doc = Document(self.app, self.dir)
        self.writes = []
        real = os.replace
        self.addCleanup(setattr, os, "replace", real)

        def counting(src, dst):
            self.writes.append(dst)
            return real(src, dst)
        os.replace = counting

    def test_an_unchanged_snapshot_is_not_rewritten(self):
        self.doc.tick(100.0)
        self.doc.tick(102.5)
        self.doc.tick(105.0)
        self.assertEqual(len(self.writes), 1,
                         "the timer rewrote an unchanged project.json")

    def test_a_change_is_written_on_the_next_tick(self):
        self.doc.tick(100.0)
        self.app.bpm = 97.0                      # the negative control (#56)
        self.doc.tick(102.5)
        self.assertEqual(len(self.writes), 2)
        with open(self.doc.path) as f:
            self.assertEqual(json.load(f)["bpm"], 97.0)

    def test_a_deleted_file_is_written_again(self):
        self.doc.tick(100.0)
        os.remove(self.doc.path)
        self.doc.tick(102.5)
        self.assertTrue(os.path.exists(self.doc.path))

    def test_an_explicit_save_always_writes(self):
        self.doc.tick(100.0)
        self.doc.save_working()
        self.assertEqual(len(self.writes), 2)


if __name__ == "__main__":
    unittest.main()
