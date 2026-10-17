"""Stage J2: FILE › MIDI / USB / SYSTEM, the browser's file ops, the battery.

Four separate claims, and each one is about a thing the instrument either
really does or honestly says it cannot:

* **the settings document** — `<library>/settings.json` survives a restart,
  because a device setting is neither a patch nor a project;
* **MIDI** — the channel really filters, the clock really measures, and TEMPO's
  SYNC `midi` (locked since H2 for want of a source) really follows it;
* **USB** — switching MODE to STORAGE force-saves first (DDR-021), which is
  the whole of that rule and the only part of USB this host can do;
* **SYSTEM** — the list edits real settings and RESET takes 1-2-3-4 **twice**
  (DDR-064);
* **the browser as a file manager** (DDR-055) — LIFT moves a real file out of
  a real folder into `.trash/`, DROP moves it into another one, SHIFT+DROP
  makes `FOLDER-NN`, stock content refuses, consecutive lifts undo as one
  group, and a *project* — a folder with media in it — moves whole.

Every file op runs against a throwaway copy of the library. Nothing here may
touch the committed one.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np                                            # noqa: E402

from app import battery                                       # noqa: E402
from app import library_ops as OPS                            # noqa: E402
from app import project_file as PF                            # noqa: E402
from app.midi_clock import MidiClock, PPQN                    # noqa: E402
from app.settings import OMNI, Settings                       # noqa: E402
from pages import file as FILEPAGE                            # noqa: E402
from ui import palette                                        # noqa: E402
from tests.native_support import (app_for, host, library_copy,  # noqa: E402
                                  state_dir)

STATE = state_dir("file-system")
LIB = os.path.join(HERE, "state", "file-system-library")

CONFIRM = ("1", "2", "3", "4")


class FileCase(unittest.TestCase):
    def setUp(self):
        library_copy(LIB)
        battery.reset_cache()
        self.native = host(self, library_root=LIB)
        self.app, self.audio = app_for(self.native, STATE, fresh=True,
                                       library_root=LIB)
        self.addCleanup(self.audio.stop)
        self.app.goto("FILE")
        self.page = self.app.pages["FILE"]

    # -- gestures ----------------------------------------------------------
    def subpage(self, n: int):
        self.app.on_key(str(n + 1), True)
        self.app.on_key(str(n + 1), False)

    def press(self, key, shift=False):
        if shift:
            self.app.on_key("SHIFT", True)
        self.app.on_key(key, True)
        self.app.on_key(key, False)
        if shift:
            self.app.on_key("SHIFT", False)

    def confirm(self, keys=CONFIRM):
        for k in keys:
            self.press(k)

    def names(self):
        return [s.name if s else None for s in self.page.slots()]

    def values(self):
        return [s.format_value() if s else None for s in self.page.slots()]


# ---------------------------------------------------------------------------
# the settings document
# ---------------------------------------------------------------------------

class TestSettings(FileCase):
    def test_a_setting_survives_the_session(self):
        st = self.app.settings
        self.assertEqual(st.midi_channel, OMNI)
        self.assertFalse(os.path.exists(st.path))   # nothing written yet
        st.set("system", "brightness", 40)
        self.assertTrue(os.path.exists(st.path))

        with open(st.path) as f:
            doc = json.load(f)
        self.assertEqual(doc["format"], 1)
        self.assertEqual(doc["kind"], "settings")
        self.assertEqual(doc["system"]["brightness"], 40)

        # a second instrument on the same library reads it back
        self.assertEqual(Settings(LIB).get("system", "brightness"), 40)

    def test_a_refused_write_is_retried_until_it_lands(self):
        """t617 #3 (#876): `dirty` used to be cleared before the write, so a
        write the card refused once was forgotten. A directory where the
        temporary file goes refuses it (as root too)."""
        st = self.app.settings
        blocker = st.path + ".tmp"
        os.makedirs(blocker)
        st.set("system", "brightness", 40)
        self.assertFalse(os.path.exists(st.path))
        self.assertFalse(st.flush())
        self.assertTrue(st.dirty, "the refused setting was forgotten")
        os.rmdir(blocker)                    # the card takes writes again
        self.assertTrue(st.flush())
        self.assertEqual(Settings(LIB).get("system", "brightness"), 40)
        self.assertFalse(st.dirty)
        self.assertFalse(st.flush(), "a landed write wrote again")

    def test_it_is_not_in_the_project(self):
        """A project is a song; a setting is the device. Neither carries the
        other, and a project loaded onto a differently-configured instrument
        must not take its MIDI channel with it."""
        self.app.settings.set("midi", "in_ch", 4)
        doc = PF.capture(self.app, "PROJ_TEST")
        self.assertNotIn("settings", doc)
        self.assertNotIn("midi", doc)
        blob = json.dumps(doc)
        self.assertNotIn("in_ch", blob)

    def test_a_broken_document_is_the_defaults(self):
        with open(os.path.join(LIB, "settings.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(Settings(LIB).midi_channel, OMNI)

    def test_velocity_curves(self):
        st = self.app.settings
        self.assertAlmostEqual(st.velocity(0.5), 0.5)
        st.set("system", "vel_curve", "SOFT")
        self.assertGreater(st.velocity(0.5), 0.5)      # easier to reach the top
        st.set("system", "vel_curve", "HARD")
        self.assertLess(st.velocity(0.5), 0.5)
        st.set("system", "vel_curve", "FIXED")
        self.assertEqual(st.velocity(0.1), 1.0)        # the device's own keys


# ---------------------------------------------------------------------------
# FILE › MIDI
# ---------------------------------------------------------------------------

class TestMidiPage(FileCase):
    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.MIDI)

    def test_the_row_is_the_sketch(self):
        self.assertEqual(self.page.breadcrumb(), ["USB/FILE", "MIDI"])
        self.assertEqual(self.names(),
                         ["IN CH", "OUT CH", "CLOCK IN", "CLOCK OUT",
                          None, None, None, None])

    def test_in_channel_is_the_settings_document(self):
        self.assertEqual(self.values()[0], "OMNI")
        self.app.on_encoder(0, 3)
        self.assertEqual(self.values()[0], "3")
        self.assertEqual(self.app.settings.get("midi", "in_ch"), 2)  # 0-based
        self.app.on_encoder(0, -3)
        self.assertEqual(self.app.settings.get("midi", "in_ch"), OMNI)

    def test_there_is_no_midi_out(self):
        """Named, dim and inert (DDR-053's shape) — not a number that lies."""
        slots = self.page.slots()
        for idx in (1, 3):
            self.assertTrue(slots[idx].locked, slots[idx].name)
            self.assertTrue(slots[idx].dimmed, slots[idx].name)
        before = self.values()
        self.app.on_encoder(1, 5)
        self.app.on_encoder(3, 5)
        self.assertEqual(self.values(), before)

    def test_clock_in_arms_the_clock(self):
        self.assertFalse(self.app.midi_clock.enabled)
        self.app.on_encoder(2, 1)
        self.assertEqual(self.values()[2], "ON")
        self.assertTrue(self.app.midi_clock.enabled)
        self.assertTrue(self.app.settings.get("midi", "clock_in"))
        # and it is remembered: a fresh instrument on this library boots armed
        self.assertTrue(Settings(LIB).clock_in)

    def test_the_page_draws_on_every_state(self):
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        for delta in (0, 1):
            if delta:
                self.app.on_encoder(2, 1)
            self.app.render(fb)
        self.assertGreater(int(fb.sum()), 0)


class TestMidiClock(unittest.TestCase):
    """The meter itself — no app, no host: it is arithmetic on timestamps."""

    def feed(self, clock, bpm, pulses=PPQN, t0=100.0):
        dt = 60.0 / (bpm * PPQN)
        for i in range(pulses):
            clock.pulse(now=t0 + i * dt)
        return t0 + (pulses - 1) * dt

    def test_it_measures_the_tempo(self):
        c = MidiClock()
        c.set_enabled(True)
        end = self.feed(c, 96.0)
        self.assertAlmostEqual(c.bpm, 96.0, delta=0.2)
        self.assertTrue(c.alive(now=end))

    def test_a_disabled_clock_hears_nothing(self):
        c = MidiClock()
        self.feed(c, 120.0)
        self.assertEqual(c.bpm, 0.0)
        self.assertFalse(c.alive())

    def test_it_goes_stale_when_the_cable_stops(self):
        c = MidiClock()
        c.set_enabled(True)
        end = self.feed(c, 120.0)
        self.assertTrue(c.alive(now=end))
        self.assertFalse(c.alive(now=end + 1.0))

    def test_a_gap_in_the_clock_does_not_move_the_tempo(self):
        """Pulse 7 comes three periods late and every one after it as late:
        a pause or dropped pulses, which a fit through it would read as a
        slower tempo. The window starts again at the gap (#1126)."""
        c = MidiClock()
        c.set_enabled(True)
        dt = 60.0 / (120.0 * PPQN)
        t = 100.0
        for i in range(PPQN):
            t += dt * (4.0 if i == 7 else 1.0)
            c.pulse(now=t)
        self.assertAlmostEqual(c.bpm, 120.0, delta=1.0)

    def test_stamps_late_by_the_gil_still_read_the_wire(self):
        """#1126: each stamp waits for the GIL, up to ~9 ms late, never early.

        The lateness is the 3B+'s at Zero clocks behind a following UI (four
        runs: p50 0.4-0.7 ms, p90 3.4-3.9, p99 4.8-7.7, max 7.2-8.9): half
        the stamps under 1 ms, 4 in 10 at 1-4 ms, 1 in 10 at 4-9 ms. On that
        board the median of a beat's intervals wandered 127-135 BPM on a clock
        exact to 0.04 ms; every reading from the first bar on must be within
        the bench cell's 0.5 BPM (`p130_clock.py` TOLERANCE_BPM), 40 to 240.
        """
        import random
        rng = random.Random(1126)
        for bpm in (40.0, 96.0, 132.0, 180.0, 240.0):
            c = MidiClock()
            c.set_enabled(True)
            dt = 60.0 / (bpm * PPQN)
            worst = 0.0
            for i in range(12 * PPQN):
                r = rng.random()
                lo, hi = (0.0, 0.001) if r < 0.5 else \
                    (0.001, 0.004) if r < 0.9 else (0.004, 0.009)
                late = rng.uniform(lo, hi)
                c.pulse(now=100.0 + i * dt + late)
                if i >= 4 * PPQN:
                    worst = max(worst, abs(c.bpm - bpm))
            self.assertLess(worst, 0.5, "at %g BPM" % bpm)

    def test_it_follows_a_tempo_change_within_a_bar(self):
        c = MidiClock()
        c.set_enabled(True)
        end = self.feed(c, 120.0, pulses=8 * PPQN)
        self.feed(c, 126.0, pulses=4 * PPQN + 1, t0=end + 60.0 / (126.0 * PPQN))
        self.assertAlmostEqual(c.bpm, 126.0, delta=0.01)

    def test_turning_it_off_forgets_everything(self):
        c = MidiClock()
        c.set_enabled(True)
        self.feed(c, 140.0)
        c.set_enabled(False)
        self.assertEqual(c.bpm, 0.0)
        self.assertFalse(c.running)


class _Msg:
    """The two fields `MidiIn.handle` reads off a mido message."""

    def __init__(self, type_, **kw):
        self.type = type_
        self.__dict__.update(kw)


class TestMidiIn(FileCase):
    def driver(self):
        from drivers.desktop.midi_in import MidiIn
        # no device: `_open` finds nothing and says so, which is the point —
        # the routing is testable with no MIDI hardware at all
        return MidiIn(self.app, config={"device": "zzz-no-such-port",
                                        "channel": -1, "note_offset": 53,
                                        "velocity": True,
                                        "cc": {"enc1": 16, "enc2": 17,
                                               "enc3": 18, "enc4": 19,
                                               "encoding": "twos-complement"}})

    def test_clock_bypasses_the_channel_filter(self):
        """System realtime has no channel; a master's clock is not on one."""
        self.app.settings.set("midi", "clock_in", True)
        self.app.midi_clock.set_enabled(True)
        self.app.settings.set("midi", "in_ch", 9)     # a channel nothing uses
        midi = self.driver()
        # stamped, not real time: back-to-back pulses are microseconds apart,
        # and one hiccup under load reads as a gap that restarts the window (#1164)
        period = 60.0 / (120.0 * PPQN)
        for i in range(PPQN):
            midi.handle(_Msg("clock"), now=100.0 + i * period)
        self.assertGreater(self.app.midi_clock.bpm, 0.0)
        midi.handle(_Msg("start"))
        self.assertTrue(self.app.midi_clock.running)
        midi.handle(_Msg("stop"))
        self.assertFalse(self.app.midi_clock.running)

    def test_a_caller_that_knows_when_a_message_arrived_can_say_so(self):
        """`handle(msg, now)` — the device's reader thread queues and the tick
        drains (`arbutus_os/midi.py`, device-os-spec § 8), so the meter has to
        be told when the pulse landed on the wire rather than when it was
        dispatched. Polling in the tick leaves `now` None, which is correct
        there: the poll *is* the arrival."""
        self.app.midi_clock.set_enabled(True)
        midi = self.driver()
        period = 60.0 / (120.0 * PPQN)               # 120 BPM
        for i in range(2 * PPQN):
            midi.handle(_Msg("clock"), now=100.0 + i * period)
        self.assertAlmostEqual(self.app.midi_clock.bpm, 120.0, places=3)
        midi.handle(_Msg("start"), now=200.0)
        self.assertTrue(self.app.midi_clock.running)

    def test_the_decoder_can_be_built_without_taking_a_port(self):
        """The device opens its own (every MIDI-class input, hot-plugged);
        two transports on one cable would each get half the messages."""
        from drivers.desktop.midi_in import MidiIn
        midi = MidiIn(self.app, open_port=False)
        self.assertIsNone(midi.port)
        self.assertIsNone(midi.port_name)

    def test_the_page_picks_the_channel(self):
        midi = self.driver()
        self.assertEqual(midi.in_channel, OMNI)
        self.app.settings.set("midi", "in_ch", 5)
        self.assertEqual(midi.in_channel, 5)
        self.assertTrue(midi._wanted_channel(_Msg("note_on", channel=5)))
        self.assertFalse(midi._wanted_channel(_Msg("note_on", channel=6)))

    def test_the_curve_shapes_an_incoming_note(self):
        self.app.settings.set("system", "vel_curve", "FIXED")
        midi = self.driver()
        seen = []
        self.app.on_key = lambda k, p, v=1.0: seen.append((k, p, v))
        midi.handle(_Msg("note_on", channel=0, note=60, velocity=20))
        self.assertEqual(seen[-1][2], 1.0)

    def test_the_snapshots_defaults_are_the_documents_defaults(self):
        """The default tuple is what a decoder with no settings decodes
        against, and what `arbutus_os/midi.py` holds before the first frame.
        It must be the same instrument as a fresh document, or a device would
        answer differently in its first 16.7 ms."""
        from drivers.desktop import midi_in as M
        from app.settings import DEFAULTS
        snap = M.MidiSnapshot()
        self.assertEqual(snap.in_ch, DEFAULTS["midi"]["in_ch"])
        self.assertEqual(snap.vel_curve, DEFAULTS["system"]["vel_curve"])
        self.assertEqual(snap.note_offset, M.DEFAULTS["note_offset"])
        self.assertEqual(snap.use_velocity, M.DEFAULTS["velocity"])

    def test_the_snapshot_is_the_two_settings_and_the_two_toml_keys(self):
        """#128: the device's reader thread sounds a MIDI note itself and may
        not read `Settings` while the tick is writing it, so everything the
        note decode needs is published once a frame as one immutable tuple
        (`device-os-spec.md` § 8.2)."""
        self.app.settings.set("midi", "in_ch", 7)
        self.app.settings.set("system", "vel_curve", "SOFT")
        snap = self.driver().snapshot()
        self.assertEqual(snap.in_ch, 7)
        self.assertEqual(snap.vel_curve, "SOFT")
        self.assertEqual(snap.note_offset, 53)
        self.assertTrue(snap.use_velocity)

    def test_the_snapshot_moves_when_the_player_turns_the_cell(self):
        midi = self.driver()
        self.assertEqual(midi.snapshot().in_ch, OMNI)
        self.app.settings.set("midi", "in_ch", 2)
        self.assertEqual(midi.snapshot().in_ch, 2)

    def test_one_decode_serves_both_threads(self):
        """`handle` on the main thread and the reader thread's `note_of` are
        the same function — the split is a snapshot, not a second decoder."""
        from drivers.desktop.midi_in import decode_note
        self.app.settings.set("system", "vel_curve", "FIXED")
        midi = self.driver()
        seen = []
        self.app.on_key = lambda k, p, v=1.0: seen.append((k, p, v))
        msg = _Msg("note_on", channel=0, note=60, velocity=20)
        midi.handle(msg)
        self.assertEqual(decode_note(msg, midi.snapshot()), seen[-1])
        self.assertEqual(midi.note_of(msg), seen[-1])

    def test_the_decode_refuses_everything_that_is_not_our_note(self):
        midi = self.driver()
        for msg in (_Msg("clock"), _Msg("start"),
                    _Msg("pitchwheel", channel=0, pitch=0),
                    _Msg("control_change", channel=0, control=16, value=1)):
            self.assertIsNone(midi.note_of(msg), msg.type)
        self.app.settings.set("midi", "in_ch", 3)
        self.assertIsNone(midi.note_of(_Msg("note_on", channel=0, note=60,
                                            velocity=9)), "wrong channel")

    def test_a_note_below_the_24_keys_still_decodes(self):
        """A 49-key controller has played the instrument since phase 3:
        `note_key` names a chromatic index and `App.on_key` adds it to
        NOTE_BASE_MIDI, negative index and all."""
        key, pressed, _ = self.driver().note_of(
            _Msg("note_on", channel=0, note=41, velocity=100))
        self.assertEqual((key, pressed), ("NOTE_-12", True))

    def test_a_note_on_at_zero_velocity_is_a_release(self):
        midi = self.driver()
        self.assertEqual(midi.note_of(_Msg("note_on", channel=0, note=60,
                                           velocity=0))[1], False)

    def test_pitch_bend_reaches_the_active_instrument(self):
        """DDR-052: bend comes from MIDI or from SHIFT+‹ ›. The wire is
        -8192..8191, the host gets -1..1 and owns the ramp and the range."""
        midi = self.driver()
        sent = []
        self.native.bend = lambda inst, v: sent.append((inst, v))
        self.app.goto("DRUM")
        midi.handle(_Msg("pitchwheel", channel=0, pitch=8191))
        midi.handle(_Msg("pitchwheel", channel=0, pitch=-8192))
        midi.handle(_Msg("pitchwheel", channel=0, pitch=0))
        self.assertEqual([i.lower() for i, _ in sent], ["drum"] * 3)
        self.assertAlmostEqual(sent[0][1], 1.0, places=2)
        self.assertEqual(sent[1][1], -1.0)
        self.assertEqual(sent[2][1], 0.0)
        # ...and not on the wrong channel
        self.app.settings.set("midi", "in_ch", 3)
        midi.handle(_Msg("pitchwheel", channel=0, pitch=4000))
        self.assertEqual(len(sent), 3)


class TestTempoFollowsTheClock(FileCase):
    def test_sync_midi_follows_clock_in(self):
        """H2 left `TempoPage.external_sync` waiting for this."""
        tempo = self.app.pages["TEMPO"]
        self.subpage(FILEPAGE.MIDI)
        self.app.on_encoder(2, 1)                    # CLOCK IN = ON
        self.app.goto("TEMPO")
        tempo.p_sync.set_value(1)                    # SYNC = MIDI
        self.assertTrue(tempo.external_sync)
        self.assertFalse(tempo.clock_locked)         # the switch is not a cable

        # the local controls stay locked either way: the clock owns the tempo
        self.assertTrue(tempo.slots()[0].locked)

        dt = 60.0 / (100.0 * PPQN)
        t = 500.0
        for i in range(PPQN * 2):
            self.app.midi_clock.pulse(now=t + i * dt)
        # `alive()` is measured against the wall clock, so drive the poll with
        # the same timeline the pulses were fed on
        self.assertGreater(self.app.midi_clock.bpm, 0.0)
        self.app.midi_clock._seen_at = __import__("time").monotonic()
        self.assertTrue(tempo.clock_locked)
        tempo.poll_external()
        self.assertAlmostEqual(self.app.bpm, 100.0, delta=0.5)
        self.assertAlmostEqual(self.native.state["tempo"], 100.0, delta=0.5)

    def test_clock_jitter_leaves_the_tape_alone(self):
        """Pages review risk 13 (#1123): with LINK on the BPM is the tape's
        speed, so a clock wobbling around 120 must send no `tempo` at all,
        and a real move is followed in 0.1 BPM steps."""
        tempo = self.app.pages["TEMPO"]
        tempo.p_sync.set_value(1)                    # SYNC = MIDI

        class Clock:                                 # a stub: the median is the input
            bpm = 120.0
            def alive(self):
                return True
        self.app.midi_clock = clock = Clock()
        sent = []
        tempo_msg = self.app.native.tempo
        self.app.native.tempo = lambda v: (sent.append(v), tempo_msg(v))
        for b in (120.06, 119.94, 120.09, 119.91, 120.07):
            clock.bpm = b
            tempo.poll_external()
        self.assertEqual(sent, [])
        self.assertEqual(self.app.bpm, 120.0)

        clock.bpm = 120.13                           # a whole step away: follow, rounded
        tempo.poll_external()
        self.assertEqual(sent, [120.1])
        clock.bpm = 120.04                           # back across the edge: hysteresis holds
        tempo.poll_external()
        self.assertEqual(sent, [120.1])
        clock.bpm = 95.0                             # a new tempo from the master
        tempo.poll_external()
        self.assertEqual(sent, [120.1, 95.0])

    def test_free_sync_ignores_a_running_clock(self):
        tempo = self.app.pages["TEMPO"]
        self.app.midi_clock.set_enabled(True)
        dt = 60.0 / (100.0 * PPQN)
        for i in range(PPQN):
            self.app.midi_clock.pulse(now=700.0 + i * dt)
        self.app.midi_clock._seen_at = __import__("time").monotonic()
        tempo.poll_external()
        self.assertEqual(self.app.bpm, 120.0)


# ---------------------------------------------------------------------------
# FILE › USB
# ---------------------------------------------------------------------------

class TestUsbPage(FileCase):
    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.USB)

    def test_the_row_is_the_sketch(self):
        """The sketch's fourth cell is `—`; since #103 it is the `.fw` line's
        UPDATE, locked and named until `fw.sock` answers (screen-sketches
        § FILE › USB *As built*)."""
        self.assertEqual(self.names(),
                         ["MODE", "CHARGE", "PAIR", FILEPAGE.FW_UPDATE,
                          None, None, None, None])

    def test_storage_force_saves_first(self):
        """DDR-021: no prompt, no decision. Since D97 (#817) the project is
        always open (a boot's `ensure_open`) and the save is its flush."""
        PF.ensure_open(self.app)
        self.app.on_encoder(0, 1)                     # OFF -> STORAGE
        self.assertEqual(self.values()[0], "STORAGE")
        saved = [n for v in PF.list_projects(self.app).values() for n in v]
        self.assertEqual(saved, ["PROJ_0001"])
        self.assertEqual(self.page.usb_saved, "PROJ_0001")

        # ...and coming back to STORAGE flushes it again: no new folder
        self.app.on_encoder(0, 1)                     # STORAGE -> MIDI
        self.app.on_encoder(0, -1)                    # back to STORAGE: saves
        names = [n for v in PF.list_projects(self.app).values() for n in v]
        self.assertEqual(sorted(names), ["PROJ_0001"])
        self.assertEqual(self.page.usb_saved, "PROJ_0001")

    def test_storage_stops_the_transport(self):
        """`tape_save` is refused while a take is open (J1's `project_file.halt`)."""
        self.app.tape.rec(0)
        self.app.on_encoder(0, 1)
        self.assertFalse(self.app.tape.recording)

    def test_charge_and_pair_are_named_and_dead(self):
        slots = self.page.slots()
        for idx in (1, 2):
            self.assertTrue(slots[idx].locked)
        before = self.values()
        self.app.on_encoder(1, 1)
        self.app.on_encoder(2, 1)
        self.assertEqual(self.values(), before)

    def test_it_draws(self):
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        self.app.on_encoder(0, 1)
        self.app.render(fb)
        self.assertGreater(int(fb.sum()), 0)


