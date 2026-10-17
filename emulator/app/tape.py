"""8-track tape: the clip model, the pure clip ops, the transport (workflow §2).

The clip list is the only truth and it is **geometry**: where a clip plays,
how long it is, how loud, and where its audio sits in the audio process's
medium (`src_offset` / `src_track`, `audio/host/PROTOCOL.md` § Tape). The
samples themselves never come up here — the host renders the list, and TRACK
draws a clip by asking for its peaks.

Every editing gesture is a *pure function* `List[Clip] -> List[Clip]` at module
level; `Tape` is the thin stateful shell that snapshots for undo, calls one,
and stores the result. The three ops that change *audio* rather than geometry —
REV, SPEED and FILTER committed while stopped, and TAPE joining clips that are
not contiguous in the medium — are one `tape_edit` message each, and the result
is a new address for the clip to read.
"""

import copy
import math
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

SAMPLE_RATE = 48000
#: DDR-060: the tape is 8 tracks in two banks of four. The model is always
#: 8-wide; TRACK draws one bank at a time and the host has been 8-wide since
#: stage E1.
NUM_TRACKS = 8
BANK_SIZE = 4
NUM_BANKS = NUM_TRACKS // BANK_SIZE
#: #1039: the timeline is the audio host's tape, DDR-072's 8 minutes a track
#: (`device-os-spec.md` § 3.3). The one value is the host's `--tape-seconds`
#: default (`audio/host/audio_host.hpp`); this copy is the UI's ruler, and
#: `tests/test_tape_length.py` fails when the two disagree. A spawned host
#: gets no `--tape-seconds` unless a caller asks (`NativeAudio`).
TRACK_SECONDS = 480.0
TRACK_LEN = int(TRACK_SECONDS * SAMPLE_RATE)      # the timeline, in samples
MERGE_GAP_S = 0.010                               # TAPE merge window (§2.1)
EDGE_TOL_S = 0.010                                # cursor-on-marker tolerance
#: frames per bin when TRACK asks the host for a clip's waveform, and the
#: resolution the rolling take is drawn at — finer than a pixel at any zoom.
REC_PEAK_BUCKET = 256

# Audible scrub: moving the cursor while stopped plays a short grain of what
# is under it, at 1x pitch, so an edit point can be found by ear. The grain is
# rendered by the host (`scrub`); the geometry is decided here.
SCRUB_GRAIN_S = 0.12

STOPPED, PLAYING, RECORDING = "STOPPED", "PLAYING", "RECORDING"

# ---------------------------------------------------------------------------
# the tick ladder (DDR-058, workflow §2.3)
# ---------------------------------------------------------------------------
#: Musical rungs, in bars, coarse to fine. Each is half the one before, so the
#: tick count doubles per rung and a window can always be covered by 8..16.
#: DDR-058 names 8 bars as the coarsest; 32 and 16 are #1039's, so the whole
#: 480 s tape keeps at most 16 ticks up to 240 BPM (480 bars / 32 = 15).
BAR_RUNGS = (32.0, 16.0, 8.0, 4.0, 2.0, 1.0, 0.5, 0.25, 0.125, 1.0 / 16.0, 1.0 / 32.0)
#: Below the musical rungs the ruler counts time. The spec names 1 s -> 100 ms
#: -> 10 ms -> 1 ms -> samples; bare decades jump by 10 and cannot keep 8..16
#: ticks on screen, so the rungs are 1-2-5 per decade (which contains the
#: spec's four) down to a single sample.
TIME_RUNGS = tuple(
    m * 10.0 ** e
    for e in range(0, -6, -1)
    for m in (5.0, 2.0, 1.0)
)
TICKS_MAX = 16                # DDR-058: 8..16 ticks on screen


class Tick:
    """One rung of the ladder: how long a tick is, and what it means.

    `kind` is `"bar"` or `"time"`; `size` is bars per tick or seconds per tick.
    `labelled` is true only for whole-bar rungs — sub-bar ticks are unlabelled
    (workflow §2.3), which is also what keeps the ruler legible at 6 px a glyph.
    """

    __slots__ = ("samples", "kind", "size")

    def __init__(self, samples: float, kind: str, size: float):
        self.samples = float(samples)
        self.kind = kind
        self.size = float(size)

    @property
    def labelled(self) -> bool:
        return self.kind == "bar" and self.size >= 1.0

    def __repr__(self):                       # test failures read better
        return f"Tick({self.samples:.1f}, {self.kind}, {self.size:g})"

    def __eq__(self, other):
        return (isinstance(other, Tick) and self.kind == other.kind
                and abs(self.samples - other.samples) < 1e-6
                and abs(self.size - other.size) < 1e-9)


