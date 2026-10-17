"""Page base class.

Each page owns a state object that is never reset by navigation (DDR-028).
Subclasses override the hooks they need; the frame renderer asks for
breadcrumb / tickers / slots / strip labels and then calls render().
"""

from typing import List, Optional, Tuple

import numpy as np

from .params import Param, pad_slots


class PageState:
    """Per-page persistent state. Attributes are free-form."""

    def __init__(self, **kw):
        self.subpage = 0
        self.slot_letter = "A"
        self.octave = 0
        self.__dict__.update(kw)


class Page:
    #: page key id, e.g. "SYNTH"
    key_id: str = "PAGE"
    #: display name in the breadcrumb
    title: str = "PAGE"
    #: performance | recording | utility (ui-aesthetic §3.1)
    category: str = "utility"
    #: labels for keys 1-4, or None
    subpage_names: Tuple[str, ...] = ()

    def __init__(self, app=None):
        self.app = app
        self.state = PageState()

    # --- content hooks ---------------------------------------------------
    def breadcrumb(self) -> List[str]:
        crumbs = [self.title]
        if self.subpage_names:
            crumbs.append(self.subpage_names[self.state.subpage])
        return crumbs

    def slots(self) -> List[Optional[Param]]:
        return pad_slots([])

    def strip_labels(self) -> List[Optional[str]]:
        if not self.subpage_names:
            return [None, None, None, None]
        return [n for n in self.subpage_names] + [None] * (4 - len(self.subpage_names))

    def active_strip_cell(self) -> Optional[int]:
        return self.state.subpage if self.subpage_names else None

    def red_strip_cell(self) -> Optional[int]:
        """The one next input, when there is one (D98, #818): a menu waiting
        for its commit draws that key's cell red (`ui-aesthetic.md` § 4)."""
        return None

    def tickers(self) -> List[Tuple[int, str, str]]:
        """(priority, long_form, short_form); lower priority renders first."""
        return []

    def help_for(self, target) -> str:
        """One line describing a control on this page (DDR-025). '' = generic."""
        return ""

    # --- input hooks -----------------------------------------------------
    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        """Return True if the page consumed the event."""
        return False

    def claims_note_keys(self) -> bool:
        """True while a gesture on this page eats chromatic keys instead of
        letting them sound — the state `on_note_key` answers from.

        Read off the tick, never as part of handling a key: it is the one
        field of L3's note snapshot where a stale answer is wrong rather than
        slightly different (`arbutus_os/fastnote.py`, #125). A page that
        overrides `on_note_key` overrides this too, and keeps the two in step.
        """
        return False

    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        slots = self.slots()
        target = slots[idx + 4] if shift else slots[idx]
        fine = False
        if shift and target is None:
            target = slots[idx]
            fine = True
        if target is None:
            return False
        target.turn(delta, fine=fine)
        if self.app is not None:
            self.app.notify_param_changed(self)
        return True

    # --- rendering -------------------------------------------------------
    def render(self, fb: np.ndarray, rect, app):
        pass

    # --- persistence -----------------------------------------------------
    def snapshot(self) -> dict:
        return {"subpage": self.state.subpage, "octave": self.state.octave}

    def restore(self, data: dict):
        self.state.subpage = int(data.get("subpage", self.state.subpage))
        self.state.octave = int(data.get("octave", self.state.octave))
