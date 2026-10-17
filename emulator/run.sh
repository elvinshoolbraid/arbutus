#!/usr/bin/env bash
# Run the emulator without juggling the venv. Works from any directory.
#
#   ./run.sh                 # the everyday window (the audio process, live)
#   ./run.sh --edit          # + the live palette/font editor in the browser
#   ./run.sh --fresh --edit  # any main.py flag is passed straight through
#   ./run.sh --offline       # no audio device; `render` is the clock
#   ./run.sh --phone         # the window as the mock device's UI for the iPhone
#                            # app: starts the mock, prints its address + token
#                            # (--phone [--root DIR] [--offline] [-- main.py flags])
#
# `audio/build/host/arbutus-audio` and `venv/` are made on first run if missing.
set -euo pipefail

CALLER=$PWD                 # --phone's relative --root is the caller's (#629)
cd "$(dirname "$0")"

# The audio process is the audio path (stage N1), so a checkout with no build
# gets one rather than a refusal. Everything else about the run is unchanged.
HOST=../audio/build/host/arbutus-audio
if [ ! -x "$HOST" ]; then
    echo "building the audio process (first run) ..." >&2
    if ! (cmake -S ../audio -B ../audio/build -G Ninja >/dev/null &&
          cmake --build ../audio/build >/dev/null); then
        echo "could not build $HOST — build it by hand:" >&2
        echo "  cmake -S audio -B audio/build -G Ninja && cmake --build audio/build" >&2
        exit 1
    fi
fi

# Likewise the venv: a fresh clone (the public repo's one command, #1232) gets one.
if [ ! -x venv/bin/python ]; then
    echo "making the Python environment (first run) ..." >&2
    if ! (python3 -m venv venv && venv/bin/pip -q install -r requirements.txt); then
        rm -rf venv     # half-made, it would pass the check above next time
        echo "could not make emulator/venv — make it by hand:" >&2
        echo "  python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
        exit 1
    fi
fi

if [ "${1:-}" = "--phone" ]; then
    shift
    # `--` separates this launcher's flags from emulator/main.py's.
    HERE=$PWD
    cd "$CALLER"
    exec "$HERE/venv/bin/python" "$HERE/../tools/emu_phone.py" "$@"
fi

# --width 1800 is the size that fits a 1x framebuffer in the photo's LCD
# cutout (emulator/notes/chrome-and-input.md § Chrome). Passing --width again overrides it.
exec venv/bin/python main.py --width 1800 "$@"
