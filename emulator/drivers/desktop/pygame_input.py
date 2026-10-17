"""Pygame-based input driver for desktop keyboard/mouse."""

try:
    import pygame
except ImportError:
    pygame = None

from ..input import InputDriver


class PygameInput(InputDriver):
    """Input driver using Pygame for keyboard and mouse events.

    Maps computer keyboard to 54-key matrix (5 rows × 11 cols).
    Maps scroll wheel and arrow keys to 4 rotary encoders.
    """

    def __init__(self, display_driver=None):
        """Initialize Pygame input driver.

        Args:
            display_driver: PygameDisplay instance (to detect quit events)
        """
        super().__init__()
        self.display_driver = display_driver

        # Keyboard mapping: computer key -> (row, col) on hardware matrix
        # Hardware: 5 rows × 11 cols = 54 keys
        # Map QWERTY rows to hardware rows 0-4
        self.key_map = self._build_key_map()

        # Track currently pressed keys to detect changes
        self.pressed_keys = set()

        # Encoder state (for mouse wheel / arrow keys)
        self.encoder_scroll_sensitivity = 1  # Notches per encoder step

    def _build_key_map(self) -> dict:
        """Build mapping from pygame keys to (row, col).

        Returns:
            Dict mapping pygame key constants to (row, col) tuples
        """
        if pygame is None:
            return {}

        # Map 5 QWERTY rows to hardware rows 0-4
        # Each row has up to 11 keys
        key_map = {}

        # Row 0: Number row (5-0, -, skipping 1-4 which are T keys)
        row_0_keys = [
            pygame.K_5, pygame.K_6, pygame.K_7,
            pygame.K_8, pygame.K_9, pygame.K_0,
            pygame.K_MINUS
        ]
        for col, key in enumerate(row_0_keys):
            key_map[key] = (0, col)

        # Row 1: QWERTY row (Q-P)
        row_1_keys = [
            pygame.K_q, pygame.K_w, pygame.K_e, pygame.K_r, pygame.K_t,
            pygame.K_y, pygame.K_u, pygame.K_i, pygame.K_o, pygame.K_p,
            pygame.K_LEFTBRACKET
        ]
        for col, key in enumerate(row_1_keys):
            key_map[key] = (1, col)

        # Row 2: ASDF row (A-;)
        row_2_keys = [
            pygame.K_a, pygame.K_s, pygame.K_d, pygame.K_f, pygame.K_g,
            pygame.K_h, pygame.K_j, pygame.K_k, pygame.K_l, pygame.K_SEMICOLON,
            pygame.K_QUOTE
        ]
        for col, key in enumerate(row_2_keys):
            key_map[key] = (2, col)

        # Row 3: ZXCV row (Z-/)
        row_3_keys = [
            pygame.K_z, pygame.K_x, pygame.K_c, pygame.K_v, pygame.K_b,
            pygame.K_n, pygame.K_m, pygame.K_COMMA, pygame.K_PERIOD,
            pygame.K_SLASH
        ]
        for col, key in enumerate(row_3_keys):
            key_map[key] = (3, col)

        # Row 4: Bottom row (space only; modifiers/arrows handled separately)
        row_4_keys = [
            pygame.K_SPACE
        ]
        for col, key in enumerate(row_4_keys):
            key_map[key] = (4, col)

        return key_map

    def initialize(self) -> bool:
        """Initialize Pygame input (no-op, Pygame already initialized by display).

        Returns:
            True if successful
        """
        if pygame is None:
            print("ERROR: pygame not installed. Run: pip install pygame")
            return False
        return True

    def poll_events(self):
        """Poll Pygame events and trigger callbacks."""
        if pygame is None:
            return

        for event in pygame.event.get():
            # Handle quit event
            if event.type == pygame.QUIT:
                if self.display_driver:
                    self.display_driver.quit_requested = True

            # Handle key press
            elif event.type == pygame.KEYDOWN:
                mods = pygame.key.get_mods()

                # Escape to quit (highest priority)
                if event.key == pygame.K_ESCAPE:
                    if self.display_driver:
                        self.display_driver.quit_requested = True

                # Mode keys (F1..F5)
                elif event.key == pygame.K_F1:
                    if self.on_mode_change:
                        self.on_mode_change('synth')

                elif event.key == pygame.K_F2:
                    if self.on_mode_change:
                        self.on_mode_change('drum')

                elif event.key == pygame.K_F3:
                    if mods & pygame.KMOD_SHIFT:
                        if self.on_action:
                            self.on_action('toggle_tape_bank')
                    elif self.on_mode_change:
                        self.on_mode_change('tape')

                elif event.key == pygame.K_F4:
                    if self.on_mode_change:
                        self.on_mode_change('mixer')

                elif event.key == pygame.K_F5:
                    if self.on_mode_change:
                        self.on_mode_change('com')

                # T keys: 1..4, with shift for alternate menu actions
                elif event.key == pygame.K_1:
                    if mods & pygame.KMOD_SHIFT:
                        if self.on_button_press:
                            self.on_button_press(0)  # Shift+T1
                    elif self.on_track_key:
                        self.on_track_key(0)

                elif event.key == pygame.K_2:
                    if mods & pygame.KMOD_SHIFT:
                        if self.on_button_press:
                            self.on_button_press(1)  # Shift+T2
                    elif self.on_track_key:
                        self.on_track_key(1)

                elif event.key == pygame.K_3:
                    if mods & pygame.KMOD_SHIFT:
                        if self.on_button_press:
                            self.on_button_press(2)  # Shift+T3
                    elif self.on_track_key:
                        self.on_track_key(2)

                elif event.key == pygame.K_4:
                    if mods & pygame.KMOD_SHIFT:
                        if self.on_button_press:
                            self.on_button_press(3)  # Shift+T4
                    elif self.on_track_key:
                        self.on_track_key(3)

                # Arrow keys for encoder simulation
                # Plain arrows = enc 0/1, Shift+arrows = enc 2, Ctrl+arrows = enc 3
                elif event.key == pygame.K_UP:
                    if self.on_encoder_turn:
                        if mods & pygame.KMOD_CTRL:
                            self.on_encoder_turn(3, 1)
                        elif mods & pygame.KMOD_SHIFT:
                            self.on_encoder_turn(2, 1)
                        else:
                            self.on_encoder_turn(0, 1)

                elif event.key == pygame.K_DOWN:
                    if self.on_encoder_turn:
                        if mods & pygame.KMOD_CTRL:
                            self.on_encoder_turn(3, -1)
                        elif mods & pygame.KMOD_SHIFT:
                            self.on_encoder_turn(2, -1)
                        else:
                            self.on_encoder_turn(0, -1)

                elif event.key == pygame.K_LEFT:
                    if self.on_encoder_turn:
                        if mods & pygame.KMOD_SHIFT:
                            self.on_encoder_turn(3, -1)
                        else:
                            self.on_encoder_turn(1, -1)

                elif event.key == pygame.K_RIGHT:
                    if self.on_encoder_turn:
                        if mods & pygame.KMOD_SHIFT:
                            self.on_encoder_turn(3, 1)
                        else:
                            self.on_encoder_turn(1, 1)

                # Note keys
                elif event.key in self.key_map:
                    row, col = self.key_map[event.key]
                    key_id = (row, col)
                    if key_id not in self.pressed_keys:
                        self.pressed_keys.add(key_id)
                        if self.on_key_press:
                            self.on_key_press(row, col)

            # Handle key release
            elif event.type == pygame.KEYUP:
                if event.key in self.key_map:
                    row, col = self.key_map[event.key]

                    # Track as released
                    key_id = (row, col)
                    if key_id in self.pressed_keys:
                        self.pressed_keys.remove(key_id)

                        # Trigger callback
                        if self.on_key_release:
                            self.on_key_release(row, col)

            # Handle mouse wheel for encoder simulation
            elif event.type == pygame.MOUSEWHEEL:
                if self.on_encoder_turn:
                    # event.y > 0 = scroll up (clockwise)
                    # event.y < 0 = scroll down (counter-clockwise)
                    if event.y != 0:
                        self.on_encoder_turn(1, event.y)  # Encoder 1

    def cleanup(self):
        """Clean up input resources (no-op for Pygame input)."""
        pass
