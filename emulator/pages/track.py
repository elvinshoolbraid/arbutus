"""TRACK page — four lanes of the eight-track tape (screen-sketches § TRACK).

1-4 select the track inside the shown bank and **SHIFT+1-4 flips bank and
selects** (DDR-060); there are no subpages. Encoders: scrub / zoom / speed /
level — and ENC3 is the **song tempo** while `LINK` is on, because DDR-059
makes BPM and tape speed one control (#69); it is the varispeed only with LINK
off. The six slot keys carry their brown tape legends here (IN OUT LOOP REV
SPEED FILTER); the left block does SLICE / TAPE / LIFT / LIFT-ALL / DROP / undo.

The ruler is the **tick ladder** (DDR-058): the finest rung that keeps 8-16
ticks on screen, which is also the step SHIFT+‹ › takes and the grid REC+note
snaps to — zoom is the precision control and there is no separate one.
"""

import math
from typing import List, Optional

import numpy as np

from app import tape as T
from app.page import Page
from app.params import Param, StepType, pad_slots
from pages.tempo import BPM_MAX, BPM_MIN
from ui import draw, palette
from ui.font import SMALL

LANE_H = 23
#: deepest zoom, in seconds across the whole window. At 300 px of lane that is
#: ~3 samples per pixel — enough to place a trim on the waveform by eye.
MIN_VIEW_SECONDS = 0.02
#: the track numbers live in this left gutter, *outside* the tape. The lane
#: box, the timeline and the ruler all start to its right, so sample 0 — head
#: of the tape, first clip, cursor at zero — coincides and is never behind a
#: digit.
LABEL_W = 10
#: waveform replies kept; far above what one bank of four lanes shows, so a
#: frame never evicts its own clips (#632)
PEAKS_CACHE_MAX = 256


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def fmt_span(seconds: float) -> str:
    """ZOOM spans four orders of magnitude — one %f cannot read well at both ends."""
    if seconds < 1.0:
        return f"{seconds * 1000.0:.0f}ms"
    if seconds < 10.0:
        return f"{seconds:.2f}s"
    return f"{seconds:.1f}s"


def fmt_hz(hz: float) -> str:
    return f"{hz / 1000.0:.1f}k" if hz >= 1000.0 else f"{int(hz)}"


