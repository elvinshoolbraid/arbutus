#!/usr/bin/env python3
"""Stage N1: everything the emulator's tape keys and TEMPO page need natively.

The Python audio path used to do these in numpy; the host does them now, so
the demo's user stories survive the move (hollyhock-demo-spec stories 10, 11,
12, 14): the loop, varispeed and REV, the FILTER hold, the metronome click,
MASTER COMP, the scrub grain and INPUT MONITOR.

Everything runs `--offline`, so `render` is the clock and no device is opened.
"""

import sys
import time

from hostcase import Host, args, db, ok, peak, peak_error, read_wav

SR = 48000
TAKE = 24000               # 0.5 s of recorded synth on track 0


def record_take(h, track=0, frames=TAKE, note=60):
    """A take of the example synth onto `track`, as the UI would roll one."""
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "record", "track": track, "on": True})
    h.note(note, True, 0.9)
    h.render(frames)
    h.note(note, False)
    h.ok({"type": "record", "track": track, "on": False})
    h.render(512)
    h.ok({"type": "transport", "op": "stop"})
    st = h.c.state()
    clips = st["tape"]["tracks"][track]["clips"]
    assert len(clips) == 1, clips
    return clips[0]


def render_span(h, frames, name):
    """Play `frames` from 0 into a WAV and hand back its channels."""
    path = h.path(name)
    h.ok({"type": "transport", "op": "stop"})
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.render(frames, path)
    h.ok({"type": "transport", "op": "stop"})
    return read_wav(path)[1]


