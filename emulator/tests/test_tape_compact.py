"""C1 step 8's UI half (#164): the remap through a compaction map, and T9.

The *medium* is the file one tape track's recorded audio lives in. It is
written append-only (DDR-056), so a retake, a LIFT with no DROP and every
`tape_edit` leave audio nothing plays; `tape_compact` copies out only the spans
the UI says to keep and hands back a **map** of where each one landed
(`audio/host/PROTOCOL.md` § `tape_compact`, `tape-streaming-design.md` § 3.8).
The host's half is `audio/tests/host_compact.py`.

The half here is the one the host cannot do, and T9 (§ 4's table) is its test:
the UI holds two references the audio process has never seen — the **lift
buffer** (what LIFT took off the timeline and DROP puts back) and the **one
undo record** — so if the keep set misses one, pressing undo after a compaction
brings back a clip that plays somebody else's take.

Three rigs, on purpose:

  * `TestKeepSet` / `TestRemap` drive `Tape` alone. No host, no card: the keep
    set and the map arithmetic are pure and are tested as such.
  * `TestTrigger` binds the real `App` methods to a stand-in, the way
    `test_tape_flags.py` does with `_absorb_pushes` — the threshold, the queue
    and "the host takes one job at a time" need a state document and a reply,
    not an audio process.
  * `TestCompactionOnDisk` is T9 end to end against a real host started with a
    `--tape-dir` of its own, because **nothing in the shipping emulator passes
    one until C1 step 3b** (#157 owns where a tape file lives). The audio is
    compared against a copy of the medium taken before anything moved.
"""

import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import tape as T                                     # noqa: E402
from app.app import App, SAMPLE_RATE                          # noqa: E402
from app.keys import note_key                                 # noqa: E402
from testing.scripted_input import ScriptedInput               # noqa: E402

from tests.native_support import library_copy, native_or_skip  # noqa: E402

BLOCK = 256
BOXES = os.path.join(REPO, "audio", "build", "boxes")
RANGE = 49152                       # Tape::kRangeFrames, 1.024 s at 48 kHz


def clip(start, length, src_offset=None, src_track=None, gain=1.0):
    return T.Clip(start, length, gain, src_offset, src_track)


class TestKeepSet(unittest.TestCase):
    """`keep` is every span the UI references, including the two the host
    cannot see."""

    def setUp(self):
        self.tape = T.Tape()

    def test_a_clip_on_another_track_reading_this_medium_is_in_it(self):
        """A clip says which medium it reads (`src_track`), so the sweep is
        over all eight lists and not over the medium's own track."""
        self.tape.tracks[0] = [clip(0, 100, src_offset=500)]
        self.tape.tracks[3] = [clip(0, 200, src_offset=900, src_track=0)]
        self.assertEqual(self.tape.keep_spans(0),
                         [{"src_offset": 500, "len": 100},
                          {"src_offset": 900, "len": 200}])
        self.assertEqual(self.tape.keep_spans(3), [])

    def test_the_lift_buffer_is_in_it_with_no_clip_on_the_timeline(self):
        """T9's shape: LIFT with no DROP leaves the only reference off the
        timeline entirely."""
        held = clip(0, 300, src_offset=1000, src_track=0)
        self.tape.lift_buffer = T.LiftBuffer("held", clip=held,
                                             source_track=0, clips=[held],
                                             lanes=[0])
        self.assertEqual(self.tape.keep_spans(0),
                         [{"src_offset": 1000, "len": 300}])

    def test_the_undo_record_is_in_it(self):
        """The one-level undo (DDR-054/055) points at a take the live lists no
        longer name — compact it away and undo plays somebody else's audio."""
        self.tape.tracks[0] = [clip(0, 100, src_offset=0)]
        self.tape.push_undo()
        self.tape.tracks[0] = []
        self.assertEqual(self.tape.keep_spans(0),
                         [{"src_offset": 0, "len": 100}])

    def test_touching_spans_coalesce(self):
        """One row per run of kept audio, so the remap is a lookup."""
        self.tape.tracks[0] = [clip(0, 100, src_offset=0),
                               clip(100, 100, src_offset=100),
                               clip(400, 50, src_offset=4000)]
        self.assertEqual(self.tape.keep_spans(0),
                         [{"src_offset": 0, "len": 200},
                          {"src_offset": 4000, "len": 50}])

    def test_an_overlapping_span_does_not_shorten_the_one_it_is_inside(self):
        """Two clips reading the same take, one inside the other's span."""
        self.tape.tracks[0] = [clip(0, 1000, src_offset=0),
                               clip(2000, 100, src_offset=200)]
        self.assertEqual(self.tape.keep_spans(0),
                         [{"src_offset": 0, "len": 1000}])

    def test_a_clip_with_no_explicit_address_reads_where_it_plays(self):
        """`src_offset` None is "the same as where I play" — a fresh take."""
        self.tape.tracks[2] = [clip(7000, 500)]
        self.assertEqual(self.tape.keep_spans(2),
                         [{"src_offset": 7000, "len": 500}])


