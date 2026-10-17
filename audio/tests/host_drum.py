#!/usr/bin/env python3
"""Stage D through the host: arbutus/drum v2, arbutus/transient, waveform.

Per-pad params (§15.3) set and read back, a kit in the v1 shape migrating to
v2, PLAY gate/loop against NOTE_OFF, a choke group, copy_pad, the pad values
surviving an engine rebuild, FX tab assets, `waveform` against a WAV written
here, transient transparency and attack boost in a rack, and no allocation in
the block with drum + transient loaded.
"""

import gc
import math
import os
import struct
import sys
import wave

from hostcase import Host, args, compare, ok, peak, read_wav

KICK, SNARE, HAT = 53, 54, 55          # pads 0, 1, 2 of tests/golden/kit
PAD_IDS = ["start", "end", "pitch", "level", "play", "rev", "pan", "choke"]


def rms(c, a, b):
    seg = c[a:b]
    return math.sqrt(sum(v * v for v in seg) / max(1, len(seg)))


def render(h, frames, name):
    path = h.path(name)
    h.render(frames, path)
    return read_wav(path)[1]


def hit(h, note, frames, name, vel=0.9):
    h.ok({"type": "note", "on": True, "note": note, "vel": vel})
    return render(h, frames, name)


def kit_doc(golden, pads):
    return {"format": 1, "kind": "kit", "name": "TEST-KIT",
            "dir": os.path.join(golden, "kit"), "pads": pads}


def pads_of(h):
    return h.c.state()["instruments"]["drum"]["kit"]["pads"]


