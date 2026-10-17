#!/usr/bin/env python3
"""The rack: macros, an old parallel span loading flat, and every refusal (engine-spec.md §6, §7.4)."""

import json
import os
import shutil
import sys
import tempfile

from hostcase import Host, args, db, ok, peak, peak_error, read_wav

FRAMES = 8192


def render_rack(exe, boxes, rack, out, params=None, macro=None):
    """One host, one rack, one render of white-ish noise from the synth."""
    with Host(exe, [boxes]) as h:
        # One patch: one chain build, so nothing is crossfading at frame 0.
        h.ok({"type": "load_patch", "patch": {
            "format": 1, "kind": "patch", "name": "RACKTEST",
            "engine": {"id": "arbutus/example", "params": {}},
            "env": {"a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"},
            "fx": rack}})
        for tab, idx, value in (params or []):
            h.ok({"type": "set_param", "slot": "fx", "tab": tab, "idx": idx, "value": value})
        if macro is not None:
            h.ok({"type": "set_macro", "slot": 0, "value": macro})
        h.note(60, True, 0.9)
        h.note(67, True, 0.7)
        path = h.path(out)
        h.render(FRAMES, path)
        shutil.copy(path, os.path.join(TMP, out))
    return os.path.join(TMP, out)


def two_biquads(macros=None, parallel=None, params=None):
    # `parallel` builds a rack file written before parallel groups were cut
    # (engine-spec §6.5, 2026-10-02); the host must load it flat.
    rack = {"format": 1, "kind": "rack", "name": "TWO",
            "tabs": [{"instance": "a", "engine": "arbutus/biquad",
                      "params": params[0] if params else {}},
                     {"instance": "b", "engine": "arbutus/biquad",
                      "params": params[1] if params else {}}]}
    if macros:
        rack["macros"] = macros
    if parallel:
        rack["parallel"] = parallel
    return rack


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix="arbutus-rack-")
    try:
        return _main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)    # #105: /tmp is RAM on the CM4