class TestRemap(unittest.TestCase):
    """`from + d` becomes `to + d`, for every reference the UI holds."""

    def setUp(self):
        self.tape = T.Tape()

    def test_a_clip_and_the_undo_record_move_together(self):
        self.tape.tracks[0] = [clip(0, 100, src_offset=1000)]
        self.tape.push_undo()
        self.tape.tracks[0] = [clip(0, 100, src_offset=1000),
                               clip(500, 100, src_offset=1050)]
        orphans = self.tape.remap_medium(
            0, [{"from": 1000, "to": 0, "len": 150}], 150)
        self.assertEqual(orphans, 0)
        self.assertEqual([c.src for c in self.tape.tracks[0]], [0, 50])
        self.assertEqual([c.src for c in self.tape._undo[0]], [0])

    def test_the_lift_buffer_moves_and_its_shared_clip_moves_once(self):
        """LIFT ALL puts the **same** object in `clips` and in `clip`; remap it
        twice and the address is doubly displaced.

        The negative control for the identity rule is
        `test_lift_keeps_two_objects_and_both_move`, below: `do_lift` holds two
        *different* objects at the same address and both are real references.
        """
        held = clip(0, 200, src_offset=4000, src_track=1)
        self.tape.lift_buffer = T.LiftBuffer("held", clip=held,
                                             source_track=1, clips=[held],
                                             lanes=[1])
        self.tape.remap_medium(1, [{"from": 4000, "to": 1000, "len": 200}], 1200)
        self.assertEqual(held.src, 1000)

    def test_lift_keeps_two_objects_and_both_move(self):
        a = clip(0, 200, src_offset=4000, src_track=1)
        b = clip(0, 200, src_offset=4000, src_track=1)     # `do_lift`'s copy
        self.tape.lift_buffer = T.LiftBuffer("held", clip=b, source_track=1,
                                             clips=[a], lanes=[1])
        self.tape.remap_medium(1, [{"from": 4000, "to": 1000, "len": 200}], 1200)
        self.assertEqual((a.src, b.src), (1000, 1000))

    def test_the_address_becomes_explicit(self):
        """`src_track` None means "my own track", which a compaction has just
        made untrue for a clip that has moved lane."""
        self.tape.tracks[2] = [clip(7000, 500)]
        self.tape.remap_medium(2, [{"from": 7000, "to": 0, "len": 500}], 500)
        c = self.tape.tracks[2][0]
        self.assertEqual((c.src_offset, c.src_track), (0, 2))

    def test_a_reference_the_map_misses_is_parked_past_the_end(self):
        """The keep set missed one. Silence is the only honest answer: clamping
        it into the medium would play the audio that is now at that offset."""
        self.tape.tracks[0] = [clip(0, 100, src_offset=9000)]
        orphans = self.tape.remap_medium(
            0, [{"from": 1000, "to": 0, "len": 500}], 500)
        self.assertEqual(orphans, 1)
        self.assertEqual(self.tape.compact_orphans, 1)
        self.assertGreaterEqual(self.tape.tracks[0][0].src, 500)

    def test_a_half_covered_reference_is_an_orphan_too(self):
        """Its tail would land on the next kept run's audio."""
        self.tape.tracks[0] = [clip(0, 400, src_offset=1000)]
        orphans = self.tape.remap_medium(
            0, [{"from": 1000, "to": 0, "len": 200},
                {"from": 5000, "to": 200, "len": 200}], 400)
        self.assertEqual(orphans, 1)
        self.assertGreaterEqual(self.tape.tracks[0][0].src, 400)

    def test_another_track_medium_is_left_alone(self):
        self.tape.tracks[0] = [clip(0, 100, src_offset=1000)]
        self.tape.tracks[1] = [clip(0, 100, src_offset=1000)]
        self.tape.remap_medium(0, [{"from": 1000, "to": 0, "len": 100}], 100)
        self.assertEqual(self.tape.tracks[0][0].src, 0)
        self.assertEqual(self.tape.tracks[1][0].src, 1000)
        self.assertIsNone(self.tape.tracks[1][0].src_track)


