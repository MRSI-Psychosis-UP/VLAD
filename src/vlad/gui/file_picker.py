"""Editable paths with desktop Browse buttons; no uploads or file copies."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Iterable

import streamlit as st


def choose_native_paths(**options) -> list[str]:
    """Tk must run in a separate process, never Streamlit's script thread."""
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("path_dialog.py")), json.dumps(options)],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The chooser timed out. Click Browse to open it again.") from exc
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("Could not open the desktop chooser. " + result.stderr.strip()[-500:]) from exc
    if result.returncode or response.get("error"):
        raise RuntimeError("Could not open the desktop chooser: " + response.get("error", "unknown error"))
    return response["paths"]


def _browse(key: str, label: str, kind: str, extensions: tuple[str, ...],
            allow_new: bool, multiple: bool = False) -> None:
    """Widget callback: update the path before Streamlit renders the field."""
    error_key = "browse_error:" + key
    st.session_state.pop(error_key, None)
    current = str(st.session_state.get(key, ""))
    if multiple:
        current = next((line for line in current.splitlines() if line.strip()), "")
    try:
        with st.spinner("Choose a path in the desktop window…"):
            paths = choose_native_paths(title="VLAD · " + label, current=current, kind=kind,
                                        extensions=list(extensions), allow_new=allow_new, multiple=multiple,
                                        default_dir=str(st.session_state.get("global_bids_dir", "")))
        if paths:
            if multiple:
                existing = [line.strip() for line in str(st.session_state.get(key, "")).splitlines() if line.strip()]
                st.session_state[key] = "\n".join(dict.fromkeys([*existing, *paths]))
            else:
                st.session_state[key] = paths[0]
    except (OSError, RuntimeError) as exc:
        st.session_state[error_key] = (str(exc) + " The chooser needs a desktop session on the machine running VLAD. "
                                    "You can still enter or paste the path below.")


def _button(key, label, kind, extensions, allow_new, disabled, multiple=False):
    st.button("Browse…", key="browse:" + key, disabled=disabled,
              help="Choose a path on the computer running VLAD. No file is uploaded.",
              on_click=_browse, args=(key, label, kind, tuple(extensions or ()), allow_new, multiple))


def path_input(label: str, *, key: str, value: str = "", kind: str = "file",
               extensions: Iterable[str] | None = None, allow_new: bool = False,
               placeholder: str | None = None, help: str | None = None,
               disabled: bool = False, inline: bool = True) -> str:
    """Keep the original path on the machine running VLAD."""
    if kind not in {"file", "directory", "savefile"}:
        raise ValueError("Choose a file or directory path.")
    if key not in st.session_state:
        st.session_state[key] = value
    if error := st.session_state.get("browse_error:" + key):
        st.warning(error)
    if inline:
        field, browse = st.columns([5, 1], vertical_alignment="bottom")
        with field:
            result = st.text_input(label, key=key, placeholder=placeholder, help=help, disabled=disabled)
        with browse:
            _button(key, label, kind, extensions, allow_new, disabled)
    else:
        result = st.text_input(label, key=key, placeholder=placeholder, help=help, disabled=disabled)
        _button(key, label, kind, extensions, allow_new, disabled)
    return result


def path_list_input(label: str, *, key: str, extensions: Iterable[str] | None = None,
                    placeholder: str = "", help: str | None = None, height: int = 90,
                    disabled: bool = False) -> str:
    """Select one or more masks, retaining manual paths without copying files."""
    if key not in st.session_state:
        st.session_state[key] = ""
    if error := st.session_state.get("browse_error:" + key):
        st.warning(error)
    result = st.text_area(label, key=key, placeholder=placeholder, help=help, height=height, disabled=disabled)
    _button(key, label, "file", extensions, False, disabled, multiple=True)
    return result
