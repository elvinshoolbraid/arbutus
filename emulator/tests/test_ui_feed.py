"""D17 (#276) — the element map and the actions the UI publishes for the phone.

`app/ui_feed.py`. The relay (host) and the end-to-end path are
`arbutus_os/tests/test_ui_feed.py`; this file is the UI's half: the map names
what the frame draws, and an encoder turn says which parameter it moved.
"""

import unittest

from app import ui_feed
from app.ui_feed import REMAP_TICKS, element_map
from tests.native_support import app_for, host, state_dir
from ui import zones

STATE_DIR = state_dir("ui_feed")
MAX_ACTIONS = 64            # the host's limit per `ui_action` (PROTOCOL.md)


class TheElementMap(unittest.TestCase):

    def setUp(self):
        native = host(self)
        self.app, self.audio = app_for(native, STATE_DIR, fresh=True)
        self.addCleanup(self.audio.stop)
        self.sent = []
        native.publish = lambda msg: self.sent.append(msg) or True

    def ids(self):
        return [e["id"] for e in element_map(self.app)]

    def test_the_fixed_zones_are_always_named(self):
        for page in sorted(self.app.pages):
            self.app.goto(page)
            ids = self.ids()
            self.assertEqual(ids[:3], ["header", "strip", "main"], page)
            for e in element_map(self.app):
                x, y, w, h = e["rect"]
                self.assertTrue(0 <= x and 0 <= y and x + w <= zones.WIDTH
                                and y + h <= zones.HEIGHT, (page, e))

    def test_a_param_cell_is_named_by_its_slot_and_labelled_as_drawn(self):
        """One row (#1231): slots 0-3, and 4-7 in the same cells while SHIFT
        is held; the other four are not on the screen, so not in the map."""
        self.app.goto("SYNTH")
        slots = self.app.current_page.slots()
        for shift in (False, True):
            self.app.shift_held = shift
            by_id = {e["id"]: e for e in element_map(self.app)}
            shown = range(4, 8) if shift else range(4)
            for idx, slot in enumerate(slots[:8]):
                if slot is None or idx not in shown:
                    self.assertNotIn("param.%d" % idx, by_id)
                    continue
                e = by_id["param.%d" % idx]
                self.assertEqual(e["label"], slot.name)
                self.assertEqual(e["rect"], list(zones.param_cell(idx % 4)))

    def test_a_turn_names_the_parameter_it_moved(self):
        self.app.goto("SYNTH")
        name = self.app.current_page.slots()[1].name
        self.app.on_encoder(1, 1)
        self.app.tick()
        acts = [a for m in self.sent if m["type"] == "ui_action"
                for a in m["actions"]]
        turn = [a for a in acts if a["kind"] == "encoder"]
        self.assertEqual(len(turn), 1)
        self.assertEqual((turn[0]["id"], turn[0]["delta"], turn[0]["param"]),
                         ("ENC2", 1, name))

    def test_the_map_goes_down_once_until_it_changes(self):
        self.app.tick()
        self.app.tick()
        maps = [m for m in self.sent if m["type"] == "ui_elements"]
        self.assertEqual(len(maps), 1)
        self.app.goto("MIXER")
        self.app.tick()
        maps = [m for m in self.sent if m["type"] == "ui_elements"]
        self.assertEqual([m["page"] for m in maps], ["SYNTH", "MIXER"])

    # -- #412 ------------------------------------------------------------------
    def actions(self):
        return [a for m in self.sent if m["type"] == "ui_action"
                for a in m["actions"]]

    def test_a_tick_of_more_than_64_actions_goes_down_whole_in_chunks(self):
        """The host refuses a `ui_action` of more than 64 whole; a played
        phrase after a stalled frame is one tick, and none of it may be lost."""
        self.app.tick()
        self.sent.clear()
        for _ in range(33):
            self.app.on_key("NOTE_1", True)
            self.app.on_key("NOTE_1", False)
        self.app.tick()
        batches = [m["actions"] for m in self.sent if m["type"] == "ui_action"]
        self.assertTrue(all(len(b) <= MAX_ACTIONS for b in batches), batches)
        keys = [a for b in batches for a in b if a["kind"] == "key"]
        self.assertEqual(len(keys), 66)
        self.assertEqual([a["down"] for a in keys], [True, False] * 33)

    def test_the_strip_is_labelled_not_valued(self):
        """PROTOCOL.md: labels, never values. The strip's text moves (a flash,
        SPEED x1.20, BATT n%), so its label must not, or the map is pushed
        again on every detent."""
        self.app.tick()
        before = [m for m in self.sent if m["type"] == "ui_elements"]
        self.app.flash("SAVED PROJECT 3", "SAVED")
        self.app.tick()
        after = [m for m in self.sent if m["type"] == "ui_elements"]
        self.assertEqual(len(after), len(before), "a flash republished the map")
        strip = [e for e in element_map(self.app) if e["id"] == "strip"]
        self.assertEqual(strip[0]["label"], "")

    def test_a_take_starting_and_stopping_is_an_action(self):
        self.app.goto("TRACK")
        self.app.tick()
        self.sent.clear()
        for k in ("REC", "PLAY"):           # arm, then roll: a take
            self.app.on_key(k, True)
            self.app.on_key(k, False)
        self.app.tick()
        self.assertTrue(self.app.recording)
        self.app.on_key("STOP", True)
        self.app.on_key("STOP", False)
        self.app.tick()
        self.assertFalse(self.app.recording)
        self.assertEqual([a["on"] for a in self.actions() if a["kind"] == "rec"],
                         [True, False])

    def test_a_new_host_connection_is_sent_the_map_again(self):
        """A restarted host knows no map: the UI republishes it unchanged."""
        self.app.tick()
        self.app.tick()
        self.assertEqual(len([m for m in self.sent if m["type"] == "ui_elements"]), 1)
        native = self.app.native
        old = native.conn
        native.conn = object()          # only for the flush: the tick talks to it
        try:
            self.app.feed.flush(self.app)
        finally:
            native.conn = old
        self.assertEqual(len([m for m in self.sent if m["type"] == "ui_elements"]), 2)

    # -- #413 ------------------------------------------------------------------
    def builds(self, ticks):
        """How many times `ticks` idle ticks build the map."""
        n = [0]
        real = ui_feed.element_map

        def counted(app):
            n[0] += 1
            return real(app)
        ui_feed.element_map = counted
        try:
            for _ in range(ticks):
                self.app.tick()
        finally:
            ui_feed.element_map = real
        return n[0]

    def test_an_idle_tick_does_not_build_the_map(self):
        """The map was built and compared on every tick, ~170 us of the UI
        thread on the CM4 (#413); idle, it is rebuilt every REMAP_TICKS."""
        self.app.tick()
        self.assertEqual(self.builds(3 * REMAP_TICKS), 3)

    def test_input_and_a_menu_rebuild_it_at_once(self):
        self.app.goto("SYNTH")
        self.app.tick()
        self.app.on_encoder(1, 1)
        self.assertEqual(self.builds(1), 1)
        self.app.menu = self.app.pages["MIXER"]     # a page object the frame shows
        self.assertEqual(self.builds(1), 1)

    def test_a_change_with_no_input_is_sent_within_remap_ticks(self):
        self.app.goto("SYNTH")
        self.app.tick()
        self.sent.clear()
        page = self.app.current_page
        page.breadcrumb = lambda: ["SYNTH", "LOADED LATE"]
        for _ in range(REMAP_TICKS):
            self.app.tick()
        maps = [m for m in self.sent if m["type"] == "ui_elements"]
        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0]["elements"][0]["label"], "SYNTH › LOADED LATE")


