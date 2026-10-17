# The OP-1 adapter boxes (#1230)

**op1r** (<https://github.com/pcppcp/op1r-src>) is pcppcp's community firmware for the
Teenage Engineering OP-1; it adds synth engines and effects that the OP-1 scene writes as
small C files. This folder runs those C files **unmodified** as Arbutus boxes, "as an example
of interoperability" (Elvin, the M7 grill, 2026-10-15). Effects only, so far; a synth would
need a voice wrapper (scout `docs/research/scouts/idea-2026-10-15-op1-ecosystem/03-portability.md` § 3a).

| Path | What |
|---|---|
| `audio/third_party/op1r-src/` | op1r's files, copied byte for byte from a pinned commit (`VENDORED.md`; `ctest -R op1r_vendored` checks) |
| `adapter/op1r_fx.hpp` | the adapter: an op1r `op1_fx_t` on the outside of `arbutus_engine.h`. Its header comment has every mapping and why |
| `adapter/op1r_host.c` | the variables op1r's own PC harness (`harness/op1_host.h`) wants in place of the OP-1's firmware addresses |
| `op1r-verb/` | the first box: op1r's reverb; `screen/` is its OP-1 screen for the emulator (#1255) |

**Licence:** everything here is GPL-3.0-or-later and ships with its source, because op1r's
code is (`LICENSING.md`). Never add Teenage Engineering code; only op1r's GPL source, and only
a plugin whose licence is stated.

## Adding a second op1r effect

1. Copy its files from op1r-src at the pinned commit into `audio/third_party/op1r-src/` at the
   same paths, and add their hashes to `SHA256SUMS` (`sha256sum <paths> >> SHA256SUMS`).
   A newer commit moves every box: see `VENDORED.md`.
2. `cp -r op1r-verb <name>`, then in it:
   - `src/plugin.c` includes the plugin's `.c` (a pragma only if `-Wextra` names something);
   - `src/engine.cpp` names its `op1_fx_t` symbol (`plugin.json`'s `symbol`), the box id
     `op1r/<name>`, and its knobs: names from the plugin's comments, defaults from
     `plugin.json`'s `defaults` (÷ 32767). Knobs 0–3 are the four knobs; params 4–6, if any,
     are op1r's page-2 words 5–7;
   - a header the plugin includes and op1r keeps beside it is vendored with it, never stubbed;
   - `engine.json`, `README.md`, `CMakeLists.txt` (the names), and a golden.
3. `add_subdirectory(<name>)` in this folder's `CMakeLists.txt`.

A box name must differ from every stock box's (`arbutus/<name>`): the emulator's FX list is
by name. Hence `op1r-verb`, not `reverb`.
