"""Literal cohort configuration adapters; displaying a study never runs shell code."""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import tempfile
from contextlib import contextmanager

from .subjects import read_subject_visits


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
MODALITIES = {"MRSI": "mrsi", "DTI": "dti", "STRUCTURAL": "structural"}


@dataclass
class Cohort:
    name: str
    config_file: Path
    global_dir: Path
    values: dict = field(default_factory=dict)
    modalities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def registry_path(root: Path) -> Path:
    return Path(os.environ.get("VLAD_CONFIG_FILE", root / "VLAD_configs.txt")).expanduser().resolve()


def _expand_literals(raw: str, substitutions: dict) -> str:
    """Expand known variables without interpreting commands or single quotes."""
    output = []
    quote = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and quote != "'" and index + 1 < len(raw):
            output.append(raw[index:index + 2])
            index += 2
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            output.append(char)
            index += 1
            continue
        if quote != "'" and (raw[index:index + 2] == "$(" or char == "`"):
            raise ValueError("Dynamic shell expressions are not evaluated for display.")
        match = re.match(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", raw[index:]) if char == "$" and quote != "'" else None
        if match and (match.group(1) or match.group(2)) in substitutions:
            value = str(substitutions[match.group(1) or match.group(2)])
            output.append(value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") if quote == '"' else shlex.quote(value))
            index += len(match.group(0))
        else:
            output.append(char)
            index += 1
    return "".join(output)


def read_assignments(path: Path, base: dict | None = None) -> tuple[dict, list[str]]:
    """Read the simple shell assignments produced by VLAD without sourcing them."""
    values = dict(base or {})
    notes = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            if line.strip() and not line.lstrip().startswith("#"):
                notes.append(f"{path.name}:{number}: a non-assignment line was not evaluated for display.")
            continue
        name, raw = match.groups()
        array = raw.strip().startswith("(") and raw.strip().endswith(")")
        try:
            substitutions = {"HOME": str(Path.home()), **{key: val for key, val in values.items() if isinstance(val, str)}}
            expanded = _expand_literals(raw.strip()[1:-1] if array else raw, substitutions)
            tokens = shlex.split(expanded, comments=True)
        except ValueError:
            notes.append(f"{path.name}:{number}: could not read {name}.")
            continue
        if not array and len(tokens) > 1:
            notes.append(f"{path.name}:{number}: {name} is not a literal scalar assignment.")
            continue
        values[name] = tokens if array else (tokens[0] if tokens else "")
    return values, notes


def load_cohort(config_file: Path) -> Cohort:
    config_file = Path(config_file).expanduser().resolve()
    if not config_file.is_file():
        raise ValueError(f"Cohort configuration not found: {config_file}")
    values, notes = read_assignments(config_file)
    name = values.get("COHORT", "")
    directory = values.get("GLOBAL_DIR", "")
    if not isinstance(name, str) or not name or not isinstance(directory, str) or not directory:
        raise ValueError(f"{config_file.name} needs literal COHORT and GLOBAL_DIR values for the browser.")
    global_dir = Path(directory).expanduser()
    if not global_dir.is_absolute():
        global_dir = config_file.parent / global_dir
    global_dir = global_dir.resolve()
    modalities = []
    for label, suffix in MODALITIES.items():
        path = global_dir / f"config_{suffix}.sh"
        if path.is_file():
            additions, messages = read_assignments(path, values)
            values.update(additions)
            notes.extend(messages)
            modalities.append(label)
    if not global_dir.is_dir():
        notes.append(f"Study directory does not exist: {global_dir}")
    return Cohort(name, config_file, global_dir, values, modalities, notes)


def list_cohorts(registry: Path) -> list[Cohort]:
    registry = Path(registry)
    if not registry.is_file():
        return []
    cohorts = []
    seen = set()
    for line in registry.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        path = Path(line.strip()).expanduser()
        path = (registry.parent / path).resolve() if not path.is_absolute() else path.resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            cohorts.append(load_cohort(path))
        except (OSError, ValueError) as exc:
            cohorts.append(Cohort(f"[Unavailable] {path.parent.name}", path, path.parent, warnings=[str(exc)]))
    return cohorts


def register_cohort(registry: Path, config_file: Path) -> Cohort:
    registry = Path(registry).expanduser().resolve()
    cohort = load_cohort(config_file)
    with file_lock(registry.with_suffix(registry.suffix + ".lock")):
        existing = list_cohorts(registry)
        if any(item.name == cohort.name and item.config_file != cohort.config_file for item in existing):
            raise ValueError(f"Another study is already registered as {cohort.name!r}.")
        if not any(item.config_file == cohort.config_file for item in existing):
            text = registry.read_text() if registry.exists() else ""
            atomic_text(registry, text.rstrip("\n") + ("\n" if text else "") + str(cohort.config_file) + "\n")
    return cohort


def _shell_file(values: dict) -> str:
    lines = ["# Cohort settings written by the VLAD browser interface."]
    for name, value in values.items():
        if isinstance(value, list):
            lines.append(f"{name}=(" + " ".join(shlex.quote(str(item)) for item in value) + ")")
        else:
            lines.append(f"{name}={shlex.quote(str(value))}")
    return "\n".join(lines) + "\n"


def save_cohort(registry: Path, name: str, global_dir: Path, reference_image: Path,
                subjects_file: Path, exports_dir: Path | None = None, modality: str = "MRSI",
                input_dir: Path | str = "", compression: bool = True, space: str = "mni",
                extra_tags: str = "", metabolites: list[str] | None = None,
                masks: list[str] | None = None, overwrite: bool = False, *,
                input_mode: str = "legacy", resolution: str = "", signal_description: str = "signal",
                dwi_files: list[str] | None = None, qmask_mode: str = "thresholds",
                crlb_max: float = 20., fwhm_max: float = .1, snr_min: float = 10.,
                population_mask: Path | str | None = None, reference_metadata: dict | None = None,
                mask_space_confirmed: bool = False) -> Cohort:
    if not IDENTIFIER.fullmatch(name):
        raise ValueError("Study names must start with a letter/number and contain only letters, numbers and dashes.")
    modality = {"DWI": "DTI"}.get(modality.upper(), modality.upper())
    if modality not in MODALITIES:
        raise ValueError("Choose MRSI, DTI or STRUCTURAL.")
    root = Path(global_dir).expanduser().resolve()
    reference = Path(reference_image).expanduser().resolve()
    subjects = Path(subjects_file).expanduser().resolve()
    inputs = Path(input_dir).expanduser().resolve() if str(input_dir).strip() else None
    if not reference.is_file() or not reference.name.endswith((".nii", ".nii.gz")):
        raise ValueError("Select an existing NIfTI anatomical reference (.nii or .nii.gz).")
    if not subjects.is_file():
        raise ValueError("Select an existing subject list or covariates TSV/CSV file.")
    entries = read_subject_visits(subjects)
    if inputs is None or not inputs.is_dir():
        raise ValueError("Select the existing input NIfTI directory.")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", space):
        raise ValueError("The space label must contain letters, numbers, dots or dashes.")
    if input_mode == "bids" and reference_metadata and reference_metadata.get("template") and reference_metadata["template"] != space:
        raise ValueError("The TemplateFlow reference must use the same template as the image Space label. Choose matching anatomy or normalise the maps upstream first.")
    if input_mode not in {"legacy", "bids"}:
        raise ValueError("Choose legacy filenames or BIDS derivative discovery.")
    if input_mode == "bids" and masks and not mask_space_confirmed:
        raise ValueError("Confirm that the selected tissue masks are already registered in the image space. Resampling changes the grid, not the anatomical space.")
    if resolution and not re.fullmatch(r"[A-Za-z0-9.-]+", resolution):
        raise ValueError("Resolution must be the exact res- label, using letters, numbers, dots or dashes.")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", signal_description):
        raise ValueError("The signal description must be a valid desc- label.")
    if qmask_mode not in {"thresholds", "population"}:
        raise ValueError("Choose quality thresholds or a provided population binary mask.")
    for label, value in (("CRLB maximum", crlb_max), ("FWHM maximum", fwhm_max), ("SNR minimum", snr_min)):
        if not math.isfinite(float(value)) or float(value) < 0 or (label != "SNR minimum" and float(value) == 0):
            raise ValueError(f"{label} must be finite and {'nonnegative' if label == 'SNR minimum' else 'positive'}.")
    population = Path(population_mask).expanduser().resolve() if population_mask and str(population_mask).strip() else None
    if modality == "MRSI" and input_mode == "bids" and qmask_mode == "population":
        if population is None or not population.is_file() or not population.name.endswith((".nii", ".nii.gz")):
            raise ValueError("Select an existing population binary NIfTI mask.")
    if dwi_files is not None and (not dwi_files or any(not token or "/" in token or "\\" in token or token.startswith("-") or any(c.isspace() for c in token) for token in dwi_files)):
        raise ValueError("Select DWI scalar descriptions, suffixes or filename patterns, without directory components.")
    exports = Path(exports_dir).expanduser().resolve() if exports_dir and str(exports_dir).strip() else root / "reports"
    config = root / "config_global.sh"
    modality_config = root / f"config_{MODALITIES[modality]}.sh"
    if any(item.name == name and item.config_file != config for item in list_cohorts(registry)):
        raise ValueError(f"Another study is already registered as {name!r}.")
    with file_lock(root / ".vlad-settings.lock"):
        if (config.exists() or modality_config.exists()) and not overwrite:
            raise ValueError("Settings already exist. Select overwrite explicitly to replace them.")
        # Preserve advanced literal settings when changing the basic browser form.
        global_values = read_assignments(config)[0] if config.exists() else {}
        previous_global = dict(global_values)
        source_text = subjects.read_text(encoding="utf-8-sig")
        is_table = subjects.suffix.lower() in {".tsv", ".csv"} or "\t" in source_text.splitlines()[0] or "," in source_text.splitlines()[0]
        normalized_subjects = root / "Listes" / "AllSubjects.txt" if is_table else subjects
        # Keep the source covariates intact; the Bash workflow reads this stable order.
        if not is_table:
            original_entries = [line.strip() for line in source_text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
            if original_entries != entries:
                normalized_subjects = root / "Listes" / "AllSubjects.txt"
        global_values.update(COHORT=name, GLOBAL_DIR=str(root), T1_MNI=str(reference),
                             LIST_ALL_SUBJECTS=str(normalized_subjects), DIR_RANALYSES=str(exports),
                             SUBJECTS_SOURCE_FILE=str(subjects),
                             SUBJECT_VISITS_SHA256=hashlib.sha256(("\n".join(entries) + "\n").encode()).hexdigest(),
                             REFERENCE_IMAGE_FINGERPRINT=f"{reference.stat().st_size}:{reference.stat().st_mtime_ns}")
        changed_global_inputs = any(previous_global.get(key) != global_values.get(key)
                                    for key in ("COHORT", "GLOBAL_DIR", "T1_MNI", "LIST_ALL_SUBJECTS",
                                                "SUBJECT_VISITS_SHA256", "REFERENCE_IMAGE_FINGERPRINT"))
        if is_table:
            global_values["COVARIATES_FILE"] = str(subjects)
        else:
            global_values.pop("COVARIATES_FILE", None)
        if reference_metadata is not None:
            global_values["REFERENCE_TEMPLATE_METADATA"] = json.dumps(reference_metadata, sort_keys=True)
        else:
            global_values.pop("REFERENCE_TEMPLATE_METADATA", None)
        prefix = modality
        mode_values = read_assignments(modality_config, global_values)[0] if modality_config.exists() else {}
        mode_values = {key: value for key, value in mode_values.items() if key not in global_values}
        previous_mode = dict(mode_values)
        mode_values.update({f"NII_DIR_{prefix}": str(inputs), f"NII_DIR_{prefix}_compression": "yes" if compression else "no",
                            f"NII_DIR_{prefix}_orga": "bysubj", f"{prefix}_SPACE_LABEL": space,
                            f"{prefix}_EXTRA_TAGS": extra_tags, f"{prefix}_MASK_LIST": "|".join(str(Path(item).expanduser().resolve()) for item in (masks or []))})
        mode_values.update({f"{prefix}_INPUT_MODE": input_mode, f"{prefix}_SOURCE_DIR": str(inputs),
                            f"{prefix}_RESOLUTION": resolution})
        if input_mode == "bids":
            mode_values.update({f"{prefix}_MASK_SPACE": space if mask_space_confirmed else "",
                                f"{prefix}_MASK_SPACE_CONFIRMED": "yes" if mask_space_confirmed else "no"})
        if modality == "MRSI":
            mode_values.setdefault("METAB", metabolites or ["NAANAAG", "CrPCr", "GPCPCh", "Ins", "GluGln"])
            if metabolites is not None:
                mode_values["METAB"] = metabolites
            mode_values.setdefault("FILES_MRSI_MET", ["signal_filtbiharmonic_pvcorr", "crlb"])
            mode_values.setdefault("FILES_MRSI_GLOBAL", ["fwhm", "snr"])
            mode_values.update(MRSI_SIGNAL_DESC=signal_description, MRSI_QMASK_MODE=qmask_mode,
                               MRSI_CRLB_MAX=str(float(crlb_max)), MRSI_FWHM_MAX=str(float(fwhm_max)),
                               MRSI_SNR_MIN=str(float(snr_min)), MRSI_POPULATION_MASK=str(population or ""))
        elif modality == "DTI":
            mode_values.setdefault("FILES_DTI", ["gFA"])
            if dwi_files is not None:
                mode_values["FILES_DTI"] = dwi_files
                mode_values.pop("DTI_SELECTORS", None)
            mode_values.setdefault("DTI_ACQ_LABEL", "dti")
            mode_values.setdefault("DTI_DATA_SUFFIX", "dwi")
        else:
            mode_values.setdefault("FILES_STRUCTURAL", ["mwp1", "mwp2", "wp1", "wp2"])
            mode_values.setdefault("STRUCTURAL_ACQ_LABEL", "memprage")
            mode_values.setdefault("STRUCTURAL_DATA_SUFFIX", "T1w")
        if changed_global_inputs or mode_values != previous_mode:
            mode_values.pop(f"{prefix}_FILES_VALIDATED", None)
        for folder in ("Cartes4D", "Masques", "Results", "Scripts", "Listes"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        exports.mkdir(parents=True, exist_ok=True)
        if normalized_subjects != subjects:
            atomic_text(normalized_subjects, "\n".join(entries) + "\n")
        atomic_text(config, _shell_file(global_values))
        atomic_text(modality_config, _shell_file(mode_values))
        if changed_global_inputs:
            # A common anatomy/population edit invalidates other modalities too.
            # Append only the readiness flag to preserve custom shell settings.
            for other, suffix in MODALITIES.items():
                other_config = root / f"config_{suffix}.sh"
                if other != prefix and other_config.is_file():
                    atomic_text(other_config, other_config.read_text().rstrip() +
                                f"\n# Shared study inputs changed; prepare this modality again.\n{other}_FILES_VALIDATED=()\n")
    return register_cohort(registry, config)


def list_reports(cohort: Cohort) -> list[Path]:
    results = cohort.global_dir / "Results"
    return sorted(results.rglob("*.html"), key=lambda path: path.stat().st_mtime, reverse=True) if results.is_dir() else []
