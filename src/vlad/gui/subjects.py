"""Read study visits from BIDS covariates or the original VLAD text format."""

from __future__ import annotations

import csv
import io
from pathlib import Path
import re


_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")


def detect_identifier_columns(frame) -> tuple[str, str]:
    """Prefer subject_id, accepting the BIDS participant_id spelling as well."""
    columns = list(frame.columns) if hasattr(frame, "columns") else list(frame)
    lowered = {str(column).strip().lower(): column for column in columns}
    if len(lowered) != len(columns):
        raise ValueError("The covariate table has duplicate column names.")
    subject = next((lowered[name] for name in ("subject_id", "participant_id") if name in lowered), None)
    session = lowered.get("session_id")
    if subject is None or session is None:
        raise ValueError("The covariate table needs subject_id (or participant_id) and session_id columns.")
    return subject, session


def visit_label(subject: str, session: str) -> str:
    subject, session = str(subject).strip(), str(session).strip()
    if subject.startswith("sub-"):
        subject = subject[4:]
    if session.startswith("ses-"):
        session = session[4:]
    if any(not _LABEL.fullmatch(value) or value.lower() in {"n/a", "nan", "none"} for value in (subject, session)):
        raise ValueError("Subject and session identifiers must be nonempty letters, numbers, dots or dashes; missing identifiers are not allowed.")
    return f"{subject}_{session}"


def read_subject_visits(source: Path | str | bytes, filename: str | None = None) -> list[str]:
    """Keep table order and leading zeros; reject duplicate or missing visits."""
    if isinstance(source, bytes):
        text = source.decode("utf-8-sig")
    else:
        path = Path(source).expanduser()
        filename = filename or path.name
        text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError("The subject-visit file is empty.")
    table = (Path(filename or "").suffix.lower() in {".tsv", ".csv"} or "\t" in lines[0] or "," in lines[0])
    if table:
        delimiter = "\t" if "\t" in lines[0] else ","
        reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter)
        subject, session = detect_identifier_columns(reader.fieldnames or [])
        entries = []
        for number, row in enumerate(reader, 2):
            if None in row:
                raise ValueError(f"Row {number} has more fields than the covariate header.")
            try:
                entries.append(visit_label(row.get(subject) or "", row.get(session) or ""))
            except ValueError as exc:
                raise ValueError(f"Invalid identifiers at row {number}: {exc}") from exc
    else:
        entries = []
        for number, line in enumerate(lines, 1):
            parts = line.strip().split("_")
            if len(parts) != 2:
                raise ValueError(f"Line {number} needs a SUBJECT_VISIT identifier, or provide a covars.tsv table with subject_id and session_id.")
            entries.append(visit_label(*parts))
    if not entries:
        raise ValueError("The subject-visit file has no rows.")
    if len(set(entries)) != len(entries):
        raise ValueError("The subject-visit file contains duplicate subject/session pairs.")
    return entries
