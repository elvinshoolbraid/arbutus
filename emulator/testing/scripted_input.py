"""InputDriver fed from a list of events — the test seam for input.

Events are tuples:
    ("key", key_id, pressed)      key_id from app.keys (str or Key)
    ("enc", encoder_idx, delta)
    ("wait", n)                   no-op, advances the script by one poll
"""

from typing import Iterable, List, Optional, Tuple

from drivers.input import InputDriver

Event = Tuple


class ScriptedInput(InputDriver):
    def __init__(self, events: Optional[Iterable[Event]] = None):
        super().__init__()
        self.events: List[Event] = list(events or [])
        #: new-style callback: (key_id: str, pressed: bool)
        self.on_control = None
        self.exhausted = False

    def initialize(self) -> bool:
        return True

    def feed(self, *events: Event):
        self.events.extend(events)

    def poll_events(self):
        """Dispatch every queued event (tests drive frame-by-frame via step)."""
        while self.events:
            self.step()

    def step(self) -> bool:
        """Dispatch a single event. Returns False when the script is empty."""
        if not self.events:
            self.exhausted = True
            return False
        ev = self.events.pop(0)
        kind = ev[0]
        if kind == "key" and self.on_control:
            key = ev[1]
            self.on_control(getattr(key, "value", key), bool(ev[2]))
        elif kind == "enc" and self.on_encoder_turn:
            self.on_encoder_turn(int(ev[1]), int(ev[2]))
        return True

    def cleanup(self):
        pass
