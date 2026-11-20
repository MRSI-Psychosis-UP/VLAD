"""Headless, explicit design construction for the browser interface.

The browser uses numerical matrices rather than running the old interactive
Tk helpers. Continuous models include an intercept; grouped Randomise models
use group-cell means, with contrasts between groups rather than against zero.
SwE uses a treatment-coded fixed design plus its three-column subject file.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import io
import json
from pathlib import Path
import re
from typing import BinaryIO
import zipfile

import numpy as np
import pandas as pd


class DesignError(ValueError):
    """A design choice cannot define the requested statistical model."""


@dataclass
class DesignOptions:
    analysis_name: str
    matrix_name: str
    subject_column: str
    main_variable: str
    predictors: list[str] = field(default_factory=list)
    kind: str = "randomise"
    visit_column: str | None = None
    group_column: str | None = None
    analysis_type: str = "auto"
    demean_covariates: bool = True
    categorical_columns: list[str] = field(default_factory=list)
    reference_levels: dict[str, str] = field(default_factory=dict)
    contrasts: list[list[float]] | None = None
    contrast_labels: list[str] | None = None
    selection_audit: list[dict] = field(default_factory=list)


def detect_identifier_columns(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    """Find BIDS identifiers before considering the older spreadsheet names."""
    columns = {str(name).strip().lower(): name for name in frame.columns}
    if len(columns) != len(frame.columns):
        raise DesignError("Identifier detection needs distinct column names, ignoring letter case.")
    subject = next((columns[name] for name in
                    ("subject_id", "participant_id", "subject", "nom_dossier", "id")
                    if name in columns), None)
    visit = next((columns[name] for name in
                  ("session_id", "session", "visit_id", "visit", "timepoint", "time")
                  if name in columns), None)
    return subject, visit


def _identity(value, prefix: str) -> str:
    value = _text(value)
    return value[len(prefix):] if value.lower().startswith(prefix) else value


def _subject_visit(subject, visit=None, visit_column: str | None = None) -> str:
    subject = _identity(subject, "sub-")
    if visit is None:
        if subject.count("_") != 1:
            raise DesignError("Use SUBJECT_VISIT identifiers such as 001_V1, or select a separate session column.")
        subject, session = subject.split("_", 1)
        session = _identity(session, "ses-")
    else:
        raw = _text(visit)
        session = _identity(raw, "ses-")
        # Older VLAD spreadsheets use bare visit numbers for V1, V2, etc.
        # BIDS session_id and ses-* are identifiers: preserve their exact text.
        if (visit_column or "").lower() not in {"session_id", "session"} and not raw.lower().startswith("ses-"):
            if not session.startswith(("V", "v")):
                session = "V" + session
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", item) for item in (subject, session)):
        raise DesignError("Subject/session labels may contain letters, numbers, dashes and dots; use one underscore only between them.")
    return subject + "_" + session


def filter_rows(frame: pd.DataFrame, subject_column: str, visit_column: str | None = None,
                filters: list[dict] | None = None, include_subjects: list[str] | None = None,
                exclude_subjects: list[str] | None = None,
                excluded_rows: list[int] | None = None) -> dict:
    """Select observations in source order and explain every discarded data row.

    Filter rules are combined with AND. Inclusion/exclusion tokens can identify
    a whole participant or a single SUBJECT_SESSION observation. Row numbers
    refer to one-based data rows, so sorting a displayed table cannot change
    what a saved exclusion means.
    """
    required = [name for name in (subject_column, visit_column) if name]
    missing = [name for name in required if name not in frame]
    if missing:
        raise DesignError("Identifier columns not found: " + ", ".join(missing))
    if subject_column == visit_column:
        raise DesignError("Subject and session must be different columns.")
    count = len(frame)
    reasons: list[list[str]] = [[] for _ in range(count)]
    labels = []
    subjects = []
    for position, (_, row) in enumerate(frame.iterrows()):
        if pd.isna(row[subject_column]) or (visit_column and pd.isna(row[visit_column])):
            labels.append("")
            subjects.append("")
            reasons[position].append("Missing subject/session identifier")
            continue
        label = _subject_visit(row[subject_column], row[visit_column] if visit_column else None, visit_column)
        labels.append(label)
        subjects.append(label.split("_", 1)[0])

    def add_reasons(keep, reason):
        for index in np.flatnonzero(~np.asarray(keep, dtype=bool)):
            reasons[index].append(reason)

    def tokens(values):
        normal = set()
        for value in values or []:
            text = _identity(value, "sub-")
            if "_" in text:
                text = _subject_visit(text)
            normal.add(text)
        return normal

    included = tokens(include_subjects)
    excluded = tokens(exclude_subjects)
    if included:
        add_reasons([label in included or subject in included for label, subject in zip(labels, subjects)],
                    "Outside included subjects/visits")
    if excluded:
        add_reasons([label not in excluded and subject not in excluded for label, subject in zip(labels, subjects)],
                    "Explicitly excluded subject/visit")
    for rule in filters or []:
        column = rule.get("column")
        operator = rule.get("operator", "in")
        if column not in frame:
            raise DesignError(f"Filter column not found: {column}.")
        values = rule.get("values", [])
        if not isinstance(values, (list, tuple, set, np.ndarray, pd.Series)):
            values = [values]
        series = frame[column]
        nonmissing = series.notna() & series.astype(str).str.strip().ne("")
        if operator == "not missing":
            keep = nonmissing
        elif operator in ("in", "not in"):
            normalized = {_text(value) for value in values}
            if not normalized:
                raise DesignError(f"Choose at least one value for the {column} filter.")
            keep = series.map(_text).isin(normalized)
            if operator == "not in":
                keep = ~keep
            keep &= nonmissing
        elif operator in (">", ">=", "<", "<=", "between"):
            expected = 2 if operator == "between" else 1
            if len(values) != expected:
                raise DesignError(f"The {column} {operator} filter requires {expected} numeric value(s).")
            try:
                targets = np.asarray(values, dtype=float)
            except (TypeError, ValueError) as exc:
                raise DesignError(f"Filter bounds for {column} must be finite numbers.") from exc
            if not np.isfinite(targets).all():
                raise DesignError(f"Filter bounds for {column} must be finite numbers.")
            number = pd.to_numeric(series, errors="coerce")
            if operator == "between":
                if targets[0] > targets[1]:
                    raise DesignError(f"The lower bound exceeds the upper bound for {column}.")
                keep = number.between(*targets, inclusive="both")
            else:
                keep = {">": number.gt, ">=": number.ge, "<": number.lt, "<=": number.le}[operator](targets[0])
            keep &= np.isfinite(number)
        else:
            raise DesignError(f"Unsupported filter operator: {operator}.")
        add_reasons(keep.fillna(False), f"Filter: {column} {operator} " + ", ".join(map(str, values)))
    rows = set(excluded_rows or [])
    if any(not isinstance(row, (int, np.integer)) or not 1 <= row <= count for row in rows):
        raise DesignError("Excluded row numbers must identify one-based rows of the covariate table.")
    for row in rows:
        reasons[row - 1].append("Manually excluded data row")
    keep = np.asarray([not reason for reason in reasons], dtype=bool)
    audit = pd.DataFrame({"row": np.arange(1, count + 1), "subject_visit": labels,
                          "included": keep, "reason": ["; ".join(reason) for reason in reasons]})
    return {"frame": frame.iloc[np.flatnonzero(keep)].copy(), "audit": audit,
            "included_rows": audit.loc[keep, "row"].tolist(),
            "dropped_rows": audit.loc[~keep, "row"].tolist()}


def _source(data: bytes | Path | str | BinaryIO):
    if isinstance(data, (str, Path)):
        return Path(data)
    if isinstance(data, bytes):
        return io.BytesIO(data)
    if hasattr(data, "getvalue"):
        return io.BytesIO(data.getvalue())
    if hasattr(data, "read"):
        position = data.tell() if hasattr(data, "tell") else None
        payload = data.read()
        if position is not None:
            data.seek(position)
        return io.BytesIO(payload.encode() if isinstance(payload, str) else payload)
    raise DesignError("Choose an Excel, CSV or TSV table.")


def _filename(data, filename: str | None) -> str:
    return filename or (str(data) if isinstance(data, (str, Path)) else getattr(data, "name", "table.csv"))


def sheet_names(data, filename: str | None = None) -> list[str]:
    """Return workbook tabs, or no tabs for delimited text files."""
    suffix = Path(_filename(data, filename)).suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        return []
    if suffix not in (".xlsx", ".xlsm"):
        raise DesignError("Supported tables are .xlsx, .xlsm, .csv and .tsv.")
    try:
        with pd.ExcelFile(_source(data), engine="openpyxl") as workbook:
            return list(workbook.sheet_names)
    except (ValueError, OSError, ImportError, zipfile.BadZipFile) as exc:
        raise DesignError(f"Cannot open the workbook: {exc}") from exc


def load_table(data, filename: str | None = None, sheet_name: str | None = None) -> pd.DataFrame:
    """Read uploaded bytes or a server-side file while preserving textual IDs."""
    suffix = Path(_filename(data, filename)).suffix.lower()
    try:
        if suffix in (".xlsx", ".xlsm"):
            frame = pd.read_excel(_source(data), sheet_name=sheet_name or 0, dtype=object, engine="openpyxl")
        elif suffix in (".csv", ".tsv", ".txt"):
            # Text IDs such as 001 must survive the browser upload intact.
            frame = pd.read_csv(_source(data), sep="\t" if suffix == ".tsv" else None,
                                engine="python", dtype=str, encoding="utf-8-sig")
        else:
            raise DesignError("Supported tables are .xlsx, .xlsm, .csv and .tsv.")
    except (ValueError, OSError, ImportError, pd.errors.ParserError, zipfile.BadZipFile) as exc:
        raise DesignError(f"Cannot read the table: {exc}") from exc
    if frame.empty or not len(frame.columns):
        raise DesignError("The table has no subject rows.")
    frame.columns = [str(name).strip() for name in frame.columns]
    if len(set(frame.columns)) != len(frame.columns) or any(not name for name in frame.columns):
        raise DesignError("The table needs distinct, nonempty column names.")
    return frame


def validate_contrasts(contrasts, design: pd.DataFrame | np.ndarray,
                       labels: list[str] | None = None) -> tuple[np.ndarray, list[str]]:
    """Require finite, nonzero linear hypotheses in the design's column order."""
    try:
        matrix = np.asarray(contrasts, dtype=float)
    except (ValueError, TypeError) as exc:
        raise DesignError("Contrast weights must be numeric.") from exc
    if matrix.ndim == 1:
        matrix = matrix[np.newaxis, :]
    values = np.asarray(design, dtype=float)
    if values.ndim != 2 or matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] != values.shape[1]:
        raise DesignError("Each contrast needs one weight for every design column, in the displayed order.")
    if not np.isfinite(matrix).all():
        raise DesignError("Contrast weights must be finite numbers.")
    if np.any(np.all(matrix == 0, axis=1)):
        raise DesignError("Every contrast must contain at least one nonzero weight.")
    # Explicitly test membership of the row space, including designs supplied
    # directly by callers. Full-rank models pass this condition automatically.
    projected = matrix @ np.linalg.pinv(values) @ values
    if not np.allclose(matrix, projected, rtol=1e-8, atol=1e-10):
        raise DesignError("A contrast is not estimable from this design.")
    if labels is None:
        names = [f"Contrast {number}" for number in range(1, len(matrix) + 1)]
    else:
        names = [str(label).strip() for label in labels]
        if len(names) != len(matrix) or any(not label or any(char in label for char in "\r\n\t\x00") for label in names):
            raise DesignError("Give each contrast a nonempty, single-line label.")
        if len(set(names)) != len(names):
            raise DesignError("Contrast labels must be distinct.")
    return matrix, names


