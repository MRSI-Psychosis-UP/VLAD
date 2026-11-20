"""Runtime checks without changing the user's system or analysis files."""

import importlib
import importlib.metadata
import os
import shutil
import subprocess
import sys

PYTHON_PACKAGES = ("numpy", "pandas", "scipy", "matplotlib", "seaborn", "nibabel", "nilearn", "openpyxl", "pyarrow", "streamlit", "templateflow")
FSL_COMMANDS = ("fslmaths", "fslmerge", "fslsplit", "fslstats", "fslval", "fslnvols", "fslmeants", "flirt", "Text2Vest")


def check_environment(profile: str = "python") -> int:
    failures = 0

    def report(ok: bool, description: str) -> None:
        nonlocal failures
        print(f"{'OK' if ok else 'MISSING'}  {description}")
        failures += not ok

    report((3, 10) <= sys.version_info[:2] < (3, 13), f"Python 3.10–3.12 ({sys.version.split()[0]})")
    if profile in ("python", "analysis", "prep", "reports", "full"):
        for name in PYTHON_PACKAGES:
            try:
                # TemplateFlow's module import initializes its global cache. A
                # dependency check must not populate or download template data.
                if name != "templateflow":
                    importlib.import_module(name)
                report(True, f"{name} {importlib.metadata.version(name)}")
            except Exception as exc:
                report(False, f"{name}: {exc}")
    if profile in ("analysis", "prep", "full"):
        bash = shutil.which("bash")
        result = subprocess.run([bash, "-c", "(( BASH_VERSINFO[0] >= 4 ))"], check=False) if bash else None
        report(result is not None and result.returncode == 0, "Bash >= 4")
        commands = FSL_COMMANDS + ("bc",) if profile != "prep" else ("fslmaths", "fslstats", "fslval")
        for name in commands:
            report(shutil.which(name) is not None, name)
        if profile in ("analysis", "full"):
            report(any(shutil.which(name) for name in ("randomise", "randomise_parallel")), "randomise or randomise_parallel")
            if profile == "full":
                report(shutil.which("swe") is not None, "swe (longitudinal SwE)")
            else:
                print("INFO  Longitudinal SwE requires swe.")
            print("INFO  The optional AFNI helper requires a separate 3dLME installation.")
        try:
            import tkinter

            tkinter.Tcl()
            report(True, "Tk support for matrix file dialogs")
        except Exception as exc:
            report(False, f"Tk support: {exc}")
        if profile in ("prep", "full"):
            report(shutil.which("zenity") is not None, "zenity")
        if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            print("INFO  No GUI display detected; interactive file dialogs need a desktop session.")
    print(f"\n{failures} required check(s) failed for profile '{profile}'.")
    return int(failures > 0)
