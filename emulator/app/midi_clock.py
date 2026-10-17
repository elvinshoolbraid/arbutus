"""MIDI clock in — FILE › MIDI's `CLOCK IN`, and what unlocks TEMPO's SYNC.

Stage H2 shipped `SYNC (free/midi)` with a lock on it: choosing `midi` made
BPM, BPM FINE, the `‹ ›` nudge and tap tempo inert, because there was no clock
source in the instrument and a switch that changes nothing reads as broken.
`CLOCK IN` is that source, and this is the meter behind it.

The emulator really does have a MIDI input — `drivers/desktop/midi_in.py` has
opened one since phase 3 — so this is not a stub: 0xF8 arrives on the same
port the keys do, and the BPM is measured from it. With no cable plugged in
there is simply no clock, and the page says so rather than inventing a tempo.

**24 pulses per quarter note** is the MIDI standard, so

    BPM = 60 / (seconds per pulse * 24)

Timing is the **least-squares slope** of the last bar's arrival stamps
against their pulse numbers (#1126). Each stamp is taken in rtmidi's thread,
which has to win the GIL from the UI's frame first, so a stamp is late by
0–9 ms (measured on the 3B+ at Zero clocks: half under 0.6 ms, a tail to 9)
and never early. An interval is the difference of two such stamps, and the
median of a beat's 24 wandered 127–135 BPM on a clock exact to 0.04 ms. A
line through 97 stamps spreads each one's lateness over the whole window: the
same stamps put it within 0.1 BPM of the wire. One very long interval is a
gap in the clock, not a tempo, and starts the window again.

Nothing here touches the transport. `start` / `stop` / `continue` are recorded
so the page can say whether the master is rolling, but they do not roll this
tape: DDR-057 and the tape's own COUNT-IN own when a take begins, and a
foot-pedal on someone else's sequencer is not a decision this instrument should
let a cable make. That is stage J2's deviation and it is written down in
`emulator/notes/as-built.md` § J2.
"""

import time
from typing import List, Optional

#: MIDI standard: 24 clock pulses to the quarter note
PPQN = 24

#: no pulse for this long and the clock is gone (a cable out, a stopped master)
TIMEOUT_S = 0.5

#: intervals the slope is fitted over — a bar of four beats (#1126: on the
#: 3B+'s own stamps at 132 BPM a one-beat fit was off by up to 0.9 BPM, two
#: by 0.32, four by 0.1; the error grows as the tempo squared, so two beats
#: were 0.7-1.0 off at 240). A tempo change is followed within the bar.
WINDOW = 4 * PPQN

#: an interval this many times the window's median is a gap in the clock
#: (a master that paused or dropped pulses), not a tempo: the window starts
#: again. Three clears the GIL's worst lateness at 240 BPM (10.4 ms a pulse,
#: up to ~9 ms late: an interval of under 2x).
GAP_RATIO = 3.0

#: the same range the BPM knob has (`pages/tempo.py`)
BPM_MIN, BPM_MAX = 40.0, 240.0


class MidiClock:
    """Measures an incoming MIDI clock. Fed by `drivers/desktop/midi_in.py`."""

    def __init__(self):
        #: FILE › MIDI's `CLOCK IN`. Off means the pulses are ignored entirely.
        self.enabled = False
        #: the last measured tempo, 0.0 when there is nothing to measure
        self.bpm = 0.0
        #: the master's transport, as it reports it (display only — see above)
        self.running = False
        #: arrival stamps since the window last started, at most WINDOW + 1
        self._stamps: List[float] = []
        self._seen_at: float = -1e9

    # -- the wire ---------------------------------------------------------
    def pulse(self, now: Optional[float] = None):
        """One 0xF8."""
        if not self.enabled:
            return
        now = time.monotonic() if now is None else now
        self._seen_at = now
        st = self._stamps
        if st:
            dt = now - st[-1]
            # a pulse this far apart is a gap, not a tempo: start again rather
            # than averaging a pause into the answer
            if not 0.0 < dt < TIMEOUT_S or dt > GAP_RATIO * self._median():
                st.clear()
        st.append(now)
        if len(st) > WINDOW + 1:
            del st[:-(WINDOW + 1)]
        self._recompute()

    def transport(self, kind: str, now: Optional[float] = None):
        """0xFA / 0xFB / 0xFC — start, continue, stop."""
        if not self.enabled:
            return
        now = time.monotonic() if now is None else now
        self._seen_at = now
        if kind in ("start", "continue"):
            self.running = True
            if kind == "start":
                self._stamps = []
        elif kind == "stop":
            self.running = False

    # -- what the page and TEMPO read --------------------------------------
    def alive(self, now: Optional[float] = None) -> bool:
        """Is a clock actually arriving right now?"""
        now = time.monotonic() if now is None else now
        return bool(self.enabled and self.bpm > 0.0
                    and (now - self._seen_at) < TIMEOUT_S)

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        if not self.enabled:
            self.reset()

    def reset(self):
        self.bpm = 0.0
        self.running = False
        self._stamps = []
        self._seen_at = -1e9

    # -- measurement -------------------------------------------------------
    def _median(self) -> float:
        """The window's median interval: only the gap test's yardstick, so
        infinite (no gap possible) until a quarter beat has given one."""
        st = self._stamps
        if len(st) <= PPQN // 4:
            return float("inf")
        ordered = sorted(b - a for a, b in zip(st, st[1:]))
        return ordered[len(ordered) // 2]

    def _recompute(self):
        # half a beat is enough to name a tempo and short enough to follow one
        st = self._stamps
        n = len(st)
        if n <= PPQN // 2:
            return
        # the slope of stamp against pulse number, x = 0..n-1 centred on its
        # mean: sum((x - xm) * t) / sum((x - xm)^2), the second in closed form
        xm = 0.5 * (n - 1)
        sxx = n * (n * n - 1) / 12.0
        period = sum((i - xm) * t for i, t in enumerate(st)) / sxx
        if period <= 0.0:
            return
        bpm = 60.0 / (period * PPQN)
        self.bpm = max(BPM_MIN, min(BPM_MAX, bpm))
