"""Headless equivalent of the study-settings command builder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .cohorts import save_cohort


# Shared by the CLI parser and command builder so every setting round-trips.
PATHS = ("registry", "global_dir", "reference_image", "subjects_file", "exports_dir", "input_dir", "population_mask")
STRINGS = {"name": None, "modality": "MRSI", "space": "mni", "extra_tags": "", "input_mode": "bids",
           "resolution": "", "signal_description": "signal", "qmask_mode": "thresholds"}
NUMBERS = {"crlb_max": 20., "fwhm_max": .1, "snr_min": 10.}
LISTS = ("metabolites", "masks", "dwi_files")
FLAGS = ("overwrite", "mask_space_confirmed")
REQUIRED = ("registry", "name", "global_dir", "reference_image", "subjects_file", "input_dir")


def settings_arguments(settings: dict) -> list[str]:
    """Serialize settings as literal arguments without reading/writing inputs."""
    args = []
    for key in (*PATHS, *STRINGS, *NUMBERS):
        value = settings.get(key)
        if value is not None:
            args += ["--" + key.replace("_", "-"), str(value)]
    for key in LISTS:
        if settings.get(key) is not None:
            args += ["--" + key.replace("_", "-"), json.dumps(settings[key])]
    for key in FLAGS:
        if settings.get(key):
            args.append("--" + key.replace("_", "-"))
    if not settings.get("compression", True):
        args.append("--uncompressed")
    if settings.get("reference_metadata"):
        args += ["--reference-metadata", json.dumps(settings["reference_metadata"], sort_keys=True)]
    return args


def _string_list(value: str) -> list[str]:
    try:
        result = json.loads(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a JSON list of strings.") from exc
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise argparse.ArgumentTypeError("Expected a JSON list of strings.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vlad configure", description=__doc__)
    for key in PATHS:
        parser.add_argument("--" + key.replace("_", "-"), type=Path, required=key in REQUIRED)
    for key, default in STRINGS.items():
        parser.add_argument("--" + key.replace("_", "-"), default=default, required=key in REQUIRED)
    for key, default in NUMBERS.items():
        parser.add_argument("--" + key.replace("_", "-"), default=default, type=float)
    for key in LISTS:
        parser.add_argument("--" + key.replace("_", "-"), type=_string_list, help="JSON list of strings")
    for key in FLAGS:
        parser.add_argument("--" + key.replace("_", "-"), action="store_true")
    parser.add_argument("--uncompressed", dest="compression", action="store_false")
    parser.add_argument("--reference-metadata", type=json.loads)
    settings = vars(parser.parse_args(argv))
    try:
        metadata = settings.get("reference_metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Reference metadata must be a JSON object.")
        saved = save_cohort(**settings)
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(saved.config_file)
    return 0