class _Stand:
    """Enough of `App` for the three real trigger methods to run.

    The state document is the host's; `sent` is what went out. `busy` makes the
    host answer `BUSY`, which is what a second job in one SAVE really gets.
    """

    compact_dead_media = App.compact_dead_media
    _compact_next = App._compact_next
    _native_compact = App._native_compact

    def __init__(self, tracks, ranges=117, busy=False, reentrant=False):
        self.tape = T.Tape()
        self.sent = []
        self.busy = busy
        self.reentrant = reentrant
        self._compact_queue = []
        self._compacting = -1
        self._clip_fingerprint = ("stale",)
        self.native = self
        self.refreshes = 0
        self.state = {"tape": {"pool": {"ranges": ranges}, "tracks": tracks}}

    # -- the NativeAudio surface the methods touch
    def refresh(self):
        self.refreshes += 1
        return self.state

    def tape_compact(self, track, keep):
        self.sent.append((int(track), list(keep)))
        if self.busy and len(self.sent) > 1:
            return {"type": "error", "code": "bad_request", "message": "BUSY"}
        if self.reentrant:
            # Offline there is no I/O thread: the copy runs inside the request,
            # so the result push is already in the buffer when the reply comes
            # back and this re-enters before the send returns.
            self._native_compact({"type": "tape_compact", "track": int(track),
                                  "map": [], "frames": 0})
        return {"type": "tape_compact", "track": int(track), "started": True}


def doc(used, dead):
    return {"used_frames": used, "dead_frames": dead}