# ---------------------------------------------------------------------------
# FILE › USB — the `.fw` line (D5b, #103)
# ---------------------------------------------------------------------------

def fw_status(**over):
    """A `fw.sock status` document — `arbutus_os/fw_service.py::status`.

    Written out here rather than imported: `emulator/` never imports
    `arbutus_os/`, and `arbutus_os/tests/test_fw_pump.py` is where the two
    halves are checked against each other.
    """
    doc = {"type": "status", "version": "1.0.0", "other_slot_version": "0.9.0",
           "slot": "a", "other_slot": "b", "pending": None, "staged": None,
           "staged_version": "", "tries": 0,
           "kept": None, "last_error": "", "boot_ok": True, "os": "1.0.0",
           "signed_releases": True, "last_result": {}, "boot_result": {},
           "message": "", "inbox": []}
    doc.update(over)
    return doc


class TestFirmwareLine(FileCase):
    """§ 10.4 / § 10.5 on screen. `app.firmware` is written by
    `arbutus_os/pumps.py`'s `FwPump` and is **never** set on the emulator, so
    the no-socket case below is the one every ordinary run takes."""

    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.USB)
        self.sent = []

    def wire(self, doc):
        self.app.firmware = doc
        self.app.fw_command = self.sent.append

    def cell(self):
        return self.page.slots()[3]

    def draw(self):
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        return fb

    # -- no socket ------------------------------------------------------
    def test_with_no_fw_sock_the_cell_is_locked_and_the_line_is_j2s(self):
        self.assertIsNone(self.page.firmware)
        self.assertTrue(self.cell().locked)
        self.assertTrue(self.cell().dimmed)
        self.assertIsNone(self.page._fw_action())
        self.assertEqual(self.page._fw_message(), FILEPAGE.FW_LINE)
        self.assertEqual(self.page._fw_rows(), [])

    def test_a_locked_cell_cannot_be_armed(self):
        self.app.on_encoder(3, 1)
        self.assertIsNone(self.page._armed)

    def test_arming_with_no_door_says_so_instead_of_raising(self):
        """`app.firmware` without `app.fw_command` cannot happen through
        `FwPump`, which sets both — but the page must not crash on it."""
        self.app.firmware = fw_status(staged="b")
        self.page._arm(FILEPAGE.FW_RESTART)
        self.confirm()
        self.assertIsNone(self.page._armed)

    # -- what is running ------------------------------------------------
    def test_both_slots_are_on_the_page(self):
        self.wire(fw_status())
        rows = self.page._fw_rows()
        self.assertEqual([r[0] for r in rows], ["SLOTS", FILEPAGE.FW_UPDATE])
        self.assertIn("A 1.0.0", rows[0][1])
        self.assertIn("B 0.9.0", rows[0][1])

    def test_an_answering_socket_with_nothing_to_do_unlocks_nothing(self):
        self.wire(fw_status())
        self.assertIsNone(self.page._fw_action())
        self.assertTrue(self.cell().locked)
        self.assertEqual(self.page._fw_message(), FILEPAGE.FW_NONE)

    # -- UPDATE READY ---------------------------------------------------
    def test_a_staged_release_restarts_behind_1234(self):
        self.wire(fw_status(staged="b", staged_version="1.0.1", message=
                            "UPDATE READY — RESTART TO APPLY",
                            last_result={"result": "staged",
                                         "version": "1.0.1"}))
        self.assertEqual(self.page._fw_action(), FILEPAGE.FW_RESTART)
        self.assertFalse(self.cell().locked)
        self.assertEqual(self.cell().format_value(), FILEPAGE.FW_RESTART)
        self.app.on_encoder(3, 1)
        self.assertEqual(self.page._armed, FILEPAGE.FW_RESTART)
        self.assertEqual(self.cell().format_value(), "ARM")
        self.assertIn("1.0.1", self.page._armed_line())
        self.assertEqual(self.sent, [])                 # armed is not done
        self.confirm()
        self.assertEqual(self.sent, [{"type": "reboot"}])
        self.assertIsNone(self.page._armed)

    def test_three_of_the_four_is_not_a_restart(self):
        self.wire(fw_status(staged="b"))
        self.app.on_encoder(3, 1)
        self.confirm(("1", "2", "3"))
        self.assertEqual(self.sent, [])

    def test_a_wrong_key_puts_it_down(self):
        self.wire(fw_status(staged="b"))
        self.app.on_encoder(3, 1)
        self.confirm(("1", "3"))
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.page._armed)

    def test_turning_back_disarms(self):
        self.wire(fw_status(staged="b"))
        self.app.on_encoder(3, 1)
        self.app.on_encoder(3, -1)
        self.assertIsNone(self.page._armed)

    # -- UNSIGNED UPDATE ------------------------------------------------
    def unsigned(self, **over):
        entry = {"file": "box.arbox", "kind": "box", "version": "0.2.0",
                 "trust": "unsigned", "needs_confirm": True}
        return fw_status(inbox=[dict(entry, **over)])

    def test_an_unsigned_file_asks_and_then_applies_with_confirm(self):
        self.wire(self.unsigned())
        self.assertEqual(self.page._fw_action(), FILEPAGE.FW_INSTALL)
        self.assertEqual(self.page._fw_message(), FILEPAGE.FW_CONFIRM)
        self.app.on_encoder(3, 1)
        line = self.page._armed_line()
        self.assertIn("box.arbox", line)
        # the file's own `trust`, not the word "UNSIGNED" guessed here
        self.assertIn("UNSIGNED", line)
        self.confirm()
        self.assertEqual(self.sent, [{"type": "apply", "file": "box.arbox",
                                      "confirm": True}])

    def test_a_trusted_file_in_the_inbox_asks_nothing(self):
        """It installs at the next boot by itself (§ 10.4); the player has no
        decision to make, so there is no cell to turn."""
        self.wire(fw_status(inbox=[{"file": "rel.fw", "kind": "app",
                                    "version": "1.0.1", "trust": "signed",
                                    "needs_confirm": False}]))
        self.assertIsNone(self.page._fw_action())
        self.assertTrue(self.cell().locked)

    def test_a_staged_release_wins_over_an_unsigned_file(self):
        """`_stage_app` unpacks into the other slot, which while something is
        staged is that same slot: installing now would silently replace the
        release the player has just been told is ready."""
        doc = self.unsigned()
        doc["staged"] = "b"
        self.wire(doc)
        self.assertEqual(self.page._fw_action(), FILEPAGE.FW_RESTART)

    def test_a_release_on_trial_is_running_and_offers_no_restart(self):
        """#111's distinction on screen. `pending` is the release the pointer
        is already on — it *is* what this instrument is running, waiting only
        to draw a frame — so offering to restart into it would be offering to
        apply what is applied. Only `staged` is a restart."""
        self.wire(fw_status(pending="b", slot="b", version="1.0.1",
                            other_slot="a", other_slot_version="1.0.0",
                            boot_ok=False))
        self.assertIsNone(self.page._fw_action())
        self.assertTrue(self.cell().locked)
        rows = self.page._fw_rows()
        self.assertIn("B 1.0.1", rows[0][1])
        self.assertEqual(rows[1][1], "\u2014")

    def test_the_armed_line_names_the_staged_version(self):
        """`staged_version` first: since #111 the pointer has *not* moved, so
        `status.version` is honestly the running release and naming it here
        would be backwards."""
        self.wire(fw_status(staged="b", staged_version="1.2.0",
                            last_result={"result": "staged",
                                         "version": "1.1.0"}))
        self.page._arm(FILEPAGE.FW_RESTART)
        self.assertIn("1.2.0", self.page._armed_line())

    # -- UPDATE FAILED --------------------------------------------------
    def test_a_rollback_stands_until_it_is_dismissed(self):
        self.wire(fw_status(
            boot_result={"result": "rolled_back", "kept": "1.0.0",
                         "message": "UPDATE FAILED — KEPT 1.0.0"},
            message="UPDATE FAILED — KEPT 1.0.0"))
        self.assertEqual(self.page._fw_message(), "UPDATE FAILED — KEPT 1.0.0")
        self.assertEqual(self.page._fw_action(), FILEPAGE.FW_DISMISS)
        self.app.on_encoder(3, 1)
        self.confirm()
        self.assertEqual(self.sent, [{"type": "dismiss"}])

    def test_the_failed_line_outlives_what_happened_after_it(self):
        """§ 10.7 item 5: `status.message` is `boot_result`'s first."""
        self.wire(fw_status(
            boot_result={"message": "UPDATE FAILED — KEPT 1.0.0"},
            last_result={"result": "applied", "message": "SOMETHING ELSE"},
            message="UPDATE FAILED — KEPT 1.0.0"))
        self.assertEqual(self.page._fw_message(), "UPDATE FAILED — KEPT 1.0.0")

    # -- § 11.1 ----------------------------------------------------------
    def test_the_computer_holding_the_library_locks_it(self):
        """The inbox *is* the library root, and it is unmounted."""
        self.wire(fw_status(pending="b"))
        self.app.usb_exported = True
        self.assertTrue(self.cell().locked)
        self.page._arm(FILEPAGE.FW_RESTART)
        self.assertIsNone(self.page._armed)
        self.assertEqual(self.sent, [])

    # -- pixels ----------------------------------------------------------
    def test_it_draws_on_every_state(self):
        for doc in (None, fw_status(), fw_status(pending="b"),
                    self.unsigned(),
                    fw_status(boot_result={"message": "UPDATE FAILED — "
                                                      "KEPT 1.0.0"}),
                    fw_status(last_result={"result": "rejected",
                                           "message": "UPDATE REJECTED — "
                                                      "BAD SIGNATURE"},
                              message="UPDATE REJECTED — BAD SIGNATURE")):
            if doc is None:
                self.app.firmware = None
            else:
                self.wire(doc)
            self.assertTrue(self.draw().any())
        self.wire(fw_status(pending="b"))
        self.app.on_encoder(3, 1)
        self.assertTrue(self.draw().any())


