"""Mic capture for desktop, via sounddevice.

Opened lazily (see `pages/input.py`) rather than at boot: on macOS the first
open triggers the microphone permission prompt, and nothing should ask for the
mic until someone actually visits the INPUT page. A failure here is never
fatal — `initialize()` returns False and the page says so on screen.
"""

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

from ..audio_in import AudioInputDriver, InputCallback


class SounddeviceInput(AudioInputDriver):
    BLOCKSIZE = 512

    def __init__(self, device=None):
        self.stream = None
        self.callback_fn = None
        self.sample_rate = 48000
        self.device = device
        self.device_name = "—"

    def initialize(self, sample_rate: int, callback: InputCallback) -> bool:
        if sd is None:
            print("mic: sounddevice not installed")
            return False
        self.sample_rate = sample_rate
        self.callback_fn = callback
        try:
            info = sd.query_devices(self.device, kind="input")
            self.device_name = str(info["name"])

            def stream_callback(indata, frames, time_info, status):
                # `status` carries input overflows; dropping a block during
                # monitoring is survivable and printing from the audio thread
                # is not, so it is deliberately ignored.
                block = np.asarray(indata, dtype=np.float32)
                if block.ndim > 1:
                    block = block.mean(axis=1)
                self.callback_fn(block)

            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                blocksize=self.BLOCKSIZE,
                callback=stream_callback,
                dtype=np.float32,
                device=self.device,
            )
            return True
        except Exception as e:                  # sounddevice raises broadly
            print(f"mic: could not open input device: {e}")
            self.stream = None
            return False

    def start(self):
        if self.stream is not None:
            self.stream.start()

    def stop(self):
        if self.stream is not None:
            self.stream.stop()

    def cleanup(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
