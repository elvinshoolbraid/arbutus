"""Abstract input driver interface."""

from abc import ABC, abstractmethod
from typing import Callable, Optional


# Event callback types
KeyCallback = Callable[[int, int], None]  # (row, col)
EncoderCallback = Callable[[int, int], None]  # (encoder_id, delta)
ButtonCallback = Callable[[int], None]  # (button_id)
PageCallback = Callable[[str], None]  # ('synth' | 'effect')
ModeCallback = Callable[[str], None]  # ('synth'|'drum'|'tape'|'mixer'|'com')
TrackKeyCallback = Callable[[int], None]  # (0..3 for T1..T4)
ActionCallback = Callable[[str], None]  # ('toggle_tape_bank', ...)


class InputDriver(ABC):
    """Abstract base class for input drivers."""

    def __init__(self):
        """Initialize input driver."""
        self.on_key_press: Optional[KeyCallback] = None
        self.on_key_release: Optional[KeyCallback] = None
        self.on_encoder_turn: Optional[EncoderCallback] = None
        self.on_button_press: Optional[ButtonCallback] = None
        self.on_page_change: Optional[PageCallback] = None
        self.on_mode_change: Optional[ModeCallback] = None
        self.on_track_key: Optional[TrackKeyCallback] = None
        self.on_action: Optional[ActionCallback] = None

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize input system.

        Returns:
            True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    def poll_events(self):
        """Poll for input events and call registered callbacks."""
        pass

    @abstractmethod
    def cleanup(self):
        """Clean up input resources."""
        pass

    def set_key_press_callback(self, callback: KeyCallback):
        """Register key press callback.

        Args:
            callback: Function(row, col) called when key pressed
        """
        self.on_key_press = callback

    def set_key_release_callback(self, callback: KeyCallback):
        """Register key release callback.

        Args:
            callback: Function(row, col) called when key released
        """
        self.on_key_release = callback

    def set_encoder_callback(self, callback: EncoderCallback):
        """Register encoder turn callback.

        Args:
            callback: Function(encoder_id, delta) called when encoder turned
        """
        self.on_encoder_turn = callback

    def set_button_callback(self, callback: ButtonCallback):
        """Register button press callback.

        Args:
            callback: Function(button_id) called when button pressed
        """
        self.on_button_press = callback

    def set_page_callback(self, callback: PageCallback):
        """Register page change callback.

        Args:
            callback: Function('synth'|'effect') called when page key pressed
        """
        self.on_page_change = callback

    def set_mode_callback(self, callback: ModeCallback):
        """Register mode change callback.

        Args:
            callback: Function(mode_name) called when mode key pressed
        """

        self.on_mode_change = callback

    def set_track_key_callback(self, callback: TrackKeyCallback):
        """Register T1..T4 callback.

        Args:
            callback: Function(tkey_idx) called for track keys
        """

        self.on_track_key = callback

    def set_action_callback(self, callback: ActionCallback):
        """Register generic action callback.

        Args:
            callback: Function(action_name) called for global actions
        """

        self.on_action = callback
