"""Read-only path discovery and explicit model construction for the wizard."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import nibabel as nib
import numpy as np
import pandas as pd

from vlad.gui.design import validate_contrasts
from vlad.gui.subjects import visit_label
from vlad.preparation.bids import image_entities


METABOLITE_LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+-]*')
SUM_OF_METABOLITES = 'SUMOFMETABOLITES'
NATIVE_SPACES = {'native', 't1w', 'dwi', 't2w', 'individual', 'orig', 'original'}
DWI_NON_SCALAR_SUFFIXES = {'mask', 'probseg', 'dseg', 'dwiref', 'sbref', 't1w', 't2w', 'xfm', 'fieldmap'}


def validate_output_location(plan, output):
    """Keep generated files out of the signal and quality derivative trees."""
    destination = Path(output).resolve()
    for source in (plan.get('data', {}), plan.get('mask', {})):
        if folder := source.get('folder'):
            input_root = Path(folder).expanduser().resolve()
            if destination == input_root or input_root in destination.parents:
                raise ValueError(f'Choose a VLAD output folder outside the input derivative folder {input_root}.')


def parse_analytes(values) -> dict:
    """Parse raw maps, voxelwise ratios, and gauge-corrected map contrasts."""
    tokens = [str(value).strip().removeprefix('met-') for value in values if str(value).strip()]
    if not tokens or len(tokens) != len(set(tokens)):
        raise ValueError('Specify distinct metabolite or ratio labels.')
    gauge_token = re.compile(r'^z_?[A-Z].*(?:[+-]z?[A-Z].*)?$')
    explicit = [token for token in tokens if '/' not in token and not gauge_token.fullmatch(token)]
    if any(not METABOLITE_LABEL.fullmatch(token) for token in explicit):
        raise ValueError('Metabolite labels may contain letters, digits, dots, pluses and dashes.')
    sources = list(explicit)
    analytes = [{'id': token, 'label': token, 'operation': 'identity',
                 'numerator': token, 'denominator': [], 'components': [token]}
                for token in explicit]
    for token in tokens:
        if '/' in token:
            if token.count('/') != 1:
                raise ValueError(f'Invalid ratio {token!r}; use numerator/denominator or numerator/(map+map).')
            numerator_expression, denominator = (part.strip().strip('()')
                                                  for part in token.split('/'))
            numerator_components = [part.strip().removeprefix('met-')
                                    for part in numerator_expression.split('+')]
            if not numerator_components or any(not METABOLITE_LABEL.fullmatch(part)
                                               for part in numerator_components):
                raise ValueError(f'Invalid ratio numerator: {numerator_expression!r}.')
            numerator = numerator_components[0]
            if denominator.upper() == SUM_OF_METABOLITES:
                if numerator not in explicit or len(explicit) < 2:
                    raise ValueError(f'{numerator}/{SUM_OF_METABOLITES} requires the numerator and at least one other base metabolite to be explicitly selected.')
                denominator_components = list(explicit)
            else:
                denominator_components = [part.strip().removeprefix('met-') for part in denominator.split('+')]
                if not denominator_components or any(not METABOLITE_LABEL.fullmatch(part) for part in denominator_components):
                    raise ValueError(f'Invalid ratio denominator: {denominator!r}. Use a metabolite sum such as (Glu+GABA).')
            if (len(numerator_components) == len(denominator_components) == 1 and
                    numerator == denominator_components[0]):
                raise ValueError(f'Ratio {token!r} has the same numerator and denominator.')
            for component in [*numerator_components, *denominator_components]:
                if component not in sources:
                    sources.append(component)
            components = list(dict.fromkeys([*numerator_components, *denominator_components]))
            numerator_label = '+'.join(numerator_components)
            denominator_label = (SUM_OF_METABOLITES if denominator.upper() == SUM_OF_METABOLITES
                                 else '+'.join(denominator_components))
            identifier = f'ratio-{numerator_label}-over-{denominator_label}'
            display_denominator = (denominator_label if (len(denominator_components) == 1 or
                                                         denominator.upper() == SUM_OF_METABOLITES)
                                   else f'({denominator_label})')
            item = {'id': identifier, 'label': f'{numerator_label}/{display_denominator}',
                    'operation': 'ratio', 'numerator': numerator,
                    'denominator': denominator_components, 'components': components}
            if len(numerator_components) > 1:
                item['numerator_components'] = numerator_components
            analytes.append(item)
        elif gauge_token.fullmatch(token):
            # A single z-prefixed term selects a gauge-corrected map; a signed
            # pair selects a contrast between gauge-corrected maps.
            expression = token[1:].removeprefix('_')
            terms = re.split(r'([+-])', expression)
            names = [part.strip().removeprefix('z').removeprefix('_').removeprefix('met-')
                     for part in terms[::2]]
            signs = [1 if sign == '+' else -1 for sign in terms[1::2]]
            if not names or any(not METABOLITE_LABEL.fullmatch(name) for name in names):
                raise ValueError(f'Invalid gauge-map expression {token!r}; use z_Glu or zGlu-zGABA.')
            if len(names) == 1:
                name = names[0]
                identifier = f'z-{name}'
                analytes.append({'id': identifier, 'label': f'z_{name}', 'operation': 'identity',
                                 'numerator': name, 'denominator': [], 'components': [name],
                                 'view': 'gauge'})
            else:
                if len(signs) != len(names) - 1:
                    raise ValueError(f'Invalid gauge-map contrast {token!r}.')
                if len(names) != len(set(names)):
                    raise ValueError(f'Gauge-map contrast {token!r} repeats a metabolite.')
                coefficients = [1, *signs]
                components = list(dict.fromkeys(names))
                identifier = 'contrast-' + '-'.join(f'{"plus" if c > 0 else "minus"}-{n}'
                                                       for n, c in zip(names, coefficients))
                analytes.append({'id': identifier, 'label': token, 'operation': 'contrast',
                                 'numerator': names[0], 'denominator': names[1:],
                                 'components': components, 'coefficients': coefficients,
                                 'view': 'gauge'})
            for component in names:
                if component not in sources:
                    sources.append(component)
    identifiers = [item['id'] for item in analytes]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('The selected analytes generate duplicate output labels.')
    return {'requested': tokens, 'source_metabolites': sources, 'analytes': analytes}


def signature(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def read_covariates(path) -> tuple[pd.DataFrame, list[str], str, str]:
    raw = str(path).strip()
    if not raw:
        raise ValueError('Choose a covariates TSV file before loading it.')
    source = Path(raw).expanduser()
    if not source.is_file():
        if source.is_dir():
            raise ValueError('Choose the covariates TSV file itself, not a directory.')
        raise ValueError(f'Covariates TSV not found: {source}')
    if source.suffix.lower() != '.tsv':
        raise ValueError('The guided workflow requires a tab-separated .tsv covariates file.')
    frame = pd.read_csv(source, sep='\t', dtype=str, keep_default_na=False)
    required = ('participant_id', 'session_id')
    missing = [name for name in required if name not in frame.columns]
    if missing:
        detail = (' The guided workflow uses participant_id, not subject_id.'
                  if 'participant_id' in missing and 'subject_id' in frame.columns else '')
        raise ValueError('The covariates TSV needs participant_id and session_id columns.' + detail)
    subject, session = required
    labels = [visit_label(a, b) for a, b in zip(frame[subject], frame[session])]
    if not labels or len(labels) != len(set(labels)):
        raise ValueError('The table must contain unique, nonempty subject/session pairs.')
    return frame, labels, subject, session


def index_images(folder: str):
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValueError('Choose an existing image folder.')
    return [image_entities(path.resolve()) for path in sorted(root.rglob('*'))
            if path.is_file() and path.name.endswith(('.nii', '.nii.gz'))]


def discover_metabolites(folder: str, *, space='', desc='', resolution='') -> list[str]:
    """Return met- values from NIfTI filenames matching the active selectors."""
    filters = {'space': str(space).strip().removeprefix('space-'),
               'desc': str(desc).strip().removeprefix('desc-'),
               'res': str(resolution).strip().removeprefix('res-')}
    filters = {key: value for key, value in filters.items() if value}
    labels = {entry.entities['met'] for entry in index_images(folder)
              if entry.entities.get('met') and
              all(entry.entities.get(key) == value for key, value in filters.items())}
    return sorted(labels, key=str.casefold)


def _by_visit(index):
    result = {}
    for entry in index:
        key = entry.entities.get('sub', '') + '_' + entry.entities.get('ses', '')
        result.setdefault(key, []).append(entry)
    return result


def _dwi_series(index, space, resolution):
    """Group exact post-visit filename patterns, including legacy scalar suffixes."""
    if not space:
        raise ValueError('Specify the exact DWI space- label.')
    if space.casefold() in NATIVE_SPACES:
        raise ValueError('DWI group analysis requires maps registered to a common space, such as space-mni.')
    result = {}
    for entry in index:
        if entry.suffix.casefold() in DWI_NON_SCALAR_SUFFIXES or 'met' in entry.entities:
            continue
        stem = entry.path.name.removesuffix('.gz').removesuffix('.nii')
        match = re.fullmatch(r'sub-([^_]+)_ses-([^_]+)_(.+)', stem)
        if not match:
            continue
        sub, ses, series = match.groups()
        tags = dict(re.findall(r'(?:^|_)(space|res)-([^_]+)', series))
        if tags.get('space') != space or (resolution and tags.get('res') != resolution):
            continue
        result.setdefault(series, {}).setdefault(f'{sub}_{ses}', []).append(entry.path)
    return result


def _dwi_header_issue(path):
    try:
        shape = nib.load(path).shape
    except (OSError, ValueError, nib.filebasedimages.ImageFileError) as exc:
        return f'Cannot read NIfTI header {path.name}: {exc}'
    if len(shape) != 3:
        return f'{path.name}: expected a scalar 3D DWI map, found shape {shape}.'
    return ''


def discover_dwi_series(folder, *, space, resolution=''):
    """List DWI series with a 3D representative and their per-visit file counts."""
    series = _dwi_series(index_images(folder), space, resolution)
    return {label: {visit: len(paths) for visit, paths in visits.items()}
            for label, visits in sorted(series.items())
            if not _dwi_header_issue(next(iter(visits.values()))[0])}


def _scan_dwi_signals(config, visits):
    selected = config.get('dwi_series', [])
    if not selected or len(set(selected)) != len(selected):
        raise ValueError('Choose distinct DWI scalar-map series from the folder scan.')
    indexed = _dwi_series(index_images(config['folder']), config.get('space', ''), config.get('resolution', ''))
    analytes = []
    for label in selected:
        slug = re.sub(r'[^A-Za-z0-9.+-]+', '-', label)[:80].rstrip('.-')
        identifier = f'dwi-{slug}-{signature(label)[:8]}'
        analytes.append({'id': identifier, 'label': label, 'operation': 'identity',
                         'numerator': identifier, 'denominator': [], 'components': [identifier]})
    rows = []
    for visit in visits:
        images, issues = {}, []
        for analyte in analytes:
            label = analyte['label']
            matches = indexed.get(label, {}).get(visit, [])
            if len(matches) != 1:
                detail = '; '.join(str(path) for path in matches[:3])
                issues.append(f'{label}: {len(matches)} matches' + (f' ({detail})' if detail else ''))
            elif issue := _dwi_header_issue(matches[0]):
                issues.append(issue)
            else:
                images[analyte['id']] = str(matches[0])
        rows.append({'visit': visit, 'images': images, 'issues': issues, 'complete': not issues})
    return {'config': config, 'rows': rows, 'analytes': analytes,
            'source_metabolites': [item['id'] for item in analytes], 'fingerprint': signature(config)}


def _match(index, visit, space, resolution='', desc='', metabolite=None, scope='Metabolite', signal=None):
    subject, session = visit.split('_', 1)
    filters = {'sub': subject, 'ses': session, 'space': space}
    if resolution:
        filters['res'] = resolution
    if desc:
        filters['desc'] = desc
    if scope == 'Metabolite':
        filters['met'] = metabolite
    candidates = [entry for entry in index if all(entry.entities.get(key) == value for key, value in filters.items())
                  and (scope != 'Global' or 'met' not in entry.entities)]
    if signal:
        entities = image_entities(Path(signal)).entities
        candidates = [entry for entry in candidates if entry.entities.get('res') == entities.get('res')
                      and all(entry.entities.get(key) == entities.get(key) for key in ('run', 'acq'))]
    return [str(entry.path) for entry in candidates]


def scan_signals(config: dict, visits: list[str]) -> dict:
    """Match visits by filename and check DWI dimensionality without loading voxels."""
    if config.get('modality') == 'DWI':
        return _scan_dwi_signals(config, visits)
    if not config.get('space'):
        raise ValueError('Specify the space- label.')
    parsed = parse_analytes(config.get('metabolites', []))
    metabolites = parsed['source_metabolites']
    index = _by_visit(index_images(config['folder']))
    rows = []
    for visit in visits:
        images, issues = {}, []
        for met in metabolites:
            matches = _match(index.get(visit, []), visit, config['space'], config.get('resolution', ''), config.get('desc', ''), met)
            if len(matches) == 1:
                images[met] = matches[0]
            else:
                issues.append(f'{met}: {len(matches)} matches' + (f' ({"; ".join(matches[:3])})' if matches else ''))
        rows.append({'visit': visit, 'images': images, 'issues': issues, 'complete': not issues})
    return {'config': config, 'rows': rows, 'analytes': parsed['analytes'],
            'source_metabolites': metabolites, 'fingerprint': signature(config)}


def scan_quality(config: dict, signals: dict) -> dict:
    """QC files must match each signal's subject, space, res, run and acquisition."""
    if config['space'] != signals['config']['space']:
        raise ValueError('Quality maps must use the same space as the signal maps.')
    metrics = config.get('metrics', [])
    if not metrics or len({item['name'] for item in metrics}) != len(metrics):
        raise ValueError('Choose distinct quality metrics.')
    index = _by_visit(index_images(config['folder']))
    rows = []
    for row in signals['rows']:
        quality, issues = {}, list(row['issues'])
        for met, signal in row['images'].items():
            quality[met] = {}
            for metric in metrics:
                if not metric['desc']:
                    raise ValueError('Each quality metric needs a desc- label.')
                matches = _match(index.get(row['visit'], []), row['visit'], config['space'], config.get('resolution', ''),
                                 metric['desc'], met, metric['scope'], signal)
                if len(matches) == 1:
                    quality[met][metric['name']] = matches[0]
                else:
                    issues.append(f'{met}/{metric["name"]}: {len(matches)} matches')
        rows.append({'visit': row['visit'], 'quality': quality, 'issues': issues, 'complete': not issues})
    return {'config': config, 'rows': rows, 'fingerprint': signature(config)}


