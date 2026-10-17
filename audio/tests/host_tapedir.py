#!/usr/bin/env python3
"""Where the media live: `tape_dir {dir, move}` (C1 step 3b, #157).

Elfy's answer on #157 is (a): SAVE moves the tape files into the project folder
and recording carries on there. This is the audio process's half, through the
whole host on a real filesystem:

  * `tape_save` onto the medium's own path is a flush, not a copy -- the file
    stays one of our media (4 KiB header) instead of being replaced by a
    44-byte-header WAV behind the I/O thread's open handle;
  * `move` renames the medium and its sidecar into a new folder, the next take
    appends there, and the old folder is left without them; a folder that
    already holds a medium refuses the move and nothing moves;
  * an open (no `move`) reads nothing into RAM and takes each head from the
    repaired header, so a clip reads the audio that is in that folder,
    sample-exact against the file itself; a track with no file has no medium,
    and a WAV that is not one of ours is refused and left alone;
  * a host started with `--tape-dir` over media already there opens them the
    same way -- the head comes off the header, so a take appends after them;
  * without `--tape-dir` the message is refused.
"""

import os
import shutil
import tempfile

from hostcase import Host, args, ok, read_wav

RANGE = 49152                      # Tape::kRangeFrames
TAKE = RANGE + 20000               # one whole range and a partial second
HEADER = 4096                      # the live medium's header (design § 3.5)
FRAME = 8                          # stereo float32


def take(h, track, frames, notes=(60, 67)):
    h.ok({"type": "transport", "op": "locate", "position": 0})
    h.ok({"type": "transport", "op": "start"})
    h.ok({"type": "record", "track": track, "on": True})
    for n in notes:
        h.note(n, True, 0.9)
    h.render(frames)
    for n in notes:
        h.note(n, False)
    h.ok({"type": "record", "track": track, "on": False})
    h.ok({"type": "transport", "op": "stop"})
    h.render(512)
    return h.c.state()["tape"]["tracks"][track]["used_frames"]


def setup(h):
    h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
    h.ok({"type": "env", "a": 0.01, "d": 0.1, "s": 0.8, "r": 0.2, "mode": "poly"})


def ours(path, frames):
    """The medium on disk is one of ours and holds exactly `frames`."""
    with open(path, "rb") as f:
        head = f.read(HEADER)
    assert head[:4] == b"RIFF", head[:4]
    assert len(head) == HEADER, len(head)
    size = os.path.getsize(path)
    assert size == HEADER + frames * FRAME, (size, HEADER + frames * FRAME)


def payload(path, frames):
    """The audio bytes of a medium's first `frames`, the header left out."""
    with open(path, "rb") as f:
        f.seek(HEADER)
        return f.read(frames * FRAME)


