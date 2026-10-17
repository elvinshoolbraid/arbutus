#!/usr/bin/env python3
"""Write the fixed test assets for arbutus/sampler and arbutus/drum.

Standard library only, so the test tree can be rebuilt from a bare checkout
with no venv. Everything is deterministic: a fixed LCG stands in for noise, so
re-running this produces byte-identical files and the goldens stay valid.

    python3 audio/tests/tools/make_samples.py

Writes:
    audio/tests/golden/sample.wav        44.1 kHz, so the sampler's
                                         rate conversion is exercised
    audio/tests/golden/kit/{kit.json,kick.wav,snare.wav,hat.wav}
"""

import json
import math
import os
import struct
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "golden"))


class Lcg:
    """Numerical Recipes' LCG: noise nobody has to install anything to get."""

    def __init__(self, seed=20260915):
        self.s = seed & 0xFFFFFFFF

    def pm1(self):
        self.s = (1664525 * self.s + 1013904223) & 0xFFFFFFFF
        return self.s / 2147483648.0 - 1.0


def write_wav(path, frames, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", max(-32768, min(32767, int(v * 32767.0)))) for v in frames))


def pluck(n, rate, hz, tau):
    """A decaying sine with a little second harmonic: a pitch with an attack."""
    out = []
    for i in range(n):
        t = i / rate
        a = math.exp(-t / tau)
        out.append(0.8 * a * (math.sin(2 * math.pi * hz * t)
                              + 0.3 * math.sin(4 * math.pi * hz * t)))
    return out


def kick(n, rate):
    out = []
    ph = 0.0
    for i in range(n):
        t = i / rate
        hz = 45.0 + 110.0 * math.exp(-t / 0.02)
        ph += hz / rate
        out.append(0.9 * math.exp(-t / 0.12) * math.sin(2 * math.pi * ph))
    return out


def noise_hit(n, rate, tau, lp, seed):
    rng = Lcg(seed)
    prev = 0.0
    out = []
    for i in range(n):
        prev += lp * (rng.pm1() - prev)
        out.append(0.7 * math.exp(-(i / rate) / tau) * prev)
    return out


def main():
    os.makedirs(os.path.join(OUT, "kit"), exist_ok=True)

    write_wav(os.path.join(OUT, "sample.wav"), pluck(17640, 44100, 220.0, 0.15), 44100)

    write_wav(os.path.join(OUT, "kit", "kick.wav"), kick(9600, 48000), 48000)
    write_wav(os.path.join(OUT, "kit", "snare.wav"),
              noise_hit(7200, 48000, 0.09, 0.45, 11), 48000)
    write_wav(os.path.join(OUT, "kit", "hat.wav"),
              noise_hit(2400, 48000, 0.02, 0.9, 22), 48000)

    # §7.3. Pad 0 is F3 (MIDI 53) on the drum keyboard; paths are relative to
    # this file, which arbutus/drum resolves against the kit.json's own folder.
    kit = {"format": 1, "kind": "kit", "name": "TEST-KIT",
           "pads": {"0": "kick.wav", "1": "snare.wav", "2": "hat.wav"}}
    with open(os.path.join(OUT, "kit", "kit.json"), "w") as f:
        json.dump(kit, f, indent=2)
        f.write("\n")
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