def validate_population_paths(mapping, metabolites, space, resolution='') -> dict:
    result = {}
    for met in metabolites:
        path = Path(mapping.get(met, '')).expanduser()
        if not path.is_file() or not path.name.endswith(('.nii', '.nii.gz')):
            raise ValueError(f'{met}: choose an existing population-mask NIfTI.')
        entities = image_entities(path).entities
        for key, expected in [('space', space), ('res', resolution), ('met', met)]:
            if expected and entities.get(key) and entities[key] != expected:
                raise ValueError(f'{met}: mask filename has a different {key}- label.')
        result[met] = str(path.resolve())
    return result


def build_model(frame: pd.DataFrame, roles: list[dict], centre=True) -> dict:
    """Intercept, confounds, then tested regressors: the displayed order is exact."""
    included = [row for row in roles if row['Include']]
    tested = [row for row in included if row['Regressor']]
    if len(tested) != 1:
        raise ValueError('Include exactly one tested regressor. Other included variables must be confounds.')
    if any(bool(row['Confound']) == bool(row['Regressor']) for row in included):
        raise ValueError('Assign each included variable to either Confound or Regressor, not both.')
    design = pd.DataFrame({'Intercept': np.ones(len(frame))})
    regressor_columns, regressor_labels, coding = [], {}, {}
    for row in sorted(included, key=lambda row: bool(row['Regressor'])):
        name = row['Name']
        if name not in frame or name == 'Intercept':
            raise ValueError(f'Invalid model variable: {name}')
        raw = frame[name].reset_index(drop=True)
        if raw.isna().any() or raw.astype(str).str.strip().isin(['', 'n/a', 'nan', 'NA']).any():
            raise ValueError(f'{name}: missing values; explicitly exclude those rows in Data.')
        z_score = bool(row.get('Z-score', False))
        if z_score and row['Type'] != 'Numeric':
            raise ValueError(f'{name}: z-scoring is only available for numeric variables.')
        encoded = {}
        if row['Type'] == 'Numeric':
            values = pd.to_numeric(raw, errors='raise').astype(float)
            if not np.isfinite(values).all() or values.nunique() < 2:
                raise ValueError(f'{name}: needs finite, varying numeric values.')
            mean = float(values.mean()) if z_score or (centre and row['Confound']) else 0.
            encoded[name] = values - mean
            coding[name] = {'type': 'numeric', 'subtracted_mean': mean}
            if z_score:
                scale = float(values.std(ddof=0))
                if not np.isfinite(scale) or scale <= 0:
                    raise ValueError(f'{name}: z-scoring needs a finite, nonzero standard deviation.')
                encoded[name] /= scale
                coding[name].update(transform='z-score', standard_deviation=scale)
            if row['Regressor']:
                levels = sorted(values.unique())
                if len(levels) == 2:
                    low, high = (f'{value:g}' for value in levels)
                    regressor_labels[name] = (f'{name}: {high} > {low}',
                                              f'{name}: {high} < {low}')
                else:
                    regressor_labels[name] = (f'{name}: slope > 0',
                                              f'{name}: slope < 0')
        else:
            levels = sorted(raw.astype(str).unique())
            reference = row.get('Reference') or levels[0]
            if reference not in levels or len(levels) < 2:
                raise ValueError(f'{name}: choose a present reference level and at least two categories.')
            levels = [reference, *[level for level in levels if level != reference]]
            encoded = {f'{name}[{level}]': (raw.astype(str) == level).astype(float) for level in levels[1:]}
            coding[name] = {'type': 'categorical', 'reference': reference, 'levels': levels}
            if row['Regressor']:
                for level in levels[1:]:
                    regressor_labels[f'{name}[{level}]'] = (
                        f'{name}: {level} > {reference}',
                        f'{name}: {level} < {reference}',
                    )
        for column, values in encoded.items():
            if column in design:
                raise ValueError(f'Duplicate design column: {column}')
            design[column] = values
            if row['Regressor']:
                regressor_columns.append(column)
    values = design.to_numpy(dtype=float)
    if len(values) <= values.shape[1] or np.linalg.matrix_rank(values) < values.shape[1]:
        raise ValueError('The model needs more observations than columns and no constant/collinear predictors.')
    contrasts, labels = [], []
    for name in regressor_columns:
        row = np.zeros(values.shape[1]); row[design.columns.get_loc(name)] = 1
        contrasts.extend([row.tolist(), (-row).tolist()])
        labels.extend(regressor_labels[name])
    return {'matrix': values.tolist(), 'columns': list(design.columns), 'contrasts': contrasts,
            'contrast_labels': labels, 'coding': coding, 'regressors': regressor_columns}


