"""Browse TemplateFlow's installed catalogue without downloading on UI reruns."""

from __future__ import annotations

from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from vlad.paths import project_root


def _cache_home() -> Path:
    override = os.environ.get("TEMPLATEFLOW_HOME")
    return Path(override or project_root() / ".cache" / "templateflow").expanduser().resolve()


def _cache_roots() -> list[Path]:
    return list(dict.fromkeys((_cache_home(), Path.home() / ".cache" / "templateflow")))


@lru_cache(maxsize=1)
def _bundled_catalogue() -> tuple[tuple[str, ...], dict]:
    """Read the official client's bundled skeleton, without importing its API."""
    spec = importlib.util.find_spec("templateflow")
    if spec is None or spec.origin is None:
        raise RuntimeError("TemplateFlow is not installed. Run ./install.sh to include the template catalogue.")
    skeleton = Path(spec.origin).parent / "conf" / "templateflow-skel.zip"
    if not skeleton.is_file():
        raise RuntimeError("The TemplateFlow catalogue is unavailable; reinstall the locked dependencies.")
    with zipfile.ZipFile(skeleton) as archive:
        names = tuple(name for name in archive.namelist() if name.endswith((".nii", ".nii.gz")))
        descriptions = {name: json.loads(archive.read(name)) for name in archive.namelist()
                        if name.endswith("template_description.json")}
    return names, descriptions


def _entities(name: str) -> dict:
    stem = Path(name).name.removesuffix(".gz").removesuffix(".nii")
    values = dict(token.split("-", 1) for token in stem.split("_") if "-" in token)
    values["suffix"] = stem.split("_")[-1]
    return values


def _anatomical(name: str) -> bool:
    values = _entities(name)
    return values.get("suffix") in {"T1w", "T2w"} and not any(key in values for key in ("desc", "atlas", "hemi", "label", "space", "den"))


def _files(template: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", template):
        raise ValueError("Choose a valid TemplateFlow template identifier.")
    names = {name for name in _bundled_catalogue()[0] if name.startswith(f"tpl-{template}/") and _anatomical(name)}
    for root in _cache_roots():
        folder = root / f"tpl-{template}"
        if folder.is_dir():
            names.update(str(path.relative_to(root)) for path in folder.rglob("*.nii*")
                         if path.name.endswith((".nii", ".nii.gz")) and _anatomical(path.name))
    return sorted(names)


def list_templates() -> list[str]:
    names = {name.split("/")[0][4:] for name in _bundled_catalogue()[0] if _anatomical(name)}
    for root in _cache_roots():
        if root.is_dir():
            names.update(path.name[4:] for path in root.glob("tpl-*") if path.is_dir() and _files(path.name[4:]))
    return sorted(names)


def template_options(template: str) -> dict:
    files = _files(template)
    if not files:
        raise ValueError("This template has no whole-head T1w/T2w NIfTI reference in the installed catalogue.")
    metadata = _bundled_catalogue()[1].get(f"tpl-{template}/template_description.json", {})
    for root in reversed(_cache_roots()):
        path = root / f"tpl-{template}" / "template_description.json"
        if path.is_file():
            metadata = json.loads(path.read_text())
    values = [_entities(name) for name in files]
    return {"name": metadata.get("Name", template), "metadata": metadata,
            "resolutions": sorted({value["res"] for value in values if "res" in value}),
            "cohorts": sorted({value["cohort"] for value in values if "cohort" in value}),
            "suffixes": sorted({value["suffix"] for value in values}),
            "files": files}


def _same_label(left, right) -> bool:
    left, right = str(left), str(right)
    return int(left) == int(right) if left.isdigit() and right.isdigit() else left == right


def _validate_reference(path: Path) -> Path:
    import nibabel as nib
    import numpy as np
    try:
        image = nib.load(path)
        if len(image.shape) != 3 or not np.isfinite(image.affine).all():
            raise ValueError("The reference must be a 3D NIfTI image with a finite affine.")
    except (OSError, nib.filebasedimages.ImageFileError) as exc:
        raise ValueError(f"The template is not a readable NIfTI image: {path}") from exc
    return path.resolve()


def fetch_template(template: str, resolution=None, cohort=None, suffix: str = "T1w") -> Path:
    """Fetch one explicitly selected reference; reuse either local cache first."""
    if suffix not in {"T1w", "T2w"}:
        raise ValueError("Choose a T1w or T2w anatomical reference.")
    candidates = []
    for name in _files(template):
        values = _entities(name)
        if values["suffix"] != suffix:
            continue
        if resolution not in (None, "") and not _same_label(values.get("res", ""), resolution):
            continue
        if cohort not in (None, "") and not _same_label(values.get("cohort", ""), cohort):
            continue
        candidates.append(name)
    if len(candidates) != 1:
        raise ValueError(f"The selection matches {len(candidates)} anatomical files. Select a specific template resolution and cohort.")
    for root in _cache_roots():
        path = root / candidates[0]
        if path.is_file() and path.stat().st_size:
            return _validate_reference(path)
    cache = _cache_home()
    env = os.environ.copy()
    env.update(TEMPLATEFLOW_HOME=str(cache), TEMPLATEFLOW_AUTOUPDATE="false", TEMPLATEFLOW_USE_DATALAD="false")
    # Use a separate client process so a desktop app's imported global cache cannot
    # redirect a browser selection into a different cache or DataLad dataset.
    script = """import json, sys
from templateflow import TemplateFlowClient
query = json.loads(sys.argv[2])
client = TemplateFlowClient(root=sys.argv[1], autoupdate=False, use_datalad=False)
path = client.get(sys.argv[3], **query)
print('VLAD_TEMPLATE=' + json.dumps(str(path)))
"""
    entity = _entities(candidates[0])
    query = {"suffix": suffix, "desc": None, "extension": ".nii.gz" if candidates[0].endswith(".gz") else ".nii"}
    for key, name in (("res", "resolution"), ("cohort", "cohort")):
        if key in entity:
            query[name] = int(entity[key]) if entity[key].isdigit() else entity[key]
    try:
        result = subprocess.run([sys.executable, "-c", script, str(cache), json.dumps(query), template],
                                env=env, capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"TemplateFlow could not retrieve the reference: {exc}") from exc
    lines = [line[len("VLAD_TEMPLATE="):] for line in result.stdout.splitlines() if line.startswith("VLAD_TEMPLATE=")]
    if result.returncode or not lines:
        raise RuntimeError("TemplateFlow could not retrieve the reference. Check network access or choose a custom NIfTI path. " + result.stderr[-1500:])
    path = Path(json.loads(lines[-1]))
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError("TemplateFlow did not return a nonempty anatomical image.")
    return _validate_reference(path)
