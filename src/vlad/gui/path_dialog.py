"""Run a desktop path chooser outside Streamlit."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def _choose_mac_nifti(title, initial, multiple):
    # The macOS extension filter can hide compound extensions such as .nii.gz.
    # Open the system file dialog and let the caller validate the chosen NIfTI.
    script = '''function run(argv) {
    const app = Application.currentApplication();
    app.includeStandardAdditions = true;
    const selected = app.chooseFile({
        withPrompt: argv[0], defaultLocation: Path(argv[1]),
        multipleSelectionsAllowed: argv[2] === 'true'
    });
    const files = Array.isArray(selected) ? selected : [selected];
    return JSON.stringify(files.map(path => path.toString()));
}'''
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script,
                             title, str(initial), str(multiple).lower()],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        if '(-128)' in result.stderr:
            return []
        raise RuntimeError(result.stderr.strip() or 'The macOS file picker could not open.')
    return json.loads(result.stdout)


def choose_path(*, title: str, current: str = "", kind: str = "file",
                extensions: list[str] | None = None, multiple: bool = False,
                allow_new: bool = False, default_dir: str = "") -> list[str]:
    candidate = Path(current).expanduser() if current.strip() else Path.home()
    configured = Path(default_dir).expanduser() if default_dir.strip() else None
    initial = configured if configured is not None and configured.is_dir() else (
        candidate if candidate.is_dir() else candidate.parent
    )
    while not initial.is_dir() and initial != initial.parent:
        initial = initial.parent
    if sys.platform == 'darwin' and kind == 'file' and any(
            value.lower().lstrip('.') in {'nii', 'nii.gz'} for value in (extensions or ())):
        return _choose_mac_nifti(title, initial.absolute(), multiple)

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        # Parenting the dialog to a topmost root keeps it above the browser.
        root.attributes("-topmost", True)
        root.update()

        def raise_dialog(attempt=0):
            # On X11 the chooser is a separate toplevel; a withdrawn parent's
            # topmost flag alone is not enough to put it above the browser.
            for child in root.tk.splitlist(root.tk.call("winfo", "children", ".")):
                if root.tk.call("winfo", "toplevel", child) == child:
                    root.tk.call("wm", "attributes", child, "-topmost", True)
                    root.tk.call("raise", child)
                    root.tk.call("focus", "-force", child)
                    return
            if attempt < 20:
                root.after(100, raise_dialog, attempt + 1)

        root.after(100, raise_dialog)
        options = dict(parent=root, title=title, initialdir=str(initial))
        if kind == "directory":
            selected = filedialog.askdirectory(**options, mustexist=not allow_new)
        else:
            suffixes = ["*." + value.lstrip(".") for value in (extensions or [])]
            if suffixes:
                options["filetypes"] = [("Supported files", tuple(suffixes)), ("All files", "*")]
            if current.strip() and not candidate.is_dir():
                options["initialfile"] = candidate.name
            if kind == "savefile":
                # This chooses a path only; saving remains an explicit VLAD action.
                selected = filedialog.asksaveasfilename(**options, confirmoverwrite=False)
            elif multiple:
                selected = filedialog.askopenfilenames(**options)
            else:
                selected = filedialog.askopenfilename(**options)
        if not selected:
            return []
        values = selected if isinstance(selected, (tuple, list)) else [selected]
        return [str(Path(value).absolute()) for value in values]
    finally:
        root.destroy()


def main() -> int:
    try:
        paths = choose_path(**json.loads(sys.argv[1]))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps({"paths": paths}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
