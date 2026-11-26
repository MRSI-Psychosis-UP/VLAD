"""Browse TemplateFlow's installed catalogue without downloading on UI reruns."""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from urllib.request import urlopen
import zipfile

from vlad.paths import project_root


FSL_WM_URL = 'https://git.fmrib.ox.ac.uk/fsl/mist/-/raw/2512.2/data/masks/wm_thr75.nii.gz'
FSL_WM_SHA256 = 'f784af57a9874ab7f129579ccf51c7b100ca118a11337c71120dfb7e5c238151'
TEMPLATEFLOW_ARCHIVE = 'https://templateflow.s3.amazonaws.com'


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


def list_anatomical_images() -> list[str]:
    return [name for template in list_templates() for name in _files(template)]


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


def validate_reference_anatomy(path: Path) -> Path:
    import nibabel as nib
    import numpy as np
    path = Path(path).expanduser()
    if not path.is_file() or not path.name.lower().endswith(('.nii', '.nii.gz')):
        raise ValueError(f'Choose an existing anatomical NIfTI (.nii or .nii.gz): {path}')
    try:
        image = nib.load(path)
        if len(image.shape) != 3 or not np.isfinite(image.affine).all():
            raise ValueError("The reference must be a 3D NIfTI image with a finite affine.")
    except (OSError, nib.filebasedimages.ImageFileError) as exc:
        raise ValueError(f"The template is not a readable NIfTI image: {path}") from exc
    return path.resolve()


def _cached_resource(name):
    for root in _cache_roots():
        path = root / name
        if path.is_file() and path.stat().st_size:
            try:
                return validate_reference_anatomy(path)
            except (ValueError, OSError):
                continue
    return None


def _fetch_resource(name):
    import requests

    if path := _cached_resource(name):
        return path
    path = _cache_home() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = '.nii.gz' if name.endswith('.gz') else '.nii'
    # Use the exact catalogue key. A partially populated cache may not have a
    # placeholder for the file, and an entity query may return multiple files.
    with NamedTemporaryFile(dir=path.parent, suffix=suffix, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with requests.get(f'{TEMPLATEFLOW_ARCHIVE}/{quote(name)}', stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            with temporary.open('wb') as stream:
                for block in response.iter_content(chunk_size=256 * 1024):
                    stream.write(block)
        validate_reference_anatomy(temporary)
        temporary.replace(path)
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f'TemplateFlow could not load {path.name}: {exc}') from exc
    finally:
        temporary.unlink(missing_ok=True)
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
    return _fetch_resource(candidates[0])


def fetch_anatomical_image(template: str, name: str) -> Path:
    """Fetch the exact anatomical image selected in a template catalogue."""
    if name not in _files(template):
        raise ValueError('Choose an anatomical image listed for the selected template.')
    return _fetch_resource(name)


def _tissue_resources(template: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", template):
        return {}
    pairs = {}
    prefix = f"tpl-{template}/tpl-{template}_"
    for name in _bundled_catalogue()[0]:
        if not name.startswith(prefix):
            continue
        fields = _entities(name)
        if (fields["suffix"] == "probseg" and fields.get("label") in {"GM", "WM"}
                and fields.keys() <= {"tpl", "res", "label", "suffix"}):
            pairs.setdefault(fields.get("res", ""), {})[fields["label"].lower()] = name
    return pairs


def tissue_probabilities(template: str, *, download=False) -> dict:
    """Find exact-space, cohort-independent GM and WM probability maps."""
    pairs = _tissue_resources(template)
    for _, pair in sorted(pairs.items()):
        if len(pair) == 2:
            if download:
                return {key: str(_fetch_resource(pair[key])) for key in ("gm", "wm")}
            cached = {key: _cached_resource(pair[key]) for key in ("gm", "wm")}
            if all(cached.values()):
                return {key: str(path) for key, path in cached.items()}
    if download:
        raise ValueError(f"TemplateFlow has no matching GM/WM probability pair for {template}. Choose custom masks for this space.")
    return {}


def white_matter_probability(template: str, *, download=False) -> Path | None:
    """Find a TemplateFlow WM map independently of any GM map."""
    candidates = [pair['wm'] for _, pair in sorted(_tissue_resources(template).items()) if 'wm' in pair]
    for name in candidates:
        if cached := _cached_resource(name):
            return cached
    if download:
        if not candidates:
            raise ValueError(f'TemplateFlow has no WM probability map for {template}. Choose a matching binary WM mask.')
        return _fetch_resource(candidates[0])
    return None


def fsl_white_matter(*, download=False) -> Path | None:
    """Cache FSL MIST's binary MNI152 WM mask from a pinned release."""
    path = _cache_home() / 'fsl' / 'mist-2512.2' / 'wm_thr75.nii.gz'
    if path.is_file() and sha256(path.read_bytes()).hexdigest() == FSL_WM_SHA256:
        return path
    if not download:
        return None
    installed = Path(os.environ.get('FSLDIR', '~/fsl')).expanduser() / 'data/mist/masks/wm_thr75.nii.gz'
    data = installed.read_bytes() if installed.is_file() else b''
    if sha256(data).hexdigest() != FSL_WM_SHA256:
        with urlopen(FSL_WM_URL, timeout=30) as response:
            data = response.read(100_000)
    if sha256(data).hexdigest() != FSL_WM_SHA256:
        raise ValueError('The downloaded FSL white-matter mask failed its checksum.')
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix='.nii.gz', delete=False) as stream:
        temp = Path(stream.name)
    try:
        temp.write_bytes(data)
        validate_reference_anatomy(temp)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return path
