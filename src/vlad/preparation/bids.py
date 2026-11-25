"""Find BIDS derivative scalar maps and stage VLAD's prepared inputs.

Selection uses filename entities, never directory order. Source derivatives are
opened read-only; generated maps, masks and provenance belong to the study.
"""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

import nibabel as nib
import numpy as np
from nibabel.processing import resample_from_to

from vlad.gui.cohorts import atomic_text, file_lock, read_assignments, _shell_file
from vlad.gui.subjects import read_subject_visits


class PreparationError(ValueError):
    """A derivative selection cannot safely form an analysis population."""


@dataclass(frozen=True)
class ImageEntry:
    path: Path
    entities: dict
    suffix: str


def image_entities(path: Path) -> ImageEntry:
    name = path.name
    name = name[:-7] if name.endswith('.nii.gz') else name[:-4]
    body, _, suffix = name.rpartition('_')
    entities = dict(re.findall(r'(?:^|_)([A-Za-z0-9]+)-(.+?)(?=_[A-Za-z0-9]+-|$)', body))
    return ImageEntry(path, entities, suffix)


def _list(value) -> list[str]:
    return list(value) if isinstance(value, (list, tuple)) else str(value or '').split()


def _prefix(modality: str) -> str:
    result = modality.upper()
    if result == 'DWI':
        result = 'DTI'
    if result not in {'MRSI', 'DTI', 'STRUCTURAL'}:
        raise PreparationError('Choose MRSI, DTI/DWI or STRUCTURAL.')
    return result


def _subjects(path: Path) -> list[str]:
    try:
        return read_subject_visits(path)
    except ValueError as exc:
        raise PreparationError(str(exc)) from exc


def _grid(image: nib.spatialimages.SpatialImage, reference: nib.spatialimages.SpatialImage, label: str) -> None:
    if len(image.shape) != 3:
        raise PreparationError(f'{label}: expected a scalar 3D image, found shape {image.shape}.')
    if image.shape != reference.shape or not np.allclose(image.affine, reference.affine, rtol=0, atol=1e-4):
        raise PreparationError(f'{label}: image grid/affine differs from the selected scalar maps. Resample upstream first.')


def _binary(image, reference, label):
    _grid(image, reference, label)
    data = image.get_fdata(dtype=np.float32)
    if not np.isfinite(data).all() or not np.isin(data, [0, 1]).all():
        raise PreparationError(f'{label}: the supplied population mask must contain only finite binary 0/1 values.')
    if not np.any(data):
        raise PreparationError(f'{label}: the population mask is empty.')
    return data.astype(np.uint8)


def quality_mask(signal, crlb, fwhm, snr, crlb_max=20.0, fwhm_max=0.1, snr_min=10.0):
    """Apply the three strict thresholds; nonfinite values always fail QC."""
    if not (math.isfinite(crlb_max) and crlb_max > 0 and math.isfinite(fwhm_max) and fwhm_max > 0
            and math.isfinite(snr_min) and snr_min >= 0):
        raise PreparationError('Quality limits must be finite: CRLB/FWHM positive, SNR nonnegative.')
    arrays = [np.asarray(item) for item in (signal, crlb, fwhm, snr)]
    if len({item.shape for item in arrays}) != 1:
        raise PreparationError('Signal and quality maps must have the same shape.')
    signal, crlb, fwhm, snr = arrays
    return (np.isfinite(signal) & np.isfinite(crlb) & np.isfinite(fwhm) & np.isfinite(snr)
            & (crlb < crlb_max) & (fwhm < fwhm_max)
            & (snr > snr_min)).astype(np.uint8)


def _select(candidates, label, issues):
    if not candidates:
        issues['missing'].append(f'{label}: no matching image.')
        return None
    if len(candidates) > 1:
        issues['ambiguous'].append(f'{label}: {len(candidates)} matches; specify resolution/run/acquisition or a narrower selector. '
                                   + '; '.join(str(item.path) for item in candidates[:4]))
        return None
    return candidates[0]


