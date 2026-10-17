#!/usr/bin/env python3
"""Hot-swapping the ENGINE slot must not click (§8, "no xrun, short fade").

A sine so the signal has no steps of its own, then swap the engine mid-note and
look at the largest adjacent-sample jump across the boundary. Without a
crossfade the output falls off a cliff the height of the signal; with one it
cannot move faster than signal/kFadeFrames plus the sine's own slope.
"""

import sys

from hostcase import Host, args, ok, read_wav

BLOCK = 512
PRE = 4096
POST = 4096


def max_step(ch, lo, hi):
    return max(abs(ch[i] - ch[i - 1]) for i in range(max(1, lo), min(hi, len(ch))))


def main():
    a = args()
    with Host(a.exe, [a.boxes], block=BLOCK) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        # WAVE = SINE: the signal then has no steps of its own to hide behind.
        # DETUNE stays at its default -- at 0 cents the template's two
        # oscillators start a half turn apart and cancel exactly.
        h.ok({"type": "set_param", "slot": "engine", "idx": 0, "value": 0.0})
        h.ok({"type": "env", "a": 0.005, "d": 0.1, "s": 1.0, "r": 0.2, "mode": "poly"})
        h.note(60, True, 0.9)
        pre = h.path("pre.wav")
        h.render(PRE, pre)
        # swap the ENGINE slot for another instance of the same box: the new
        # engine holds no notes, so a naive swap would cut the sine dead.
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        post = h.path("post.wav")
        h.render(POST, post)

    _, p = read_wav(pre)
    _, q = read_wav(post)
    level = max(abs(v) for v in p[0][-BLOCK:])
    assert level > 0.05, "nothing was sounding before the swap (%.4f)" % level
    quiet = max(abs(v) for v in q[0][-BLOCK:])
    assert quiet < level * 0.1, "the swap did not take (%.4f -> %.4f)" % (level, quiet)

    joined = p[0][-BLOCK:] + q[0][:BLOCK * 2]
    step = max_step(joined, 1, len(joined))
    natural = max_step(p[0], PRE - BLOCK, PRE)
    budget = natural + level / 128.0
    assert step <= budget, (
        "discontinuity %.5f across the swap; the sine's own slope is %.5f and the "
        "%d-frame fade allows %.5f" % (step, natural, 256, budget))
    ok("hot-swap: largest step %.5f (sine slope %.5f, fade budget %.5f), %.3f -> %.3f"
       % (step, natural, budget, level, quiet))
    return 0


if __name__ == "__main__":
    sys.exit(main())