def main():
    a = args()
    golden = os.path.join(a.audio_root, "tests", "golden")

    with Host(a.exe, [a.boxes, golden], extra=["--rt-guard"]) as h:
        h.ok({"type": "instrument", "active": "drum"})
        h.ok({"type": "load_engine", "instrument": "drum", "slot": "engine", "id": "arbutus/drum"})

        # --- kit migration: the v1 shape loads and reads back as v2 ------------
        h.ok({"type": "load_kit", "instrument": "drum",
              "kit": kit_doc(golden, {"0": "kick.wav", "1": "snare.wav", "2": "hat.wav"})})
        pads = pads_of(h)
        assert set(pads) == {"0", "1", "2"}, pads
        for k, f in (("0", "kick.wav"), ("1", "snare.wav"), ("2", "hat.wav")):
            assert pads[k]["file"] == f, pads[k]
            assert sorted(pads[k]["params"]) == sorted(PAD_IDS), pads[k]
        p0 = pads["0"]["params"]
        assert (p0["start"], p0["end"], p0["pitch"], p0["play"], p0["rev"], p0["pan"], p0["choke"]) == \
               (0, 1, 0.5, 0, 0, 0.5, 0), p0
        assert abs(p0["level"] - 0.8) < 1e-6, p0
        eng = h.c.state()["instruments"]["drum"]["engine"]
        assert all(p["per_note"] for p in eng["desc"]["params"]), eng["desc"]["params"]
        env = h.c.state()["instruments"]["drum"]["env"]
        assert env["a"] == 0 and env["d"] == 4, env
        ok("kit: v1 pad strings migrate to {file, params}, every per_note param at its default")
        ok("env: DRUM defaults a 0 s, d 4 s (v1's one-shot)")

        h.render(1024)                           # past the load's 256-frame crossfade
        h.ok({"type": "panic"})
        full = peak(hit(h, KICK, 2048, "kick-full.wav"))

        # --- set_note_param: by pad + id, by note + idx; state reports it -------
        h.ok({"type": "set_note_param", "instrument": "drum", "pad": 0, "id": "level", "value": 0.4})
        assert pads_of(h)["0"]["params"]["level"] == 0.4
        h.ok({"type": "panic"})
        half = peak(hit(h, KICK, 2048, "kick-half.wav"))
        assert abs(half / full - 0.5) < 0.01, (full, half)
        ok("set_note_param: pad 0 LEVEL 0.8 -> 0.4 halves the kick (%.3f -> %.3f)" % (full, half))

        h.ok({"type": "set_note_param", "note": KICK + 1, "idx": 3, "value": 0.2})
        assert pads_of(h)["1"]["params"]["level"] == 0.2
        assert pads_of(h)["0"]["params"]["level"] == 0.4, "another pad moved"
        ok("set_note_param: note + idx addresses pad 1 only")

        for bad, code in (({"pad": 24, "id": "level"}, "bad_request"),
                          ({"pad": 9, "id": "level"}, "not_found"),
                          ({"pad": 0, "id": "nope"}, "not_found")):
            r = h.req(dict({"type": "set_note_param", "value": 0.5}, **bad))
            assert r["type"] == "error" and r["code"] == code, (bad, r)
        ok("set_note_param: pad out of range, pad without a file, unknown id refused")

        # The pad values live in the kit document, not the file the engine read:
        # a rebuilt engine gets them back.
        h.ok({"type": "load_engine", "instrument": "drum", "slot": "engine", "id": "arbutus/drum"})
        h.render(512)
        h.ok({"type": "panic"})
        again = peak(hit(h, KICK, 2048, "kick-rebuilt.wav"))
        assert abs(again - half) < 1e-6, (half, again)
        ok("set_note_param: survives an engine rebuild (%.3f)" % again)
        h.ok({"type": "set_note_param", "pad": 0, "id": "level", "value": 0.8})

        # --- PLAY: one-shot ignores NOTE_OFF, gate and loop do not --------------
        def after_release(play, note, hold, name):
            h.ok({"type": "set_note_param", "note": note, "id": "play", "value": play})
            h.ok({"type": "panic"})
            h.render(512)
            hit(h, note, hold, name + "-held.wav")
            h.ok({"type": "note", "on": False, "note": note})
            c = render(h, 2048, name + "-off.wav")
            return peak([c[0][512:]]), c

        tail_one, _ = after_release(0.0, KICK, 2048, "oneshot")
        tail_gate, _ = after_release(0.5, KICK, 2048, "gate")
        assert tail_one > 0.05, tail_one
        assert tail_gate < 1e-6, tail_gate
        ok("PLAY: one-shot rings past NOTE_OFF (%.3f), gate is silent 5 ms after (%.1e)"
           % (tail_one, tail_gate))
        h.ok({"type": "set_note_param", "note": KICK, "id": "play", "value": 0.0})

        # The hat is 2400 frames. Held in LOOP it is still sounding at 12000.
        h.ok({"type": "set_note_param", "note": HAT, "id": "play", "value": 1.0})
        h.ok({"type": "panic"})
        h.render(512)
        held = hit(h, HAT, 12288, "loop-held.wav")
        late = rms(held[0], 10000, 12288)
        h.ok({"type": "note", "on": False, "note": HAT})
        off = render(h, 2048, "loop-off.wav")
        assert late > 0.01, late
        assert peak([off[0][512:]]) < 1e-6, peak([off[0][512:]])
        ok("PLAY: loop sustains a 2400-frame hat for 12000 frames (rms %.3f), stops on release" % late)
        h.ok({"type": "set_note_param", "note": HAT, "id": "play", "value": 0.0})

        # --- CHOKE: the hat cuts the ringing snare in the same group ------------
        def snare_then_hat(name):
            h.ok({"type": "panic"})
            h.render(512)
            hit(h, SNARE, 512, name + "-snare.wav")
            c = hit(h, HAT, 4096, name + "-hat.wav")
            return rms(c[0], 3000, 4096)          # hat is over, snare (7200) is not

        free = snare_then_hat("nochoke")
        for note in (SNARE, HAT):
            h.ok({"type": "set_note_param", "note": note, "id": "choke", "value": 0.25})
        choked = snare_then_hat("choke")
        assert free > 0.005 and choked < 1e-6, (free, choked)
        ok("CHOKE: group 1 hat cuts the snare (rms %.4f -> %.1e)" % (free, choked))

        # --- copy_pad: file + params -------------------------------------------
        r = h.ok({"type": "copy_pad", "instrument": "drum", "from": 1, "to": 5})
        assert r["kit"]["pads"]["5"] == r["kit"]["pads"]["1"], r["kit"]["pads"]
        assert pads_of(h)["5"]["file"] == "snare.wav"
        assert pads_of(h)["5"]["params"]["choke"] == 0.25
        h.ok({"type": "panic"})
        p5 = peak(hit(h, KICK + 5, 2048, "pad5.wav"))
        assert p5 > 0.02, p5
        ok("copy_pad: pad 1 -> 5 copies snare.wav and CHOKE 1; note 58 plays (%.3f)" % p5)
        r = h.req({"type": "copy_pad", "from": 9, "to": 3})
        assert r["type"] == "error" and r["code"] == "not_found", r
        ok("copy_pad: from an empty pad refused")

        # --- v2 kit shape in, params applied at load ----------------------------
        h.ok({"type": "load_kit", "instrument": "drum",
              "kit": kit_doc(golden, {"0": {"file": "kick.wav", "params": {"level": 0.4}}})})
        assert pads_of(h)["0"]["params"]["level"] == 0.4
        h.render(1024)
        h.ok({"type": "panic"})
        v2 = peak(hit(h, KICK, 2048, "kick-v2.wav"))
        assert abs(v2 - half) < 1e-6, (v2, half)
        ok("kit: a v2 pad's params apply at load (%.3f)" % v2)

        # --- rt-guard: drum + transient in the rack -----------------------------
        h.ok({"type": "add_tab", "instrument": "drum", "engine": "arbutus/transient",
              "params": {"attack": 0.8, "sustain": 0.3}})
        h.ok({"type": "panic"})
        h.ok({"type": "note", "on": True, "note": KICK, "vel": 1.0})
        r = h.render(24000)
        allocs = r.get("rt_allocs")
        assert allocs is not None, r
        if allocs < 0:
            ok("rt-guard: unavailable on this platform")
        else:
            assert allocs == 0, "%d heap blocks gained by drum + transient" % allocs
            ok("rt-guard: drum + transient render without allocating")

    # --- transient in a rack: transparent at default, attack is audible --------
    with Host(a.exe, [a.boxes, golden]) as h:
        h.ok({"type": "instrument", "active": "drum"})
        h.ok({"type": "load_engine", "instrument": "drum", "slot": "engine", "id": "arbutus/drum"})
        h.ok({"type": "load_kit", "kit": kit_doc(golden, {"0": "kick.wav", "1": "snare.wav"})})

        def take(name):
            h.render(1024)                       # past any rebuild crossfade
            h.ok({"type": "panic"})
            h.render(512)
            h.ok({"type": "note", "on": True, "note": SNARE, "vel": 1.0})
            path = h.path(name)
            h.render(8192, path)
            return path

        dry = take("dry.wav")
        h.ok({"type": "add_tab", "engine": "arbutus/transient"})
        wet = take("default.wav")
        compare(dry, wet, -100.0, "transient default vs no rack")
        ok("transient: default settings are transparent")

        h.ok({"type": "set_param", "slot": "fx", "tab": 0, "idx": 0, "value": 1.0})   # ATTACK +100 %
        boosted = take("attack.wav")
        _, d = read_wav(dry)
        _, b = read_wav(boosted)
        crest_d = peak([d[0][:480]]) / rms(d[0], 2400, 7200)
        crest_b = peak([b[0][:480]]) / rms(b[0], 2400, 7200)
        assert crest_b > crest_d * 1.5, (crest_d, crest_b)
        ok("transient: ATTACK +100 %% raises onset/tail from %.2f to %.2f" % (crest_d, crest_b))

    # --- waveform + FX tab assets ---------------------------------------------
    with Host(a.exe, [a.boxes, golden]) as h:
        wav = h.path("known.wav")
        n = 1000
        left = [int(((i % 100) - 50) * 600) for i in range(n)]
        right = [int(-v / 2) for v in left]
        with wave.open(wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<hh", l, r) for l, r in zip(left, right)))
        bins = 10
        r = h.ok({"type": "waveform", "path": wav, "bins": bins})
        assert r["type"] == "waveform" and r["frames"] == n and r["bins"] == bins, r
        for b in range(bins):
            seg = [(left[i] / 32768.0 + right[i] / 32768.0) / 2.0 for i in range(b * n // bins, (b + 1) * n // bins)]
            assert abs(r["min"][b] - min(seg)) < 1e-6 and abs(r["max"][b] - max(seg)) < 1e-6, (b, r)
        again = h.ok({"type": "waveform", "path": wav, "bins": bins})
        assert again["min"] == r["min"] and again["max"] == r["max"], again
        ok("waveform: 10 bins of a known stereo WAV match min/max of the mono mix, cached")

        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "W",
            "engine": {"id": "arbutus/sampler", "params": {}, "assets": {"sample": "sample.wav"}}}})
        r = h.ok({"type": "waveform", "instrument": "synth", "slot": "engine", "key": "sample", "bins": 64})
        assert r["frames"] == 17640 and len(r["max"]) == 64 and max(r["max"]) > 0.1, r["frames"]
        h.ok({"type": "load_engine", "instrument": "drum", "slot": "engine", "id": "arbutus/drum"})
        kit_path = h.ok({"type": "load_kit", "instrument": "drum",
                         "kit": kit_doc(golden, {"2": "hat.wav"})})["kit_path"]
        assert os.path.exists(kit_path), kit_path
        r = h.ok({"type": "waveform", "instrument": "drum", "pad": 2, "bins": 32})
        assert r["frames"] == 2400 and r["path"].endswith("hat.wav"), r
        r = h.req({"type": "waveform", "instrument": "drum", "pad": 3})
        assert r["type"] == "error" and r["code"] == "not_found", r
        ok("waveform: the sampler's sample by slot + key, a drum pad by pad index")

        # FX tabs take assets like MOD tabs. No stock effect declares one, so
        # the sampler stands in as the tab: only the plumbing is under test.
        r = h.ok({"type": "add_tab", "instrument": "synth", "engine": "arbutus/sampler",
                  "assets": {"sample": "sample.wav"}})
        want = os.path.join(golden, "sample.wav")
        assert r["rack"]["tabs"][0]["assets"] == {"sample": want}, r["rack"]["tabs"][0]
        assert r["tabs"][0]["assets"] == {"sample": want}, r["tabs"][0]
        rack = r["rack"]
        r = h.req({"type": "add_tab", "engine": "arbutus/sampler", "assets": {"kit": "sample.wav"}})
        assert r["type"] == "error" and r["code"] == "unknown_asset", r
        h.ok({"type": "load_rack", "instrument": "synth", "rack": {"format": 1, "kind": "rack", "tabs": []}})
        r = h.ok({"type": "load_rack", "instrument": "synth", "rack": rack})
        assert r["rack"]["tabs"][0]["assets"] == {"sample": want}, r["rack"]
        r = h.ok({"type": "waveform", "instrument": "synth", "slot": "fx", "tab": 0, "key": "sample", "bins": 8})
        assert r["frames"] == 17640, r
        ok("fx tabs: assets resolve, refuse an unknown key, round-trip through load_rack")

    # #105: on the device the temp directory is RAM, and the resolved kit is
    # named per instrument and per pid -- so it never accumulates within a run,
    # but one file per host restart stayed there until a reboot. The host now
    # takes them back out on the way down.
    assert not os.path.exists(kit_path), kit_path
    ok("load_kit: the resolved kit.json is gone from the temp directory at exit")

    # And hostcase.Host's own directory belongs to the object rather than to
    # `close()` (a gate run had left 176 of them in the CM4's tmpfs), so it
    # goes when the last reference does -- with the cycle collector *off*,
    # because reference counting is the whole of that claim. Written as a plain
    # `del` this was green in WSL and red on the CM4, 5 runs of 5 (#179): a
    # cycle in arbutus_ctl.connect kept the host reachable, so the directory
    # waited for a collection, and which machine got one before this line came
    # down to how many objects the run had allocated. host_protocol.py has the
    # machine-independent check on the cycle itself; this one is the directory.
    gc.disable()
    try:
        hdir = h.dir
        del h
        assert not os.path.exists(hdir), hdir
        # The control, because a present alarm can still be vacuous (#56): hold
        # a host in a cycle on purpose and the very same `del` must leave the
        # directory there, so the assertion above had something to catch.
        caged = Host(a.exe, [a.boxes, golden])
        caged.close()
        caged.itself = caged
        cdir = caged.dir
        del caged
        assert os.path.exists(cdir), "control freed without a collection: " + cdir
    finally:
        gc.enable()
    gc.collect()
    assert not os.path.exists(cdir), cdir
    ok("hostcase: the temp directory goes with the last reference, no collection needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
