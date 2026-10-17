"""#1039: the emulator's timeline is the audio host's tape, one length.

DDR-072's 8 minutes a track has one home in code, the host's `--tape-seconds`
default (`audio/host/audio_host.hpp`). `app/tape.py`'s `TRACK_SECONDS` is the
UI's ruler (TRACK's SCRUB and ZOOM ranges, where a clip may be dropped) and
`NativeAudio` spawns a host with no `--tape-seconds` unless asked. A host
started the way the emulator starts one must report the ruler's length.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import tape as T                                     # noqa: E402
from tests.native_support import native_or_skip               # noqa: E402


class TapeLength(unittest.TestCase):
    def test_spawned_host_reports_the_ruler(self):
        # no `tape_seconds`: the host's own default, as `main.py` starts it
        native = native_or_skip(self, offline=True)
        self.addCleanup(native.close)
        tape = native.state["tape"]
        self.assertEqual(tape["seconds"], T.TRACK_SECONDS)
        self.assertEqual(tape["capacity_frames"], T.TRACK_LEN)

    def test_a_caller_may_still_ask_for_less(self):
        native = native_or_skip(self, offline=True, tape_seconds=30.0)
        self.addCleanup(native.close)
        self.assertEqual(native.state["tape"]["seconds"], 30.0)


if __name__ == "__main__":
    unittest.main()