class TestTrigger(unittest.TestCase):
    """§ 3.8's SAVE trigger: more than half the medium dead."""

    def test_over_half_is_asked_and_exactly_half_is_not(self):
        s = _Stand([doc(1000, 501), doc(1000, 500), doc(1000, 0),
                    doc(0, 0)] + [doc(0, 0)] * 4)
        self.assertEqual(s.compact_dead_media(), [0])
        self.assertEqual([t for t, _ in s.sent], [0])

    def test_with_no_medium_on_storage_nothing_is_sent(self):
        """`pool.ranges` 0 is "no tape on the card", which is every stock
        emulator until step 3b: the trigger must not ask and must not put
        `NO TAPE DIR` in front of a player."""
        s = _Stand([doc(1000, 900)] + [doc(0, 0)] * 7, ranges=0)
        self.assertEqual(s.compact_dead_media(), [])
        self.assertEqual(s.sent, [])
        self.assertEqual(s.refreshes, 0)

    def test_the_host_is_given_one_job_at_a_time(self):
        """Three half-dead media in one SAVE: the host runs one compaction
        (`BUSY`), so the rest wait for the result push."""
        s = _Stand([doc(1000, 900), doc(1000, 900), doc(1000, 900)]
                   + [doc(0, 0)] * 5)
        self.assertEqual(s.compact_dead_media(), [0, 1, 2])
        self.assertEqual([t for t, _ in s.sent], [0])
        self.assertEqual(s._compacting, 0)

        s._native_compact({"type": "tape_compact", "track": 0,
                           "map": [], "frames": 0})
        self.assertEqual([t for t, _ in s.sent], [0, 1])
        s._native_compact({"type": "tape_compact", "track": 1,
                           "cancelled": True})
        self.assertEqual([t for t, _ in s.sent], [0, 1, 2])

    def test_offline_the_result_arrives_before_the_reply(self):
        """The queue has to survive re-entry, or one finished job blocks the
        rest of the SAVE for ever."""
        s = _Stand([doc(1000, 900)] * 3 + [doc(0, 0)] * 5, reentrant=True)
        self.assertEqual(s.compact_dead_media(), [0, 1, 2])
        self.assertEqual([t for t, _ in s.sent], [0, 1, 2])
        self.assertEqual(s._compacting, -1)
        self.assertEqual(s._compact_queue, [])

    def test_a_cancelled_push_moves_nothing(self):
        """PLAY or REC arrived: the old medium was never touched.

        The map here is belt and braces — the host sends a `cancelled` push
        without one — and it is what makes `cancelled` decisive rather than
        incidentally covered by the absence of a map.
        """
        s = _Stand([doc(1000, 900)] + [doc(0, 0)] * 7)
        s.tape.tracks[0] = [clip(0, 100, src_offset=1000)]
        s._native_compact({"type": "tape_compact", "track": 0,
                           "cancelled": True, "frames": 100,
                           "map": [{"from": 1000, "to": 0, "len": 100}]})
        self.assertEqual(s.tape.tracks[0][0].src, 1000)
        self.assertEqual(s._clip_fingerprint, ("stale",))

    def test_an_error_push_moves_nothing(self):
        s = _Stand([doc(1000, 900)] + [doc(0, 0)] * 7)
        s.tape.tracks[0] = [clip(0, 100, src_offset=1000)]
        s._native_compact({"type": "tape_compact", "track": 0,
                           "error": "COMPACT FAILED"})
        self.assertEqual(s.tape.tracks[0][0].src, 1000)

    def test_a_map_remaps_and_forces_the_clip_list_down(self):
        """The host is still holding the pre-compaction list, so the diff is
        not allowed to decide."""
        s = _Stand([doc(1000, 900)] + [doc(0, 0)] * 7)
        s.tape.tracks[0] = [clip(0, 100, src_offset=1000)]
        s._native_compact({"type": "tape_compact", "track": 0, "frames": 100,
                           "map": [{"from": 1000, "to": 0, "len": 100}]})
        self.assertEqual(s.tape.tracks[0][0].src, 0)
        self.assertIsNone(s._clip_fingerprint)

    def test_a_refused_request_frees_the_slot_for_the_next_trigger(self):
        s = _Stand([doc(1000, 900), doc(1000, 900)] + [doc(0, 0)] * 6,
                   busy=True)
        s.compact_dead_media()
        s._native_compact({"type": "tape_compact", "track": 0, "frames": 0,
                           "map": []})
        # track 1 was refused BUSY; nothing is left running
        self.assertEqual([t for t, _ in s.sent], [0, 1])
        self.assertEqual(s._compacting, -1)
        self.assertEqual(s._compact_queue, [])


