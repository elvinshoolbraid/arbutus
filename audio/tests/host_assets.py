#!/usr/bin/env python3
"""Assets end to end: a patch names a file, the engine opens it (§3.2).

Content is not a parameter (DDR-037). This walks the whole path the ABI's
`load_asset` opened up -- patch -> host resolution -> engine -> sound -- and
every §7.4 refusal along it.
"""

import os
import sys

from hostcase import Host, args, ok, peak, read_wav

FRAMES = 24000


def patch(engine, assets=None, extra=None):
    e = {"id": engine, "params": {}}
    if assets is not None:
        e["assets"] = assets
    p = {"format": 1, "kind": "patch", "name": "ASSETS", "engine": e,
         "env": {"a": 0.005, "d": 0.2, "s": 0.8, "r": 0.2, "mode": "poly"}}
    if extra:
        p.update(extra)
    return p


def render_note(h, note, name):
    h.ok({"type": "panic"})
    h.note(note, True, 0.9)
    path = h.path(name)
    h.render(FRAMES, path)
    h.ok({"type": "note", "on": False, "note": note})
    _, c = read_wav(path)
    return peak(c)


def main():
    a = args()
    golden = os.path.join(a.audio_root, "tests", "golden")
    wav = os.path.join(golden, "sample.wav")
    kit = os.path.join(golden, "kit", "kit.json")

    # The second library root is what makes "sample.wav" resolvable: §7.2 says
    # a patch names assets library-relative.
    with Host(a.exe, [a.boxes, golden]) as h:
        # --- absolute path ---------------------------------------------------
        h.ok({"type": "load_patch", "patch": patch("arbutus/sampler", {"sample": wav})})
        st = h.c.state()["instruments"]["synth"]["engine"]
        assert st["assets"] == {"sample": wav}, st["assets"]
        assert st["desc"]["assets"] == ["sample"], st["desc"]["assets"]
        p = render_note(h, 60, "abs.wav")
        assert p > 0.05, "sampler played nothing: peak %f" % p
        ok("assets: an absolute path loads and plays (peak %.3f)" % p)

        # --- library-relative ------------------------------------------------
        h.ok({"type": "load_patch",
              "patch": patch("arbutus/sampler", {"sample": "sample.wav"})})
        st = h.c.state()["instruments"]["synth"]["engine"]
        assert st["assets"]["sample"] == wav, st["assets"]
        ok("assets: \"sample.wav\" resolved against a library root")

        # --- refusals (§7.4), and the previous graph keeps running ------------
        r = h.req({"type": "load_patch",
                   "patch": patch("arbutus/sampler", {"kit": wav})})
        assert r["type"] == "error" and r["code"] == "unknown_asset", r
        assert r["message"] == "UNKNOWN ASSET kit", r
        ok("refuse: %s" % r["message"])

        r = h.req({"type": "load_patch",
                   "patch": patch("arbutus/sampler", {"sample": "nope-not-here.wav"})})
        assert r["type"] == "error" and r["code"] == "asset_not_found", r
        assert r["message"] == "ASSET NOT FOUND sample", r
        ok("refuse: %s" % r["message"])

        st = h.c.state()["instruments"]["synth"]["engine"]
        assert st["assets"]["sample"] == wav, "a refused load changed the graph"
        p = render_note(h, 60, "still.wav")
        assert p > 0.05, "a refused load silenced the engine"
        ok("assets: a refused load leaves the previous one playing (peak %.3f)" % p)

        # --- an engine that declares no assets refuses any ---------------------
        r = h.req({"type": "load_patch",
                   "patch": patch("arbutus/osc", {"sample": wav})})
        assert r["type"] == "error" and r["code"] == "unknown_asset", r
        ok("refuse: %s on an engine with no assets" % r["message"])

        # --- load_kit: the pad map is one asset ------------------------------
        h.ok({"type": "instrument", "active": "drum"})
        h.ok({"type": "load_engine", "instrument": "drum", "slot": "engine",
              "id": "arbutus/drum"})
        r = h.ok({"type": "load_kit", "instrument": "drum",
                  "kit": {"format": 1, "kind": "kit", "name": "TEST-KIT",
                          "dir": os.path.join(golden, "kit"),
                          "pads": {"0": "kick.wav", "1": "snare.wav", "2": "hat.wav"}}})
        assert r.get("kit_path"), r
        st = h.c.state()["instruments"]["drum"]["engine"]
        assert list(st["assets"].keys()) == ["kit"], st["assets"]
        assert st["assets"]["kit"] == r["kit_path"], st["assets"]
        ok("kit: one asset, %s" % os.path.basename(r["kit_path"]))

        # Pad 0 is F3 = MIDI 53.
        p = render_note(h, 53, "kick.wav")
        assert p > 0.05, "the kit pad played nothing: peak %f" % p
        ok("kit: pad 0 on F3 plays (peak %.3f)" % p)

        # A kit naming a file that is not there loads nothing.
        r = h.req({"type": "load_kit", "instrument": "drum",
                   "kit": {"format": 1, "kind": "kit", "name": "BROKEN",
                           "dir": os.path.join(golden, "kit"),
                           "pads": {"0": "missing.wav"}}})
        assert r["type"] == "error", r
        ok("refuse: a kit with a missing pad (%s)" % r.get("detail"))
        p = render_note(h, 53, "kick2.wav")
        assert p > 0.05, "a refused kit silenced the drum engine"
        ok("kit: a refused kit leaves the old one playing (peak %.3f)" % p)

        # DRUM › ENV is the drum's AD (DDR-053), so the page is live.
        assert st["desc"]["honours_env"] is True, st["desc"]
        ok("kit: arbutus/drum v2 declares honours_env true (DDR-053)")

        # Swapping the engine drops the assets with the params (PROTOCOL.md).
        h.ok({"type": "load_engine", "instrument": "drum", "slot": "engine",
              "id": "arbutus/osc"})
        st = h.c.state()["instruments"]["drum"]["engine"]
        assert st["assets"] == {}, st["assets"]
        ok("assets: a different engine starts with none")

        # ...and a *patch* does the same. A patch carries the kit asset over so
        # that loading a sound onto DRUM does not drop the kit, but only onto
        # the engine that asked for it: a box that takes no assets, loaded from
        # a patch after the sampler, used to be refused UNKNOWN ASSET sample.
        # Found in stage I1 by audio/library/synth/va/*.patch (no `assets` key).
        h.ok({"type": "instrument", "active": "synth"})
        h.ok({"type": "load_patch", "instrument": "synth",
              "patch": patch("arbutus/sampler", {"sample": wav})})
        h.ok({"type": "load_patch", "instrument": "synth",
              "patch": patch("arbutus/va")})
        st = h.c.state()["instruments"]["synth"]["engine"]
        assert st["id"] == "arbutus/va", st
        assert st["assets"] == {}, st["assets"]
        p = render_note(h, 60, "va-after-sampler.wav")
        assert p > 0.01, "arbutus/va played nothing after a sampler patch: %f" % p
        ok("assets: a patch for another engine does not inherit them (peak %.3f)" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