# ---------------------------------------------------------------------------
# FILE › SYSTEM
# ---------------------------------------------------------------------------

class TestUsbExported(FileCase):
    """§ 11.1 (D6, #27): what FILE says and refuses while the computer holds
    `/data`.

    `app.usb_exported` is set by `arbutus_os/main.py` off `usb.sock` and is
    **never** true on the emulator, which has no socket — so this class sets
    it by hand, and nothing it proves changes a pixel in an ordinary run.
    """

    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.PROJECT)

    def export(self, on=True):
        self.app.usb_exported = on

    def test_all_four_actions_lock(self):
        """#617: NEW too. It no longer resets only RAM: since #297 it clears
        `/data/.state` and points the tape back there."""
        self.export()
        slots = self.page.slots()
        for i, name in enumerate(FILEPAGE.ACTIONS):
            if name is not None:              # ENC3, SAVE until D97 (#817)
                self.assertTrue(slots[i].locked, name)

    def test_nothing_is_locked_when_the_cable_is_not_holding_the_library(self):
        for slot in self.page.slots()[:len(FILEPAGE.ACTIONS)]:
            if slot is not None:
                self.assertFalse(slot.locked)

    def test_arming_load_from_the_keys_is_refused_too(self):
        """Arming is reachable from the strip keys and not only from a cell,
        so the refusal cannot live in `slots()` alone."""
        self.export()
        self.page._arm("LOAD")
        self.assertIsNone(self.page._armed)

    def test_new_and_reset_are_refused(self):
        """#617 (m-emu-pages spec 6): both write `/data`."""
        self.export()
        for action in ("NEW", FILEPAGE.RESET):
            self.page._arm(action)
            self.assertIsNone(self.page._armed, action)

    def test_the_reason_is_on_the_page(self):
        """§ 11.1: the instrument keeps playing, so this is a line on the page
        and not a modal."""
        self.export()
        lines = [t[1] for t in self.page.tickers()]
        self.assertIn(FILEPAGE.USB_EXPORTED_LINE, lines)
        self.export(False)
        self.assertEqual(self.page.tickers(), [])

    def test_it_draws_while_exported(self):
        self.export()
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        self.assertTrue(fb.any())