def tick_ladder(view_len: float, bar_samples: float,
                sample_rate: int = SAMPLE_RATE) -> Tick:
    """The finest rung that still keeps at most `TICKS_MAX` ticks on screen.

    Zoom is the precision control (DDR-058): one SHIFT+‹ › is one tick and
    REC+note snaps to one, so this function is what a punch-in lands on.

    Musical rungs first, time rungs below the finest of them — the crossover
    is where the bars run out (1/32 of a bar, ~62 ms at 120 BPM) rather than a
    fixed 250 ms, because the 8..16 rule is the one the spec asks to keep and
    a fixed crossover leaves a window where neither ladder can.
    """
    view_len = max(1.0, float(view_len))
    bar_samples = max(1.0, float(bar_samples))
    rungs = [(r * bar_samples, "bar", r) for r in BAR_RUNGS]
    finest_bar = rungs[-1][0]
    rungs += [(r * sample_rate, "time", r) for r in TIME_RUNGS
              if r * sample_rate < finest_bar]
    best = rungs[0]
    for rung in rungs:
        if rung[0] < 1.0:                     # finer than one sample
            break
        if view_len / rung[0] > TICKS_MAX:
            break
        best = rung
    return Tick(*best)

SPEED_STEPS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
SPEED_MIN, SPEED_MAX = 0.25, 4.0

FILTER_MIN_HZ, FILTER_MAX_HZ = 80.0, 12000.0
FILTER_CENTRE_HZ = 2000.0


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

@dataclass
class Clip:
    """Where a clip plays, and where it reads.

    `src_offset` / `src_track` are the audio's address in the host's medium:
    a clip that has been moved, sliced, copied or dropped on another track has
    to say where its sound is, because the list is all the host gets. `None`
    means "the same as where I play" / "my own track", which is what a fresh
    take is. Every pure op below propagates them.
    """

    start: int                  # sample position on the track
    length: int                 # frames
    gain: float = 1.0
    src_offset: Optional[int] = None
    src_track: Optional[int] = None

    @property
    def end(self) -> int:
        return self.start + self.length

    @property
    def src(self) -> int:
        return self.start if self.src_offset is None else int(self.src_offset)

    def copy(self) -> "Clip":
        return Clip(self.start, self.length, self.gain,
                    self.src_offset, self.src_track)

    def on(self, track: int) -> int:
        """Which medium this clip reads when it sits on `track`."""
        return track if self.src_track is None else int(self.src_track)


@dataclass
class LiftBuffer:
    """What LIFT is holding, plus the strip text DDR-024 demands."""
    description: str
    clip: Optional[Clip] = None          # the one DROP lays down (its address)
    source_track: Optional[int] = None
    clips: List[Clip] = field(default_factory=list)   # LIFT ALL keeps them all
    #: the lane each of `clips` came off, so a LIFT ALL buffer can drop each
    #: clip back onto **its own** track (§2.1). `src_track` is the medium the
    #: clip reads, which is a different question.
    lanes: List[int] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.clip.length if self.clip is not None else 0

    @property
    def seconds(self) -> float:
        return self.length / float(SAMPLE_RATE)


# ---------------------------------------------------------------------------
# pure clip-list operations  (workflow §2.1)
# ---------------------------------------------------------------------------

def _sorted(clips: List[Clip]) -> List[Clip]:
    return sorted((c for c in clips if c.length > 0), key=lambda c: c.start)


def clip_index_at(clips: List[Clip], pos: int, tol: int = 0) -> Optional[int]:
    """Index of the clip under the cursor, or None.

    `tol` (samples) lets the cursor sit just off a clip edge and still count as
    on it — parking exactly on a marker after a slice is the common case.
    The nearest edge wins when two clips are both within tolerance.
    """
    for i, c in enumerate(clips):
        if c.start <= pos < c.end:
            return i
    if tol <= 0:
        return None
    best, best_d = None, tol + 1
    for i, c in enumerate(clips):
        d = c.start - pos if pos < c.start else pos - c.end + 1
        if 0 < d <= tol and d < best_d:
            best, best_d = i, d
    return best


def cut_range(clips: List[Clip], start: int, end: int) -> List[Clip]:
    """Punch [start, end) out of the clip list, trimming/splitting survivors."""
    out: List[Clip] = []
    for c in clips:
        if c.end <= start or c.start >= end:
            out.append(c)
            continue
        if c.start < start:                       # head survives
            out.append(Clip(c.start, start - c.start, c.gain,
                            c.src, c.src_track))
        if c.end > end:                           # tail survives
            out.append(Clip(end, c.end - end, c.gain,
                            c.src + (end - c.start), c.src_track))
    return _sorted(out)


def record_commit(clips: List[Clip], start: int, length: int,
                  gain: float = 1.0, src_offset: Optional[int] = None,
                  src_track: Optional[int] = None) -> List[Clip]:
    """Commit a take at `start`, replacing whatever it overlaps (replace mode)."""
    if length <= 0:
        return _sorted(list(clips))
    kept = cut_range(clips, start, start + length)
    return _sorted(kept + [Clip(start, length, gain, src_offset, src_track)])


def slice_at(clips: List[Clip], pos: int) -> List[Clip]:
    """Split the clip under the playhead in two."""
    i = clip_index_at(clips, pos)
    if i is None:
        return _sorted(list(clips))
    c = clips[i]
    off = pos - c.start
    if off <= 0 or off >= c.length:
        return _sorted(list(clips))
    out = list(clips)
    out[i:i + 1] = [Clip(c.start, off, c.gain, c.src, c.src_track),
                    Clip(pos, c.length - off, c.gain, c.src + off, c.src_track)]
    return _sorted(out)


