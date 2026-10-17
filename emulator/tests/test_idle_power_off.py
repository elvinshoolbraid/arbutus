"""§ 7.2's idle row on the real `App` — `device-os-spec.md`, #176.

FILE › SYSTEM has drawn and stored `POWER-OFF` since stage J2 and **nothing
read it**: there was no idle timer anywhere and nothing in the tree sent
`poweroff`. The audit (#31) found it; this is the half of the fix that lives in
the UI, which is every fact the rule is made of except one.

The rule, in § 7.2's own words: "idle >= `settings.system.power_off` minutes
(0 = never), no transport rolling, no take open, no client on the network
sockets". The last one is `arbutus-net`'s listener and cannot be seen from
here, so it arrives as an argument and `arbutus_os/tests/test_power_pump.py`
is where it is read off the wire.

This process never powers anything off — there is no device here and a laptop
is not an instrument. What is proved is the predicate, and that a key press
really moves the clock it is measured against.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.tape import PLAYING, RECORDING, STOPPED           # noqa: E402
from tests.native_support import (app_for, host, library_copy,  # noqa: E402
                                  state_dir)

STATE = state_dir("idle-power-off")
#: this case writes `settings.json` (`POWER-OFF`), so it writes into a
#: throwaway copy — nothing here may touch the committed library.
LIB = os.path.join(HERE, "state", "idle-power-off-library")
MINUTES = 10
LATER = MINUTES * 60


class IdleCase(unittest.TestCase):
    def setUp(self):
        library_copy(LIB)
        self.native = host(self, library_root=LIB)
        self.app, self.audio = app_for(self.native, STATE, fresh=True,
                                       library_root=LIB)
        self.addCleanup(self.audio.stop)
        self.app.settings.set("system", "power_off", MINUTES)
        self.app.note_input(0.0)

    def due(self, at=LATER, **kw):
        return self.app.idle_power_off_due(at, **kw)


class TheRule(IdleCase):
    def test_ten_quiet_minutes_are_due_and_nine_are_not(self):
        self.assertFalse(self.due(LATER - 1))
        self.assertTrue(self.due(LATER))

    def test_off_is_the_default_and_never_comes_due(self):
        """`POWER-OFF: OFF` is 0 minutes and is what a fresh instrument has.
        A day of silence on the default must not turn it off."""
        self.assertEqual(self.app.settings.get("system", "power_off"),
                         MINUTES)                      # setUp really set it
        self.app.settings.set("system", "power_off", 0)
        self.assertFalse(self.due(86400.0))

    def test_a_rolling_transport_is_never_idle(self):
        """§ 7.2's second condition. A tape playing to an empty room is still
        an instrument doing what it was asked to."""
        self.app.tape.state = PLAYING
        self.assertFalse(self.due())
        self.app.tape.state = STOPPED
        self.assertTrue(self.due())               # the control for the above

    def test_a_take_being_recorded_is_never_idle(self):
        self.app.tape.state = RECORDING
        self.assertFalse(self.due())
        self.app.tape.state = STOPPED
        self.assertTrue(self.due())

    def test_a_count_in_is_rolling(self):
        """The one transport state with nothing else to show it: the take is
        armed and the click is running, and the keys are about to be played.
        The tape is STOPPED here, so nothing else in the rule catches it."""
        self.app.tape._count_in_until = self.app.tape.playhead + 48000
        self.assertTrue(self.app.tape.counting_in)
        self.assertFalse(self.app.tape.playing)
        self.assertFalse(self.due())

    def test_a_client_on_the_network_is_never_idle(self):
        self.assertFalse(self.due(network_busy=True))
        self.assertTrue(self.due(network_busy=False))

    def test_a_setting_that_is_not_a_number_is_off_not_a_crash(self):
        """`settings.json` is on the library partition and a phone can put a
        file there. An idle timer is not worth a traceback on the tick."""
        self.app.settings.set("system", "power_off", "sometimes")
        self.assertFalse(self.due(86400.0))


class WhatCountsAsTouchingIt(IdleCase):
    def test_a_key_press_restarts_the_clock(self):
        self.assertTrue(self.due())
        self.app.on_key("1", True)
        self.app.on_key("1", False)
        self.assertLess(self.app.idle_seconds(), 1.0)

    def test_an_encoder_restarts_the_clock(self):
        self.app.note_input(0.0)
        self.app.on_encoder(0, 1)
        self.assertLess(self.app.idle_seconds(), 1.0)

    def test_a_frame_does_not(self):
        """The negative control: a tick is the instrument sitting there, and
        an idle timer that a render resets never fires at all."""
        self.app.note_input(0.0)
        for _ in range(5):
            self.app.tick()
        self.assertTrue(self.due())
