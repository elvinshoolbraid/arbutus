"""The name menu: the one way the instrument names anything (D98, #818).

`control-inventory.md` § Menus: three names, the first highlighted; turning
past the third rolls three more; never a name already taken; the strip reads
`PICK A NAME · RENAME IN APP`. It commits on the push of the encoder just
turned or on the red key (the one cell of the key strip drawn red), and any
other page key cancels.

The word lists are a metaphor kept for clarity — projects are places,
patches plants and landmarks, racks tools, patterns action nouns (#819).
A project's name is two words with a space (`QUIET HARBOUR`); the other
three are file names in an engine's folder and keep the old patch rule,
two words joined by a hyphen, at most 12 characters (`WET-FERN`), so the
name you pick is the name the browser shows once the file is written.

The names are drawn from a generator seeded by what is already taken, so the
same card offers the same names (a test or a screenshot can name what it
expects), and a name the card holds is never offered: it is checked against
`taken` case-blind, since the phone's file system may be.
"""

import random
import zlib
from typing import Callable, Iterable, List, Optional

from ui import draw, palette
from ui.font import LARGE, SMALL

from . import keys as K
from .page import Page
from .params import pad_slots

STRIP = "PICK A NAME · RENAME IN APP"
STRIP_SHORT = "PICK A NAME"
#: the red key's label: what pressing it does
PICK = "PICK"
#: beside RENAME's first row, the name the project has now
CURRENT = "(CURRENT)"
SHOWN = 3

#: projects are places: an adjective and a place (`QUIET HARBOUR`). Short
#: words, so the pair fits the FILE page's large line and a phone's row.
PLACE_ADJECTIVES = (
    "QUIET", "AMBER", "SALT", "HIGH", "LOW", "STILL", "BRIGHT", "COLD",
    "WARM", "GREEN", "GREY", "RED", "BLUE", "GOLD", "SILVER", "MISTY",
    "SUNNY", "WINDY", "DUSTY", "HIDDEN", "OLD", "SILENT", "FAR", "NEAR",
    "LONG", "WIDE", "DEEP", "STONY", "SANDY", "MOSSY", "PINE", "CEDAR",
    "ARBUTUS", "NORTH", "SOUTH", "EAST", "WEST", "UPPER", "LOWER", "SLOW",
)
PLACES = (
    "HARBOUR", "BAY", "COVE", "INLET", "RIDGE", "VALLEY", "MEADOW", "FOREST",
    "ISLAND", "BEACH", "CREEK", "RIVER", "LAKE", "POND", "FIELD", "HILL",
    "PEAK", "PASS", "CANYON", "MARSH", "DUNE", "CLIFF", "POINT", "SPIT",
    "GROVE", "ORCHARD", "GARDEN", "PIER", "DOCK", "BRIDGE", "ROAD", "LANE",
    "SQUARE", "MARKET", "STATION", "TOWER", "SHORE", "SOUND", "STRAIT", "FJORD",
)

#: patches, racks and patterns share one adjective list: concrete and a bit
#: odd, a name you can say out loud and remember an hour later (the old
#: patch generator's words, `app/patch.py` before #819)
ADJECTIVES = (
    "BIG", "FLAT", "WET", "DRY", "SLOW", "FAST", "COLD", "WARM", "THIN",
    "DEEP", "LOUD", "SOFT", "HARD", "BENT", "BLUNT", "SHARP", "CHEAP",
    "TIGHT", "LOOSE", "ROUGH", "CLEAN", "DIRTY", "HOLLOW", "SQUARE",
)
#: patches are plants and landmarks
PLANTS_AND_LANDMARKS = (
    "FERN", "MOSS", "CEDAR", "ALDER", "MAPLE", "OAK", "PINE", "FIR", "YEW",
    "BIRCH", "WILLOW", "HOLLY", "IVY", "CLOVER", "THISTLE", "SALAL", "KELP",
    "LICHEN", "SORREL", "LUPIN", "BRACKEN", "REED", "SEDGE", "POPPY",
    "CAIRN", "BEACON", "ARCH", "MILL", "WELL", "GATE", "SPIRE", "DOLMEN",
    "STATUE", "CASTLE", "LIGHTHOUSE", "FORT", "WALL", "STEPS",
)
#: effect racks are tools
TOOLS = (
    "HAMMER", "SAW", "AWL", "CHISEL", "PLANE", "RASP", "WRENCH", "PLIERS",
    "LEVEL", "CLAMP", "VICE", "DRILL", "AXE", "ADZE", "TONGS", "SHOVEL",
    "RAKE", "HOE", "TROWEL", "SPANNER", "MALLET", "AUGER", "SCYTHE",
    "SICKLE", "ANVIL", "PULLEY", "WEDGE", "CROWBAR",
)
#: sequencer patterns are action nouns (`WALK`, `RUN`)
ACTIONS = (
    "WALK", "RUN", "JOG", "SKIP", "HOP", "JUMP", "LEAP", "DASH", "CRAWL",
    "MARCH", "STROLL", "TROT", "SPIN", "ROLL", "SWAY", "STOMP", "CLAP",
    "KNOCK", "TAP", "DRIFT", "GLIDE", "SWIM", "CLIMB", "DIVE", "SLIDE",
    "TWIST", "SHUFFLE", "STEP", "SWING", "RUSH", "CREEP",
)