def main():
    a = args()
    tmp = tempfile.mkdtemp(prefix="arbutus-tapedir-test-")
    try:
        work = os.path.join(tmp, "work")
        proj = os.path.join(tmp, "projects", "2026-10-03", "PROJ_0001")
        with Host(a.exe, [a.boxes], extra=["--tape-dir", work]) as h:
            setup(h)
            used = take(h, 0, TAKE)
            assert used >= TAKE, used
            medium = os.path.join(work, "tape-1.wav")
            ours(medium, used)

            # -- tape_save onto the medium itself: a flush --------------------
            r = h.ok({"type": "tape_save", "track": 0, "file": medium})
            assert r["frames"] == used and r.get("in_place") is True, r
            ours(medium, used)
            # and onto another path it is still the copy it always was: the
            # 44-byte header the old writer uses, which is how a save-as-copy
            # (and every project saved before step 3b) looks.
            copy = os.path.join(tmp, "copy.wav")
            r = h.ok({"type": "tape_save", "track": 0, "file": copy})
            assert r["frames"] == used and "in_place" not in r, r
            assert os.path.getsize(copy) == 44 + used * FRAME, os.path.getsize(copy)
            ok("tape dir: tape_save onto the medium is a flush; elsewhere a copy")

            # -- move: SAVE's rename --------------------------------------------
            # The audio as it is before SAVE, read off the disk now, so the
            # checks after the move compare against something the move cannot
            # have touched (#484 finding 5: a move that zeroed the audio passed
            # every assertion here, which read only names, sizes and headers).
            snap = payload(medium, used)
            assert len(snap) == used * FRAME and snap.count(0) < len(snap), "a silent take"
            r = h.ok({"type": "tape_dir", "dir": proj, "move": True})
            assert r["dir"] == proj and r["moved"] is True, r
            assert r["frames"][0] == used and r["frames"][1:] == [0] * 7, r
            assert r["refused"] == [], r
            assert sorted(os.listdir(proj)) == ["tape-1.peaks", "tape-1.wav"], os.listdir(proj)
            assert os.listdir(work) == [], os.listdir(work)
            assert h.c.state()["tape"]["io"]["dir"] == proj
            ours(os.path.join(proj, "tape-1.wav"), used)
            assert payload(os.path.join(proj, "tape-1.wav"), used) == snap, \
                "the move changed the audio"

            # The take carries on there: a second take appends to the moved
            # file (the I/O thread's handle followed the rename) and nothing
            # reappears in the old folder.
            used2 = take(h, 0, TAKE, notes=(62,))
            assert used2 > used, (used2, used)
            ours(os.path.join(proj, "tape-1.wav"), used2)
            assert payload(os.path.join(proj, "tape-1.wav"), used) == snap, \
                "the next take changed the audio recorded before SAVE"
            assert os.listdir(work) == [], os.listdir(work)
            io = h.c.state()["tape"]["io"]
            assert io["errors"] == 0 and io["dropped"] == 0, io
            ok("tape dir: move renames the medium into the project and the next "
               "take appends there (%d -> %d frames)" % (used, used2))

            # A folder that already holds a medium of that name refuses, and
            # nothing on either side moves.
            other = os.path.join(tmp, "other")
            os.makedirs(other)
            shutil.copy(copy, os.path.join(other, "tape-1.wav"))
            r = h.req({"type": "tape_dir", "dir": other, "move": True})
            assert r.get("type") == "error" and r.get("code") == "exists", r
            assert sorted(os.listdir(proj)) == ["tape-1.peaks", "tape-1.wav"], os.listdir(proj)
            assert h.c.state()["tape"]["io"]["dir"] == proj
            ok("tape dir: a folder that already holds tape-1.wav refuses the move")

            # A folder holding a medium the tape has no file for yet refuses
            # too (#484 finding 1): track 2's first take would make its medium
            # under that name, from frame zero, over the folder's own audio.
            os.remove(os.path.join(other, "tape-1.wav"))
            shutil.copy(copy, os.path.join(other, "tape-2.wav"))
            r = h.req({"type": "tape_dir", "dir": other, "move": True})
            assert r.get("type") == "error" and r.get("code") == "exists", r
            assert sorted(os.listdir(other)) == ["tape-2.wav"], os.listdir(other)
            # And a dangling symlink is a name taken (finding 2): `rename`
            # would have replaced the link.
            os.remove(os.path.join(other, "tape-2.wav"))
            os.symlink(os.path.join(tmp, "owner-missing.wav"), os.path.join(other, "tape-1.wav"))
            r = h.req({"type": "tape_dir", "dir": other, "move": True})
            assert r.get("type") == "error" and r.get("code") == "exists", r
            assert os.path.islink(os.path.join(other, "tape-1.wav"))
            assert sorted(os.listdir(proj)) == ["tape-1.peaks", "tape-1.wav"], os.listdir(proj)
            ok("tape dir: a medium for another track, or a dangling symlink, "
               "refuses the move too")

            # Moving to where it already is moves nothing and is not an error,
            # under any spelling of it (finding 4: a trailing slash was refused
            # `exists` against the tape's own files).
            for again in (proj, proj + "/", os.path.join(tmp, "projects", "2026-10-03", ".", "PROJ_0001")):
                r = h.ok({"type": "tape_dir", "dir": again, "move": True})
                assert r["frames"][0] == used2, (again, r)
                assert h.c.state()["tape"]["io"]["dir"] == proj, again
            assert payload(os.path.join(proj, "tape-1.wav"), used) == snap
            ok("tape dir: a move to the folder it is in, however spelled, moves nothing")
            shutil.rmtree(other)

        # -- open in place: LOAD --------------------------------------------------
        scratch = os.path.join(tmp, "scratch")
        with Host(a.exe, [a.boxes], extra=["--tape-dir", scratch]) as h:
            setup(h)
            assert take(h, 2, RANGE // 2) > 0
            # A WAV under one of our names that is not one of ours.
            shutil.copy(copy, os.path.join(proj, "tape-4.wav"))
            r = h.ok({"type": "tape_dir", "dir": proj})
            assert r["moved"] is False, r
            assert r["frames"][0] == used2, r
            assert r["frames"][2] == 0, r              # no tape-3.wav in the project
            assert r["refused"] == [3], r
            tracks = h.c.state()["tape"]["tracks"]
            assert tracks[0]["used_frames"] == used2, tracks[0]
            assert tracks[2]["used_frames"] == 0, tracks[2]
            assert tracks[3]["used_frames"] == 0, tracks[3]
            # The foreign file is left byte for byte alone, and the scratch
            # folder keeps its own medium.
            with open(os.path.join(proj, "tape-4.wav"), "rb") as f, open(copy, "rb") as g:
                assert f.read() == g.read()
            assert os.path.exists(os.path.join(scratch, "tape-3.wav"))
            os.remove(os.path.join(proj, "tape-4.wav"))

            # What the tape plays from now is the file in the project, sample
            # for sample: a copy written through the host's own reader against
            # the medium parsed straight off the disk.
            back = os.path.join(tmp, "back.wav")
            r = h.ok({"type": "tape_save", "track": 0, "file": back})
            assert r["frames"] == used2, r
            _, got = read_wav(back)
            _, want = read_wav(os.path.join(proj, "tape-1.wav"))
            assert len(got[0]) == len(want[0]) == used2, (len(got[0]), len(want[0]))
            wrong = sum(1 for c in range(2) for i in range(used2) if got[c][i] != want[c][i])
            assert wrong == 0, "%d frames differ" % wrong
            ok("tape dir: open reads the project's media in place, %d frames "
               "sample-exact; a missing track is empty, a foreign WAV refused" % used2)

            # And a take after the open appends to the project's medium.
            used3 = take(h, 0, RANGE // 2, notes=(64,))
            assert used3 > used2, (used3, used2)
            ours(os.path.join(proj, "tape-1.wav"), used3)

        # -- the boot: --tape-dir over media already there -----------------------
        with Host(a.exe, [a.boxes], extra=["--tape-dir", proj]) as h:
            tracks = h.c.state()["tape"]["tracks"]
            assert tracks[0]["used_frames"] == used3, (tracks[0], used3)
            setup(h)
            used4 = take(h, 0, RANGE // 4, notes=(65,))
            assert used4 > used3, (used4, used3)
            ours(os.path.join(proj, "tape-1.wav"), used4)
            ok("tape dir: a host started over a project's media takes its heads "
               "from their headers and appends after them")

        # -- tape_load into a folder: an old project converted (#297) -----------
        # The UI's half moves an old 44-byte-header WAV aside and `tape_load`s
        # it, which must leave one of our media in the folder that reopens to
        # the same frames. Before #297 the load wrote into pool slots it never
        # acquired: the head moved, the file stayed a bare header, and the
        # converted take was gone on the next open.
        conv = os.path.join(tmp, "converted")
        with Host(a.exe, [a.boxes], extra=["--tape-dir", conv]) as h:
            r = h.ok({"type": "tape_load", "track": 5, "file": copy})
            assert r["frames"] == used, r
            ours(os.path.join(conv, "tape-6.wav"), used)
            h.ok({"type": "tape_dir", "dir": scratch})
            r = h.ok({"type": "tape_dir", "dir": conv})
            assert r["frames"][5] == used and r["refused"] == [], r
            back = os.path.join(tmp, "conv-back.wav")
            h.ok({"type": "tape_save", "track": 5, "file": back})
            _, got = read_wav(back)
            _, want = read_wav(copy)
            wrong = sum(1 for c in range(2) for i in range(used) if got[c][i] != want[c][i])
            assert wrong == 0, "%d frames differ" % wrong
            ok("tape dir: tape_load writes one of our media into the folder, "
               "%d frames sample-exact after a reopen" % used)

        # -- the open project does not move under the tape (#297; #821 lets the
        #    folder itself be renamed) ---------------------------------------------
        # Once the medium lives in the project folder, a rename, move, trash or
        # delete of that folder -- or of a folder above it, or of a medium in
        # it -- would leave the I/O thread writing to a path that moved. Every
        # file op reaches the library through `library {op}` (the UI's browser,
        # the phone's rename, the HTTP DELETE), so that is where it is refused.
        lib = os.path.join(tmp, "lib")
        live = os.path.join(lib, "projects", "2026-10-03", "PROJ_0002")
        spare = os.path.join(lib, "projects", "2026-10-03", "PROJ_0003")
        os.makedirs(spare)
        with Host(a.exe, [a.boxes, lib], extra=["--tape-dir", conv]) as h:
            h.ok({"type": "tape_dir", "dir": live, "move": True})
            for msg in ({"op": "rename", "path": "projects/2026-10-03", "name": "X"},
                        {"op": "move", "path": "projects/2026-10-03/PROJ_0002", "to": ""},
                        {"op": "trash", "path": "projects/2026-10-03"},
                        {"op": "delete", "path": "projects"},
                        {"op": "trash", "path": "projects/2026-10-03/PROJ_0002/tape-6.wav"}):
                r = h.req(dict(msg, type="library"))
                assert r.get("type") == "error" and r.get("code") == "open_project" \
                    and r.get("message") == "OPEN ON THE INSTRUMENT", (msg, r)
            assert os.path.exists(os.path.join(live, "tape-6.wav"))
            r = h.ok({"type": "library", "op": "rename",
                      "path": "projects/2026-10-03/PROJ_0003", "name": "MINE"})
            assert os.path.isdir(os.path.join(lib, "projects", "2026-10-03", "MINE")), r
            # #821 (D99): the phone renames the open project. The folder itself
            # renames, the medium with it, and the I/O thread follows; nothing
            # above it or in it does (the refusals above).
            r = h.ok({"type": "library", "op": "rename",
                      "path": "projects/2026-10-03/PROJ_0002", "name": "X"})
            moved = os.path.join(lib, "projects", "2026-10-03", "X")
            assert os.path.exists(os.path.join(moved, "tape-6.wav")), r
            assert not os.path.exists(live), r
            assert h.ok({"type": "state"})["tape"]["io"]["dir"] == moved
            ok("tape dir: the folder the tape lives in refuses move / trash / "
               "delete, and every folder above it rename too; it renames itself "
               "with the tape (#821); a sibling does whatever it is asked")

        # -- no --tape-dir: nothing to point ------------------------------------
        with Host(a.exe, [a.boxes]) as h:
            r = h.req({"type": "tape_dir", "dir": proj})
            assert r.get("type") == "error" and r.get("message") == "NO TAPE DIR", r
            ok("tape dir: refused without --tape-dir")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
