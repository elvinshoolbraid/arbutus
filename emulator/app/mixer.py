"""Mixer state (screen-sketches § MIXER, DDR-061/062).

Per-track gain/pan, the MASTER strip's six knobs and the peak meters the MIX
page draws. The DSP is the audio process's — `App._native_sync` pushes every
value here down with `mixer` and the meters come back on `meters`, gain
reduction included.

**Not here:** mute, solo and the per-track EQ. Those are the host's own state
since stage F1 (`mixer {track, mute|solo}`, `eq {track, ...}`) and the page
reads them back out of `state.tape.tracks[i]`. A second copy up here could only
ever disagree — solo wins over mute in the host, so a UI-side gate would be a
second answer to the same question. The master FX rack is the §6 rack on
`instrument: "master"` and lives in `state.mixer.fx`, likewise not here.
"""

import cmath
import math
from typing import Dict, List, Sequence

from .tape import NUM_TRACKS

METER_DECAY = 0.90          # per meters push

# --- the per-track EQ's ranges (DDR-062, audio/host/mixer.hpp) --------------
# The host clamps and its reply carries the whole clamped block back, so these
# are the *drawing* ranges — how far one encoder detent goes and how long the
# bar is — never a second authority on the value. Whatever comes back out of
# `state.tape.tracks[i].eq` is what the page shows.
EQ_MAX_DB = 12.0
EQ_LOW_HZ = 200.0           # LOW shelf corner (fixed, not a knob)
EQ_HIGH_HZ = 3000.0         # HIGH shelf corner
EQ_MID_F = (100.0, 8000.0)
EQ_MID_Q = (0.2, 8.0)
EQ_HP = (20.0, 2000.0)      # 20 Hz = off
EQ_LP = (200.0, 20000.0)    # 20 kHz = off
EQ_FILTER_Q = 0.70710678    # one biquad, Butterworth

EQ_FLAT: Dict[str, float] = {"low": 0.0, "mid": 0.0, "high": 0.0,
                             "mid_f": 1000.0, "mid_q": 0.7,
                             "hp": EQ_HP[0], "lp": EQ_LP[1]}


def db_text(gain: float) -> str:
    if gain <= 1e-4:
        return "-inf"
    db = 20.0 * math.log10(gain)
    return f"{db:+.1f}"


def eq_is_flat(eq: Dict[str, float]) -> bool:
    """The host's own test: flat is bit-exact bypass, so the page draws it so."""
    return (abs(float(eq.get("low", 0.0))) < 1e-6
            and abs(float(eq.get("mid", 0.0))) < 1e-6
            and abs(float(eq.get("high", 0.0))) < 1e-6
            and float(eq.get("hp", EQ_HP[0])) <= EQ_HP[0]
            and float(eq.get("lp", EQ_LP[1])) >= EQ_LP[1])


# --- the EQ curve, for drawing only ----------------------------------------
# This is not DSP: it makes no samples and touches no audio. It is the same
# five RBJ sections as `audio/host/mixer.hpp` evaluated on the unit circle, so
# the curve MIXER > EQ draws is the response the host actually applies rather
# than a cartoon of it. §8.2 hands no magnitude response up, so the alternative
# is a drawing that lies.

def _shelf(hz: float, db: float, high: bool, sr: float):
    a = 10.0 ** (db / 40.0)
    w = 2.0 * math.pi * hz / sr
    cs, sn = math.cos(w), math.sin(w)
    alpha = sn * 0.5 * math.sqrt(2.0)          # S = 1
    tw = 2.0 * math.sqrt(a) * alpha
    if high:
        return ((a * ((a + 1) + (a - 1) * cs + tw),
                 -2 * a * ((a - 1) + (a + 1) * cs),
                 a * ((a + 1) + (a - 1) * cs - tw)),
                ((a + 1) - (a - 1) * cs + tw,
                 2 * ((a - 1) - (a + 1) * cs),
                 (a + 1) - (a - 1) * cs - tw))
    return ((a * ((a + 1) - (a - 1) * cs + tw),
             2 * a * ((a - 1) - (a + 1) * cs),
             a * ((a + 1) - (a - 1) * cs - tw)),
            ((a + 1) + (a - 1) * cs + tw,
             -2 * ((a - 1) + (a + 1) * cs),
             (a + 1) + (a - 1) * cs - tw))


def _peak(hz: float, db: float, q: float, sr: float):
    a = 10.0 ** (db / 40.0)
    w = 2.0 * math.pi * hz / sr
    cs, alpha = math.cos(w), math.sin(w) / (2.0 * q)
    return ((1 + alpha * a, -2 * cs, 1 - alpha * a),
            (1 + alpha / a, -2 * cs, 1 - alpha / a))