class TestCompactionOnDisk(unittest.TestCase):
    """T9 against a real host with a medium on disk.

    Two takes on one track, the second absorbing the first's clip, then LIFT:
    the only references left to the surviving take are the lift buffer and the
    undo record, which is exactly the case § 3.8 says the host cannot see.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arbutus-compact-ui-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.media = os.path.join(self.tmp, "media")
        self.state = os.path.join(self.tmp, "state")
        #: SAVE writes a project into the library, so the library is a
        #: throwaway copy: a test never leaves a folder in the repo's own.
        self.lib_root = os.path.join(self.tmp, "library")
        library_copy(self.lib_root)
        self.native = native_or_skip(
            self, offline=True, block=BLOCK, libraries=[BOXES, self.lib_root],
            tape_seconds=30.0, extra=["--tape-dir", self.media])
        self.addCleanup(self.native.close)

        from app.native_library import NativeLibrary
        from drivers.native_backend import NativeOfflineAudio
        self.app = App(sample_rate=SAMPLE_RATE, fresh=True,
                       state_dir=self.state, native=self.native,
                       library=NativeLibrary(self.native, root=self.lib_root))
        self.audio = NativeOfflineAudio(self.native, blocksize=BLOCK)
        self.audio.initialize(SAMPLE_RATE, 2, None)
        self.audio.start()
        self.input = ScriptedInput()
        self.input.initialize()
        self.input.on_control = self.app.on_key
        self.input.on_encoder_turn = self.app.on_encoder

    # -- the seam ---------------------------------------------------------
    def key(self, name, down=None):
        name = str(getattr(name, "value", name))
        if down is None:
            evs = [("key", name, True), ("key", name, False)]
        else:
            evs = [("key", name, bool(down))]
        self.input.feed(*evs)
        self.input.poll_events()

    def run_blocks(self, n):
        block = self.audio.pull(n)
        self.app.tick()
        return block

    def record_take(self, blocks, idx):
        self.key("TRACK")
        self.key("REC", down=True)
        self.key(note_key(idx), down=True)
        self.run_blocks(blocks)
        self.key(note_key(idx), down=False)
        self.key("REC", down=False)
        self.run_blocks(2)
        self.key("STOP")
        self.run_blocks(2)

    def used(self, track=0):
        doc = self.native.refresh()["tape"]["tracks"][track]
        return int(doc["used_frames"]), int(doc["dead_frames"])

    def medium_copy(self, name):
        """An independent copy of the whole medium, as (frames, 2) float32.

        `tape_save` writes float32 (WAV format tag 3), which `wave` will not
        read — so the chunks are walked here, as `audio/tests/hostcase.py` does
        for the same reason.
        """
        import struct

        path = os.path.join(self.tmp, name)
        reply = self.native.tape_save(0, path)
        self.assertNotEqual(reply.get("type"), "error", reply)
        with open(path, "rb") as f:
            b = f.read()
        self.assertEqual((b[:4], b[8:12]), (b"RIFF", b"WAVE"))
        at, fmt, chans, data = 12, None, None, None
        while at + 8 <= len(b):
            cid = b[at:at + 4]
            n = struct.unpack_from("<I", b, at + 4)[0]
            body = at + 8
            if cid == b"fmt ":
                fmt, chans = struct.unpack_from("<HH", b, body)
            elif cid == b"data":
                data = (body, n)
            at = body + n + (n & 1)
        self.assertEqual((fmt, chans), (3, 2), "the medium should be float32 stereo")
        self.assertIsNotNone(data)
        body, n = data
        return np.frombuffer(b, dtype="<f4", count=n // 4,
                             offset=body).reshape(-1, 2)

    def peaks_at(self, src_offset, length, bins=64):
        """The medium's own audio at an address, as min/max pairs.

        The engine's release tail sounds over an empty timeline, so rendering
        the transport says nothing about what a clip reads; `clip_peaks` asks
        the medium and nothing else. It also crosses #163's peaks sidecar,
        which a compaction drops and which heals through the fetch path.
        """
        reply = self.native.clip_peaks(0, int(src_offset), int(length), bins)
        self.assertIsNotNone(reply)
        pairs = np.asarray([reply.get("min") or [], reply.get("max") or []],
                           dtype=np.float32)
        self.assertEqual(pairs.shape, (2, bins), reply)
        return pairs

    def lift_the_take(self):
        """LIFT the surviving clip: the timeline is empty and the buffer and
        the undo record are the only references to its audio."""
        tp = self.app.tape
        tp.selected = 0
        tp.seek(tp.tracks[0][0].start + 10)
        tp.do_lift()
        self.run_blocks(2)                  # the empty list goes down
        self.assertEqual(tp.tracks[0], [])
        self.assertIsNotNone(tp.lift_buffer)
        return tp

    def two_takes(self):
        """-> (first, used): a retake whose clip absorbs the first take's."""
        blocks = (RANGE + RANGE // 2) // BLOCK          # ~1.5 ranges a take
        self.record_take(blocks, 7)
        first, _ = self.used()
        self.assertGreater(first, RANGE)
        self.app.tape.seek(0)
        self.record_take(blocks, 9)
        used, _ = self.used()
        self.assertGreater(used, first)
        self.assertEqual(len(self.app.tape.tracks[0]), 1,
                         "the retake should have absorbed the first clip")
        return first, used

    # -- T9 ---------------------------------------------------------------
    def test_t9_the_lift_buffer_survives_a_compaction(self):
        first, used = self.two_takes()
        tape = self.lift_the_take()
        before = self.medium_copy("before.wav")
        self.assertEqual(len(before), used)
        held = tape.lift_buffer.clip
        was_loud = self.peaks_at(first, held.length)
        self.assertGreater(float(np.max(np.abs(was_loud))), 1e-3,
                           "the lifted take is silent — the test proves nothing")

        # The keep set holds the take the *lift buffer* points at, although no
        # clip on any track names it any more. That is T9's whole claim.
        keep = tape.keep_spans(0)
        self.assertEqual(keep, [{"src_offset": first, "len": held.length}])
        used_now, dead = self.used()
        self.assertEqual(used_now, used)
        self.assertEqual(dead, used, "the host sees no clip, so it calls it all dead")

        self.assertEqual(self.app.compact_dead_media(), [0])
        self.run_blocks(2)                  # the result push, and the remap
        self.assertEqual(tape.compact_orphans, 0)
        self.assertEqual(held.src, 0, "the kept run should start the new medium")
        self.assertEqual(tape.lift_buffer.clips[0].src, 0)
        self.assertEqual([c.src for c in tape._undo[0]], [0])

        # The medium on the card is the compacted one, and the audio the buffer
        # points at now is the audio it pointed at before — against a copy of
        # the medium taken before anything moved, which is an independent file.
        after = self.medium_copy("after.wav")
        self.assertEqual(len(after), held.length)
        self.assertTrue(np.array_equal(after[held.src:held.src + held.length],
                                       before[first:first + held.length]))
        # the same compare one frame out has to fail, or "it matched" says
        # nothing (#56). The window is taken mid-take, where the note sounds.
        mid = held.length // 2
        self.assertFalse(np.array_equal(after[mid:mid + 2000],
                                        before[first + mid + 1:first + mid + 2001]),
                         "the shifted compare matched — the check proves nothing")
        self.assertTrue(np.allclose(self.peaks_at(held.src, held.length),
                                    was_loud, atol=1e-6),
                        "the remapped address reads different audio")
        # Since #297 the App points the tape at its own working folder on
        # start, so the medium is there and not in the host's starting folder.
        folder = self.app.project.tape_folder
        self.assertEqual(sorted(n for n in os.listdir(folder)
                                if n.startswith("tape-") and n != "tape.json"),
                         ["tape-1.wav"])

    def test_save_starts_a_compaction(self):
        """§ 3.8's trigger, through the real `project_file.save`.

        After the LIFT no clip names the take, so the host calls the whole
        medium dead — over half — and SAVE hands it over. SAVE itself does not
        wait: the map comes back as a push and the remap lands on a later tick.
        """
        from app import project_file

        first, used = self.two_takes()
        tape = self.lift_the_take()
        held = tape.lift_buffer.clip
        folder = project_file.save(self.app)
        self.assertTrue(os.path.isdir(folder))
        self.run_blocks(2)
        self.assertEqual(held.src, 0)
        self.assertEqual(tape.compact_orphans, 0)
        self.assertEqual(int(self.used()[0]), held.length)

    def test_t9_negative_control_a_missed_reference_points_past_the_end(self):
        """The same rig with the lift buffer left **out** of `keep`.

        The kept span is the *dead* take, so a remap that clamped its reference
        into the medium instead of parking it past the end would play that take
        — somebody else's audio, which is the outcome § 3.8 exists to prevent.
        """
        first, used = self.two_takes()
        tape = self.lift_the_take()
        before = self.medium_copy("before.wav")
        held = tape.lift_buffer.clip

        reply = self.native.tape_compact(0, [{"src_offset": 0, "len": first}])
        self.assertTrue(reply.get("started"), reply)
        self.run_blocks(2)                  # the result push, and the remap

        # Three references, all to the same take: `do_lift` keeps the removed
        # clip *and* the copy DROP lays down, and the undo snapshot holds a
        # third. Every one of them has to be parked, not clamped.
        self.assertEqual(tape.compact_orphans, 3)
        self.assertEqual(len(tape.medium_clips(0)), 3)

        after = self.medium_copy("after.wav")
        self.assertEqual(len(after), first)
        self.assertTrue(np.array_equal(after, before[:first]),
                        "the kept (dead) take is what survived")
        self.assertGreaterEqual(held.src, len(after))
        self.assertEqual(held.src, int(self.used()[0]))
        silence = self.peaks_at(held.src, held.length)
        self.assertEqual(float(np.max(np.abs(silence))), 0.0,
                         "a reference the map missed must read as silence")

if __name__ == "__main__":
    unittest.main()
