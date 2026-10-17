---
tags: [emulator, persistence]
status: active
read_when: you touch autosave, the state/ folder, project files or sequencer pattern files
index: emulator/CLAUDE.md
---

# State and files on disk

> Moved verbatim from `emulator/CLAUDE.md` on 2026-10-08 (#440), headings unchanged, so a `§ <name>` citing that file finds its section here. The index of this folder's notes is `emulator/CLAUDE.md` § Read by task.

## Sequencer pattern files

`audio/library/seq/<box>/*.seq` items carry patterns (DDR-037): a format-1
document with `pattern` (lists of MIDI notes, `[]` = rest, `"tie"` = a tie),
optional `root`, and `named_params`. The format is written down once, in
`audio/library/README.md` § Patterns.

**Since stage H2 the file is the asset.** `NativeLibrary.apply_item` calls
`NativeSequencer.load_pattern(doc, path)` and the *path* goes down as
`load_engine {assets: {"pattern": path}}` — the box opens the library's own
document. N1's `_replay_pattern` bridge, which played the mirror in as the
keystrokes that would have entered it, is gone: it only ever worked on a box
that declared REC/REST/UNDO/CLEAR, and `arp` and `euclid` declare none.

The Python list is still the *picture* — the host reports no pattern back, so
the note row is drawn from a mirror of the entry rules. A pattern that was
entered rather than loaded rides to the box on a scratch file under `$TMPDIR`,
and becomes a library item only when a patch is promoted (§ H2 below).

Shipped: `init.seq` (empty — every folder's first item is the blank one, and a
fresh SEQ slot must not arrive with someone else's tune in it), `penta-7.seq`
(7 steps of A minor pentatonic at 1/8 — seven against a bar of eight precesses
one eighth per bar and comes round after 8 bars), `penta-5.seq` (5 steps at
1/16) and `pattern/riff-8.seq` (8 steps at absolute pitch). Every one of them
is polymeter by construction: the step count does not divide the bar.

## Persistence

`state/` holds four kinds of file, all autosaved, all wiped by `--fresh`:

| File | Contents | When it is written |
|---|---|---|
| `<page>.json` | 6 slot snapshots per slot page (SYNTH, DRUM, SEQ) | debounced 0.25 s after any change |
| `project.json` | BPM, **current page** (`page` — read by `arbutus-net` since #173, so it is a contract now, not ours alone), active instrument, transport/loop/selected track, mixer, one snapshot per non-slot page, and **`open_project`** — the id of the saved project this state came from, `"<date>/<name>"` or `null` (#95) | every 2 s while running when it changed (#624: the timer skips an identical snapshot), and on exit |
| `tape.json` | every clip's start/length/gain/`src_offset`/`src_track`, per track — geometry only | on STOP and on exit, and only when the clip layout changed |
| `tape-N.wav` (N = 1..8) | that track's medium **while no project is open** — written by the audio process itself, a range at a time, as the take happens (C1 step 3b, #297). Once a SAVE or a LOAD has a project open, the media live in *that* folder instead | by the audio process, during the take; **never** by STOP or SAVE |

`App.__init__` restores all three, so a restart lands on the page you left with
the tape, mixer and tempo intact (user story 18) — and since #297 it first
points the tape at the folder it was living in (`ProjectStore._boot_folder`:
the open project's, if the autosave names one that is still there).

**Who writes which (#1017 step 2, 2026-10-15):** `project.json` and the open
project's own document are `project_file.Document`'s (`app.doc`: the 2 s timer, the
§ 7.3 document clock, the boot, `commit`, `flush`; `Document.boot_folder` is the old
`_boot_folder`); `tape.json` and where the tape lives are `persist.ProjectStore`'s
(`app.project`).

**Superseded by #297 (below): STOP writes no audio at all**, so the rest of this
paragraph is history. **Dirty tracks only, and what says a track is dirty (#93, 2026-09-23).**
`save_audio` used to compare one signature over the whole tape, so a take on
one track re-wrote every track that had clips — ~0.9 s each on the CM4's eMMC
with the screen frozen for all of it (C6, #79), which is what `device-os-spec.md`
§ 7.3 and DDR-070 had asked it not to do since 2026-09-19. `ProjectStore` now
keeps, per track, the clip geometry as of that track's last write and the
`used_frames` the host reported, and writes a track only when its geometry
moved, its medium file is missing, or the save is forced. Three things worth
knowing about it:

- **`c.src` is in the signature**, which is what makes geometry a proxy for
  "the medium changed": a take always appends to the medium and always reads at
  a new offset. What it cannot see is medium *past* the last clip that reads it
  — an undone take — and that tail is unplayable by definition.
- **LOAD and NEW swap the audio under clips that need not move**, and the state
  directory still holds the previous project's `tape-N.wav`, so geometry alone
  would leave yesterday's sound on disk. `ProjectStore.forget_media()` drops
  every flag and **`project_file.resync` calls it** — the one door LOAD, NEW and
  `rewalk` share, and already where the `_native_sync` fingerprints are
  forgotten for the same reason. The old whole-tape signature had this hole too.
- **A refused `tape_save` leaves its track dirty**, so the next save retries it
  and the document keeps the frame count of the file that is actually there.

`tests/test_persist_dirty.py` counts `tape_save` per track, with the control the
standing rule asks for: the counter is also shown going *up* for the track that
moved. The write is still synchronous on the host's control thread — that is
C1's tape I/O thread (#74), not this.