def load_contrasts(data, columns: list[str], filename: str | None = None) -> dict:
    """Read FSL .con, named CSV/TSV, or plain numeric contrast rows.

    A named table must have every design column once and may include one
    additional label column named contrast, name or label. Named weights are
    reordered explicitly to the displayed design; positional weights keep
    their input order. Validation against the actual model happens in preview.
    """
    source = _source(data)
    try:
        text = source.read_text(encoding="utf-8-sig") if isinstance(source, Path) else source.read().decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DesignError(f"Cannot read contrast text: {exc}") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise DesignError("The contrast file has no rows.")
    labels = None
    if any(line.startswith("/") for line in lines):
        try:
            start = lines.index("/Matrix")
            matrix = np.loadtxt(io.StringIO("\n".join(lines[start + 1:])), ndmin=2)
            headers = {match.group(1): match.group(2) for line in lines[:start]
                       if (match := re.match(r"^/(NumWaves|NumContrasts|NumPoints)\s+(\d+)$", line))}
            nrows = headers.get("NumContrasts", headers.get("NumPoints"))
            if "NumWaves" not in headers or nrows is None:
                raise ValueError("Missing /NumWaves or /NumContrasts header")
            if matrix.shape != (int(nrows), int(headers["NumWaves"])):
                raise ValueError("FSL headers do not match the contrast matrix")
            named = {}
            for line in lines[:start]:
                match = re.match(r"^/ContrastName(\d+)\s+(.+)$", line)
                if match:
                    named[int(match.group(1))] = match.group(2)
            if named:
                labels = [named.get(i, f"Contrast {i}") for i in range(1, len(matrix) + 1)]
        except (ValueError, TypeError) as exc:
            raise DesignError(f"Cannot read FSL contrasts: {exc}") from exc
    else:
        first = re.split(r"[,\t\s]+", lines[0])
        try:
            [float(value) for value in first]
            numeric = True
        except ValueError:
            numeric = False
        try:
            if numeric:
                rows = [[float(value) for value in re.split(r"[,\t\s]+", line)] for line in lines]
                matrix = np.asarray(rows, dtype=float)
            else:
                # CSV sniffing correctly handles quoted labels and columns
                # containing spaces such as Group[Healthy controls].
                separator = "\t" if "\t" in lines[0] else ","
                table = pd.read_csv(io.StringIO(text), sep=separator, dtype=str, keep_default_na=False)
                table.columns = [str(name).strip() for name in table.columns]
                if len(table.columns) != len(set(table.columns)):
                    raise DesignError("Contrast table column names must be distinct.")
                extra = [name for name in table if name not in columns]
                missing = [name for name in columns if name not in table]
                if missing or len(extra) > 1 or (extra and extra[0].lower() not in {"contrast", "name", "label"}):
                    raise DesignError("Named contrast tables must contain exactly the displayed design columns and an optional contrast/name/label column.")
                matrix = table[columns].to_numpy(dtype=float)
                labels = table[extra[0]].tolist() if extra else None
        except (ValueError, TypeError, pd.errors.ParserError) as exc:
            if isinstance(exc, DesignError):
                raise
            raise DesignError(f"Cannot read contrast weights: {exc}") from exc
    # An identity matrix validates dimensions/labels without assuming a model;
    # actual estimability is checked against the fitted design in preview.
    matrix, labels = validate_contrasts(matrix, np.eye(len(columns)), labels)
    return {"contrasts": matrix, "labels": labels}


