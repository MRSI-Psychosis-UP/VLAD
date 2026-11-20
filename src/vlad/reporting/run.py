#!/usr/bin/env python3
"""Direct-file entry point for source checkouts and alternate Python environments."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vlad.reporting.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
