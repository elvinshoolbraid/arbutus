"""HELP page + the two hold overlays (DDR-025 / DDR-033).

Tap HELP  -> this page, which teaches the hold gesture.
Hold HELP -> overlay describing the page you are on.
Hold HELP + any key or encoder -> overlay describing that control here.

Per-page copy lives in PAGE_HELP; per-control copy comes from the page's
`help_for(target)`, with GLOBAL_HELP as the fallback for controls that mean
the same thing everywhere (control-inventory).
"""

from typing import List

from app import battery
from app.page import Page
from ui import draw, palette
from ui.font import LARGE, SMALL

#: one line per page (screen-sketches, control-inventory); keyed by page
#: key id. #1123 brought it past the demo (findings m-emu-pages spec 9).
PAGE_HELP = {
    "SYNTH": "SYNTH: play the synth. 1-4 pick ENGINE/ENV/FX/MOD.",
    "DRUM": "DRUM: 24 one-shots. Params edit the last key you hit.",
    "TRACK": "TRACK: the 8-track tape, two banks of 4. 1-4 select a track; SHIFT+1-4 flip the bank.",
    "MIXER": "MIXER: levels, pans, master. 1-4 pick the subpage.",
    "SEQ": "SEQ: hold 1 (REC) to enter steps, hold a key to play them.",
    "INPUT": "INPUT: sample external audio in. 1 arms, 2 retries.",
    "HELP": "HELP: you are here. Hold HELP anywhere for context.",
    "FILE": "USB/FILE: 1-4 = PROJECT, MIDI, USB, SYSTEM.",
    "TEMPO": "TEMPO: BPM, tap tempo and the metronome click.",
}

#: controls that mean the same thing on every page
GLOBAL_HELP = {
    "PLAY": "PLAY: start the tape. Works on every page.",
    "STOP": "STOP: stop; tap twice to return to the start.",
    "REC": "REC: arm the selected track; REC + PLAY records.",
    "SHIFT": "SHIFT: hold to reveal the brown row of params.",
    "SLICE": "SLICE: split the clip at the playhead (SHIFT: TAPE merge).",
    "LIFT": "LIFT: take the clip under the cursor (SHIFT: all 8).",
    "DROP": "DROP: drop the lifted clip at the playhead.",
    "LEFT": "‹ : octave down, or rewind on TRACK.",
    "RIGHT": "› : octave up, or fast-forward on TRACK.",
    "TEMPO": "TEMPO: the tempo page; repeated taps set the BPM.",
    "HELP": "HELP: hold me and press anything to learn it.",
    "A": "A: recall slot A (IN on TRACK).",
    "B": "B: recall slot B (OUT on TRACK).",
    "C": "C: recall slot C (LOOP on TRACK).",
    "X": "X: recall slot X (REV on TRACK).",
    "Y": "Y: recall slot Y (SPEED on TRACK).",
    "Z": "Z: recall slot Z (FILTER on TRACK).",
    "1": "1: first cell of the bottom strip.",
    "2": "2: second cell of the bottom strip.",
    "3": "3: third cell of the bottom strip.",
    "4": "4: fourth cell of the bottom strip.",
}

HELP_COPY = [
    "HOLD HELP — anywhere.",
    "",
    "Hold HELP alone: what this page is.",
    "Hold HELP + any key or encoder:",
    "what that control does right here.",
    "",
    "Release to go back. Help never",
    "navigates and never changes a value.",
]


def _wrap(text: str, width: int = 36) -> List[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def control_help(app, target) -> str:
    page = app.current_page
    txt = ""
    fn = getattr(page, "help_for", None)
    if fn is not None:
        txt = fn(target) or ""
    if not txt:
        txt = GLOBAL_HELP.get(str(target), "")
    if not txt and str(target).startswith("NOTE_"):
        txt = f"KEY: plays {app.active_instrument_id}, the active instrument."
    if not txt and str(target).startswith("ENC"):
        slots = page.slots()
        idx = int(str(target)[3]) - 1
        slot = slots[idx]
        shifted = slots[idx + 4]
        name = slot.name if slot else "nothing"
        extra = f" (SHIFT: {shifted.name})" if shifted else ""
        txt = f"{target}: {name} on this page{extra}."
    if not txt:
        txt = f"{target}: no meaning on this page."
    return txt


def overlay_lines(app) -> List[str]:
    """What the HELP overlay shows: page copy, or the touched control.

    The last line is always the **battery** (screen-sketches § FILE › SYSTEM:
    "Battery % shows on HELP-hold"). HELP-hold is the one gesture available on
    every page from every state, which is why the spec puts it there and not
    on a status bar; the reading is the platform's, and where there is none it
    says so rather than drawing a number nobody measured (`app/battery.py`).
    """
    page = app.current_page
    head = " › ".join(page.breadcrumb())
    target = getattr(app, "help_target", None)
    if target is None:
        body = PAGE_HELP.get(getattr(page, "key_id", ""), "")
        if not body:
            body = f"{head}: no help written yet."
        lines = [f"HELP — {head}", ""] + _wrap(body)
        lines += ["", "Still holding HELP? Press any key or",
                  "turn an encoder to learn it."]
    else:
        lines = [f"HELP — {head}", ""] + _wrap(control_help(app, target))
    return lines[:7] + ["", battery.line()]


class HelpPage(Page):
    key_id = "HELP"
    title = "HELP"
    category = "utility"
    subpage_names = ()

    def render(self, fb, rect, app):
        x, y, w, h = rect
        draw.text(fb, x + 8, y + 6, "HOLD HELP", palette.INK, LARGE)
        draw.hline(fb, x + 8, y + 20, w - 16, palette.RULE)
        ty = y + 26
        for line in HELP_COPY[1:]:
            draw.text(fb, x + 10, ty, draw.clip_text(line, w - 20, SMALL),
                      palette.INK if line else palette.DIM, SMALL)
            ty += 10

    def help_for(self, target: str) -> str:
        return GLOBAL_HELP.get(str(target), "")
