#!/usr/bin/env bash
# Development launcher: run the GUI straight from the source tree.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python "$ROOT/scripts/run_gui.py" "$@"
