"""UI-facing parameter slot (parameter-framework.md).

A parameter slot as the UI sees it, minus any colour field — colour is
positional (slot n renders in encoder n mod 4's colour). Natively the slots are
built from the box's descriptor in the host's `state` (`app/native_engine.py`).
"""

from enum import Enum
from typing import Callable, List, Optional


class StepType(str, Enum):
    LINEAR = "LINEAR"
    LOGARITHMIC = "LOGARITHMIC"
    DISCRETE = "DISCRETE"


class Param:
    def __init__(self, name: str, min_val: float = 0.0, max_val: float = 1.0,
                 default: float = 0.0, step_type: StepType = StepType.LINEAR,
                 step_size: float = 0.01, unit: str = "",
                 display_format: str = "{:.2f}",
                 choices: Optional[List[str]] = None,
                 on_change: Optional[Callable[[float], None]] = None):
        self.name = name
        self.min_val = float(min_val)
        self.max_val = float(max_val)
        self.step_type = step_type
        self.step_size = float(step_size)
        self.unit = unit
        self.display_format = display_format
        self.choices = choices
        self.on_change = on_change
        #: overrides display_format when a fixed format cannot span the range
        self.formatter: Optional[Callable[[float], str]] = None
        self.value = float(default)
        #: draw the cell dim — an armed/off control that is still adjustable
        self.dimmed = False
        #: ring the cell — "this is one of the knobs the current gesture
        #: means". Rack copy/paste modes set it (DDR-039).
        self.highlight = False
        #: Normalised live value supplied by the MOD rack.  The ordinary bar
        #: remains the knob value; the renderer adds this second, thin marker
        #: so a moving parameter does not lie about where its knob is
        #: (DDR-051).  None means this slot is not currently modulated.
        self.ghost: Optional[float] = None
        #: the slot exists but this engine does not use it — DRUM › ENV's
        #: SUS and REL (DDR-053). Named and drawn `dimmed`, but the encoder
        #: does nothing, which is what "greyed" has to mean to a finger.
        self.locked = False

    # -- value plumbing ---------------------------------------------------
    def _clamp(self, v: float) -> float:
        return max(self.min_val, min(self.max_val, v))

    def set_value(self, v: float):
        self.value = self._clamp(v)
        if self.on_change:
            self.on_change(self.value)

    def get_normalized(self) -> float:
        span = self.max_val - self.min_val
        return 0.0 if span == 0 else (self.value - self.min_val) / span

    def set_normalized(self, n: float):
        self.set_value(self.min_val + n * (self.max_val - self.min_val))

    def turn(self, detents: int, fine: bool = False):
        """One detent = one step of the slot's range. No acceleration."""
        if self.locked:
            return
        scale = 0.1 if fine else 1.0
        if self.step_type is StepType.LOGARITHMIC:
            factor = (1.0 + self.step_size * scale) ** detents
            base = self.value if self.value > 0 else max(self.min_val, 1e-6)
            self.set_value(base * factor)
        elif self.step_type is StepType.DISCRETE:
            self.set_value(round(self.value + detents))
        else:
            self.set_value(self.value + detents * self.step_size * scale)

    # -- display ----------------------------------------------------------
    def format_value(self) -> str:
        if self.choices:
            idx = int(round(self.value))
            idx = max(0, min(len(self.choices) - 1, idx))
            return self.choices[idx]
        if self.formatter is not None:
            return self.formatter(self.value)
        try:
            s = self.display_format.format(self.value)
        except (ValueError, KeyError):
            s = f"{self.value:.2f}"
        return f"{s}{self.unit}" if self.unit else s

    def is_discrete(self) -> bool:
        return self.step_type is StepType.DISCRETE or bool(self.choices)


def pad_slots(slots: List[Optional[Param]]) -> List[Optional[Param]]:
    """Always exactly 8 entries."""
    out = list(slots)[:8]
    while len(out) < 8:
        out.append(None)
    return out
