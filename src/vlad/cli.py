"""Small, dependency-light interface to the established VLAD workflow."""

import argparse
import os
from pathlib import Path
import runpy
import subprocess
import sys

from . import __version__
from .paths import project_root

WORKFLOWS = {"run": "VLAD_beta7.sh", "prep": "Prep_VLAD_b6.1.sh"}
HELPERS = {
    "matrix-randomise": "create_matrix_randoMRSI_beta7",
    "matrix-swe": "create_matrix_VLADlov_swe_alpha2",
    "matrix-afni": "create_matrix_VLADlov_afni_alpha1",
    "resample-masks": "resample_masks_b3_replace",
    "snapshot": "snapshot_results_randomise_beta5",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vlad", description="VLAD neuroimaging workflow and development tools."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("run", "Run the existing Bash analysis workflow"),
        ("prep", "Prepare cohort images"),
        ("configure", "Save study settings from paths and matching options"),
        ("workflow", "Run a saved guided MRSI / Randomise plan"),
        ("matrix-randomise", "Build Randomise matrices (legacy arguments)"),
        ("matrix-swe", "Build SwE matrices (legacy arguments)"),
        ("matrix-afni", "Build AFNI inputs (legacy arguments)"),
        ("resample-masks", "Resample masks to a reference image"),
        ("snapshot", "Create a statistical-map snapshot (legacy arguments)"),
        ("report", "Generate Randomise or longitudinal reports in Python"),
    ):
        # Pass options to the underlying command, including its own --help.
        commands.add_parser(name, help=description, add_help=False)
    doctor_parser = commands.add_parser("doctor", help="Check installed dependencies")
    doctor_parser.add_argument(
        "--profile", choices=("python", "analysis", "prep", "reports", "full"), default="python"
    )
    gui_parser = commands.add_parser("gui", help="Open the local Streamlit browser interface")
    gui_parser.add_argument("--host", default="127.0.0.1", help="Listen address (default: this computer only)")
    gui_parser.add_argument("--port", type=int, default=2342, help="Browser port (default: 2342)")
    gui_parser.add_argument("--headless", "--no-browser", dest="headless", action="store_true",
                            help="Start the server without opening a browser")
    args, remaining = parser.parse_known_args(argv)
    if args.command == "gui":
        if remaining:
            parser.error("unrecognized arguments: " + " ".join(remaining))
        if not 1 <= args.port <= 65535:
            gui_parser.error("--port must be between 1 and 65535")
        if not args.host or any(character.isspace() for character in args.host):
            gui_parser.error("--host must be a listen address without whitespace")
        try:
            root = project_root()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        app = Path(__file__).resolve().parent / "gui" / "app.py"
        env = os.environ.copy()
        env["VLAD_HOME"] = str(root)
        if not env.get("VLAD_PYTHON_BIN") and not env.get("VLAD_PYTHON_ENV"):
            env["VLAD_PYTHON_BIN"] = sys.executable
        package_parent = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        # Keep launch noninteractive without changing the user's Streamlit files.
        env["STREAMLIT_SERVER_SHOW_EMAIL_PROMPT"] = "false"
        command = [
            sys.executable, "-m", "streamlit", "run", str(app),
            "--server.address", args.host,
            "--server.port", str(args.port),
            "--browser.gatherUsageStats=false",
            "--server.headless", str(args.headless).lower(),
            "--theme.base", "light", "--theme.primaryColor", "#087f8c",
        ]
        if args.host in ("127.0.0.1", "localhost", "::1"):
            command += ["--browser.serverAddress", args.host]
        try:
            return subprocess.call(command, env=env)
        except FileNotFoundError:
            print("The Python interpreter was not found. Re-run ./install.sh.", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
    if args.command == "workflow":
        from .workflow.runner import main as workflow_main

        return workflow_main(remaining)
    if args.command == "configure":
        from .gui.configure import main as configure_main

        return configure_main(remaining)
    if args.command == "report":
        from .reporting.cli import main as report_main

        return report_main(remaining)
    if args.command == "doctor":
        if remaining:
            parser.error("unrecognized arguments: " + " ".join(remaining))
        from .doctor import check_environment

        return check_environment(args.profile)
    if args.command in WORKFLOWS:
        try:
            root = project_root()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        env = os.environ.copy()
        env["VLAD_HOME"] = str(root)
        if not env.get("VLAD_PYTHON_BIN") and not env.get("VLAD_PYTHON_ENV"):
            env["VLAD_PYTHON_BIN"] = sys.executable
        try:
            return subprocess.call(
                ["bash", str(root / "workflows" / WORKFLOWS[args.command]), *remaining], env=env
            )
        except FileNotFoundError:
            print("Bash was not found. Install Bash 4 or newer.", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
    # Import scientific libraries only when a helper actually needs them.
    sys.argv = [args.command, *remaining]
    runpy.run_module("vlad.legacy." + HELPERS[args.command], run_name="__main__")
    return 0
