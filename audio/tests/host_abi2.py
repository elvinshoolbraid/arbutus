#!/usr/bin/env python3
"""ABI 2 through the host (§15): the handshake, `display`, the new descriptor
fields in `state`, ctl_out zeroing, and the §15.8 default block."""

import os
import sys

from hostcase import Host, args, ok, peak, read_wav


def main():
    a = args()

    # --- hello: ABI 1 is refused now the emulator speaks 2; default block 128 --
    with Host(a.exe, [a.boxes], block=None) as h:
        r = h.req({"type": "hello", "proto": 1, "abi": 1, "firmware": "abi1-ui"})
        assert r["type"] == "error" and r["message"] == "NEEDS FIRMWARE UPDATE", r
        ok("hello: abi 1 client refused (kMinClientAbi = 2)")
        r = h.req({"type": "hello", "proto": 1, "abi": 2, "firmware": "abi2-ui"})
        assert r["type"] == "hello" and r["abi"] == 2 and r["box_abi"] == 2, r
        assert r["block"] == 128, r
        ok("hello: abi 2 accepted; default block is %d (§15.8)" % r["block"])
        for bad in (0, 1, 3):
            r = h.req({"type": "hello", "proto": 1, "abi": bad, "firmware": "x"})
            assert r["type"] == "error" and r["message"] == "NEEDS FIRMWARE UPDATE", r
        ok("hello: abi 0, 1 and 3 refused")
        st = h.c.state()
        assert st["abi"] == 2 and st["block"] == 128 and st["max_block"] == 128, st
        ok("state: abi 2, block 128")

    with Host(a.exe, [a.boxes]) as h:
        # --- descriptor fields and display -----------------------------------
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbtest/abi2"})
        eng = h.c.state()["instruments"]["synth"]["engine"]
        params = eng["desc"]["params"]
        assert [p["per_voice"] for p in params] == [True, False, False], params
        assert [p["per_note"] for p in params] == [False, False, True], params
        assert eng["desc"]["outputs"] == [], eng["desc"]
        ok("state: per_voice, per_note and outputs reach the descriptor")

        assert eng.get("display") == {"rate": "STOP"}, eng.get("display")
        ok("display: format_param labels RATE at centre as %r" % eng["display"]["rate"])
        h.ok({"type": "set_param", "slot": "engine", "idx": 1, "value": 1.0})
        disp = h.c.state()["instruments"]["synth"]["engine"]["display"]
        assert disp == {"rate": "1/32T"}, disp
        h.ok({"type": "set_param", "slot": "engine", "idx": 1, "value": 0.0})
        disp = h.c.state()["instruments"]["synth"]["engine"]["display"]
        assert disp == {"rate": "50.0 Hz"}, disp
        ok("display: follows the value (%r), only for params the box labels" % disp["rate"])

        h.ok({"type": "add_tab", "engine": "arbutus/biquad", "instance": "lp"})
        tab = h.c.state()["instruments"]["synth"]["tabs"][0]
        assert "display" not in tab, tab
        ok("display: absent for a box without format_param (backward compatible)")

        # --- ctl_out is zero on entry every block ------------------------------
        # The box writes 0.5 into ctl_out after each block and turns any
        # non-zero value it finds on entry into +1.0 DC, so a clean render is a
        # level-0.5 note under the envelope and nothing more.
        h.ok({"type": "remove_tab", "index": 0})
        h.note(60, True, 1.0)
        out = h.path("ctl.wav")
        h.render(16384, out)
        _, chans = read_wav(out)
        p = peak(chans)
        assert 0.1 < p <= 0.5001, "peak %f: ctl_out was not zeroed before process()" % p
        ok("ctl_out: zero on entry across %d blocks (peak %.3f)" % (16384 // 512, p))

    # --- rule 7 in a box: the template's LEVEL glides, it does not step -------
    with Host(a.exe, [a.boxes]) as h:          # 512-frame blocks: ramp = one block
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.note(57, True, 1.0)
        h.render(8192)
        h.ok({"type": "set_param", "slot": "engine", "idx": 3, "value": 0.0})
        out = h.path("glide.wav")
        h.render(1024, out)
        _, chans = read_wav(out)
        head = peak([c[:64] for c in chans])
        tail = peak([c[512:] for c in chans])
        assert head > 0.02, "LEVEL 0 stepped instead of gliding (head peak %f)" % head
        assert tail == 0.0, "the glide did not arrive within one block (tail peak %f)" % tail
        ok("smoothing: LEVEL -> 0 glides (first 64 frames peak %.3f) and lands in one block"
           % head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