def merge_groups(clips: List[Clip], gap_samples: int = None) -> List[List[Clip]]:
    """TAPE, as geometry: the runs of clips a merge would join into one."""
    if gap_samples is None:
        gap_samples = int(MERGE_GAP_S * SAMPLE_RATE)
    groups: List[List[Clip]] = []
    for c in _sorted(list(clips)):
        if groups:
            prev = groups[-1][-1]
            gap = c.start - prev.end
            if 0 <= gap < gap_samples:
                groups[-1].append(c)
                continue
        groups.append([c])
    return groups


def contiguous(group: List[Clip]) -> bool:
    """True when one clip can span the whole group without moving any audio.

    Butted clips off one run of the medium — a take and its slices — join by
    arithmetic. A gap cannot: the medium holds *something* there (the next
    take, usually) and the merge is supposed to leave silence, so that one is
    a bounce.
    """
    first = group[0]
    prev = None
    for c in group:
        if c.src_track != first.src_track or c.gain != first.gain:
            return False
        if c.src != first.src + (c.start - first.start):
            return False
        if prev is not None and c.start != prev.end:
            return False
        prev = c
    return True


def lift(clips: List[Clip], pos: int) -> Tuple[List[Clip], Optional[Clip]]:
    """Remove the clip under the cursor and hand it back (DDR-012/013)."""
    i = clip_index_at(clips, pos)
    if i is None:
        return _sorted(list(clips)), None
    taken = clips[i].copy()
    out = [c for j, c in enumerate(clips) if j != i]
    return _sorted(out), taken


def drop(clips: List[Clip], length: int, pos: int, gain: float = 1.0,
         src_offset: Optional[int] = None,
         src_track: Optional[int] = None) -> List[Clip]:
    """Insert what LIFT is holding at the playhead, replacing what it overlaps."""
    return record_commit(clips, pos, length, gain, src_offset, src_track)


def slide(clips: List[Clip], pos: int, delta: int) -> List[Clip]:
    """Move the clip under the cursor until it touches a neighbour."""
    i = clip_index_at(clips, pos)
    if i is None or delta == 0:
        return _sorted(list(clips))
    src = _sorted(list(clips))
    i = clip_index_at(src, pos)
    c = src[i]
    lo = src[i - 1].end if i > 0 else 0
    hi = (src[i + 1].start if i + 1 < len(src) else TRACK_LEN) - c.length
    new_start = int(max(lo, min(hi, c.start + delta)))
    # a moved clip keeps reading where its audio is, not where it now plays
    src[i] = Clip(new_start, c.length, c.gain, c.src, c.src_track)
    return _sorted(src)


def compaction_rows(moves) -> list:
    """A compaction map (`[{from, to, len}, ...]`) as sorted tuples; a
    malformed row is skipped."""
    rows = []
    for m in moves or []:
        try:
            rows.append((int(m["from"]), int(m["to"]), int(m["len"])))
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort()
    return rows


def compacted_address(rows, src: int, length: int):
    """Where `length` frames read from `src` landed, or None if no one kept
    run carries all of them (`Tape.remap_medium` says why it must be whole).
    Shared with the saved project's copy of the clips (#410)."""
    for frm, to, mlen in rows:
        if frm <= src and src + length <= frm + mlen:
            return to + (src - frm)
    return None


# ---------------------------------------------------------------------------
# stateful shell
# ---------------------------------------------------------------------------


