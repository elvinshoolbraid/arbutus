"""Device settings — what FILE › MIDI / USB / SYSTEM remember (stage J2).

Three kinds of state live in this instrument and they are deliberately kept
apart:

  * a **patch / rack / kit** is a sound, and lives in the library (§7);
  * a **project** is a session, and lives in `<library>/projects/` (J1);
  * a **setting** is the device — which MIDI channel it listens on, how bright
    the screen is, whether it auto-powers-off. It belongs to the box, not to
    the song, so it is neither in a patch nor in a project.

`screen-sketches.md` § FILE lists the settings and never says where they are
written down; no document in `v1-spec-package.md` specifies a settings file,
exactly as none specified a project file (J1's deviation). So, the same
answer: one format-1 document at the library root, written down in
`audio/library/README.md` § Settings.

    <library>/settings.json

The library root is the right home because it is the one directory that is the
*instrument's* rather than this checkout's — `emulator/state/` is wiped by
`--fresh` and is per-install working state, and a setting must survive that.
Neither the host's box scan nor the emulator's browser can see the file: the
box index only indexes folders carrying an `engine.json`, and the browser walks
`synth/`, `drum/`, `fx/`, `seq/` and `mod/`, never the root.

Every write is immediate. A setting changes once in a session, not sixty times
a second, so there is nothing here to debounce. The one exception is `held`
(#617): while the library is a computer's, the root is a bare mount point, so a
write waits in memory and `flush` puts it down once `/data` is back.
"""

import json
import os
import time
from typing import Any, Callable, Dict, Optional

SETTINGS_FORMAT = 1
SETTINGS_FILE = "settings.json"

#: MIDI IN channel: -1 is omni, otherwise 0-15 on the wire and 1-16 on screen
OMNI = -1

#: `MIDI VEL CURVE` (screen-sketches § FILE › SYSTEM). FIXED ignores the
#: velocity byte entirely, which is what the device itself does — the keys are
#: plain switches (control-inventory § chromatic keys).
VEL_CURVES = ("LINEAR", "SOFT", "HARD", "FIXED")

#: `POWER-OFF`: idle minutes before the device powers itself down. 0 = never.
POWER_OFF_MINUTES = (0, 5, 10, 30, 60)

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "midi": {"in_ch": OMNI, "out_ch": 1, "clock_in": False, "clock_out": False},
    "usb": {"mode": "OFF", "charge": True},
    "system": {"vel_curve": "LINEAR", "power_off": 0, "brightness": 80,
               "appearance": "light"},
}

#: the screen's two palettes (ui-aesthetic.md § 3, #334). The phone's own
#: appearance picks one (PROTOCOL.md § The phone's appearance); this setting is
#: where the instrument remembers it, so a boot with no phone keeps the last.
APPEARANCES = ("light", "dark")


def _merged(raw: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    if not isinstance(raw, dict):
        return out
    for section, values in raw.items():
        if section in out and isinstance(values, dict):
            for k, v in values.items():
                if k in out[section]:
                    out[section][k] = v
    return out


def shape_velocity(v: float, curve: str) -> float:
    """`MIDI VEL CURVE` applied to one incoming velocity, 0..1.

    The device's own keys have no velocity at all — plain switches — so this
    only ever shapes what arrives over MIDI (control-inventory § chromatic
    keys, DDR-048).

    Module level and pure because `arbutus_os/midi.py`'s reader thread applies
    it off a snapshot of the settings rather than off the live document
    (#128): the curve has one implementation and both threads use it.
    """
    v = max(0.0, min(1.0, float(v)))
    if curve == "FIXED":
        return 1.0
    if curve == "SOFT":                 # easier to reach the top
        return v ** 0.6
    if curve == "HARD":                 # more of the range at the bottom
        return v ** 1.7
    return v


class Settings:
    """The document, plus the two things anything outside FILE asks it."""

    def __init__(self, root: str):
        self.root = root
        self.data = _merged(self._read())
        #: #617: the App points this at `library_held`; a save asked while it
        #: is true sets `dirty` and writes nothing
        self.held: Callable[[], bool] = lambda: False
        self.dirty = False

    # -- disk -------------------------------------------------------------
    @property
    def path(self) -> str:
        return os.path.join(self.root, SETTINGS_FILE)

    def _read(self) -> Optional[dict]:
        try:
            with open(self.path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("format") != SETTINGS_FORMAT:
            return None
        return doc

    def save(self) -> bool:
        """Write the document out. A read-only library is not an error — the
        settings still work, they just do not survive the session."""
        #: #876: `dirty` until the rename lands. Cleared before the write, a
        #: refusal after the hold was forgotten and `flush` never retried it;
        #: kept on a failure, the next frame's `flush` tries again (as
        #: `persist`'s `_refused` does for the working state).
        self.dirty = True
        if self.held():
            return False
        doc = {"format": SETTINGS_FORMAT, "kind": "settings"}
        doc.update({k: dict(v) for k, v in self.data.items()})
        try:
            os.makedirs(self.root, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(doc, f, indent=1)
                f.write("\n")
            os.replace(tmp, self.path)
        except OSError:
            return False
        self.dirty = False
        return True

    def flush(self) -> bool:
        """Write what `held` kept back, if anything (#617)."""
        return self.save() if self.dirty else False

    def reset(self):
        """SYSTEM › RESET's half of DDR-064: the device, back to the box.

        All but the appearance (#693): the screen follows the phone and stays
        in the mode it last had (ui-aesthetic § 3.1), so a reset in dark mode
        leaves the screen and this setting agreeing."""
        mode = self.appearance
        self.data = {k: dict(v) for k, v in DEFAULTS.items()}
        self.data["system"]["appearance"] = mode
        self.save()

    # -- values -----------------------------------------------------------
    def get(self, section: str, key: str):
        return self.data.get(section, {}).get(key, DEFAULTS[section][key])

    def set(self, section: str, key: str, value):
        if section not in self.data:
            return
        if self.data[section].get(key) == value:
            return
        self.data[section][key] = value
        self.save()

    # -- what the rest of the instrument asks ------------------------------
    @property
    def midi_channel(self) -> int:
        """-1 = omni. `drivers/desktop/midi_in.py` reads this every message."""
        return int(self.get("midi", "in_ch"))

    @property
    def clock_in(self) -> bool:
        return bool(self.get("midi", "clock_in"))

    @property
    def vel_curve(self) -> str:
        """`MIDI VEL CURVE` (SYSTEM), upper-cased. One read, so a thread that
        only has a snapshot of the settings can carry the name and apply the
        curve itself (`shape_velocity` below, #128)."""
        return str(self.get("system", "vel_curve")).upper()

    @property
    def appearance(self) -> str:
        """`light` or `dark`, the mode the screen last had; light the first
        time, and light for anything this build does not know."""
        mode = str(self.get("system", "appearance")).lower()
        return mode if mode in APPEARANCES else "light"

    def velocity(self, v: float) -> float:
        """The live curve applied to one incoming velocity, 0..1."""
        return shape_velocity(v, self.vel_curve)

    def now_string(self) -> str:
        """`DATE / TIME` as the platform has it (SYSTEM is a read-out here)."""
        return time.strftime("%Y-%m-%d %H:%M")