def parse_contrast_text(text: str, model: dict) -> tuple[list, list]:
    rows = [[float(value) for value in line.replace(',', ' ').split()]
            for line in text.replace(';', '\n').splitlines() if line.strip()]
    matrix, _ = validate_contrasts(rows, pd.DataFrame(model['matrix'], columns=model['columns']))
    suggested = list(zip(np.asarray(model.get('contrasts', []), dtype=float),
                         model.get('contrast_labels', [])))
    labels = []
    for number, row in enumerate(matrix, 1):
        match = next((label for weights, label in suggested
                      if weights.shape == row.shape and np.allclose(weights, row)), None)
        labels.append(match or f'Custom contrast {number}')
    return matrix.tolist(), labels


def population_mask(individual_masks, coverage):
    """At least x percent of included subjects must have a valid voxel."""
    if not 0 < float(coverage) <= 100:
        raise ValueError('Population coverage must be above 0 and at most 100 percent.')
    arrays = np.asarray(individual_masks, dtype=bool)
    if arrays.ndim < 2 or not len(arrays):
        raise ValueError('At least one individual mask is required.')
    # Compare counts to avoid floating-point ambiguity at exact percentages.
    return (arrays.sum(axis=0) * 100 >= float(coverage) * len(arrays)).astype(np.uint8)
