"""Persistent, machine-local settings shared by the browser interfaces."""

from __future__ import annotations

import json
from pathlib import Path

from .cohorts import atomic_text


SETTINGS_FILENAME = "settings.json"
POINTER_FILENAME = "settings-location.json"
THEMES = ("Light", "Dark")


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label.capitalize()} must contain a JSON object.")
    return value


def load_gui_settings(state_dir: Path) -> dict:
    bootstrap = Path(state_dir).expanduser().resolve()
    pointer = bootstrap / POINTER_FILENAME
    storage = bootstrap
    if pointer.exists():
        location = _read_object(pointer, "GUI settings location").get("settings_dir", "")
        if not isinstance(location, str) or not location.strip():
            raise ValueError("The saved GUI settings location is invalid.")
        storage = Path(location).expanduser().resolve()
    path = storage / SETTINGS_FILENAME
    if not path.exists():
        return {"bids_dir": "", "theme": "Light", "settings_dir": str(storage)}
    value = _read_object(path, "global GUI settings")
    bids_dir = value.get("bids_dir", "")
    if bids_dir and not isinstance(bids_dir, str):
        raise ValueError("The saved BIDS directory is invalid.")
    theme = value.get("theme", "Light")
    if theme not in THEMES:
        raise ValueError("The saved GUI theme must be Light or Dark.")
    configured_storage = value.get("settings_dir", str(storage))
    if not isinstance(configured_storage, str) or not configured_storage.strip():
        raise ValueError("The saved GUI settings folder is invalid.")
    return {"bids_dir": bids_dir, "theme": theme,
            "settings_dir": str(Path(configured_storage).expanduser().resolve())}


def save_gui_settings(state_dir: Path, *, bids_dir: str, theme: str = "Light",
                      settings_dir: str = "") -> dict:
    bootstrap = Path(state_dir).expanduser().resolve()
    directory = Path(bids_dir).expanduser().resolve() if bids_dir.strip() else None
    if directory is not None and not directory.is_dir():
        raise ValueError("Choose an existing main BIDS directory.")
    if theme not in THEMES:
        raise ValueError("Choose the Light or Dark theme.")
    storage = Path(settings_dir).expanduser().resolve() if settings_dir.strip() else bootstrap
    storage.mkdir(parents=True, exist_ok=True)
    bootstrap.mkdir(parents=True, exist_ok=True)
    settings = {"bids_dir": str(directory) if directory is not None else "",
                "theme": theme, "settings_dir": str(storage)}
    atomic_text(storage / SETTINGS_FILENAME, json.dumps(settings, indent=2) + "\n")
    atomic_text(bootstrap / POINTER_FILENAME,
                json.dumps({"settings_dir": str(storage)}, indent=2) + "\n")
    return settings
