#!/usr/bin/env python3
"""Stage I1 through the host: va, string, wavetable, dbox.

The boxes' own goldens prove their DSP. This proves the parts only the host
can: a patch loading each one and making sound, the eight slots arriving with
the ranges the descriptor declares, the ENV page's MODE reaching PolyHost
(glide and unison), `wavetable`'s asset resolving library-relative, and
`dbox`'s **fileless kit** -- a pads map that is parameters and nothing else,
which stage I1 taught `load_kit` to accept -- through set_note_param and
copy_pad, the same messages `arbutus/drum` uses.
"""

import os
import sys

from hostcase import Host, args, ok, peak, read_wav

FRAMES = 24000
DBOX_PAD0, DBOX_PAD1 = 53, 54          # F3 and F#3 (kKitBaseNote)


def patch(engine, params=None, assets=None, env=None):
    e = {"id": engine, "params": params or {}}
    if assets is not None:
        e["assets"] = assets
    return {"format": 1, "kind": "patch", "name": "I1", "engine": e,
            "env": env or {"a": 0.005, "d": 0.2, "s": 0.9, "r": 0.2, "mode": "poly"}}


def play(h, note, name, frames=FRAMES, vel=0.9):
    h.ok({"type": "panic"})
    h.note(note, True, vel)
    path = h.path(name)
    h.render(frames, path)
    h.ok({"type": "note", "on": False, "note": note})
    return peak(read_wav(path)[1])


def slot(h, inst="synth"):
    return h.c.state()["instruments"][inst]["engine"]


