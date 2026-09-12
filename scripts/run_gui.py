#!/usr/bin/env python3
"""Development helper: run the GUI without installing the package."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aindiff.cli import main

raise SystemExit(main())