def discover_inputs(settings: dict, modality: str, subjects: list[str]) -> dict:
    """Preview exact source selections without creating or changing files."""
    prefix = _prefix(modality)
    source = Path(settings.get(f'{prefix}_SOURCE_DIR') or settings.get(f'NII_DIR_{prefix}', '')).expanduser()
    result = {'rows': [], 'missing': [], 'ambiguous': [], 'errors': [], 'subjects': list(subjects),
              'series': [], 'image_count': 0}
    if not source.is_dir():
        result['errors'].append(f'Image source directory does not exist: {source}')
        return result
    space = str(settings.get(f'{prefix}_SPACE_LABEL', '')).strip()
    resolution = str(settings.get(f'{prefix}_RESOLUTION', '')).strip().removeprefix('res-')
    if not space:
        result['errors'].append('Set an exact space label before discovering inputs.')
        return result
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', space):
        result['errors'].append('The space label must use letters, digits, dots and dashes.')
        return result
    if len({item.split('_', 1)[0] for item in subjects}) > 1 and space.casefold() in {'dwi', 't1w', 't2w', 'native', 'orig', 'original', 'individual'}:
        result['errors'].append(f'Space {space!r} is subject-specific. Group analysis needs maps normalized to one common anatomical space upstream; matching image grids alone do not establish anatomical alignment.')
    if str(settings.get(f'{prefix}_MASK_LIST', '')).strip():
        mask_paths = [Path(value).expanduser() for value in str(settings[f'{prefix}_MASK_LIST']).split('|') if value]
        if len({path.name for path in mask_paths}) != len(mask_paths):
            result['errors'].append('Analysis masks have duplicate basenames; use distinct filenames before preparing them.')
        for path in mask_paths:
            if not path.is_file():
                result['errors'].append(f'Analysis mask not found: {path}')
        if (settings.get(f'{prefix}_MASK_SPACE_CONFIRMED') != 'yes'
                or settings.get(f'{prefix}_MASK_SPACE') != space):
            result['errors'].append('Confirm that tissue/analysis masks are already anatomically registered in the selected space. Grid resampling cannot perform registration.')
    filters = {}
    for tag in _list(settings.get(f'{prefix}_EXTRA_TAGS')):
        key, separator, value = tag.partition('-')
        if not separator or not re.fullmatch(r'[A-Za-z0-9]+', key) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', value):
            result['errors'].append(f'Additional filename tags need ENTITY-VALUE format: {tag}')
        else:
            filters[key] = value
    if resolution:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', resolution):
            result['errors'].append('Resolution must be a filename entity value such as 3 or 1x1x1mm.')
        if 'res' in filters and filters['res'] != resolution:
            result['errors'].append('Resolution and the additional res- tag disagree.')
        filters['res'] = resolution
    filters['space'] = space
    index = []
    prepared_root = (Path(settings['GLOBAL_DIR']) / 'prepared').resolve() if settings.get('GLOBAL_DIR') else None
    for path in source.rglob('*'):
        if path.is_file() and path.name.endswith(('.nii', '.nii.gz')):
            if prepared_root is not None and prepared_root in path.resolve().parents:
                continue
            entry = image_entities(path)
            if all(entry.entities.get(key) == value for key, value in filters.items()):
                index.append(entry)
    if prefix == 'MRSI':
        selectors = _list(settings.get('METAB'))
        if not selectors:
            result['errors'].append('Select at least one MRSI metabolite.')
    else:
        selectors = _list(settings.get(f'{prefix}_SELECTORS') or settings.get('FILES_DTI' if prefix == 'DTI' else 'FILES_STRUCTURAL'))
        if not selectors:
            result['errors'].append('Select at least one scalar image descriptor, suffix or filename pattern.')
    if len(set(subjects)) != len(subjects):
        result['errors'].append('The subject-visit list contains duplicate entries.')
    labels = {}
    reference = None
    signal_desc = str(settings.get('MRSI_SIGNAL_DESC', 'signal'))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', signal_desc):
        result['errors'].append('The signal descriptor must contain only letters, digits, underscores, dots and dashes.')
    for setting in (f'{prefix}_ACQ_LABEL', f'{prefix}_DATA_SUFFIX'):
        if settings.get(setting) and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', str(settings[setting])):
            result['errors'].append(f'{setting} needs a safe filename entity value.')
    mode = str(settings.get('MRSI_QMASK_MODE', 'thresholds'))
    if prefix == 'MRSI' and mode not in {'thresholds', 'population'}:
        result['errors'].append('Choose thresholds or population quality-mask mode.')
    if prefix == 'MRSI' and mode == 'thresholds':
        try:
            quality_mask([1], [1], [.01], [11], float(settings.get('MRSI_CRLB_MAX', 20)),
                         float(settings.get('MRSI_FWHM_MAX', .1)), float(settings.get('MRSI_SNR_MIN', 10)))
        except ValueError as exc:
            result['errors'].append(str(exc))
    for subject in subjects:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*_[A-Za-z0-9][A-Za-z0-9.-]*', subject):
            result['errors'].append(f'Invalid subject-session entry: {subject}')
            continue
        sub, ses = subject.split('_', 1)
        sub, ses = sub.removeprefix('sub-'), ses.removeprefix('ses-')
        entries = [entry for entry in index if entry.entities.get('sub') == sub and entry.entities.get('ses') == ses]
        for selector in selectors:
            if prefix == 'MRSI':
                matches = [entry for entry in entries if entry.entities.get('met') == selector
                           and entry.entities.get('desc') == signal_desc and entry.suffix == 'mrsi']
            elif any(char in selector for char in '*?['):
                matches = [entry for entry in entries if fnmatch.fnmatchcase(entry.path.name, selector)]
            else:
                matches = [entry for entry in entries if selector.casefold() in
                           {entry.entities.get('desc', '').casefold(), entry.suffix.casefold()}]
            selected = _select(matches, f'{subject} / {selector}', result)
            if selected is None:
                continue
            if prefix == 'MRSI' or not any(char in selector for char in '*?['):
                series = selector
            else:
                series = selected.entities.get('desc') or selected.suffix
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', series):
                result['errors'].append(f'Scalar series label {series!r} cannot be used by VLAD; select a simple descriptor/suffix.')
                continue
            previous_label = labels.setdefault(selector, series)
            if previous_label != series:
                result['errors'].append(f'{selector}: filename pattern selected different series labels across subjects.')
                continue
            row = {'subject': subject, 'series': series, 'image': str(selected.path), 'crlb': '', 'snr': '', 'fwhm': ''}
            try:
                image = nib.load(selected.path)
                if len(image.shape) != 3:
                    raise PreparationError(f'{selected.path}: expected a scalar 3D map, found shape {image.shape}.')
                if reference is None:
                    reference = image
                _grid(image, reference, str(selected.path))
                if prefix == 'MRSI' and mode == 'thresholds':
                    for metric in ('crlb', 'snr', 'fwhm'):
                        quality = [entry for entry in entries
                                   if entry.entities.get('desc', '').casefold() == metric
                                   and entry.suffix == 'mrsi'
                                   and entry.entities.get('res') == selected.entities.get('res')
                                   and all(key not in entry.entities or entry.entities[key] == selected.entities.get(key)
                                           for key in ('run', 'acq'))
                                   and (entry.entities.get('met') == selector if metric == 'crlb'
                                        else entry.entities.get('met') in {None, selector})]
                        # A metabolite-specific quality map takes priority over a shared map.
                        specific = [entry for entry in quality if entry.entities.get('met') == selector]
                        quality_entry = _select(specific or quality, f'{subject} / {selector} / {metric}', result)
                        if quality_entry:
                            row[metric] = str(quality_entry.path)
                            _grid(nib.load(quality_entry.path), image, str(quality_entry.path))
            except (OSError, ValueError, nib.filebasedimages.ImageFileError) as exc:
                result['errors'].append(str(exc))
            result['rows'].append(row)
    result['series'] = list(dict.fromkeys(labels.values()))
    if len(result['series']) != len(selectors):
        if not result['missing'] and not result['ambiguous']:
            result['errors'].append('Image selectors must identify distinct scalar series.')
    result['image_count'] = len(result['rows'])
    if prefix == 'MRSI' and mode == 'population':
        value = str(settings.get('MRSI_POPULATION_MASK', '')).strip()
        try:
            if not value or not Path(value).is_file():
                raise PreparationError('Select an existing precomputed binary population mask.')
            named = image_entities(Path(value))
            if named.entities.get('space') and named.entities['space'] != space:
                raise PreparationError('The population mask filename specifies a different space from the selected images.')
            if reference is not None and named.entities.get('res'):
                selected_resolution = image_entities(Path(result['rows'][0]['image'])).entities.get('res')
                if named.entities['res'] != selected_resolution:
                    raise PreparationError('The population mask filename specifies a different resolution from the selected images.')
            if reference is not None:
                _binary(nib.load(value), reference, value)
        except (OSError, ValueError, nib.filebasedimages.ImageFileError) as exc:
            result['errors'].append(str(exc))
    return result