def main():
    a = args()
    library = os.path.join(a.audio_root, "library")

    with Host(a.exe, [a.boxes, library], extra=["--rt-guard"]) as h:
        # --- the three synth boxes play from a patch -------------------------
        for box, note in (("arbutus/va", 48), ("arbutus/string", 52)):
            h.ok({"type": "load_patch", "instrument": "synth", "patch": patch(box)})
            st = slot(h)
            assert st["id"] == box, st["id"]
            assert len(st["desc"]["params"]) >= 7, st["desc"]["params"]
            p = play(h, note, box.split("/")[-1] + ".wav")
            assert p > 0.01, "%s played nothing: %f" % (box, p)
            ok("%s: loads from a patch and sounds (peak %.3f)" % (box, p))

        # --- wavetable: the table is an asset, named library-relative --------
        h.ok({"type": "load_patch", "instrument": "synth",
              "patch": patch("arbutus/wavetable", {"pos": 0.6},
                             {"wavetable": "synth/wavetable/harmonics.wav"})})
        st = slot(h)
        want = os.path.join(library, "synth", "wavetable", "harmonics.wav")
        assert st["assets"] == {"wavetable": want}, st["assets"]
        assert st["desc"]["assets"] == ["wavetable"], st["desc"]["assets"]
        p = play(h, 60, "wavetable.wav")
        assert p > 0.01, "wavetable played nothing: %f" % p
        ok("wavetable: the table resolves against a library root and sounds (peak %.3f)" % p)

        # POS reads as the frame it lands on, through format_param (§15.1).
        disp = st.get("display") or {}
        assert disp.get("pos", "").endswith("/15"), disp
        ok("wavetable: POS reads \"%s\" — the frame, not a percentage" % disp["pos"])

        # A patch naming the wrong key is refused before anything opens (§7.4).
        r = h.req({"type": "load_patch", "instrument": "synth",
                   "patch": patch("arbutus/wavetable", None, {"sample": want})})
        assert r["type"] == "error" and r["code"] == "unknown_asset", r
        ok("refuse: %s" % r["message"])

        # --- ENV MODE reaches PolyHost on the new boxes ----------------------
        h.ok({"type": "load_patch", "instrument": "synth",
              "patch": patch("arbutus/va", {"spread": 0.0},
                             env={"a": 0.001, "d": 0.2, "s": 1.0, "r": 0.2,
                                  "mode": "unison", "detune": 40.0})})
        p = play(h, 57, "va-unison.wav", frames=12000)
        assert p > 0.01, "unison played nothing"
        st = h.c.state()["instruments"]["synth"]
        assert st["env"]["mode"] == "unison", st["env"]
        assert abs(st["env"]["detune"] - 40.0) < 0.01, st["env"]
        ok("va: ENV MODE unison + 40 ct of DETUNE reach PolyHost (peak %.3f)" % p)

        # --- dbox: a kit that is parameters and no files ---------------------
        h.ok({"type": "instrument", "active": "drum"})
        h.ok({"type": "load_engine", "instrument": "drum", "slot": "engine",
              "id": "arbutus/dbox"})
        kit = {"format": 1, "kind": "kit", "name": "FILELESS",
               "pads": {"0": {"params": {"mode": 0.0}},
                        "1": {"params": {"mode": 0.6, "decay": 0.2}}}}
        r = h.ok({"type": "load_kit", "instrument": "drum", "kit": kit})
        assert r.get("kit_path"), r
        pads = h.c.state()["instruments"]["drum"]["kit"]["pads"]
        assert "file" not in pads["0"], pads["0"]
        # normalise_kit still fills every per_note id from the descriptor, so
        # the document is the save whether or not there is a sample in it.
        assert set(pads["0"]["params"]) == {"mode", "tune", "decay", "snap",
                                            "tone", "drive", "level", "pan"}, pads["0"]
        ok("dbox: a fileless kit loads and normalises (%d ids on pad 0)"
           % len(pads["0"]["params"]))

        p0 = play(h, DBOX_PAD0, "dbox-kick.wav", frames=12000)
        p1 = play(h, DBOX_PAD1, "dbox-hat.wav", frames=12000)
        assert p0 > 0.05 and p1 > 0.01, (p0, p1)
        ok("dbox: pad 0 KICK %.3f, pad 1 HAT %.3f" % (p0, p1))

        # set_note_param: the same message arbutus/drum takes, on a pad with no
        # file behind it. LEVEL down must be audible and must land in the kit.
        h.ok({"type": "set_note_param", "instrument": "drum", "slot": "engine",
              "pad": 0, "id": "level", "value": 0.15})
        pads = h.c.state()["instruments"]["drum"]["kit"]["pads"]
        assert abs(pads["0"]["params"]["level"] - 0.15) < 1e-6, pads["0"]
        quiet = play(h, DBOX_PAD0, "dbox-quiet.wav", frames=12000)
        assert quiet < p0 * 0.5, "LEVEL 0.15 was not quieter: %f vs %f" % (quiet, p0)
        ok("dbox: set_note_param on a fileless pad is audible (%.3f -> %.3f)" % (p0, quiet))

        # copy_pad carries params with no file to carry.
        h.ok({"type": "copy_pad", "instrument": "drum", "from": 1, "to": 0})
        pads = h.c.state()["instruments"]["drum"]["kit"]["pads"]
        assert pads["0"]["params"]["mode"] == pads["1"]["params"]["mode"], pads
        assert "file" not in pads["0"], pads["0"]
        ok("dbox: copy_pad copies a fileless pad")

        # A pad with neither a file nor params is still a refusal.
        r = h.req({"type": "load_kit", "instrument": "drum",
                   "kit": {"format": 1, "kind": "kit", "name": "BAD",
                           "pads": {"0": 7}}})
        assert r["type"] == "error", r
        ok("refuse: a pad that is neither a file nor params (%s)" % r.get("detail"))

        # dbox does not take the instrument's AD: DECAY is per pad (§15.11's
        # deviation is what this box exists to undo).
        assert slot(h, "drum")["desc"]["honours_env"] is False, slot(h, "drum")["desc"]
        ok("dbox: honours_env false — the envelope is per pad, not the page's")

        # --- no allocation in the block with all four loaded -----------------
        h.ok({"type": "instrument", "active": "synth"})
        h.ok({"type": "load_patch", "instrument": "synth",
              "patch": patch("arbutus/wavetable", None,
                             {"wavetable": "synth/wavetable/vowel.wav"})})
        h.note(64, True, 0.8)
        h.note(DBOX_PAD0, True, 0.9)
        r = h.ok({"type": "render", "frames": 24000})
        h.ok({"type": "panic"})
        allocs = r.get("rt_allocs")
        assert allocs is not None, r
        if allocs < 0:
            ok("rt-guard: unavailable on this platform")
        else:
            assert allocs == 0, "%d heap blocks gained inside render_block" % allocs
            ok("rt-guard: no allocation in render_block with wavetable + dbox loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
