"""Execute an immutable guided-workflow plan, writing only a new run folder."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

import nibabel as nib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn

from vlad.gui.cohorts import atomic_text
from vlad.gui.design import _vest, validate_contrasts
from vlad.gui.templates import validate_reference_anatomy
from vlad.workflow.core import (NATIVE_SPACES, local_nifti_path, validate_mask_path, validate_output_location,
                                validate_population_paths)
from vlad.workflow.gauge import correct_map_voxelwise, gauge_from_group_means
from vlad.workflow.tissues import align_tissue_masks, align_white_matter, load_white_matter_image, report_tissue_inputs


PROGRESS_PREFIX = 'VLAD_PROGRESS '
_OUTPUT_LOCK = threading.Lock()
_RICH_PROGRESS = None
_RICH_TASKS = {}


def _analytes(plan):
    """Return explicit derived-map specifications, with old-plan compatibility."""
    values = plan.get('data', {}).get('analytes')
    if values:
        selected = plan['data']['metabolites']
        identifiers = [item.get('id') for item in values]
        # Older callers and saved tests sometimes narrow an identity-only plan
        # by editing ``metabolites``.  Treat the retained identity metadata as
        # advisory in that case; derived specifications remain strict because
        # silently dropping a ratio component would change the analysis.
        if identifiers != selected and all(item.get('operation') == 'identity' for item in values):
            indexed = {item.get('id'): item for item in values}
            if all(identifier in indexed for identifier in selected):
                return [indexed[identifier] for identifier in selected]
        return values
    return [{'id': metabolite, 'label': metabolite, 'operation': 'identity',
             'numerator': metabolite, 'denominator': [], 'components': [metabolite]}
            for metabolite in plan['data']['metabolites']]


def _analyte(plan, identifier):
    return next(item for item in _analytes(plan) if item['id'] == identifier)


def _emit(message):
    with _OUTPUT_LOCK:
        if _RICH_PROGRESS is not None:
            _RICH_PROGRESS.console.print(message)
        else:
            if sys.stdout.isatty():
                print('\r\033[2K', end='')
            print(message, flush=True)


@contextmanager
def _metabolite_progress(metabolites, permutations, contrasts):
    """Show one live Rich task per metabolite when attached to a terminal."""
    global _RICH_PROGRESS, _RICH_TASKS
    if not sys.stdout.isatty():
        yield
        return
    total = permutations * contrasts if permutations > 0 else None
    console = Console(file=sys.stdout, force_terminal=True)
    progress = Progress(
        TextColumn('[bold cyan]{task.fields[metabolite]:<12}'),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TextColumn('• {task.fields[counter]}'),
        TextColumn('• {task.fields[detail]}'),
        TimeRemainingColumn(),
        console=console,
        expand=True,
        refresh_per_second=10,
    )
    with _OUTPUT_LOCK:
        _RICH_PROGRESS = progress
        _RICH_TASKS = {
            metabolite: progress.add_task(
                '', total=total, metabolite=metabolite,
                counter=(f'0/{total}' if total else 'waiting'),
                detail='Waiting for worker',
            )
            for metabolite in metabolites
        }
    try:
        with progress:
            yield
    finally:
        with _OUTPUT_LOCK:
            _RICH_PROGRESS = None
            _RICH_TASKS = {}


def _emit_progress(metabolite, stage, current, total=None, detail=''):
    event = {'metabolite': metabolite, 'stage': stage, 'current': current,
             'total': total, 'detail': detail}
    if _RICH_PROGRESS is not None and metabolite in _RICH_TASKS:
        with _OUTPUT_LOCK:
            counter = f'{current}/{total}' if total else f'permutation {current}'
            _RICH_PROGRESS.update(
                _RICH_TASKS[metabolite], completed=current,
                total=total, counter=counter,
                detail=('Complete' if stage == 'Complete' else detail or stage),
                refresh=stage == 'Complete',
            )
        return
    if sys.stdout.isatty():
        with _OUTPUT_LOCK:
            if total:
                fraction = min(1., max(0., current / total))
                width = 30
                filled = round(width * fraction)
                bar = '█' * filled + '░' * (width - filled)
                counter = f'{current}/{total} · {fraction:.0%}'
            else:
                bar = '·' * 30
                counter = f'permutation {current}'
            suffix = f' · {detail}' if detail else ''
            print(f'\r\033[2K[{bar}] {metabolite} · {stage} · {counter}{suffix}',
                  end='\n' if stage == 'Complete' else '', flush=True)
        return
    _emit(PROGRESS_PREFIX + json.dumps(event, separators=(',', ':')))


def _gauge_enabled(plan):
    # Explicit true remains valid for previously saved plans.
    return plan['randomise'].get('gauge_correction', False) or any(
        item.get('view') == 'gauge' for item in _analytes(plan))


def _gauge_key(analyte):
    return (tuple(analyte['components']) if analyte['operation'] == 'identity'
            else analyte['id'])


def _analysis_stack_name(plan, metabolite):
    if _gauge_enabled(plan) and _analyte(plan, metabolite).get('view', 'raw') == 'raw':
        return 'signals_raw.nii.gz'
    return 'signals.nii.gz'


def randomise_command(plan, metabolite, executable='randomise'):
    directory = Path(plan['output_dir']) / metabolite
    settings = plan['randomise']
    input_stack = _analysis_stack_name(plan, metabolite)
    argv = [executable, '-i', str(directory / input_stack), '-o', str(directory / 'randomise'),
            '-m', str(directory / 'population_mask.nii.gz'), '-d', str(Path(plan['output_dir']) / 'design.mat'),
            '-t', str(Path(plan['output_dir']) / 'design.con'), '-n', str(settings['permutations'])]
    if plan['model'].get('ftests'):
        argv += ['-f', str(Path(plan['output_dir']) / 'design.fts'), '--fonly']
    if settings.get('tfce', True):
        argv += ['-T', f"--tfce_H={settings.get('tfce_h', 2.)}", f"--tfce_E={settings.get('tfce_e', .5)}",
                 f"--tfce_C={settings.get('tfce_c', 6)}"]
    if settings.get('voxelwise', False): argv.append('-x')
    if settings.get('uncorrected', False): argv.append('--uncorrp')
    if settings.get('variance_smoothing', 0): argv += ['-v', str(settings['variance_smoothing'])]
    if settings.get('seed') is not None: argv += [f"--seed={settings['seed']}"]
    if settings.get('blocks'): argv += ['-e', str(Path(plan['output_dir']) / 'design.grp')]
    return argv


def _run_randomise(command, *, environment, metabolite, permutations, contrasts):
    """Stream Randomise while replacing per-permutation spam with progress events."""
    _emit('Running: ' + __import__('shlex').join(command))
    total = permutations * contrasts if permutations > 0 else None
    _emit_progress(metabolite, 'Randomise', 0, total, f'{contrasts} contrast(s), sequential')
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    last_permutation = 0
    contrast_index = 0
    last_bucket = -1
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip()
        match = re.search(r'Starting permutation\s+(\d+)', line, flags=re.IGNORECASE)
        if match:
            permutation = int(match.group(1))
            if permutations > 0 and last_permutation and permutation <= last_permutation:
                contrast_index += 1
            last_permutation = permutation
            current = contrast_index * permutations + permutation if permutations > 0 else permutation
            if total and _RICH_PROGRESS is not None:
                current = min(current, total)
                _emit_progress(metabolite, 'Randomise', current, total,
                               f'contrast {min(contrast_index + 1, contrasts)}/{contrasts}')
            elif total:
                current = min(current, total)
                bucket = min(20, int(current * 20 / total))
                if bucket > last_bucket or current == total:
                    last_bucket = bucket
                    _emit_progress(metabolite, 'Randomise', current, total,
                                   f'contrast {min(contrast_index + 1, contrasts)}/{contrasts}')
            elif permutation == 1 or permutation % 100 == 0:
                _emit_progress(metabolite, 'Randomise', permutation, None, 'exhaustive permutations')
            continue
        if line:
            _emit(f'[{metabolite}] {line}')
    code = process.wait()
    if code:
        raise subprocess.CalledProcessError(code, command)
    _emit_progress(metabolite, 'Complete', total or last_permutation, total or last_permutation,
                   f'{contrasts} contrast(s) completed sequentially')


def _statistic_outputs(model):
    return ('fstat', model['ftest_labels']) if model.get('ftests') else ('tstat', model['contrast_labels'])


def _analyse_metabolite(plan, metabolite, executable, environment):
    """Run one metabolite; its contrasts remain sequential inside Randomise."""
    analyte = _analyte(plan, metabolite)
    command = randomise_command(plan, metabolite, executable)
    statistic, labels = _statistic_outputs(plan['model'])
    _run_randomise(command, environment=environment, metabolite=analyte['label'],
                   permutations=plan['randomise']['permutations'],
                   contrasts=len(labels))
    directory = Path(plan['output_dir']) / metabolite
    population = nib.load(directory / 'population_mask.nii.gz').get_fdata()
    summary = []
    for number, label in enumerate(labels, 1):
        for pmap in sorted(directory.glob(f'randomise*corrp_{statistic}{number}.nii.gz')):
            values = nib.load(pmap).get_fdata()
            summary.append({'metabolite': analyte['label'], 'analyte': metabolite,
                            'contrast': label, 'map': str(pmap), 'signal_stack': _analysis_stack_name(plan, metabolite),
                            'voxels_p_le_0.05': int(np.count_nonzero((values >= .95) & (population > 0)))})
    return summary


def validate_plan(plan):
    if plan.get('version') != 1:
        raise ValueError('Unsupported workflow plan version.')
    rows, metabolites, model = plan['rows'], plan['data']['metabolites'], plan['model']
    modality = plan['data'].get('modality', 'MRSI')
    if modality not in ('MRSI', 'DWI'):
        raise ValueError('Choose MRSI or DWI as the map modality.')
    if not rows or not metabolites:
        raise ValueError('Select subjects and maps first.')
    visits = [row['visit'] for row in rows]
    if visits != model['subjects'] or len(visits) != len(set(visits)):
        raise ValueError('Image rows and model rows must have the same unique subject/session order.')
    if len(set(metabolites)) != len(metabolites): raise ValueError('Duplicate map labels.')
    import re
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*_[A-Za-z0-9][A-Za-z0-9.-]*', visit) for visit in visits):
        raise ValueError('Invalid subject/session label in the run plan.')
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]*', met) for met in metabolites):
        raise ValueError('Invalid output map label.')
    analytes = _analytes(plan)
    if [item.get('id') for item in analytes] != metabolites:
        raise ValueError('Analyte specifications must match the ordered output labels.')
    source_metabolites = set(plan['data'].get('source_metabolites', metabolites))
    for item in analytes:
        if (item.get('operation') not in {'identity', 'ratio', 'contrast'} or not item.get('label') or
                any(char in item['label'] for char in '\r\n\t\x00') or
                not item.get('components') or not set(item['components']) <= source_metabolites):
            raise ValueError(f'Invalid analyte specification: {item!r}')
        numerator_components = item.get('numerator_components', [item.get('numerator')])
        if item['operation'] == 'ratio' and (not numerator_components or
                                             not set(numerator_components) <= set(item['components']) or
                                             not item.get('denominator')):
            raise ValueError(f'Invalid ratio specification: {item["label"]}')
        if item.get('view', 'raw') not in {'raw', 'gauge'}:
            raise ValueError(f'Invalid map view for {item["label"]}.')
        if modality == 'DWI' and (item['operation'] != 'identity' or
                                  item['components'] != [item.get('numerator')] or
                                  item.get('view', 'raw') != 'raw'):
            raise ValueError('DWI analysis requires one scalar image per observation for each series.')
        if item['operation'] == 'contrast' and (
                len(item.get('coefficients', [])) != len(item['components']) or
                not all(math.isfinite(float(value)) for value in item['coefficients'])):
            raise ValueError(f'Invalid gauge-map contrast: {item["label"]}.')
    if plan['data']['space'].lower() in NATIVE_SPACES:
        raise ValueError('Group analysis requires a common template space, not subject-native coordinates.')
    design = pd.DataFrame(model['matrix'], columns=model['columns'])
    if (len(design) != len(rows) or len(design) <= len(design.columns) or not np.isfinite(design).all().all()
            or np.linalg.matrix_rank(design.to_numpy()) != len(design.columns)):
        raise ValueError('The model must be finite, full rank and have residual degrees of freedom.')
    t_contrasts, _ = validate_contrasts(model['contrasts'], design, model['contrast_labels'])
    if 'ftests' in model:
        ftests = np.asarray(model['ftests'], dtype=float)
        labels = model.get('ftest_labels', [])
        if (ftests.ndim != 2 or not len(ftests) or ftests.shape[1] != len(t_contrasts)
                or not np.isin(ftests, (0, 1)).all() or len(labels) != len(ftests)
                or any(not isinstance(label, str) or not label.strip() for label in labels)
                or len(set(labels)) != len(labels)):
            raise ValueError('F-tests need distinct labels and a 0/1 row selecting the relevant t-contrasts for each test.')
        for row in ftests:
            selected = t_contrasts[row.astype(bool)]
            if not len(selected) or np.linalg.matrix_rank(selected) != len(selected):
                raise ValueError('Each F-test needs independent contrast rows.')
    options = plan['randomise']
    nperm = options['permutations']
    if not isinstance(nperm, int) or nperm < 0: raise ValueError('Permutations must be a nonnegative integer (0 means exhaustive).')
    nproc = options.get('nproc', 1)
    if not isinstance(nproc, int) or not 1 <= nproc <= 64:
        raise ValueError('Concurrent maps (nproc) must be an integer from 1 to 64.')
    if not isinstance(options.get('gauge_correction', False), bool):
        raise ValueError('Gauge correction must be enabled or disabled.')
    if modality == 'DWI' and _gauge_enabled(plan):
        raise ValueError('Gauge correction is available for MRSI only.')
    gauge_inputs = {_gauge_key(item) for item in analytes if item['operation'] != 'contrast'}
    if _gauge_enabled(plan) and len(gauge_inputs) < 2:
        raise ValueError('Gauge correction requires at least two distinct metabolite or ratio maps. For example, enter z_Glu GABA.')
    selected_ids = {item['id'] for item in analytes}
    for item in analytes:
        if item['operation'] == 'contrast' and any(
                component not in selected_ids and f'z-{component}' not in selected_ids
                for component in item['components']):
            raise ValueError(f'{item["label"]}: include each component map (for example z_{item["components"][0]}) so VLAD can contrast their gauge-corrected values.')
    if not options.get('tfce') and not options.get('voxelwise'):
        raise ValueError('Enable TFCE or voxelwise corrected inference.')
    for field in ('tfce_h', 'tfce_e', 'variance_smoothing'):
        value = float(options.get(field, 0))
        if not math.isfinite(value) or value < 0: raise ValueError(f'Invalid {field}.')
    if options.get('tfce_c', 6) not in (6, 26): raise ValueError('TFCE connectivity must be 6 or 26.')
    seed = options.get('seed')
    if seed is not None and (not isinstance(seed, int) or seed < 0): raise ValueError('Seed must be a nonnegative integer.')
    blocks = options.get('blocks')
    if blocks and (len(blocks) != len(rows) or any(not isinstance(value, int) or value < 1 for value in blocks)):
        raise ValueError('Exchangeability blocks must have one positive integer per model row.')
    if len({visit.split('_')[0] for visit in visits}) != len(visits) and not blocks:
        raise ValueError('Repeated subjects require an explicit exchangeability-block column or one selected visit per subject.')
    mask = plan['mask']
    mask_modes = ('coverage', 'white_matter', 'precomputed') if modality == 'DWI' else ('quality', 'precomputed')
    if mask['mode'] not in mask_modes:
        raise ValueError(f'Invalid population mask mode for {modality}.')
    if not 0 < float(mask['coverage']) <= 100: raise ValueError('Coverage must be above 0 and at most 100%.')
    if mask['mode'] in ('coverage', 'white_matter') and float(mask['coverage']) != 100:
        raise ValueError('Common DWI coverage requires a finite nonzero value in every included observation.')
    if mask['mode'] == 'quality':
        metrics = mask['metrics']
        if not metrics or len({metric['name'] for metric in metrics}) != len(metrics):
            raise ValueError('Specify distinct quality metrics.')
        for metric in metrics:
            if metric['operator'] not in ('<', '>', '<=', '>='): raise ValueError('Invalid quality operator.')
            if metric.get('threshold') is None or not math.isfinite(float(metric['threshold'])):
                raise ValueError(f'Enter the {metric["name"]} threshold in {metric["unit"]}.')
    source_paths = set(report_tissue_inputs(plan)[0].values())
    for field in ('report_reference', 'tfce_slice_template'):
        if reference := plan.get(field):
            source_paths.add(str(validate_reference_anatomy(local_nifti_path(reference))))
    if mask['mode'] == 'precomputed':
        validate_population_paths(mask['precomputed'], metabolites, plan['data']['space'], plan['data'].get('resolution', ''))
        source_paths.update(str(local_nifti_path(mask['precomputed'][met])) for met in metabolites)
    elif mask['mode'] == 'white_matter':
        wm = mask.get('white_matter', {})
        if not wm.get('path') or not isinstance(wm.get('probability'), bool):
            raise ValueError('Population mask: choose a 3D white-matter NIfTI or download a matching WM map.')
        wm_path = validate_mask_path(wm['path'], 'White-matter mask', plan['data']['space'])
        load_white_matter_image(wm_path)
        source_paths.add(wm_path)
    for row in rows:
        for analyte in analytes:
            for met in analyte['components']:
                source_paths.add(row['images'][met])
                if mask['mode'] == 'quality':
                    source_paths.update(row['quality'][met][metric['name']] for metric in mask['metrics'])
    output = Path(plan['output_dir'])
    if not output.is_absolute(): raise ValueError('Use an absolute output directory.')
    validate_output_location(plan, output)
    for value in source_paths:
        path = Path(value)
        if not path.is_file(): raise ValueError(f'Input no longer exists: {path}')
        if output == path or output in path.parents: raise ValueError('Inputs must be outside the new run folder.')
        expected = plan.get('sources', {}).get(value)
        if expected and expected != [path.stat().st_size, path.stat().st_mtime_ns]:
            raise ValueError(f'Input changed since this plan was built: {path}. Load the files again.')
    return source_paths


def _load(path, reference=None):
    image = nib.load(path)
    if len(image.shape) != 3: raise ValueError(f'Expected a 3D NIfTI: {path}, shape={image.shape}')
    if reference is not None and (image.shape != reference.shape or not np.allclose(image.affine, reference.affine, atol=1e-4, rtol=0)):
        raise ValueError(f'Image grid/affine does not match the signal maps: {path}. Register/resample upstream.')
    return image


def _load_analyte(row, analyte, reference=None):
    """Load one base map or construct a voxelwise ratio for one observation."""
    images, arrays = {}, {}
    local_reference = reference
    for component in analyte['components']:
        image = _load(row['images'][component], local_reference)
        if local_reference is None:
            local_reference = image
        images[component] = image
        arrays[component] = image.get_fdata(dtype=np.float32)
    numerator_components = analyte.get('numerator_components', [analyte['numerator']])
    numerator = np.sum([arrays[name] for name in numerator_components], axis=0,
                       dtype=np.float64).astype(np.float32) if len(numerator_components) > 1 else arrays[numerator_components[0]]
    valid = np.ones(numerator.shape, dtype=bool)
    for values in arrays.values():
        valid &= np.isfinite(values)
    if analyte['operation'] == 'identity':
        signal = numerator
    elif analyte['operation'] == 'contrast':
        signal = np.zeros(numerator.shape, dtype=np.float32)
        for component, coefficient in zip(analyte['components'], analyte['coefficients']):
            signal += arrays[component] * float(coefficient)
    else:
        denominator = np.sum([arrays[name] for name in analyte['denominator']],
                             axis=0, dtype=np.float64)
        valid &= np.isfinite(denominator) & (denominator != 0)
        signal = np.divide(numerator, denominator,
                           out=np.zeros(numerator.shape, dtype=np.float32),
                           where=valid).astype(np.float32, copy=False)
    return local_reference, signal, valid


def _save(data, reference, path):
    header = reference.header.copy(); header.set_data_dtype(data.dtype)
    nib.save(nib.Nifti1Image(data, reference.affine, header), path)


def _apply_gauge_correction(plan, reference, group_means):
    """Create gauge diagnostics and corrected 4D stacks before inference."""
    output = Path(plan['output_dir'])
    analytes = [item for item in _analytes(plan) if item['operation'] != 'contrast']
    metabolites = [item['id'] for item in analytes]
    gauge_inputs = {_gauge_key(item): item for item in analytes}
    positions = {key: index for index, key in enumerate(gauge_inputs)}
    means = np.stack([group_means[item['id']] for item in gauge_inputs.values()])
    met_std, z_maps, gauge_term = gauge_from_group_means(means)
    invalid = [item['label'] for item, value in zip(gauge_inputs.values(), met_std)
               if not np.isfinite(value) or value <= np.finfo(float).eps]
    if invalid:
        raise ValueError('Gauge correction needs a finite, nonzero spatial standard deviation for: '
                         + ', '.join(invalid))
    if not np.isfinite(gauge_term).any():
        raise ValueError('Gauge correction produced no finite voxels. Check the selected maps.')
    _save(gauge_term.astype(np.float32), reference, output / 'gauge_term.nii.gz')
    metadata = {'enabled': True, 'metabolites': metabolites,
                'analytes': [{'id': item['id'], 'label': item['label']} for item in analytes],
                'metabolite_spatial_std': {
                    item['id']: float(met_std[positions[_gauge_key(item)]]) for item in analytes},
                'formula': 'corrected = raw - gauge_term * metabolite_spatial_std; raw zeros remain zero'}
    atomic_text(output / 'gauge_metadata.json', json.dumps(metadata, indent=2) + '\n')
    for analyte in analytes:
        metabolite = analyte['id']
        met_index = positions[_gauge_key(analyte)]
        directory = output / metabolite
        _save(means[met_index].astype(np.float32), reference,
              directory / 'gauge_group_mean.nii.gz')
        _save(z_maps[met_index].astype(np.float32), reference,
              directory / 'gauge_z.nii.gz')
        raw_image = nib.load(directory / 'signals_raw.nii.gz')
        corrected = np.lib.format.open_memmap(
            directory / 'corrected.npy', mode='w+', dtype='float32',
            shape=raw_image.shape)
        population = nib.load(directory / 'population_mask.nii.gz').get_fdata().astype(bool)
        for index in range(raw_image.shape[3]):
            raw = np.asanyarray(raw_image.dataobj[..., index], dtype=np.float32)
            result = correct_map_voxelwise(raw, met_std[met_index], gauge_term)
            if np.any(population & ~np.isfinite(result)):
                raise ValueError(f'{metabolite}: gauge correction produced nonfinite values inside the population mask.')
            corrected[..., index] = np.nan_to_num(result, nan=0., posinf=0., neginf=0.)
        _save(corrected, reference, directory / 'signals.nii.gz')
        del corrected
        (directory / 'corrected.npy').unlink()
        _emit(f'[{analyte["label"]}] Gauge-corrected 4D signal image saved.')
    for analyte in _analytes(plan):
        if analyte['operation'] != 'contrast':
            continue
        directory = output / analyte['id']
        selected_ids = {item['id'] for item in _analytes(plan)}
        component_ids = [f'z-{name}' if f'z-{name}' in selected_ids else name
                         for name in analyte['components']]
        component_images = [nib.load(output / name / 'signals.nii.gz')
                            for name in component_ids]
        result = np.zeros(component_images[0].shape, dtype=np.float32)
        for component, coefficient in zip(component_images, analyte['coefficients']):
            result += np.asanyarray(component.dataobj, dtype=np.float32) * float(coefficient)
        _save(result, reference, directory / 'signals.nii.gz')


def execute(plan, *, prepare_only=False):
    validate_plan(plan)
    environment = dict(os.environ, FSLOUTPUTTYPE='NIFTI_GZ')
    executable = shutil.which('randomise', path=environment.get('PATH'))
    if not prepare_only and not executable:
        raise ValueError('FSL randomise was not found. Run ./install.sh or configure FSLDIR.')
    output = Path(plan['output_dir'])
    if output.exists(): raise ValueError('This run folder already exists. Choose a new run name to keep results intact.')
    tissue_paths, tissue_probabilities = report_tissue_inputs(plan)
    if tissue_paths:
        first_analyte = _analytes(plan)[0]
        first_image = _load(plan['rows'][0]['images'][first_analyte['components'][0]])
        align_tissue_masks(tissue_paths, first_image, probabilities=tissue_probabilities)
    output.mkdir(parents=True)
    atomic_text(output / 'plan.json', json.dumps(plan, indent=2, allow_nan=False) + '\n')
    model, mask = plan['model'], plan['mask']
    atomic_text(output / 'design.mat', _vest(np.asarray(model['matrix'])))
    atomic_text(output / 'design.con', _vest(np.asarray(model['contrasts']), 'NumContrasts'))
    if model.get('ftests'):
        atomic_text(output / 'design.fts', _vest(np.asarray(model['ftests']), 'NumContrasts'))
    atomic_text(output / 'subjects.txt', '\n'.join(model['subjects']) + '\n')
    pd.DataFrame(model['matrix'], columns=model['columns']).to_csv(output / 'design.tsv', sep='\t', index=False)
    pd.DataFrame(model.get('covariates', [])).to_csv(output / 'covariates.tsv', sep='\t', index=False)
    pd.DataFrame(model.get('audit', [])).to_csv(output / 'selection.tsv', sep='\t', index=False)
    if plan['randomise'].get('blocks'):
        atomic_text(output / 'design.grp', _vest(np.asarray(plan['randomise']['blocks'])[:, None]))
    reference = None
    gauge_enabled = _gauge_enabled(plan)
    gauge_group_means = {}
    analytes = _analytes(plan)
    modality = plan['data'].get('modality', 'MRSI')
    for analyte in analytes:
        if modality == 'DWI':
            reference = None  # Different scalar series can use different grids in the same space.
        met = analyte['id']
        label = analyte['label']
        print(f'[{label}] Checking 3D images and constructing masks for {len(plan["rows"])} observations', flush=True)
        directory = output / met; directory.mkdir()
        (directory / 'individual_masks').mkdir()
        supplied = None
        white_matter = None
        counts = None
        stack = None
        finite_all = None
        group_sum = None
        group_count = None
        for index, row in enumerate(plan['rows']):
            loaded_reference, signal, valid_signal = _load_analyte(row, analyte, reference)
            if reference is None:
                reference = loaded_reference
            image = reference
            if stack is None:
                stack = np.lib.format.open_memmap(directory / 'stack.npy', mode='w+', dtype='float32', shape=(*image.shape, len(plan['rows'])))
                counts = np.zeros(image.shape, dtype=np.uint32)
                finite_all = np.ones(image.shape, dtype=bool)
                if gauge_enabled:
                    group_sum = np.zeros(image.shape, dtype=np.float64)
                    group_count = np.zeros(image.shape, dtype=np.uint32)
                if mask['mode'] == 'precomputed':
                    supplied = _load(local_nifti_path(mask['precomputed'][met]), image).get_fdata(dtype=np.float32)
                    if not np.isfinite(supplied).all() or not np.isin(supplied, [0, 1]).all() or not supplied.any():
                        raise ValueError(f'{label}: supplied population mask must be nonempty, finite and binary.')
                elif mask['mode'] == 'white_matter':
                    wm = mask['white_matter']
                    white_matter = align_white_matter(local_nifti_path(wm['path']), image, probability=wm['probability'])
            finite_all &= valid_signal
            if gauge_enabled:
                group_sum[valid_signal] += signal[valid_signal]
                group_count[valid_signal] += 1
            if mask['mode'] in ('coverage', 'white_matter'):
                good = valid_signal & (signal != 0)
                if white_matter is not None:
                    good &= white_matter
            elif supplied is None:
                good = valid_signal.copy()
                quality_cache = {}
                for component in analyte['components']:
                    for metric in mask['metrics']:
                        path = row['quality'][component][metric['name']]
                        if path not in quality_cache:
                            quality_cache[path] = _load(path, image).get_fdata(dtype=np.float32)
                        values = quality_cache[path]
                        threshold = float(metric['threshold'])
                        compare = {'<': np.less, '>': np.greater, '<=': np.less_equal, '>=': np.greater_equal}[metric['operator']]
                        good &= np.isfinite(values) & compare(values, threshold)
            else:
                good = supplied.astype(bool) & valid_signal
            counts += good
            _save(good.astype(np.uint8), image, directory / 'individual_masks' / f'{row["visit"]}.nii.gz')
            stack[..., index] = np.nan_to_num(signal, nan=0., posinf=0., neginf=0.)
            current = index + 1
            total_rows = len(plan['rows'])
            if index == 0 or current == total_rows or int(current * 20 / total_rows) > int(index * 20 / total_rows):
                _emit_progress(label, 'Preparing images and masks', current, total_rows, row['visit'])
        population = supplied.astype(np.uint8) if supplied is not None else (counts * 100 >= float(mask['coverage']) * len(plan['rows'])).astype(np.uint8)
        if not population.any():
            hint = ('Review coverage or use a precomputed DWI mask.' if modality == 'DWI'
                    else 'Review quality units, thresholds, coverage or included subjects.')
            raise ValueError(f'{label}: population mask is empty. {hint}')
        if np.any(population.astype(bool) & ~finite_all):
            raise ValueError(f'{label}: invalid or nonfinite ratio/signal values occur inside the population mask. Adjust the mask or exclude affected observations.')
        _save(counts.astype(np.float32) / len(plan['rows']), reference, directory / 'coverage.nii.gz')
        _save(population, reference, directory / 'population_mask.nii.gz')
        signal_name = 'signals_raw.nii.gz' if gauge_enabled else 'signals.nii.gz'
        _save(stack, reference, directory / signal_name)
        if gauge_enabled:
            gauge_group_means[met] = np.divide(
                group_sum, group_count,
                out=np.full(image.shape, np.nan, dtype=np.float64),
                where=group_count > 0,
            )
        del stack
        (directory / 'stack.npy').unlink()
        if prepare_only:
            _emit(f'[{label}] Preparation complete (analysis not requested).')
            continue
    if gauge_enabled:
        _emit('Computing voxelwise gauge correction across selected metabolites.')
        _apply_gauge_correction(plan, reference, gauge_group_means)
    summary = []
    _, result_labels = _statistic_outputs(model)
    if not prepare_only:
        metabolites = plan['data']['metabolites']
        display_labels = [item['label'] for item in analytes]
        workers = min(plan['randomise'].get('nproc', 1), len(metabolites))
        _emit(f'Running Randomise for {len(metabolites)} {modality} map(s) with {workers} concurrent process(es); contrasts within each map are sequential.')
        if not sys.stdout.isatty():
            total = (plan['randomise']['permutations'] * len(result_labels)
                     if plan['randomise']['permutations'] > 0 else None)
            for label in display_labels:
                _emit_progress(label, 'Queued for Randomise', 0, total,
                               'Waiting for worker')
        with _metabolite_progress(display_labels, plan['randomise']['permutations'],
                                  len(result_labels)):
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='vlad-metabolite') as pool:
                futures = {pool.submit(_analyse_metabolite, plan, met, executable, environment): met
                           for met in metabolites}
                for future in as_completed(futures):
                    summary.extend(future.result())
        metabolite_order = {name: index for index, name in enumerate(metabolites)}
        contrast_order = {name: index for index, name in enumerate(result_labels)}
        summary.sort(key=lambda row: (metabolite_order[row.get('analyte', row['metabolite'])],
                                      contrast_order[row['contrast']], row['map']))
    pd.DataFrame(summary).to_csv(output / 'results.tsv', sep='\t', index=False)
    gauge_note = ('Gauge-corrected stacks were generated for z-prefixed outputs; unprefixed metabolite and ratio analyses use raw stacks.'
                  if gauge_enabled else 'Gauge correction was not applied.')
    atomic_text(output / 'results.json', json.dumps({'status': 'prepared' if prepare_only else 'succeeded', 'results': summary, 'modality': modality,
                'output_dir': str(output), 'model_columns': model['columns'], 'contrast_labels': result_labels,
                'gauge_correction': {'enabled': gauge_enabled,
                                     'term': str(output / 'gauge_term.nii.gz') if gauge_enabled else None},
                'note': gauge_note + ' FSL corrected maps contain 1-p; values >= 0.95 represent corrected p <= 0.05 within each map. Correction across metabolites or contrasts is not applied.'}, indent=2))
    from vlad.workflow.report import generate_guided_report
    report = generate_guided_report(plan, output, summary)
    _emit('Visual HTML report: ' + report['report'])
    print(f'Completed. Results: {output}', flush=True)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--report-only', action='store_true',
                        help='Regenerate the visual HTML report for an existing completed run.')
    parser.add_argument('--reference-anatomy', help='Anatomical template NIfTI for a report-only run.')
    parser.add_argument('--gm-mask', type=Path, help='Gray matter binary NIfTI for a report-only run.')
    parser.add_argument('--wm-mask', type=Path, help='White matter binary NIfTI for a report-only run.')
    args = parser.parse_args(argv)
    if args.prepare_only and args.report_only:
        parser.error('--prepare-only and --report-only cannot be combined.')
    if args.reference_anatomy and not args.report_only:
        parser.error('--reference-anatomy requires --report-only.')
    if args.gm_mask or args.wm_mask:
        if not (args.report_only and args.gm_mask and args.wm_mask):
            parser.error('--gm-mask and --wm-mask must be supplied together with --report-only.')
    try:
        from vlad.gui.commands import runtime_environment
        from vlad.paths import project_root
        root = project_root()
        os.environ.update(runtime_environment(root, root / 'VLAD_configs.txt'))
        plan = json.loads(args.plan.read_text())
        if args.report_only:
            from vlad.workflow.report import generate_guided_report
            if args.reference_anatomy:
                plan['report_reference'] = args.reference_anatomy
                plan.pop('tfce_slice_template', None)
            if args.gm_mask:
                plan['tissue_masks'] = {'gm': str(args.gm_mask), 'wm': str(args.wm_mask)}
            output = Path(plan['output_dir'])
            if not output.is_dir():
                raise ValueError(f'The run output folder does not exist: {output}')
            report = generate_guided_report(plan, output)
            print('Visual HTML report: ' + report['report'], flush=True)
        else:
            execute(plan, prepare_only=args.prepare_only)
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'Workflow failed: {exc}', file=sys.stderr, flush=True)
        return 1
