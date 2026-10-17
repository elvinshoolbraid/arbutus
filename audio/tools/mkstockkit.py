#!/usr/bin/env python3
"""mkstockkit — synthesize BARK, the stock sample kit that is ours (#1295).

The other four stock kits are op1.fun uploads with no stated licence (#1239),
so the public export leaves them out, and without this one DRUM's sample-kit
engine (`arbutus/drum`) would have nothing to play there. Every sound here is
made from sines, squares and seeded noise below; nothing is recorded or sliced.

    python3 audio/tools/mkstockkit.py            # (re)write audio/library/drum/drum/bark/
    python3 audio/tools/mkstockkit.py --check    # do the files match what this makes?

The folder name is lowercase on purpose: the kit list sorts by plain string
order, so `bark` comes after `South_808` and a fresh DRUM page here still
loads the kit it always did; in the public export it is the only kit, so it
loads first there. `--check` compares bytes, so it is only exact on the
machine that wrote the files (libm's last bit can differ elsewhere).

Standard library only, like everything else in `audio/tools/`.
"""

import argparse
import io
import json
import math
import os
import random
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "library", "drum", "drum", "bark")
SR = 48000
PEAK = 0.89            # -1 dBFS
TWO_PI = 2 * math.pi


# -- building blocks: each returns a list of floats, n samples long ----------

def env(n, decay, attack=0.0):
    """An exponential decay (seconds to -60 dB) after a linear attack."""
    k = math.log(1000.0) / (decay * SR)
    a = int(attack * SR)
    return [(i / a if i < a else 1.0) * math.exp(-k * max(0, i - a)) for i in range(n)]


def sweep(n, f0, f1, t):
    """A sine whose pitch falls exponentially from f0 to f1 over about t seconds."""
    out, ph = [], 0.0
    for i in range(n):
        f = f1 + (f0 - f1) * math.exp(-i / (t * SR))
        ph += TWO_PI * f / SR
        out.append(math.sin(ph))
    return out


def square(n, f):
    return [1.0 if (i * f / SR) % 1.0 < 0.5 else -1.0 for i in range(n)]


def noise(n, seed):
    r = random.Random(seed)
    return [r.uniform(-1.0, 1.0) for _ in range(n)]


def biquad(x, kind, f, q=0.707):
    """RBJ cookbook high-pass, low-pass or band-pass (0 dB peak)."""
    w = TWO_PI * f / SR
    c, al = math.cos(w), math.sin(w) / (2 * q)
    if kind == "hp":
        b = ((1 + c) / 2, -(1 + c), (1 + c) / 2)
    elif kind == "lp":
        b = ((1 - c) / 2, 1 - c, (1 - c) / 2)
    else:
        b = (al, 0.0, -al)
    a0, a1, a2 = 1 + al, -2 * c, 1 - al
    b0, b1, b2 = (v / a0 for v in b)
    a1, a2 = a1 / a0, a2 / a0
    out, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for s in x:
        y = b0 * s + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, s, y1, y
        out.append(y)
    return out


def mix(*parts):
    """(gain, signal) pairs summed; the signals may differ in length."""
    n = max(len(s) for _, s in parts)
    out = [0.0] * n
    for g, s in parts:
        for i, v in enumerate(s):
            out[i] += g * v
    return out


def mul(a, b):
    return [x * y for x, y in zip(a, b)]


def metal(n):
    """Six detuned squares, the classic analogue cymbal source."""
    return mix(*[(1 / 6, square(n, f)) for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)])


def secs(t):
    return int(t * SR)


# -- the twelve sounds, in pad order ----------------------------------------

def kick():
    n = secs(0.6)
    body = mul(sweep(n, 150, 48, 0.04), env(n, 0.45))
    click = mul(biquad(noise(secs(0.006), 1), "hp", 2000), env(secs(0.006), 0.006))
    return mix((1.0, body), (0.3, click))


def kick_short():
    n = secs(0.3)
    return [math.tanh(2.0 * v) for v in mul(sweep(n, 190, 60, 0.02), env(n, 0.22))]


