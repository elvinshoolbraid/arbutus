#!/bin/bash
# Run all emulator tests (unit + phase-1 UI trace).
#
#   ./run_tests.sh            unit tests only
#   ./run_tests.sh verify     unit tests, then every verify-arbutus feature
#                             script (tools/verify.py --all --diff)

set -e
cd "$(dirname "$0")"

PY=python3
[ -x venv/bin/python ] && PY=venv/bin/python

echo "Checking dependencies..."
$PY -c "import numpy, pygame, sounddevice" 2>/dev/null || {
    echo "ERROR: Missing dependencies. Run:"
    echo "  pip install -r requirements.txt"
    exit 1
}

echo "Running tests..."
$PY -m unittest discover -s tests -t . -v

echo ""
echo "All tests passed. Screenshots: validation/ui/phase{1,2a,2b,3}/"

if [ "$1" = "verify" ]; then
    echo ""
    echo "Running verification features (.claude/skills/verify-arbutus)..."
    $PY -c "import yaml" 2>/dev/null || {
        echo "ERROR: PyYAML missing. Run: venv/bin/pip install pyyaml"
        exit 1
    }
    $PY tools/verify.py --all --diff
    echo "Evidence: validation/ui/<feature>/"
fi