def main():
    a = args()

    # ---- the click ---------------------------------------------------------
    with Host(a.exe, [a.boxes], block=128) as h:
        st = h.c.state()
        assert st["metronome"] == {"level": 0.0, "pitch": 1600.0}, st["metronome"]
        h.ok({"type": "tempo", "bpm": 120})
        # No engine, no clips: anything that sounds is the click.
        silent = render_span(h, SR, "click-off.wav")
        assert peak(silent) == 0.0, peak(silent)
        h.ok({"type": "metronome", "level": 0.8, "pitch": 1000.0})
        clicky = render_span(h, SR, "click-on.wav")
        assert peak(clicky) > 0.1, peak(clicky)
        ok("click: CLICK LVL 0 is silence, 0.8 sounds (peak %.2f)" % peak(clicky))

        # Beats land on the song grid: 120 bpm = one every 24000 frames, and
        # the 30 ms burst has decayed long before the next one.
        loud = [max(abs(clicky[0][f]) for f in range(b * 24000, b * 24000 + 64))
                for b in range(2)]
        quiet = max(abs(clicky[0][f]) for f in range(12000, 12000 + 64))
        assert min(loud) > 0.1 and quiet < 1e-6, (loud, quiet)
        ok("click: a burst on every beat boundary, nothing between them")

        # CLICK PITCH 0 is silent, and the downbeat is an octave up: beat 0
        # crosses zero twice as often as beat 1.
        def crossings(f0, n=480):
            ch = clicky[0]
            return sum(1 for f in range(f0, f0 + n - 1) if (ch[f] < 0) != (ch[f + 1] < 0))
        assert crossings(0) > 1.6 * crossings(24000), (crossings(0), crossings(24000))
        ok("click: the downbeat sits an octave up")

        h.ok({"type": "metronome", "pitch": 0.0})
        off = render_span(h, 48000, "click-pitch0.wav")
        assert peak(off) == 0.0, peak(off)
        ok("click: CLICK PITCH 0 fades it to silence")

        # ... and it is master only: never the record tap (DDR-034).
        h.ok({"type": "metronome", "level": 1.0, "pitch": 1000.0})
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.ok({"type": "record", "track": 1, "on": True})
        h.render(TAKE)
        h.ok({"type": "record", "track": 1, "on": False})
        h.render(512)
        h.ok({"type": "transport", "op": "stop"})
        clip = h.c.state()["tape"]["tracks"][1]["clips"][0]
        pk = h.ok({"type": "clip_peaks", "track": 1, "src_offset": clip["src_offset"],
                   "len": clip["len"], "bins": 32})
        assert max(max(pk["max"]), -min(pk["min"])) == 0.0, pk
        ok("click: the take is silent — the click is in the master, not the tap")

    # ---- loop, speed, REV, the FILTER hold, COMP ---------------------------
    with Host(a.exe, [a.boxes], block=128) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "env", "a": 0.005, "d": 0.1, "s": 0.9, "r": 0.05, "mode": "poly"})
        clip = record_take(h)
        h.ok({"type": "unload_engine", "slot": "engine"})
        h.render(2048)
        assert clip["src_offset"] == 0 and clip["start_frame"] == 0, clip

        # the tape plays where the UI put it
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": 0, "gain": 1.0}]})
        plain = render_span(h, TAKE * 2, "plain.wav")
        assert max(abs(v) for v in plain[0][:TAKE]) > 0.01
        assert max(abs(v) for v in plain[0][TAKE:]) == 0.0
        ok("tape: one clip, then silence")

        # LOOP: IN 0 / OUT half the clip, and the second half of the render is
        # the first half again.
        h.ok({"type": "transport", "op": "loop", "in": 0, "out": TAKE // 2, "on": True})
        st = h.c.state()["tape"]["loop"]
        assert st == {"in": 0, "out": TAKE // 2, "on": True}, st
        looped = render_span(h, TAKE, "looped.wav")
        first = [looped[0][f] for f in range(TAKE // 2)]
        second = [looped[0][f] for f in range(TAKE // 2, TAKE)]
        e = peak_error([first], [second])
        assert db(e) <= -100.0, "loop did not repeat (%.1f dBFS)" % db(e)
        ok("loop: PLAY cycles IN..OUT (%.1f dBFS between passes)" % db(e))
        h.ok({"type": "transport", "op": "loop", "on": False})

        # SPEED: half speed is twice as long and an octave down.
        h.ok({"type": "tape_speed", "speed": 0.5})
        assert h.c.state()["tape"]["speed"] == 0.5
        slow = render_span(h, TAKE * 2, "slow.wav")
        tail = max(abs(v) for v in slow[0][TAKE + 2000:TAKE * 2 - 2000])
        assert tail > 0.01, tail
        e = peak_error([[slow[0][f * 2] for f in range(TAKE // 2)]],
                       [[plain[0][f] for f in range(TAKE // 2)]])
        assert db(e) <= -100.0, "half speed is not every other frame (%.1f dBFS)" % db(e)
        ok("speed: x0.5 runs twice as long, sampling every other frame")
        h.ok({"type": "tape_speed", "speed": 1.0})

        # REV: rolling backwards from the clip's end plays it in reverse, and
        # letting go lands where the tape would have been going forward.
        h.ok({"type": "transport", "op": "locate", "position": TAKE})
        h.ok({"type": "tape_speed", "reverse": True})
        h.ok({"type": "transport", "op": "start"})
        r = h.render(TAKE // 2, h.path("rev.wav"))
        back = read_wav(h.path("rev.wav"))[1]
        assert r["tape_pos"] == TAKE - TAKE // 2, r
        # frame k reads position TAKE - k: the first one is past the clip's end
        fwd = [0.0] + [plain[0][TAKE - f] for f in range(1, TAKE // 2)]
        e = peak_error([back[0]], [fwd])
        assert db(e) <= -100.0, "REV is not the clip backwards (%.1f dBFS)" % db(e)
        ok("REV: the tape runs backwards while held (%.1f dBFS)" % db(e))
        h.ok({"type": "tape_speed", "reverse": False})
        r = h.render(128)
        assert r["tape_pos"] >= TAKE, r         # forward-continuing, as if never reversed
        ok("REV: release lands where it would have been rolling forward")
        h.ok({"type": "transport", "op": "stop"})

        # FILTER hold: a low-pass on the tape, and only while held.
        h.ok({"type": "tape_filter", "on": True, "cutoff": 300.0, "mode": "LP"})
        filtered = render_span(h, TAKE, "filtered.wav")
        h.ok({"type": "tape_filter", "on": False})
        again = render_span(h, TAKE, "unfiltered.wav")

        def roughness(ch):
            return max(abs(ch[f + 1] - ch[f]) for f in range(len(ch) - 1))
        assert roughness(filtered[0]) < 0.5 * roughness(again[0]), \
            (roughness(filtered[0]), roughness(again[0]))
        assert db(peak_error([again[0]], [plain[0][:TAKE]])) <= -100.0
        ok("FILTER: held it dulls the tape, released it is bit-exact again")

        # MASTER COMP: pulls a loud mix down, and is off at 0.
        h.ok({"type": "meters", "on": True, "hz": 30})
        h.ok({"type": "mixer", "comp": 0.9})
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": 0, "gain": 4.0}]})
        comped = render_span(h, TAKE, "comp.wav")
        time.sleep(0.05)                         # meters are paced by wall clock
        h.render(256)
        gr = min(m.get("comp_gr", 1.0) for m in h.c.pushes("meters"))
        h.ok({"type": "mixer", "comp": 0.0})
        raw = render_span(h, TAKE, "nocomp.wav")
        # 0.9 pulls a hot mix down by more than the 1.54x makeup lifts it
        assert gr < 0.8, gr
        assert peak(comped) < peak(raw) * (1.0 + 0.6 * 0.9), (peak(comped), peak(raw))
        ok("COMP: 0.9 holds a hot mix down (gain reduction %.2f)" % gr)
        assert db(peak_error([raw[0]], [[v * 4.0 for v in plain[0][:TAKE]]])) <= -100.0
        ok("COMP: 0 is out of the path — gain 4 is exactly 4x")

        # The scrub grain: stopped, the tape is silent until the UI asks for a
        # grain, which then runs out on its own.
        h.ok({"type": "set_clips", "track": 0, "clips": [
            {"start_frame": 0, "len": TAKE, "src_offset": 0, "gain": 1.0}]})
        h.ok({"type": "transport", "op": "locate", "position": 4000})
        quiet = h.render(4096, h.path("stopped.wav"))
        assert quiet and peak(read_wav(h.path("stopped.wav"))[1]) == 0.0
        ok("stopped: the tape does not sound")
        h.ok({"type": "scrub", "position": 4000, "frames": 5760})
        h.render(5760, h.path("grain.wav"))
        grain = read_wav(h.path("grain.wav"))[1]
        assert peak(grain) > 0.01, peak(grain)
        h.render(4096, h.path("after.wav"))
        assert peak(read_wav(h.path("after.wav"))[1]) == 0.0
        ok("scrub: a grain sounds under the cursor and then runs dry")
        assert abs(grain[0][0]) < 1e-6 and abs(grain[0][5759]) < 1e-6
        ok("scrub: the grain fades in and out — no click")

        # INPUT MONITOR is state the UI owns; offline there is no input to hear.
        h.ok({"type": "monitor", "on": True, "gain": 2.0})
        assert h.c.state()["monitor"] == {"on": True, "gain": 2.0}
        ok("monitor: on/gain round-trip in the state document")

    return 0


if __name__ == "__main__":
    sys.exit(main())
