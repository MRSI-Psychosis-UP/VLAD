"""Validated adapters for the existing VLAD reporting files."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
import io
from pathlib import Path
import re

import numpy as np
import pandas as pd


class ReportError(ValueError):
    """An input cannot be interpreted without changing the requested analysis."""


@dataclass
class ReportRequest:
    kind: str
    results_dir: Path
    analysis_name: str
    matrix_name: str
    permutations: int = 500
    export_dir: Path | None = None
    modality: str = "mrsi"
    output: Path | None = None
    confirmation: bool = False
    report_date: str | None = None
    subject_column: str | None = None
    visit_column: str | None = None
    design_columns: list[str] | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("randomise", "swe"):
            raise ReportError("Report kind must be randomise or swe.")
        for label, value in (("analysis", self.analysis_name), ("matrix", self.matrix_name), ("modality", self.modality)):
            if not value or any(char in value for char in ("/", "\\", "\n", "\r", "\x00")) or value in (".", ".."):
                raise ReportError(f"Invalid {label} name: {value!r}.")
        if self.permutations < 1:
            raise ReportError("The permutation count must be positive.")
        self.results_dir = Path(self.results_dir).expanduser().resolve()
        self.export_dir = Path(self.export_dir or self.results_dir).expanduser().resolve()
        self.report_date = self.report_date or date.today().isoformat()
        try:
            date.fromisoformat(self.report_date)
        except ValueError as exc:
            raise ReportError("Report date must use YYYY-MM-DD.") from exc
        suffix = self.modality + ("-swe" if self.kind == "swe" else "")
        suffix += "-confirmation" if self.confirmation else ""
        default_name = f"{self.analysis_name}_{self.matrix_name}_{suffix}.html"
        self.output = Path(self.output or self.results_dir / default_name).expanduser().resolve()


@dataclass
class Region:
    source: Path
    metabolite: str
    matrix: str
    acquisition: str
    mask: str
    contrast_id: int
    column: str

    @property
    def label(self) -> str:
        return f"{self.metabolite} · {self.acquisition} · {self.mask} · contrast {self.contrast_id}"


@dataclass
class ReportData:
    request: ReportRequest
    design: pd.DataFrame
    combined: pd.DataFrame
    regions: list[Region]
    main_variables: list[str] = field(default_factory=list)
    analysis_type: str = "continuous"
    group_column: str | None = None
    group_levels: list[str] = field(default_factory=list)
    subject_column: str | None = None
    visit_column: str | None = None
    contrast_matrix: np.ndarray | None = None
    contrast_terms: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    inputs: list[Path] = field(default_factory=list)


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def read_fsl_matrix(path: Path) -> np.ndarray:
    """Read plain or FSL-header matrices, including a single contrast row."""
    lines = _lines(path)
    header = {}
    for line in lines:
        if line.startswith("/") and line != "/Matrix":
            parts = line.split()
            if len(parts) == 2:
                header[parts[0].lower()] = parts[1]
    if "/Matrix" in lines:
        lines = lines[lines.index("/Matrix") + 1:]
    elif any(line.startswith("/") for line in lines):
        raise ReportError(f"Missing /Matrix section in {path.name}.")
    if not lines:
        raise ReportError(f"Empty matrix: {path}.")
    try:
        matrix = np.loadtxt(io.StringIO("\n".join(lines)), ndmin=2)
    except ValueError as exc:
        raise ReportError(f"Invalid numeric matrix: {path}.") from exc
    if not np.isfinite(matrix).all():
        raise ReportError(f"Non-finite values in {path.name}.")
    for key, expected in (("/numwaves", matrix.shape[1]), ("/numpoints", matrix.shape[0]), ("/numcontrasts", matrix.shape[0])):
        if key in header:
            try:
                matches = int(header[key]) == expected
            except ValueError as exc:
                raise ReportError(f"Invalid {key} header in {path.name}.") from exc
            if not matches:
                raise ReportError(f"{key} header disagrees with data in {path.name}.")
    return matrix


def _find_column(frame: pd.DataFrame, explicit: str | None, kind: str) -> str | None:
    if explicit:
        if explicit not in frame:
            raise ReportError(f"{kind} column {explicit!r} is absent from the matrix.")
        return explicit
    exact = {
        "subject": ("subject", "subject_id", "subj", "subj_id", "id", "participant", "participant_id", "nom_dossier"),
        "visit": ("visit", "visit_id", "session", "session_id", "ses", "time", "timepoint", "tp"),
        "group": ("group", "grp"),
    }[kind]
    lower = {str(name).lower(): name for name in frame}
    for name in exact:
        if name in lower:
            return lower[name]
    pattern = {"subject": r"subject|subj|participant", "visit": r"visit|session|timepoint", "group": r"group|grp"}[kind]
    matches = [name for name in frame if re.search(pattern, name, flags=re.I)]
    if len(matches) > 1:
        raise ReportError(f"Ambiguous {kind} columns {matches}; select a column explicitly.")
    return matches[0] if matches else None


def _read_design(path: Path, request: ReportRequest) -> pd.DataFrame:
    try:
        # Inspect the raw header: pandas otherwise silently renames duplicates.
        header = path.read_text(encoding="utf-8-sig").splitlines()[0].split("\t")
        if len(set(header)) != len(header) or any(not name for name in header):
            raise ReportError(f"Empty or duplicate matrix column names in {path.name}.")
        frame = pd.read_csv(path, sep="\t", dtype=str, encoding="utf-8-sig")
    except (OSError, pd.errors.ParserError, IndexError) as exc:
        raise ReportError(f"Cannot read tab-separated design {path}: {exc}") from exc
    if frame.empty:
        raise ReportError("The design matrix has no rows.")
    identities = {_find_column(frame, request.subject_column, "subject"), _find_column(frame, request.visit_column, "visit")}
    for name in frame:
        if name in identities:
            continue
        numeric = pd.to_numeric(frame[name], errors="coerce")
        if numeric.notna().sum() == frame[name].notna().sum():
            frame[name] = numeric
            if np.isinf(numeric.to_numpy(dtype=float)).any():
                raise ReportError(f"Non-finite design values in {name!r}.")
    return frame


def _design_terms(data: ReportData) -> list[str]:
    request = data.request
    candidates = [name for name in data.design if name not in (data.subject_column, data.visit_column)]
    if request.design_columns:
        if len(set(request.design_columns)) != len(request.design_columns) or any(name not in candidates for name in request.design_columns):
            raise ReportError("--design-columns must be distinct non-ID columns from the design table.")
        terms = list(request.design_columns)
    else:
        matrix_path = request.results_dir / f"{request.matrix_name}.mat"
        if matrix_path.exists():
            matrix = read_fsl_matrix(matrix_path)
            data.inputs.append(matrix_path)
            if len(matrix) != len(data.design):
                raise ReportError("FSL design and exported table have different row counts.")
            remaining = [name for name in candidates if pd.api.types.is_numeric_dtype(data.design[name])]
            terms = []
            for column in matrix.T:
                matches = [name for name in remaining if np.allclose(data.design[name], column, rtol=1e-5, atol=1e-7)]
                if not matches:
                    raise ReportError("Cannot align FSL design columns to the exported table; supply --design-columns in FSL order.")
                if len(matches) > 1 and np.allclose(column, 1) and "Intercept" in matches:
                    matches = ["Intercept"]
                if len(matches) > 1:
                    raise ReportError(f"Ambiguous value-identical design columns {matches}; supply --design-columns in exact FSL order.")
                name = matches[0]
                remaining.remove(name)
                terms.append(name)
        else:
            terms = candidates
    if data.contrast_matrix is not None and data.contrast_matrix.shape[1] != len(terms):
        raise ReportError("Contrast/design column counts differ; provide the FSL .mat or --design-columns in exact contrast order.")
    return terms


def load_report(request: ReportRequest) -> ReportData:
    matrix_path = request.results_dir / f"{request.matrix_name}.csv"
    if not matrix_path.is_file():
        raise ReportError(f"Design table not found: {matrix_path}.")
    design = _read_design(matrix_path, request)
    data = ReportData(request, design, design.copy(), [], inputs=[matrix_path])
    data.subject_column = _find_column(design, request.subject_column, "subject")
    data.visit_column = _find_column(design, request.visit_column, "visit")
    list_path = request.results_dir / f"To_analyse_with_R_{request.analysis_name}_{request.matrix_name}.txt"
    if not list_path.exists():
        # New spelling is accepted while the workflow retains its existing file contract.
        list_path = request.results_dir / f"To_analyse_{request.analysis_name}_{request.matrix_name}.txt"
    if not list_path.is_file():
        raise ReportError(f"Regional-means file list not found: {list_path}.")
    data.inputs.append(list_path)
    names = set(design.columns)
    seen_paths = set()
    pattern = re.compile(r"^meants__(.+?)__(.+?)__(.+?)__(.+?)__(.+?)__contrast(\d+)\.txt$")
    for raw in _lines(list_path):
        source = Path(raw).expanduser()
        source = (request.results_dir / source).resolve() if not source.is_absolute() else source.resolve()
        if source in seen_paths:
            raise ReportError(f"Duplicate regional-means input: {source}.")
        seen_paths.add(source)
        match = pattern.match(source.name)
        if not match:
            raise ReportError(f"Unexpected regional-means filename: {source.name}.")
        analysis, metabolite, matrix, acquisition, mask, contrast = match.groups()
        if analysis != request.analysis_name or matrix != request.matrix_name:
            raise ReportError(f"Analysis/matrix labels in {source.name} do not match the requested report.")
        if not source.is_file():
            raise ReportError(f"Regional means not found: {source}.")
        try:
            values = pd.read_csv(source, sep=r"\s+", header=None, skip_blank_lines=False)
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise ReportError(f"Invalid regional-means input: {source}.") from exc
        if values.shape != (len(design), 1):
            raise ReportError(f"{source.name}: expected {len(design)} rows and one column, got {values.shape}.")
        numeric = pd.to_numeric(values.iloc[:, 0], errors="coerce")
        invalid = values.iloc[:, 0].notna() & numeric.isna()
        if invalid.any() or np.isinf(numeric.to_numpy(dtype=float)).any():
            raise ReportError(f"Invalid numeric values in {source.name}.")
        column = "__".join((metabolite, matrix, acquisition, mask, f"contrast{contrast}"))
        if column in names:
            raise ReportError(f"Duplicate result column: {column}.")
        names.add(column)
        data.combined[column] = numeric.to_numpy()
        data.regions.append(Region(source, metabolite, matrix, acquisition, mask, int(contrast), column))
        data.inputs.append(source)
    if request.kind == "randomise":
        interest_path = request.results_dir / f"main_variable_{request.matrix_name}.txt"
        if not interest_path.exists() and data.regions:
            raise ReportError(f"Main-variable list not found: {interest_path}.")
        if interest_path.exists():
            data.inputs.append(interest_path)
            data.main_variables = _lines(interest_path)
        if data.regions and not data.main_variables:
            raise ReportError("The main-variable list is empty.")
        if any(name not in design for name in data.main_variables):
            raise ReportError("A main-variable name is absent from the design table.")
        if len(set(data.main_variables)) != len(data.main_variables):
            raise ReportError("Duplicate names in the main-variable list.")
        if len(data.main_variables) >= 2:
            data.analysis_type = "group" if len(data.main_variables) == 2 else "anova"
            numeric = design[data.main_variables].apply(pd.to_numeric, errors="coerce")
            if numeric.isna().any().any():
                raise ReportError("Group design columns must be numeric and complete.")
            if len(data.main_variables) == 2:
                labels = np.where(numeric.iloc[:, 0] > numeric.iloc[:, 1], data.main_variables[0], data.main_variables[1])
            else:
                if not (numeric.eq(1).sum(axis=1) == 1).all():
                    raise ReportError("ANOVA rows must select exactly one main-variable group with value 1.")
                labels = numeric.eq(1).idxmax(axis=1)
            if "group" in data.combined and not (data.combined["group"].astype(str).to_numpy() == labels).all():
                raise ReportError("The existing group column conflicts with groups derived from main variables.")
            data.group_column = "group"
            data.group_levels = list(data.main_variables)
            data.combined["group"] = pd.Categorical(labels, categories=data.group_levels, ordered=True)
    else:
        data.analysis_type = "mixed"
        if not data.subject_column:
            sub_path = next((request.results_dir / f"{request.matrix_name}{suffix}" for suffix in ("_design_sub.txt", ".sub", "-sub.sub") if (request.results_dir / f"{request.matrix_name}{suffix}").exists()), None)
            if sub_path:
                subjects = read_fsl_matrix(sub_path)
                if subjects.shape[0] != len(design):
                    raise ReportError("Subject file and design table have different row counts.")
                data.design["subject_id"] = subjects[:, 0].astype(str)
                data.combined["subject_id"] = data.design["subject_id"]
                data.subject_column = "subject_id"
                data.inputs.append(sub_path)
                data.warnings.append("Subject identifiers were recovered from the SwE subject file.")
        if not data.subject_column and data.regions:
            raise ReportError("SwE reporting needs a subject column; use --subject-column or supply the SwE subject file.")
        data.group_column = _find_column(design, None, "group")
        for suffix in (".con", "_con.txt", "_contrast.txt"):
            path = request.results_dir / f"{request.matrix_name}{suffix}"
            if path.exists():
                data.contrast_matrix = read_fsl_matrix(path)
                data.inputs.append(path)
                break
        data.contrast_terms = _design_terms(data)
        # R coerced predictors before dropping response-specific missing rows.
        # Preserve that type policy in fitting, summaries and Arrow exports.
        from .mixed import is_categorical

        for name in data.design:
            if name in (data.subject_column, data.visit_column, "Intercept"):
                continue
            if is_categorical(data.design[name]):
                levels = sorted(data.design[name].dropna().unique())
                data.design[name] = pd.Categorical(data.design[name], categories=levels)
                data.combined[name] = data.design[name]
        if data.contrast_matrix is not None:
            if any(region.contrast_id < 1 or region.contrast_id > len(data.contrast_matrix) for region in data.regions):
                raise ReportError("A regional result refers to a contrast row outside the contrast matrix.")
        elif data.regions:
            data.warnings.append("No contrast file was found; coefficient summaries are available but no signed contrast test can be calculated.")
    if data.regions:
        data.warnings.append("Regional means are aligned by row with the design table; their order must match the original 4D subject list.")
    return data


def input_manifest(data: ReportData) -> list[dict]:
    return [{"name": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in dict.fromkeys(data.inputs)]
