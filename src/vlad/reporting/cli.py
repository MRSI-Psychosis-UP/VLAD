"""Command line interface for reports that require only Python."""

import argparse
from pathlib import Path
import sys

from .io import ReportError, ReportRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vlad report", description="Render Randomise or SwE reports in Python (HTML, Feather, SVG and statistical JSON).")
    parser.add_argument("--kind", choices=("randomise", "swe"), required=True)
    parser.add_argument("--results-dir", type=Path, default=Path.cwd())
    parser.add_argument("--analysis-name", required=True)
    parser.add_argument("--matrix-name", required=True)
    parser.add_argument("--permutations", type=int, default=500)
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--modality", default="mrsi")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirmation", action="store_true")
    parser.add_argument("--date", dest="report_date", help="Fixed YYYY-MM-DD date for reproducible exports")
    parser.add_argument("--subject-column")
    parser.add_argument("--visit-column")
    parser.add_argument("--design-columns", nargs="+", help="Exact exported column names in FSL design/contrast order")
    args = parser.parse_args(argv)
    try:
        request = ReportRequest(**vars(args))
        from .render import generate_report

        manifest = generate_report(request)
    except (ReportError, OSError, ImportError, ValueError) as exc:
        print(f"Report failed: {exc}", file=sys.stderr)
        return 1
    for name in ("html", "json", "feather"):
        print(f"{name.upper()}: {manifest['outputs'][name]}")
    for error in manifest["errors"]:
        print(f"Regional summary failed: {error}", file=sys.stderr)
    return int(bool(manifest["errors"]))
