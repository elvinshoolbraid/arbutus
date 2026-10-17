# Arbutus — the software

Arbutus is a pocket music instrument: a keyboard, synthesizer, sampler, sequencer and
four-track tape recorder in one battery-powered box, built by Arbutus Audio. This repository
is its software, and it is fully open source: the desktop emulator (the instrument's whole
screen and controls, played from your keyboard, mouse or a MIDI controller), the audio
engine that makes every sound, the SDK for writing your own sound engines, the stock engines
and the specs they are built to. The hardware's board layouts stay closed for now; the
licences are in [LICENSING.md](LICENSING.md).

## Run the emulator

You need Python 3.11 or newer, CMake 3.20+, Ninja and a C++20 compiler (Xcode's command line
tools on a Mac; `build-essential` on Debian or Ubuntu). Then, from a clone:

```bash
emulator/run.sh
```

The first run builds the audio engine (`audio/build/`) and the emulator's Python
environment (`emulator/venv/`), then opens the window; later runs start at once.
`emulator/run.sh --offline` runs with no audio device. `--phone`, which serves the window to
the companion app, needs the device runtime, which is not in this repository yet.

## Find your way

| Folder | What it is |
|---|---|
| `emulator/` | the UI: every page, key and screen of the instrument, drawn at 320×240. Start with `emulator/CLAUDE.md` (the folder's rules and layout) |
| `audio/` | the audio process (`audio/host/`, its control protocol in `PROTOCOL.md`), the stock engines (`audio/engines/`), the stock sounds (`audio/library/`) and the engine SDK (`audio/sdk/`, MIT-licensed) |
| `audio/sdk/template/` | a buildable engine and how to write one: the shortest way to add a sound |
| `docs/design/` | the specs: `v1-spec-package.md` says which document is the authority on what. A link to a document not here points into the private design repo. The specs cite their history: `#123` is a ticket, `D98` or `DDR-031` a design decision, `desk 49` or `R36` a question Elvin answered; those records are private, the decision itself is written where it is cited |

Tests: `cd emulator && venv/bin/python -m unittest discover -s tests` and
`ctest --test-dir audio/build`. Some fail here, and that is known: a few check files that
live only in the private repo (the phone app's lessons, the device runtime), and the drum-kit
tests expect sample kits this repository does not carry yet (their licence is unclear, so
they were left out; the DRUM page has the synthesized kits meanwhile).

## Contributing

Pull requests and feature suggestions are welcome: [CONTRIBUTING.md](CONTRIBUTING.md).