def _settings(config_file: Path, modality: str):
    prefix = _prefix(modality)
    settings, _ = read_assignments(Path(config_file))
    mode_file = Path(settings['GLOBAL_DIR']) / f'config_{prefix.lower()}.sh'
    settings, _ = read_assignments(mode_file, settings)
    return settings, mode_file, prefix


def preview_inputs(config_file: Path, modality: str, subjects_file: Path | None = None) -> dict:
    settings, _, prefix = _settings(config_file, modality)
    subject_source = Path(subjects_file) if subjects_file is not None else Path(settings['LIST_ALL_SUBJECTS'])
    return discover_inputs(settings, prefix, _subjects(subject_source))


def _save_image(data, reference, destination):
    header = reference.header.copy()
    header.set_data_dtype(data.dtype)
    nib.save(nib.Nifti1Image(data, reference.affine, header), destination)


def _canonical(subject, series, prefix, settings, quality=False):
    sub, ses = subject.split('_', 1)
    sub, ses = sub.removeprefix('sub-'), ses.removeprefix('ses-')
    tags = _list(settings.get(f'{prefix}_EXTRA_TAGS'))
    res = str(settings.get(f'{prefix}_RESOLUTION', '')).removeprefix('res-')
    if res and not any(tag.startswith('res-') for tag in tags):
        tags.append('res-' + res)
    suffix = ''.join('_' + item for item in tags)
    start = f'sub-{sub}_ses-{ses}_space-{settings[f"{prefix}_SPACE_LABEL"]}{suffix}'
    if prefix == 'MRSI':
        return f'{start}_met-{series}_desc-{"QMask" if quality else settings.get("MRSI_SIGNAL_DESC", "signal")}_mrsi.nii.gz'
    acquisition = settings.get(f'{prefix}_ACQ_LABEL', 'dti' if prefix == 'DTI' else 'memprage')
    data_suffix = settings.get(f'{prefix}_DATA_SUFFIX', 'dwi' if prefix == 'DTI' else 'T1w')
    return f'{start}_acq-{acquisition}_desc-{series}_{data_suffix}.nii.gz'