class TheLiveStream(unittest.TestCase):
    """#853: the UI's half of the live stream (`PROTOCOL.md` § The live
    stream): its part of the situation document, the new action kinds, and
    `ui_frame` when the drawn frame changed while a phone watches."""

    def setUp(self):
        self.native = host(self)
        self.app, self.audio = app_for(self.native, STATE_DIR, fresh=True)
        self.addCleanup(self.audio.stop)
        self.sent = []
        self.native.publish = lambda msg: self.sent.append(msg) or True
        self.app.tick()

    def tap(self, key):
        self.app.on_key(key, True)
        self.app.on_key(key, False)
        self.app.tick()

    def situations(self):
        return [m["situation"] for m in self.sent if m["type"] == "ui_situation"]

    def actions(self, kind):
        return [a for m in self.sent if m["type"] == "ui_action"
                for a in m["actions"] if a["kind"] == kind]

    def test_the_half_goes_down_once_and_again_only_when_it_changes(self):
        self.app.goto("SYNTH")
        self.app.tick()
        first = self.situations()[-1]
        self.assertEqual((first["screen"]["page"], first["octave"]), ("SYNTH", 0))
        n = len(self.situations())
        for _ in range(2 * REMAP_TICKS):
            self.app.tick()
        self.assertEqual(len(self.situations()), n, "an idle UI republished its half")
        self.tap("LEFT")                                    # octave down
        self.assertEqual(len(self.situations()), n + 1)
        self.assertEqual(self.situations()[-1]["octave"], -1)

    def test_a_held_key_is_in_the_half_while_it_is_held(self):
        self.app.on_key("SHIFT", True)
        self.app.tick()
        self.assertEqual(self.situations()[-1]["keys"]["held"], ["SHIFT"])
        self.app.on_key("SHIFT", False)
        self.app.tick()
        self.assertEqual(self.situations()[-1]["keys"]["held"], [])

    def test_a_midi_keyboard_note_is_a_note_not_a_key(self):
        from drivers.desktop.midi_in import MidiIn

        class Msg:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        midi = MidiIn(self.app, open_port=False)
        midi.handle(Msg(type="note_on", note=60, velocity=100, channel=0))
        midi.handle(Msg(type="note_off", note=60, velocity=0, channel=0))
        self.app.tick()
        notes = self.actions("note")
        self.assertEqual([(a["note"], a["on"], a["source"]) for a in notes],
                         [(60, True, "midi"), (60, False, "midi")])
        self.assertEqual(notes[0]["key"], "NOTE_7")          # 60 - 53
        self.assertGreater(notes[0]["vel"], 0)
        self.assertEqual(self.actions("key"), [], "a MIDI note read as a key press")
        self.assertEqual(self.situations()[-1]["keys"]["held"], [])

    def test_selecting_and_arming_a_track_are_actions(self):
        self.app.goto("TRACK")
        self.app.tick()
        self.tap("2")
        self.tap("REC")
        self.assertEqual(self.actions("track"), [{"kind": "track", "selected": 2},
                                                 {"kind": "track", "armed": 2}])
        self.assertEqual(self.situations()[-1]["transport"]["selected"], 2)

    def test_new_is_a_project_action_and_the_half_names_it(self):
        from app import project_file
        folder = project_file.new(self.app)
        self.app.tick()
        acts = self.actions("project")
        self.assertEqual([a["op"] for a in acts], ["new"])
        self.assertEqual(acts[0]["id"], project_file.project_id(folder))
        half = self.situations()[-1]["project"]
        self.assertEqual((half["id"], half["new"]), (acts[0]["id"], True))

    def test_save_and_load_are_project_actions_and_a_load_is_not_new(self):
        """#1179 (t853 row 6): the other two `op`s, each naming its project;
        a LOADed project is not one NEW made."""
        from app import project_file
        made = project_file.new(self.app)
        copy = project_file.save(self.app, copy=True)       # the phone's Duplicate
        self.assertTrue(project_file.load(self.app, copy))
        self.app.tick()
        acts = self.actions("project")
        self.assertEqual([(a["op"], a["id"]) for a in acts],
                         [("new", project_file.project_id(made)),
                          ("save", project_file.project_id(copy)),
                          ("load", project_file.project_id(copy))])
        half = self.situations()[-1]["project"]
        self.assertEqual((half["id"], half["new"]), (project_file.project_id(copy), False))

    def test_a_sample_taken_is_a_capture_action(self):
        from testing.fake_mic import FakeMic
        mic = FakeMic()
        self.app.mic_driver_factory = lambda: mic
        self.app.goto("INPUT")
        self.app.tick()
        self.tap("1")                                       # ARM
        self.assertTrue(self.situations()[-1]["input"]["listening"])
        mic.tap()
        self.audio.pull(16)
        self.app.tick()
        caps = self.actions("capture")
        self.assertEqual(len(caps), 1, self.sent[-3:])
        self.assertTrue(caps[0]["file"].endswith(".wav"), caps[0])
        self.assertGreater(caps[0]["seconds"], 0)

    def test_a_frame_is_announced_only_while_watched_and_only_when_it_changed(self):
        import numpy as np
        feed = self.app.feed
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        frames = lambda: [m["rev"] for m in self.sent if m["type"] == "ui_frame"]  # noqa: E731
        feed.frame_drawn(self.app, fb)
        self.assertEqual(frames(), [], "announced with nobody watching")
        self.native.live_watchers = 1               # a `live {watchers:1}` push
        self.app.tick()
        feed.frame_drawn(self.app, fb)
        feed.frame_drawn(self.app, fb)              # the same pixels again
        fb[100, 100] = (255, 255, 255)
        feed.frame_drawn(self.app, fb)
        self.assertEqual(frames(), [1, 2])
        self.native.live_watchers = 0
        self.app.tick()
        fb[0, 0] = (1, 1, 1)
        feed.frame_drawn(self.app, fb)
        self.assertEqual(frames(), [1, 2])


if __name__ == "__main__":
    unittest.main()