class TestWorkingStateWhileExported(FileCase):
    """Q3 audit (#31): the working-state autosave pauses while `/data` is in
    a computer's hands (`device-os-spec.md` § 11.1) — the slot stores, the
    2 s `project.json` timer, `flush()` and STOP's medium write — and lands
    on the first tick after the flag clears. The second half of each test is
    the control (#56): the same gesture with the flag down writes."""

    def _slot_path(self):
        return self.app.pages["SYNTH"].slot_store.path

    def test_a_slot_change_waits_for_the_remount(self):
        store = self.app.pages["SYNTH"].slot_store
        store.flush()
        if os.path.exists(store.path):
            os.remove(store.path)
        self.app.usb_exported = True
        store.touch(now=1000.0)
        self.app.tick(1010.0)                # well past the 0.25 s debounce
        self.app.flush()
        self.assertFalse(os.path.exists(store.path),
                         "a slot was written while /data was exported")
        self.app.usb_exported = False
        self.app.tick(1010.1)
        self.assertTrue(os.path.exists(store.path))

    def test_stop_writes_nothing_while_exported(self):
        """Since #297 STOP writes the clip list and never a medium; while
        `/data` is exported it writes neither, and the remount's STOP writes
        the clip list it held back."""
        calls = []
        real = self.native.tape_save
        self.native.tape_save = lambda t, p: calls.append(t) or real(t, p)
        tape_json = os.path.join(self.app.state_dir, "tape.json")
        if os.path.exists(tape_json):
            os.remove(tape_json)
        from app.tape import Clip
        self.app.tape.tracks[0] = [Clip(0, 4800, 1.0)]
        self.app.usb_exported = True
        self.app.on_key("STOP", True)
        self.app.on_key("STOP", False)
        self.assertFalse(os.path.exists(tape_json),
                         "STOP wrote into an unmounted /data")
        self.app.usb_exported = False
        self.app.on_key("STOP", True)
        self.app.on_key("STOP", False)
        self.assertTrue(os.path.exists(tape_json))
        self.assertEqual(calls, [], "STOP copied a medium")


