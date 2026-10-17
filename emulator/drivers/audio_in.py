"""Abstract audio *input* driver interface.

Mirrors `drivers/audio.py`: the app hands the driver a callback and the driver
calls it with mono float32 blocks. Hardware supplies its own implementation
(the WM8960 capture path); desktop uses sounddevice.
"""

from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

#: (mono float32 block) -> None
InputCallback = Callable[[np.ndarray], None]


class AudioInputDriver(ABC):
    """Abstract base class for audio capture drivers."""

    @abstractmethod
    def initialize(self, sample_rate: int, callback: InputCallback) -> bool:
        """Open the capture device. False on any failure — never raises."""

    @abstractmethod
    def start(self):
        """Begin delivering blocks to the callback."""

    @abstractmethod
    def stop(self):
        """Stop delivering blocks."""

    @abstractmethod
    def cleanup(self):
        """Release the device."""