def _name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", value or ""):
        raise DesignError(f"{label} must start with a letter/number and contain only letters, numbers and dashes.")


def _text(value) -> str:
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _numeric(series: pd.Series) -> pd.Series | None:
    values = pd.to_numeric(series, errors="coerce")
    return values.astype(float) if values.notna().all() else None


def _categorical(series: pd.Series, options: DesignOptions, name: str) -> bool:
    if name in options.categorical_columns or pd.api.types.is_bool_dtype(series):
        return True
    numeric = _numeric(series)
    if numeric is None:
        return True
    unique = np.unique(numeric)
    if len(unique) == 2:
        return True
    # Match the existing longitudinal report's documented coercion policy.
    return options.kind == "swe" and len(unique) <= 6 and np.all(np.abs(unique - np.round(unique)) < 1e-8)


def _levels(series: pd.Series, reference: str | None = None) -> list:
    values = list(pd.unique(series))
    numeric = _numeric(series)
    values = sorted(values, key=lambda value: float(value)) if numeric is not None else sorted(values, key=lambda value: str(value))
    if reference is not None:
        match = [value for value in values if _text(value) == str(reference).strip()]
        if len(match) != 1:
            raise DesignError(f"Reference level {reference!r} is absent from the included observations.")
        values = [match[0], *[value for value in values if value != match[0]]]
    return values