#: a file name's length limit (the patch rule, control-inventory § Patches)
MAX_NAME = 12

#: kind -> (adjectives, nouns, joiner, longest name or None)
WORDS = {
    "project": (PLACE_ADJECTIVES, PLACES, " ", None),
    "patch": (ADJECTIVES, PLANTS_AND_LANDMARKS, "-", MAX_NAME),
    "rack": (ADJECTIVES, TOOLS, "-", MAX_NAME),
    "pattern": (ADJECTIVES, ACTIONS, "-", MAX_NAME),
}


def free_names(kind: str, taken: Iterable[str],
               current: Optional[str] = None) -> List[str]:
    """Every name of `kind` not in `taken` (case-blind) and not `current`,
    in an order the taken set decides: the same card offers the same names,
    so a test or a screenshot can say what it expects."""
    taken = {str(t).upper() for t in taken}
    seed = zlib.crc32(("%s|%s" % (kind, "|".join(sorted(taken))))
                      .encode("utf-8"))
    adjectives, nouns, joiner, longest = WORDS[kind]
    pool = [n for n in (a + joiner + b for a in adjectives for b in nouns)
            if (longest is None or len(n) <= longest)
            and n not in taken and n != current]
    random.Random(seed).shuffle(pool)
    return pool


def numbered_names(kind: str, taken: Iterable[str], n: int,
                   current: Optional[str] = None) -> List[str]:
    """`n` names past the words' end (#1180): every pair again with a number
    after it (`QUIET HARBOUR 2`, `WET-FERN-2`), in the lists' own order, none
    in `taken`. A card can hold all 1,600 project names; the menu still has
    three to show and a push still commits one."""
    taken = {str(t).upper() for t in taken}
    adjectives, nouns, joiner, longest = WORDS[kind]
    out: List[str] = []
    for number in range(2, 1000):
        for name in (a + joiner + b + joiner + str(number)
                     for a in adjectives for b in nouns):
            if (longest is None or len(name) <= longest) \
                    and name not in taken and name != current:
                out.append(name)
                if len(out) == n:
                    return out
    return out


def first_free(kind: str, taken: Iterable[str]) -> Optional[str]:
    """The name the menu would highlight first: for a file written beside
    the one being named (a patch's `.seq`), which gets no menu of its own."""
    pool = free_names(kind, taken)
    return pool[0] if pool else None


