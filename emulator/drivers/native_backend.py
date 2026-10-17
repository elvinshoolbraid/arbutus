"""AudioDrivers for `--audio=native`: the audio process is the audio device.

Two of them, because the audio process has two clocks.

`NativeLiveAudio` — the host opened CoreAudio itself and is already making
sound. There is nothing for an `AudioDriver` to do, and that is the point: the
emulator's audio seam is *satisfied by doing nothing*, which is what proves the
Python graph is out of the path rather than merely muted.

`NativeOfflineAudio` — `--offline`. `pull(n)` asks the host to `render` n
blocks and reads the result back, presenting the `buffers` / `recorded` /
`peak` / `save_wav` surface the tests and `tools/verify.py` drive. That surface
is the old `testing/null_audio.NullAudio`'s, which is why the suite came across
to the audio process without changing how a test asks for sound.
"""

import os
import shutil
import struct
import tempfile
import weakref
from typing import List, Optional

import numpy as np

from drivers.audio import AudioCallback, AudioDriver


class NativeLiveAudio(AudioDriver):
    """The host owns the device; this is the seam kept honest."""

    def __init__(self, native):
        self.native = native
        self.sample_rate = native.sample_rate
        self.channels = 2
        self.running = False

    def initialize(self, sample_rate: int, channels: int,
                   callback: AudioCallback) -> bool:
        self.sample_rate = sample_rate
        self.channels = channels
        return True

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def cleanup(self):
        pass

    def pull(self, n_buffers: int = 1) -> np.ndarray:
        return np.zeros(0, dtype=np.float32)

    def peak(self) -> float:
        m = self.native.meters.get("master") or [0.0, 0.0]
        return float(max(m))


class NativeOfflineAudio(AudioDriver):
    """`render` as the clock, with the driver surface the tests expect."""

    def __init__(self, native, blocksize: Optional[int] = None):
        self.native = native
        self.blocksize = int(blocksize or native.host_block)
        self.sample_rate = native.sample_rate
        self.channels = 2
        self.buffers: List[np.ndarray] = []
        self.running = False
        self._dir = tempfile.mkdtemp(prefix="arbutus-native-render-")
        self._tmp = os.path.join(self._dir, "block.wav")
        # On the CM4 /tmp is a tmpfs -- this directory is RAM, and one offline
        # render's block.wav reached 226 MB (#105). Most callers are tests that
        # never reach `cleanup()`, so the removal hangs off a weakref.finalize:
        # the destructor C++ would give us. It runs when this object is
        # collected *or* at interpreter exit, whichever comes first, and at
        # most once -- __del__ is not guaranteed to run at shutdown.
        # ARBUTUS_KEEP_TMP=1 keeps the directory for a post-mortem.
        self._rm = None
        if not os.environ.get("ARBUTUS_KEEP_TMP"):
            self._rm = weakref.finalize(self, shutil.rmtree, self._dir, True)

    def initialize(self, sample_rate: int, channels: int,
                   callback: AudioCallback) -> bool:
        self.sample_rate = sample_rate
        self.channels = channels
        return True

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def cleanup(self):
        if self._rm is not None:
            self._rm()            # idempotent: finalize() runs at most once

    # -- the clock ---------------------------------------------------------
    def pull(self, n_buffers: int = 1) -> np.ndarray:
        """Render `n_buffers` blocks and hand back the mono mix."""
        frames = self.blocksize * max(1, int(n_buffers))
        reply = self.native.render(frames, self._tmp)
        if reply.get("type") == "error":
            return np.zeros(0, dtype=np.float32)
        block = read_float_wav(self._tmp)
        if block is None:
            block = np.zeros(frames, dtype=np.float32)
        self.buffers.append(block)
        return block

    @property
    def recorded(self) -> np.ndarray:
        if not self.buffers:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.buffers)

    def peak(self) -> float:
        rec = self.recorded
        return float(np.max(np.abs(rec))) if len(rec) else 0.0

    def save_wav(self, path: str) -> str:
        import wave
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = np.clip(self.recorded, -1.0, 1.0)
        pcm = (data * 32767.0).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm.tobytes())
        return path


# ---------------------------------------------------------------------------
# WAV, by hand: the host writes float32 (fmt 3), which `wave` will not read.
# ---------------------------------------------------------------------------

def read_wav(path: str):
    """-> (sample_rate, (n, channels) float32), or None."""
    try:
        with open(path, "rb") as f:
            b = f.read()
    except OSError:
        return None
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        return None
    p, fmt, ch, bits, rate, data = 12, None, None, None, None, None
    while p + 8 <= len(b):
        cid = b[p:p + 4]
        n = struct.unpack_from("<I", b, p + 4)[0]
        body = p + 8
        if cid == b"fmt ":
            fmt, ch, rate = struct.unpack_from("<HHI", b, body)
            bits = struct.unpack_from("<H", b, body + 14)[0]
        elif cid == b"data":
            data = b[body:body + n]
        p = body + n + (n & 1)
    if data is None or not ch:
        return None
    if fmt == 3 and bits == 32:
        a = np.frombuffer(data, dtype="<f4")
    elif bits == 16:
        a = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif bits == 24:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        q = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        q = np.where(q & 0x800000, q - 0x1000000, q)
        a = q.astype(np.float32) / 8388608.0
    else:
        return None
    return rate, np.ascontiguousarray(a.reshape(-1, ch).astype(np.float32))


def read_float_wav(path: str) -> Optional[np.ndarray]:
    """The mono mix of a rendered block, which is what the seam carries."""
    got = read_wav(path)
    if got is None:
        return None
    return got[1].mean(axis=1).astype(np.float32)