def _subject_labels(frame: pd.DataFrame, options: DesignOptions) -> tuple[list[str], list[str], list[str]]:
    subjects, visits, labels = [], [], []
    for row in frame.itertuples(index=False, name=None):
        values = dict(zip(frame.columns, row))
        label = _subject_visit(values[options.subject_column], values[options.visit_column] if options.visit_column else None,
                               options.visit_column)
        subject, visit = label.split("_", 1)
        subjects.append(subject)
        visits.append(visit)
        labels.append(label)
    if len(labels) != len(set(labels)):
        raise DesignError("Duplicate subject/visit rows would duplicate a 4D observation; remove them first.")
    return subjects, visits, labels


def preview_design(frame: pd.DataFrame, options: DesignOptions) -> dict:
    """Build and validate a design without writing files or opening dialogs."""
    if options.visit_column is None and options.subject_column.lower() in {"subject_id", "participant_id"}:
        _, session_column = detect_identifier_columns(frame)
        if session_column is not None:
            options = replace(options, visit_column=session_column)
    _name(options.analysis_name, "Analysis name")
    _name(options.matrix_name, "Matrix name")
    if options.kind not in ("randomise", "swe"):
        raise DesignError("Choose Randomise or SwE.")
    if options.analysis_type not in ("auto", "continuous", "group", "anova"):
        raise DesignError("Choose an automatic, continuous, two-group or ANOVA model.")
    if options.subject_column == options.visit_column:
        raise DesignError("Subject and visit must be different columns.")
    nuisance = list(dict.fromkeys(name for name in options.predictors if name != options.main_variable))
    if any(name.lower() == "intercept" for name in [options.main_variable, *nuisance]):
        raise DesignError("Intercept is generated automatically; choose measured variables instead.")
    ids = [name for name in (options.subject_column, options.visit_column) if name]
    model_inputs = [options.main_variable, *nuisance]
    if options.subject_column in model_inputs:
        raise DesignError("Subject identifiers cannot also be fixed-effect predictors.")
    if options.kind != "swe" and options.visit_column and options.visit_column in model_inputs:
        raise DesignError("Visit identifiers can be modelled directly in SwE; use a measured predictor for Randomise.")
    required = list(dict.fromkeys([*ids, options.main_variable, *nuisance,
                                   *([options.group_column] if options.group_column else [])]))
    missing = [name for name in required if name not in frame]
    if missing:
        raise DesignError("Columns not found: " + ", ".join(missing))
    if any(any(char in name for char in ("\n", "\r", "\t", "\x00")) for name in required):
        raise DesignError("Selected column names must not contain line breaks, tabs or NUL characters.")
    selected = frame[required].copy()
    selected = selected.replace(r"^\s*$", np.nan, regex=True).replace([np.inf, -np.inf], np.nan)
    keep = selected.notna().all(axis=1)
    # Row numbers are one-based data rows, independent of the uploaded index.
    dropped = (np.flatnonzero(~keep.to_numpy()) + 1).tolist()
    selection_audit = [dict(item) for item in options.selection_audit]
    if selection_audit:
        try:
            included_records = [item for item in selection_audit if item["included"]]
            source_rows = [int(item["row"]) for item in selection_audit]
            if len(included_records) != len(frame) or len(source_rows) != len(set(source_rows)) or min(source_rows) < 1:
                raise ValueError("Selection audit does not match the input observations")
            for position in np.flatnonzero(~keep.to_numpy()):
                record = included_records[position]
                record["included"] = False
                record["reason"] = "; ".join(filter(None, [record.get("reason", ""), "Missing selected model value"]))
            dropped = [int(item["row"]) for item in selection_audit if not item["included"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise DesignError("Selection audit must match the filtered table in source order.") from exc
    selected = selected.loc[keep].reset_index(drop=True)
    if selected.empty:
        raise DesignError("No complete observations remain for the selected columns.")
    subjects, visits, labels = _subject_labels(selected, options)
    warnings = []
    missing_count = int((~keep).sum())
    if missing_count:
        warnings.append(f"Dropped {missing_count} row(s) missing one or more selected values; all files use the same remaining order.")
    if options.kind == "randomise" and len(subjects) != len(set(subjects)):
        raise DesignError("Randomise browser designs require one observation per subject. Use SwE for repeated visits.")
    if options.kind == "swe" and (len(set(subjects)) < 2 or len(set(subjects)) == len(subjects)):
        raise DesignError("SwE requires at least two subjects and repeated observations.")
    main = selected[options.main_variable]
    levels = _levels(main, options.reference_levels.get(options.main_variable))
    if len(levels) < 2:
        raise DesignError("The main variable needs at least two distinct values.")
    if len({_text(level) for level in levels}) != len(levels) or any(any(char in _text(level) for char in ("\n", "\r", "\t", "\x00")) for level in levels):
        raise DesignError("Main-variable levels need distinct single-line labels.")
    main_numeric = _numeric(main)
    main_categorical = _categorical(main, options, options.main_variable)
    mode = options.analysis_type
    if mode == "auto":
        mode = "group" if len(levels) == 2 else ("anova" if main_categorical else "continuous")
    if mode == "continuous" and main_numeric is None:
        raise DesignError("A continuous main variable must contain numbers.")
    if mode == "continuous" and options.kind == "swe" and main_categorical:
        raise DesignError("The SwE report treats this low-cardinality variable as a factor. Choose automatic/group coding to keep voxelwise and report models consistent.")
    if mode == "group" and len(levels) != 2:
        raise DesignError("A two-group model needs exactly two main-variable levels.")
    if mode == "anova" and len(levels) < 3:
        raise DesignError("ANOVA needs at least three groups; select a two-group model instead.")
    if mode in ("group", "anova") and main_numeric is not None and len(levels) > 12:
        raise DesignError("This grouped model has more than 12 levels; confirm that the main variable is categorical.")

    design = pd.DataFrame(index=selected.index)
    coding = {}
    main_columns = []
    if options.kind == "randomise" and mode in ("group", "anova"):
        for level in levels:
            column = f"{options.main_variable}[{_text(level)}]"
            design[column] = (main == level).astype(float)
            main_columns.append(column)
        coding[options.main_variable] = {"type": "group cell means", "reference": _text(levels[0]),
                                        "levels": [_text(level) for level in levels]}
    else:
        design["Intercept"] = 1.0
        if mode in ("group", "anova"):
            for level in levels[1:]:
                column = f"{options.main_variable}[{_text(level)}]"
                design[column] = (main == level).astype(float)
                main_columns.append(column)
            coding[options.main_variable] = {"type": "treatment", "reference": _text(levels[0]), "levels": [_text(level) for level in levels]}
            warnings.append(f"{options.main_variable} is treatment coded with reference {_text(levels[0])}; each nonreference level has positive/negative contrasts.")
        else:
            column = options.main_variable
            if options.kind == "swe" and column == "visit":
                column = "visit_effect"
            design[column] = main_numeric
            main_columns.append(column)
            coding[options.main_variable] = {"type": "continuous", "centered": False}
    for name in nuisance:
        values = selected[name]
        if _categorical(values, options, name):
            categories = _levels(values, options.reference_levels.get(name))
            if len(categories) < 2:
                raise DesignError(f"Covariate {name!r} is constant; remove it.")
            if len({_text(level) for level in categories}) != len(categories) or any(any(char in _text(level) for char in ("\n", "\r", "\t", "\x00")) for level in categories):
                raise DesignError(f"Covariate {name!r} needs distinct single-line category labels.")
            for level in categories[1:]:
                column = f"{name}[{_text(level)}]"
                if column in design:
                    raise DesignError(f"Generated design column collides with another column: {column}.")
                design[column] = (values == level).astype(float)
            coding[name] = {"type": "treatment", "reference": _text(categories[0]), "levels": [_text(level) for level in categories]}
        else:
            numeric = _numeric(values)
            if numeric is None or not np.isfinite(numeric).all():
                raise DesignError(f"Covariate {name!r} has invalid numeric values.")
            column = "visit_effect" if options.kind == "swe" and name == "visit" else name
            if column in design:
                raise DesignError(f"Duplicate design column: {column}.")
            design[column] = numeric - numeric.mean() if options.demean_covariates else numeric
            coding[name] = {"type": "continuous", "centered": bool(options.demean_covariates),
                            "mean": float(numeric.mean())}
    values = design.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise DesignError("The design contains non-finite numeric values.")
    if len(selected) <= len(design.columns):
        raise DesignError("The model needs more observations than design columns.")
    if np.linalg.matrix_rank(values) != len(design.columns):
        raise DesignError("The design is rank deficient; remove duplicate/collinear predictors or empty groups.")
    condition = float(np.linalg.cond(values))
    if condition > 1e6:
        warnings.append(f"The design is poorly conditioned ({condition:.3g}); inspect covariate scaling and collinearity.")

    contrasts = []
    contrast_labels = []
    ftests = None
    if options.kind == "randomise" and mode in ("group", "anova"):
        for column in main_columns[1:]:
            row = np.zeros(len(design.columns))
            row[design.columns.get_loc(main_columns[0])] = 1
            row[design.columns.get_loc(column)] = -1
            contrasts.append(row)
            contrast_labels.append(f"{main_columns[0]} minus {column}")
            if mode == "group":
                contrasts.append(-row)
                contrast_labels.append(f"{column} minus {main_columns[0]}")
        if mode == "anova":
            ftests = np.ones((1, len(contrasts)))
            warnings.append("ANOVA is an omnibus equality-of-group-means test using independent group differences; it does not test all groups against zero.")
    else:
        for column in main_columns:
            row = np.zeros(len(design.columns))
            row[design.columns.get_loc(column)] = 1
            contrasts.extend([row, -row])
            contrast_labels.extend([f"{column} > 0", f"{column} < 0"])
    if options.contrasts is not None:
        contrasts, contrast_labels = validate_contrasts(options.contrasts, design, options.contrast_labels)
        if options.kind == "randomise":
            warnings.append("Custom weights define the voxelwise hypotheses. Regional main-variable correlations/group comparisons are descriptive summaries and do not reproduce these adjusted GLM contrasts.")
        if ftests is not None:
            warnings.append("Custom contrasts replace the automatic omnibus ANOVA test; these hypotheses run as individual t-contrasts.")
            ftests = None
    else:
        contrasts, contrast_labels = validate_contrasts(contrasts, design, contrast_labels)
    groups = selected[options.group_column] if options.group_column else pd.Series([1] * len(selected))
    if options.kind == "swe":
        # Modified SwE assumes its variance groups are constant per subject.
        pairs = pd.DataFrame({"subject": subjects, "group": groups.to_numpy()})
        if pairs.groupby("subject")["group"].nunique().max() > 1:
            raise DesignError("The SwE group assignment must be constant for each subject.")
        encoded = []
        for vector in (subjects, visits, groups.tolist()):
            mapping = {value: index + 1 for index, value in enumerate(sorted(set(vector), key=str))}
            encoded.append([mapping[value] for value in vector])
        sub_matrix = np.asarray(encoded, dtype=int).T
        exported = pd.DataFrame({"subject": subjects, "visit": visits})
        if options.group_column:
            exported["group"] = groups.map(lambda value: f"Group {_text(value)}").to_numpy()
        for name in design:
            if name in exported:
                raise DesignError(f"Model column {name!r} conflicts with reserved report identifiers.")
            exported[name] = design[name]
    else:
        sub_matrix = None
        exported = design.copy()
    return {"design": design, "contrasts": np.asarray(contrasts, dtype=float),
            "ftests": ftests, "sub_matrix": sub_matrix, "exported": exported,
            "subjects": labels, "dropped_rows": dropped, "main_variables": main_columns,
            "warnings": warnings, "analysis_type": mode, "kind": options.kind,
            "coding": coding, "contrast_labels": contrast_labels,
            "custom_contrasts": options.contrasts is not None,
            "selection_audit": selection_audit,
            "nobs": len(design), "columns": list(design.columns)}


def _vest(matrix: np.ndarray, rows_header: str = "NumPoints") -> str:
    body = io.StringIO()
    np.savetxt(body, np.atleast_2d(matrix), fmt="%.12g")
    # Space-separated headers also support the legacy masked-confirmation reader.
    return f"/NumWaves {matrix.shape[1]}\n/{rows_header} {matrix.shape[0]}\n/Matrix\n" + body.getvalue()


def write_design(frame: pd.DataFrame, options: DesignOptions, global_dir: Path,
                 overwrite: bool = False) -> dict:
    """Write a matched FSL/report/subject set only after validation succeeds."""
    result = preview_design(frame, options)
    root = Path(global_dir).expanduser().resolve()
    output = root / "Results" / options.analysis_name
    list_dir = root / "Listes"
    paths = {"mat": output / f"{options.matrix_name}.mat",
             "con": output / f"{options.matrix_name}.con",
             "csv": output / f"{options.matrix_name}.csv",
             "main_variables": output / f"main_variable_{options.matrix_name}.txt",
             "subjects": list_dir / f"List_{options.analysis_name}.txt",
             "metadata": output / f"{options.matrix_name}_browser-design.json"}
    if result["ftests"] is not None:
        paths["fts"] = output / f"{options.matrix_name}.fts"
    if result["sub_matrix"] is not None:
        paths["sub"] = output / f"{options.matrix_name}.sub"
        paths["sub_plain"] = output / f"{options.matrix_name}_design_sub.txt"
    if result["selection_audit"]:
        paths["selection"] = output / f"{options.matrix_name}_selection.tsv"
    subject_path = paths["subjects"]
    existing_subjects = subject_path.read_text(encoding="utf-8").splitlines() if subject_path.exists() else None
    if existing_subjects is not None and existing_subjects != result["subjects"]:
        raise DesignError("This analysis already has a different subject/visit order. Choose a new analysis name so existing 4D data remain aligned.")
    existing = [path for key, path in paths.items() if key != "subjects" and path.exists()]
    if existing and not overwrite:
        raise DesignError("Existing design/list files would be replaced. Select overwrite explicitly or choose new analysis/matrix names.")
    stale_fts = output / f"{options.matrix_name}.fts"
    if result["ftests"] is None and stale_fts.exists():
        if not overwrite:
            raise DesignError("A previous ANOVA .fts file exists; choose a new matrix name or allow replacement.")
    output.mkdir(parents=True, exist_ok=True)
    list_dir.mkdir(parents=True, exist_ok=True)
    payloads = {"mat": _vest(result["design"].to_numpy()),
                "con": _vest(result["contrasts"], "NumContrasts"),
                "csv": result["exported"].to_csv(sep="\t", index=False),
                "main_variables": "\n".join(result["main_variables"]) + "\n",
                "subjects": "\n".join(result["subjects"]) + "\n"}
    metadata = {key: result[key] for key in ("kind", "analysis_type", "nobs", "columns", "dropped_rows", "warnings", "coding", "contrast_labels", "selection_audit", "custom_contrasts")}
    metadata["analysis_name"] = options.analysis_name
    metadata["matrix_name"] = options.matrix_name
    metadata["subject_order"] = result["subjects"]
    metadata["contrasts"] = result["contrasts"].tolist()
    payloads["metadata"] = json.dumps(metadata, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if result["selection_audit"]:
        payloads["selection"] = pd.DataFrame(result["selection_audit"]).to_csv(sep="\t", index=False)
    if result["ftests"] is not None:
        payloads["fts"] = _vest(result["ftests"], "NumContrasts")
    if result["sub_matrix"] is not None:
        payloads["sub"] = _vest(result["sub_matrix"])
        plain = io.StringIO()
        np.savetxt(plain, result["sub_matrix"], fmt="%d")
        payloads["sub_plain"] = plain.getvalue()
    for key, content in payloads.items():
        if key == "subjects" and existing_subjects is not None:
            continue
        paths[key].write_text(content, encoding="utf-8")
    if result["ftests"] is None and stale_fts.exists():
        stale_fts.unlink()
    result["paths"] = paths
    return result
