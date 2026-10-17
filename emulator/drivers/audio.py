"""Abstract audio driver interface."""

from abc import ABC, abstractmethod
from typing import Callable
import numpy as np


# Audio callback type: (num_samples) -> np.ndarray
AudioCallback = Callable[[int], np.ndarray]


class AudioDriver(ABC):
    """Abstract base class for audio drivers."""

    @abstractmethod
    def initialize(self, sample_rate: int, channels: int, callback: AudioCallback) -> bool:
        """Initialize audio system.

        Args:
            sample_rate: Sample rate in Hz (e.g., 48000)
            channels: Number of audio channels (1=mono, 2=stereo)
            callback: Function called to generate audio samples

        Returns:
            True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    def start(self):
        """Start audio playback."""
        pass

    @abstractmethod
    def stop(self):
        """Stop audio playback."""
        pass

    @abstractmethod
    def cleanup(self):
        """Clean up audio resources."""
        pass