class TrackPage(Page):
    key_id = "TRACK"
    title = "TRACK"
    category = "recording"
    subpage_names = ()

    def __init__(self, app):
        super().__init__(app)
        self.state.view_len = float(30 * T.SAMPLE_RATE)
        self.state.view_start = 0.0
        self._held_stopped = {}          # tape key -> transport was STOPPED
        #: `clip_peaks` replies, keyed by the geometry that produced them. The
        #: samples are the host's, so a clip's waveform is a message; a settled
        #: clip's answer never changes, which is what makes caching it honest.
        self._peaks = {}
        #: the host's `media_gen` the cache was filled under (#632)
        self._peaks_gen = 0
        self._build_params()

    # -- helpers -----------------------------------------------------------
    @property
    def tape(self) -> T.Tape:
        return self.app.tape

    @property
    def link(self) -> bool:
        """DDR-059's one bit, read from the page that owns it.

        TEMPO reads it off the host every frame (E1), so there is no second
        copy of it up here either — a Python property on another page is a
        read, not a cache.
        """
        tempo = self.app.pages.get("TEMPO")
        return True if tempo is None else bool(tempo.link)

    @property
    def external_sync(self) -> bool:
        """A MIDI master owns the tempo; TEMPO page's switch, same answer."""
        tempo = self.app.pages.get("TEMPO")
        return False if tempo is None else bool(tempo.external_sync)

    def bar_samples(self) -> float:
        return 4.0 * 60.0 / max(1.0, self.app.bpm) * T.SAMPLE_RATE

    def tick(self) -> T.Tick:
        """The current rung of the ladder (DDR-058)."""
        return T.tick_ladder(self.state.view_len, self.bar_samples(),
                             T.SAMPLE_RATE)

    def min_view(self) -> float:
        return MIN_VIEW_SECONDS * T.SAMPLE_RATE

    def _clamp_view(self):
        st = self.state
        st.view_len = float(min(float(T.TRACK_LEN), max(self.min_view(), st.view_len)))
        head = self.tape.playhead
        st.view_start = float(min(max(0.0, head - st.view_len * 0.5),
                                  max(0.0, T.TRACK_LEN - st.view_len)))

    def _frame_loop(self):
        """Zoom out far enough to see the loop — once, when it is set.

        Doing this every frame instead would silently undo a deliberate
        zoom-in on a trim point the moment a loop happened to be active.
        """
        tp = self.tape
        if not (tp.loop_on and tp.loop_out > tp.loop_in):
            return
        # The view is centred on the playhead (`_clamp_view`), and a loop is
        # usually set with the playhead at OUT: 1.1 × the loop showed only its
        # second half (#632, review m-emu-pages row 7). Wide enough for the
        # farther end on either side of the playhead fits both.
        head = float(tp.playhead)
        span = 2.2 * max(head - tp.loop_in, tp.loop_out - head,
                         0.5 * (tp.loop_out - tp.loop_in))
        if span > self.state.view_len:
            self.state.view_len = min(float(T.TRACK_LEN), span)

    def x_of(self, pos: float, x: int, w: int) -> int:
        """Sample position -> pixel column in the tape area (past the gutter)."""
        st = self.state
        return int(x + (pos - st.view_start) / max(1.0, st.view_len) * w)

    # -- params ------------------------------------------------------------
    def _build_params(self):
        self.p_scrub = Param("SCRUB", 0.0, T.TRACK_SECONDS, 0.0,
                             StepType.LINEAR, 0.1, unit="s", display_format="{:.3f}")
        self.p_zoom = Param("ZOOM", 0.0, T.TRACK_SECONDS, 30.0,
                            StepType.LINEAR, 1.0)
        self.p_zoom.formatter = fmt_span
        self.p_speed = Param("SPEED", T.SPEED_MIN, T.SPEED_MAX, 1.0,
                             StepType.LINEAR, 0.01, display_format="×{:.2f}")
        self.p_level = Param("LEVEL", 0.0, 1.0, 0.8, StepType.LINEAR, 0.02)
        self.p_slide = Param("SLIDE", -1.0, 1.0, 0.0, StepType.LINEAR, 0.01,
                             unit="s", display_format="{:+.2f}")
        self.p_steps = Param("STEPS", 0, len(T.SPEED_STEPS) - 1, 3,
                             StepType.DISCRETE,
                             choices=[f"×{v:g}" for v in T.SPEED_STEPS])
        self.p_hold = Param("SPEED", 0.0, 1.0, 0.0, StepType.LINEAR, 0.01)
        #: ENC3 while LINK is on (#69): DDR-059 makes BPM and tape speed one
        #: control, so the knob is the song tempo and not the varispeed. One
        #: detent = 1 BPM, the same as TEMPO's ENC1 and the arrow keys.
        self.p_bpm = Param("BPM", BPM_MIN, BPM_MAX, 120.0, StepType.DISCRETE,
                           display_format="{:.0f}")

    def _sync_params(self):
        tp = self.tape
        self.p_scrub.value = tp.playhead / T.SAMPLE_RATE
        self.p_zoom.value = self.state.view_len / T.SAMPLE_RATE
        self.p_speed.value = tp.speed
        self.p_level.value = self.app.mixer.master_level
        self.p_bpm.value = float(self.app.bpm)
        # under a MIDI master the tempo is not ours to set, exactly as on the
        # TEMPO page: named, dim and inert rather than silently ignored
        self.p_bpm.locked = self.p_bpm.dimmed = self.external_sync

    def slots(self) -> List[Optional[Param]]:
        self._clamp_view()
        self._sync_params()
        tp = self.tape
        enc4 = self.p_level
        if tp.speed_held:
            self.p_hold.name = "SPEED"
            self.p_hold.min_val, self.p_hold.max_val = T.SPEED_MIN, T.SPEED_MAX
            self.p_hold.value = tp.momentary_speed
            self.p_hold.display_format = "×{:.2f}"
            self.p_hold.unit = ""
            enc4 = self.p_hold
        elif tp.filter_held:
            self.p_hold.name = "FILTER"
            self.p_hold.min_val = T.FILTER_MIN_HZ / 1000.0
            self.p_hold.max_val = T.FILTER_MAX_HZ / 1000.0
            self.p_hold.value = tp.filter_cutoff / 1000.0
            self.p_hold.display_format = "{:.1f}k"
            self.p_hold.unit = ""
            enc4 = self.p_hold
        enc3 = self.p_bpm if self.link else self.p_speed
        return pad_slots([self.p_scrub, self.p_zoom, enc3, enc4,
                          self.p_slide, None, self.p_steps, None])

    def strip_labels(self):
        return [f"TRK{t + 1}" for t in self.tape.bank_tracks()]

    def active_strip_cell(self):
        return self.tape.selected % T.BANK_SIZE

    def tickers(self):
        out = list(super().tickers())
        buf = self.tape.lift_buffer
        # §2.1: the strip says so while a LIFT ALL buffer is held, because
        # SHIFT+DROP is the only gesture on this page you cannot see
        if buf is not None and len(buf.clips) > 1:
            out.append((20, "SHIFT+DROP = MERGE", "SH+DROP=MRG"))
        return out

    # -- input -------------------------------------------------------------
    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        tp = self.tape
        st = self.state
        if idx == 0:
            amount = st.view_len / 64.0 * delta
            if shift:
                tp.do_slide(int(amount))
            else:
                tp.nudge(amount)
                tp.scrub_preview(1 if delta >= 0 else -1)
        elif idx == 1:
            st.view_len = st.view_len * (1.25 ** (-delta))
            self._clamp_view()
        elif idx == 2:
            if shift:
                self.p_steps.turn(delta)
                tp.speed = T.SPEED_STEPS[int(self.p_steps.value)]
            elif self.link:
                # DDR-059 / #69: with LINK on the two are one control, so ENC3
                # moves the song tempo and the host's link factor drags the
                # tape with it. `App.set_bpm` is the one door — it is what the
                # TEMPO page, the arrow keys and tap tempo all go through, so
                # the MIDI-clock lock and the host message come for free.
                self.p_bpm.value = float(self.app.bpm)
                self.p_bpm.turn(delta)
                self.app.set_bpm(self.p_bpm.value)
            else:
                tp.speed = float(np.clip(tp.speed + delta * 0.01,
                                         T.SPEED_MIN, T.SPEED_MAX))
        elif idx == 3:
            if tp.speed_held:
                tp.momentary_speed = float(np.clip(
                    tp.momentary_speed + delta * 0.02, T.SPEED_MIN, T.SPEED_MAX))
            elif tp.filter_held:
                tp.filter_cutoff = float(np.clip(
                    tp.filter_cutoff * (1.12 ** delta),
                    T.FILTER_MIN_HZ, T.FILTER_MAX_HZ))
            else:
                self.app.mixer.master_level = float(np.clip(
                    self.app.mixer.master_level + delta * 0.02, 0.0, 1.0))
        else:
            return False
        return True

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        tp = self.tape
        if key in ("1", "2", "3", "4"):
            if pressed:
                # DDR-060: plain selects inside the shown bank, SHIFT flips to
                # the other bank *and* selects there — one gesture, so bank B
                # never has to be found before it can be used
                n = int(key) - 1
                tp.flip_bank_select(n) if shift else tp.select_in_bank(n)
            return True

        # --- tape keys (brown legends) ------------------------------------
        if key in ("A", "B", "C"):
            if pressed:
                if key == "A":
                    tp.loop_in = int(tp.playhead)
                elif key == "B":
                    tp.loop_out = max(int(tp.playhead), tp.loop_in + 1)
                else:
                    tp.loop_on = not tp.loop_on
                self._frame_loop()
            return True
        if key in ("X", "Y", "Z"):
            self._tape_hold(key, pressed)
            return True

        if key in ("LEFT", "RIGHT"):
            d = -1 if key == "LEFT" else 1
            if pressed:
                if getattr(self.app, "stop_held", False):
                    tp.jump(d)                       # DDR-057: loop / tape ends
                elif shift:
                    tp.nudge_tick(d, self.tick().samples)     # DDR-058
                else:
                    tp.nudge(d * self.bar_samples())
                    tp.scrub_dir = d
            else:
                tp.scrub_dir = 0
            return True

        if not pressed:
            return False
        if key == "SLICE":
            tp.do_merge() if shift else tp.do_slice()
            return True
        if key == "LIFT":
            tp.do_lift_all() if shift else tp.do_lift()
            return True
        if key == "DROP":
            # SHIFT+DROP mixes a LIFT ALL buffer down onto this track (§2.1)
            tp.do_merge_drop() if shift else tp.do_drop()
            return True
        return False

    def help_for(self, target) -> str:
        if target in ("1", "2", "3", "4"):
            return "1-4 select in this bank · SHIFT+1-4 flips bank and selects"
        if target in ("LEFT", "RIGHT"):
            return "‹ › scan · SHIFT one tick · STOP+‹ › loop / tape ends"
        if target == "DROP":
            return "DROP lays the buffer down · SHIFT+DROP merges a LIFT ALL"
        if target == "ENC3":
            if self.link:
                return ("ENC3 BPM: LINK is on, so tempo and tape are one "
                        "control (SHIFT: SPEED steps)")
            return "ENC3 SPEED: the tape only, LINK is off (SHIFT: steps)"
        return ""

    def _tape_hold(self, key: str, pressed: bool):
        """DDR-001/002: hold performs, tap (while stopped) commits."""
        tp = self.tape
        if pressed:
            self._held_stopped[key] = (tp.state == T.STOPPED)
            if key == "X":
                tp.hold_rev(True)
            elif key == "Y":
                tp.hold_speed(True)
            else:
                tp.hold_filter(True)
            return
        # no record = panic already let this hold go: the release is a
        # stray and commits nothing (#873)
        was_stopped = self._held_stopped.pop(key, False)
        if key == "X":
            tp.hold_rev(False)
            if was_stopped:
                tp.do_reverse()
        elif key == "Y":
            tp.hold_speed(False)
            if was_stopped:
                tp.do_speed()
        else:
            tp.hold_filter(False)
            if was_stopped:
                tp.do_bake_filter()

    def on_panic(self):
        """Let go of ‹ › and the three tape holds without committing them:
        panic is "every held control let go", not a tap (#623)."""
        tp = self.tape
        tp.scrub_dir = 0
        tp.hold_rev(False)
        tp.hold_speed(False)
        tp.hold_filter(False)
        self._held_stopped.clear()

    # -- render ------------------------------------------------------------
    def render(self, fb, rect, app):
        x, y, w, h = rect
        self._clamp_view()
        tp = self.tape
        lanes_y = y + 3
        gx, gw = x + 4, w - 8
        # the timeline lives right of the track-number gutter, so nothing at
        # 0:00 ever sits under a digit
        cx, cw = gx + LABEL_W, gw - LABEL_W

        lanes = tp.bank_tracks()
        # loop region shading, behind everything
        if tp.loop_out > tp.loop_in:
            lx0 = max(cx, self.x_of(tp.loop_in, cx, cw))
            lx1 = min(cx + cw, self.x_of(tp.loop_out, cx, cw))
            if lx1 > lx0:
                col = palette.LOOP_ON if tp.loop_on else palette.LOOP_OFF
                draw.fill_rect(fb, lx0, lanes_y, lx1 - lx0,
                               LANE_H * len(lanes), col)

        self._prefetch_peaks(lanes, cx, cw)
        for i, t in enumerate(lanes):
            self._render_lane(fb, t, gx, lanes_y + i * LANE_H, gw, LANE_H - 2)

        self._render_cursor(fb, cx, cw, lanes_y - 1,
                            LANE_H * len(lanes) + 1, app)
        ruler_y = lanes_y + LANE_H * len(lanes) + 2
        self._render_ruler(fb, gx, ruler_y, gw, x + w)
        self._render_bank_glyph(fb, gx, ruler_y)

    def _render_bank_glyph(self, fb, gx: int, ruler_y: int):
        """`A` / `B` in the ruler's gutter — only once bank B holds a clip.

        DDR-060 reveals bank B on use, so a first session is four lanes with
        nothing extra on screen. `[TBD, Elvin's call]`: the form is one letter in the
        gutter under lane 4, in the selected track's encoder colour — the only
        column on this page that is never tape.
        """
        tp = self.tape
        other = (tp.bank + 1) % T.NUM_BANKS
        if not (tp.bank_has_clips(other) or tp.bank):
            return
        draw.text(fb, gx + 1, ruler_y + 5, "AB"[tp.bank],
                  palette.encoder_color(tp.selected % T.BANK_SIZE), SMALL)

    def _render_lane(self, fb, t: int, gx: int, y: int, gw: int, h: int):
        """One lane: its number in the gutter, the tape box to the right."""
        tp = self.tape
        cx, cw = gx + LABEL_W, gw - LABEL_W
        sel = (t == tp.selected)
        # the box IS the tape: its left edge is the head, not a frame around
        # the numbers as well
        draw.rect_outline(fb, cx, y, cw, h, palette.RULE)
        label = f"{t + 1}"
        draw.text(fb, gx + 1, y + (h - 7) // 2, label,
                  palette.RED if tp.armed[t] else
                  (palette.INK if sel else palette.DIM), SMALL)
        body = palette.encoder_color(t) if sel else palette.CLIP_DIM
        for c, x0, x1, x0c, x1c in self._clip_rects(t, cx, cw):
            draw.fill_rect(fb, x0c, y + 2, x1c - x0c, h - 4,
                           palette.CLIP_FILL_SEL if sel else palette.CLIP_FILL)
            self._clip_wave(fb, c, x0, x1, x0c, x1c, y + 2, h - 4, body, t)
            # clip edges = the markers SLICE cuts on and SHIFT+‹ › snaps to
            for mx in (x0c, x1c - 1):
                if cx + 1 <= mx <= cx + cw - 2:
                    draw.vline(fb, mx, y + 2, h - 4,
                               palette.INK if sel else palette.DIM)
        if tp.recording and sel:
            self._render_take(fb, cx, cw, y + 2, h - 4)

    def _clip_rects(self, t: int, cx: int, cw: int):
        """`(clip, x0, x1, x0c, x1c)` for every clip of lane `t` on screen.

        The un-clipped `x0`/`x1` are the clip's own width — the waveform maps
        pixels through those, not through the visible pair — and `x0c`/`x1c`
        are what is inside the lane box.
        """
        for c in self.tape.tracks[t]:
            x0 = self.x_of(c.start, cx, cw)
            x1 = self.x_of(c.end, cx, cw)
            x0c, x1c = max(cx + 1, x0), min(cx + cw - 1, x1)
            if x1c <= x0c:
                continue
            yield c, x0, x1, x0c, x1c

    def _wave_key(self, clip, track, x0, x1, x0c, x1c):
        """The geometry a clip's waveform is asked for — and its cache key.

        Zoomed out, one answer per clip covers it (a bin per REC_PEAK_BUCKET,
        capped at one per pixel of the clip's width — #163) and is cached until
        the clip's geometry changes. Zoomed in past a bin per pixel, the visible
        range is asked for exactly, so the 20 ms zoom still shows detail.

        `None` when the clip asks for nothing. Separate from the drawing
        because `_prefetch_peaks` needs the whole bank's keys before the first
        lane is drawn (#169).
        """
        span = max(1, x1 - x0)
        n = clip.length
        if n <= 0 or x1c <= x0c:
            return None
        if n / float(span) >= T.REC_PEAK_BUCKET:
            a0, a1 = 0, n
            # One bin per pixel of the clip's own width, and no more (#163).
            # It used to ask for `ceil(n / REC_PEAK_BUCKET)` whatever the zoom —
            # 33,750 bins for a three-minute clip into a ~300 px lane — and then
            # drew one in every hundred-odd of them; the rest were ~60 ms of
            # JSON a frame cannot afford, and they are also what put the question
            # below the host's peaks sidecar resolution, so it had to read the
            # audio. `span` (the clip's full width) and not the visible
            # `x1c - x0c`, so the cache key stays geometry and a scroll still
            # asks for nothing.
            bins = max(1, min(65536, -(-n // T.REC_PEAK_BUCKET), span))
        else:
            a0 = max(0, min(n - 1, int((x0c - x0) / span * n)))
            a1 = min(n, max(a0 + 1, int((x1c - x0) / span * n) + 1))
            bins = max(1, min(4096, x1c - x0c))
        src_track = track if clip.src_track is None else int(clip.src_track)
        return (src_track, clip.src, a0, a1, bins)

    @staticmethod
    def _peaks_req(track, key) -> dict:
        """One `clip_peaks` request, from a `_wave_key`. One definition, so the
        batch and the lone fetch cannot drift apart."""
        src_track, src, a0, a1, bins = key
        return {"track": int(track), "src_track": int(src_track),
                "src_offset": int(src + a0), "len": int(a1 - a0),
                "bins": int(bins)}

    def _fresh_peaks(self):
        """Forget every waveform once a medium may have been replaced (#632).

        The key is geometry, and zoomed in that geometry does not depend on
        the audio: after NEW, LOAD or a compaction a new take at an old offset
        would draw the old take's waveform, and you trim by that waveform.
        """
        gen = getattr(self.app.native, "media_gen", 0)
        if gen != self._peaks_gen:
            self._peaks.clear()
            self._peaks_gen = gen

    def _remember_peaks(self, key, reply):
        # Evict the oldest entry, never the whole cache: clearing it at 65
        # made a bank with 66 clips on screen refetch every clip every frame
        # (#632, review m-emu-pages row 12). Dicts keep insertion order.
        while len(self._peaks) >= PEAKS_CACHE_MAX:
            self._peaks.pop(next(iter(self._peaks)))
        self._peaks[key] = (reply.get("min") or [], reply.get("max") or [])

    def _prefetch_peaks(self, lanes, cx: int, cw: int):
        """The whole bank's waveforms in **one** message (#169).

        A `clip_peaks` round trip costs 2.1–2.5 ms on the CM4 even when the
        host answers out of its own reply cache — the message itself, 600 JSON
        numbers down a socket and back through `json.loads` — and a redraw asks
        once per clip drawn, four lanes at a time (#163's bench cell: ~9 ms of
        a 35 ms frame). So every fetch this frame is about to make goes down
        together and the lanes below read the cache.

        It is only ever a prefetch: a batch the host refuses (or one this
        cache already covers) leaves `_clip_wave` to ask for itself, one at a
        time, exactly as it did before.
        """
        self._fresh_peaks()
        want, seen = [], set()
        for t in lanes:
            for c, x0, x1, x0c, x1c in self._clip_rects(t, cx, cw):
                key = self._wave_key(c, t, x0, x1, x0c, x1c)
                if key is None or key in self._peaks or key in seen:
                    continue
                seen.add(key)
                want.append((t, key))
        if len(want) < 2:
            return                  # one fetch is not a batch
        got = self.app.native.clip_peaks_batch(
            [self._peaks_req(t, key) for t, key in want])
        if got is None:
            return
        for (_, key), reply in zip(want, got):
            self._remember_peaks(key, reply)

    def _clip_wave(self, fb, clip, x0, x1, x0c, x1c, y, h, color, track=0):
        """The clip's waveform, as peaks read out of the host's medium."""
        span = max(1, x1 - x0)
        n = clip.length
        mid = y + h // 2
        amp = h // 2 - 1
        key = self._wave_key(clip, track, x0, x1, x0c, x1c)
        if key is None:
            return
        got = self._clip_peaks(track, key)
        if got is None:
            return
        _, _, a0, a1, bins = key
        lo, hi = got
        for px in range(x0c, x1c):
            f = (px - x0) / span * n
            b = int((f - a0) / float(max(1, a1 - a0)) * bins)
            b = max(0, min(bins - 1, b))
            if b >= len(hi) or b >= len(lo):
                continue
            half = int(max(abs(float(hi[b])), abs(float(lo[b]))) * amp)
            draw.vline(fb, px, mid - half, max(1, half * 2), color)

    def _clip_peaks(self, track, key):
        self._fresh_peaks()
        got = self._peaks.get(key)
        if got is None:
            req = self._peaks_req(track, key)
            reply = self.app.native.clip_peaks(
                req["track"], req["src_offset"], req["len"], req["bins"],
                src_track=req["src_track"])
            if reply is None:
                return None
            self._remember_peaks(key, reply)
            got = self._peaks[key]
        return got

    def _render_take(self, fb, x: int, w: int, y: int, h: int):
        """The rolling take, read from where it is writing in the medium.

        The clip does not exist until STOP commits it, so this asks for the
        take's own region — `take_src` is the medium's head at the moment REC
        rolled — and draws it exactly as a clip. Never cached: it is growing.
        """
        tp = self.tape
        n = tp.take_length
        if n <= 0:
            return
        x0 = self.x_of(tp.take_start, x, w)
        x1 = self.x_of(tp.take_start + n, x, w)
        x0c, x1c = max(x + 1, x0), min(x + w - 1, max(x1, x0 + 1))
        if x1c <= x0c:
            return
        draw.fill_rect(fb, x0c, y, x1c - x0c, h, palette.REC_FILL)
        span = max(1, x1 - x0)
        a0 = max(0, min(n - 1, int((x0c - x0) / span * n)))
        a1 = min(n, max(a0 + 1, int((x1c - x0) / span * n) + 1))
        bins = max(1, min(4096, x1c - x0c))
        got = self.app.native.clip_peaks(self.tape.selected, tp.take_src + a0,
                                         a1 - a0, bins)
        if got is None:
            return
        lo, hi = got.get("min") or [], got.get("max") or []
        mid = y + h // 2
        amp = h // 2 - 1
        for px in range(x0c, x1c):
            f = (px - x0) / span * n
            b = max(0, min(bins - 1, int((f - a0) / float(max(1, a1 - a0)) * bins)))
            if b >= len(hi) or b >= len(lo):
                continue
            half = int(max(abs(float(hi[b])), abs(float(lo[b]))) * amp)
            draw.vline(fb, px, mid - half, max(1, half * 2), palette.REC_WAVE)

    def _render_cursor(self, fb, x: int, w: int, top: int, height: int, app):
        """Playhead. Armed or rolling it goes hot: 3 px of RED carrying the
        input level as an off-white foot that rises from the bottom, so
        silence still reads as armed (a solid red bar)."""
        tp = self.tape
        cx = self.x_of(tp.playhead, x, w)
        if not (x <= cx <= x + w):
            return
        hot = (tp.recording or tp.armed[tp.selected]
               or getattr(app, "rec_held", False))
        if not hot:
            draw.vline(fb, cx, top, height, palette.INK)
            return
        level = min(1.0, max(0.0, float(getattr(app, "input_peak", 0.0))))
        foot = min(height - 2, int(level * height))
        for dx in (-1, 0, 1):
            sx = cx + dx
            if not (x <= sx <= x + w):
                continue
            draw.vline(fb, sx, top, height - foot, palette.RED)
            if foot > 0:
                draw.vline(fb, sx, top + height - foot, foot, palette.INK)

    def _render_ruler(self, fb, x: int, y: int, w: int, right: int):
        """The tick ladder (DDR-058), whichever rung the zoom has landed on.

        Whole-bar rungs are labelled with bar numbers; sub-bar rungs are
        unlabelled but stand taller on the beat, so the eye still finds the
        pulse at 1/16. Below ~250 ms the rungs are time and carry a readout.
        """
        st = self.state
        draw.hline(fb, x, y, w, palette.RULE)
        cx, cw = x + LABEL_W, w - LABEL_W
        bar = self.bar_samples()
        tick = self.tick()
        step = tick.samples
        first = int(st.view_start // step)
        last = int((st.view_start + st.view_len) // step) + 1
        # thin the labels so they never collide at 6 px per glyph
        label_every = max(1, int(math.ceil((last - first + 1) / 10.0)))
        beat = bar / 4.0
        for k in range(first, last + 1):
            pos = k * step
            if pos < 0:
                continue
            tx = self.x_of(pos, cx, cw)
            if not (cx <= tx < x + w - 2):
                continue
            tall = 4
            if tick.kind == "bar" and tick.size < 1.0:
                # taller on the beat, tallest on the bar
                tall = 6 if pos % bar < step * 0.5 else (
                    5 if beat > 0 and pos % beat < step * 0.5 else 3)
            draw.vline(fb, tx, y, tall, palette.SUBTLE)
            if k % label_every:
                continue
            text = self._tick_label(tick, k, bar)
            if text:
                draw.text(fb, tx + 2, y + 5, text, palette.SUBTLE, SMALL)
        self._render_clock(fb, y, right)

    @staticmethod
    def _tick_label(tick: T.Tick, k: int, bar: float) -> str:
        """Bar numbers on bar rungs, seconds on time rungs, nothing between."""
        if tick.labelled:
            return str(int(round(k * tick.size)) + 1)
        if tick.kind == "time":
            # absolute ms would read "61250ms" a minute in; seconds stay legible
            dp = 0 if tick.size >= 1.0 else min(3, max(1, int(
                math.ceil(-math.log10(tick.size)))))
            return f"{k * tick.size:.{dp}f}"
        return ""

    def _render_clock(self, fb, y: int, right: int):
        txt = fmt_time(self.tape.playhead / T.SAMPLE_RATE)
        tw = draw.text_width(txt, SMALL)
        draw.fill_rect(fb, right - 6 - tw, y + 5, tw, 8,
                       palette.GROUND)
        draw.text(fb, right - 6 - tw, y + 5, txt, palette.INK, SMALL)

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        return {"view_len": self.state.view_len}

    def restore(self, data: dict):
        self.state.view_len = float(data.get("view_len", self.state.view_len))
