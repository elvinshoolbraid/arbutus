"""INPUT page — sample the world through the mic.

The demo beat this exists for: arm, tap a resonant bottle, and the strike is
already a playable instrument. So the page waits on the *transient* rather than
on a keypress (`app/mic.py` explains why), trims and normalises what it caught,
writes it into `library/synth/sampler/` and loads it straight onto SYNTH. The
chromatic keys then play it without leaving this page.

    1 ARM · 2 RETRY · 3 → SYNTH · 4 MONITOR

MONITOR defaults off: a laptop's mic and speakers are a feedback loop, and the
level meter is the confirmation you actually need while holding a bottle.

Input **routing** is not here — that is the `LIVE INPUT` engine (DDR-063). What
this page gained in stage G is the one field the two share: SOURCE, on the SHIFT
row, which is the host's `input_source` and the same value as that box's SOURCE
knob. Everything else on this page is the capture path and is the UI's.
"""

import os
from typing import List, Optional

import numpy as np

from app.mic import ARMED, CAPTURING, IDLE, READY, MicCapture
from app.page import Page
from app.params import Param, StepType, pad_slots
from ui import draw, palette
from ui.font import LARGE, SMALL

SAMPLER_DIR = ("synth", "sampler")

#: DDR-063's four, in the host's order. The labels are the box's own, so the
#: cell here and the SOURCE knob on SYNTH › ENGINE read the same four words.
SOURCES = ("mic", "line", "usb", "resample")
SOURCE_LABELS = ("MIC", "LINE", "USB", "RSMP")