class TestDotFilesAreNotContent(FileCase):
    """Q3 audit (#31): `audio/library/README.md` says a dot-prefixed folder is
    not content, and the browser's file listings applied it to folders only.
    A Mac writing to the exported `/data` (device-os-spec § 11) leaves
    `._name.patch` AppleDouble files beside every document; each was a row
    that did nothing when picked. The visible twin is the control (#56)."""

    def _items(self, category, folder):
        for f in self.app.library.list(category):
            if f.folder == folder:
                return f
        self.fail("no %s/%s folder" % (category, folder))

    def test_appledouble_files_are_not_listed(self):
        cases = [("synth", "osc", "._ghost.patch", "ghost.patch"),
                 ("fx", "racks", "._ghost.rack", "ghost.rack"),
                 ("mod", "racks", "._ghost.rack", "ghost.rack")]
        root = self.app.library.root
        for cat, folder, hidden, visible in cases:
            d = os.path.join(root, cat, folder)
            os.makedirs(d, exist_ok=True)      # the copy has no mod/racks
            for name in (hidden, visible):
                with open(os.path.join(d, name), "w") as f:
                    f.write("{}")
        self.app.library.rescan()
        for cat, folder, hidden, visible in cases:
            lf = self._items(cat, folder)
            self.assertNotIn(hidden, lf.items, "%s/%s lists %s" % (cat, folder, hidden))
            self.assertIn(visible, lf.items, "%s/%s lost %s" % (cat, folder, visible))


class TestSystemPage(FileCase):
    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.SYSTEM)

    def rows(self):
        return self.page._system_rows()

    def labels(self):
        return [r[1] for r in self.rows()]

    def goto(self, key):
        """Walk ENC1 to a row by key — the list has no fixed indices (#116)."""
        rows = self.rows()
        target = [r[0] for r in rows].index(key)
        self.app.on_encoder(0, target - self.page._row_index(rows))

    def test_two_knobs_and_a_list(self):
        self.assertEqual(self.names()[:4], ["SELECT", "VALUE", None, None])
        self.assertEqual(self.labels(),
                         ["MIDI VEL CURVE", "POWER-OFF", "DATE / TIME",
                          FILEPAGE.TAPE_ROW, "BRIGHTNESS", "WIFI",
                          "FORGET PHONES", "RESET"])
        # "KEY VEL (fixed / motion — hidden without an IMU)": this host says
        # it has no motion sensor, so the row is not in the list (§ 6.5)
        self.assertFalse(self.app.native.motion_sensor)
        self.assertNotIn("KEY VEL", self.labels())

    def test_enc1_selects_and_enc2_changes(self):
        self.app.on_encoder(0, 1)                     # POWER-OFF
        self.assertEqual(self.page.system_row, FILEPAGE.SYS_POWER)
        self.assertEqual(self.values()[0], "POWER-OFF")
        self.app.on_encoder(1, 1)
        self.assertEqual(self.app.settings.get("system", "power_off"), 5)
        self.assertEqual(self.values()[1], "5 MIN")

        self.app.on_encoder(0, -1)                    # MIDI VEL CURVE
        self.app.on_encoder(1, 1)
        self.assertEqual(self.app.settings.get("system", "vel_curve"), "SOFT")

        self.app.on_encoder(0, 4)                     # BRIGHTNESS
        self.assertEqual(self.page.system_row, FILEPAGE.SYS_BRIGHT)
        self.app.on_encoder(1, -3)
        self.assertEqual(self.app.settings.get("system", "brightness"), 50)

    def test_a_halt_only_board_locks_power_off(self):
        """#659 (t446 row 3): where `power.sock` says `halt_only` the device
        refuses the idle power-off, so the row reads OFF, dim and locked,
        and ENC2 changes nothing; the stored minutes are left alone."""
        self.app.settings.set("system", "power_off", 10)
        self.app.power = {"percent": 80, "halt_only": True}
        self.goto(FILEPAGE.SYS_POWER)
        rows = {r[0]: r for r in self.rows()}
        self.assertEqual(rows[FILEPAGE.SYS_POWER][2:],
                         ("OFF", False, "NO IDLE OFF: NOTHING HERE RESTARTS IT"))
        self.app.on_encoder(1, 1)
        self.assertEqual(self.app.settings.get("system", "power_off"), 10)
        # every other board, and no power service at all, keeps the minutes
        for power in ({"percent": 80, "halt_only": False}, None):
            self.app.power = power
            rows = {r[0]: r for r in self.rows()}
            self.assertEqual(rows[FILEPAGE.SYS_POWER][2:],
                             ("10 MIN", True,
                              "IDLE TIMEOUT, HONOURED BY THE DEVICE"))

    # -- § 6.5: the list is a different length with a sensor fitted (#116) --
    def fit_sensor(self, present=True):
        """What the host reports over `hello` / `state`, forced.

        No laptop and no CM4 on the bench has an LSM6DSO, so the fitted case
        is reachable only by writing the flag the protocol carries. What is
        proven here is the UI's half; the host's half is `host_protocol.py`
        and the sensor's is `test_imu.cpp`.
        """
        self.app.native.motion_sensor = present

    def test_key_vel_appears_with_a_sensor(self):
        self.fit_sensor()
        self.assertEqual(self.labels(),
                         ["MIDI VEL CURVE", "KEY VEL", "POWER-OFF",
                          "DATE / TIME", FILEPAGE.TAPE_ROW, "BRIGHTNESS",
                          "WIFI", "FORGET PHONES", "RESET"])
        rows = {r[0]: r for r in self.rows()}
        # named, dim and locked with the reason, because the inference itself
        # is not built (#117) -- not an option that can be set and do nothing
        self.assertEqual(rows[FILEPAGE.SYS_KEY_VEL][2], "FIXED")
        self.assertFalse(rows[FILEPAGE.SYS_KEY_VEL][3])
        self.assertEqual(rows[FILEPAGE.SYS_KEY_VEL][4],
                         FILEPAGE.KEY_VEL_UNBUILT)
        # and the sentence that stood in for the row is gone with it
        self.assertEqual(rows[FILEPAGE.SYS_VEL][4], "")

    def test_the_missing_row_is_said_without_a_sensor(self):
        rows = {r[0]: r for r in self.rows()}
        self.assertEqual(rows[FILEPAGE.SYS_VEL][4], FILEPAGE.NO_IMU)

    def test_the_cursor_is_a_row_and_not_an_index(self):
        """A sensor that stops answering must not move the cursor's meaning."""
        self.fit_sensor()
        self.goto(FILEPAGE.SYS_BRIGHT)
        self.fit_sensor(False)
        self.assertEqual(self.page.system_row, FILEPAGE.SYS_BRIGHT)
        self.assertEqual(self.values()[0], "BRIGHTNESS")

    def test_the_cursor_leaves_a_row_that_went_away(self):
        self.fit_sensor()
        self.goto(FILEPAGE.SYS_KEY_VEL)
        self.fit_sensor(False)
        self.assertEqual(self.values()[0], "MIDI VEL CURVE")
        self.assertEqual(self.page.system_row, FILEPAGE.SYS_VEL)

    def test_key_vel_is_inert_and_draws(self):
        self.fit_sensor()
        self.goto(FILEPAGE.SYS_KEY_VEL)
        before = json.dumps(self.app.settings.data, sort_keys=True)
        self.app.on_encoder(1, 3)
        self.assertEqual(json.dumps(self.app.settings.data, sort_keys=True),
                         before)
        self.assertTrue(self.page.slots()[1].locked)
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        self.assertTrue(fb.any())

    def test_an_old_snapshot_carries_an_index(self):
        self.page.restore({"subpage": FILEPAGE.SYSTEM, "system_row": 4})
        self.assertEqual(self.page.system_row, FILEPAGE.SYS_WIFI)
        self.page.restore({"subpage": FILEPAGE.SYSTEM,
                           "system_row": FILEPAGE.SYS_RESET})
        self.assertEqual(self.page.system_row, FILEPAGE.SYS_RESET)

    def test_a_read_out_row_is_inert(self):
        self.goto(FILEPAGE.SYS_WIFI)
        before = json.dumps(self.app.settings.data, sort_keys=True)
        self.app.on_encoder(1, 5)
        self.assertEqual(json.dumps(self.app.settings.data, sort_keys=True),
                         before)
        self.assertTrue(self.page.slots()[1].locked)

    def test_reset_asks_twice(self):
        """DDR-064: 1-2-3-4, then again."""
        self.app.settings.set("system", "brightness", 30)
        self.app.bpm = 96.0
        self.goto(FILEPAGE.SYS_RESET)
        self.app.on_encoder(1, 1)                     # arm
        self.assertEqual(self.page._armed, "RESET")
        self.assertEqual(self.page.strip_labels(), ["CONFIRM"] * 4)

        self.confirm()                                # one full run…
        self.assertEqual(self.page._armed, "RESET")   # …and it is still armed
        self.assertEqual(self.app.settings.get("system", "brightness"), 30)
        self.assertIn("AGAIN", " ".join(t[1] for t in self.app.tickers()))

        self.confirm()                                # the second run does it
        self.assertIsNone(self.page._armed)
        self.assertEqual(self.app.settings.get("system", "brightness"), 80)
        self.assertEqual(self.app.bpm, PF.BOOT_TEMPO["bpm"])

    def test_a_wrong_key_cancels_either_round(self):
        for pre in ((), CONFIRM):
            self.app.on_encoder(0, 0)
            self.page.system_row = FILEPAGE.SYS_RESET
            self.app.on_encoder(1, 1)
            self.confirm(pre)
            self.press("1")
            self.press("3")
            self.assertIsNone(self.page._armed)

    def test_moving_the_cursor_puts_the_gun_down(self):
        self.goto(FILEPAGE.SYS_RESET)
        self.app.on_encoder(1, 1)
        self.assertEqual(self.page._armed, "RESET")
        self.app.on_encoder(0, -1)
        self.assertIsNone(self.page._armed)

    def test_it_draws_armed_and_not(self):
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        self.goto(FILEPAGE.SYS_RESET)
        self.app.on_encoder(1, 1)
        self.app.render(fb)
        self.assertGreater(int(fb.sum()), 0)