class NameMenu(Page):
    """Three names at a time, a highlight, and a pick.

    `parent` is the page it was opened from (its title and breadcrumb), and
    `opener` the encoder whose turn opened it: that encoder is the one "just
    turned" until another is, and its column is the red key's (`red_key`), so
    the key to press sits under the cell that was turned.
    """

    category = "utility"

    def __init__(self, app, parent: Page, kind: str, taken: Iterable[str],
                 on_pick: Callable[[str], None], opener: int,
                 current: Optional[str] = None,
                 crumb: Optional[List[str]] = None):
        super().__init__(app)
        self.parent = parent
        self.category = parent.category
        self.kind = kind
        self.on_pick = on_pick
        self.opener = opener
        self.current = current
        self._crumb = crumb
        #: every free name once, in an order the card decides: the rolls
        #: walk it, so no name comes round twice until the words run out
        taken = list(taken)
        self._pool = free_names(kind, taken, current)
        if len(self._pool) < SHOWN:          # the words ran out (#1180)
            self._pool += numbered_names(kind, taken,
                                         SHOWN - len(self._pool), current)
        #: every three shown so far, so turning back finds the last ones
        self._rolls: List[List[str]] = []
        self._roll_i = 0
        self.sel = 0
        #: the encoder a push must come from to commit
        self.last_turned = opener
        first = [current] if current else []
        self._rolls.append(first + self._fresh(SHOWN - len(first)))

    # -- the names ---------------------------------------------------------
    def _fresh(self, n: int) -> List[str]:
        out, self._pool = self._pool[:n], self._pool[n:]
        return out

    def names(self) -> List[str]:
        """The three on screen now."""
        return list(self._rolls[self._roll_i])

    def picked(self) -> str:
        names = self.names()
        return names[max(0, min(len(names) - 1, self.sel))]

    # -- input -------------------------------------------------------------
    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        """Any encoder moves the highlight; past the third rolls three more,
        and back past the first finds the three before."""
        if delta == 0:
            return False
        self.last_turned = idx
        step = 1 if delta > 0 else -1
        for _ in range(abs(int(delta))):
            sel = self.sel + step
            if sel >= len(self.names()):
                self._roll_i += 1
                if self._roll_i == len(self._rolls):
                    fresh = self._fresh(SHOWN)
                    if not fresh:              # the words ran out: stay put
                        self._roll_i -= 1
                        break
                    self._rolls.append(fresh)
                sel = 0
            elif sel < 0:
                if self._roll_i == 0:
                    break
                self._roll_i -= 1
                sel = len(self.names()) - 1
            self.sel = sel
        if self.app is not None:
            self.app.notify_param_changed(self.parent)
        return True

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        """The push of the encoder just turned commits; any other key here
        cancels (the number keys and page keys never reach this: `App.on_key`
        closes the menu on them and the parent decides)."""
        if not pressed:
            return False
        if K.push_index(key) == self.last_turned:
            self.commit()
        else:
            self.cancel()
        return True

    def commit(self):
        name = self.picked()
        self.close()
        self.on_pick(name)

    def cancel(self):
        self.close()

    def close(self):
        if self.app is not None and self.app.menu is self:
            self.app.close_menu()
        closed = getattr(self.parent, "menu_closed", None)
        if closed is not None:
            closed(self)

    # -- the Page contract -------------------------------------------------
    def breadcrumb(self) -> List[str]:
        base = list(self._crumb) if self._crumb else list(self.parent.breadcrumb())
        return base + ["NAME"]

    @property
    def red_key(self) -> int:
        return self.opener

    def strip_labels(self):
        labels: List[Optional[str]] = [None] * 4
        labels[self.red_key] = PICK
        return labels

    def active_strip_cell(self):
        return None

    def red_strip_cell(self):
        return self.red_key

    def tickers(self):
        from .app import TICKER_OTHER
        return [(TICKER_OTHER, STRIP, STRIP_SHORT)]

    def slots(self):
        return pad_slots([None, None, None, None])

    def render(self, fb, rect, app):
        x, y, w, h = rect
        row_h = 26
        top = y + (h - row_h * SHOWN) // 2
        for i, name in enumerate(self.names()):
            ry = top + i * row_h
            if i == self.sel:
                draw.fill_rect(fb, x + 8, ry - 4, w - 16, row_h - 4,
                               palette.INVERT_BG)
            fg = palette.INVERT_FG if i == self.sel else palette.INK
            draw.text(fb, x + 14, ry, draw.clip_text(name, w - 28, LARGE), fg,
                      LARGE)
            if self.current and name == self.current and self._roll_i == 0:
                draw.text(fb, x + w - 14 - draw.text_width(CURRENT, SMALL),
                          ry + 3, CURRENT, fg, SMALL)