def _pass(hz: float, highpass: bool, sr: float):
    w = 2.0 * math.pi * hz / sr
    cs, alpha = math.cos(w), math.sin(w) / (2.0 * EQ_FILTER_Q)
    if highpass:
        return (((1 + cs) * 0.5, -(1 + cs), (1 + cs) * 0.5),
                (1 + alpha, -2 * cs, 1 - alpha))
    return (((1 - cs) * 0.5, 1 - cs, (1 - cs) * 0.5),
            (1 + alpha, -2 * cs, 1 - alpha))


def _mag_db(sections, w: float) -> float:
    z = cmath.exp(-1j * w)
    total = 1.0
    for b, a in sections:
        num = b[0] + b[1] * z + b[2] * z * z
        den = a[0] + a[1] * z + a[2] * z * z
        total *= abs(num) / abs(den) if abs(den) > 1e-20 else 0.0
    return -90.0 if total < 1e-5 else 20.0 * math.log10(total)


def eq_sections(eq: Dict[str, float], sr: float = 48000.0):
    """The sections the host would build for this block (mixer.hpp `set`)."""
    ny = sr * 0.5
    out = []
    low = float(eq.get("low", 0.0))
    mid = float(eq.get("mid", 0.0))
    high = float(eq.get("high", 0.0))
    hp = float(eq.get("hp", EQ_HP[0]))
    lp = float(eq.get("lp", EQ_LP[1]))
    if low:
        out.append(_shelf(min(EQ_LOW_HZ, ny * 0.9), low, False, sr))
    if high:
        out.append(_shelf(min(EQ_HIGH_HZ, ny * 0.9), high, True, sr))
    if mid:
        out.append(_peak(min(float(eq.get("mid_f", 1000.0)), ny * 0.9), mid,
                         max(1e-3, float(eq.get("mid_q", 0.7))), sr))
    if hp > EQ_HP[0]:
        out.append(_pass(min(hp, ny * 0.9), True, sr))
    if lp < EQ_LP[1] and lp < ny:
        out.append(_pass(min(lp, ny * 0.9), False, sr))
    return out


def eq_response_db(eq: Dict[str, float], freqs: Sequence[float],
                   sr: float = 48000.0) -> List[float]:
    """dB at each frequency — what MIXER > EQ plots behind the columns."""
    sections = eq_sections(eq, sr)
    if not sections:
        return [0.0] * len(freqs)
    return [_mag_db(sections, 2.0 * math.pi * min(f, sr * 0.499) / sr)
            for f in freqs]


def log_freqs(n: int, lo: float = 30.0, hi: float = 18000.0) -> List[float]:
    if n <= 1:
        return [lo]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [lo * (ratio ** i) for i in range(n)]


class Comp:
    """The one COMP knob, and what the host says it is doing.

    The compressor itself lives in the host (`mixer {comp}`, the emulator's
    own numbers — `audio/CLAUDE.md` § MASTER COMP); `gain_reduction` is the
    lowest gain it applied since the last `meters` push, which is what the
    MASTER page draws.
    """

    def __init__(self):
        self.amount = 0.0            # 0..1, the single COMP knob
        self.gain_reduction = 1.0


class Mixer:
    def __init__(self, sample_rate: int = 48000):
        self.levels: List[float] = [0.8] * NUM_TRACKS
        self.pans: List[float] = [0.0] * NUM_TRACKS      # -1 L .. +1 R
        self.master_level = 0.8
        self.comp = Comp()
        # the rest of the MASTER strip (DDR-062, stage F1). Host units, host
        # defaults: COMP REL in seconds, DRIVE/TONE 0..1, BAL -1..+1 unity at
        # centre. Every default is the value at which that stage does nothing.
        self.comp_rel = 0.12
        self.drive = 0.0
        self.drive_tone = 0.5
        self.bal = 0.0

        self.track_peaks: List[float] = [0.0] * NUM_TRACKS
        self.master_peak = 0.0

    def reset(self):
        """FILE › NEW: back to the boot values (stage J1).

        Re-running `__init__` rather than listing the fields again, so a knob
        added to the MASTER strip cannot be forgotten here.
        """
        Mixer.__init__(self)

    # -- persistence ------------------------------------------------------
    def snapshot(self) -> dict:
        return {"levels": list(self.levels), "pans": list(self.pans),
                "master": self.master_level, "comp": self.comp.amount,
                "comp_rel": self.comp_rel, "drive": self.drive,
                "drive_tone": self.drive_tone, "bal": self.bal}

    def restore(self, data: dict):
        for i, v in enumerate((data.get("levels") or [])[:NUM_TRACKS]):
            self.levels[i] = float(v)
        for i, v in enumerate((data.get("pans") or [])[:NUM_TRACKS]):
            self.pans[i] = float(v)
        self.master_level = float(data.get("master", self.master_level))
        self.comp.amount = float(data.get("comp", self.comp.amount))
        self.comp_rel = float(data.get("comp_rel", self.comp_rel))
        self.drive = float(data.get("drive", self.drive))
        self.drive_tone = float(data.get("drive_tone", self.drive_tone))
        self.bal = float(data.get("bal", self.bal))