class Tape:
    def __init__(self, sample_rate: int = SAMPLE_RATE, host=None):
        self.sample_rate = sample_rate
        #: the audio process. The clip list lives here and goes down as
        #: `set_clips`; the ops that change *audio* (REV, SPEED, FILTER
        #: committed, TAPE across a gap) are `tape_edit` messages.
        self.host = host
        #: #617: asked before every `tape_edit`; the App answers True (and
        #: says why on the strip) while the library is a computer's
        self.edit_refused: Callable[[], bool] = lambda: False
        self.tracks: List[List[Clip]] = [[] for _ in range(NUM_TRACKS)]
        self.selected = 0
        self.armed = [False] * NUM_TRACKS

        self.state = STOPPED
        self.playhead = 0.0                 # samples
        self.loop_in = 0
        self.loop_out = 0            # 0 = never set; no region drawn or looped
        self.loop_on = False

        self.speed = 1.0                    # TRACK ENC3, persistent
        self.momentary_speed = 1.0          # hold-SPEED, remembers last value
        self.speed_held = False
        self.rev_held = False
        self.filter_held = False
        self.filter_cutoff = FILTER_CENTRE_HZ
        self.filter_mode = "LP"
        self.scrub_dir = 0                  # ‹ › held

        #: wall time the host's grain runs out (#1123, app row 6): the host
        #: plays SCRUB_GRAIN_S from the last `scrub`, so a nudge after that
        #: must reseed or it plays on from wherever the old grain ran to
        self._scrub_until = 0.0
        self._scrub_pos = 0.0               # where the grain is reading from
        self.clock: Callable[[], float] = time.monotonic   # tests replace it

        self.lift_buffer: Optional[LiftBuffer] = None
        self._undo: Optional[List[List[Clip]]] = None
        #: references a compaction map did not cover (#164). Zero is the only
        #: correct value: anything else means `keep_spans` missed a reference
        #: this object holds, and the tests assert on it.
        self.compact_orphans = 0
        #: DDR-055: consecutive lifts with no drop between are **one** undo
        #: group. The snapshot is only taken when the group opens, so undoing
        #: after "lift, lift, oops" puts both clips back.
        self._undo_group: Optional[str] = None

        self._rec_start = 0
        self._rec_medium = 0                # where the open take writes
        self._rec_samples = 0
        #: frame the punch lands on while the click counts in (TEMPO COUNT-IN)
        self._count_in_until: Optional[int] = None

    # -- banks (DDR-060) --------------------------------------------------
    @property
    def bank(self) -> int:
        """Which bank of four TRACK is showing.

        Derived, not stored: DDR-060's SHIFT+1-4 flips the bank **and** selects
        a track in it, so the shown bank is always the selected track's. One
        fact, one home — a stored bank could disagree with the selection.
        """
        return self.selected // BANK_SIZE

    def bank_tracks(self, bank: Optional[int] = None) -> List[int]:
        b = self.bank if bank is None else int(bank)
        return list(range(b * BANK_SIZE, b * BANK_SIZE + BANK_SIZE))

    def bank_has_clips(self, bank: int) -> bool:
        """The bank glyph's condition: bank B is revealed once it is used."""
        return any(self.tracks[t] for t in self.bank_tracks(bank))

    def select_in_bank(self, n: int):
        """Plain 1-4: select within the shown bank."""
        self.selected = self.bank * BANK_SIZE + max(0, min(BANK_SIZE - 1, int(n)))

    def flip_bank_select(self, n: int):
        """SHIFT+1-4: flip to the other bank *and* select that track."""
        other = (self.bank + 1) % NUM_BANKS
        self.selected = other * BANK_SIZE + max(0, min(BANK_SIZE - 1, int(n)))

    # -- undo -------------------------------------------------------------
    def push_undo(self, group: Optional[str] = None):
        """Snapshot for SHIFT+STOP.

        `group` names an op that coalesces with itself (DDR-055's lifts): the
        second and later calls in a run keep the snapshot the first one took.
        Any other op closes the group.
        """
        if group is not None and group == self._undo_group and self._undo is not None:
            return
        self._undo = copy.deepcopy(self.tracks)
        self._undo_group = group

    def undo(self) -> bool:
        """SHIFT+STOP — one level, tape ops only (a lift group counts as one)."""
        if self._undo is None:
            return False
        self.tracks = self._undo
        self._undo = None
        self._undo_group = None
        return True

    @property
    def can_undo(self) -> bool:
        return self._undo is not None

    # -- transport (§2.2) -------------------------------------------------
    # COUNT-IN (screen-sketches § TEMPO) is a **delayed punch**, not a
    # pre-roll: the transport rolls from where you asked, the click counts
    # `count_in` frames, and the take begins after them. A pre-roll would have
    # to locate backwards, and at the head of the tape there is nowhere to go —
    # which is exactly when a player wants the count most.
    def play(self, count_in: int = 0):
        if self.state != STOPPED:
            return
        if not self.armed[self.selected]:
            self.state = PLAYING
            return
        self.state = PLAYING
        if count_in > 0:
            self._count_in_until = int(self.playhead) + int(count_in)
            return
        self.state = RECORDING
        self._begin_take()

    def rec(self, count_in: int = 0):
        """Arm the selected track; if already rolling, punch in."""
        if self._count_in_until is not None:
            # counting in: REC again is "no, not yet after all"
            self._count_in_until = None
            self.armed[self.selected] = False
            return
        if self.state == PLAYING:
            self.armed[self.selected] = True
            if count_in > 0:
                self._count_in_until = int(self.playhead) + int(count_in)
                return
            self.state = RECORDING
            self._begin_take()
            return
        if self.state == RECORDING:
            self.state = PLAYING
            self.armed[self.selected] = False
            self._end_take()
            return
        self.armed[self.selected] = not self.armed[self.selected]

    def stop(self):
        if self.state == STOPPED:
            self.playhead = float(self.loop_in if self.loop_on else 0)
        self.state = STOPPED
        self.armed = [False] * NUM_TRACKS
        self.rev_held = False
        self._count_in_until = None
        self._end_take()

    def follow(self, playing: bool, position: float):
        """Adopt the host's transport: something else rolled or stopped it.

        The tape the UI draws is a **mirror** of the audio process's (#99).
        `arbutus-audio` serves several clients at once — the companion app has
        its own TCP connection since D2 — so a start, a stop or a locate can
        come from somewhere this UI never sees. `App._adopt_host_transport`
        reads the host's own `state` and calls this; nothing is sent back.

        A roll adopted here is never a take: recording is a gesture of this
        UI's own (REC, or REC + a note), and nothing else in v1 punches in
        (companion-app-spec.md § 1). So the state adopted is PLAYING, and an
        open take of ours that a remote stop ends is closed exactly as STOP
        closes it, minus the rewind — the playhead stays where the host left
        it, because that is the fact being adopted.
        """
        self.playhead = float(min(max(0.0, position), TRACK_LEN - 1))
        if playing == self.playing:
            return
        self._count_in_until = None
        if playing:
            self.state = PLAYING
        else:
            self.state = STOPPED
            self.armed = [False] * NUM_TRACKS
            self.rev_held = False
            self._end_take()

    # -- the count-in ------------------------------------------------------
    @property
    def counting_in(self) -> bool:
        return self._count_in_until is not None

    def count_in_left(self) -> int:
        """Frames still to run before the punch, 0 when nothing is pending."""
        if self._count_in_until is None:
            return 0
        return max(0, int(self._count_in_until - self.playhead))

    def poll_count_in(self) -> bool:
        """Called once a frame while the playhead moves; True = just punched."""
        if self._count_in_until is None or self.state != PLAYING:
            return False
        if self.playhead < self._count_in_until:
            return False
        self._count_in_until = None
        if not self.armed[self.selected]:
            return False
        self.state = RECORDING
        self._begin_take()
        return True

    @property
    def recording(self) -> bool:
        return self.state == RECORDING

    @property
    def playing(self) -> bool:
        return self.state in (PLAYING, RECORDING)

    # -- the open take ----------------------------------------------------
    # The audio goes straight into the host's medium, so all that is kept
    # here is where it started and how far it has got — which is what TRACK
    # needs to draw it while it rolls (`clip_peaks` over `take_src`).
    def _begin_take(self):
        self._rec_start = int(self.playhead)
        self._rec_samples = 0

    def feed_take_length(self, frames: int, medium: Optional[int] = None):
        if medium is not None:
            self._rec_medium = int(medium)
        if self.state == RECORDING and frames > 0:
            self._rec_samples = int(frames)

    def _end_take(self):
        """No take is open: `take_length` is 0 until the next one rolls.

        TRACK only draws the open take while `recording`, so a stale length is
        invisible — but the property says "how long the take is", and after
        STOP there is no take.
        """
        self._rec_samples = 0

    @property
    def take_start(self) -> int:
        return self._rec_start

    @property
    def take_src(self) -> int:
        """The open take's address in the host's medium."""
        return self._rec_medium

    @property
    def take_length(self) -> int:
        return self._rec_samples

    # -- speed / holds ----------------------------------------------------
    @property
    def effective_speed(self) -> float:
        if self.state == RECORDING:
            return 1.0                       # takes are captured at 1x
        return self.momentary_speed if self.speed_held else self.speed

    def hold_speed(self, held: bool):
        self.speed_held = bool(held)

    def hold_filter(self, held: bool):
        self.filter_held = bool(held)

    def hold_rev(self, held: bool):
        self.rev_held = bool(held)

    # -- cursor -----------------------------------------------------------
    def seek(self, pos: float):
        self.playhead = float(min(max(0.0, pos), TRACK_LEN - 1))

    def nudge(self, delta: float):
        self.seek(self.playhead + delta)

    # -- audible scrub ----------------------------------------------------
    def scrub_preview(self, direction: int = 1):
        """Arm/extend the scrub grain around the playhead (stopped only).

        Moving forward reads from the playhead; moving back reads the grain
        that *ends* at the playhead, so either direction auditions the audio
        you are pointing at rather than the audio you just left. The host
        renders it; this decides where it reads.
        """
        if self.playing:
            return
        grain = int(SCRUB_GRAIN_S * self.sample_rate)
        want = self.playhead if direction >= 0 else self.playhead - grain
        want = max(0.0, min(want, TRACK_LEN - 1))
        # a grain still running near the new target keeps reading where it is:
        # re-seeding every detent would click on every step of a slow turn
        now = self.clock()
        reseed = now >= self._scrub_until or abs(want - self._scrub_pos) > grain
        if reseed:
            self._scrub_pos = want
        self._scrub_until = now + SCRUB_GRAIN_S
        if self.host is not None:
            self.host.scrub(self._scrub_pos, grain, reseed)

    # -- the host's medium ------------------------------------------------
    def _edit(self, track: int, sources: List[dict], length: int,
              op: str = "copy", **kw) -> Optional[dict]:
        if self.host is None or self.edit_refused():
            return None
        reply = self.host.tape_edit(track, sources, length, op, **kw)
        if not reply or reply.get("type") == "error":
            return None
        return reply

    def _rebake(self, op: str, **kw) -> bool:
        """REV / SPEED / FILTER tapped while stopped, in the medium."""
        t = self.selected
        clips = self.tracks[t]
        i = clip_index_at(clips, self._edit_cursor(), self.edge_tol)
        if i is None:
            return False
        c = clips[i]
        got = self._edit(t, [{"track": c.on(t), "offset": c.src,
                              "len": c.length}], c.length, op, **kw)
        if got is None:
            return False
        self.push_undo()
        out = list(self.tracks[t])
        out[i] = Clip(c.start, int(got["len"]), c.gain,
                      int(got["src_offset"]), t)
        self.tracks[t] = _sorted(out)
        return True

    # -- clip ops (undo-wrapped) ------------------------------------------
    def _cursor(self) -> int:
        return int(self.playhead)

    def _edit_cursor(self, track: Optional[int] = None) -> int:
        """Cursor for ops that act on 'the clip under the playhead'.

        Parking exactly on a marker (a clip edge, e.g. right after a slice)
        leaves the playhead technically outside the clip. Within EDGE_TOL_S
        the cursor is pulled just inside the nearest clip so slide/lift/reverse
        do what the eye expects.
        """
        pos = self._cursor()
        clips = self.tracks[self.selected if track is None else track]
        i = clip_index_at(clips, pos, self.edge_tol)
        if i is None:
            return pos
        c = clips[i]
        if c.start <= pos < c.end:
            return pos
        return c.start if pos < c.start else c.end - 1

    @property
    def edge_tol(self) -> int:
        return int(EDGE_TOL_S * self.sample_rate)

    def markers(self, track: Optional[int] = None,
                bar_samples: int = 0) -> List[int]:
        """Snap targets on a track: bar ticks and clip edges, plus 0.

        Bar ticks are what you want most of the time (drop the playhead on a
        downbeat before arming); clip edges matter after a slice.
        """
        clips = self.tracks[self.selected if track is None else track]
        pts = {0}
        for c in clips:
            pts.add(int(c.start))
            pts.add(int(c.end))
        if bar_samples > 0:
            last = max(pts) if pts else 0
            span = max(last, int(self.playhead)) + 2 * bar_samples
            pts.update(range(0, min(span, TRACK_LEN), int(bar_samples)))
        return sorted(pts)

    def nudge_tick(self, direction: int, tick: float):
        """SHIFT + ‹ ›: one tick of the current ladder (DDR-057/058).

        Always one tick, loop or no loop, and **never** a clip edge: the edges
        are drawn as markers but they are not snap targets — zoom in to land on
        one (workflow §2.1). Off the grid, the first step lands on the adjacent
        tick, so a run of steps stays on it.
        """
        tick = float(tick)
        if tick <= 0 or direction == 0:
            return
        k = self.playhead / tick
        # a cursor within half a sample of a tick counts as on it, so stepping
        # back and forth is an exact round trip
        eps = 0.5 / tick
        if direction > 0:
            n = math.floor(k + eps) + 1
        else:
            n = math.ceil(k - eps) - 1
        self.seek(max(0.0, n * tick))

    def jump(self, direction: int) -> bool:
        """STOP held + ‹ ›: loop in / out with the loop on, tape ends without.

        DDR-057. Getting to a loop edge to trim it is the common tape move, and
        the destination never depends on anything but the loop flag.
        """
        if self.loop_on and self.loop_out > self.loop_in:
            self.seek(self.loop_in if direction < 0 else self.loop_out)
            return True
        if direction < 0:
            self.seek(0)
            return True
        ends = [c.end for track in self.tracks for c in track]
        self.seek(max(ends) if ends else 0)
        return True

    def _commit(self, t: int, out: List[Clip], group: Optional[str] = None) -> bool:
        """Store track `t`'s new list, taking the undo snapshot only when it
        differs: a gesture that changes nothing must not spend the one undo
        level (#1123, app row 5)."""
        if out == self.tracks[t]:
            return False
        self.push_undo(group)
        self.tracks[t] = out
        return True

    def do_slice(self):
        t = self.selected
        self._commit(t, slice_at(self.tracks[t], self._cursor()))

    def do_merge(self):
        """TAPE: join clips inside the gap window.

        A run that is already one stretch of the medium becomes one clip by
        arithmetic; anything else is bounced into a new run, the later clips
        scaled to the first's gain.
        """
        t = self.selected
        out: List[Clip] = []
        for group in merge_groups(self.tracks[t]):
            if len(group) == 1:
                out.append(group[0])
                continue
            first, last = group[0], group[-1]
            total = last.end - first.start
            if contiguous(group):
                out.append(Clip(first.start, total, first.gain,
                                first.src, first.src_track))
                continue
            base = first.gain if first.gain else 1.0
            sources = [{"track": c.on(t), "offset": c.src, "len": c.length,
                        "at": c.start - first.start, "gain": c.gain / base}
                       for c in group]
            got = self._edit(t, sources, total, "copy")
            if got is None:
                out.extend(group)
                continue
            out.append(Clip(first.start, int(got["len"]), first.gain,
                            int(got["src_offset"]), t))
        self._commit(t, _sorted(out))

    def do_lift(self):
        t = self.selected
        out, taken = lift(self.tracks[t], self._edit_cursor())
        if taken is not None:
            self._commit(t, out, "lift")
            self.seek(taken.start)          # ready to DROP it straight back
            held = Clip(taken.start, taken.length, taken.gain,
                        taken.src, taken.on(t))
            self.lift_buffer = LiftBuffer(
                f"TRK{t + 1} {taken.length / self.sample_rate:.1f}s — REMOVED",
                clip=held, source_track=t, clips=[taken], lanes=[t])

    def do_lift_all(self):
        taken: List[Clip] = []
        lanes: List[int] = []
        outs: List[List[Clip]] = []
        for t in range(NUM_TRACKS):
            out, c = lift(self.tracks[t], self._edit_cursor(t))
            outs.append(out)
            if c is not None:
                c.src_track = c.on(t)
                taken.append(c)
                lanes.append(t)
        if taken:
            self.push_undo("lift")          # nothing under the cursor: no undo spent
            self.tracks = outs
            self.seek(min(c.start for c in taken))
            longest = max(taken, key=lambda c: c.length)
            self.lift_buffer = LiftBuffer(
                f"{len(taken)} CLIP{'S' if len(taken) > 1 else ''} "
                f"{longest.length / self.sample_rate:.1f}s — REMOVED",
                clip=longest, source_track=None, clips=taken, lanes=lanes)

    def do_drop(self):
        """DROP: the buffer at the playhead (§2.1).

        A LIFT's clip goes on the **selected** track; a LIFT ALL buffer goes
        back one clip per **lane**, keeping the clips' relative offsets, so
        lifting a vertical slice and dropping it later moves the whole stack
        intact. That holds for a LIFT ALL that caught one clip too: it is
        keyed on where the buffer came from, not on its count (#1123, app
        row 11).
        """
        buf = self.lift_buffer
        if buf is None or buf.clip is None:
            return
        if buf.source_track is None and buf.lanes:
            self.push_undo()
            at = self._cursor()
            base = min(c.start for c in buf.clips)
            end = at
            for c, lane in zip(buf.clips, buf.lanes):
                pos = at + (c.start - base)
                self.tracks[lane] = drop(self.tracks[lane], c.length, pos,
                                         c.gain, c.src, c.src_track)
                end = max(end, pos + c.length)
            self.seek(end)
            return
        self._drop_on_selected(buf.clip)

    def _drop_on_selected(self, held: Clip):
        self.push_undo()
        at = self._cursor()
        t = self.selected
        # DROP lays the audio down where it already is: the clip carries its
        # address, so the same take can sit on four tracks without four copies.
        self.tracks[t] = drop(self.tracks[t], held.length, at,
                              src_offset=held.src, src_track=held.src_track)
        # the tape moves to the end of what you dropped, so pressing DROP
        # again lays the next copy down butted against it (OP-1 §8.9)
        self.seek(at + held.length)

    def do_merge_drop(self) -> bool:
        """SHIFT+DROP: a LIFT ALL buffer **mixed down** onto the selected track.

        The mix is the host's — one `tape_edit` of every held clip into a new
        run of the selected track's medium, which is the same bounce TAPE uses
        across a gap. A single-clip buffer has nothing to mix: it is laid on
        the selected track as it is.
        """
        buf = self.lift_buffer
        if buf is None or buf.clip is None:
            return False
        if len(buf.clips) < 2:
            self._drop_on_selected(buf.clip)    # onto the selected track, as a mixdown would
            return True
        got = self._bounce(self.selected, buf.clips)
        if got is None:
            return False
        offset, length = got
        self.push_undo()
        t = self.selected
        at = self._cursor()
        self.tracks[t] = drop(self.tracks[t], length, at,
                              src_offset=offset, src_track=t)
        self.seek(at + length)
        return True

    def _bounce(self, track: int, clips: List[Clip]) -> Optional[Tuple[int, int]]:
        """Mix `clips` into a fresh run of `track`'s medium -> (offset, length)."""
        if not clips:
            return None
        base = min(c.start for c in clips)
        total = max(c.end for c in clips) - base
        if total <= 0:
            return None
        sources = [{"track": c.on(track), "offset": c.src, "len": c.length,
                    "at": c.start - base, "gain": c.gain} for c in clips]
        got = self._edit(track, sources, total, "copy")
        if got is None:
            return None
        return int(got["src_offset"]), int(got["len"])

    def buffer_to_asset(self, file: Optional[str] = None) -> Optional[str]:
        """What LIFT is holding, as a WAV in the sampler's folder (DDR-030).

        `clip_to_asset` renders a window of the **timeline**, and a lifted clip
        is no longer on it — so the held audio is laid onto its own track for
        exactly the length of the call and the real list is put straight back.
        `set_clips` is literal (E1), which is what makes that safe: the host
        renders the list it was given and tidies nothing.

        `file` names where the WAV lands. The caller passes one because the
        host's default is its *first* library root, which is the box folder in
        every run — the library the browser reads is the UI's to name.
        """
        buf = self.lift_buffer
        if self.host is None or buf is None or not buf.clips:
            return None
        if len(buf.clips) == 1:
            c = buf.clips[0]
            track = c.on(buf.source_track if buf.source_track is not None
                         else self.selected)
            offset, length, gain = c.src, c.length, c.gain
        else:
            # a LIFT ALL buffer arrives as one stereo mixdown (§2.1)
            track = self.selected
            got = self._bounce(track, buf.clips)
            if got is None:
                return None
            offset, length = got
            gain = 1.0
        if length <= 0:
            return None
        self._push_clips(track, [{"start_frame": int(offset), "len": int(length),
                                  "src_offset": int(offset), "gain": float(gain),
                                  "src_track": int(track)}])
        try:
            reply = self.host.clip_to_asset(track, int(offset), int(length),
                                            file=file)
        finally:
            self._push_clips(track, None)
        if not reply or reply.get("type") == "error":
            return None
        return reply.get("path") or None

    def _push_clips(self, track: int, clips):
        """Send one track's list down; `None` means "the UI's real list"."""
        if clips is None:
            clips = [{"start_frame": int(c.start), "len": int(c.length),
                      "src_offset": int(c.src), "gain": float(c.gain),
                      "src_track": track if c.src_track is None else int(c.src_track)}
                     for c in self.tracks[track]]
        self.host.set_clips(int(track), clips)

    def do_slide(self, delta: int):
        """SHIFT+ENC1: every detent of one slide is one undo group, like
        DDR-055's lifts, so undo puts the clip back where the slide began
        (#1123, app row 5)."""
        t = self.selected
        self._commit(t, slide(self.tracks[t], self._edit_cursor(), int(delta)),
                     "slide")

    def do_reverse(self):
        self._rebake("reverse")

    def do_speed(self):
        self._rebake("speed", factor=self.momentary_speed)

    def do_bake_filter(self):
        self._rebake("filter", cutoff=self.filter_cutoff, mode=self.filter_mode)

    # -- compaction (C1 step 8's UI half, #164; design § 3.8) -------------
    # The medium -- the file one track's recorded audio lives in -- is written
    # append-only, so a retake, a LIFT with no DROP and every `tape_edit` leave
    # audio nothing plays. `tape_compact` is how that space comes back, and the
    # keep set is **this** object's to compute: two of the references belong to
    # the UI and the audio process has never seen either.
    #
    #   * the **lift buffer** -- what LIFT took off the timeline and DROP puts
    #     back. It is not a clip, so it is in no clip list the host has.
    #   * the **one undo record** (DDR-054/055) -- a whole snapshot of the eight
    #     clip lists, which may point at a take the live lists no longer do.
    #
    # Compact either away and pressing undo brings back a clip that plays
    # somebody else's take. That is why `keep` comes from here.

    def medium_clips(self, medium: int) -> List[Clip]:
        """Every clip object this UI holds that reads track `medium`'s medium.

        Identity, not value: `do_lift` keeps the removed clip *and* a copy of
        it with the address made explicit, and both are real references that
        have to move together — while `do_lift_all` puts the **same** object in
        `clips` and in `clip`, which must be remapped once and not twice.
        """
        out: List[Clip] = []
        seen = set()

        def take(clip: Optional[Clip], lane: int):
            if clip is None or id(clip) in seen:
                return
            if clip.on(lane) != medium or clip.length <= 0:
                return
            seen.add(id(clip))
            out.append(clip)

        for t, track in enumerate(self.tracks):
            for c in track:
                take(c, t)
        if self._undo is not None:
            for t, track in enumerate(self._undo):
                for c in track:
                    take(c, t)
        buf = self.lift_buffer
        if buf is not None:
            lanes = list(buf.lanes)
            fallback = (buf.source_track if buf.source_track is not None
                        else self.selected)
            for i, c in enumerate(buf.clips):
                take(c, lanes[i] if i < len(lanes) else fallback)
            take(buf.clip, fallback)
        return out

    def keep_spans(self, medium: int) -> List[dict]:
        """`tape_compact`'s `keep`: every span of `medium` this UI references.

        Sorted and coalesced here as well as in the host — two spans that touch
        become one, so the map that comes back has one row per run of kept
        audio and the remap is a lookup rather than a merge.
        """
        spans = sorted((c.src, c.length) for c in self.medium_clips(medium))
        out: List[dict] = []
        for src, length in spans:
            if out and src <= out[-1]["src_offset"] + out[-1]["len"]:
                last = out[-1]
                last["len"] = max(last["len"], src + length - last["src_offset"])
                continue
            out.append({"src_offset": int(src), "len": int(length)})
        return out

    def remap_medium(self, medium: int, moves, frames: int) -> int:
        """Move every reference to `medium` through a compaction map.

        `moves` is the host's `[{from, to, len}, ...]`: audio that was at
        `from + d` is now at `to + d`. Returns the number of references the map
        did **not** cover, which is a bug in the keep set and not something to
        paper over — an uncovered reference is parked at `frames`, the end of
        the new medium, so it reads as silence. Clamping it into the medium
        instead would play somebody else's take, which is the one outcome
        § 3.8 exists to prevent.

        Coverage has to be **whole**: a reference whose head is inside a kept
        run but whose tail is not would have its tail land on the next run's
        audio, and silence is the only honest answer for a span the copy did
        not carry.
        """
        rows = compaction_rows(moves)
        end = max(0, int(frames))
        orphans = 0
        for c in self.medium_clips(medium):
            landed = compacted_address(rows, c.src, c.length)
            if landed is None:
                orphans += 1
                landed = end
            c.src_offset = int(landed)
            # the address is explicit from here on: `None` means "where I play",
            # which a compaction has just made untrue
            c.src_track = int(medium)
        self.compact_orphans += orphans
        return orphans

    # -- status -----------------------------------------------------------
    def has_audio(self) -> bool:
        return any(self.tracks[t] for t in range(NUM_TRACKS))

    def reset(self):
        """FILE › NEW: an empty tape (stage J1).

        `stop()` first, because that is what clears a pending COUNT-IN punch —
        a punch left armed would fire into the next song. Then everything
        `__init__` sets except the two collaborators, which outlive a project:
        re-running it is the only version of this that cannot drift.
        """
        self.stop()
        Tape.__init__(self, self.sample_rate, self.host)

    def snapshot(self) -> dict:
        return {"selected": self.selected, "speed": self.speed,
                "loop_in": self.loop_in, "loop_out": self.loop_out,
                "loop_on": self.loop_on}

    def restore(self, data: dict):
        self.selected = int(data.get("selected", self.selected))
        self.speed = float(data.get("speed", self.speed))
        self.loop_in = int(data.get("loop_in", self.loop_in))
        self.loop_out = int(data.get("loop_out", self.loop_out))
        self.loop_on = bool(data.get("loop_on", self.loop_on))
