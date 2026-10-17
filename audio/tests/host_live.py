#!/usr/bin/env python3
"""#853: the live stream — the situation document as merge patches, and frames.

PROTOCOL.md § The live stream. `live {on:true}` answers the whole *situation*
document and its `rev`; after that the host pushes `situation {rev, patch}`, an
RFC 7396 merge patch, only when the document changed and at most ~20 a second.
The document is the host's own half (transport, tempo, tracks, the four slots,
the input, the flags) with the UI's half (`ui_situation`) merged over it; a UI
session that leaves takes its half with it (#412's rule). `ui_frame {rev}`
becomes a `frame {rev}` push, at most every 200 ms.

Pinned here (each seen red against a host without it):
  * subscribe -> the whole document, with a rev;
  * one key press (the UI's half moving once) -> one patch at rev+1;
  * a host edit -> its own patch, in host terms;
  * nothing changed -> nothing pushed;
  * the rate cap, a whole-document request between changes included (#1179);
  * a UI leaving clears its fields, a one-shot publisher's stand;
  * frames, the watcher count, and the refusals.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402
from arbutus_ctl import connect                                     # noqa: E402


def merge(doc, patch):
    """RFC 7396, the client's half: what a phone does with each push."""
    if not isinstance(patch, dict):
        return patch
    out = dict(doc) if isinstance(doc, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge(out.get(k), v)
    return out


def ui_conn(h, hello=True):
    """A second connection that publishes as the UI does."""
    c = connect(h.sock)
    if hello:
        c.hello(firmware="arbutus-ui")
    return c


def situations(c):
    return [p for p in c.pushes() if p.get("type") == "situation"]


def subscribe(h):
    r = h.ok({"type": "live", "on": True})
    assert r["type"] == "live" and r["on"] is True, r
    h.c.drain(0.15)                      # the watcher count's own push
    h.c.clear_pushes()
    return r


def test_subscribe_answers_the_whole_document(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        r = subscribe(h)
        s = r["situation"]
        assert isinstance(r["rev"], int), r
        for k in ("transport", "tempo", "tracks", "instruments", "input", "flags"):
            assert k in s, "no %r in %s" % (k, sorted(s))
        assert sorted(s["tracks"], key=int) == [str(n) for n in range(1, 9)], s["tracks"]
        assert s["transport"]["playing"] is False and s["transport"]["bar"] == 1, s
        assert s["instruments"]["front"] == "synth", s["instruments"]
        assert r["watchers"] == 1, r
        # and `situation` answers the same document to anyone, subscribed or not
        other = connect(h.sock)
        other.hello()
        again = other.ok({"type": "situation"})
        assert (again["rev"], again["situation"]) == (r["rev"], s), again
        other.close()
        ok("live {on:true}: the whole document at rev %d, %d bytes"
           % (r["rev"], len(str(s))))
    return 0


def test_one_key_press_is_one_patch_at_the_next_rev(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        ui = ui_conn(h)
        ui.ok({"type": "ui_situation", "situation": {"octave": 0, "screen": {"page": "SYNTH"}}})
        r = subscribe(h)
        doc, rev = r["situation"], r["rev"]
        assert doc["octave"] == 0 and doc["screen"]["page"] == "SYNTH", doc
        # `‹` on SYNTH: the UI's half moves once
        ui.ok({"type": "ui_situation", "situation": {"octave": -1, "screen": {"page": "SYNTH"}}})
        p = h.c.wait_push("situation", timeout=2.0)
        assert p is not None, "no patch for the key press"
        h.c.drain(0.3)
        got = situations(h.c)
        assert len(got) == 1, "one press, %d patches: %s" % (len(got), got)
        assert p["rev"] == rev + 1, (p["rev"], rev)
        assert p["patch"] == {"octave": -1}, p["patch"]
        doc = merge(doc, p["patch"])
        assert doc == h.ok({"type": "situation"})["situation"]
        ui.close()
        ok("one press -> one patch {octave:-1} at rev %d" % p["rev"])
    return 0


def test_a_host_edit_is_patched_in_host_terms(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        r = subscribe(h)
        h.ok({"type": "tempo", "bpm": 93})
        h.ok({"type": "metronome", "level": 0.5})
        h.ok({"type": "mixer", "track": 2, "mute": True})
        h.ok({"type": "set_clips", "track": 1,
              "clips": [{"start_frame": 0, "len": 96000, "src_offset": 0, "gain": 1.0}]})
        doc = r["situation"]
        end = time.monotonic() + 3.0
        want = lambda d: (d["tempo"]["bpm"] == 93 and d["tempo"]["click"]["on"]  # noqa: E731
                          and d["tracks"]["3"]["mute"] and d["tracks"]["2"]["has_clip"])
        while not want(doc) and time.monotonic() < end:
            p = h.c.wait_push("situation", timeout=1.0)
            if p is None:
                break
            h.c._pushes.remove(p)                                   # noqa: SLF001
            doc = merge(doc, p["patch"])
        assert want(doc), doc
        assert doc["tracks"]["2"]["length_s"] == 2.0, doc["tracks"]["2"]
        assert doc["tracks"]["2"]["clips"] == 1, doc["tracks"]["2"]
        assert doc == h.ok({"type": "situation"})["situation"]
        ok("tempo, the click, a mute and a clip arrive as patches; the merged copy is the document")
    return 0


def test_nothing_changed_is_nothing_pushed(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        ui = ui_conn(h)
        half = {"octave": 0, "screen": {"page": "TRACK"}}
        ui.ok({"type": "ui_situation", "situation": half})
        subscribe(h)
        for _ in range(10):                  # the UI republishing the same half
            ui.ok({"type": "ui_situation", "situation": half})
        h.ok({"type": "tempo", "bpm": 120})  # the tempo it already has
        h.c.drain(0.5)
        got = situations(h.c)
        assert got == [], "pushed with nothing changed: %s" % got
        ui.close()
        ok("an unchanged half, ten times, and a no-op edit: no patch in 0.5 s")
    return 0


def test_the_rate_is_capped(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        ui = ui_conn(h)
        r = subscribe(h)
        doc = r["situation"]
        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < 0.5:   # the rate is what is measured
            n += 1
            ui.ok({"type": "ui_situation", "situation": {"count": n}})
        dt = time.monotonic() - t0
        last = h.c.wait_push("situation", where=lambda p: p["patch"].get("count") == n,
                             timeout=2.0)
        assert last is not None, "the last change never arrived"
        got = situations(h.c)
        cap = int(dt * 20) + 2
        assert len(got) <= cap, "%d patches for %.2f s, the cap is %d" % (len(got), dt, cap)
        assert len(got) >= 2, "%d changes went out as %d patch" % (n, len(got))
        revs = [p["rev"] for p in got]
        assert revs == list(range(r["rev"] + 1, r["rev"] + 1 + len(got))), revs
        for p in got:
            doc = merge(doc, p["patch"])
        assert doc["count"] == n, doc
        ui.close()
        ok("%d changes in %.2f s -> %d patches, revs gapless, the last one current"
           % (n, dt, len(got)))
    return 0


def test_whole_document_requests_keep_the_cap(a):
    """#1179 (t853 rows 3-4): another client asking for the whole document
    between changes does not push patches to a subscriber faster than the cap,
    and every answer's `rev` names the document the subscriber has at it."""
    with Host(a.exe, [a.boxes], block=128) as h:
        ui = ui_conn(h)
        asker = connect(h.sock)
        r = subscribe(h)
        doc = r["situation"]
        answers = []
        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < 0.5:
            n += 1
            ui.ok({"type": "ui_situation", "situation": {"count": n}})
            q = asker.ok({"type": "situation"})
            answers.append((q["rev"], q["situation"]))
        dt = time.monotonic() - t0
        last = h.c.wait_push("situation", where=lambda p: p["patch"].get("count") == n,
                             timeout=2.0)
        assert last is not None, "the last change never arrived"
        got = situations(h.c)
        cap = int(dt * 20) + 2
        assert len(got) <= cap, "%d patches for %.2f s with %d whole requests, the cap " \
            "is %d" % (len(got), dt, n, cap)
        revs = [p["rev"] for p in got]
        assert revs == list(range(r["rev"] + 1, r["rev"] + 1 + len(got))), revs
        at = {r["rev"]: doc}
        for p in got:
            doc = merge(doc, p["patch"])
            at[p["rev"]] = doc
        assert doc["count"] == n, doc
        for rev, whole in answers:
            assert at.get(rev) == whole, "rev %d answered a document the subscriber " \
                "never had at it" % rev
        asker.close()
        ui.close()
        ok("%d changes and %d whole requests in %.2f s -> %d patches, revs gapless, "
           "every answer the subscriber's document at its rev" % (n, n, dt, len(got)))
    return 0


def test_a_ui_leaving_clears_its_half(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        ui = ui_conn(h)
        ui.ok({"type": "ui_situation",
               "situation": {"octave": 1, "screen": {"page": "SEQ"},
                             "transport": {"selected": 3}}})
        r = subscribe(h)
        assert r["situation"]["transport"]["selected"] == 3, r["situation"]
        # a one-shot publisher does not replace a UI session's half
        one = ui_conn(h, hello=False)
        one.ok({"type": "ui_situation", "situation": {"screen": {"page": "STAND-IN"}}})
        one.close()
        assert h.ok({"type": "situation"})["situation"]["screen"]["page"] == "SEQ"
        h.c.drain(0.2)
        h.c.clear_pushes()
        ui.close()
        p = h.c.wait_push("situation", timeout=2.0)
        assert p is not None, "the UI left and the phone was not told"
        assert p["patch"] == {"octave": None, "screen": None,
                              "transport": {"selected": None}}, p["patch"]
        doc = merge(r["situation"], p["patch"])
        assert "selected" not in doc["transport"] and "playing" in doc["transport"], doc
        # a publisher that never said hello (the mock's --ui) owns nothing
        one = ui_conn(h, hello=False)
        one.ok({"type": "ui_situation", "situation": {"screen": {"page": "MIXER"}}})
        one.close()
        h.c.drain(0.3)
        assert h.ok({"type": "situation"})["situation"]["screen"]["page"] == "MIXER"
        ok("a UI session's half goes with it as nulls; a one-shot's stands")
    return 0


def test_frames_watchers_and_refusals(a):
    with Host(a.exe, [a.boxes], block=128) as h:
        ui = ui_conn(h)
        first = ui.ok({"type": "ui_situation", "situation": {}})
        assert first["watchers"] == 0, first
        subscribe(h)
        told = ui.wait_push("live", where=lambda p: p.get("watchers") == 1, timeout=2.0)
        assert told is not None, "the UI was not told someone watches"
        for rev in range(1, 31):
            ui.ok({"type": "ui_frame", "rev": rev})
        last = h.c.wait_push("frame", where=lambda p: p["rev"] == 30, timeout=2.0)
        assert last is not None, "the newest frame was never announced"
        frames = h.c.pushes("frame")
        assert len(frames) <= 3, "30 frames in a burst -> %d pushes" % len(frames)
        # nobody watching: a frame goes nowhere, and the UI is told
        h.ok({"type": "live", "on": False})
        assert ui.wait_push("live", where=lambda p: p.get("watchers") == 0, timeout=2.0)
        h.c.clear_pushes()
        ui.ok({"type": "ui_frame", "rev": 31})
        h.c.drain(0.4)
        assert h.c.pushes("frame") == [] and situations(h.c) == [], h.c.pushes()
        for bad in ({"type": "ui_situation"}, {"type": "ui_situation", "situation": [1]},
                    {"type": "ui_situation", "situation": {"a": None}},
                    {"type": "ui_frame", "rev": "1"}):
            got = ui.request(bad)
            assert (got["type"], got.get("code")) == ("error", "bad_request"), (bad, got)
        ui.close()
        ok("30 frames -> %d pushes ending at 30; none unwatched; watchers told; refusals"
           % len(frames))
    return 0


def main():
    a = args()
    for t in (test_subscribe_answers_the_whole_document,
              test_one_key_press_is_one_patch_at_the_next_rev,
              test_a_host_edit_is_patched_in_host_terms,
              test_nothing_changed_is_nothing_pushed,
              test_the_rate_is_capped,
              test_whole_document_requests_keep_the_cap,
              test_a_ui_leaving_clears_its_half,
              test_frames_watchers_and_refusals):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