def _apply_prepared_settings(config_file, mode_file, settings, prefix, source_root, destination, report):
    """Also used on an unchanged rerun after the browser saved source settings."""
    masks_destination = destination / 'masks'
    global_keys = read_assignments(Path(config_file))[0]
    local_values = {key: value for key, value in settings.items() if key not in global_keys}
    local_values[f'{prefix}_SOURCE_DIR'] = str(source_root)
    local_values[f'NII_DIR_{prefix}'] = str(destination)
    local_values[f'NII_DIR_{prefix}_compression'] = 'yes'
    local_values[f'{prefix}_FILES_VALIDATED'] = [settings.get('MRSI_SIGNAL_DESC', 'signal')] if prefix == 'MRSI' else report['series']
    local_values[f'{prefix}_MANIFEST'] = str(destination / 'manifest.json')
    local_values[f'{prefix}_MASK_DIR'] = str(masks_destination)
    if prefix != 'MRSI':
        local_values.setdefault(f'{prefix}_SELECTORS', settings.get('FILES_DTI' if prefix == 'DTI' else 'FILES_STRUCTURAL', []))
        local_values['FILES_DTI' if prefix == 'DTI' else 'FILES_STRUCTURAL'] = report['series']
    tags = _list(settings.get(f'{prefix}_EXTRA_TAGS'))
    resolution = str(settings.get(f'{prefix}_RESOLUTION', '')).removeprefix('res-')
    if resolution and not any(tag.startswith('res-') for tag in tags):
        tags.append('res-' + resolution)
    local_values[f'{prefix}_EXTRA_TAGS'] = ' '.join(tags)
    atomic_text(mode_file, _shell_file(local_values))


