"""#105: the scratch directories must not outlive what made them.

On the CM4 `/tmp` is a tmpfs -- a directory there is RAM, and it counts
against the headroom C2 (#75) measured. A gate run had left 733 MB of these
behind, the largest single `block.wav` being 226 MB.

Nothing here needs a real audio process: `NativeOfflineAudio.__init__` reads
two attributes off the native shim and makes its directory, which is exactly
the part under test.
"""

import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from drivers.native_backend import NativeOfflineAudio      # noqa: E402


class FakeNative:
    sample_rate = 48000
    host_block = 128


class TempDirLifetime(unittest.TestCase):
    def test_cleanup_removes_the_render_dir(self):
        a = NativeOfflineAudio(FakeNative())
        d = a._dir
        self.assertTrue(os.path.isdir(d))
        a.cleanup()
        self.assertFalse(os.path.exists(d))
        a.cleanup()                      # idempotent: finalize() runs once

    def test_a_dropped_driver_takes_its_dir_with_it(self):
        # The tests and tools that build one of these mostly never call
        # `cleanup()`; the weakref.finalize is what covers them. CPython's
        # refcounting makes the drop deterministic here.
        a = NativeOfflineAudio(FakeNative())
        d = a._dir
        self.assertTrue(os.path.isdir(d))
        del a
        self.assertFalse(os.path.exists(d))

    def test_keep_tmp_env_var_keeps_it_for_a_post_mortem(self):
        os.environ["ARBUTUS_KEEP_TMP"] = "1"
        try:
            a = NativeOfflineAudio(FakeNative())
            d = a._dir
            a.cleanup()
            del a
            self.assertTrue(os.path.isdir(d))
        finally:
            os.environ.pop("ARBUTUS_KEEP_TMP", None)
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