def snare():
    n = secs(0.35)
    tone = mix((0.6, sweep(n, 200, 185, 0.01)), (0.4, sweep(n, 340, 330, 0.01)))
    hiss = biquad(noise(n, 3), "hp", 1200)
    return mix((0.55, mul(tone, env(n, 0.12))), (0.7, mul(hiss, env(n, 0.22))))


def rim():
    n = secs(0.08)
    tone = mix((0.6, sweep(n, 1700, 1700, 1)), (0.5, sweep(n, 820, 820, 1)))
    return biquad(mul(tone, env(n, 0.035)), "hp", 400)


def clap():
    n = secs(0.4)
    src = biquad(noise(n, 5), "bp", 1100, 1.2)
    shape = [0.0] * n
    for k, start in enumerate((0.0, 0.011, 0.022)):
        s = secs(start)
        burst = env(secs(0.011), 0.01)
        for i, v in enumerate(burst):
            if s + i < n:
                shape[s + i] = max(shape[s + i], v * (0.8 + 0.1 * k))
    tail = env(n - secs(0.033), 0.2)
    for i, v in enumerate(tail):
        shape[secs(0.033) + i] = max(shape[secs(0.033) + i], v)
    return mul(src, shape)


def hat(decay, length):
    n = secs(length)
    src = mix((0.7, metal(n)), (0.3, noise(n, 6)))
    return mul(biquad(biquad(src, "bp", 9000, 0.8), "hp", 7000), env(n, decay))


def tom(f0, f1, decay):
    n = secs(decay * 1.4)
    return mix((1.0, mul(sweep(n, f0, f1, 0.05), env(n, decay))),
               (0.08, mul(biquad(noise(n, int(f0)), "lp", 3000), env(n, 0.03))))


def cowbell():
    n = secs(0.35)
    src = mix((0.5, square(n, 540)), (0.5, square(n, 800)))
    return mul(biquad(src, "bp", 700, 2.0), env(n, 0.25))


def shaker():
    n = secs(0.12)
    return mul(biquad(noise(n, 7), "hp", 5000), env(n, 0.07, attack=0.02))


def crash():
    n = secs(1.6)
    src = mix((0.5, metal(n)), (0.5, noise(n, 8)))
    return mul(biquad(src, "hp", 4000), env(n, 1.3))


PADS = [("kick", kick), ("kick-short", kick_short), ("snare", snare), ("rim", rim),
        ("clap", clap), ("hat-closed", lambda: hat(0.06, 0.12)),
        ("hat-open", lambda: hat(0.45, 0.6)), ("tom-low", lambda: tom(130, 90, 0.35)),
        ("tom-mid", lambda: tom(180, 130, 0.3)), ("tom-high", lambda: tom(250, 185, 0.25)),
        ("cowbell", cowbell), ("crash", crash)]


def wav_bytes(x):
    """Normalised to PEAK, 16-bit mono PCM at SR, as a .wav file's bytes."""
    g = PEAK / (max(abs(v) for v in x) or 1.0)
    pcm = b"".join(struct.pack("<h", int(round(v * g * 32767))) for v in x)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


def build():
    """{file name: bytes} for the whole kit folder, kit.json included."""
    files = {"%02d.wav" % (i + 1): wav_bytes(make()) for i, (_, make) in enumerate(PADS)}
    pads = {str(i): {"file": "%02d.wav" % (i + 1), "params": {}} for i in range(len(PADS))}
    doc = {"format": 1, "kind": "kit", "name": "BARK", "pads": pads}
    files["kit.json"] = (json.dumps(doc, indent=1) + "\n").encode()
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=OUT, help="the kit folder (default: the stock one)")
    ap.add_argument("--check", action="store_true", help="compare, write nothing")
    a = ap.parse_args(argv)
    files = build()
    if a.check:
        bad = []
        for name, data in files.items():
            path = os.path.join(a.out, name)
            if not os.path.exists(path) or open(path, "rb").read() != data:
                bad.append(name)
        print("differs: " + ", ".join(bad) if bad else "%s matches" % a.out)
        return 1 if bad else 0
    os.makedirs(a.out, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(a.out, name), "wb") as f:
            f.write(data)
    print("%s — %d pads: %s" % (a.out, len(PADS), ", ".join(n for n, _ in PADS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
