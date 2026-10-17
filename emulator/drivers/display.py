"""Abstract display driver interface."""

from abc import ABC, abstractmethod
import numpy as np


class DisplayDriver(ABC):
    """Abstract base class for display drivers."""

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize display hardware/window.

        Returns:
            True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    def update(self, pixels: np.ndarray):
        """Update display with new frame.

        Args:
            pixels: RGB888 pixel array (HEIGHT, WIDTH, 3)
        """
        pass

    @abstractmethod
    def should_quit(self) -> bool:
        """Check if display should quit (window closed, etc.).

        Returns:
            True if should quit, False otherwise
        """
        pass

    @abstractmethod
    def cleanup(self):
        """Clean up display resources."""
        pass
