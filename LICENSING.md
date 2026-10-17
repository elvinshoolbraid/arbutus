# Licensing — which licence covers which folder

The licences were chosen by Elfy in grill Q12 (2026-09-29); the table and the reasons are
`docs/strategy/strategy.md` § 10 (*Open hardware and software … — which?*). The repository
is private until about preorders (M7, 2026-10-15: the software opens sooner, as a clean public repo exported from this one by `tools/public-export/export.py`, whose `allow.txt` is what crosses, #1232), so this file settles what applies **when it opens**; the
licence names are marked provisional there, and a change is made here and in § 10 together.

**The rule:** a file is under the licence of the nearest `LICENSE` file above it. The
top-level `LICENSE` is the GNU GPL v3, applied as **GPL-3.0-or-later**; the folders below
that differ carry their own `LICENSE`. Third-party files keep their own licence (the last
section) whatever folder they sit in.

| Folder | Licence | `LICENSE` file |
|---|---|---|
| `arbutus_os/` (the device runtime and OS), `firmware/` (the ATmega key scanner), `app/` (the companion app), `emulator/`, `audio/` (the audio engine host, the stock engines, their tests and tools), `tools/`, `validation/`, `archive/` | **GPL-3.0-or-later** | `LICENSE` |
| `audio/sdk/` — the engine SDK: `arbutus_engine.h`, the helper headers, `arb_manifest` and the engine template | **MIT** | `audio/sdk/LICENSE` |
| `hardware/` — the schematics (the KiCad sheets and the SKiDL Python that generates P3's), the 3D-print files and their generators | **CERN-OHL-S-2.0** | `hardware/LICENSE` |
| `docs/` — specs, designs, logs, repair guides, the manual | **CC BY-SA 4.0** | `docs/LICENSE` |
| The name *Arbutus* and the logo | **not licensed** — a trademark | — |

**Not covered by any open licence — the board layouts.** The strategy publishes schematics
and printable files "Framework's way", **not the full board layouts**. So the layouts are
excluded from `hardware/LICENSE`: every `*.kicad_pcb` file and `hardware/layout/` (the
placement and routing scripts and their output). They are all rights reserved until Elfy
decides otherwise, and are to be left out of the public repository when it opens.

**Not yet in the repo:** the IO board's modular stub reference design
(**CERN-OHL-P-2.0**) and the USB data-stream SDKs (**MIT**). Each gets its own folder and
`LICENSE` file when it is written.

## Third-party files

Checked 2026-10-03 (#238). **Compatible** — each keeps its own licence and may sit in the
folder it is in:

| Path | What | Licence |
|---|---|---|
| `audio/third_party/miniaudio/` | miniaudio, the audio I/O header, verbatim | public domain or MIT-0 (its choice) |
| `audio/third_party/nlohmann/` | nlohmann/json, verbatim | MIT |
| `audio/third_party/op1r-src/` | op1r's plugin API, PC harness header and reverb, unmodified at a pinned commit (`VENDORED.md`, #1230); built into the OP-1 adapter boxes, `audio/engines/op1r/` | **GPL-3.0-or-later** with op1r's § 7 OP-1 firmware permission (`LICENSE`, `NOTICE` beside it), the same licence as `audio/`; the boxes ship with their source. The reverb's algorithm is the Elements reverb, (c) 2014 Emilie Gillet, MIT, its notice kept in `VENDORED.md`. op1r's reverb artwork (the **Kali** art, credited to DAN76) is copied **with no licence stated**, by Elvin's call of 2026-10-15 for his non-commercial outreach build to the op1r modders (#1255): the two SVGs, `display/reverb_preview.png` and the emulator's rasterised copy `audio/engines/op1r/op1r-verb/screen/kali.npz` are op1r/DAN76's, not ours, and are held out of the public export until the author says yes |
| `hardware/tools/kicad-netlist-tool/` | the netlist parser, vendored (`VENDORED.md`) | MIT |
| `app/ios/Vendor/lame/` | LAME 3.100's MP3 encoder library, unmodified, statically linked into the app (`VENDORED.md`, #274) | **LGPL-2.0-or-later** (`COPYING` beside it); the LGPL allows a GPL-3.0 program to use it. Owed when the app ships: an in-app notice that it uses LAME, with a link (`VENDORED.md`) |
| `app/ios/Arbutus/Fonts/Rubik.ttf` | the app's typeface | SIL OFL 1.1 (`OFL.txt` beside it) |
| `arbutus_os/kmod/wm8904/` | the Linux WM8904 codec driver, copied unchanged from `raspberrypi/linux` (README names the commit), and our patch to it (#782) | **GPL-2.0-only** (`COPYING` beside it), the patch included. A kernel module is built and loaded on its own, not linked into GPL-3 code, so the two do not mix |
| `firmware/atmega/build/*with_bootloader*` | build output that embeds the Arduino Leonardo bootloader (Caterina, built on LUFA) | LUFA's permissive licence; the bootloader is a separate program on the chip |
| KiCad standard-library footprints copied into `hardware/kicad/*.pretty` (for example `LED_0805_2012Metric`) | KiCad's libraries | CC BY-SA 4.0 with KiCad's exception for designs that use them |

**Conflicts or unknown — to settle before the repo opens:**

| Path | What | The problem |
|---|---|---|
| `audio/library/drum/drum/` TR_707, TR_909, South_808, lofi_2 | the four stock drum kits, sliced from OP-1 patches downloaded from op1.fun (`audio/library/README.md` § Kits) | **Not cleared** (#1239, 2026-10-16): each is byte-for-byte an op1.fun upload (TR_707 and South_808 by `snugsworth`, TR_909 by `stone4th`, lofi_2 by `Grumpysnorlax`), and op1.fun states no licence. TR_707 is Roland's ROM samples by the uploader's own description; the others' sources are unstated. Replace (a CC0/CC BY shortlist is in #1239's report; Elvin picks, #1298), or Elvin asks lofi_2's uploader |
| `audio/library/drum/drum/bark/` | BARK, the stock drum kit that is ours: twelve sounds synthesized from sines, squares and seeded noise by `audio/tools/mkstockkit.py` (#1295) | **Ours**, like `bottle.wav`; the public export carries it |
| `audio/library/synth/sampler/*.wav` except `bottle.wav` | the eight stock sampler sounds (committed 2026-09-11) | **Not cleared** (#1239, 2026-10-16): all eight are `snugsworth`'s op1.fun uploads. Six are recordings of a Yamaha PortaTone keyboard's built-in voices (the uploader's pack text), `alicekeys` is "piano sample from native instruments vst" (NI's licence forbids passing samples on), `adams_piano` a piano the uploader recorded. Replace (#1298), or Elvin asks about `adams_piano`. `bottle.wav` was synthesized by the emulator's `tools/make_bottle.py` (deleted in stage N1) and is ours |
| `hardware/kicad/CM4IO.pretty`, `CM4IO.kicad_sym` | footprints and symbols from Raspberry Pi's own CM4 IO board design | **Not cleared to redistribute** (checked 2026-10-16, #1239): the KiCad download carries no licence and every sheet says "© 2020-2022 Raspberry Pi Ltd"; Raspberry Pi's resource terms allow use "solely in conjunction with the Raspberry Pi products" and grant no other licence. Redraw, or ask Raspberry Pi, before `hardware/` opens |
| `hardware/kicad/Easy2Kicad_projects.*`, `P3_parts.*` (EasyEDA-derived parts), `ProPrj_Bea-easyedapro.pretty` | footprints, symbols and 3D models converted from LCSC/EasyEDA's library by `easyeda2kicad` | **Not cleared to redistribute** (#1239): EasyEDA's legal terms (effective 2026-09-21, § VII) say the site's content and files are EasyEDA's or its licensors' and "you may not … copy, distribute … any information or work contained on the Services". Redraw from the datasheets, or use KiCad's library, before `hardware/` opens |
| `hardware/mechanical/component models/` | vendor STEP models (CM4, DF40C, EC11J, Kailh Choc) | each vendor's terms, no redistribution grant found (#1239): `CM4.step` Raspberry Pi's (the terms above), DF40C Hirose's, RK10J12 and EC11J Alps's (EC11J through TraceParts). The two Kailh Choc models are probably from `perigoso/keyswitch-kicad-library` (MIT and CC BY-SA 4.0), not matched file for file. Link to the vendors' pages instead |
| `docs/datasheets/` | vendor datasheet PDFs | the vendors' copyright, not CC BY-SA; link to them instead when the repo opens. Raspberry Pi's own are CC BY-ND 4.0 (their stated licence, #1239): those may be shipped unchanged, with credit |

`audio/library/synth/wavetable/*.wav` are generated by `audio/tools/mkwavetable.py` and are
ours. A dependency installed by a package manager (the emulator's `pygame`, `numpy`; the
app's system frameworks) is not in the repo and is not listed.

## Session History

- [[logs/2026-10-16-01-05]] — tools/public-export/ writes the public software repo (fresh history); the gate checks nothing private crosses
- [[logs/2026-10-16-03-37]] — Stock sounds: all 12 are op1.fun uploads, none cleared (10 are Roland/Yamaha/NI underneath); a CC0/CC BY shortlist
- [[logs/2026-10-16-08-50]] — BARK, our own synthesized drum kit; the public export's DRUM plays and its emulator suite runs 1033 OK
