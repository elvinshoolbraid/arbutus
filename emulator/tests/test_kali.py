"""#1255: OP1R-VERB's page is op1r's own reverb screen, the Kali art.

Three things: `ui/kali.py`'s state is reverb.c's `kali_screen_update`, line for
line; the layers it draws match op1r's own `reverb_preview.png` at the six knob
settings that picture shows; and the rack draws it for the op1r-verb tab only,
in the whole main zone, and the usual page in a mode (copy) or on another box.
"""

import os
import sys
import unittest
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from ui import frame, kali, zones                           # noqa: E402
from tests.test_fx_boxes import FxBoxCase                    # noqa: E402

PREVIEW = os.path.join(HERE, "..", "audio", "third_party", "op1r-src", "synth_sdk",
                       "display", "reverb_preview.png")
DEFAULTS = (16000, 20000, 16384, 12000)       # op1r's plugin.json
# harness/render.py's FX_STATES_BY_NAME["reverb"], the picture's six cells in order
PREVIEW_STATES = [DEFAULTS, (0, 0, 0, 0), (32767,) * 4, (13100, 9830, 19660, 16384),
                  (24575, 26214, 6553, 6553), (32767, 16384, 32767, 26214)]


def preview_cell(img, n):
    """Cell n of the preview (render.py: 3x, 12 px pad, a 22 px label, two
    columns), box-filtered down to the OP-1's 320 x 160."""
    x0 = 12 + (n % 2) * (960 + 12)
    y0 = 12 + (n // 2) * (480 + 12 + 22) + 22
    return img[y0:y0 + 480, x0:x0 + 960].reshape(160, 3, 320, 3, 3).mean((1, 3))


@unittest.skipUnless(kali.available(), "no kali.npz (audio/engines/op1r/op1r-verb/screen)")
class TestState(unittest.TestCase):
    def hidden(self, k):
        return set(kali.state(k)["hidden"])

    def test_all_min(self):
        # 1 arm, no mix line, no fire, the dimmest skulls; needles left
        st = kali.state((0, 0, 0, 0))
        self.assertEqual(self.hidden((0, 0, 0, 0)),
                         {"karm%d" % i for i in range(2, 7)} | {"kmix%d" % i for i in range(10)}
                         | {"kfire%d" % i for i in range(1, 6)}
                         | {"kskull%d" % i for i in range(2, 6)})
        self.assertEqual(st["angles"], {"ktime": 0, "kdiff": 0, "ktone": 0})

    def test_all_max(self):
        # 6 arms, 10 lines, the brightest fire and skulls; needles right (4095/8192)
        st = kali.state((32767,) * 4)
        self.assertEqual(set(st["hidden"]), {"kfire%d" % i for i in range(1, 5)}
                         | {"kskull%d" % i for i in range(1, 5)})
        self.assertEqual(set(st["angles"].values()), {4095})

    def test_each_knob_moves_its_own(self):
        # time: arms; tone: fire; diffusion: skulls; mix: lines from the bottom
        st = kali.state((13100, 9830, 19660, 16384))
        h = set(st["hidden"])
        self.assertEqual(sorted(a for a in ("karm%d" % i for i in range(1, 7)) if a not in h),
                         ["karm1", "karm2", "karm3"])                      # 1 + (13100 * 5 + 16383) // 32767
        self.assertEqual([f for f in ("kfire%d" % i for i in range(1, 6)) if f not in h],
                         ["kfire1"])                                       # (9830 * 5 + 16383) // 32767 = 1
        self.assertEqual([s for s in ("kskull%d" % i for i in range(1, 6)) if s not in h],
                         ["kskull3"])                                      # (19660 * 4 + 16383) // 32767 = 2
        self.assertEqual([m for m in ("kmix%d" % i for i in range(10)) if m not in h],
                         ["kmix5", "kmix6", "kmix7", "kmix8", "kmix9"])    # 5, the bottom ones
        self.assertEqual(st["angles"], {"ktime": 13100 * 4096 >> 15,
                                        "kdiff": 19660 * 4096 >> 15,
                                        "ktone": 9830 * 4096 >> 15})

    def test_word_is_the_adapters(self):
        # op1r_fx.hpp's knob_q15: lround(clamp(v, 0, 1) * 32767)
        self.assertEqual([kali.word(v) for v in (-1, 0, 0.5, 16000 / 32767, 1, 2)],
                         [0, 0, 16384, 16000, 32767, 32767])


@unittest.skipUnless(kali.available(), "no kali.npz")
class TestAgainstPreview(unittest.TestCase):
    """The 1x screen against op1r's own 3x preview, box-filtered to 1x. The two
    are the same drawer at different scales, so a few edge pixels differ (AA at
    1x is not a 3x picture averaged) and nothing else: a wrong group or needle
    puts whole strokes, 100+ levels, out."""

    @classmethod
    def setUpClass(cls):
        import pygame                                       # noqa: PLC0415
        surf = pygame.image.load(PREVIEW)
        cls.img = pygame.surfarray.array3d(surf).transpose(1, 0, 2).astype(float)

    def test_six_states(self):
        for n, k in enumerate(PREVIEW_STATES):
            with self.subTest(state=n, knobs=k):
                d = np.abs(kali.render(k).astype(float) - preview_cell(self.img, n))
                self.assertLess(d.mean(), 0.5)
                self.assertEqual(int((d.max(2) > 64).sum()), 0)


@unittest.skipUnless(kali.available(), "no kali.npz")
class TestRackDrawsIt(FxBoxCase):
    name = "t1255"

    def main_zone(self):
        x, y, w, h = zones.MAIN
        return frame.render_frame(self.app)[y:y + h, x:x + w]

    def test_op1r_verb_tab_is_the_screen(self):
        self.put("OP1R-VERB")
        np.testing.assert_array_equal(self.main_zone(), kali.render(DEFAULTS))

    def test_knobs_reach_it(self):
        self.put("OP1R-VERB")
        self.enc(0, -100)                       # TIME to 0, the others at their defaults
        self.enc(3, 100)                        # MIX to 1
        self.run_blocks(1)
        np.testing.assert_array_equal(self.main_zone(),
                                      kali.render((0, 20000, 16384, 32767)))

    def test_copy_mode_draws_the_rack(self):
        rack = self.put("OP1R-VERB")
        rack.mode = "copy"
        with mock.patch.object(kali, "draw") as drawn:
            self.main_zone()
        drawn.assert_not_called()

    def test_another_box_draws_the_rack(self):
        self.put("DELAY")
        with mock.patch.object(kali, "draw") as drawn:
            self.main_zone()
        drawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
