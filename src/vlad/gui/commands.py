"""Validated, literal argv builders shared by the browser and tests."""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys

from .cohorts import Cohort, IDENTIFIER, MODALITIES, read_assignments
from .subjects import read_subject_visits


MASK_PRESETS = {
    "default": ("white-grey-matter",),
    "wmgm": ("white-grey-matter", "white-matter", "grey-matter"),
    "wmgmbg": ("white-grey-matter", "white-matter", "cortex", "subcortical-nuclei"),
    "onlygrey": ("grey-matter",), "onlywhite": ("white-matter",),
    "onlygreydetail": ("grey-matter", "cortex", "subcortical-nuclei"),
    "cerebellum": ("white-grey-matter-cerebellum",), "population": (),
}


@dataclass
class AnalysisOptions:
    cohort: Cohort
    analysis_name: str
    matrix_name: str
    modality: str = "MRSI"
    engine: str = "randomise"
    mode: str = "run"
    permutations: int = 500
    mask: str = "default"
    quality_threshold: int = 68
    metabolites: list[str] | None = None
    include_sums: bool = False
    quotients: list[str] | None = None
    b0: str = "3"
    smooth: float | None = None
    logarithm: bool = False
    parallel: bool = False
    overwrite_outputs: bool = False


def validate_analysis(options: AnalysisOptions) -> list[str]:
    errors = []
    for label, value in (("Study", options.cohort.name), ("Analysis", options.analysis_name), ("Model", options.matrix_name)):
        if not IDENTIFIER.fullmatch(value):
            errors.append(f"{label} names must use letters, numbers and dashes (no spaces or underscores).")
    raw_root = str(options.cohort.values.get("GLOBAL_DIR", options.cohort.global_dir))
    if not Path(raw_root).is_absolute():
        errors.append("Set an absolute GLOBAL_DIR in the cohort configuration before running through the browser.")
    if any(char.isspace() for char in str(options.cohort.global_dir) + raw_root):
        errors.append("The legacy analysis engine needs a study directory without whitespace. Reports and design editing support spaces, but move/link analysis data to a whitespace-free study path before running VLAD.")
    if options.modality.upper() not in MODALITIES:
        errors.append("Choose MRSI, DTI or STRUCTURAL.")
    elif options.modality.upper() not in options.cohort.modalities:
        errors.append("Configure this modality in Study setup first.")
    elif not options.cohort.values.get(f"{options.modality.upper()}_FILES_VALIDATED"):
        errors.append("Prepare this modality's cohort data in Study setup before running an analysis.")
    if options.engine not in ("randomise", "swe") or options.mode not in ("run", "prepare", "analyze"):
        errors.append("Choose a supported engine and run mode.")
    if not isinstance(options.permutations, int) or options.permutations < 1:
        errors.append("Permutation count must be a positive integer.")
    if not 1 <= options.quality_threshold <= 99:
        errors.append("Quality coverage must be between 1 and 99 percent.")
    if options.mask not in MASK_PRESETS:
        errors.append("Choose a supported tissue-mask preset.")
    if options.mask == "population" and options.modality.upper() != "MRSI":
        errors.append("Quality population masking is available for MRSI; choose a tissue-mask preset for DWI or structural maps.")
    if options.b0 not in ("3", "7", "7as3"):
        errors.append("Choose a supported field-strength preset.")
    if options.smooth is not None and (not math.isfinite(options.smooth) or options.smooth <= 0):
        errors.append("Smoothing sigma must be positive and finite.")
    for token in options.metabolites or []:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", token):
            errors.append(f"Invalid metabolite label: {token!r}.")
    for token in options.quotients or []:
        if not re.fullmatch(r"[A-Za-z0-9*?][A-Za-z0-9_.+*?\[\]-]*", token):
            errors.append(f"Invalid quotient selector: {token!r}.")
    if not options.cohort.config_file.is_file():
        errors.append("The study's configuration file is missing.")
    population = options.cohort.global_dir / "Listes" / f"List_{options.analysis_name}.txt"
    results = options.cohort.global_dir / "Results" / options.analysis_name
    needed = [population, results / f"{options.matrix_name}.mat", results / f"{options.matrix_name}.con"]
    if options.engine == "swe":
        needed.append(results / f"{options.matrix_name}.sub")
    for path in needed:
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"Create/save the design and subject list first: missing {path.name}.")
    prefix = options.modality.upper()
    if options.cohort.values.get(f"{prefix}_INPUT_MODE") == "bids":
        manifest_path = Path(options.cohort.values.get(f"{prefix}_MANIFEST") or
                             options.cohort.global_dir / "prepared" / prefix.lower() / "manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text())
            if population.is_file():
                unavailable = set(read_subject_visits(population)) - set(manifest["subjects"])
                if unavailable:
                    errors.append(f"The design includes {len(unavailable)} visits without prepared images. Filter them out or update and prepare the study population first.")
            if prefix == "MRSI" and options.metabolites:
                unavailable_maps = set(options.metabolites) - set(manifest["series"])
                if unavailable_maps:
                    errors.append("Prepare the selected metabolites first: " + ", ".join(sorted(unavailable_maps)) + ".")
        except (OSError, ValueError, KeyError, TypeError):
            errors.append("Prepare the BIDS derivative inputs first; the prepared-input manifest is missing or invalid.")
        if options.include_sums or options.quotients:
            errors.append("For BIDS inputs, prepare metabolite sums/ratios upstream and select their met- labels. Legacy derived-map options require legacy preparation.")
        if prefix == "MRSI" and options.smooth is not None:
            errors.append("Leave MRSI smoothing disabled for BIDS-prepared maps; prepare any smoothed signal maps upstream and select their desc- label.")
        if prefix == "MRSI" and options.b0 == "7as3":
            required = {"NAANAAG", "CrPCr", "Ins", "GPCPCh", "GluGln"}
            if not required.issubset(set(options.cohort.values.get("METAB", []))):
                errors.append("The 7 T as 3 T preset needs prepared NAANAAG, CrPCr, Ins, GPCPCh and GluGln maps; choose the original field-strength mode for other metabolites.")
        if options.mode == "run" and options.mask in MASK_PRESETS and options.mask != "population":
            mask_dir = Path(options.cohort.values.get(f"{prefix}_MASK_DIR") or
                            manifest_path.parent / "masks")
            missing_masks = [f"HarvardOxford-{label}.nii.gz" for label in MASK_PRESETS[options.mask]
                             if not (mask_dir / f"HarvardOxford-{label}.nii.gz").is_file()]
            if missing_masks:
                advice = " Select quality population masking to use the MRSI mask without a tissue restriction." if prefix == "MRSI" else ""
                errors.append("Prepare tissue masks in the selected image space for this preset: " + ", ".join(missing_masks) + "." + advice)
    if options.mode == "run" and not options.overwrite_outputs and results.exists():
        if any(path.name.startswith(("rdm_", "meants__")) or path.is_dir() for path in results.iterdir()):
            errors.append("This analysis already has outputs. Select the explicit rerun/overwrite option or use a new analysis name.")
    return list(dict.fromkeys(errors))


def _vlad() -> list[str]:
    return [sys.executable, "-m", "vlad"]


def settings_command(settings: dict) -> list[str]:
    from .configure import settings_arguments

    return [*_vlad(), "configure", *settings_arguments(settings)]


def command_text(command: list[str], root: Path, registry: Path) -> str:
    """A copyable command uses the same workspace and registry as GUI execution."""
    return shlex.join(["env", f"VLAD_HOME={root}", f"VLAD_CONFIG_FILE={registry}", *command])


def analysis_command(root: Path, options: AnalysisOptions, *, validate: bool = True) -> list[str]:
    errors = validate_analysis(options) if validate else []
    if errors:
        raise ValueError("\n".join(errors))
    args = [*_vlad(), "run", "-c", options.cohort.name, "-t", options.modality.upper(),
            "-n", options.analysis_name, "-m", options.matrix_name, "-p", str(options.permutations),
            "--qualithresh", str(options.quality_threshold), "--b0", options.b0, "--non-interactive"]
    if options.mode != "run":
        args.append("--prepare" if options.mode == "prepare" else "--analyze")
    if options.engine == "swe":
        args += ["--longitudinal", "swe"]
    if options.mask != "default":
        args += ["--mask", options.mask]
    if options.metabolites:
        args += ["--metabs", *options.metabolites]
    if options.include_sums:
        args.append("--sum")
    if options.quotients:
        args += ["--quotients", *options.quotients]
    if options.smooth is not None:
        args += ["--smooth", str(options.smooth)]
    if options.logarithm:
        args.append("--logarithm")
    if not options.parallel:
        args.append("--noparallel")
    if (options.cohort.global_dir / "Results" / options.analysis_name / f"{options.matrix_name}.fts").is_file():
        args.append("--anova")
    return args


def report_command(root: Path, cohort: Cohort, analysis: str, matrix: str, kind: str = "randomise",
                   modality: str = "mrsi", permutations: int = 500) -> list[str]:
    if not IDENTIFIER.fullmatch(analysis) or not IDENTIFIER.fullmatch(matrix) or kind not in ("randomise", "swe"):
        raise ValueError("Choose valid analysis/model names and a supported report kind.")
    return [*_vlad(), "report", "--kind", kind, "--results-dir", str(cohort.global_dir / "Results" / analysis),
            "--analysis-name", analysis, "--matrix-name", matrix, "--modality", modality.lower(),
            "--permutations", str(permutations), "--export-dir", str(cohort.values.get("DIR_RANALYSES", cohort.global_dir / "Reports"))]


def preparation_command(root: Path, cohort: Cohort, modality: str = "MRSI", b0: str = "3", overwrite: bool = False,
                        subjects_file: Path | None = None) -> list[str]:
    if modality.upper() not in cohort.modalities or b0 not in ("3", "7"):
        raise ValueError("Save settings for the selected modality and choose 3T or 7T preparation.")
    args = [*_vlad(), "prep", "--config", str(cohort.config_file), "--modality", modality.upper(), "--non-interactive", "--b0", b0]
    if overwrite:
        args.append("--overwrite")
    if subjects_file is not None:
        if cohort.values.get(f"{modality.upper()}_INPUT_MODE") != "bids":
            raise ValueError("Selected-population preparation requires BIDS derivative discovery.")
        selected = Path(subjects_file).expanduser().resolve()
        read_subject_visits(selected)
        args += ["--subjects", str(selected)]
    return args


def runtime_environment(root: Path, registry: Path) -> dict:
    env = os.environ.copy()
    env.update(VLAD_HOME=str(root), VLAD_CONFIG_FILE=str(Path(registry).expanduser().resolve()),
               VLAD_PYTHON_BIN=sys.executable, PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[2]), env.get("PYTHONPATH"))))
    state = Path(root) / ".local/runtime.env"
    if state.exists():
        installed, _ = read_assignments(state)
        fsldir = env.get("FSLDIR") or installed.get("VLAD_INSTALL_FSLDIR")
        if fsldir:
            env["FSLDIR"] = fsldir
            env["PATH"] = str(Path(fsldir) / "share/fsl/bin") + os.pathsep + env.get("PATH", "")
        bash = installed.get("VLAD_INSTALL_BASH")
        if bash:
            env["PATH"] = str(Path(bash).parent) + os.pathsep + env.get("PATH", "")
    env.setdefault("FSLOUTPUTTYPE", "NIFTI_GZ")
    return env
