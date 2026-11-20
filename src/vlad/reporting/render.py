"""Standalone HTML, statistical JSON, Feather and SVG report exports."""

from __future__ import annotations

import base64
import html
import importlib.metadata
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .io import ReportError, ReportRequest, input_manifest, load_report, read_fsl_matrix
from .mixed import fit_random_intercept
from .plots import design_figure, mixed_figure, randomise_figure
from .statistics import analyse_continuous, analyse_groups, describe_design


def _clean(value):
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_clean(item) for item in value]
    if value is pd.NA or value is None:
        return None
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _format(value) -> str:
    if value is None or value is pd.NA:
        return "—"
    if isinstance(value, (float, np.floating)):
        return f"{value:.5g}" if math.isfinite(value) else "—"
    if isinstance(value, (list, dict)):
        return json.dumps(_clean(value), ensure_ascii=False)
    return str(value)


def _table(rows: list[dict]) -> str:
    if not rows:
        return "<p>No estimable values.</p>"
    columns = list(dict.fromkeys(key for row in rows for key in row))
    heading = "".join(f"<th>{_escape(key.replace('_', ' '))}</th>" for key in columns)
    body = "".join("<tr>" + "".join(f"<td>{_escape(_format(row.get(key)))}</td>" for key in columns) + "</tr>" for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>'


def _image(content: bytes | str, label: str, mime: str = "image/svg+xml") -> str:
    encoded = base64.b64encode(content.encode() if isinstance(content, str) else content).decode("ascii")
    return f'<img alt="{_escape(label)}" src="data:{mime};base64,{encoded}">'


def _asset(path: Path, label: str) -> str | None:
    return _image(path.read_bytes(), label, "image/png") if path.is_file() else None


def _overview(summary: dict) -> str:
    def describe(values):
        if not values:
            return "—"
        if "levels" in values:
            return "; ".join(f"{level['level']}: {level['n']}/{level['denominator']} ({_format(level['percent'])}%)" for level in values["levels"])
        return f"{_format(values.get('mean'))} ({_format(values.get('sd'))}); n={values.get('n', 0)}"

    rows = []
    for entry in summary.get("variables", []):
        row = {"Variable": entry["name"], "Type": entry.get("type"), "Missing": entry.get("missing")}
        row["Overall"] = describe(entry.get("overall"))
        for level, values in entry.get("groups", {}).items():
            row[str(level)] = describe(values)
        test = entry.get("test") or {}
        row["Test"] = test.get("method")
        row["p"] = test.get("p")
        if test.get("note"):
            row["Notes"] = test["note"]
        rows.append(row)
    return _table(rows)


def _statistical_tables(statistics: dict) -> str:
    omitted = {"warnings", "coefficients", "covariance", "pairwise", "groups", "predictor_types", "categorical_levels"}
    scalar_rows = [{"Measure": key.replace("_", " "), "Value": value} for key, value in statistics.items() if key not in omitted and not isinstance(value, (list, dict))]
    output = _table(scalar_rows)
    for key, label in (("groups", "Group summaries"), ("coefficients", "Fixed effects"), ("pairwise", "Pairwise comparisons")):
        if statistics.get(key):
            output += f"<h3>{label}</h3>" + _table(statistics[key])
    for key in ("difference", "effect_size", "regression", "contrast"):
        if statistics.get(key):
            output += f"<h3>{_escape(key.replace('_', ' ').title())}</h3>" + _table([statistics[key]])
    return output


CUSTOM_RANDOMISE_NOTE = (
    "Custom contrast weights define the voxelwise GLM hypotheses. The regional "
    "main-variable correlations/group comparisons below are descriptive summaries "
    "of selected regions; they do not reproduce these adjusted GLM contrast tests."
)


def _custom_voxelwise_hypotheses(data, warnings: list[str]) -> dict | None:
    """Read the browser's hypothesis declaration without refitting a GLM."""
    if data.request.kind != "randomise":
        return None
    path = data.request.results_dir / f"{data.request.matrix_name}_browser-design.json"
    if not path.is_file():
        return None
    data.inputs.append(path)
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("Expected a JSON object")
    except (OSError, ValueError) as exc:
        warnings.append(f"Browser design metadata could not be read; custom hypothesis details are unavailable: {exc}")
        return None
    if metadata.get("custom_contrasts") is not True:
        return None
    warnings.append(CUSTOM_RANDOMISE_NOTE)
    columns = metadata.get("columns", list(data.design))
    columns_valid = isinstance(columns, list) and columns == list(data.design)
    if not columns_valid:
        warnings.append("Custom contrast column metadata does not match the report design; named weight details are unavailable.")
        columns = []
    weights = None
    contrast_path = data.request.results_dir / f"{data.request.matrix_name}.con"
    if contrast_path.is_file():
        data.inputs.append(contrast_path)
        try:
            values = read_fsl_matrix(contrast_path)
            if not columns_valid or values.shape[1] != len(columns):
                raise ReportError("Contrast/design column counts do not match")
            weights = values.tolist()
        except (OSError, ReportError) as exc:
            warnings.append(f"Custom voxelwise contrast weights could not be loaded: {exc}")
    labels = metadata.get("contrast_labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        labels = []
    if weights is not None and len(labels) != len(weights):
        labels = [f"Contrast {number}" for number in range(1, len(weights) + 1)]
    return {"custom_contrasts": True, "terms": columns, "labels": labels,
            "weights": weights, "regional_summary_role": "descriptive",
            "note": CUSTOM_RANDOMISE_NOTE}


def generate_report(request: ReportRequest) -> dict:
    data = load_report(request)
    request.export_dir.mkdir(parents=True, exist_ok=True)
    request.output.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    warnings = list(data.warnings)
    voxelwise_hypotheses = _custom_voxelwise_hypotheses(data, warnings)
    group = data.group_column
    summary_frame = data.combined[list(data.design.columns)].copy()
    if group and group not in summary_frame:
        summary_frame[group] = data.combined[group]
    exclude = [name for name in (data.subject_column, data.visit_column, "Intercept") if name]
    if request.kind == "randomise" and group:
        exclude += data.main_variables
    summary = describe_design(summary_frame, group=group, exclude=exclude)
    warnings.extend(summary.get("warnings", []))
    suffix = "_swe" if request.kind == "swe" else ""
    feather_path = request.export_dir / f"{request.analysis_name}_{request.matrix_name}_{request.report_date}{suffix}.feather"
    data.combined.reset_index(drop=True).to_feather(feather_path)
    regions = []
    sections = []
    for region in data.regions:
        diagnostics = []
        entry = {"column": region.column, "label": region.label, "source": str(region.source),
                 "contrast_id": region.contrast_id, "metabolite": region.metabolite,
                 "acquisition": region.acquisition, "mask": region.mask, "status": "ok"}
        if voxelwise_hypotheses:
            entry["summary_role"] = "descriptive"
            contrast_index = region.contrast_id - 1
            labels = voxelwise_hypotheses["labels"]
            weights = voxelwise_hypotheses["weights"]
            entry["voxelwise_contrast"] = {
                "id": region.contrast_id,
                "label": labels[contrast_index] if 0 <= contrast_index < len(labels) else f"Contrast {region.contrast_id}",
                "terms": voxelwise_hypotheses["terms"],
                "weights": weights[contrast_index] if weights is not None and 0 <= contrast_index < len(weights) else None,
            }
            diagnostics.append(CUSTOM_RANDOMISE_NOTE)
        try:
            if request.kind == "swe":
                contrast = data.contrast_matrix[region.contrast_id - 1] if data.contrast_matrix is not None else None
                predictors = [name for name in data.contrast_terms if name != "Intercept"]
                model = fit_random_intercept(data.combined, region.column, predictors, data.subject_column,
                                             contrast=contrast, contrast_terms=data.contrast_terms)
                statistics = dict(model.statistics)
                statistics["predictor_types"] = model.predictor_types
                statistics["categorical_levels"] = model.categorical_levels
                if contrast is not None and not statistics.get("contrast", {}).get("supported", False):
                    entry["status"] = "partial"
                    errors.append(f"{region.column}: requested contrast is unavailable: {statistics.get('contrast', {}).get('reason', 'no unique coefficient mapping')}")
                chosen = [name for name, weight in zip(data.contrast_terms, contrast) if abs(weight) > 1e-6 and name != "Intercept"] if contrast is not None else []
                predictor = next(iter(chosen or predictors), None)
                figure, predictions = mixed_figure(data, region, model, predictor)
                entry["predictions"] = predictions
                plot_name = f"{region.column}_lme.svg"
            else:
                if group:
                    statistics = analyse_groups(data.combined[region.column], data.combined[group], levels=data.group_levels)
                else:
                    statistics = analyse_continuous(data.combined[data.main_variables[0]], data.combined[region.column])
                figure = randomise_figure(data, region, statistics)
                plot_name = f"{region.column}{request.report_date}.svg"
            entry["statistics"] = statistics
            diagnostics.extend(statistics.get("warnings", []))
            plot_path = request.export_dir / plot_name
            plot_path.write_text(figure, encoding="utf-8")
            entry["plot"] = str(plot_path)
            figure_html = _image(figure, region.label)
        except (ValueError, np.linalg.LinAlgError) as exc:
            entry["status"] = "failed"
            entry["statistics"] = {}
            diagnostics.append(f"Unable to estimate this regional summary: {exc}")
            errors.append(f"{region.column}: {exc}")
            figure_html = ""
        entry["diagnostics"] = diagnostics
        brain = next((image for path in (request.results_dir / f"{region.column}.png", request.results_dir / f"{region.column.rsplit('__contrast', 1)[0]}.png") if (image := _asset(path, f"Brain snapshot: {region.label}"))), None)
        if brain is None:
            diagnostics.append("No existing brain snapshot was found for this region.")
        section = f'<section><h2>{_escape(region.label)}</h2>'
        if voxelwise_hypotheses:
            section += f'<p class="meta">Voxelwise hypothesis: {_escape(entry["voxelwise_contrast"]["label"])}. Regional statistics below are descriptive summaries.</p>'
        section += '<div class="figures">' + (brain or "") + figure_html + "</div>"
        section += _statistical_tables(entry["statistics"])
        if diagnostics:
            section += '<ul class="notes">' + "".join(f"<li>{_escape(note)}</li>" for note in diagnostics) + "</ul>"
        section += "</section>"
        sections.append(section)
        regions.append(entry)
    versions = {}
    for package in ("vlad-neuroimaging", "numpy", "pandas", "scipy", "matplotlib", "pyarrow"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "source checkout"
    manifest = _clean({"schema_version": 1, "backend": "python", "kind": request.kind,
                       "analysis_name": request.analysis_name, "matrix_name": request.matrix_name,
                       "modality": request.modality, "permutations": request.permutations,
                       "date": request.report_date, "confirmation": request.confirmation,
                       "status": "partial" if errors else "complete", "n_subject_rows": len(data.design),
                       "subject_column": data.subject_column, "visit_column": data.visit_column,
                       "design_columns": list(data.design), "contrast_terms": data.contrast_terms,
                       "analysis_type": data.analysis_type, "summary": summary, "regions": regions,
                       "warnings": warnings, "errors": errors, "versions": versions,
                       "inputs": input_manifest(data), "outputs": {"html": request.output, "feather": feather_path,
                                                                     "json": request.output.with_suffix(".json")}})
    hypotheses_html = ""
    if voxelwise_hypotheses:
        manifest["voxelwise_hypotheses"] = voxelwise_hypotheses
        weights = voxelwise_hypotheses["weights"]
        hypothesis_rows = []
        for index, label in enumerate(voxelwise_hypotheses["labels"]):
            row = {"Contrast": index + 1, "Hypothesis": label}
            if weights is not None and index < len(weights):
                row.update(dict(zip(voxelwise_hypotheses["terms"], weights[index])))
            hypothesis_rows.append(row)
        hypotheses_html = '<section><h2>Custom voxelwise hypotheses</h2>'
        hypotheses_html += f'<p>{_escape(CUSTOM_RANDOMISE_NOTE)}</p>' + _table(hypothesis_rows) + '</section>'
    overview_images = []
    for name, correlation in (("visualisation", False), ("correlation_matrix", True)):
        path = request.results_dir / f"{request.matrix_name}_{name}.png"
        overview_images.append(_asset(path, name.replace("_", " ")) or _image(design_figure(data.design, correlation), name.replace("_", " ")))
    note_list = "".join(f"<li>{_escape(note)}</li>" for note in warnings)
    empty = "<section><h2>No significant regions</h2><p>The result list is empty. Design summaries and data exports are still available.</p></section>" if not regions else ""
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VLAD {_escape(request.kind)} report — {_escape(request.analysis_name)} / {_escape(request.matrix_name)}</title>
<style>
:root{{font-family:system-ui,sans-serif;color:#183448;background:#edf3f6;line-height:1.5}}body{{max-width:1200px;margin:2rem auto;padding:0 1rem}}header,section{{background:white;padding:1.5rem 2rem;margin:1rem 0;border-radius:8px}}h1,h2,h3{{line-height:1.25}}h1{{font-size:1.8rem}}h2{{font-size:1.3rem;color:#126c8a}}h3{{font-size:1rem}}.meta,.notes{{color:#4a606e;font-size:.9rem}}.figures{{display:flex;flex-wrap:wrap;gap:1rem;align-items:center}}img{{max-width:100%;height:auto}}.figures img{{flex:1;min-width:240px;width:46%;object-fit:contain}}.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;font-size:.85rem;width:100%;margin:1rem 0}}th,td{{text-align:left;vertical-align:top;padding:.55rem;border-bottom:1px solid #d9e4eb}}th{{background:#f3f7fa}}td{{max-width:30rem;overflow-wrap:anywhere}}.badge{{display:inline-block;padding:.2rem .6rem;background:#e6f3f1;border-radius:4px;font-size:.85rem}}footer{{padding:1rem;color:#4a606e;font-size:.8rem}}
</style></head><body><header><span class="badge">Python · {_escape(request.kind)} · {_escape(manifest['status'])}</span>
<h1>VLAD report: {_escape(request.analysis_name)}</h1><p class="meta">Matrix: {_escape(request.matrix_name)} · Modality: {_escape(request.modality)} · {request.permutations} permutations · {_escape(request.report_date)}{' · confirmation run' if request.confirmation else ''}</p>
<p>Regional statistics describe areas selected by the voxelwise analysis. They are secondary exploratory summaries of those selected regions.</p></header>
{hypotheses_html}<section><h2>Design summary</h2><p>{len(data.design)} design rows · {len(regions)} regional results</p><div class="figures">{''.join(overview_images)}</div>{_overview(summary)}</section>
{empty}{''.join(sections)}<section><h2>Analysis notes</h2><ul>{note_list}</ul><p>Model methods, effect sizes, intervals, package versions, input hashes and prediction values are recorded in the accompanying JSON file.</p></section>
<footer>VLAD Python reporting. HTML includes its images and styles for viewing after copying.</footer></body></html>'''
    request.output.write_text(document, encoding="utf-8")
    request.output.with_suffix(".json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return manifest