# ---------------------------------------------------------------------------
# the browser as a file manager (DDR-055)
# ---------------------------------------------------------------------------

class TestBrowserFileOps(FileCase):
    def open_synth_browser(self):
        """SHIFT+1 on SYNTH — the engine browser, which is every browser."""
        self.app.goto("SYNTH")
        self.app.on_key("SHIFT", True)
        self.app.on_key("1", True)
        self.app.on_key("1", False)
        self.app.on_key("SHIFT", False)
        self.assertIsNotNone(self.app.menu)
        return self.app.menu

    def go_to(self, menu, folder: str, item: str = ""):
        folders = menu.source.folders()
        menu.state.folder_idx = folders.index(folder)
        menu.state.item_idx = 0
        if item:
            menu.state.item_idx = menu.source.items(
                menu.state.folder_idx).index(item)
        return menu

    def user_patch(self, folder="osc", name="mine.patch"):
        """Something the *player* made — stock content does not lift."""
        path = os.path.join(LIB, "synth", folder, name)
        with open(path, "w") as f:
            json.dump({"format": 1, "kind": "patch", "name": "MINE",
                       "engine": {"id": "arbutus/osc", "params": {}}}, f)
        self.app.library.rescan()
        return path

    # -- lift -------------------------------------------------------------
    def test_lift_moves_the_file_to_trash(self):
        path = self.user_patch()
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        self.press("LIFT")

        self.assertFalse(os.path.exists(path))
        self.assertIsNotNone(self.app.files.lifted)
        trashed = os.path.join(LIB, OPS.TRASH_DIR, "mine.patch")
        self.assertTrue(os.path.exists(trashed))
        # and the browser no longer offers it
        self.assertNotIn("mine", menu.source.items(menu.state.folder_idx))
        self.assertIn("LIFTED", " ".join(t[1] for t in self.app.tickers()))

    def test_stock_content_does_not_lift(self):
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "init")
        self.press("LIFT")
        self.assertIsNone(self.app.files.lifted)
        self.assertTrue(os.path.exists(os.path.join(LIB, "synth", "osc",
                                                    "init.patch")))
        self.assertIn("STOCK", " ".join(t[1] for t in self.app.tickers()))

    def test_drop_moves_it_into_the_highlighted_folder(self):
        self.user_patch()
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        self.press("LIFT")
        self.go_to(menu, "FM")
        self.press("DROP")

        self.assertIsNone(self.app.files.lifted)
        moved = os.path.join(LIB, "synth", "fm", "mine.patch")
        self.assertTrue(os.path.exists(moved))
        self.assertIn("mine", menu.source.items(menu.state.folder_idx))

    def test_shift_drop_makes_a_folder(self):
        self.user_patch()
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        self.press("LIFT")
        self.press("DROP", shift=True)
        made = os.path.join(LIB, "synth", "osc", "FOLDER-01")
        self.assertTrue(os.path.isdir(made))
        self.assertTrue(os.path.exists(os.path.join(made, "mine.patch")))

    def test_consecutive_lifts_undo_as_one_group(self):
        """DDR-055: "lift, lift, oops" must not cost the first one."""
        a = self.user_patch(name="one.patch")
        b = self.user_patch(name="two.patch")
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "one")
        self.press("LIFT")
        self.go_to(menu, "OSC", "two")
        self.press("LIFT")
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(b))
        self.assertEqual(len(self.app.files.undo_stack), 1)

        self.press("STOP", shift=True)
        self.assertTrue(os.path.exists(a))
        self.assertTrue(os.path.exists(b))
        self.assertIsNone(self.app.files.lifted)

    def test_a_drop_closes_the_group(self):
        a = self.user_patch(name="one.patch")
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "one")
        self.press("LIFT")
        self.go_to(menu, "FM")
        self.press("DROP")
        self.go_to(menu, "OSC", "init")             # stock: nothing happens
        self.assertEqual(len(self.app.files.undo_stack), 1)

        self.press("STOP", shift=True)              # undoes lift *and* drop
        self.assertTrue(os.path.exists(a))
        self.assertFalse(os.path.exists(os.path.join(LIB, "synth", "fm",
                                                     "one.patch")))

    # -- the move is the host's (#62 item 3) -------------------------------
    def spy(self):
        """Every message the browser sends while the spy is installed."""
        sent = []
        real = self.native.send
        self.native.send = lambda m: (sent.append(m), real(m))[1]
        self.addCleanup(lambda: setattr(self.native, "send", real))
        return sent

    def test_the_move_is_a_library_op_and_not_a_second_shutil(self):
        """#62 item 3: the browser asks the host to move the file.

        `rescan` is deliberately *not* sent afterwards — every `library {op}`
        re-reads the roots before it replies, so a second one would scan a
        library nothing had changed since.
        """
        self.user_patch()
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        sent = self.spy()
        self.press("LIFT")
        self.assertEqual([m for m in sent if m.get("op")],
                         [{"type": "library", "op": "trash",
                           "path": "synth/osc/mine.patch"}])
        self.assertNotIn("rescan", [m.get("type") for m in sent])

    def test_a_drop_is_the_hosts_move(self):
        self.user_patch()
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        self.press("LIFT")
        self.go_to(menu, "FM")
        sent = self.spy()
        self.press("DROP")
        self.assertEqual([m for m in sent if m.get("op")],
                         [{"type": "library", "op": "move",
                           "path": ".trash/mine.patch", "to": "synth/fm"}])

    def test_a_host_that_refuses_moves_nothing(self):
        """§ 4.2's reconnect window, and every other refusal: the browser has
        no second implementation to fall back on, and that is the point — it
        says so and the file stays where it is."""
        path = self.user_patch()
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        real = self.native.send

        def refuse(msg):
            if msg.get("type") == "library" and msg.get("op"):
                return {"type": "error", "code": "disconnected",
                        "message": "host went away"}
            return real(msg)

        self.native.send = refuse
        self.addCleanup(lambda: setattr(self.native, "send", real))
        self.press("LIFT")
        self.assertTrue(os.path.exists(path))
        self.assertIsNone(self.app.files.lifted)
        self.assertIn("CANNOT LIFT",
                      " ".join(t[1] for t in self.app.tickers()))

    def test_a_drop_onto_a_taken_name_renames_rather_than_refusing(self):
        """The host's `move` refuses `EXISTS` — its caller named the
        destination. A DROP names a *folder*, so the browser frees a name
        with the host's `rename` first and the item keeps travelling."""
        self.user_patch(folder="fm", name="mine.patch")
        self.user_patch(folder="osc", name="mine.patch")
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        self.press("LIFT")
        self.go_to(menu, "FM")
        sent = self.spy()
        self.press("DROP")
        self.assertIsNone(self.app.files.lifted)
        self.assertEqual([m["op"] for m in sent if m.get("op")],
                         ["move", "rename", "move"])
        self.assertTrue(os.path.exists(os.path.join(LIB, "synth", "fm",
                                                    "mine-2.patch")))
        self.assertTrue(os.path.exists(os.path.join(LIB, "synth", "fm",
                                                    "mine.patch")))

    def test_the_renamed_drop_still_undoes_as_one_group(self):
        """The rename is a step of the group like any other, so SHIFT+STOP
        walks it back: out of `fm/`, back to `mine.patch`, back to `osc/`."""
        self.user_patch(folder="fm", name="mine.patch")
        origin = self.user_patch(folder="osc", name="mine.patch")
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        self.press("LIFT")
        self.go_to(menu, "FM")
        self.press("DROP")
        self.press("STOP", shift=True)
        self.assertTrue(os.path.exists(origin))
        self.assertFalse(os.path.exists(os.path.join(LIB, "synth", "fm",
                                                     "mine-2.patch")))
        self.assertTrue(os.path.exists(os.path.join(LIB, "synth", "fm",
                                                    "mine.patch")))

    def test_a_drum_kit_is_a_folder_and_moves_whole(self):
        kit = os.path.join(LIB, "drum", "drum", "MYKIT")
        os.makedirs(kit)
        with open(os.path.join(kit, "kit.json"), "w") as f:
            json.dump({"format": 1, "kind": "kit", "pads": {}}, f)
        self.app.library.rescan()

        self.app.goto("DRUM")
        self.app.on_key("SHIFT", True)
        self.app.on_key("1", True)
        self.app.on_key("1", False)
        self.app.on_key("SHIFT", False)
        menu = self.app.menu
        self.go_to(menu, "DRUM", "MYKIT")
        self.press("LIFT")
        self.assertFalse(os.path.exists(kit))
        self.assertTrue(os.path.exists(os.path.join(LIB, OPS.TRASH_DIR,
                                                    "MYKIT", "kit.json")))

    def test_the_open_project_will_not_lift(self):
        """#297: the open project's folder is where the tape is being
        written, so the host refuses to move it — the browser says why."""
        folder = PF.save(self.app)
        self.app.goto("FILE")
        self.app.on_encoder(1, 1)                   # arm LOAD: the browser
        self.assertIsNotNone(self.app.menu)
        self.press("LIFT")
        self.assertTrue(os.path.isdir(folder))
        self.assertEqual(self.app.native.errors[-1], "OPEN ON THE INSTRUMENT")

    def test_a_project_is_a_folder_too(self):
        """J1 left this: `projects/` entries are folders with media in them."""
        folder = PF.save(self.app)
        name = os.path.basename(folder)
        new = PF.new(self.app)                      # not open any more (#297)
        self.app.goto("FILE")
        self.app.on_encoder(1, 1)                   # arm LOAD: the browser
        menu = self.app.menu
        self.assertIsNotNone(menu)
        self.press("LIFT")
        self.assertFalse(os.path.exists(folder))
        self.assertTrue(os.path.exists(os.path.join(LIB, OPS.TRASH_DIR, name)))
        # only NEW's own project is left (D97: its folder exists at once)
        self.assertEqual(PF.list_projects(self.app),
                         {PF.date_folder(): [os.path.basename(new)]})

    def test_the_trash_is_not_browsable(self):
        self.user_patch()
        menu = self.open_synth_browser()
        self.go_to(menu, "OSC", "mine")
        self.press("LIFT")
        self.assertNotIn(OPS.TRASH_DIR,
                         [f.folder for f in self.app.library.list("synth")])
        # nor does the host offer anything out of it
        self.assertNotIn(OPS.TRASH_DIR,
                         json.dumps(self.native.library()))


