#!/usr/bin/env python3
"""Compatibility launcher for the helper now maintained in src/vlad/legacy/."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "src"
            / "vlad"
            / "legacy"
            / Path(__file__).name
        ),
        run_name="__main__",
    )
