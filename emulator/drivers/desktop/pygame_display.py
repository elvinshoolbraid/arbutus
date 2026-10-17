"""Pygame-based display driver for desktop."""

import numpy as np
try:
    import pygame
except ImportError:
    pygame = None

from ..display import DisplayDriver


class PygameDisplay(DisplayDriver):
    """Display driver using Pygame for desktop rendering."""

    def __init__(self, width: int = 320, height: int = 240, scale: int = 3):
        """Initialize Pygame display driver.

        Args:
            width: Native display width (320 for ST7789)
            height: Native display height (240 for ST7789)
            scale: Integer scale factor for desktop window (e.g., 3 = 960x720)
        """
        self.width = width
        self.height = height
        self.scale = scale
        self.window_width = width * scale
        self.window_height = height * scale

        self.screen = None
        self.native_surface = None
        self.quit_requested = False

    def initialize(self) -> bool:
        """Initialize Pygame and create window.

        Returns:
            True if successful, False otherwise
        """
        if pygame is None:
            print("ERROR: pygame not installed. Run: pip install pygame")
            return False

        try:
            pygame.init()
            self.screen = pygame.display.set_mode((self.window_width, self.window_height))
            pygame.display.set_caption("Arbutus Emulator")

            # Create native-resolution surface for rendering
            self.native_surface = pygame.Surface((self.width, self.height))

            return True

        except Exception as e:
            print(f"ERROR: Failed to initialize Pygame: {e}")
            return False

    def update(self, pixels: np.ndarray):
        """Update display with new frame.

        Args:
            pixels: RGB888 pixel array (HEIGHT, WIDTH, 3)
        """
        if self.screen is None or self.native_surface is None:
            return

        # Convert numpy array to Pygame surface
        # pygame.surfarray expects (width, height, 3), numpy is (height, width, 3)
        # So we need to transpose the axes
        pixels_transposed = np.transpose(pixels, (1, 0, 2))
        pygame.surfarray.blit_array(self.native_surface, pixels_transposed)

        # Scale up to window size
        scaled = pygame.transform.scale(self.native_surface,
                                        (self.window_width, self.window_height))

        # Blit to screen
        self.screen.blit(scaled, (0, 0))
        pygame.display.flip()

    def should_quit(self) -> bool:
        """Check if window was closed.

        Returns:
            True if should quit, False otherwise
        """
        return self.quit_requested

    def cleanup(self):
        """Clean up Pygame resources."""
        if pygame is not None:
            pygame.quit()
