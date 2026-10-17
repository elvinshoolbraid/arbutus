"""Stage E2: the tape UI — banks, the tick ladder, the travel keys, the drops.

Everything here is driven through the **input seam** (`App.on_key` /
`App.on_encoder`) or through the pure functions in `app/tape.py`, because the
question is always whether a player's gesture reaches the model and the host.

Spec: `tape-sampling-workflow.md` §2, `screen-sketches.md` § TRACK,
DDR-030 / 055 / 057 / 058 / 060.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import tape as T                                   # noqa: E402
from app.tape import Clip, Tape                             # noqa: E402

from tests.test_native_tape import TapeCase                  # noqa: E402

SR = T.SAMPLE_RATE


# ---------------------------------------------------------------------------
# the ladder, as arithmetic (DDR-058)
# ---------------------------------------------------------------------------

class TestTickLadder(unittest.TestCase):
    BAR = 2.0 * SR                      # one bar at 120 BPM

    def ticks_on_screen(self, view_len):
        tick = T.tick_ladder(view_len, self.BAR)
        return view_len / tick.samples, tick

    def test_every_zoom_from_the_whole_tape_to_20_ms_keeps_at_most_16_ticks(self):
        view = float(T.TRACK_LEN)
        while view > 0.02 * SR:
            n, tick = self.ticks_on_screen(view)
            self.assertLessEqual(n, T.TICKS_MAX + 1e-9,
                                 "%.0f frames -> %r" % (view, tick))
            view /= 1.25

    def test_the_eight_end_holds_except_at_the_one_ladder_seam(self):
        """8 is the other end of DDR-058's rule.

        One rung can fall short: the 1-2-5 time rungs step by 2.5 where the
        musical ones step by 2, so the window just under the bar/time crossover
        gets ~7 ticks. Everything else keeps 8.
        """
        view = 30.0 * SR
        short = []
        while view > 0.05 * SR:
            n, tick = self.ticks_on_screen(view)
            self.assertGreater(n, 6.0, repr(tick))
            if n < 8:
                short.append(round(view / SR, 3))
            view /= 1.25
        self.assertLessEqual(len(short), 1, short)

    def test_the_default_thirty_second_window_ticks_in_bars(self):
        tick = T.tick_ladder(30.0 * SR, self.BAR)
        self.assertEqual(tick.kind, "bar")
        self.assertEqual(tick.size, 1.0)
        self.assertTrue(tick.labelled)

    def test_zooming_in_walks_down_the_musical_rungs(self):
        sizes = []
        for view in (16.0, 8.0, 4.0, 2.0, 1.0, 0.5):
            tick = T.tick_ladder(view * SR, self.BAR)
            if tick.kind == "bar":
                sizes.append(tick.size)
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertIn(0.25, sizes)          # a sixteenth of a 4/4 bar

    def test_sub_bar_ticks_are_not_labelled(self):
        tick = T.tick_ladder(1.0 * SR, self.BAR)
        self.assertEqual(tick.kind, "bar")
        self.assertLess(tick.size, 1.0)
        self.assertFalse(tick.labelled)

    def test_under_a_quarter_second_the_ladder_counts_time(self):
        tick = T.tick_ladder(0.2 * SR, self.BAR)
        self.assertEqual(tick.kind, "time")
        self.assertFalse(tick.labelled)
        self.assertLessEqual(0.2 * SR / tick.samples, T.TICKS_MAX)

    def test_the_deepest_zoom_ticks_in_milliseconds(self):
        tick = T.tick_ladder(0.02 * SR, self.BAR)
        self.assertEqual(tick.kind, "time")
        self.assertLess(tick.size, 0.01)
        self.assertGreaterEqual(tick.samples, 1.0)

    def test_the_ladder_follows_the_tempo_not_the_clock(self):
        """Same window, same tick *count* — but a different musical value."""
        slow = T.tick_ladder(30.0 * SR, 4.0 * SR)       # 60 BPM
        fast = T.tick_ladder(30.0 * SR, 1.0 * SR)       # 240 BPM
        self.assertEqual(slow.kind, "bar")
        self.assertEqual(fast.kind, "bar")
        self.assertLess(slow.size, fast.size)
        self.assertAlmostEqual(slow.samples, fast.samples, places=3)


# ---------------------------------------------------------------------------
# banks (DDR-060), on the model alone
# ---------------------------------------------------------------------------

class TestBanks(unittest.TestCase):
    def setUp(self):
        self.tp = Tape()

    def test_the_tape_is_eight_tracks_in_two_banks(self):
        self.assertEqual(T.NUM_TRACKS, 8)
        self.assertEqual(len(self.tp.tracks), 8)
        self.assertEqual(len(self.tp.armed), 8)
        self.assertEqual(T.NUM_BANKS, 2)

    def test_plain_select_stays_in_the_shown_bank(self):
        self.tp.select_in_bank(2)
        self.assertEqual(self.tp.selected, 2)
        self.assertEqual(self.tp.bank, 0)

    def test_shift_flips_the_bank_and_selects_in_one_gesture(self):
        self.tp.flip_bank_select(1)
        self.assertEqual(self.tp.selected, 5)
        self.assertEqual(self.tp.bank, 1)
        self.assertEqual(self.tp.bank_tracks(), [4, 5, 6, 7])

    def test_plain_select_inside_bank_b_stays_in_bank_b(self):
        self.tp.flip_bank_select(0)
        self.tp.select_in_bank(3)
        self.assertEqual(self.tp.selected, 7)
        self.assertEqual(self.tp.bank, 1)

    def test_shift_from_bank_b_comes_back(self):
        self.tp.flip_bank_select(0)
        self.tp.flip_bank_select(2)
        self.assertEqual(self.tp.selected, 2)
        self.assertEqual(self.tp.bank, 0)

    def test_bank_b_is_only_revealed_once_it_holds_a_clip(self):
        self.assertFalse(self.tp.bank_has_clips(1))
        self.tp.tracks[6] = [Clip(0, SR)]
        self.assertTrue(self.tp.bank_has_clips(1))

    def test_lift_all_reaches_all_eight_tracks(self):
        for t in range(T.NUM_TRACKS):
            self.tp.tracks[t] = [Clip(SR, SR)]
        self.tp.seek(SR + 10)
        self.tp.do_lift_all()
        self.assertEqual(len(self.tp.lift_buffer.clips), 8)
        self.assertTrue(all(not self.tp.tracks[t] for t in range(8)))


# ---------------------------------------------------------------------------
# travel: SHIFT+arrows and STOP+arrows (DDR-057)
# ---------------------------------------------------------------------------

class TestJump(unittest.TestCase):
    def setUp(self):
        self.tp = Tape()
        self.tp.tracks[0] = [Clip(SR, SR)]
        self.tp.tracks[5] = [Clip(3 * SR, 2 * SR)]      # the last clip, bank B

    def test_with_no_loop_the_arrows_reach_the_tape_ends(self):
        self.tp.seek(2 * SR)
        self.tp.jump(-1)
        self.assertEqual(self.tp.playhead, 0)
        self.tp.jump(1)
        self.assertEqual(self.tp.playhead, 5 * SR)      # end of the last clip

    def test_with_a_loop_on_they_reach_its_edges(self):
        self.tp.loop_in, self.tp.loop_out = SR, 4 * SR
        self.tp.loop_on = True
        self.tp.jump(1)
        self.assertEqual(self.tp.playhead, 4 * SR)
        self.tp.jump(-1)
        self.assertEqual(self.tp.playhead, SR)

    def test_a_loop_that_is_set_but_off_still_means_the_tape_ends(self):
        self.tp.loop_in, self.tp.loop_out = SR, 4 * SR
        self.tp.loop_on = False
        self.tp.jump(1)
        self.assertEqual(self.tp.playhead, 5 * SR)

    def test_an_empty_tape_has_both_ends_at_zero(self):
        tp = Tape()
        tp.seek(SR)
        tp.jump(1)
        self.assertEqual(tp.playhead, 0)


# ---------------------------------------------------------------------------
# lift undo groups (DDR-055)
# ---------------------------------------------------------------------------

class TestLiftUndoGroup(unittest.TestCase):
    def setUp(self):
        self.tp = Tape()
        self.tp.tracks[0] = [Clip(0, SR), Clip(2 * SR, SR), Clip(4 * SR, SR)]

    def lift_at(self, pos):
        self.tp.seek(pos)
        self.tp.do_lift()

    def test_consecutive_lifts_undo_as_one(self):
        self.lift_at(SR // 2)
        self.lift_at(2 * SR + 100)
        self.assertEqual(len(self.tp.tracks[0]), 1)
        self.assertTrue(self.tp.undo())
        self.assertEqual(len(self.tp.tracks[0]), 3)     # both came back

    def test_a_drop_between_two_lifts_closes_the_group(self):
        self.lift_at(SR // 2)
        self.tp.seek(6 * SR)
        self.tp.do_drop()
        self.lift_at(2 * SR + 100)
        self.assertTrue(self.tp.undo())
        # only the second lift is undone: the drop is its own edit
        starts = [c.start for c in self.tp.tracks[0]]
        self.assertIn(2 * SR, starts)
        self.assertNotIn(0, starts)

    def test_a_slice_between_two_lifts_closes_the_group(self):
        self.lift_at(SR // 2)
        self.tp.seek(2 * SR + SR // 2)
        self.tp.do_slice()
        self.lift_at(2 * SR + 100)
        self.tp.undo()
        self.assertEqual(len(self.tp.tracks[0]), 3)     # sliced, not unlifted

    def test_undo_is_one_level_and_does_not_repeat(self):
        self.lift_at(SR // 2)
        self.assertTrue(self.tp.undo())
        self.assertFalse(self.tp.undo())


# ---------------------------------------------------------------------------
# LIFT ALL drops back per lane (§2.1)
# ---------------------------------------------------------------------------

class TestLiftAllDrop(unittest.TestCase):
    def test_a_lift_all_buffer_drops_each_clip_on_its_own_lane(self):
        tp = Tape()
        tp.tracks[0] = [Clip(SR, SR)]
        tp.tracks[2] = [Clip(SR, 2 * SR)]
        tp.seek(SR + 100)
        tp.do_lift_all()
        tp.seek(5 * SR)
        tp.do_drop()
        self.assertEqual([(c.start, c.length) for c in tp.tracks[0]],
                         [(5 * SR, SR)])
        self.assertEqual([(c.start, c.length) for c in tp.tracks[2]],
                         [(5 * SR, 2 * SR)])
        self.assertFalse(tp.tracks[1])

    def test_relative_offsets_survive_the_round_trip(self):
        tp = Tape()
        tp.tracks[0] = [Clip(SR, SR)]
        tp.tracks[1] = [Clip(SR + 4000, SR)]      # under the cursor too
        tp.seek(SR + 5000)
        tp.do_lift_all()
        tp.seek(10 * SR)
        tp.do_drop()
        self.assertEqual(tp.tracks[0][0].start, 10 * SR)
        self.assertEqual(tp.tracks[1][0].start, 10 * SR + 4000)


# ---------------------------------------------------------------------------
# the page and the host
# ---------------------------------------------------------------------------

class TrackUICase(TapeCase):
    """The TRACK page on a real offline host."""

    def page(self):
        return self.app.pages["TRACK"]


class TestBankKeys(TrackUICase):
    def test_shift_one_to_four_flips_the_bank_and_selects(self):
        self.key("TRACK")
        self.key("2")
        self.assertEqual(self.app.tape.selected, 1)
        self.key("2", shift=True)
        self.assertEqual(self.app.tape.selected, 5)
        self.assertEqual(self.app.tape.bank, 1)
        self.assertEqual(self.page().strip_labels(),
                         ["TRK5", "TRK6", "TRK7", "TRK8"])
        self.assertEqual(self.page().active_strip_cell(), 1)
        self.shot("e2-01-bank-b")

    def test_a_bank_b_track_records_and_the_host_agrees(self):
        self.key("TRACK")
        self.key("3", shift=True)                   # TRK7
        self.assertEqual(self.app.tape.selected, 6)
        self.key("REC", down=True)
        self.note(7, True)
        self.run_blocks(24)
        self.note(7, False)
        self.key("REC", down=False)
        self.run_blocks(2)
        self.key("STOP")
        self.run_blocks(2)
        self.assertEqual(len(self.app.tape.tracks[6]), 1)
        clip = self.app.tape.tracks[6][0]
        got = self.native.clip_peaks(6, clip.src, clip.length, 8)
        self.assertGreater(max(got["max"]), 1e-3)
        host_clips = self.native.state["tape"]["tracks"][6]["clips"]
        self.assertEqual(len(host_clips), 1)
        self.shot("e2-02-bank-b-take")

    def test_the_mixer_follows_the_bank(self):
        self.key("TRACK")
        self.key("1", shift=True)
        self.key("MIXER")
        names = [p.name for p in self.app.pages["MIXER"].slots()[:4] if p]
        self.assertEqual(names, ["TRK5", "TRK6", "TRK7", "TRK8"])


class TestTickKeys(TrackUICase):
    def test_shift_arrow_steps_one_tick_at_whatever_zoom(self):
        self.key("TRACK")
        page = self.page()
        tp = self.app.tape
        coarse = page.tick().samples
        tp.seek(0)
        self.key("RIGHT", shift=True)
        self.assertAlmostEqual(tp.playhead, coarse, places=3)

        self.enc(1, 14)                     # ENC2: zoom well in
        fine = page.tick().samples
        self.assertLess(fine, coarse)
        before = tp.playhead
        self.key("RIGHT", shift=True)
        self.assertAlmostEqual(tp.playhead - before, fine, places=3)

    def test_stop_held_jumps_to_the_tape_end(self):
        self.record_take()
        tp = self.app.tape
        end = tp.tracks[0][0].end
        self.key("STOP")                    # a second STOP rewinds
        self.assertEqual(int(tp.playhead), 0)
        self.key("STOP", down=True)
        self.key("RIGHT")
        self.key("STOP", down=False)
        self.assertEqual(int(tp.playhead), int(end))
        self.shot("e2-03-jump-to-end")

    def test_stop_held_jumps_to_the_loop_edges(self):
        self.key("TRACK")
        tp = self.app.tape
        tp.seek(SR)
        self.key("A")                       # IN
        tp.seek(3 * SR)
        self.key("B")                       # OUT
        self.key("C")                       # LOOP on
        self.assertTrue(tp.loop_on)
        tp.seek(0)
        self.key("STOP", down=True)
        self.key("RIGHT")
        self.assertEqual(int(tp.playhead), 3 * SR)
        self.key("LEFT")
        self.assertEqual(int(tp.playhead), SR)
        self.key("STOP", down=False)

    def test_rec_plus_note_snaps_to_the_current_tick(self):
        self.key("TRACK")
        page = self.page()
        self.enc(1, 10)                     # zoom in: the tick is sub-bar
        tick = page.tick().samples
        self.assertLess(tick, self.app.bar_samples())
        self.app.tape.seek(tick * 3.4)
        self.key("REC", down=True)
        self.note(7, True)
        self.assertTrue(self.app.tape.recording)
        self.assertAlmostEqual(self.app.tape.playhead / tick, 3.0, places=3)
        self.run_blocks(8)
        self.note(7, False)
        self.key("REC", down=False)
        self.key("STOP")
        self.run_blocks(2)
        self.assertEqual(len(self.app.tape.tracks[0]), 1)


class TestMergeDrop(TrackUICase):
    def two_takes(self):
        """One take on TRK1, one on TRK2, overlapping in time."""
        for track, idx in ((0, 7), (1, 12)):
            self.key("TRACK")
            self.key(str(track + 1))
            self.app.tape.seek(0)
            self.key("REC", down=True)
            self.note(idx, True)
            self.run_blocks(24)
            self.note(idx, False)
            self.key("REC", down=False)
            self.run_blocks(2)
            self.key("STOP")
            self.run_blocks(2)
        self.assertEqual(len(self.app.tape.tracks[0]), 1)
        self.assertEqual(len(self.app.tape.tracks[1]), 1)

    def test_shift_drop_mixes_a_lift_all_buffer_onto_the_selected_track(self):
        self.two_takes()
        tp = self.app.tape
        tp.seek(tp.tracks[0][0].start + 1000)
        self.key("LIFT", shift=True)                 # LIFT ALL
        self.assertEqual(len(tp.lift_buffer.clips), 2)
        self.assertTrue(any("MERGE" in long for _, long, _ in
                            self.page().tickers()))
        self.key("3")                                # onto TRK3
        tp.seek(6 * SR)
        self.key("DROP", shift=True)
        self.run_blocks(2)
        self.assertEqual(len(tp.tracks[2]), 1)
        merged = tp.tracks[2][0]
        self.assertEqual(merged.start, 6 * SR)
        got = self.native.clip_peaks(2, merged.src, merged.length, 16,
                                     src_track=merged.src_track)
        self.assertGreater(max(got["max"]), 1e-3)
        self.shot("e2-04-merge-drop")

    def test_a_single_clip_buffer_merge_drops_as_an_ordinary_drop(self):
        self.record_take()
        tp = self.app.tape
        tp.seek(tp.tracks[0][0].start + 100)
        self.key("LIFT")
        tp.seek(8 * SR)
        self.key("DROP", shift=True)
        self.assertEqual(len(tp.tracks[0]), 1)
        self.assertEqual(tp.tracks[0][0].start, 8 * SR)


class TestDropOnInstrument(TrackUICase):
    """DDR-030: the tape → instrument half of the round trip."""

    def test_drop_on_synth_engine_makes_the_clip_the_sampler_sample(self):
        self.record_take()
        tp = self.app.tape
        tp.seek(tp.tracks[0][0].start + 100)
        self.key("LIFT")
        self.assertIsNotNone(tp.lift_buffer)
        self.key("SYNTH")
        self.key("1")                                # ENGINE
        self.key("DROP")
        synth = self.app.pages["SYNTH"]
        self.assertEqual(synth.instrument.engine_folder.folder, "sampler")
        slot = self.native.slot("SYNTH", "engine")
        self.assertEqual(slot.get("id"), "arbutus/sampler")
        path = slot["assets"]["sample"]
        self.assertTrue(path.startswith(self.lib_root), path)
        self.assertIn(os.path.join("synth", "sampler"), path)
        self.assertTrue(os.path.exists(path))
        # the buffer is a copy, not a move: the clip can still go back on tape
        self.assertIsNotNone(tp.lift_buffer)
        self.key("SYNTH")
        self.note(7, True)
        block = self.run_blocks(24)
        self.note(7, False)
        self.assertGreater(self.peak(block), 1e-3)
        self.shot("e2-05-tape-to-synth")

    def test_the_tape_list_is_put_back_after_the_render(self):
        self.record_take()
        tp = self.app.tape
        before = [(c.start, c.length, c.src) for c in tp.tracks[0]]
        tp.seek(tp.tracks[0][0].start + 100)
        self.key("LIFT")
        after_lift = [(c.start, c.length, c.src) for c in tp.tracks[0]]
        self.key("SYNTH")
        self.key("1")
        self.key("DROP")
        self.run_blocks(2)
        self.assertNotEqual(before, after_lift)
        host_clips = self.native.state["tape"]["tracks"][0]["clips"]
        self.assertEqual(len(host_clips), len(after_lift))

    def test_drop_on_drum_engine_lands_on_the_last_hit_pad(self):
        self.record_take()
        tp = self.app.tape
        tp.seek(tp.tracks[0][0].start + 100)
        self.key("LIFT")
        self.key("DRUM")
        self.key("1")                                # ENGINE
        drum = self.app.pages["DRUM"]
        self.note(3, True)                           # the pad the drop is about
        self.note(3, False)
        pad = drum.pad
        before = drum.instrument.engine.pad_file(pad)
        self.key("DROP")
        after = drum.instrument.engine.pad_file(pad)
        self.assertNotEqual(before, after)
        self.assertTrue(after.endswith(".wav"), after)
        self.assertIn("tape-", os.path.basename(after))
        self.shot("e2-06-tape-to-pad")

    def test_a_lift_all_buffer_arrives_as_one_mixdown(self):
        for track, idx in ((0, 7), (1, 12)):
            self.key("TRACK")
            self.key(str(track + 1))
            self.app.tape.seek(0)
            self.key("REC", down=True)
            self.note(idx, True)
            self.run_blocks(24)
            self.note(idx, False)
            self.key("REC", down=False)
            self.run_blocks(2)
            self.key("STOP")
            self.run_blocks(2)
        tp = self.app.tape
        tp.seek(tp.tracks[0][0].start + 100)
        self.key("LIFT", shift=True)
        self.assertEqual(len(tp.lift_buffer.clips), 2)
        self.key("SYNTH")
        self.key("1")
        self.key("DROP")
        slot = self.native.slot("SYNTH", "engine")
        self.assertEqual(slot.get("id"), "arbutus/sampler")
        self.assertTrue(os.path.exists(slot["assets"]["sample"]))


class TestLinkMakesEncThreeOneControl(TrackUICase):
    """DDR-059 / #69: with LINK on, BPM and tape speed are **one control**.

    Both directions are asserted, because only one of them was ever built:
    BPM already dragged the tape (the host's link factor), while TRACK's ENC3
    moved the varispeed and never touched BPM, so the two disagreed after any
    turn of it. Spec: `tape-sampling-workflow.md` §2.3.
    """

    def link_doc(self):
        return self.native.refresh()["tape"]["link"]

    def set_link(self, on):
        """Through the seam: ENC4 on the TEMPO page is LINK and nothing else."""
        self.key("TEMPO")
        self.enc(3, 1 if on else -1)
        self.key("TRACK")
        self.assertEqual(bool(self.link_doc()["on"]), bool(on))

    def test_link_on_enc3_moves_the_tempo_and_the_tape_follows(self):
        self.key("TRACK")
        self.run_blocks(1)
        self.assertTrue(bool(self.link_doc()["on"]))      # DDR-059: on by default
        base = float(self.link_doc()["base_bpm"])
        self.assertEqual(self.page().slots()[2].name, "BPM")

        self.enc(2, 10)
        self.assertAlmostEqual(self.app.bpm, 130.0, places=3)
        doc = self.link_doc()
        # the tape moved with it, and it moved *as the link factor* -- the
        # varispeed is a different control and stayed where it was
        self.assertAlmostEqual(float(doc["factor"]), 130.0 / base, places=3)
        self.assertAlmostEqual(float(self.native.state["tape"]["speed"]), 1.0,
                               places=3)
        self.assertAlmostEqual(self.app.tape.speed, 1.0, places=6)

        self.enc(2, -20)
        self.assertAlmostEqual(self.app.bpm, 110.0, places=3)
        self.assertAlmostEqual(float(self.link_doc()["factor"]), 110.0 / base,
                               places=3)

    def test_link_off_enc3_moves_the_varispeed_and_the_tempo_holds(self):
        self.key("TRACK")
        self.run_blocks(1)
        self.set_link(False)
        bpm = float(self.app.bpm)
        self.assertEqual(self.page().slots()[2].name, "SPEED")

        self.enc(2, 10)
        self.assertAlmostEqual(self.app.tape.speed, 1.1, places=3)
        self.assertAlmostEqual(self.app.bpm, bpm, places=6)
        self.app.tick()
        self.assertAlmostEqual(float(self.native.refresh()["tape"]["speed"]),
                               1.1, places=3)
        # LINK off is the host's own rule as well: the factor is 1 whatever
        # the tempo is
        self.assertAlmostEqual(float(self.link_doc()["factor"]), 1.0, places=6)

    def test_the_two_agree_after_a_turn_of_either_one(self):
        """The bug in one line: ENC3 then BPM used to leave two tempos."""
        self.key("TRACK")
        self.run_blocks(1)
        self.enc(2, 4)                                  # 124 through TRACK
        self.key("TEMPO")
        self.assertEqual(self.app.pages["TEMPO"].slots()[0].format_value(),
                         "124")
        self.enc(0, 4)                                  # 128 through TEMPO
        self.key("TRACK")
        self.assertEqual(self.page().slots()[2].format_value(), "128")
        self.assertAlmostEqual(self.app.bpm, 128.0, places=3)

    def test_a_midi_master_locks_the_knob_on_this_page_too(self):
        """TEMPO dims BPM under an external clock; the same knob here is the
        same control, so it is dim and inert here as well."""
        self.key("TRACK")
        self.run_blocks(1)
        tempo = self.app.pages["TEMPO"]
        tempo.p_sync.set_value(1.0)                     # SYNC = MIDI
        cell = self.page().slots()[2]
        self.assertEqual(cell.name, "BPM")
        self.assertTrue(cell.locked and cell.dimmed)
        self.enc(2, 10)
        self.assertAlmostEqual(self.app.bpm, 120.0, places=6)



class TestTapeHoldsLetGo(TrackUICase):
    """#623: a tape key's release reaches TRACK from any page, and panic lets
    go of every tape hold (control-inventory § Tape keys: snaps on release)."""

    def setUp(self):
        super().setUp()
        self.key("TRACK")
        self.tape = self.app.tape

    def test_rev_released_on_another_page_lets_go(self):
        self.key("PLAY")
        self.key("X", down=True)
        self.assertTrue(self.tape.rev_held)
        self.key("SYNTH")
        self.key("X", down=False)
        self.assertFalse(self.tape.rev_held)

    def test_scrub_released_on_another_page_stops(self):
        self.key("RIGHT", down=True)
        self.assertEqual(self.tape.scrub_dir, 1)
        self.key("SYNTH")
        self.key("RIGHT", down=False)
        self.assertEqual(self.tape.scrub_dir, 0)

    def test_filter_released_under_help_lets_go(self):
        self.key("Z", down=True)
        self.key("HELP", down=True)
        self.key("Z", down=False)
        self.key("HELP", down=False)
        self.assertFalse(self.tape.filter_held)

    def take(self):
        """One clip on track 1, stopped; returns what a bake would change."""
        self.key("REC", down=True)
        self.note(7, True)
        self.run_blocks(24)
        self.note(7, False)
        self.key("REC", down=False)
        self.run_blocks(2)
        self.key("STOP")
        self.run_blocks(2)
        return self.geometry()

    def geometry(self):
        return [[(c.start, c.length, c.src) for c in t]
                for t in self.tape.tracks], self.tape._undo

    def spy_bakes(self):
        calls = []
        for name in ("do_reverse", "do_speed", "do_bake_filter"):
            real = getattr(self.tape, name)
            setattr(self.tape, name,
                    lambda real=real, name=name: (calls.append(name), real()))
        return calls

    def test_a_release_after_panic_commits_nothing(self):
        """#873 (t623 row 1): panic let the hold go, so its release is not
        a stopped tap: no reverse is baked and the undo stays as it was."""
        before = self.take()
        bakes = self.spy_bakes()
        self.key("PLAY")
        self.run_blocks(1)
        self.key("X", down=True)
        self.app.panic()
        self.key("X", down=False)
        self.assertEqual(bakes, [])
        self.assertEqual(self.geometry(), before)

    def test_panic_forgets_where_a_held_key_belongs(self):
        """#873: a hold begun on TRACK and released on SYNTH after panic
        reaches no page as the end of a gesture."""
        before = self.take()
        bakes = self.spy_bakes()
        self.key("X", down=True)
        self.key("SYNTH")
        self.app.panic()
        self.assertEqual(self.app._key_owner, {})
        self.key("X", down=False)
        self.assertEqual(bakes, [])
        self.assertEqual(self.geometry(), before)

    def test_panic_lets_every_tape_hold_go(self):
        self.key("RIGHT", down=True)
        for k in ("X", "Y", "Z"):
            self.key(k, down=True)
        self.app.panic()
        self.assertEqual(self.tape.scrub_dir, 0)
        self.assertFalse(self.tape.rev_held or self.tape.speed_held
                         or self.tape.filter_held)


# ---------------------------------------------------------------------------
# #632: a set loop is framed; the waveform cache forgets a replaced medium
# ---------------------------------------------------------------------------

class TestLoopIsFramed(TrackUICase):
    """screen-sketches § TRACK: "Setting a loop frames it once, at the moment
    you set it" (review m-emu-pages rows 7 and 8)."""

    def view(self):
        st = self.page().state
        self.page().slots()                 # what a frame does: re-centre
        return st.view_start, st.view_start + st.view_len

    def test_in_then_out_at_the_playhead_shows_both_ends(self):
        self.key("TRACK")
        tp = self.app.tape
        self.page().state.view_len = float(SR)          # zoomed in to 1 s
        tp.seek(0)
        self.key("A")                                   # IN at 0 s
        tp.seek(10 * SR)
        self.key("B")                                   # OUT at the playhead
        self.key("C")                                   # LOOP on
        a, b = self.view()
        self.assertLessEqual(a, tp.loop_in)
        self.assertGreaterEqual(b, tp.loop_out)

    def test_a_playhead_inside_the_loop_frames_both_ends_too(self):
        self.key("TRACK")
        tp = self.app.tape
        self.page().state.view_len = float(SR)
        tp.loop_in, tp.loop_out = 20 * SR, 26 * SR
        tp.seek(21 * SR)                                # near IN, not centred
        self.key("C")
        a, b = self.view()
        self.assertLessEqual(a, tp.loop_in)
        self.assertGreaterEqual(b, tp.loop_out)


class TestWaveCacheForgetsAReplacedMedium(TrackUICase):
    """Review m-emu-pages row 4: after NEW, LOAD or a compaction a new take
    can sit at an old offset, so a waveform cached by geometry alone drew the
    old take over the new one."""

    KEY = (0, 0, 0, 100, 10)

    def poison(self):
        """A cached reply no host would give, so its reuse is visible."""
        page = self.page()
        page._fresh_peaks()
        page._peaks[self.KEY] = ([-0.5] * 10, [0.5] * 10)
        self.assertEqual(page._clip_peaks(0, self.KEY)[1], [0.5] * 10)

    def assertForgotten(self):
        got = self.page()._clip_peaks(0, self.KEY)
        self.assertNotEqual((got or ([], []))[1], [0.5] * 10)

    def test_tape_dir_forgets(self):
        self.poison()
        folder = os.path.join(self.tmp, "elsewhere")
        os.makedirs(folder)
        self.native.tape_dir(folder)
        self.assertForgotten()

    def test_tape_load_forgets(self):
        self.poison()
        self.native.tape_load(0, os.path.join(self.tmp, "missing.wav"))
        self.assertForgotten()

    def test_a_finished_compaction_forgets(self):
        self.poison()
        push = {"type": "tape_compact", "track": -1, "map": []}
        self.native.conn.pushes = lambda: [push]
        self.native.conn.clear_pushes = lambda: None
        self.native._absorb_pushes()
        self.assertForgotten()

    def test_nothing_replaced_keeps_the_cache(self):
        self.poison()
        self.assertEqual(self.page()._clip_peaks(0, self.KEY)[1], [0.5] * 10)

    def test_a_full_cache_evicts_the_oldest_not_everything(self):
        """Row 12: clearing at 65 made a 66-clip bank refetch every frame."""
        from pages.track import PEAKS_CACHE_MAX
        page = self.page()
        page._fresh_peaks()
        for n in range(PEAKS_CACHE_MAX + 1):
            page._remember_peaks((0, n, 0, 1, 1), {"min": [0], "max": [0]})
        self.assertEqual(len(page._peaks), PEAKS_CACHE_MAX)
        self.assertNotIn((0, 0, 0, 1, 1), page._peaks)
        self.assertIn((0, PEAKS_CACHE_MAX, 0, 1, 1), page._peaks)
        self.assertIn((0, 1, 0, 1, 1), page._peaks)


if __name__ == "__main__":
    unittest.main()
