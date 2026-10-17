# op1r-src, vendored (#1230)

**What:** the files the OP-1 adapter boxes (`audio/engines/op1r/`) compile, copied **unmodified**
from op1r's source, the community firmware mod for the Teenage Engineering OP-1:
<https://github.com/pcppcp/op1r-src>, commit **`dbaa3f0bc89d77b00febc876645db41e56acfa84`**
(2026-10-15 22:45 +0200).

| File | What it is |
|---|---|
| `synth_sdk/op1_plugin.h` | op1r's plugin API: the `op1_fx_t` callback struct an effect fills |
| `synth_sdk/harness/op1_host.h` | op1r's own PC test harness header: replaces every OP-1 firmware address with a variable, so a plugin compiles off the OP-1 with `-DOP1_HOST` |
| `synth_sdk/plugins/reverb/reverb.c` | the `reverb` effect: op1r's fixed-point port of the Elements reverb |
| `synth_sdk/plugins/reverb/plugin.json` | its op1r registration (the knob defaults the box uses) |
| `LICENSE`, `NOTICE` | op1r's licence: GPL-3.0-or-later, with a § 7 permission for the OP-1 firmware |
| `synth_sdk/plugins/reverb/kalireverb.svg` | the reverb's screen: the **Kali** artwork, 320 × 160, as op1r's screen code shows it (#1255) |
| `synth_sdk/plugins/reverb/kali-reverb.svg`, `gen_svg.py` | the artwork's source and the script that makes `kalireverb.svg` and `kali_geom.h` from it |
| `synth_sdk/plugins/reverb/kali_geom.h` | the three needles' pivots, generated from the art; `reverb.c` includes it |
| `synth_sdk/display/reverb_preview.png` | op1r's picture of that screen at six knob settings: the reference the emulator's copy is checked against |
| `synth_sdk/harness/render.py` | op1r's own SVG drawer (cairo), which made that picture; `audio/engines/op1r/op1r-verb/screen/rasterise.py` draws the art with it |

**The reverb's art has no stated licence.** op1r's README credits the Kali artwork to DAN76
and says nothing of its terms (scout
`docs/research/scouts/idea-2026-10-15-op1-ecosystem/03-portability.md` § 4), so #1230 left
it out. Elvin, 2026-10-15, for the build he sends to the op1r modders, non-commercial: "it's
okay to run on the assumption that visual was vibe coded by someone who doesn't care that
much, I'm literally messaging it back to them so I'll find out if they don't like it" (#1255).
It is here on that call, and **held out of the public repo** (`tools/public-export/`'s NEVER
and `allow.txt`) until the art's author says yes: the two SVGs, the preview picture and the
emulator's rasterised copy (`audio/engines/op1r/op1r-verb/screen/kali.npz`).
`gen_svg.py`, `render.py` and `kali_geom.h` are op1r's code (GPL), not the pictures. No
Teenage Engineering code is here or anywhere in the box.

**Unmodified, checked:** `ctest -R op1r_vendored` compares each file with the sha256 in
`SHA256SUMS`. To move to a newer op1r: copy the same paths from a clone at the new commit,
update the commit above and `SHA256SUMS`, rebuild, and re-freeze the box's golden only if
the sound was meant to move.

**Licences.** op1r's own code is GPL-3.0-or-later (`LICENSE`, `NOTICE`); the boxes built
from it are GPL-3.0-or-later too and ship with their source (`LICENSING.md`). The reverb
algorithm is Mutable Instruments' Elements reverb, original code (c) 2014 Emilie Gillet,
under the MIT License, whose notice is kept here:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this
> software and associated documentation files (the "Software"), to deal in the Software
> without restriction, including without limitation the rights to use, copy, modify, merge,
> publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
> to whom the Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
> INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
> PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
> FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
> OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
> DEALINGS IN THE SOFTWARE.

## Session History

- [[logs/2026-10-16-06-05]] — #1255: OP1R-VERB's page is op1r's Kali reverb screen, rasterised once, 88-93 % pixel-identical to op1r's preview