# ---------------------------------------------------------------------------
# FILE's three device-OS cells: PAIR, WIFI, FORGET PHONES (D3/D4's UI half, #97)
# ---------------------------------------------------------------------------

def net_status(**over):
    """What `arbutus_os/net_service.py`'s `status()` answers with.

    Written out here rather than imported, because `emulator/` never imports
    `arbutus_os/` — that is the whole reason the device runtime hands this
    page a plain dictionary. `arbutus_os/tests/test_net_pump.py` is the other
    end of the seam and asserts the two agree on the one state name.
    """
    doc = {"type": "status", "id": "arbutus-1f2e", "name": "arbutus-1f2e",
           "paired": False, "serving": False, "storage_export": False,
           "wifi": "off", "ssid": "", "ip": "", "saved": [],
           "pairing": False, "pair_state": "idle", "code": "",
           "attempts_left": 3, "ble": True, "pair_error": ""}
    doc.update(over)
    return doc


class NetCase(FileCase):
    """A device runtime's half, faked: `NetPump` sets exactly these two."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.app.net_command = self.sent.append

    def net(self, **over):
        self.app.network = net_status(**over)
        return self.app.network

    def draw(self):
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        return fb


class TestNoNetSocket(FileCase):
    """Item 4 of the ticket, and § 4.3's rule: on a laptop nothing changes.

    `NetPump` never ran, so `app.network` does not exist at all and every one
    of the three cells is stage J2's — which is what the other classes in this
    file have been asserting all along, said once here on purpose.
    """

    def test_the_page_has_no_network_document(self):
        self.assertIsNone(self.page.network)
        self.assertFalse(hasattr(self.app, "network"))

    def test_pair_is_locked_and_says_why(self):
        self.subpage(FILEPAGE.USB)
        self.assertTrue(self.page.slots()[2].locked)
        self.assertEqual(self.page._pair_row(),
                         (FILEPAGE.NO_BLE, palette.DIM))
        self.assertEqual(self.page._pair_code(), "")

    def test_turning_pair_does_nothing_at_all(self):
        self.subpage(FILEPAGE.USB)
        self.app.on_encoder(2, 1)
        self.assertEqual(self.page.slots()[2].value, 0.0)

    def test_the_two_system_rows_are_j2s(self):
        self.subpage(FILEPAGE.SYSTEM)
        rows = {r[0]: r for r in self.page._system_rows()}
        self.assertEqual(rows[FILEPAGE.SYS_WIFI][2], FILEPAGE.NO_WIFI)
        self.assertFalse(rows[FILEPAGE.SYS_WIFI][3])
        self.assertEqual(rows[FILEPAGE.SYS_PHONES][2], "\u2014")
        self.assertFalse(rows[FILEPAGE.SYS_PHONES][3])
        self.assertEqual(rows[FILEPAGE.SYS_PHONES][4], FILEPAGE.NO_BLE)

    def test_forget_phones_cannot_be_armed_from_the_row(self):
        self.subpage(FILEPAGE.SYSTEM)
        rows = [r[0] for r in self.page._system_rows()]
        self.app.on_encoder(0, rows.index(FILEPAGE.SYS_PHONES))
        self.app.on_encoder(1, 1)
        self.assertIsNone(self.page._armed)


class TestUsbAndPowerRows(FileCase):
    """#524: STORAGE and CHARGE read `usb.sock` and `power.sock` where they
    answer, and keep stage J2's laptop sentences where they do not.

    `UsbPump` and `PowerPump` set `app.usb_status` and `app.power`; here they
    are set by hand, and the rows are read off what `_render_usb` draws.
    """

    def drawn(self):
        self.subpage(FILEPAGE.USB)
        seen = []
        self.page._rows = lambda fb, rect, rows, top=6: seen.extend(rows)
        self.app.render(np.zeros((240, 320, 3), dtype=np.uint8))
        return {label: (value, colour) for label, value, colour in seen}

    def test_a_laptop_draws_as_j2_did(self):
        rows = self.drawn()
        self.assertFalse(hasattr(self.app, "usb_status"))
        self.assertEqual(rows["STORAGE"], (FILEPAGE.NO_GADGET, palette.DIM))
        self.assertEqual(rows["CHARGE"], (FILEPAGE.NO_CHARGE, palette.DIM))

    def test_storage_reads_the_usb_service(self):
        self.app.usb_status = {"mode": "off", "exported": False}
        self.assertEqual(self.drawn()["STORAGE"],
                         (FILEPAGE.STORAGE_READY, palette.INK))
        self.app.usb_status = {"mode": "storage", "exported": True}
        self.assertEqual(self.drawn()["STORAGE"],
                         (FILEPAGE.STORAGE_EXPORTED, palette.GREEN))

    def test_storage_says_the_services_refusal_verbatim(self):
        self.app.usb_status = {"mode": "off", "exported": False,
                               "storage_blocked": "NO /data PARTITION"}
        self.assertEqual(self.drawn()["STORAGE"],
                         ("NO /data PARTITION", palette.DIM))

    def test_charge_reads_the_power_document(self):
        self.app.power = {"source": "device", "charging": True}
        self.assertEqual(self.drawn()["CHARGE"],
                         (FILEPAGE.CHARGING, palette.GREEN))
        self.assertEqual(self.page.slots()[1].value, 1.0)
        self.app.power = {"source": "device", "charging": False}
        self.assertEqual(self.drawn()["CHARGE"],
                         (FILEPAGE.NOT_CHARGING, palette.INK))
        self.assertEqual(self.page.slots()[1].value, 0.0)
        self.assertTrue(self.page.slots()[1].locked)

    def test_a_board_with_no_charge_line_says_so(self):
        """`charging` is `false` there too; `charge_stat` says it is a guess."""
        self.app.power = {"source": "none", "charging": False,
                          "charge_stat": "unknown"}
        self.assertEqual(self.drawn()["CHARGE"],
                         (FILEPAGE.CHARGE_UNKNOWN, palette.DIM))


class TestPairCell(NetCase):
    """§ 9.5's flow as the player sees it, one line of the page at a time."""

    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.USB)

    def test_it_unlocks_when_net_sock_answers(self):
        self.assertTrue(self.page.slots()[2].locked)
        self.net()
        self.assertFalse(self.page.slots()[2].locked)
        self.assertEqual(self.page._pair_row(),
                         (FILEPAGE.PAIR_OFF, palette.DIM))

    def test_a_device_with_no_adapter_is_locked_and_says_which(self):
        """Every CM4 on the bench (E7): `arbutus-net` is up, the radio is not.
        `NO BLUETOOTH ON THIS DEVICE` is not the same fact as J2's sentence
        and the page must not print one for the other."""
        self.net(ble=False)
        self.assertTrue(self.page.slots()[2].locked)
        self.assertEqual(self.page._pair_row(),
                         (FILEPAGE.NO_ADAPTER, palette.DIM))

    def test_turning_it_opens_the_window(self):
        self.net()
        self.app.on_encoder(2, 1)
        self.assertEqual(self.sent, [{"type": "pair", "on": True}])
        # the cell lights on the detent, not on the service's answer
        self.assertEqual(self.values()[2], "ON")

    def test_a_second_detent_does_not_mint_a_second_code(self):
        """`PairingService.begin()` makes a fresh code every call, so a
        repeated turn would change the number under the player's fingers."""
        self.net()
        self.app.on_encoder(2, 1)
        self.app.on_encoder(2, 1)
        self.app.on_encoder(2, 1)
        self.assertEqual(self.sent, [{"type": "pair", "on": True}])

    def test_turning_it_back_closes_the_window(self):
        self.net(pairing=True, pair_state="advertising", code="0439")
        self.app.on_encoder(2, -1)
        self.assertEqual(self.sent, [{"type": "pair", "on": False}])

    def test_the_code_is_on_the_page(self):
        self.net(pairing=True, pair_state="advertising", code="0439")
        self.assertEqual(self.page._pair_row(), ("0439", palette.GREEN))
        self.assertEqual(self.page._pair_code(), "0439")
        self.assertTrue(self.draw().any())

    def test_the_code_stops_being_drawn_once_the_phone_has_it_right(self):
        """`code_ok` means the three writes are in flight; repeating the code
        then would only invite a retype."""
        self.net(pairing=True, pair_state="code_ok", code="0439")
        self.assertEqual(self.page._pair_row(),
                         (FILEPAGE.PAIR_ACCEPTED, palette.GREEN))
        self.assertEqual(self.page._pair_code(), "")

    def test_a_closed_window_takes_the_code_with_it(self):
        """Three minutes, and the service closes it by itself: a code still on
        the glass is one a player types into a phone that will refuse it."""
        self.net(pairing=True, pair_state="advertising", code="0439")
        self.assertEqual(self.page._pair_code(), "0439")
        self.net(pairing=False, pair_state="idle", code="")
        self.assertEqual(self.page._pair_code(), "")
        self.assertEqual(self.page._pair_row(),
                         (FILEPAGE.PAIR_OFF, palette.DIM))

    def test_a_paired_instrument_says_so(self):
        self.net(paired=True)
        self.assertEqual(self.page._pair_row(),
                         (FILEPAGE.PAIR_PAIRED, palette.INK))

    def test_the_code_takes_the_foot_of_the_page_from_the_fw_line(self):
        """Both want the one sentence at the bottom. The pairing window is
        three minutes and is what the player is doing; the `.fw` line is
        still there afterwards (§ 10.8)."""
        self.app.firmware = {"type": "status", "slot": "a", "other_slot": "b",
                             "version": "1.0.0", "message": "UPDATE READY",
                             "inbox": [], "pending": "b"}
        self.net(pairing=True, pair_state="advertising", code="0439")
        self.assertEqual(self.page._fw_message(), "UPDATE READY")
        self.assertEqual(self.page._pair_code(), "0439")
        self.assertTrue(self.draw().any())

    def test_the_other_cells_are_untouched(self):
        self.net()
        self.assertEqual(self.names(),
                         ["MODE", "CHARGE", "PAIR", FILEPAGE.FW_UPDATE,
                          None, None, None, None])
        self.assertTrue(self.page.slots()[1].locked)     # CHARGE, still J2's
        self.assertTrue(self.page.slots()[3].locked)     # no fw.sock