def prepare_inputs(config_file: Path, modality: str, overwrite=False, subjects_file: Path | None = None) -> dict:
    """Validate first, then stage canonical copies and masks inside the study."""
    settings, mode_file, prefix = _settings(config_file, modality)
    study = Path(settings['GLOBAL_DIR']).resolve()
    subject_source = Path(subjects_file) if subjects_file is not None else Path(settings['LIST_ALL_SUBJECTS'])
    subjects = _subjects(subject_source)
    report = discover_inputs(settings, prefix, subjects)
    issues = report['errors'] + report['missing'] + report['ambiguous']
    if issues:
        raise PreparationError('\n'.join(issues[:40]))
    if not report['rows']:
        raise PreparationError('No scalar input images were selected.')
    source_root = Path(settings.get(f'{prefix}_SOURCE_DIR') or settings[f'NII_DIR_{prefix}']).resolve()
    destination = study / 'prepared' / prefix.lower()
    resolved_destination = destination.resolve()
    if (source_root == resolved_destination or resolved_destination in source_root.parents
            or source_root in resolved_destination.parents or source_root == study or source_root in study.parents):
        raise PreparationError('Choose a study and prepared output folder outside the original derivatives source.')
    tags = _list(settings.get(f'{prefix}_EXTRA_TAGS'))
    resolution = str(settings.get(f'{prefix}_RESOLUTION', '')).removeprefix('res-')
    if resolution and not any(tag.startswith('res-') for tag in tags):
        tags.append('res-' + resolution)
    selection = {'source': str(source_root), 'space': settings.get(f'{prefix}_SPACE_LABEL'),
                 'tags': tags, 'resolution': resolution, 'masks': settings.get(f'{prefix}_MASK_LIST', ''),
                 'mask_space': settings.get(f'{prefix}_MASK_SPACE', ''),
                 'mask_space_confirmed': settings.get(f'{prefix}_MASK_SPACE_CONFIRMED', '')}
    if prefix == 'MRSI':
        for key, default in [('METAB', []), ('MRSI_SIGNAL_DESC', 'signal'), ('MRSI_QMASK_MODE', 'thresholds'),
                             ('MRSI_CRLB_MAX', '20'), ('MRSI_FWHM_MAX', '0.1'), ('MRSI_SNR_MIN', '10'),
                             ('MRSI_POPULATION_MASK', ''), ('MRSI_POPULATION_COVERAGE', '68')]:
            selection[key] = settings.get(key, default)
    else:
        selection['selectors'] = _list(settings.get(f'{prefix}_SELECTORS') or settings.get('FILES_DTI' if prefix == 'DTI' else 'FILES_STRUCTURAL'))
        selection['acquisition'] = settings.get(f'{prefix}_ACQ_LABEL', 'dti' if prefix == 'DTI' else 'memprage')
        selection['suffix'] = settings.get(f'{prefix}_DATA_SUFFIX', 'dwi' if prefix == 'DTI' else 'T1w')
    provenance = {'subjects': subjects, 'series': report['series'], 'selection': selection,
        'sources': {row[key]: [Path(row[key]).stat().st_size, Path(row[key]).stat().st_mtime_ns]
                    for row in report['rows'] for key in ('image', 'crlb', 'snr', 'fwhm') if row[key]},
        'rows': report['rows']}
    for value in filter(None, str(settings.get(f'{prefix}_MASK_LIST', '')).split('|')):
        path = Path(value).expanduser()
        if path.is_file():
            provenance['sources'][str(path)] = [path.stat().st_size, path.stat().st_mtime_ns]
    if prefix == 'MRSI' and settings.get('MRSI_QMASK_MODE', 'thresholds') == 'population':
        path = Path(settings['MRSI_POPULATION_MASK'])
        provenance['sources'][str(path)] = [path.stat().st_size, path.stat().st_mtime_ns]
    manifest = destination / 'manifest.json'
    with file_lock(study / '.vlad-inputs.lock'):
        if manifest.exists() and not overwrite:
            existing = json.loads(manifest.read_text())
            if (all(existing.get(key) == value for key, value in provenance.items())
                    and all((destination / value).is_file() for value in existing.get('prepared_files', []))):
                _apply_prepared_settings(config_file, mode_file, settings, prefix, source_root, destination, report)
                print(f'Prepared {prefix} inputs already match this selection; keeping them.')
                return existing
            raise PreparationError('Prepared inputs already exist with another selection or changed source files. Select recompute explicitly.')
        if destination.exists() and not overwrite:
            raise PreparationError('Prepared inputs already exist. Select recompute explicitly.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f'.{prefix.lower()}-', dir=destination.parent))
        try:
            reference = nib.load(report['rows'][0]['image'])
            populations = {}
            population = None
            if prefix == 'MRSI' and settings.get('MRSI_QMASK_MODE', 'thresholds') == 'population':
                population = _binary(nib.load(settings['MRSI_POPULATION_MASK']), reference, 'Population mask')
            for row in report['rows']:
                image = nib.load(row['image'])
                data = image.get_fdata(dtype=np.float32)
                if population is not None and np.any((population > 0) & ~np.isfinite(data)):
                    raise PreparationError(f'{row["image"]}: nonfinite signal values occur inside the supplied population mask.')
                _save_image(np.nan_to_num(data, nan=0, posinf=0, neginf=0), image, temporary / _canonical(row['subject'], row['series'], prefix, settings))
                if prefix == 'MRSI':
                    mask = population if population is not None else quality_mask(
                        data, nib.load(row['crlb']).get_fdata(dtype=np.float32),
                        nib.load(row['fwhm']).get_fdata(dtype=np.float32), nib.load(row['snr']).get_fdata(dtype=np.float32),
                        float(settings.get('MRSI_CRLB_MAX', 20)), float(settings.get('MRSI_FWHM_MAX', .1)),
                        float(settings.get('MRSI_SNR_MIN', 10)))
                    _save_image(mask, image, temporary / _canonical(row['subject'], row['series'], prefix, settings, quality=True))
                    populations.setdefault(row['series'], np.zeros(reference.shape, dtype=np.uint32))
                    populations[row['series']] += mask
                print(f'Prepared {row["subject"]} / {row["series"]}')
            coverage = float(settings.get('MRSI_POPULATION_COVERAGE', 68))
            if not math.isfinite(coverage) or not 0 < coverage <= 100:
                raise PreparationError('Population quality coverage must be above 0 and at most 100 percent.')
            for series, counts in populations.items():
                population_mask = (counts / len(subjects) >= coverage / 100).astype(np.uint8)
                if not population_mask.any():
                    raise PreparationError(f'{series}: no voxels meet the quality criteria at {coverage:g}% subject coverage. Review the maps, thresholds or population mask.')
                _save_image(population_mask, reference,
                            temporary / f'population_met-{series}_QMask.nii.gz')
            # Mask resampling writes only inside the study, never beside source masks.
            masks = str(settings.get(f'{prefix}_MASK_LIST', '')).split('|')
            masks_dir = temporary / 'masks'
            masks_dir.mkdir()
            for mask_path in filter(None, masks):
                path = Path(mask_path).expanduser()
                if not path.is_file():
                    raise PreparationError(f'Analysis mask not found: {path}')
                image = nib.load(path)
                if len(image.shape) != 3:
                    raise PreparationError(f'Analysis mask must be 3D: {path}')
                aligned = resample_from_to(image, (reference.shape, reference.affine), order=0)
                values = aligned.get_fdata(dtype=np.float32)
                if not np.isfinite(values).all():
                    raise PreparationError(f'Analysis mask contains nonfinite values: {path}')
                _save_image(values, reference, masks_dir / path.name)
            provenance['prepared_files'] = [str(path.relative_to(temporary)) for path in temporary.rglob('*') if path.is_file()]
            atomic_text(temporary / 'manifest.json', json.dumps(provenance, indent=2) + '\n')
            if destination.exists():
                shutil.rmtree(destination)
            temporary.replace(destination)
            _apply_prepared_settings(config_file, mode_file, settings, prefix, source_root, destination, report)
            print(f'{prefix} derivative preparation completed: {destination}')
            return provenance
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