class InputPage(Page):
    key_id = "INPUT"
    title = "INPUT"
    category = "utility"
    #: no subpages — 1-4 are verbs, so the strip names them (user story 2)
    subpage_names = ()

    def __init__(self, app):
        super().__init__(app)
        self.mic: Optional[MicCapture] = None
        self._driver = None
        self._open_failed = False
        self._build_params()

    # -- params -----------------------------------------------------------
    def _build_params(self):
        self.p_thresh = Param("THRESH", 0.01, 0.50, 0.06, StepType.LINEAR, 0.01,
                              display_format="{:.2f}")
        self.p_length = Param("LENGTH", 0.25, 4.0, 2.0, StepType.LINEAR, 0.25,
                              unit="s", display_format="{:.2f}")
        self.p_gain = Param("GAIN", 0.25, 8.0, 1.0, StepType.LINEAR, 0.25,
                            unit="x", display_format="{:.2f}")
        self.p_monitor = Param("MONITOR", 0, 1, 0, StepType.DISCRETE,
                               choices=["off", "on"])
        # DDR-063. The host owns this and `arbutus/live-input` wears it as a
        # knob, so the cell is pushed down and read back — never kept here.
        self.p_source = Param("SOURCE", 0, len(SOURCES) - 1, 0, StepType.DISCRETE,
                              choices=list(SOURCE_LABELS))
        self.p_source.on_change = lambda v: self.app.native.input_source(int(v))
        self.p_thresh.on_change = lambda v: self._apply("threshold", float(v))
        self.p_length.on_change = lambda v: self._apply("max_len", float(v))
        self.p_gain.on_change = lambda v: self._apply("gain", float(v))
        self.p_monitor.on_change = lambda v: self._apply("monitor", bool(int(v)))

    def _apply(self, attr: str, value):
        if self.mic is not None:
            setattr(self.mic, attr, value)

    def slots(self) -> List[Optional[Param]]:
        self.p_monitor.dimmed = not bool(int(self.p_monitor.value))
        # The host is the only copy of SOURCE: read it every frame, because
        # SYNTH › ENGINE's knob moves the same value.
        self.p_source.value = float(self.source_index)
        # 5-7 stay empty so SHIFT still fine-tunes LENGTH and GAIN
        # (parameter-framework); SOURCE takes the one SHIFT cell it needs.
        return pad_slots([self.p_thresh, self.p_length, self.p_gain,
                          self.p_monitor, self.p_source])

    @property
    def source_index(self) -> int:
        name = self.app.native.input_source()
        return SOURCES.index(name) if name in SOURCES else 0

    def strip_labels(self):
        return ["ARM" if not self._armed else "DISARM", "RETRY", "→ SYNTH",
                "MONITOR"]

    def active_strip_cell(self) -> Optional[int]:
        return 3 if bool(int(self.p_monitor.value)) else None

    # -- mic ownership ----------------------------------------------------
    @property
    def _armed(self) -> bool:
        return self.mic is not None and self.mic.armed

    @property
    def sampler_dir(self) -> str:
        return os.path.join(self.app.library.root, *SAMPLER_DIR)

    def ensure_mic(self) -> MicCapture:
        """Build the capture object; opening a device is the app's business."""
        if self.mic is None:
            self.mic = MicCapture(self.app.sample_rate, self.sampler_dir)
            self.mic.threshold = float(self.p_thresh.value)
            self.mic.max_len = float(self.p_length.value)
            self.mic.gain = float(self.p_gain.value)
            self.mic.monitor = bool(int(self.p_monitor.value))
            self.app.mic = self.mic
        return self.mic

    def attach(self, driver) -> bool:
        """Bind an AudioInputDriver. False when the device would not open."""
        mic = self.ensure_mic()
        if driver is None:
            return False
        if not driver.initialize(self.app.sample_rate, mic.feed):
            self._open_failed = True
            return False
        driver.start()
        self._driver = driver
        self._open_failed = False
        return True

    def on_enter(self):
        """Called by App.goto — the mic is opened on arrival, never at boot."""
        self.ensure_mic()
        if self._driver is None and not self._open_failed:
            self.app.open_mic(self)

    def release(self):
        """Close the capture device (app shutdown)."""
        if self._driver is not None:
            try:
                self._driver.stop()
                self._driver.cleanup()
            except Exception as exc:                  # pragma: no cover
                print(f"mic shutdown: {exc}")
            self._driver = None

    # -- capture ----------------------------------------------------------
    def tick(self):
        """Finish a landed capture. Called once per frame from the app loop."""
        if self.mic is None:
            return
        if self.mic.poll():
            if self.mic.captured_path:          # #853: the live stream's `capture`
                self.app.feed.capture(self.mic.captured_path, self.mic.length_s)
            self._load_into_synth()

    def _load_into_synth(self):
        """Put the new WAV on the SYNTH sampler so the keys play it at once."""
        path = self.mic.captured_path
        if not path:
            return
        self.app.library.rescan()
        synth = self.app.pages.get("SYNTH")
        folders = [f.folder for f in self.app.library.list("synth")]
        if synth is None or "sampler" not in folders:
            return
        lf = self.app.library.find("synth", "sampler")
        item = os.path.basename(path)
        if item not in lf.items:
            return
        synth.load_engine(folders.index("sampler"), lf.items.index(item))
        self.app.notify_param_changed(synth)

    # -- input ------------------------------------------------------------
    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        if not pressed:
            return False
        mic = self.ensure_mic()
        if key == "1":
            mic.disarm() if mic.armed else mic.arm()
            return True
        if key == "2":
            mic.clear()
            mic.arm()
            return True
        if key == "3":
            self.app.goto("SYNTH")
            return True
        if key == "4":
            self.p_monitor.set_value(0 if int(self.p_monitor.value) else 1)
            return True
        return False

    def tickers(self):
        mic = self.mic
        if mic is None:
            return []
        if mic.state == ARMED:
            return [(2, "LISTENING — MAKE A SOUND", "LISTENING")]
        if mic.state == CAPTURING:
            return [(0, "SAMPLING", "SAMPLING")]
        if mic.error:
            return [(1, mic.error, mic.error[:12])]
        if mic.captured is not None and mic.captured_path:
            name = os.path.basename(mic.captured_path)
            return [(2, f"SAMPLED: {name} → SYNTH", name)]
        return []

    def help_for(self, target) -> str:
        return {
            "1": "ARM: waits for a sound, then samples it — attack included.",
            "2": "RETRY: throw that take away and listen again.",
            "3": "Jump to SYNTH, where the new sample is already loaded.",
            "4": "MONITOR: input through the speakers. Feeds back on a laptop.",
            "ENC1": "THRESH: how loud a sound has to be to start the "
                    "sample. SHIFT: SOURCE — mic, line, USB or RESAMPLE.",
            "ENC2": "LENGTH: longest sample it will take.",
            "ENC3": "GAIN: input gain, before the threshold.",
            "ENC4": "MONITOR on/off.",
        }.get(str(target), "")

    # -- render -----------------------------------------------------------
    def render(self, fb, rect, app):
        x, y, w, h = rect
        mic = self.mic
        state = mic.state if mic is not None else IDLE

        source = SOURCE_LABELS[self.source_index]
        if self.source_index == 0 and self._driver is not None:
            source = f"MIC {getattr(self._driver, 'device_name', '')}".strip()
        elif self.source_index == 0 and self._open_failed:
            source = "NO INPUT DEVICE"
        label = {IDLE: "READY", ARMED: "LISTENING", CAPTURING: "SAMPLING",
                 READY: "SAMPLING"}[state]
        if state == IDLE and mic is not None and mic.captured is not None:
            label = f"{mic.length_s:.2f}s"
        col = palette.RED if state in (ARMED, CAPTURING) else palette.GREEN
        draw.text(fb, x + 4, y + 3, draw.clip_text(source, w // 2, SMALL),
                  palette.INK, SMALL)
        lw = draw.text_width(label, SMALL)
        draw.text(fb, x + w - 4 - lw, y + 3, label, col, SMALL)
        draw.hline(fb, x + 4, y + 13, w - 8, palette.RULE)

        gx, gy, gw, gh = x + 6, y + 18, w - 12, h - 26
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        inner = (gx + 1, gy + 1, gw - 2, gh - 2)
        if self._open_failed:
            draw.text_centered(fb, gx + gw // 2, gy + gh // 2 - 3,
                               "NO INPUT DEVICE", palette.DIM, SMALL)
        elif mic is not None and mic.captured is not None and not mic.armed:
            self._draw_capture(fb, mic, *inner)
        else:
            self._draw_meter(fb, mic, *inner)

    @staticmethod
    def _meter_scale(v: float) -> float:
        """Square root, so room tone is visible rather than a flat line.

        A linear meter shows nothing until something is already loud, which
        reads as a dead mic exactly when you need to know it is listening.
        The threshold marker uses the same curve so the two stay comparable.
        """
        return min(1.0, max(0.0, v)) ** 0.5

    def _draw_meter(self, fb, mic, x, y, w, h):
        """Scrolling input level — the proof that the mic is live.

        C5b (#98): the same arithmetic as the per-column loop it replaces, done
        once over the whole width. `_meter_scale`'s `**0.5` becomes `np.sqrt`,
        which is the same correctly-rounded value, and a column whose bar is
        flat is given zero length so the one masked write draws only the bars.
        """
        mid = y + h // 2
        if mic is None:
            draw.hline(fb, x, mid, w, palette.RULE)
            return
        H, W = fb.shape[0], fb.shape[1]
        hist = mic.meter
        n = len(hist)
        amp = h // 2 - 1
        if n:
            idx = (np.arange(w) * n) // max(1, w)
            v = np.asarray(hist, dtype=float)[idx]
        else:
            v = np.zeros(w)
        half = (np.sqrt(np.clip(v, 0.0, 1.0)) * amp).astype(np.int64)
        flat = half <= 0
        xs = x + np.arange(w)
        ok = flat & (xs >= 0) & (xs < W)
        if 0 <= mid < H and ok.any():
            fb[mid, xs[ok]] = palette.RULE
        bar = ~flat
        if bar.any():
            draw.columns(fb, x, np.where(bar, mid - half, 0),
                         np.where(bar, half * 2, 0), palette.encoder_color(0))
        # the trigger level, so THRESH is aimed rather than guessed
        t = int(self._meter_scale(float(self.p_thresh.value)) * amp)
        col = palette.RED if mic.armed else palette.DIM
        dots = np.arange(x, x + w, 4)
        dots = dots[(dots >= 0) & (dots < W)]
        for edge in (mid - t, mid + t):
            if 0 <= edge < H and len(dots):
                fb[edge, dots] = col

    @staticmethod
    def _draw_capture(fb, mic, x, y, w, h):
        data = mic.captured
        n = len(data)
        step = max(1, n // max(1, w))
        mid = y + h // 2
        for i in range(w):
            seg = data[i * step:(i + 1) * step]
            if len(seg) == 0:
                break
            half = int(float(np.max(np.abs(seg))) * (h // 2 - 1))
            draw.vline(fb, x + i, mid - half, max(1, half * 2),
                       palette.encoder_color(0))

    # -- persistence ------------------------------------------------------
    def snapshot(self) -> dict:
        # SOURCE is not in here: it is the host's, and `state.input.source` is
        # where it is saved. Two copies of one fact is the bug this avoids.
        return {"params": [self.p_thresh.value, self.p_length.value,
                           self.p_gain.value, self.p_monitor.value]}

    def restore(self, data: dict):
        for p, v in zip([self.p_thresh, self.p_length, self.p_gain,
                         self.p_monitor], data.get("params") or []):
            p.set_value(float(v))
