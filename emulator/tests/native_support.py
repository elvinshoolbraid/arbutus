"""Shared scaffolding for the tests: a host, a scratch state dir, evidence.

Every test needs the same three things: an audio process that may not be built
(`native_or_skip` skips with the build command rather than failing on a bare
checkout), a state dir that is not the user's, and somewhere to leave PNGs.

`rig()` is what the page tests drive: `ScriptedInput` → `App` → the audio
process offline, with `render` as the clock, which is the same seam they used
when the graph was Python.
"""

import os
import sys
import unittest
import wave
from typing import Optional

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # emulator/
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# #334: a test draws light, whatever mode a player left in the library's
# settings.json -- the same pin `tools/verify.py` sets. A test about the mode
# itself takes it off (`tests/test_palette_modes.py`).
os.environ.setdefault("ARB_APPEARANCE", "light")

EVIDENCE = os.path.join(REPO, "validation", "ui", "native")
SAMPLE_RATE = 48000
BLOCK = 256
BOXES = os.path.join(REPO, "audio", "build", "boxes")
LIBRARY = os.path.join(REPO, "audio", "library")


def state_dir(name: str) -> str:
    """A scratch state dir per test, never the user's `emulator/state/`."""
    return os.path.join(HERE, "state", "native-%s" % name)


def native_or_skip(case: unittest.TestCase, **kw):
    """A running `arbutus-audio`, or skip with the build command."""
    from app.native_audio import NativeAudio, NativeUnavailable

    # never the checkout's library, which a player may have written in (#851)
    kw.setdefault("libraries", [BOXES, scratch_library()])
    try:
        return NativeAudio(**kw)
    except NativeUnavailable as exc:
        raise case.skipTest(str(exc))


def save_wav(path: str, mono: np.ndarray, sample_rate: int = SAMPLE_RATE) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return path


def load_wav(path: str):
    """-> (mono float32 in -1..1, sample rate). The 16-bit WAV the boxes read.

    `engines/sampler.load_wav` used to be this; the engines are the audio
    process's now, so a test that wants to look at a capture on disk reads it
    here rather than through an engine it can no longer import.
    """
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
    data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32767.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr


def library_copy(dest: str) -> str:
    """A throwaway copy of `audio/library` as the checkout ships it.

    A test that *writes* into the library (INPUT does: a capture lands in
    `synth/sampler/`) must never leave a WAV in the repo's library, and a
    test must never read what a player left there (#851): the copy is the
    files git tracks (`testing/stock_library.py`).
    """
    from testing import stock_library

    return stock_library.copy_stock(dest, LIBRARY)


def scratch_library() -> str:
    """The library a test gets when it names none: one stock copy per
    process, never the checkout's own (#851)."""
    from testing import stock_library

    return stock_library.scratch()


#: tape length per host in the tests. The host's default 480 s is ~740 MB of
#: medium when the tape is RAM only, and a test file starts a host per rig, so
#: the suite asks for less.
TEST_TAPE_SECONDS = 30.0


def host(case=None, block: int = BLOCK, library_root: Optional[str] = None,
         tape_seconds: float = TEST_TAPE_SECONDS, owner=None):
    """One offline audio process.

    Closed by `case.addCleanup` when a TestCase asks, or when `owner` (a rig)
    is collected — a test that builds three rigs must not leave three audio
    processes and three tape media behind.
    """
    libraries = [BOXES, library_root or scratch_library()]
    native = native_or_skip(case, offline=True, block=block,
                            libraries=libraries, tape_seconds=tape_seconds)
    if case is not None and owner is None:
        case.addCleanup(native.close)
    if owner is not None:
        import weakref
        weakref.finalize(owner, native.close)
    return native


def app_for(native, state_dir: str, fresh: bool = True,
            library_root: Optional[str] = None, block: int = BLOCK):
    """-> (App, AudioDriver). The driver's `pull` is the clock."""
    from app.app import App
    from app.native_library import NativeLibrary
    from drivers.native_backend import NativeOfflineAudio

    library = NativeLibrary(native, root=library_root or scratch_library())
    app = App(sample_rate=SAMPLE_RATE, fresh=fresh, state_dir=state_dir,
              library=library, native=native)
    audio = NativeOfflineAudio(native, blocksize=block)
    audio.initialize(SAMPLE_RATE, 2, None)
    audio.start()
    return app, audio