class TestWifiRow(NetCase):
    """§ 9.2: `OFF · JOINING · <SSID> <ip> · FAILED`, off the same status."""

    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.SYSTEM)

    def row(self):
        return {r[0]: r for r in self.page._system_rows()}[FILEPAGE.SYS_WIFI]

    def test_the_four_states(self):
        self.net(wifi="off")
        self.assertEqual(self.row()[2], "OFF")
        self.net(wifi="joining")
        self.assertEqual(self.row()[2], "JOINING")
        self.net(wifi="failed")
        self.assertEqual(self.row()[2], "FAILED")
        self.net(wifi="up", ssid="hut", ip="10.0.0.9")
        self.assertEqual(self.row()[2], "hut 10.0.0.9")

    def test_up_with_no_address_yet_does_not_draw_a_gap(self):
        self.net(wifi="up", ssid="hut", ip="")
        self.assertEqual(self.row()[2], "hut")

    def test_it_stays_a_read_out(self):
        """The join is the phone's (§ 9.5 hands the credentials over); this
        row reports and does not set."""
        self.net(wifi="up", ssid="hut", ip="10.0.0.9")
        rows = [r[0] for r in self.page._system_rows()]
        self.app.on_encoder(0, rows.index(FILEPAGE.SYS_WIFI))
        self.assertFalse(self.row()[3])
        self.app.on_encoder(1, 3)
        self.assertIsNone(self.page._armed)
        self.assertTrue(self.draw().any())


class TestForgetPhones(NetCase):
    def setUp(self):
        super().setUp()
        self.subpage(FILEPAGE.SYSTEM)

    def row(self):
        return {r[0]: r for r in self.page._system_rows()}[FILEPAGE.SYS_PHONES]

    def goto(self):
        rows = [r[0] for r in self.page._system_rows()]
        self.app.on_encoder(0, rows.index(FILEPAGE.SYS_PHONES)
                            - self.page._row_index(self.page._system_rows()))

    def test_it_is_editable_once_net_sock_answers(self):
        self.assertFalse(self.row()[3])
        self.net()
        self.assertTrue(self.row()[3])
        self.assertEqual(self.row()[2], "TURN RIGHT")
        self.assertEqual(self.row()[4], FILEPAGE.FORGET_NONE)

    def test_a_paired_instrument_says_what_it_is_about_to_lose(self):
        self.net(paired=True)
        self.assertEqual(self.row()[4], FILEPAGE.FORGET_PAIRED)

    def test_it_asks_once_and_then_sends(self):
        """One round of 1-2-3-4, not RESET's two: DDR-064's second round is
        for the gesture that throws away more than a session, and a forgotten
        phone is re-paired by standing next to the instrument."""
        self.net(paired=True)
        self.goto()
        self.app.on_encoder(1, 1)
        self.assertEqual(self.page._armed, FILEPAGE.FORGET)
        self.assertEqual(self.page._rounds, 1)
        self.assertEqual(self.page.strip_labels(), ["CONFIRM"] * 4)
        self.assertEqual(self.page._armed_line(), FILEPAGE.FORGET_ARMED)
        self.assertTrue(self.draw().any())
        self.confirm()
        self.assertEqual(self.sent, [{"type": "forget_phones"}])
        self.assertIsNone(self.page._armed)

    def test_a_wrong_key_cancels_it(self):
        self.net()
        self.goto()
        self.app.on_encoder(1, 1)
        self.confirm(("1", "2", "4"))
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.page._armed)

    def test_turning_it_back_puts_the_gun_down(self):
        self.net()
        self.goto()
        self.app.on_encoder(1, 1)
        self.app.on_encoder(1, -1)
        self.assertIsNone(self.page._armed)

    def test_moving_the_cursor_puts_it_down_too(self):
        self.net()
        self.goto()
        self.app.on_encoder(1, 1)
        self.app.on_encoder(0, -1)
        self.assertIsNone(self.page._armed)
        self.confirm()
        self.assertEqual(self.sent, [])

    def test_it_does_not_touch_the_settings_or_the_project(self):
        """`forget_phones` is the service's act entirely — the UI sends one
        message and owns none of the consequences (§ 9.4.1)."""
        self.net(paired=True)
        before = json.dumps(self.app.settings.data, sort_keys=True)
        self.goto()
        self.app.on_encoder(1, 1)
        self.confirm()
        self.assertEqual(json.dumps(self.app.settings.data, sort_keys=True),
                         before)

    def test_with_no_door_it_says_so_rather_than_pretending(self):
        """`app.network` without `app.net_command` cannot happen on a device —
        `NetPump` sets both — but the page is the same code on a laptop and
        every device-only door in it is read with `getattr`."""
        self.net()
        del self.app.net_command
        self.goto()
        self.app.on_encoder(1, 1)
        self.confirm()
        self.assertIsNone(self.page._armed)


class TestStockManifest(unittest.TestCase):
    def test_every_listed_item_exists(self):
        """`stock.json` is committed; a stale entry means a lift silently
        refuses on something that is gone."""
        root = os.path.join(os.path.dirname(HERE), "audio", "library")
        stock = OPS.read_stock(root)
        self.assertGreater(len(stock), 20)
        missing = [p for p in stock if not os.path.exists(
            os.path.join(root, *p.split("/")))]
        self.assertEqual(missing, [])

    def test_every_committed_item_is_listed(self):
        """The other direction (#626): a stock item committed without re-running
        `audio/tools/mkstock.py` is not in `stock.json`, so the browser treats it
        as the player's own and a LIFT takes it (DDR-055). Only items git tracks
        count: content the player made in this checkout's library (a mic take,
        a promoted patch) is untracked and is meant to be absent."""
        import subprocess
        repo = os.path.dirname(HERE)
        root = os.path.join(repo, "audio", "library")
        try:
            out = subprocess.run(["git", "-C", repo, "ls-files", "-z", "audio/library"],
                                 capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        tracked = {p[len("audio/library/"):] for p in out.split("\0") if p}
        sys.path.insert(0, os.path.join(repo, "audio", "tools"))
        try:
            import mkstock
        finally:
            sys.path.pop(0)
        # A kit is a folder item: tracked when any file under it is.
        committed = [i for i in mkstock.scan(root)
                     if i in tracked or any(t.startswith(i + "/") for t in tracked)]
        self.assertGreater(len(committed), 20)
        stock = set(OPS.read_stock(root))
        self.assertEqual([i for i in committed if i not in stock], [],
                         "run audio/tools/mkstock.py and commit stock.json")

    def test_it_names_the_stock_patches_and_not_a_new_one(self):
        root = os.path.join(os.path.dirname(HERE), "audio", "library")
        self.assertTrue(OPS.is_stock(root,
                                     os.path.join(root, "synth", "osc",
                                                  "init.patch")))
        self.assertFalse(OPS.is_stock(root,
                                      os.path.join(root, "synth", "osc",
                                                   "never-written.patch")))


# ---------------------------------------------------------------------------
# battery
# ---------------------------------------------------------------------------

class TestBattery(FileCase):
    def test_no_telemetry_says_so(self):
        self.assertEqual(battery.line(reading=None, sentinel=True),
                         "BATTERY — NO TELEMETRY ON THIS HOST")

    def test_a_reading_is_a_percentage(self):
        self.assertEqual(battery.line({"percent": 84, "charging": False}),
                         "BATTERY 84%")
        self.assertEqual(battery.line({"percent": 84, "charging": True}),
                         "BATTERY 84% (CHARGING)")

    def test_the_low_ticker_threshold(self):
        self.assertIsNone(battery.low_ticker({"percent": 15,
                                              "charging": False}))
        self.assertIsNone(battery.low_ticker({"percent": 9, "charging": True}))
        self.assertEqual(battery.low_ticker({"percent": 12,
                                             "charging": False})[0],
                         "BATT 12%")
        self.assertIsNone(battery.low_ticker({}))

    def test_help_hold_shows_it(self):
        self.app.on_key("HELP", True)
        self.assertIn("BATTERY", " ".join(self.app.help_lines()))
        self.assertLessEqual(len(self.app.help_lines()), 9)
        self.app.on_key("HELP", False)

    def test_the_strip_carries_a_low_battery(self):
        battery._cache = {"percent": 7, "charging": False}
        battery._cache_at = 1e18                       # never expires
        try:
            self.assertIn("BATT 7%",
                          " ".join(t[1] for t in self.app.tickers()))
        finally:
            battery.reset_cache()

    def test_the_platform_read_never_raises(self):
        battery.reset_cache()
        r = battery.read(force=True)
        self.assertTrue(r is None or 0 <= r["percent"] <= 100)


if __name__ == "__main__":
    unittest.main()