def _main():
    a = args()

    # --- a macro with two targets, honouring lo/hi/curve/depth --------------
    # cutoff is param 0 on the biquad. A macro at 1.0 with lo=0.2 hi=0.8 must
    # land both targets on 0.8; the same rack with the parameters set by hand
    # has to render the same samples.
    macro = [{"slot": 0, "name": "OPEN", "targets": [
        {"instance": "a", "param": "cutoff", "lo": 0.2, "hi": 0.8, "curve": "lin", "depth": 1.0},
        {"instance": "b", "param": "cutoff", "lo": 0.2, "hi": 0.8, "curve": "lin", "depth": 1.0}]}]
    by_macro = render_rack(a.exe, a.boxes, two_biquads(macros=macro), "macro_hi.wav", macro=1.0)
    by_hand = render_rack(a.exe, a.boxes, two_biquads(), "hand_hi.wav",
                          params=[(0, 0, 0.8), (1, 0, 0.8)])
    _, m = read_wav(by_macro)
    _, d = read_wav(by_hand)
    e = peak_error(m, d)
    assert db(e) <= -120.0, "macro at 1.0 != lo/hi top (%.1f dBFS)" % db(e)
    ok("macro: two targets both land on hi (%.1f dBFS)" % db(e))

    # curve exp at 0.5 is 0.25 of the way up: 0.2 + 0.25*(0.8-0.2) = 0.35
    macro_exp = [{"slot": 0, "name": "OPEN", "targets": [
        {"instance": "a", "param": "cutoff", "lo": 0.2, "hi": 0.8, "curve": "exp", "depth": 1.0},
        {"instance": "b", "param": "cutoff", "lo": 0.2, "hi": 0.8, "curve": "exp", "depth": 1.0}]}]
    cur = render_rack(a.exe, a.boxes, two_biquads(macros=macro_exp), "macro_exp.wav", macro=0.5)
    hand = render_rack(a.exe, a.boxes, two_biquads(), "hand_exp.wav",
                       params=[(0, 0, 0.35), (1, 0, 0.35)])
    _, c1 = read_wav(cur)
    _, c2 = read_wav(hand)
    assert db(peak_error(c1, c2)) <= -120.0, "exp curve is not x^2"
    ok("macro: curve exp maps 0.5 -> 0.35")

    # depth 0.5 halves the excursion about the centre of [lo, hi]:
    # centre 0.5, mapped 0.8 -> 0.5 + 0.5*(0.8-0.5) = 0.65
    macro_d = [{"slot": 0, "name": "OPEN", "targets": [
        {"instance": "a", "param": "cutoff", "lo": 0.2, "hi": 0.8, "curve": "lin", "depth": 0.5},
        {"instance": "b", "param": "cutoff", "lo": 0.2, "hi": 0.8, "curve": "lin", "depth": 0.5}]}]
    cur = render_rack(a.exe, a.boxes, two_biquads(macros=macro_d), "macro_depth.wav", macro=1.0)
    hand = render_rack(a.exe, a.boxes, two_biquads(), "hand_depth.wav",
                       params=[(0, 0, 0.65), (1, 0, 0.65)])
    _, c1 = read_wav(cur)
    _, c2 = read_wav(hand)
    assert db(peak_error(c1, c2)) <= -120.0, "depth does not scale about the centre"
    ok("macro: depth 0.5 halves the excursion about the centre")

    # --- an old rack with a parallel span loads as flat tabs in series ------
    # Parallel groups were cut from v1 (engine-spec §6.5, 2026-10-02). A file
    # that still carries "parallel" is not refused: it renders exactly as the
    # same tabs in series, and the document read back has no "parallel" key.
    pset = [{"cutoff": 0.7}, {"cutoff": 0.3}]
    flat = render_rack(a.exe, a.boxes, two_biquads(params=pset), "flat.wav")
    old = render_rack(a.exe, a.boxes,
                      two_biquads(parallel=[{"tabs": [0, 1], "levels": [0.5, 0.25]}], params=pset),
                      "old_span.wav")
    _, cf = read_wav(flat)
    _, co = read_wav(old)
    assert db(peak(cf)) > -60.0, "the series rack rendered silence; the comparison proves nothing"
    assert db(peak_error(cf, co)) <= -120.0, "an old parallel span still changes the sound"
    with Host(a.exe, [a.boxes]) as h:
        r = h.ok({"type": "load_rack",
                  "rack": two_biquads(parallel=[{"tabs": [0, 1], "levels": [1.0, 1.0]}])})
        assert "parallel" not in r["rack"], r["rack"]
        assert len(r["rack"]["tabs"]) == 2, r["rack"]
        r = h.req({"type": "set_parallel", "spans": []})
        assert r["type"] == "error", r
    ok("parallel: an old span loads flat, renders as series, and is not written back")

    # DDR-049: the master bypasses the whole rack and an engine tab bypasses
    # only that node. Both must be real passthroughs, not merely document
    # flags, and the format-1 document carries both states.
    dry = render_rack(a.exe, a.boxes,
                      {"format": 1, "kind": "rack", "name": "DRY", "tabs": []},
                      "bypass_dry.wav")
    by_master = two_biquads(params=[{"cutoff": 0.1}, {"cutoff": 0.1}])
    by_master["bypass"] = True
    master = render_rack(a.exe, a.boxes, by_master, "bypass_master.wav")
    by_tabs = two_biquads(params=[{"cutoff": 0.1}, {"cutoff": 0.1}])
    for tab in by_tabs["tabs"]:
        tab["bypass"] = True
    tabs = render_rack(a.exe, a.boxes, by_tabs, "bypass_tabs.wav")
    _, d = read_wav(dry)
    _, bm = read_wav(master)
    _, bt = read_wav(tabs)
    assert db(peak_error(d, bm)) <= -120.0, "whole-rack bypass is not dry"
    assert db(peak_error(d, bt)) <= -120.0, "tab bypass is not dry"
    ok("bypass: master and per-tab states are audible passthroughs")

    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "add_tab", "engine": "arbutus/biquad", "instance": "lp"})
        r = h.ok({"type": "set_bypass", "index": 0, "bypass": True})
        assert r["rack"]["tabs"][0]["bypass"] is True, r
        r = h.ok({"type": "set_bypass", "bypass": True})
        assert r["rack"]["bypass"] is True, r
        ok("bypass: set_bypass edits round-trip in the rack document")

    # --- refusals (§7.4) ----------------------------------------------------
    with Host(a.exe, [a.boxes]) as h:
        r = h.req({"type": "load_rack", "rack": {
            "format": 1, "kind": "rack", "name": "NOPE",
            "requires": ["arbutus/formant"],
            "tabs": [{"instance": "f", "engine": "arbutus/formant", "params": {}}]}})
        assert r["type"] == "error", r
        assert r["message"] == "NEEDS ENGINE arbutus/formant", r
        ok("refuse: %s" % r["message"])

        r = h.req({"type": "load_rack", "rack": {
            "format": 1, "kind": "rack", "name": "OLD",
            "tabs": [{"instance": "lp", "engine": "arbutus/biquad",
                      "min_version": "9.0.0", "params": {}}]}})
        assert r["type"] == "error" and r["message"].startswith("NEEDS arbutus/biquad"), r
        ok("refuse: %s" % r["message"])

        # and nothing loaded: the rack is still empty (§7.4, "loads nothing")
        st = h.c.state()
        assert st["instruments"]["synth"]["fx"]["tabs"] == [], st["instruments"]["synth"]["fx"]

        # §6.6: only an engine that declares ARB_CAP_SIDECHAIN gets a source
        h.ok({"type": "add_tab", "engine": "arbutus/biquad", "instance": "lp"})
        r = h.req({"type": "set_sidechain", "index": 0, "source": "tape:1"})
        assert r["type"] == "error", r
        ok("refuse: sidechain on an engine without ARB_CAP_SIDECHAIN")
        r = h.ok({"type": "set_sidechain", "index": 0, "source": "none"})
        assert r["rack"]["tabs"][0].get("sidechain") is None, r["rack"]

    # --- a broken box and a future ABI, both surfaced as `error` ------------
    lib2 = os.path.join(TMP, "lib2")
    src = os.path.join(a.boxes, "arbutus", "biquad")
    for name, mutate in (("drift", drift), ("abi9", abi9)):
        dst = os.path.join(lib2, "arbtest", name)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        m = json.load(open(os.path.join(dst, "engine.json")))
        m["id"] = "arbtest/" + name
        mutate(m)
        json.dump(m, open(os.path.join(dst, "engine.json"), "w"), indent=2)

    with Host(a.exe, [a.boxes, lib2]) as h:
        r = h.req({"type": "load_rack", "rack": {
            "format": 1, "kind": "rack", "name": "BROKEN",
            "tabs": [{"instance": "x", "engine": "arbtest/drift", "params": {}}]}})
        assert r["type"] == "error" and r["message"] == "ENGINE BROKEN", r
        ok("refuse: ENGINE BROKEN (%s)" % r.get("detail"))

        r = h.req({"type": "load_engine", "slot": "engine", "id": "arbtest/abi9"})
        assert r["type"] == "error" and r["message"] == "NEEDS FIRMWARE UPDATE", r
        ok("refuse: NEEDS FIRMWARE UPDATE (%s)" % r.get("detail"))
    return 0


def drift(m):
    m["params"][0]["name"] = "DRIFT"          # snapshot no longer matches the binary


def abi9(m):
    m["abi"] = 9


TMP = ""

if __name__ == "__main__":
    sys.exit(main())
