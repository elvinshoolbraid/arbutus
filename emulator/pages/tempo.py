"""TEMPO page + the click's two knobs (screen-sketches § TEMPO, DDR-015/016).

BPM is the ruler, never the audio speed (DDR-016) — unless LINK is on, which
is DDR-059's amendment: with LINK the tape plays at `bpm / base_bpm`, so
slowing the song slows the tape with it and the sequencers stay on the grid.
Either way the *grid* is what BPM moves; the host owns the rest.

Stage H2 built the rest of the page:

    BPM · CLICK LVL · CLICK PITCH · LINK
    BPM FINE · COUNT-IN · SYNC · —

`‹ ›` nudge ±1 BPM, ±0.1 with SHIFT (control-inventory § arrow keys). The
click is rendered by the audio process while the transport is playing; tapping
the TEMPO key repeatedly sets BPM (tap tempo, handled in `app.App.on_key` so
it works from any page).
"""

from typing import List, Optional

from app.page import Page
from app.params import Param, StepType, pad_slots
from ui import draw, palette
from ui.font import LARGE, SMALL

BPM_MIN, BPM_MAX = 40.0, 240.0
CLICK_MIN_HZ, CLICK_MAX_HZ = 0.0, 4000.0
CLICK_LEN_S = 0.030
BIG_SCALE = 3
# A followed MIDI clock moves the tempo in steps this big, and only once it
# is a whole step away from the tempo set (pages review risk 13, #1123).
FOLLOW_STEP = 0.1

NUDGE, NUDGE_FINE = 1.0, 0.1
COUNT_IN_LABELS = ("OFF", "1 BAR", "2 BARS")
SYNC_LABELS = ("FREE", "MIDI")


