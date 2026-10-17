#!/usr/bin/env python3
"""Write the stock wavetables for `arbutus/wavetable`, deterministically.

A wavetable is a **16-bit mono WAV** (repo `CLAUDE.md` § Library) holding
`frames x 2048` samples: frame *f* is samples `[f*2048, (f+1)*2048)`, and the
box scans between adjacent frames. Nothing else about the file is special --
it opens in any editor, and a player that does not know the convention hears
the frames played back to back.

Every frame here is built from a **harmonic amplitude list**, not drawn, so the
result is band-limited by construction and the file is reproducible: run this
twice and the bytes are identical. That is the repo's rule for a committed
asset -- the script is the source, the WAV is the build.

    python3 audio/tools/mkwavetable.py audio/library/synth/wavetable

Tables:
  harmonics.wav  16 frames, sine -> 64-harmonic saw. The scan is the harmonic
                 series arriving one octave of harmonics at a time, which is
                 the wavetable equivalent of opening a filter -- and unlike a
                 filter it costs nothing per sample.
  vowel.wav      16 frames through A E I O U. A buzz shaped by three formant
                 resonances; the scan is the mouth moving.
"""

import math
import os
import struct
import sys
import wave

FRAME = 2048          # samples per frame; the box's fixed convention
FRAMES = 16           # frames per table
RATE = 48000          # meaningless for a table, but a WAV needs one
MAX_HARM = 64         # the top of every spectrum below; see the box's README

# The five vowels as three formants (Hz) with relative levels. Standard-ish
# male values; they only have to be recognisable, not clinical.
VOWELS = [
    ("A", [(730, 1.0), (1090, 0.50), (2440, 0.25)]),
    ("E", [(530, 1.0), (1840, 0.45), (2480, 0.30)]),
    ("I", [(270, 1.0), (2290, 0.40), (3010, 0.30)]),
    ("O", [(570, 1.0), (840, 0.55), (2410, 0.15)]),
    ("U", [(300, 1.0), (870, 0.35), (2240, 0.10)]),
]
VOWEL_F0 = 130.0      # the buzz the formants shape, Hz


def frame_from_harmonics(amps):
    """One 2048-sample cycle from a list of harmonic amplitudes (1-based)."""
    out = [0.0] * FRAME
    for n, a in enumerate(amps, start=1):
        if a == 0.0:
            continue
        w = 2.0 * math.pi * n / FRAME
        for i in range(FRAME):
            out[i] += a * math.sin(w * i)
    peak = max(abs(v) for v in out) or 1.0
    return [0.98 * v / peak for v in out]


def harmonics_table():
    """Sine to saw: the harmonic series arriving an octave at a time."""
    frames = []
    for f in range(FRAMES):
        top = max(1, int(round(MAX_HARM ** (f / (FRAMES - 1)))))
        # 1/n is a saw's spectrum; the soft edge stops the top harmonic
        # switching on as a click when the scan crosses a frame.
        amps = []
        for n in range(1, top + 1):
            edge = 1.0 if n < top else 0.5
            amps.append(edge / n)
        frames.append(frame_from_harmonics(amps))
    return frames


def vowel_table():
    """A E I O U, three formants each, interpolated across the frames."""
    frames = []
    for f in range(FRAMES):
        x = f / (FRAMES - 1) * (len(VOWELS) - 1)
        i = min(int(x), len(VOWELS) - 2)
        t = x - i
        a = VOWELS[i][1]
        b = VOWELS[i + 1][1]
        formants = [(a[k][0] + t * (b[k][0] - a[k][0]),
                     a[k][1] + t * (b[k][1] - a[k][1])) for k in range(3)]
        amps = []
        for n in range(1, MAX_HARM + 1):
            hz = VOWEL_F0 * n
            g = 0.0
            for fc, lv in formants:
                bw = 0.12 * fc                      # resonance width
                g += lv / (1.0 + ((hz - fc) / bw) ** 2)
            amps.append(g / n ** 0.4)               # the glottal buzz's own tilt
        frames.append(frame_from_harmonics(amps))
    return frames


def write_wav(path, frames):
    data = bytearray()
    for fr in frames:
        for v in fr:
            s = max(-1.0, min(1.0, v))
            data += struct.pack("<h", int(round(s * 32767.0)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(data))
    print("%s: %d frames x %d samples" % (path, len(frames), FRAME))


def main(argv):
    out = argv[1] if len(argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    write_wav(os.path.join(out, "harmonics.wav"), harmonics_table())
    write_wav(os.path.join(out, "vowel.wav"), vowel_table())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
