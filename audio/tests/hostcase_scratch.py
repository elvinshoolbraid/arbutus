#!/usr/bin/env python3
"""hostcase.scratch() removes its folder, and keeps it when the block fails
(#657, #1180). The host suites lean on it for their ~27 MB tape folders but
pass whether or not it cleans up, so this is the check. No arbutus-audio
needed."""

import glob
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hostcase  # noqa: E402

PREFIX = "arbutus-scratch-check-"


def main():
    root = tempfile.mkdtemp(prefix="arbutus-scratch-root-")
    tempfile.tempdir = root        # mkdtemp's default parent: ours alone
    os.environ.pop("ARBUTUS_KEEP_TMP", None)
    left = lambda: glob.glob(os.path.join(root, PREFIX + "*"))  # noqa: E731
    try:
        with hostcase.scratch(PREFIX) as tmp:
            assert os.path.isdir(tmp) and left() == [tmp], left()
            with open(os.path.join(tmp, "f"), "w") as f:
                f.write("x")
        assert left() == [], "a passing block left %s" % left()
        hostcase.ok("a passing block's folder is gone")

        try:
            with hostcase.scratch(PREFIX) as tmp:
                raise AssertionError("the block failed")
        except AssertionError:
            pass
        assert left() == [tmp], "a failing block's folder was not kept"
        hostcase.ok("a failing block's folder is kept")
    finally:
        tempfile.tempdir = None
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
