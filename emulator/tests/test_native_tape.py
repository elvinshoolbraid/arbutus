"""Stage N1: the things only the Python path used to do, done natively.

The click, the mic landing on SYNTH, TRACK's clip waveforms, the tape keys
(loop / SPEED / REV / FILTER), the mixer and the tape across a restart. Every
one is driven through the **input seam** — `App.on_key` / `App.on_encoder` —
because the question is whether a player's gestures reach the audio process.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.app import App, SAMPLE_RATE                       # noqa: E402
from app.keys import note_key                              # noqa: E402
from app import tape as T                                  # noqa: E402
from testing.fake_mic import FakeMic                        # noqa: E402
from testing.headless_display import HeadlessDisplay        # noqa: E402
from testing.scripted_input import ScriptedInput            # noqa: E402

from tests.native_support import EVIDENCE, library_copy, native_or_skip  # noqa: E402

BLOCK = 256


class TapeCase(unittest.TestCase):
    """One host, one App, a scratch copy of `audio/library` and a fake mic.

    The library is copied because INPUT writes mic captures into it: a test
    must never drop a WAV in the repo's library.
    """

    def setUp(self):
        from app.native_library import NativeLibrary
        from drivers.native_backend import NativeOfflineAudio

        self.tmp = tempfile.mkdtemp(prefix="arbutus-native-tape-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib_root = os.path.join(self.tmp, "library")
        library_copy(self.lib_root)
        self.state = os.path.join(self.tmp, "state")

        self.native = native_or_skip(self, offline=True, block=BLOCK,
                                     libraries=[os.path.join(REPO, "audio", "build", "boxes"),
                                                self.lib_root])
        self.addCleanup(self.native.close)
        self.app = self.make_app()
        self.fake = FakeMic()
        self.app.mic_driver_factory = lambda: self.fake
        self.input = ScriptedInput()
        self.input.initialize()
        self.input.on_control = self.app.on_key
        self.input.on_encoder_turn = self.app.on_encoder
        self.display = HeadlessDisplay()
        self.display.initialize()
        self._fb = np.zeros((240, 320, 3), dtype=np.uint8)

    def make_app(self, fresh=True):
        from app.native_library import NativeLibrary
        from drivers.native_backend import NativeOfflineAudio

        app = App(sample_rate=SAMPLE_RATE, fresh=fresh, state_dir=self.state,
                  native=self.native,
                  library=NativeLibrary(self.native, root=self.lib_root))
        self.audio = NativeOfflineAudio(self.native, blocksize=BLOCK)
        self.audio.initialize(SAMPLE_RATE, 2, None)
        self.audio.start()
        return app

    # -- the seam ----------------------------------------------------------
    def key(self, name, shift=False, down=None):
        name = str(getattr(name, "value", name))
        evs = []
        if shift:
            evs.append(("key", "SHIFT", True))
        if down is None:
            evs += [("key", name, True), ("key", name, False)]
        else:
            evs.append(("key", name, bool(down)))
        if shift:
            evs.append(("key", "SHIFT", False))
        self.input.feed(*evs)
        self.input.poll_events()

    def note(self, idx, down):
        self.key(note_key(idx), down=down)

    def enc(self, idx, delta):
        self.input.feed(("enc", idx, delta))
        self.input.poll_events()

    def run_blocks(self, n):
        block = self.audio.pull(n)
        self.app.tick()
        return block

    def peak(self, block):
        return float(np.max(np.abs(block))) if len(block) else 0.0

    def shot(self, name):
        self._fb[:] = 0
        self.app.render(self._fb)
        self.display.update(self._fb)
        return self.display.save_png(os.path.join(EVIDENCE, name + ".png"))

    def record_take(self, blocks=24, idx=7):
        """REC + a note: a grid-locked take of the default engine."""
        self.key("TRACK")
        self.key("REC", down=True)
        self.note(idx, True)
        block = self.run_blocks(blocks)
        self.note(idx, False)
        self.key("REC", down=False)
        self.run_blocks(2)
        self.key("STOP")
        self.run_blocks(2)
        return block


class TestClick(TapeCase):
    def test_click_sounds_and_the_level_turns_it_off(self):
        self.key("TEMPO")
        self.run_blocks(1)
        self.enc(1, 10)                     # ENC2 CLICK LVL, 0 -> 0.5
        self.assertGreater(self.app.metronome.level, 0.4)
        self.key("PLAY")
        loud = self.run_blocks(200)         # a beat at 120 bpm is 24000 frames
        self.assertGreater(self.peak(loud), 1e-2)

        self.enc(1, -20)                    # CLICK LVL 0 = off
        self.assertEqual(self.app.metronome.level, 0.0)
        self.run_blocks(20)
        quiet = self.run_blocks(200)
        self.assertEqual(self.peak(quiet), 0.0)
        self.key("STOP")
        self.shot("06-tempo-click")

    def test_click_pitch_reaches_the_host(self):
        self.key("TEMPO")
        self.enc(2, -40)                    # CLICK PITCH down to 0
        self.run_blocks(1)
        self.assertEqual(self.app.metronome.pitch, 0.0)
        self.assertEqual(self.native.state["metronome"]["pitch"], 0.0)

    def test_the_beat_indicator_reads_the_host_song_position(self):
        self.key("TEMPO")
        self.key("PLAY")
        met = self.app.metronome
        self.assertEqual(met.beat, 0)
        beat = met.beat_samples()
        self.run_blocks(int(beat / BLOCK) + 1)
        self.assertEqual(met.beat, 1)
        self.assertTrue(met.flashing or met.beat_phase >= 0.2)
        self.key("STOP")

    def test_the_click_never_lands_on_the_tape(self):
        self.key("TEMPO")
        self.enc(1, 20)                     # CLICK LVL full
        self.run_blocks(1)
        self.key("TRACK")
        self.key("REC", down=True)
        self.key("REC", down=False)         # arm only: nothing played
        self.key("PLAY")
        self.run_blocks(40)
        self.key("STOP")
        self.run_blocks(2)
        clips = self.app.tape.tracks[0]
        self.assertTrue(clips, "the take did not become a clip")
        c = clips[0]
        got = self.native.clip_peaks(0, c.src, c.length, 16)
        self.assertEqual(max(max(got["max"]), -min(got["min"])), 0.0)


class TestMicOntoSynth(TapeCase):
    def test_a_capture_lands_on_the_native_sampler(self):
        self.key("INPUT")
        self.key("1")                                   # ARM
        self.assertTrue(self.app.mic.armed)
        self.fake.tap()
        self.app.tick()                                 # poll: trim, save, load

        self.assertIsNotNone(self.app.mic.captured)
        path = self.app.mic.captured_path
        self.assertTrue(path.startswith(self.lib_root), path)
        self.assertIn(os.path.join("synth", "sampler"), path)

        synth = self.app.pages["SYNTH"]
        self.assertEqual(synth.instrument.engine_folder.folder, "sampler")
        self.assertTrue(synth.instrument.item.startswith("mic-"))
        slot = self.native.slot("SYNTH", "engine")
        self.assertEqual(slot.get("id"), "arbutus/sampler")
        self.assertEqual(os.path.basename(slot["assets"]["sample"]),
                         os.path.basename(path))

        self.key("SYNTH")
        self.note(7, True)
        block = self.run_blocks(24)
        self.note(7, False)
        self.assertGreater(self.peak(block), 1e-3)
        self.shot("07-mic-sample")

    def test_monitor_reaches_the_host(self):
        self.key("INPUT")
        self.run_blocks(1)
        self.assertFalse(self.native.state["monitor"]["on"])
        self.key("4")                                   # MONITOR
        self.run_blocks(1)
        self.assertTrue(self.app.mic.monitor)
        self.assertTrue(self.native.state["monitor"]["on"])


class TestTrackWaveform(TapeCase):
    def test_a_clip_draws_its_waveform_from_the_host(self):
        block = self.record_take()
        self.assertGreater(self.peak(block), 1e-3)
        page = self.app.pages["TRACK"]
        self.enc(1, 14)                     # ENC2: zoom in until the clip is wide
        clips = self.app.tape.tracks[0]
        self.assertTrue(clips)
        self.shot("08-track-clip")
        self.assertTrue(page._peaks, "TRACK asked the host for no peaks")
        drawn = max(max(abs(v) for v in mx) for _, mx in page._peaks.values())
        self.assertGreater(drawn, 1e-3, "the clip drew as a flat block")

    def test_a_zoomed_out_clip_asks_for_no_more_bins_than_it_draws(self):
        """#163's UI half: `bins` is capped at one per pixel of the clip.

        Zoomed out `_clip_wave` used to ask for `ceil(len / 256)` bins however
        wide the clip was drawn — 33,750 for a three-minute clip into a ~300 px
        lane on the bench CM4 (#162), of which it drew about one in a hundred.
        The numbers it did not draw were ~60 ms of JSON a frame cannot afford,
        and they also put the question below the host's peaks sidecar's
        resolution, so the host had to read the audio for it.
        """
        self.record_take(blocks=900)            # long enough to be drawn zoomed out
        page = self.app.pages["TRACK"]
        self.app.render(self._fb)               # the ask happens in the draw
        clip = self.app.tape.tracks[0][0]
        self.assertTrue(page._peaks, "TRACK asked the host for no peaks")
        asked = [key[4] for key in page._peaks]
        # Nothing may be asked for more bins than the screen has pixels: every
        # lane is narrower than that.
        self.assertTrue(all(b <= 320 for b in asked), asked)
        # And the control: the old formula really would have asked for more, so
        # the cap is doing something on this clip rather than being a no-op.
        uncapped = -(-clip.length // T.REC_PEAK_BUCKET)
        self.assertGreater(uncapped, max(asked),
                           "the clip is too short for the cap to bite; "
                           "uncapped=%d asked=%r" % (uncapped, asked))

    def test_a_bank_redraw_asks_in_one_message_and_draws_the_same_picture(self):
        """#169: the fetches for one redraw go down as a single `clip_peaks`.

        On the CM4 a round trip costs 2.1–2.5 ms whatever the host then does
        with it — the message itself — so a bank of clips spent ~9 ms of a
        35 ms frame on message overhead alone (#163's bench cell). Two claims,
        each with the control that would catch it being wrong: the frame makes
        **one** call where it made one per clip, and the picture is
        **pixel-identical** either way, because the batch is a cache fill and
        not a second way of drawing.
        """
        self.record_take()
        self.key("3")                       # select lane 3 inside the bank
        self.app.tape.seek(0)
        self.record_take(idx=4)
        page = self.app.pages["TRACK"]
        self.enc(1, 14)                     # ENC2: zoom in until the clips are wide
        clips = sum(len(self.app.tape.tracks[t]) for t in self.app.tape.bank_tracks())
        self.assertGreaterEqual(clips, 2, "the bank holds nothing to batch")

        calls = {"one": 0, "batch": 0, "in_batch": 0}
        real_one = self.native.clip_peaks
        real_batch = self.native.clip_peaks_batch

        def count_one(*a, **kw):
            calls["one"] += 1
            return real_one(*a, **kw)

        def count_batch(reqs):
            calls["batch"] += 1
            calls["in_batch"] += len(reqs)
            return real_batch(reqs)

        self.native.clip_peaks = count_one
        self.native.clip_peaks_batch = count_batch
        self.addCleanup(setattr, self.native, "clip_peaks", real_one)
        self.addCleanup(setattr, self.native, "clip_peaks_batch", real_batch)

        page._peaks.clear()
        self._fb[:] = 0
        self.app.render(self._fb)
        batched = self._fb.copy()
        self.assertEqual(calls["batch"], 1, calls)
        self.assertEqual(calls["one"], 0,
                         "a lane still asked for itself after the prefetch")
        self.assertEqual(calls["in_batch"], clips, calls)

        # The control: with the prefetch off, the same frame asks once per
        # clip — so the count above is the batch working, not the page having
        # stopped asking for anything.
        calls.update(one=0, batch=0, in_batch=0)
        page._peaks.clear()
        real_prefetch = page._prefetch_peaks
        page._prefetch_peaks = lambda *a, **kw: None
        self.addCleanup(setattr, page, "_prefetch_peaks", real_prefetch)
        self._fb[:] = 0
        self.app.render(self._fb)
        self.assertEqual((calls["batch"], calls["one"]), (0, clips), calls)
        self.assertEqual(int(np.count_nonzero(self._fb != batched)), 0,
                         "the batched frame is not the picture the lone "
                         "fetches draw")

    def test_a_moved_clip_still_reads_its_own_audio(self):
        self.record_take()
        tp = self.app.tape
        clip = tp.tracks[0][0]
        before = self.native.clip_peaks(0, clip.src, clip.length, 8)
        self.key("LIFT")                        # takes the clip under the cursor
        self.assertIsNotNone(tp.lift_buffer)
        tp.seek(clip.start + 5 * BLOCK)
        self.key("DROP")
        self.run_blocks(2)
        moved = tp.tracks[0][0]
        self.assertNotEqual(moved.start, clip.start)
        self.assertEqual(moved.src, clip.src)   # the audio did not move
        after = self.native.clip_peaks(0, moved.src, moved.length, 8)
        self.assertEqual(before["max"], after["max"])

    def test_the_rolling_take_draws_its_envelope(self):
        self.key("TRACK")
        self.key("REC", down=True)
        self.note(7, True)
        self.run_blocks(4)          # the tick after these rolls the host
        self.run_blocks(40)
        self.assertTrue(self.app.tape.recording)
        tp = self.app.tape
        self.assertGreater(tp.take_length, 0, "the take has no length")
        self.enc(1, 14)                     # zoom in on the take
        self.shot("09-track-rolling-take")
        got = self.native.clip_peaks(0, tp.take_src, tp.take_length, 8)
        self.assertGreater(max(got["max"]), 1e-3,
                           "the rolling take is not in the medium yet")
        self.note(7, False)
        self.key("REC", down=False)
        self.key("STOP")
        self.run_blocks(2)


class TestTapeKeys(TapeCase):
    def test_loop_in_out_and_on_reach_the_host(self):
        self.record_take()
        self.key("TRACK")
        self.app.tape.seek(0)
        self.key("A")                           # IN
        self.app.tape.seek(12000)
        self.key("B")                           # OUT
        self.key("C")                           # LOOP
        self.run_blocks(1)
        loop = self.native.state["tape"]["loop"]
        self.assertEqual((loop["in"], loop["out"], loop["on"]),
                         (0, 12000, True))

    def test_speed_and_rev_reach_the_host(self):
        self.key("TEMPO")
        self.enc(3, -1)                         # ENC4 LINK off (DDR-059)
        self.key("TRACK")
        # ENC3 is the varispeed only with LINK off -- with it on the same knob
        # is the song tempo and the tape follows on the link factor (#69)
        self.enc(2, -25)                        # ENC3 SPEED down
        self.run_blocks(1)
        self.assertLess(self.native.state["tape"]["speed"], 1.0)
        self.key("X", down=True)                # REV held
        self.run_blocks(1)
        self.assertTrue(self.native.state["tape"]["reverse"])
        self.key("X", down=False)
        self.run_blocks(1)
        self.assertFalse(self.native.state["tape"]["reverse"])

    def test_filter_hold_reaches_the_host_and_lets_go(self):
        self.key("TRACK")
        self.key("Z", down=True)                # FILTER held
        self.enc(3, -4)
        self.run_blocks(1)
        flt = self.native.state["tape"]["filter"]
        self.assertTrue(flt["on"])
        self.assertLess(flt["cutoff"], T.FILTER_CENTRE_HZ)
        self.key("Z", down=False)
        self.run_blocks(1)
        self.assertFalse(self.native.state["tape"]["filter"]["on"])

    def test_rev_tapped_while_stopped_bakes_the_clip(self):
        self.record_take()
        tp = self.app.tape
        clip = tp.tracks[0][0]
        before = self.native.clip_peaks(0, clip.src, clip.length, 8)
        self.key("TRACK")
        tp.seek(clip.start + 10)
        self.key("X", down=True)                # hold, then let go while stopped
        self.key("X", down=False)
        self.run_blocks(2)
        baked = tp.tracks[0][0]
        self.assertNotEqual(baked.src, clip.src, "nothing was baked")
        after = self.native.clip_peaks(0, baked.src, baked.length, 8)
        self.assertEqual(after["max"], list(reversed(before["max"])))
        self.assertTrue(tp.can_undo)

    def test_scrub_while_stopped_is_audible(self):
        self.record_take()
        tp = self.app.tape
        self.key("TRACK")
        tp.seek(tp.tracks[0][0].start + 2000)
        self.enc(0, 1)                          # ENC1 nudges and auditions
        block = self.run_blocks(8)
        self.assertGreater(self.peak(block), 1e-4)


class TestMixerReachesTheHost(TapeCase):
    def test_track_gain_pan_and_master(self):
        self.key("MIXER")
        self.run_blocks(1)
        # the restored project pushes the emulator's own defaults down
        self.assertAlmostEqual(self.native.state["tape"]["tracks"][0]["gain"],
                               self.app.mixer.levels[0], places=3)
        self.enc(0, -10)                        # TRK1 down
        self.enc(4, 4)                          # SHIFT row: PAN1
        self.run_blocks(1)
        track = self.native.state["tape"]["tracks"][0]
        self.assertAlmostEqual(track["gain"], self.app.mixer.levels[0], places=3)
        self.assertAlmostEqual(track["pan"], self.app.mixer.pans[0], places=3)

        self.key("4")                           # MASTER
        self.enc(0, -5)
        self.enc(1, 10)                         # COMP
        self.run_blocks(1)
        self.assertAlmostEqual(self.native.state["mixer"]["master"],
                               self.app.mixer.master_level, places=3)
        self.assertAlmostEqual(self.native.state["mixer"]["comp"],
                               self.app.mixer.comp.amount, places=3)


class TestF1MixerMessages(TapeCase):
    """Stage F1's host surface through the shim, before F2 builds the pages."""

    def test_eq_mute_solo_and_the_master_strip_reach_the_host(self):
        self.native.eq(2, low=6.0, mid=-3.0, mid_f=2500.0, hp=80.0)
        self.run_blocks(1)
        eq = self.native.state["tape"]["tracks"][2]["eq"]
        self.assertAlmostEqual(eq["low"], 6.0, places=3)
        self.assertAlmostEqual(eq["mid"], -3.0, places=3)
        self.assertAlmostEqual(eq["mid_f"], 2500.0, places=1)
        self.assertAlmostEqual(eq["hp"], 80.0, places=1)
        self.assertEqual(eq["high"], 0.0)       # untouched fields do not move

        self.native.mixer(track=1, mute=True)
        self.native.mixer(track=3, solo=True)
        self.native.mixer(drive=0.3, drive_tone=0.8, bal=-0.4, comp_rel=0.25)
        self.run_blocks(1)
        st = self.native.state
        self.assertTrue(st["tape"]["tracks"][1]["mute"])
        self.assertTrue(st["tape"]["tracks"][3]["solo"])
        self.assertAlmostEqual(st["mixer"]["drive"], 0.3, places=3)
        self.assertAlmostEqual(st["mixer"]["drive_tone"], 0.8, places=3)
        self.assertAlmostEqual(st["mixer"]["bal"], -0.4, places=3)
        self.assertAlmostEqual(st["mixer"]["comp_rel"], 0.25, places=3)

    def test_the_master_rack_loads_like_an_instrument_rack(self):
        reply = self.native.load_rack("master", {
            "format": 1, "kind": "rack", "name": "MASTER",
            "tabs": [{"instance": "lp", "engine": "arbutus/biquad", "params": {}}]})
        self.assertNotEqual(reply.get("type"), "error", reply)
        self.run_blocks(1)
        mixer = self.native.state["mixer"]
        self.assertEqual(mixer["fx"]["tabs"][0]["engine"], "arbutus/biquad")
        self.assertEqual(mixer["tabs"][0]["id"], "arbutus/biquad")
        # and it is not one of the instruments' racks
        self.assertEqual(self.native.state["instruments"]["synth"]["fx"]["tabs"], [])


class TestTapeSurvivesARestart(TapeCase):
    def test_the_take_is_still_there_and_still_sounds(self):
        self.record_take()
        tp = self.app.tape
        clip = tp.tracks[0][0]
        before = self.native.clip_peaks(0, clip.src, clip.length, 8)
        self.app.flush()
        self.assertTrue(os.path.exists(os.path.join(self.state, "tape.json")))
        # the take itself, where the host wrote it as it happened (#297) —
        # the audio process's name, 1-based, and no copy made
        self.assertTrue(os.path.exists(os.path.join(self.state, "tape-1.wav")))

        # a second host, as a restart is, and the same state dir
        self.native.close()
        self.native = native_or_skip(self, offline=True, block=BLOCK,
                                     libraries=[os.path.join(REPO, "audio", "build", "boxes"),
                                                self.lib_root])
        self.addCleanup(self.native.close)
        app = self.make_app(fresh=False)
        again = app.tape.tracks[0]
        self.assertTrue(again, "the tape came back empty")
        self.assertEqual((again[0].start, again[0].length, again[0].src),
                         (clip.start, clip.length, clip.src))
        after = self.native.clip_peaks(0, again[0].src, again[0].length, 8)
        self.assertEqual(before["max"], after["max"])


class TestStopPushesOnlyTheTrackThatMoved(TapeCase):
    """#74 (C1 step 3b's CM4 measurement): every message costs the host a
    state document, ~3 ms on the CM4, so a STOP that pushed all eight clip
    lists back down spent ~50 ms of its frame telling the host seven things
    it already knew. The take's own track goes down; the other seven do not.
    """

    def pushes(self):
        sent = []
        real = self.native.set_clips

        def spy(track, clips):
            sent.append(track)
            return real(track, clips)
        self.native.set_clips = spy
        self.addCleanup(setattr, self.native, "set_clips", real)
        return sent

    def test_a_take_pushes_its_track_and_no_other(self):
        self.run_blocks(2)
        sent = self.pushes()
        self.record_take()
        self.assertTrue(self.app.tape.tracks[0], "no take to push")
        self.assertEqual(set(sent), {0})
        # and the host holds the list the UI holds: nothing was skipped
        host = self.native.send({"type": "state"})["tape"]["tracks"]
        self.assertEqual(
            [(c["start_frame"], c["len"], c["src_offset"]) for c in host[0]["clips"]],
            [(int(c.start), int(c.length), int(c.src)) for c in self.app.tape.tracks[0]])
        self.assertEqual([t["clips"] for t in host[1:]], [[]] * 7)

    def test_a_forgotten_fingerprint_still_pushes_all_eight(self):
        # a reconnect or a LOAD sets it to None: the host knows nothing
        self.run_blocks(2)
        sent = self.pushes()
        self.app._clip_fingerprint = None
        self.run_blocks(1)
        self.assertEqual(sorted(sent), list(range(8)))


class TestAnotherClientOwnsTheTransport(TapeCase):
    """#99: the host serves several clients; the UI holds a mirror.

    `arbutus-audio` takes a second connection — the companion app's TCP one
    (D2), or a bench tool — and the transport is the host's value. Before
    this, the UI believed its own copy: every frame that saw the tape move
    while the UI thought it was stopped sent `locate` back to the UI's own
    playhead (and `transport` fetches the whole state document with it), so
    the tape never got anywhere and the frame lost 3-6 ms on the CM4.
    """

    def second_client(self):
        """A second connection to the same host, as the phone is."""
        from arbutus_ctl import connect

        conn = connect(self.native.sock)
        conn.hello(firmware="test-second-client")
        self.addCleanup(conn.close)
        return conn

    def watch_transport(self):
        """Every `transport` message this UI sends, in order."""
        sent = []
        original = self.native.transport

        def spy(op, position=None):
            sent.append((op, None if position is None else int(position)))
            return original(op, position)

        self.native.transport = spy
        self.addCleanup(lambda: setattr(self.native, "transport", original))
        return sent

    def run_until(self, done, timeout=5.0):
        """Ticks until `done()`. The UI hears another client's transport in the
        host's `state` push, which the host may hold 50 ms by design (PROTOCOL.md
        § State) and longer on a loaded machine, so one tick right after the
        other client's reply can come before it (#540, #536). The timeout is a
        ceiling, not a sleep."""
        end = time.monotonic() + timeout
        while not done() and time.monotonic() < end:
            self.run_blocks(1)

    def test_a_second_client_rolls_the_tape_and_the_ui_follows(self):
        self.key("TRACK")
        self.run_blocks(2)
        sent = self.watch_transport()

        other = self.second_client()
        other.request({"type": "transport", "op": "start"})
        self.run_until(lambda: self.app.tape.playing)
        self.run_blocks(8)

        self.assertTrue(self.app.tape.playing,
                        "the UI did not follow the host's transport")
        self.assertGreater(self.native.tape_pos, 0, "the tape never moved")
        self.assertAlmostEqual(self.app.tape.playhead, self.native.tape_pos,
                               delta=1.0)
        self.assertEqual([m for m in sent if m[0] == "locate"], [],
                         "the UI relocated a tape it did not roll")

        other.request({"type": "transport", "op": "stop"})
        self.run_until(lambda: not self.app.tape.playing)
        self.assertFalse(self.app.tape.playing,
                         "the UI did not follow the host's stop")

    def test_a_second_client_locates_and_the_ui_does_not_drag_it_back(self):
        self.key("TRACK")
        self.run_blocks(2)
        sent = self.watch_transport()

        other = self.second_client()
        other.request({"type": "transport", "op": "locate",
                       "position": 48000})
        self.run_blocks(4)

        self.assertAlmostEqual(self.app.tape.playhead, 48000, delta=1.0)
        self.assertEqual([m for m in sent if m[0] == "locate"], [],
                         "the UI pulled the playhead back to its own")

    def test_the_ui_still_locates_what_it_moved_itself(self):
        """The other half of the rule: what this UI changes is still sent."""
        self.key("TRACK")
        self.run_blocks(2)
        sent = self.watch_transport()

        self.app.tape.seek(96000)
        self.run_blocks(2)
        self.assertIn(("locate", 96000), sent)
        self.assertAlmostEqual(self.native.tape_pos, 96000, delta=1.0)

        # and once is enough: the host is there now, so the next frames are
        # silent (the 20-30 a second is exactly what #99 was)
        before = len(sent)
        self.run_blocks(4)
        self.assertEqual(len(sent), before,
                         "the UI kept relocating a host already in place")


if __name__ == "__main__":
    unittest.main()
