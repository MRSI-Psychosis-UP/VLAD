"""Locate source-checkout or wheel resources without depending on cwd."""

import os
from pathlib import Path


def project_root() -> Path:
    configured = os.environ.get("VLAD_HOME")
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [Path(__file__).resolve().parents[2], Path(__file__).parent / "resources"]
    )
    for candidate in candidates:
        if (candidate / "workflows" / "common.sh").is_file():
            return candidate.resolve()
    raise RuntimeError("VLAD resources were not found. Reinstall VLAD or set VLAD_HOME to its checkout.")
