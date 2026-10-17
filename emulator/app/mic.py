"""Live sampling from an audio input (INPUT page).

The state machine and the DSP live here, with no driver knowledge, so the whole
path is testable by pushing blocks in by hand (`testing/fake_mic.py`). A driver
only has to call `feed()` with mono float32 blocks.

    IDLE ──arm()──▶ ARMED ──transient──▶ CAPTURING ──silence──▶ READY
     ▲                │                                            │
     └───── clear() ──┴────────────────────────────────────────────┘

Why a threaded ring buffer instead of just recording from the keypress: a
struck bottle's attack is the part that makes it sound like glass, and by the
time a human has heard the tap and pressed a key that attack is long gone.
`ARMED` waits on the transient itself and the ring hands back the few
milliseconds *before* it, so the capture starts ahead of the strike.

Work split across threads: `feed()` runs on the audio thread and only ever
appends (it must not miss samples, so it does no disk I/O and no allocation
beyond the block list). Trimming, normalising and saving happen on the UI
thread in `poll()`.
"""

import os
import struct
import threading
import wave
from typing import List, Optional

import numpy as np

IDLE, ARMED, CAPTURING, READY = "IDLE", "ARMED", "CAPTURING", "READY"

RING_SECONDS = 1.0          # rolling pre-roll history
PREROLL_S = 0.020           # kept before the transient that triggered capture
RELEASE_S = 0.350           # below-threshold time that ends a capture
TAIL_S = 0.050              # kept after the last sound, before the fade
FLOOR = 0.008               # absolute silence floor for trimming
TARGET_PEAK = 0.90          # normalise the kept sample to this
FADE_IN_S = 0.002
FADE_OUT_S = 0.030          # long enough that a loop point never clicks

METER_COLUMNS = 160         # scrolling level history (one per framebuffer px)
METER_SUBDIVISIONS = 4      # sub-peaks per audio block, for a smooth scroll


