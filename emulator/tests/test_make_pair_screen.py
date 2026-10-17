"""`app/ios/tools/make_pair_screen.py`'s verdict (#527): a skipped draw drew nothing,
so the script exits 1 and says `nothing drawn`, never "OK (skipped=1)" (#298).

The skip is forced the way a fresh checkout meets it: `NativeAudio` cannot start
(no built arbutus-audio), so NetCase's setUp skips. The script itself runs as
`__main__` in a child python, with that one class patched before it starts.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # emulator/
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "app", "ios", "tools", "make_pair_screen.py")

# Runs the script as __main__ after making every NativeAudio() raise the way a
# missing host does.
NO_HOST = """
import runpy, sys
sys.path.insert(0, %(emu)r)
from app import native_audio
def missing(self, *a, **kw):
    raise native_audio.NativeUnavailable("forced: no arbutus-audio")
native_audio.NativeAudio.__init__ = missing
sys.argv = [%(script)r, %(out)r]
runpy.run_path(%(script)r, run_name="__main__")
"""


@unittest.skipUnless(os.path.exists(SCRIPT), "no app/ios/ (the public export, #1295)")
class MakePairScreen(unittest.TestCase):
    def test_a_skipped_draw_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "pair.png")
            code = NO_HOST % {"emu": HERE, "script": SCRIPT, "out": out}
            r = subprocess.run([sys.executable, "-c", code], cwd=HERE,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("make_pair_screen: nothing drawn: forced: no arbutus-audio", r.stderr)
            self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
