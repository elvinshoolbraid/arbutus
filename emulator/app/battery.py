"""Battery, read from whatever platform this is running on (stage J2).

`screen-sketches.md` § FILE › SYSTEM: "Battery % shows on HELP-hold; a
`BATT 12%` ticker appears under 15 %." That is the whole specification, and it
assumes a device that can measure its own pack.

**The emulator's host cannot**, and until 2026-09-19 neither could the
instrument: `docs/specs/io-map.md` assigned no pin to any fuel gauge. P3's
Power sheet now carries one (io-map § P3 › Power, work-order § 3.4.1), so
there are three cases and not two:

- **the device** — `arbutus-power` (`device-os-spec.md` § 6.6, P6/#22) reads
  the gauge and publishes `/run/arbutus/power.json` every 5 s; this module
  reads that file and nothing else. It never touches I2C, sysfs or a GPIO: the
  UI is unprivileged and the service is the one that is not. No file, no
  service, no gauge — and `read()` is `None`, which is `NO TELEMETRY` on
  screen, unchanged;
- **the laptop the emulator runs on** — its own battery, below;
- **anything else** — no telemetry.

On the MacBook the emulator runs on there *is* a battery, and reporting the
laptop's is the honest thing to do: it is a real number about the machine the
instrument is running on. Anything else would be a made-up percentage, and a
made-up percentage on a battery readout is the one lie a player would act on.

    read() -> {"percent": int, "charging": bool, "source": str} | None

`None` means *no telemetry*, and every caller says so in words rather than
drawing a number. The value is cached: `pmset` is a subprocess and this is
asked for on every frame a HELP overlay is up.
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, Optional

#: how long a reading stands. A battery moves in minutes, not frames.
CACHE_S = 30.0
#: `pmset` is a subprocess; a hung one must not stall the render loop
TIMEOUT_S = 1.5

_PCT = re.compile(r"(\d+)%")

#: `device-os-spec.md` § 6.6: what `arbutus-power` publishes. `ARBUTUS_RUN`
#: moves the whole run directory for a test or a dev run, exactly as
#: `arbutus_os/paths.py` does — the two must agree, and they agree on the
#: environment variable rather than on an import, because `emulator/` never
#: imports `arbutus_os/` (it is the other way round: engine-spec §11).
POWER_JSON = os.path.join(os.environ.get("ARBUTUS_RUN", "/run/arbutus"),
                          "power.json")


def _device() -> Optional[Dict]:
    """`/run/arbutus/power.json`, or None if there is no device gauge.

    A service writing while this reads is handled by `arbutus-power`'s atomic
    rename, but a half-written file from an older service, a truncated one on a
    full filesystem or a missing key must not take the UI down mid-frame — so
    every failure here is "no telemetry".
    """
    try:
        with open(POWER_JSON) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("source") != "device":
        return None
    pct = doc.get("percent")
    if not isinstance(pct, (int, float)):
        return None                      # a gauge that cannot say how full
    return {"percent": max(0, min(100, int(pct))),
            "charging": bool(doc.get("charging")), "source": "device"}

_cache: Optional[Dict] = None
_cache_at: float = -1e9


def _mac() -> Optional[Dict]:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                             text=True, timeout=TIMEOUT_S).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = _PCT.search(out)
    if not m:
        return None                      # a desktop Mac: no battery at all
    low = out.lower()
    charging = ("ac power" in low and "discharging" not in low) \
        or "charging" in low.split(";")[-1]
    return {"percent": max(0, min(100, int(m.group(1)))),
            "charging": bool(charging), "source": "laptop"}


def _linux() -> Optional[Dict]:
    base = "/sys/class/power_supply"
    try:
        names = sorted(n for n in os.listdir(base) if n.startswith("BAT"))
    except OSError:
        return None
    for name in names:
        try:
            with open(os.path.join(base, name, "capacity")) as f:
                pct = int(f.read().strip())
            try:
                with open(os.path.join(base, name, "status")) as f:
                    status = f.read().strip().lower()
            except OSError:
                status = ""
        except (OSError, ValueError):
            continue
        return {"percent": max(0, min(100, pct)),
                "charging": status in ("charging", "full"), "source": "laptop"}
    return None


#: screenshots have to be the same on every machine, and a battery percentage
#: is the one thing on screen that is different on each of them. `verify.py`
#: sets this, so a promoted baseline shows the honest "no telemetry" state
#: rather than whichever number this laptop happened to be at.
NO_BATTERY_ENV = "ARB_NO_BATTERY"


def _read_platform() -> Optional[Dict]:
    if os.environ.get(NO_BATTERY_ENV):
        return None
    device = _device()
    if device is not None:
        return device
    if sys.platform == "darwin":
        return _mac()
    if sys.platform.startswith("linux"):
        return _linux()
    return None


def read(now: Optional[float] = None, force: bool = False) -> Optional[Dict]:
    """The current reading, or None where the platform has no telemetry."""
    global _cache, _cache_at
    now = time.monotonic() if now is None else now
    if not force and (now - _cache_at) < CACHE_S:
        return _cache
    _cache_at = now
    try:
        _cache = _read_platform()
    except Exception:                                       # noqa: BLE001
        _cache = None                     # a battery readout never raises
    return _cache


def reset_cache():
    """Tests and `--fresh` runs: forget what the last read said."""
    global _cache, _cache_at
    _cache, _cache_at = None, -1e9


def line(reading: Optional[Dict] = None, sentinel: bool = False) -> str:
    """One line for the HELP overlay (DDR-025). Never a number we do not have."""
    r = read() if reading is None and not sentinel else reading
    if not r:
        return "BATTERY — NO TELEMETRY ON THIS HOST"
    return "BATTERY %d%%%s" % (r["percent"],
                               " (CHARGING)" if r["charging"] else "")


#: below this the strip carries it whether you asked or not (§ FILE › SYSTEM)
LOW_PERCENT = 15


def low_ticker(reading: Optional[Dict] = None):
    """`("BATT 12%", "BATT 12%")` under 15 %, else None."""
    r = read() if reading is None else reading
    if not r or r.get("charging") or r["percent"] >= LOW_PERCENT:
        return None
    txt = "BATT %d%%" % r["percent"]
    return txt, txt