def trim_normalize(data: np.ndarray, sample_rate: int,
                   floor: float = FLOOR,
                   preroll_s: float = PREROLL_S,
                   tail_s: float = TAIL_S,
                   target: float = TARGET_PEAK,
                   fade_in_s: float = FADE_IN_S,
                   fade_out_s: float = FADE_OUT_S) -> Optional[np.ndarray]:
    """Cut silence off both ends, normalise, and fade so it never clicks.

    Returns None when the capture holds nothing above `floor`.
    """
    data = np.asarray(data, dtype=np.float32)
    if data.size == 0:
        return None
    above = np.abs(data) > floor
    if not above.any():
        return None
    first = int(np.argmax(above))
    last = len(data) - int(np.argmax(above[::-1]))

    i0 = max(0, first - int(preroll_s * sample_rate))
    i1 = min(len(data), last + int(tail_s * sample_rate))
    seg = np.array(data[i0:i1], dtype=np.float32)       # copy: we edit in place
    if seg.size < 2:
        return None

    peak = float(np.max(np.abs(seg)))
    if peak > 0:
        seg *= (target / peak)

    # The fade-in may never reach into the attack: on a struck sound the peak
    # is a couple of milliseconds in, and a fixed fade there files the transient
    # off and takes the "glass" with it. The pre-roll means real captures have
    # quiet to fade across, so cap the fade at the run-up to the attack.
    attack = int(np.argmax(np.abs(seg) > 0.5 * target))
    fi = min(int(fade_in_s * sample_rate), attack, seg.size // 2)
    fo = min(int(fade_out_s * sample_rate), seg.size // 2)
    if fi > 0:
        seg[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
    if fo > 0:
        seg[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
    return seg


def write_wav(path: str, data: np.ndarray, sample_rate: int):
    """16-bit mono, the format `arbutus/sampler` reads back."""
    clipped = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())


def next_sample_path(directory: str, stem: str = "mic") -> str:
    """`mic-01.wav`, `mic-02.wav`, … — never overwrites an earlier take."""
    os.makedirs(directory, exist_ok=True)
    existing = {f.lower() for f in os.listdir(directory)}
    n = 1
    while f"{stem}-{n:02d}.wav" in existing:
        n += 1
    return os.path.join(directory, f"{stem}-{n:02d}.wav")


class MicCapture:
    """Ring buffer + threshold trigger + trim, with no driver knowledge."""

    def __init__(self, sample_rate: int = 48000, directory: str = ".",
                 max_seconds: float = 4.0):
        self.sample_rate = int(sample_rate)
        self.directory = directory
        self.max_seconds = float(max_seconds)

        self.threshold = 0.06       # trigger level, set from the page's param
        self.gain = 1.0
        self.max_len = 2.0          # capture length ceiling, seconds

        self._lock = threading.Lock()
        ring_n = max(1, int(RING_SECONDS * self.sample_rate))
        self._ring = np.zeros(ring_n, dtype=np.float32)
        self._ring_pos = 0
        self._ring_filled = 0

        self.state = IDLE
        self._blocks: List[np.ndarray] = []
        self._captured_n = 0
        self._quiet_n = 0
        self._pending: Optional[np.ndarray] = None

        self.captured: Optional[np.ndarray] = None
        self.captured_path: Optional[str] = None
        self.level = 0.0
        self.peak_hold = 0.0
        self.meter = np.zeros(METER_COLUMNS, dtype=np.float32)

        #: INPUT MONITOR. The input goes to the speakers in the audio process
        #: (`monitor {on, gain}`), never through this object: the capture path
        #: and the monitoring path have no reason to share a buffer.
        self.monitor = False

        self.error: Optional[str] = None

    # -- audio thread -----------------------------------------------------
    def feed(self, block: np.ndarray):
        """One mono block from the input driver. Appends only; no I/O here."""
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return
        if self.gain != 1.0:
            block = block * self.gain

        with self._lock:
            self._push_ring(block)
            self._push_meter(block)
            if self.state == ARMED:
                idx = self._transient_index(block)
                if idx is not None:
                    self._begin_capture(block, idx)
            elif self.state == CAPTURING:
                self._continue_capture(block)

    def _push_ring(self, block: np.ndarray):
        n = len(self._ring)
        b = block[-n:] if block.size > n else block
        k = b.size
        pos = self._ring_pos
        if pos + k <= n:
            self._ring[pos:pos + k] = b
        else:
            head = n - pos
            self._ring[pos:] = b[:head]
            self._ring[:k - head] = b[head:]
        self._ring_pos = (pos + k) % n
        self._ring_filled = min(n, self._ring_filled + k)

    def _push_meter(self, block: np.ndarray):
        peak = float(np.max(np.abs(block)))
        self.level = peak
        self.peak_hold = max(peak, self.peak_hold * 0.92)
        parts = np.array_split(block, METER_SUBDIVISIONS)
        vals = np.array([float(np.max(np.abs(p))) if p.size else 0.0
                         for p in parts], dtype=np.float32)
        self.meter = np.concatenate([self.meter[len(vals):], vals])

    def _transient_index(self, block: np.ndarray) -> Optional[int]:
        hot = np.abs(block) > self.threshold
        return int(np.argmax(hot)) if hot.any() else None

    def _ring_tail(self, count: int) -> np.ndarray:
        """The most recent `count` samples already written to the ring."""
        n = len(self._ring)
        count = min(count, self._ring_filled, n)
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        start = (self._ring_pos - count) % n
        if start + count <= n:
            return np.array(self._ring[start:start + count], dtype=np.float32)
        head = n - start
        return np.concatenate([self._ring[start:], self._ring[:count - head]])

    def _begin_capture(self, block: np.ndarray, idx: int):
        # the ring already holds this block, so the pre-roll is measured back
        # from the transient rather than from the start of the block
        back = int(PREROLL_S * self.sample_rate) + (len(block) - idx)
        pre = self._ring_tail(back)
        self.state = CAPTURING
        self._blocks = [pre]
        self._captured_n = pre.size
        self._quiet_n = 0

    def _continue_capture(self, block: np.ndarray):
        self._blocks.append(np.array(block, dtype=np.float32))
        self._captured_n += block.size

        if float(np.max(np.abs(block))) < self.threshold * 0.35:
            self._quiet_n += block.size
        else:
            self._quiet_n = 0

        done = (self._quiet_n >= RELEASE_S * self.sample_rate
                or self._captured_n >= self.max_len * self.sample_rate)
        if done:
            self._pending = np.concatenate(self._blocks)
            self._blocks = []
            self.state = READY

    # -- UI thread --------------------------------------------------------
    def poll(self) -> bool:
        """Finish a capture: trim, normalise, save. True when one just landed."""
        with self._lock:
            raw = self._pending
            self._pending = None
        if raw is None:
            return False

        seg = trim_normalize(raw, self.sample_rate)
        if seg is None:
            self.error = "NOTHING CAPTURED"
            self.state = IDLE
            return False

        self.captured = seg
        self.state = IDLE           # the take is in hand; back to monitoring
        self.error = None
        try:
            path = next_sample_path(self.directory)
            write_wav(path, seg, self.sample_rate)
            self.captured_path = path
        except OSError as e:
            self.captured_path = None
            self.error = f"SAVE FAILED: {e.strerror or 'error'}"
        return True

    def arm(self):
        with self._lock:
            self.state = ARMED
            self._blocks = []
            self._captured_n = 0
            self._quiet_n = 0
            self._pending = None
        self.error = None

    def disarm(self):
        with self._lock:
            if self.state in (ARMED, CAPTURING):
                self.state = IDLE
                self._blocks = []

    def clear(self):
        """Throw the take away and go back to monitoring."""
        with self._lock:
            self.state = IDLE
            self._blocks = []
            self._pending = None
        self.captured = None
        self.captured_path = None
        self.error = None

    @property
    def armed(self) -> bool:
        return self.state in (ARMED, CAPTURING)

    @property
    def length_s(self) -> float:
        return 0.0 if self.captured is None else len(self.captured) / self.sample_rate