class Metronome:
    """CLICK LVL / CLICK PITCH, and where the beat is.

    The click is rendered by the audio process (`metronome {level, pitch}`),
    into the master and never the record tap: a burst summed here never
    reached the host's output. The shape is the emulator's own — 30 ms of sine
    under a squared decay, an octave up on every fourth beat, beats on the
    song grid — see `audio/CLAUDE.md` § metronome and `host/audio_host.cpp`.
    What is left here is the two parameters and the beat indicator, read from
    the host's clock.
    """

    def __init__(self, app, sample_rate: int = 48000):
        self.app = app
        self.sr = int(sample_rate)
        self.level = 0.0                  # 0 = off
        self.pitch = 1600.0               # Hz; 0 fades to silent

    def beat_samples(self) -> float:
        return 60.0 / max(1.0, float(self.app.bpm)) * self.sr

    @property
    def beat(self) -> int:
        """Which beat the transport is on, from the host's song position."""
        return int(self.app.native.song_frame // max(1.0, self.beat_samples()))

    @property
    def beat_phase(self) -> float:
        beat = max(1.0, self.beat_samples())
        return (self.app.native.song_frame % beat) / beat

    @property
    def flashing(self) -> bool:
        """Beat indicator: on for the first fifth of every beat while playing."""
        return bool(self.app.playing) and self.beat_phase < 0.2


class TempoPage(Page):
    key_id = "TEMPO"
    title = "TEMPO"
    category = "utility"
    subpage_names = ()

    def __init__(self, app):
        super().__init__(app)
        self.metronome = Metronome(app, app.sample_rate)
        #: SYNC (v1). `free` is the internal clock; `midi` means the BPM comes
        #: from an incoming MIDI clock, and while it is selected the local BPM
        #: controls are locked rather than lying about who is in charge. The
        #: port itself is FILE › MIDI's `CLOCK IN` — see the deviation note in
        #: `emulator/notes/as-built.md` § H2.
        self.sync = "free"
        self._build_params()

    def _build_params(self):
        self.p_bpm = Param("BPM", BPM_MIN, BPM_MAX, self.app.bpm,
                           StepType.DISCRETE, display_format="{:.0f}")
        self.p_bpm.on_change = self._set_bpm
        self.p_level = Param("CLICK LVL", 0.0, 1.0, 0.0, StepType.LINEAR, 0.05)
        self.p_level.on_change = lambda v: setattr(self.metronome, "level", float(v))
        self.p_pitch = Param("CLICK PITCH", CLICK_MIN_HZ, CLICK_MAX_HZ, 1600.0,
                             StepType.LINEAR, 50.0, unit="Hz",
                             display_format="{:.0f}")
        self.p_pitch.on_change = lambda v: setattr(self.metronome, "pitch", float(v))
        # LINK is DDR-059's one bit and nothing else (E1): `base_bpm` moves
        # only when a project load says so, never from this page.
        self.p_link = Param("LINK", 0, 1, 1, StepType.DISCRETE,
                            choices=["OFF", "ON"])
        self.p_link.on_change = lambda v: self.app.native.tempo(
            link=bool(int(v)))

        # SHIFT row
        self.p_fine = Param("BPM FINE", BPM_MIN, BPM_MAX, self.app.bpm,
                            StepType.LINEAR, NUDGE_FINE,
                            display_format="{:.1f}")
        self.p_fine.on_change = self._set_bpm
        self.p_count = Param("COUNT-IN", 0, len(COUNT_IN_LABELS) - 1, 0,
                             StepType.DISCRETE, choices=list(COUNT_IN_LABELS))
        self.p_sync = Param("SYNC", 0, len(SYNC_LABELS) - 1, 0,
                            StepType.DISCRETE, choices=list(SYNC_LABELS))
        self.p_sync.on_change = self._set_sync

    # -- the two host-owned values ----------------------------------------
    def _set_bpm(self, value: float):
        """ENC1 / SHIFT+ENC1. The host keeps the grid: its clock is the
        sequencer's and the click's, so BPM is a message as well as a number
        (DDR-016)."""
        self.app.bpm = float(value)
        self.app.native.tempo(float(value))

    def _set_sync(self, value: float):
        self.sync = SYNC_LABELS[int(value)].lower()

    @property
    def link(self) -> bool:
        """The host's, read every frame — never a copy up here (E1)."""
        tape = self.app.native.state.get("tape") or {}
        return bool((tape.get("link") or {}).get("on", True))

    @property
    def count_in_bars(self) -> int:
        return int(self.p_count.value)

    @property
    def external_sync(self) -> bool:
        return self.sync == "midi"

    @property
    def clock_locked(self) -> bool:
        """SYNC is `midi` **and** a clock is actually arriving (stage J2).

        The switch and the cable are two different facts and the page shows
        both: `midi` with nothing on the wire is not "following", it is
        waiting, and the BPM on screen is the last one this instrument set.
        """
        clock = getattr(self.app, "midi_clock", None)
        return bool(self.external_sync and clock is not None and clock.alive())

    def poll_external(self):
        """Follow the incoming clock. Called once a frame from `App.tick`.

        H2 left `external_sync` waiting for FILE › MIDI's `CLOCK IN`; this is
        it. The lock on the local controls stays — while a master is running
        the tempo is not this page's to set — but the number is now the
        master's rather than a frozen one.
        """
        if not self.clock_locked:
            return
        bpm = float(self.app.midi_clock.bpm)
        if bpm <= 0.0 or abs(bpm - float(self.app.bpm)) < FOLLOW_STEP:
            return
        # With LINK on the BPM is the tape's speed (DDR-059), so every new
        # `tempo` is a small pitch change: send 0.1 BPM steps, and only once
        # the clock sits a whole step away. A median that wobbles across a
        # rounding edge (USB MIDI arrives in bursts) then moves nothing (#1123).
        bpm = round(bpm, 1)
        self.p_bpm.set_value(bpm)          # `locked` stops the encoder, not us

    # -- Page contract -----------------------------------------------------
    def tick(self):
        """Once a frame, from `App.tick` — the only place the clock is read."""
        self.poll_external()

    def slots(self) -> List[Optional[Param]]:
        self.p_bpm.value = float(self.app.bpm)
        self.p_fine.value = float(self.app.bpm)
        self.p_link.value = 1.0 if self.link else 0.0
        # Under MIDI clock the BPM is not this page's to set: named, drawn dim
        # and inert, the way DDR-053's dead cells are.
        self.p_bpm.locked = self.p_bpm.dimmed = self.external_sync
        self.p_fine.locked = self.p_fine.dimmed = self.external_sync
        return pad_slots([self.p_bpm, self.p_level, self.p_pitch, self.p_link,
                          self.p_fine, self.p_count, self.p_sync, None])

    def strip_labels(self):
        return [None, None, None, None]

    def active_strip_cell(self):
        return None

    def set_bpm(self, bpm: float):
        if self.external_sync:
            return                      # tap tempo does not outrank the clock
        self.p_bpm.set_value(float(bpm))

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        """`‹ ›` nudge ±1 BPM, ±0.1 with SHIFT (control-inventory)."""
        if key not in ("LEFT", "RIGHT") or not pressed:
            return False
        if self.external_sync:
            return True                 # eaten, deliberately: see `slots`
        step = NUDGE_FINE if shift else NUDGE
        self.p_bpm.set_value(self.app.bpm + (step if key == "RIGHT" else -step))
        self.app.notify_param_changed(self)
        return True

    def help_for(self, target: str) -> str:
        table = {
            "TEMPO": "TEMPO: tap 3+ times to set the BPM by ear",
            "ENC1": "ENC1 BPM (SHIFT: BPM FINE, 0.1 steps)",
            "ENC2": "ENC2 CLICK LVL: 0 turns the metronome off "
                    "(SHIFT: COUNT-IN bars before a take)",
            "ENC3": "ENC3 CLICK PITCH: 0 fades the click to silence "
                    "(SHIFT: SYNC free/midi)",
            "ENC4": "ENC4 LINK: BPM drags the tape with it (DDR-059)",
            "LEFT": "‹ : BPM -1 (SHIFT -0.1)",   # ASCII: U+2212 drew `?` (#632)
            "RIGHT": "› : BPM +1 (SHIFT +0.1)",
            "PLAY": "PLAY: the click and the beat dot follow the transport",
            "REC": "REC: the take waits out COUNT-IN, then punches in",
        }
        return table.get(str(target), "")

    # -- render ------------------------------------------------------------
    def render(self, fb, rect, app):
        x, y, w, h = rect
        cx = x + w // 2
        txt = "%.1f" % app.bpm if abs(app.bpm - round(app.bpm)) > 0.04 \
            else "%d" % int(round(app.bpm))
        tw = draw.scaled_width(txt, LARGE, BIG_SCALE)
        ny = y + 26
        draw.text_scaled(fb, cx - tw // 2 - 12, ny, txt, palette.INK,
                         LARGE, BIG_SCALE)
        draw.text(fb, cx + tw // 2 - 6, ny + 26, "BPM", palette.SUBTLE, SMALL)
        self._metronome_glyph(fb, x + 24, y + 24)
        self._state_line(fb, x, y + 4, w)
        self._beat_dots(fb, cx, y + h - 26)

    def _state_line(self, fb, x: int, y: int, w: int):
        """LINK, SYNC and the count-in: the three things that decide what the
        big number means (screen-sketches: "`LINK` state shown")."""
        link = self.link
        draw.text(fb, x + 4, y, "LINK", palette.GREEN if link else palette.DIM,
                  SMALL)
        draw.text(fb, x + 4 + draw.text_width("LINK ", SMALL), y,
                  "ON" if link else "OFF",
                  palette.INK if link else palette.DIM, SMALL)

        if self.external_sync:
            # the switch is not the cable: say which one you have
            right = "MIDI CLK" if self.clock_locked else "MIDI NO CLK"
            colour = palette.GREEN if self.clock_locked else palette.ACCENT_BROWN
        else:
            right, colour = "SYNC FREE", palette.SUBTLE
        rw = draw.text_width(right, SMALL)
        draw.text(fb, x + w - 4 - rw, y, right, colour, SMALL)

        tp = self.app.tape
        if tp.counting_in:
            mid = "COUNT-IN"
            draw.text(fb, x + w // 2 - draw.text_width(mid, SMALL) // 2, y,
                      mid, palette.RED, SMALL)

    @staticmethod
    def _metronome_glyph(fb, x: int, y: int):
        """Flat 16x16-ish metronome: a trapezoid body and a pendulum rod."""
        for i in range(16):
            half = 3 + i // 2
            draw.hline(fb, x + 8 - half, y + i, half * 2, palette.ACCENT_BROWN)
        draw.polyline(fb, [(x + 8, y + 14), (x + 12, y + 2)], palette.INK)
        draw.fill_rect(fb, x + 11, y + 1, 3, 3, palette.INK)

    def _beat_dots(self, fb, cx: int, y: int):
        met = self.metronome
        beat_in_bar = met.beat % 4
        counting = self.app.tape.counting_in
        for i in range(4):
            bx = cx - 30 + i * 20
            on = self.app.playing and i == beat_in_bar and met.flashing
            if on:
                draw.fill_rect(fb, bx, y, 9, 9,
                               palette.GREEN if counting else palette.RED)
            else:
                draw.rect_outline(fb, bx, y, 9, 9, palette.RULE)

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        return {"bpm": float(self.app.bpm), "click_level": self.p_level.value,
                "click_pitch": self.p_pitch.value,
                "count_in": int(self.p_count.value), "sync": self.sync,
                "link": bool(self.link)}

    def restore(self, data: dict):
        if "bpm" in data:
            self.p_bpm.set_value(float(data["bpm"]))
        self.p_level.set_value(float(data.get("click_level", self.p_level.value)))
        self.p_pitch.set_value(float(data.get("click_pitch", self.p_pitch.value)))
        self.p_count.set_value(float(data.get("count_in", 0)))
        sync = str(data.get("sync", "free")).lower()
        if sync in ("free", "midi"):
            self.p_sync.set_value(SYNC_LABELS.index(sync.upper()))
        if "link" in data:
            self.p_link.set_value(1.0 if data["link"] else 0.0)
