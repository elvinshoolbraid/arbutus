"""Scripted audio input, the capture-side twin of `ScriptedInput`.

Feature scripts and unit tests push prepared audio through the same seam the
real mic uses, so the whole sampling path is provable with no audio device:

    mic = FakeMic(app.mic)
    mic.silence(0.2)
    mic.play(bottle_ping(48000))
"""

import numpy as np

from drivers.audio_in import AudioInputDriver


def bottle_ping(sample_rate: int = 48000, seconds: float = 1.2,
                f0: float = 392.0, amp: float = 0.55) -> np.ndarray:
    """A struck glass bottle: inharmonic modes over a short noise strike.

    Ratios are spread off the harmonic series so it reads as struck glass
    rather than a synth tone, and each higher mode decays faster — which is
    what gives the sound its bright attack and pure tail.
    """
    n = int(seconds * sample_rate)
    t = np.arange(n, dtype=np.float64) / sample_rate
    modes = ((1.00, 1.00, 2.5), (2.41, 0.42, 5.0),
             (4.13, 0.22, 9.0), (6.32, 0.11, 15.0))
    out = np.zeros(n, dtype=np.float64)
    for ratio, gain, decay in modes:
        out += gain * np.sin(2 * np.pi * f0 * ratio * t) * np.exp(-decay * t)

    strike = int(0.004 * sample_rate)
    rng = np.random.default_rng(7)
    out[:strike] += rng.uniform(-1.0, 1.0, strike) * np.linspace(0.6, 0.0, strike)

    out /= max(1e-9, float(np.max(np.abs(out))))
    return (out * amp).astype(np.float32)


class FakeMic(AudioInputDriver):
    """Feeds a MicCapture synchronously, in blocks, from the calling thread."""

    def __init__(self, mic=None, blocksize: int = 512):
        self.mic = mic
        self.blocksize = int(blocksize)
        self.callback_fn = None
        self.sample_rate = 48000
        self.running = False

    # -- AudioInputDriver -------------------------------------------------
    def initialize(self, sample_rate: int, callback) -> bool:
        self.sample_rate = sample_rate
        self.callback_fn = callback
        return True

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def cleanup(self):
        self.running = False

    # -- scripting --------------------------------------------------------
    def _emit(self, data: np.ndarray):
        sink = self.callback_fn or (self.mic.feed if self.mic else None)
        if sink is None:
            return
        for i in range(0, len(data), self.blocksize):
            sink(np.asarray(data[i:i + self.blocksize], dtype=np.float32))

    def play(self, data: np.ndarray):
        """Push a signal through as if the mic had heard it."""
        self._emit(np.asarray(data, dtype=np.float32))

    def silence(self, seconds: float, noise: float = 0.0005):
        """Room tone. Never digital silence — real inputs always have a floor."""
        n = int(seconds * self.sample_rate)
        rng = np.random.default_rng(3)
        self._emit((rng.uniform(-noise, noise, n)).astype(np.float32))

    def tap(self, pre: float = 0.10, post: float = 0.45, **kw):
        """Room tone, one bottle strike, then enough quiet to end the capture."""
        self.silence(pre)
        self.play(bottle_ping(self.sample_rate, **kw))
        self.silence(post)
