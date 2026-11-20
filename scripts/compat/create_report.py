#!/usr/bin/env python3
"""Compatibility entry point for the Python replacement of the R reports."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from vlad.reporting.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
