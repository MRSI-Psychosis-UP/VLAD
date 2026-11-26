"""A table-centred, six-step MRSI/DWI workflow."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
import re
import shlex
import sys

import numpy as np
import pandas as pd
import streamlit as st

from vlad.gui.cohorts import atomic_text
from vlad.gui.design import filter_rows, load_contrasts, missing_covariate_values, validate_contrasts
from vlad.gui.file_picker import path_input
from vlad.gui.templates import (_cache_home, fetch_anatomical_image, fsl_white_matter, list_anatomical_images,
                                tissue_probabilities,
                                white_matter_probability)
from vlad.workflow.core import (build_model, discover_dwi_series, discover_metabolites, dwi_grouped_series_label,
                                parse_contrast_text, read_covariates, scan_signals, scan_quality, signature,
                                validate_mask_path, validate_output_location, validate_population_paths)
from vlad.workflow.runner import validate_plan
from vlad.workflow.tissues import TISSUES, load_mask_image, load_white_matter_image, report_tissue_inputs, tissue_mask_paths

STEPS = ['1. Data', '2. Population mask', '3. Model', '4. Contrasts', '5. Randomise settings', '6. Command']
NIFTI_EXTENSIONS = ('.nii', '.nii.gz', '.NII', '.NII.GZ')
METRICS = [
    {'name': 'snr', 'desc': 'snr', 'scope': 'Global', 'unit': 'unitless', 'operator': '>', 'threshold': 10.},
    {'name': 'fwhm', 'desc': 'fwhm', 'scope': 'Global', 'unit': 'Hz', 'operator': '<', 'threshold': .1},
    {'name': 'crlb', 'desc': 'crlb', 'scope': 'Metabolite', 'unit': 'pc', 'operator': '<', 'threshold': 20.},
]
TRANSIENT_WIDGETS = {'wf_add_filter', 'wf_reset_filter', 'wf_scan_metabolites', 'wf_gauge_correction',
                     'wf_dwi_combine_run_acq'}
TRANSIENT_PREFIXES = ('wf_remove_filter_', 'wf_table_', 'wf_roles_', 'wf_metrics')
DATA_WIDGETS = {'wf_covars', 'wf_images', 'wf_space', 'wf_metabolites', 'wf_desc', 'wf_resolution'}


def _transient_widget(key):
    return key in TRANSIENT_WIDGETS or key.startswith(TRANSIENT_PREFIXES)


def _migrate_data_widgets(state, saved):
    """Seed exact widget storage from older sessions that only saved parsed Data settings."""
    for key in DATA_WIDGETS:
        saved.pop(key, None)
    if 'covars_path_current' in state or 'covars_path' in state:
        saved['wf_covars'] = state.get('covars_path_current', state.get('covars_path', ''))
    data = state.get('mrsi_data', state.get('data', {}))
    if data.get('modality') == 'DWI':
        data = state.get('signals', {}).get('config', {})
    for key, name in (('wf_images', 'folder'), ('wf_space', 'space'), ('wf_metabolites', 'metabolites'),
                      ('wf_desc', 'desc'), ('wf_resolution', 'resolution')):
        if name in data:
            saved[key] = ' '.join(data[name]) if name == 'metabolites' else data[name]
    state['raw_data_widgets'] = True


def _remember_widgets(saved):
    saved.update({key: value for key, value in st.session_state.items()
                  if key.startswith('wf_') and not _transient_widget(key)})


def _jump(step):
    st.session_state['wf_step'] = STEPS[step]


def _scan_metabolite_folder(folder):
    """Button callback: discover labels before the metabolite widget is rendered again."""
    st.session_state.pop('wf_metabolite_scan_error', None)
    st.session_state.pop('wf_metabolite_scan_message', None)
    try:
        selectors = {
            'space': st.session_state.get('wf_space', ''),
            'desc': st.session_state.get('wf_desc', ''),
            'resolution': st.session_state.get('wf_resolution', ''),
        }
        labels = discover_metabolites(folder, **selectors)
    except (OSError, ValueError) as exc:
        st.session_state['wf_metabolite_scan_error'] = str(exc)
        return
    if not labels:
        active = ', '.join(f'{key}={value}' for key, value in selectors.items() if value)
        st.session_state['wf_metabolite_scan_error'] = (
            'No met- labels were found in NIfTI filenames under this folder'
            + (f' for {active}.' if active else '.'))
        return
    derived = [item for item in re.split(r'[,\s]+', st.session_state.get('wf_metabolites', ''))
               if '/' in item or re.match(r'^z_?[A-Z]', item)]
    st.session_state['wf_metabolites'] = ' '.join([*labels, *derived])
    st.session_state['wf_metabolite_scan_message'] = (
        f'Found {len(labels)} metabolite label{"s" if len(labels) != 1 else ""} '
        'matching the current space, descriptor and resolution: ' + ', '.join(labels))


def _paths(config):
    if config.get('modality') == 'DWI':
        return {key: config[key] for key in ('modality', 'folder', 'space', 'resolution', 'dwi_series', 'combine_run_acq')
                if key in config}
    return {key: config.get(key, '') for key in ('folder', 'space', 'resolution', 'desc')} | {'metabolites': config.get('metabolites', [])}


def _quality_selection(mask):
    return {key: mask.get(key, '') for key in ('folder', 'space', 'resolution')} | {
        'metrics': [{key: row[key] for key in ('name', 'desc', 'scope')} for row in mask.get('metrics', [])]}


def _signals(state):
    result = state.get('dwi_signals' if state.get('data', {}).get('modality') == 'DWI' else 'signals')
    return result if result and result['fingerprint'] == signature(_paths(state.get('data', {}))) else None


def _mask_identity(state):
    return signature([state.get('mask', {}).get('precomputed', {}),
                      _paths(state.get('data', {}))])


def _quality(state):
    result = state.get('quality')
    mask = state.get('mask', {})
    if mask.get('mode') != 'quality' or state.get('data', {}).get('modality') == 'DWI':
        return None
    if not _signals(state): return None
    return result if result and result['fingerprint'] == signature(_quality_selection(mask)) else None


def _selection(state):
    if 'frame' not in state: raise ValueError('Load the covariates TSV in Data first.')
    missing = {}
    for scan, reason in ((_signals(state), 'Missing signal NIfTI'), (_quality(state), 'Missing quality NIfTI')):
        if scan:
            for row in scan['rows']:
                absent = row.get('missing')
                if absent is None:
                    absent = any(issue.endswith(': 0 matches') for issue in row['issues'])
                if absent:
                    missing.setdefault(row['visit'], []).append(reason)
    return filter_rows(state['frame'], state['subject'], state['session'], filters=state.get('filters', []),
                       excluded_rows=[index + 1 for index in state.get('excluded', [])],
                       automatic_exclusions=missing) | {'missing_nifti': missing}


def _table_data(state, *, quality=False):
    frame = state['frame'].copy()
    signals = _signals(state)
    found = {row['visit']: row for row in signals['rows']} if signals else {}
    qc = _quality(state)
    qc_found = {row['visit']: row for row in qc['rows']} if qc else {}
    supplied_ok = (state.get('mask', {}).get('mode') == 'precomputed' and
                   state.get('mask_check') == _mask_identity(state))
    selection = _selection(state)
    included = selection['audit']['included'].tolist()
    missing = selection['missing_nifti']
    manual_exclusions = set(state.get('excluded', []))
    table = frame.copy()
    table.insert(0, 'Exclude', [index in manual_exclusions for index in range(len(frame))])
    table.insert(1, 'Status', [
        '🔴 Excluded' if index in manual_exclusions else '🔴 Missing NIfTI' if visit in missing else
        '🟢 Included' if keep else '🟠 Filtered'
        for index, (visit, keep) in enumerate(zip(state['visits'], included))
    ])
    table.insert(2, 'Images found', [
        '—' if signals is None else ('✅' if found.get(visit, {}).get('complete', False) else '❌')
        for visit in state['visits']
    ])
    if state.get('data', {}).get('modality') != 'DWI':
        table.insert(3, 'Quality files found', [
            ('✅' if supplied_ok else ('—' if qc is None else
             ('✅' if qc_found.get(visit, {}).get('complete', False) else '❌')))
            for visit in state['visits']
        ])
    table['Matching details'] = ['; '.join(found.get(visit, {}).get('issues', []) + (qc_found.get(visit, {}).get('issues', []) if quality else [])) for visit in state['visits']]
    return table, signals, qc_found


def _table(state, *, quality=False, editable=True):
    table, signals, qc_found = _table_data(state, quality=quality)
    columns = {'Exclude': st.column_config.CheckboxColumn('Exclude'),
               'Status': st.column_config.TextColumn('Status'),
               'Images found': st.column_config.TextColumn('Images found'),
               'Quality files found': st.column_config.TextColumn('Quality files found')}
    if editable:
        # Keep source row indices fixed even when users sort the rendered table.
        key = 'wf_table_' + signature([state['table_id'], signals, qc_found,
                                       state.get('filters', []), quality])[:12]
        edited = st.data_editor(table, disabled=[name for name in table if name != 'Exclude'],
                                hide_index=True, width='stretch', height=420, column_config=columns, key=key)
        excluded = [index for index, value in enumerate(edited['Exclude']) if value]
        if excluded != state.get('excluded', []):
            state['excluded'] = excluded
            st.rerun()
    else:
        st.dataframe(table, hide_index=True, width='stretch', height=360, column_config=columns)
    selection = _selection(state)
    total, count = len(table), len(selection['frame'])
    summary = f'{total} observations · {count} included · {total - count} excluded'
    if missing := selection['missing_nifti']:
        summary += f' · {len(missing)} auto-excluded for missing NIfTIs'
    if signals:
        incomplete = {row['visit'] for row in signals['rows'] if not row['complete']}
        summary += (f' · {total - len(incomplete)} with all selected maps'
                    f' · {len(incomplete)} without a complete map match')
        if incomplete:
            audit = selection['audit']
            included_visits = set(audit.loc[audit['included'], 'subject_visit'])
            summary += f' ({len(incomplete & included_visits)} included)'
        with st.expander('Inspect matched file paths'):
            dwi = signals['config'].get('modality') == 'DWI'
            labels = {item['id']: item['label'] for item in signals['analytes']} if dwi else {}
            column = 'DWI series' if dwi else 'Metabolite'
            matches = [{'Subject / session': row['visit'], column: labels.get(met, met), 'Signal path': path,
                        **qc_found.get(row['visit'], {}).get('quality', {}).get(met, {})}
                       for row in signals['rows'] for met, path in row['images'].items()]
            st.dataframe(pd.DataFrame(matches), hide_index=True, width='stretch')
    else:
        summary += ' · Image path check pending'
    st.caption(summary + '. Checkmarks indicate unique matches. '
               'VLAD checks image shapes, grids and mask values when the command runs.')


def _filter_bar(state):
    st.markdown('#### Filters')
    a, b, c, d = st.columns([2, 2, 3, 1.2], vertical_alignment='bottom')
    available = list(state['frame'].columns)
    if st.session_state.get('wf_filter_column') not in available:
        st.session_state['wf_filter_column'] = available[0]
    column = a.selectbox('Filter covariate', available, key='wf_filter_column')
    mode = b.selectbox('Rule', ['Include values', 'Exclude values', 'Numeric range',
                                'At least', 'At most', 'Has a value'], key='wf_filter_mode')
    needs_value = mode != 'Has a value'
    value = c.text_input('Values', placeholder='CTRL, PAT or 18:65', key='wf_filter_text',
                         disabled=not needs_value)
    if d.button('+ Add filter', key='wf_add_filter', use_container_width=True):
        try:
            if mode == 'Has a value':
                rule = {'column': column, 'operator': 'not missing', 'values': []}
            elif mode in ('Include values', 'Exclude values'):
                parts = [item.strip() for item in value.split(',') if item.strip()]
                if not parts: raise ValueError('Enter at least one comma-separated filter value.')
                rule = {'column': column, 'operator': 'in' if mode == 'Include values' else 'not in',
                        'values': parts}
            elif mode == 'Numeric range':
                parts = [float(item.strip()) for item in value.split(':')]
                if len(parts) != 2: raise ValueError('Use lower:upper for a numeric range.')
                rule = {'column': column, 'operator': 'between', 'values': parts}
            else:
                try:
                    number = float(value.strip())
                except ValueError as exc:
                    raise ValueError('Enter one numeric filter limit.') from exc
                rule = {'column': column, 'operator': '>=' if mode == 'At least' else '<=',
                        'values': [number]}
        except ValueError as exc:
            st.error(str(exc))
        else:
            filters = state.setdefault('filters', [])
            if rule not in filters:
                filters.append(rule)
            st.rerun()
    filters = state.get('filters', [])
    if filters:
        st.caption('All active filters are combined with AND.')
        for index, rule in enumerate(filters):
            label, remove = st.columns([8, 1], vertical_alignment='center')
            values = ', '.join(map(str, rule['values'])) if rule['values'] else ''
            label.markdown(f'**{index + 1}.** `{rule["column"]}` `{rule["operator"]}` {values}')
            if remove.button('Remove', key=f'wf_remove_filter_{index}', use_container_width=True):
                state['filters'].pop(index)
                st.rerun()
        if st.button('Reset all filters', key='wf_reset_filter'):
            state['filters'] = []
            st.rerun()


def _dwi_config(folder):
    return {'modality': 'DWI', 'folder': folder.strip(),
            'space': st.session_state.get('wf_dwi_space', 'mni').strip().removeprefix('space-'),
            'resolution': st.session_state.get('wf_dwi_resolution', '').strip().removeprefix('res-')}


def _dwi_fields(state, folder):
    space_column, resolution_column = st.columns(2)
    st.session_state.setdefault('wf_dwi_space', 'mni')
    space_column.text_input('Space · space-', key='wf_dwi_space')
    resolution_column.text_input('Resolution · res- (optional)', key='wf_dwi_resolution')
    st.session_state.setdefault('wf_dwi_group_run_acq', True)
    combine = st.checkbox(
        'Combine run and acquisition variants', key='wf_dwi_group_run_acq',
        help='Group names that differ only in run- or acq- tags. Each visit must have one unique '
             'file for the series. Turn this off to analyze an acquisition separately.')
    if not combine:
        st.caption('Separate variants are shown. Each count applies to one exact run/acquisition pattern.')
    config = _dwi_config(folder)
    exact = signature(config)
    grouped_config = config | {'combine_run_acq': True}
    grouped = signature(grouped_config)
    if combine:
        config = grouped_config
    fingerprint = grouped if combine else exact
    key = f'wf_dwi_series_{fingerprint[:12]}'
    previous_scan = state.get('dwi_scan', {}).get('fingerprint')
    grouping_changed = previous_scan in (exact, grouped) and previous_scan != fingerprint
    scan_requested = st.button('Scan DWI folder', disabled=not (config['folder'] and config['space']),
                               help='Find 3D scalar-map series by their filename pattern and NIfTI headers.')
    if scan_requested or grouping_changed:
        state.pop('dwi_scan', None)
        try:
            with st.spinner('Finding DWI scalar-map series…'):
                series_visits = discover_dwi_series(
                    config['folder'], space=config['space'], resolution=config['resolution'],
                    combine_run_acq=combine)
            state['dwi_scan'] = {'fingerprint': fingerprint, 'series_visits': series_visits}
            if grouping_changed and combine:
                previous = st.session_state.get(f'wf_dwi_series_{exact[:12]}', [])
                if previous:
                    labels = dict.fromkeys(dwi_grouped_series_label(label) for label in previous)
                    st.session_state[key] = [label for label in labels if label in series_visits]
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    scan = state.get('dwi_scan', {})
    selected = []
    if scan.get('fingerprint') == fingerprint and 'series_visits' in scan:
        series = scan['series_visits']
        if series:
            visits = state.get('visits', [])
            options = list(series)
            labels = {label: f'{label} ({len(counts)} folder visits)' for label, counts in series.items()}
            if visits:
                matches = {label: sum(counts.get(visit) == 1 for visit in visits)
                           for label, counts in series.items()}
                options.sort(key=lambda label: (-matches[label], label.casefold()))
                labels = {label: f'{label} ({matches[label]}/{len(visits)} TSV · {len(counts)} folder)'
                          for label, counts in series.items()}
            current = st.session_state.get(key, [])
            if any(label not in series for label in current):
                st.session_state[key] = [label for label in current if label in series]
            selected = st.container(key='wf_dwi_picker').multiselect(
                'DWI scalar maps', options, key=key, format_func=labels.__getitem__,
                help='TSV counts show visits with a unique filename match. Load NIfTI paths verifies those files.')
        else:
            st.info('No 3D DWI scalar series matched this space and resolution.')
    else:
        st.caption('Choose a DWI derivative folder and scan it to list scalar maps by filename series.')
    return config | {'dwi_series': selected}


def _mrsi_config(folder):
    values = st.session_state
    return {'folder': folder.strip(), 'space': values.get('wf_space', '').strip().removeprefix('space-'),
            'metabolites': [item.removeprefix('met-') for item in re.split(
                r'[,\s]+', values.get('wf_metabolites', '').strip()) if item],
            'desc': values.get('wf_desc', '').strip().removeprefix('desc-'),
            'resolution': values.get('wf_resolution', '').strip().removeprefix('res-')}


def _mrsi_fields(state, folder):
    data = state.get('mrsi_data', state.get('data', {}))
    defaults = {
        'wf_space': data.get('space', 'MNI152NLin2009cAsym'),
        'wf_metabolites': ' '.join(data.get('metabolites', ['NAA', 'CrPCr'])),
        'wf_desc': data.get('desc', 'signal'),
        'wf_resolution': data.get('resolution', ''),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    st.text_input('Space · space-', key='wf_space')
    metabolite_field, metabolite_scan = st.columns([3, 1], vertical_alignment='bottom')
    metabolite_field.text_input(
        'Metabolites and ratios', key='wf_metabolites',
        help='List raw map labels, ratios and gauge-map contrasts, separated by spaces or commas. Examples: Glu; Glu/GABA; Glu/(Glu+GABA); (Glu+GABA)/Gln; z_Glu; zGlu-zGABA. Ratio expressions must not contain spaces.')
    metabolite_scan.button(
        'Scan folder', disabled=not folder.strip(),
        help='Recursively inspect NIfTI filenames and propose their distinct met- labels. Image data is not opened.',
        on_click=_scan_metabolite_folder, args=(folder.strip(),), use_container_width=True)
    if message := st.session_state.get('wf_metabolite_scan_message'):
        st.success(message)
    if error := st.session_state.get('wf_metabolite_scan_error'):
        st.warning(error)
    st.caption('Use Glu for the raw map and z_Glu for gauge correction. Include at least two distinct input maps, for example z_Glu GABA. For a gauge-map difference, include its component maps too, for example z_Glu z_GABA zGlu-zGABA.')
    a, b = st.columns(2)
    a.text_input('Signal descriptor · desc- (optional)', key='wf_desc')
    b.text_input('Resolution · res- (optional)', key='wf_resolution')
    return _mrsi_config(folder)


def _capture_data_widgets(state):
    """Apply Data edits that arrive together with a navigation event."""
    values = st.session_state
    table_path = values.get('wf_covars', '').strip()
    state['covars_path_current'] = str(Path(table_path).expanduser().resolve()) if table_path else ''
    if values.get('wf_modality') == 'DWI':
        config = _dwi_config(values.get('wf_dwi_images', ''))
        if values.get('wf_dwi_group_run_acq', True):
            config['combine_run_acq'] = True
        fingerprint = signature(config)
        key = f'wf_dwi_series_{fingerprint[:12]}'
        selected = values.get(key, []) if state.get('dwi_scan', {}).get('fingerprint') == fingerprint else []
        state['data'] = config | {'dwi_series': selected}
    else:
        state['data'] = state['mrsi_data'] = _mrsi_config(values.get('wf_images', ''))


def data_page(state):
    st.header('Data')
    modality = st.radio('Map modality', ['MRSI', 'DWI'], key='wf_modality', horizontal=True)
    first, second = st.columns(2)
    with first:
        table_path = path_input('Covariates TSV', key='wf_covars', value=state.get('covars_path', ''),
                                extensions=('.tsv',), placeholder='/data/derivatives/covars.tsv')
        if st.button('Load covariates', type='primary'):
            try:
                frame, visits, subject, session = read_covariates(table_path)
                reserved = {'Exclude', 'Status', 'Images found', 'Quality files found', 'Matching details'} & set(frame.columns)
                if reserved: raise ValueError('Rename reserved table columns: ' + ', '.join(sorted(reserved)))
                state.update(frame=frame, visits=visits, subject=subject, session=session,
                             table_id=signature([table_path, frame.to_csv(index=False)]),
                             covars_path=str(Path(table_path).expanduser().resolve()), filters=[], excluded=[])
                output_default = str(Path(state['covars_path']).parent)
                previous_default = state.get('study_output_default', '')
                current_output = st.session_state.get('wf_output', state.get('study_output', previous_default))
                if current_output == previous_default:
                    state['study_output'] = output_default
                    st.session_state['wf_output'] = output_default
                else:
                    state['study_output'] = current_output.strip()
                state['study_output_default'] = output_default
                for key in ('signals', 'dwi_signals', 'quality', 'model', 'roles', 'contrasts', 'mask_check'):
                    state.pop(key, None)
            except (OSError, ValueError, pd.errors.ParserError) as exc:
                st.error(str(exc))
        if modality == 'DWI':
            folder = path_input('DWI image folder', key='wf_dwi_images', kind='directory',
                                placeholder='/data/derivatives/dwi-preprocessing')
        else:
            folder = path_input('MRSI image folder', key='wf_images',
                                value=state.get('mrsi_data', state.get('data', {})).get('folder', ''),
                                kind='directory', placeholder='/data/derivatives/mrsiprep')
    with second:
        if modality == 'DWI':
            state['data'] = _dwi_fields(state, folder)
        else:
            state['data'] = state['mrsi_data'] = _mrsi_fields(state, folder)
    state['covars_path_current'] = str(Path(table_path).expanduser().resolve()) if table_path.strip() else ''
    if 'frame' not in state:
        st.info('Load the covariates TSV to display the subject/session table.')
        return
    if table_path.strip() and str(Path(table_path).expanduser().resolve()) != state['covars_path']:
        st.warning('The TSV path changed. Click Load covariates to use the new table.')
    st.caption(f'Identifiers: {state["subject"]} + {state["session"]}. BIDS prefixes are normalised; leading zeros are preserved.')
    load_help = ('Match each DWI series to its subject/session and check for 3D scalar maps.'
                 if modality == 'DWI' else 'Search filenames only. Does not read image headers or voxel arrays.')
    if st.button('Load NIfTI paths', type='primary', help=load_help):
        with st.spinner('Matching NIfTI paths…'):
            signal_key = 'dwi_signals' if modality == 'DWI' else 'signals'
            state[signal_key] = scan_signals(_paths(state['data']), state['visits'])
        if modality == 'MRSI':
            state.pop('quality', None)
    signals = _signals(state)
    if not signals:
        st.info('Click Load NIfTI paths after setting or changing the image selectors.')
    elif modality == 'DWI':
        matched = sum(row['complete'] for row in signals['rows'])
        total = len(signals['rows'])
        message = f'{matched}/{total} TSV visits have all selected DWI maps.'
        if matched == total:
            st.success(message)
        else:
            st.warning(f'{message} See Matching details for the remaining {total - matched} visits.')
    _filter_bar(state)
    _table(state)


def _dwi_white_matter_input(state):
    space = state['data']['space']
    custom = path_input('Custom white-matter NIfTI', key='wf_dwi_wm_path', kind='file', extensions=NIFTI_EXTENSIONS,
                        help='Choose a 3D .nii or .nii.gz registered to the same space as the DWI maps.').strip()
    if custom:
        try:
            path = validate_mask_path(custom, 'White-matter mask', space)
            image = load_white_matter_image(path)
        except (OSError, ValueError) as exc:
            st.warning(str(exc))
            return {}
        shape = ' × '.join(map(str, image.shape))
        st.caption(f'✅ 3D NIfTI · {shape}')
        kind = st.radio('Custom WM values', ['Binary mask', 'Probability map'], key='wf_dwi_wm_format', horizontal=True,
                        help='For a WM probability map with values from 0 to 1, VLAD keeps voxels at or above 0.5.')
        return {'path': path, 'probability': kind == 'Probability map', 'source': 'custom'}
    fsl = space.casefold() in {'fmrib58', 'fsl-mni-152'}
    if space.casefold() == 'mni':
        fsl = st.checkbox('DWI maps use FSL MNI152 / FMRIB58', key='wf_dwi_wm_fsl',
                          help='Use this source for Watson maps registered to FMRIB58.')
        if not fsl:
            return {}
    source = 'FSL MNI152 / FMRIB58' if fsl else f'TemplateFlow {space}'
    download = st.button('Download FSL WM mask' if fsl else 'Download TemplateFlow WM map')
    try:
        if download:
            validate_output_location(state, _cache_home())
        with st.spinner(f'Finding the {source} white-matter mask…') if download else nullcontext():
            path = fsl_white_matter(download=download) if fsl else white_matter_probability(space, download=download)
    except (OSError, RuntimeError, ValueError) as exc:
        st.warning(str(exc))
        return {}
    if path:
        st.caption(f'✅ {source} cache · `{path.name}`')
        return {'path': str(path), 'probability': not fsl, 'source': source}
    return {}


def mask_page(state):
    st.header('Population mask')
    signals = _signals(state)
    if not signals:
        st.info('First load the covariate table and NIfTI paths in Data.')
        return
    dwi = state['data'].get('modality') == 'DWI'
    modes = ({'Common nonzero coverage': 'coverage', 'White matter with common coverage': 'white_matter',
              'Precomputed mask per DWI series': 'precomputed'}
             if dwi else {'Construct from quality maps': 'quality', 'Precomputed mask per metabolite': 'precomputed'})
    choice = st.radio('Mask source', list(modes), key='wf_dwi_mask_mode' if dwi else 'wf_mask_mode', horizontal=True)
    previous = state.get('mask', {})
    mask = {'mode': modes[choice], 'modality': 'DWI' if dwi else 'MRSI',
            'coverage': 100. if dwi else (previous.get('coverage', 68.) if previous.get('modality') != 'DWI' else 68.)}
    if mask['mode'] == 'precomputed':
        mapping = {}
        for analyte in signals['analytes']:
            mapping[analyte['id']] = path_input(
                f'{analyte["label"]} population mask', key=('wf_dwi_mask_' if dwi else 'wf_mask_') + analyte['id'],
                extensions=NIFTI_EXTENSIONS)
        mask['precomputed'] = mapping
        state['mask'] = mask
        if st.button('Load masks', type='primary'):
            state.pop('mask_check', None)
            paths = validate_population_paths(mapping, mapping, state['data']['space'], state['data']['resolution'])
            for analyte in signals['analytes']:
                load_mask_image(paths[analyte['id']], analyte['label'])
            state['mask_check'] = _mask_identity(state)
        if state.get('mask_check') == _mask_identity(state):
            st.success('All 3D population masks found. VLAD checks binary values and image grids when the command runs.')
    elif dwi:
        if mask['mode'] == 'white_matter':
            st.caption('Keep white-matter voxels with a finite, nonzero value in every included observation.')
            mask['white_matter'] = _dwi_white_matter_input(state)
        else:
            st.caption('For each DWI series, keep voxels with a finite, nonzero value in every included observation. '
                       'Choose a precomputed mask to include valid zero-valued voxels.')
        state['mask'] = mask
    else:
        mask['folder'] = path_input('Quality-map folder', value=state['data']['folder'], key='wf_quality_folder', kind='directory')
        a, b = st.columns(2)
        st.session_state.setdefault('wf_quality_space', state['data']['space'])
        st.session_state.setdefault('wf_quality_res', state['data']['resolution'])
        mask['space'] = a.text_input('Quality space · space-', key='wf_quality_space')
        mask['resolution'] = b.text_input('Quality resolution · res- (optional)', key='wf_quality_res')
        st.caption('Global metrics match files without met-. Metabolite metrics require the same met- label as the signal. Add/remove rows for other QC metrics such as linewidth.')
        metrics = pd.DataFrame(state.get('quality_metrics', previous.get('metrics', METRICS)))
        edited = st.data_editor(metrics, hide_index=True, num_rows='dynamic', width='stretch', key='wf_metrics',
            column_config={'name': st.column_config.TextColumn('Metric', required=True),
                           'desc': st.column_config.TextColumn('Filename desc-', required=True),
                           'scope': st.column_config.SelectboxColumn('Applies to', options=['Global', 'Metabolite'], required=True),
                           'unit': st.column_config.SelectboxColumn('File values / cutoff units', options=['pc', 'Hz', 'ppm', 'unitless'], required=True),
                           'operator': st.column_config.SelectboxColumn('Pass criterion', options=['<', '>', '<=', '>='], required=True),
                           'threshold': st.column_config.NumberColumn('Cutoff', required=True, format='%.4f')})
        state['quality_metrics'] = mask['metrics'] = edited.to_dict('records')
        mask['coverage'] = st.number_input('At least this percentage of included subjects must pass (%)', min_value=0.1, max_value=100., value=68., step=1., key='wf_coverage')
        st.write(f'For each metabolite, combine all criteria per subject, then keep voxels passing in at least **{mask["coverage"]:g}%** of the final included subjects.')
        state['mask'] = mask
        if st.button('Load quality paths', type='primary'):
            with st.spinner('Matching quality-map paths…'):
                state['quality'] = scan_quality(_quality_selection(mask), signals)
        if not _quality(state): st.info('Load quality paths to check the metric filenames. Changing folder, space, resolution or metric scope requires a new check.')
    if dwi:
        _table(state, quality=True)
        return
    with st.expander('Tissue masks for report histograms', expanded=True):
        space = state['data']['space']
        st.caption('VLAD uses cached TemplateFlow tissue maps for the analysis space. To override them, provide both non-overlapping binary masks in the same space.')
        if st.button('Download TemplateFlow GM and WM'):
            try:
                validate_output_location(state, _cache_home())
                with st.spinner(f'Downloading tissue maps for {space}…'):
                    tissue_probabilities(space, download=True)
            except (OSError, RuntimeError, ValueError) as exc:
                st.warning(str(exc))
        cached = tissue_probabilities(space)
        paths, status_columns = {}, {}
        for tissue, label in TISSUES.items():
            if tissue in cached:
                field, status_columns[tissue] = st.columns([4, 1], vertical_alignment='bottom')
            else:
                field = st.container()
            with field:
                paths[tissue] = path_input(f'{label} mask', key=f'wf_tissue_{tissue}',
                                           extensions=('.nii', '.nii.gz')).strip()
        state['tissue_masks'] = paths
        if not any(paths.values()):
            for status in status_columns.values():
                status.caption('✅ TemplateFlow cache')
    _table(state, quality=True)


def _model(state):
    selection = _selection(state)
    if not state.get('roles'): raise ValueError('Choose the model variables in Model first.')
    if selection['frame'].empty:
        raise ValueError('No observations remain after filtering and NIfTI matching.')
    model = build_model(selection['frame'], state['roles'], state.get('centre', True))
    ids = selection['audit'].loc[selection['audit']['included'], 'subject_visit'].tolist()
    model.update(subjects=ids, covariates=selection['frame'].to_dict('records'), audit=selection['audit'].to_dict('records'), roles=state['roles'])
    return model


def _contrast_identity(model):
    """Invalidate reviewed contrasts when coding or generated directions change."""
    return signature([model['columns'], model['coding'], model['roles'],
                      model['contrast_labels']])


def _exclusive_checkbox_roles(edited, previous):
    """Keep the checkbox-table design while preventing overlapping roles."""
    earlier = {row['Name']: row for row in previous}
    result, changed = [], False
    for row in edited.to_dict('records'):
        row = dict(row)
        old = earlier.get(row['Name'], {})
        if bool(row.get('Confound')) and bool(row.get('Regressor')):
            if bool(old.get('Confound')) and not bool(old.get('Regressor')):
                row['Confound'] = False
            else:
                row['Regressor'] = False
            changed = True
        row['Include'] = bool(row.get('Include'))
        row['Confound'] = bool(row.get('Confound'))
        row['Regressor'] = bool(row.get('Regressor'))
        result.append(row)
    return result, changed


def _default_model_type(values):
    observed = values[~missing_covariate_values(values)]
    numeric = pd.to_numeric(observed, errors='coerce')
    return 'Numeric' if numeric.notna().all() and numeric.nunique() > 2 else 'Categorical'


def model_page(state):
    st.header('Model')
    if 'frame' not in state:
        st.info('Load the covariate table in Data first.'); return
    selection = _selection(state)
    measured = [name for name in state['frame'] if name not in (state['subject'], state['session'])]
    if not measured: raise ValueError('The covariate table needs measured variables in addition to identifiers.')
    st.caption(f'{len(selection["frame"])} included observations. Select Include and assign each included variable to Confound or Regressor. Choose exactly one tested regressor; a variable cannot be both.')
    if 'roles' not in state:
        default = next((name for name in ('Age', 'age', 'Diag', 'Group', 'Score') if name in measured), measured[0])
        state['roles'] = [{'Include': name == default, 'Name': name,
            'Type': _default_model_type(state['frame'][name]),
            'Confound': False, 'Regressor': name == default, 'Reference': ''} for name in measured]
    for row in state['roles']:
        row.setdefault('Z-score', False)
    revision = state.get('roles_editor_revision', 0)
    roles = st.data_editor(
        pd.DataFrame(state['roles']), hide_index=True, width='stretch',
        key=f'wf_roles_{state["table_id"][:12]}_{revision}',
        disabled=['Name', 'Reference'], column_config={
            'Include': st.column_config.CheckboxColumn('Include'),
            'Confound': st.column_config.CheckboxColumn('Confound'),
            'Regressor': st.column_config.CheckboxColumn('Regressor'),
            'Z-score': st.column_config.CheckboxColumn(
                'Z-score', help='For numeric confounds or regressors, subtract the mean and divide by the standard deviation of the included rows.'),
            'Type': st.column_config.SelectboxColumn(
                'Type', options=['Numeric', 'Categorical'], required=True,
                help='Numeric variables such as age use one design column. Categorical variables use one column per level beyond the reference.'),
        })
    state['roles'], corrected = _exclusive_checkbox_roles(roles, state['roles'])
    if corrected:
        state['roles_editor_revision'] = revision + 1
        st.rerun()
    for row in state['roles']:
        if row['Include'] and row['Type'] == 'Categorical':
            values = selection['frame'][row['Name']]
            levels = sorted(values[~missing_covariate_values(values)].astype(str).unique())
            if levels:
                key = 'wf_reference_' + row['Name']
                if key in st.session_state and st.session_state[key] not in levels:
                    st.session_state[key] = levels[0]
                row['Reference'] = st.selectbox('Reference category · ' + row['Name'], levels,
                                               key=key)
    st.session_state.setdefault('wf_centre', state.get('centre', True))
    state['centre'] = st.checkbox('Centre numeric confounds', key='wf_centre',
                                  help='Applies to numeric confounds without z-scoring.')
    try:
        model = _model(state)
    except (ValueError, TypeError) as exc:
        st.warning(str(exc)); return
    st.success(f'{len(model["subjects"])} observations · {len(model["columns"])} model columns · full rank')
    st.caption('Column order: intercept → confounds → tested regressor. Categorical variables use the displayed reference category.')
    st.dataframe(pd.DataFrame(model['matrix'], columns=model['columns']), width='stretch', hide_index=True)
    with st.expander('Encoding details'):
        st.json(model['coding'])


def contrast_page(state):
    st.header('Contrasts')
    try: model = _model(state)
    except ValueError as exc: st.info(str(exc)); return
    design = pd.DataFrame(model['matrix'], columns=model['columns'])
    st.write('Weights follow this exact column order:')
    st.dataframe(pd.DataFrame({'Position': range(1, len(design.columns) + 1), 'Column': design.columns}), hide_index=True, width='stretch')
    identity = _contrast_identity(model)
    modes = ['Suggested comparisons', 'Enter weights', 'Load contrast file']
    if len(model['regressors']) > 1:
        modes.insert(1, 'Omnibus ANOVA')
    if st.session_state.get('wf_contrast_mode', modes[0]) not in modes:
        st.session_state['wf_contrast_mode'] = modes[0]
    mode = st.radio('Contrast input', modes, key='wf_contrast_mode', horizontal=True)
    labels = model['contrast_labels']; contrasts = model['contrasts']
    if mode == 'Omnibus ANOVA':
        weights, basis_labels = contrasts[::2], labels[::2]
        group = next(row['Name'] for row in model['roles'] if row['Include'] and row['Regressor'])
        state['contrasts'] = {'identity': identity, 'weights': weights, 'labels': basis_labels,
                              'ftests': [[1] * len(weights)], 'ftest_labels': [f'{group}: omnibus ANOVA']}
        st.caption('The F-test jointly tests the group coefficients while adjusting for the selected confounds.')
        st.dataframe(pd.DataFrame(weights, columns=model['columns'], index=basis_labels), width='stretch')
        return
    try:
        if mode == 'Enter weights':
            default = '\n'.join(' '.join(f'{value:g}' for value in row) for row in contrasts)
            text = st.text_area('One contrast per line', value=default, key='wf_contrast_text_' + identity[:12],
                                help='For four columns, 0 0 0 1 tests the displayed forward comparison or slope > 0; 0 0 0 -1 tests its reverse. Use separate lines for both, or combine variables with mixed weights.')
            contrasts, labels = parse_contrast_text(text, model)
        elif mode == 'Load contrast file':
            path = path_input('Contrast file', key='wf_contrast_path', extensions=('.con', '.txt', '.tsv', '.csv'))
            if not path: raise ValueError('Choose a contrast file.')
            loaded = load_contrasts(Path(path), model['columns'])
            contrasts, labels = loaded['contrasts'].tolist(), loaded['labels']
        candidate_matrix, candidate_labels = validate_contrasts(contrasts, design, labels)
        selection_key = 'wf_contrast_selection_' + signature(
            [identity, mode, candidate_labels, candidate_matrix.tolist()])[:12]
        selected = st.multiselect(
            'Contrasts to test', candidate_labels, default=candidate_labels,
            key=selection_key,
            help='Both directions are selected by default. Keep either one or both.')
        if not selected:
            raise ValueError('Select at least one contrast to test.')
        selected_indices = [index for index, label in enumerate(candidate_labels)
                            if label in selected]
        matrix = candidate_matrix[selected_indices]
        labels = [candidate_labels[index] for index in selected_indices]
        state['contrasts'] = {'identity': identity, 'weights': matrix.tolist(), 'labels': labels}
        st.dataframe(pd.DataFrame(matrix, columns=model['columns'], index=labels), width='stretch')
        st.caption('Each row is a separate directional t-contrast. Labels show the tested direction and categorical reference; opposite rows test the reverse comparison.')
    except (ValueError, OSError) as exc:
        state.pop('contrasts', None)
        st.warning(str(exc))


def _tfce_slice_template(state):
    try:
        space = state.get('data', {}).get('space', '')
        preferred = 'MNI152NLin6Asym' if space.casefold() == 'mni' else space
        names = sorted(list_anatomical_images(), key=lambda name: (
            not name.startswith(f'tpl-{preferred}/'), 'res-02_T1w' not in name, name))
        options = ['Automatic', *names]
        loaded = state.get('loaded_report_template')
        if loaded and state.get('report_reference') == loaded[1] and loaded[0][1] in names:
            st.session_state.setdefault('wf_tfce_slice_template', loaded[0][1])
        name = st.selectbox('TFCE slice template', options, key='wf_tfce_slice_template',
                            format_func=lambda item: Path(item).name.removeprefix('tpl-').removesuffix('.gz').removesuffix('.nii'),
                            help='Choose TemplateFlow anatomy in the same space as the TFCE maps. '
                                 'For Watson maps registered to FMRIB58, choose MNI152NLin6Asym.')
        if name == 'Automatic':
            return None
        template = name.split('/', 1)[0].removeprefix('tpl-')
        selection = (template, name)
        if st.button('Load TFCE template'):
            validate_output_location(state, _cache_home())
            with st.spinner('Loading the TFCE slice template…'):
                path = fetch_anatomical_image(template, name)
            loaded = state['loaded_report_template'] = (selection, str(path))
        if loaded and loaded[0] == selection:
            st.caption(f'✅ TFCE slice template ready · {Path(loaded[1]).name}')
            return loaded[1]
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        st.warning(str(exc))
        if st.session_state.get('wf_tfce_slice_template', 'Automatic') == 'Automatic':
            return None
    return ''


def settings_page(state):
    st.header('Randomise settings')
    a, b = st.columns(2)
    with a:
        nperm = st.number_input('Permutations', min_value=0, max_value=1000000, value=5000, step=500, key='wf_nperm', help='0 requests exhaustive permutations.')
        tfce = st.checkbox('TFCE · threshold-free cluster enhancement', value=True, key='wf_tfce')
        voxelwise = st.checkbox('Also output voxelwise corrected p-values', value=False, key='wf_voxelwise')
        uncorr = st.checkbox('Output uncorrected p-values', value=False, key='wf_uncorrected')
    with b:
        nproc = st.number_input('Concurrent maps (nproc)', min_value=1, max_value=64, value=4, step=1,
                                key='wf_nproc', help='Maximum simultaneous Randomise processes. Contrasts within each map remain sequential.')
        smoothing = st.number_input('Variance smoothing σ (mm)', min_value=0., value=0., step=.5, key='wf_variance', help='FSL -v; smooths variance estimates, not the input signal maps. Zero disables it.')
        seed = st.number_input('Random seed', min_value=0, value=42, key='wf_seed')
        blocks_column = st.selectbox('Exchangeability-block column (optional)', ['None', *list(state.get('frame', pd.DataFrame()).columns)], key='wf_blocks',
                                    help='Permutations are restricted within blocks (-e). Default assumes independent subjects; repeated visits require an explicitly justified exchangeability scheme.')
    with st.expander('TFCE parameters'):
        c, d, e = st.columns(3)
        h = c.number_input('Height exponent H', min_value=0., value=2., key='wf_tfce_h')
        extent = d.number_input('Extent exponent E', min_value=0., value=.5, key='wf_tfce_e')
        connectivity = e.selectbox('Connectivity', [6, 26], key='wf_tfce_c')
    state['randomise'] = {'permutations': int(nperm), 'nproc': int(nproc),
                          'tfce': tfce, 'voxelwise': voxelwise, 'uncorrected': uncorr,
                          'variance_smoothing': float(smoothing), 'seed': int(seed), 'tfce_h': float(h), 'tfce_e': float(extent),
                          'tfce_c': connectivity, 'block_column': blocks_column}
    st.caption('The model includes an intercept. Image and mask preparation run sequentially. Randomise runs up to nproc maps concurrently, with contrasts within one map tested sequentially.')
    if tfce:
        with st.expander('TFCE report slices'):
            state['tfce_slice_template'] = _tfce_slice_template(state)
    else:
        state['tfce_slice_template'] = None


def compile_plan(state, output):
    if state.get('covars_path_current') != state.get('covars_path'):
        raise ValueError('Data: load the current covariates TSV path.')
    signals = _signals(state)
    if not signals: raise ValueError('Data: load NIfTI paths for the current selectors.')
    model = _model(state)
    contrasts = state.get('contrasts')
    identity = _contrast_identity(model)
    if not contrasts or contrasts['identity'] != identity:
        raise ValueError('Contrasts: review and confirm weights for the current model.')
    model['contrasts'], model['contrast_labels'] = contrasts['weights'], contrasts['labels']
    if 'ftests' in contrasts:
        model['ftests'], model['ftest_labels'] = contrasts['ftests'], contrasts['ftest_labels']
    mask = state.get('mask')
    modality = state['data'].get('modality', 'MRSI')
    if not mask or mask.get('modality', 'MRSI') != modality:
        raise ValueError(f'Configure the {modality} population mask.')
    qc = _quality(state)
    if mask['mode'] == 'quality' and not qc:
        raise ValueError('Population mask: load quality paths for the current selectors.')
    if mask['mode'] == 'precomputed' and state.get('mask_check') != _mask_identity(state):
        raise ValueError('Population mask: click Load masks to check each selected map mask.')
    indexed = {row['visit']: row for row in signals['rows']}
    indexed_quality = {row['visit']: row for row in qc['rows']} if qc else {}
    rows = []
    for visit in model['subjects']:
        row = indexed[visit]
        if not row['complete']:
            detail = '; '.join(row.get('issues', [])) or 'unknown filename mismatch'
            raise ValueError(f'{visit}: missing/ambiguous signal paths ({detail}). '
                             'Adjust the Data selectors and reload NIfTI paths, or explicitly exclude this row.')
        if qc and not indexed_quality[visit]['complete']: raise ValueError(f'{visit}: missing/ambiguous quality paths. Resolve them or explicitly exclude this row in Data.')
        rows.append({'visit': visit, 'images': row['images'], 'quality': indexed_quality.get(visit, {}).get('quality', {})})
    options = dict(state.get('randomise', {}))
    if not options: raise ValueError('Review Randomise settings first.')
    options['gauge_correction'] = any(item.get('view') == 'gauge' for item in signals['analytes'])
    column = options.pop('block_column', 'None')
    if column != 'None':
        values = _selection(state)['frame'][column].astype(str)
        if values.str.strip().eq('').any(): raise ValueError('Exchangeability labels cannot be empty.')
        options['blocks'] = (pd.factorize(values, sort=True)[0] + 1).tolist()
    plan_data = dict(state['data'])
    plan_data.update(modality=modality, metabolites=[item['id'] for item in signals['analytes']],
                     analytes=signals['analytes'],
                     requested_analytes=signals['config']['dwi_series' if modality == 'DWI' else 'metabolites'],
                     source_metabolites=signals['source_metabolites'])
    if mask['mode'] == 'precomputed':
        paths = validate_population_paths(mask['precomputed'], plan_data['metabolites'],
                                          plan_data['space'], plan_data.get('resolution', ''))
        mask = mask | {'precomputed': paths}
    plan = {'version': 1, 'output_dir': str(output), 'data': plan_data, 'mask': mask, 'rows': rows, 'model': model,
            'randomise': options, 'covariates_source': state['covars_path']}
    reference = state.get('tfce_slice_template')
    if reference == '':
        raise ValueError('Load the selected TFCE slice template in Randomise settings or choose Automatic.')
    if reference:
        plan['tfce_slice_template'] = reference
    if modality == 'MRSI' and (tissue_masks := tissue_mask_paths(state.get('tissue_masks', {}), plan_data['space'])):
        plan['tissue_masks'] = tissue_masks
    elif cached := tissue_probabilities(plan_data['space']):
        plan['tissue_probabilities'] = cached
    sources = validate_plan(plan)
    plan['sources'] = {path: [Path(path).stat().st_size, Path(path).stat().st_mtime_ns] for path in sources}
    return plan


def _command_model_summary(plan):
    """Return included model roles and meaningful tested-regressor counts."""
    roles = [row for row in plan['model'].get('roles', []) if row.get('Include')]
    variables = [{
        'Covariate': row['Name'],
        'Role': 'Regressor' if row.get('Regressor') else 'Confound',
        'Type': row['Type'],
        'Reference': row.get('Reference', '') if row['Type'] == 'Categorical' else '',
        'Z-score': bool(row.get('Z-score', False)),
    } for row in roles]
    frame = pd.DataFrame(plan['model'].get('covariates', []))
    regressor = next((row for row in roles if row.get('Regressor')), None)
    counts = []
    if regressor and regressor['Name'] in frame:
        values = frame[regressor['Name']]
        grouped = regressor['Type'] == 'Categorical' or values.nunique(dropna=False) == 2
        if grouped:
            for value, count in values.astype(str).value_counts(sort=False).items():
                counts.append({'Regressor': regressor['Name'], 'Group / value': value,
                               'Samples': int(count)})
        else:
            counts.append({'Regressor': regressor['Name'], 'Group / value': 'Continuous',
                           'Samples': int(len(values))})
    return {'samples': int(len(frame)), 'variables': variables, 'counts': counts}


def run_page(state):
    st.header('Command')
    default_study = state.get('study_output', '')
    if not default_study and state.get('covars_path'):
        default_study = str(Path(state['covars_path']).parent)
    study = path_input('Study output folder', key='wf_output', value=default_study,
                       kind='directory', allow_new=True, placeholder='/data/VLAD-study')
    state['study_output'] = study.strip()
    modality = state.get('data', {}).get('modality', 'MRSI')
    default_name = state.get('run_name_default', '')
    if not default_name.startswith(modality + '-'):
        state['run_name_default'] = modality + '-' + datetime.now().strftime('%Y%m%d-%H%M')
        if st.session_state.get('wf_run_name', default_name) == default_name:
            st.session_state['wf_run_name'] = state['run_name_default']
    st.session_state.setdefault('wf_run_name', state['run_name_default'])
    run_name = st.text_input('Run name', key='wf_run_name')
    study_key = str(Path(study).expanduser().resolve()) if study.strip() else ''
    generated = state.get('generated_command')
    if generated and ('model_summary' not in generated or
                      generated['study'] != study_key or generated['run_name'] != run_name):
        state.pop('generated_command', None)
        generated = None
    if st.button('Generate command', type='primary'):
        state.pop('generated_command', None)
        try:
            if not study.strip():
                raise ValueError('Choose the study output folder.')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', run_name):
                raise ValueError('Use letters, digits, dots, dashes and underscores for the run name.')
            study_root = Path(study).expanduser().resolve()
            if study_root.exists() and not study_root.is_dir():
                raise ValueError('Study output folder points to a file.')
            output = study_root / 'runs' / run_name
            if output.exists():
                raise ValueError('This run folder already exists. Choose a new run name to keep results intact.')
            plan = compile_plan(state, output)
            validate_output_location(plan, study_root / 'plans')
            if plan['data']['modality'] == 'MRSI' and not report_tissue_inputs(plan)[0]:
                st.warning('In Population mask, download matching TemplateFlow tissue maps or choose GM and WM mask files to include histograms.')
            study_root.mkdir(parents=True, exist_ok=True)
            (study_root / 'runs').mkdir(exist_ok=True)
            plan_path = study_root / 'plans' / (run_name + '-' + signature(plan)[:12] + '.json')
            atomic_text(plan_path, json.dumps(plan, indent=2, allow_nan=False) + '\n')
            command = shlex.join([sys.executable, '-m', 'vlad', 'workflow', '--plan', str(plan_path)])
            generated = {'study': str(study_root), 'run_name': run_name,
                         'command': command, 'plan_path': str(plan_path),
                         'model_summary': _command_model_summary(plan)}
            state['generated_command'] = generated
        except (ValueError, OSError, KeyError) as exc:
            st.info(str(exc))
    if generated:
        summary = generated['model_summary']
        st.subheader('Model recap')
        st.caption(f'{summary["samples"]} included samples')
        st.dataframe(pd.DataFrame(summary['variables']), hide_index=True,
                     width='stretch')
        if summary['counts']:
            st.dataframe(pd.DataFrame(summary['counts']), hide_index=True,
                         width='stretch')
        st.subheader('VLAD command')
        st.code(generated['command'], language='bash')
        st.caption('Plan saved to ' + generated['plan_path'])


def render(*, bids_dir=''):
    state = st.session_state.setdefault('guided_workflow', {})
    # Remove transient widget values saved by earlier VLAD versions. Streamlit
    # button state is read-only and must never be restored into session state.
    saved_widgets = state.setdefault('widgets', {})
    for key in list(saved_widgets):
        if _transient_widget(key):
            saved_widgets.pop(key, None)
    if not state.get('raw_data_widgets'):
        _migrate_data_widgets(state, saved_widgets)
    # This key was accidentally persisted by the first Scan-folder release.
    # It is safe to remove because scanning now uses a callback-only button
    # without a session-state key. Other transient editors must retain their
    # current event value until their page consumes it.
    st.session_state.pop('wf_scan_metabolites', None)
    # Keep committed values across Streamlit's cleanup of widgets on hidden steps.
    # A current widget event takes precedence over its saved entry.
    for key, value in saved_widgets.items():
        st.session_state[key] = st.session_state.get(key, value)
    if bids_dir:
        for key in ('wf_images', 'wf_dwi_images'):
            st.session_state.setdefault(key, bids_dir)
    _remember_widgets(saved_widgets)
    with st.sidebar:
        st.markdown('## VLAD workflow')
        st.caption('MRSI or DWI → population masks → voxelwise statistics')
        step = st.radio('Workflow', STEPS, key='wf_step', label_visibility='collapsed')
        included_count = st.empty()
        st.caption('Paths stay on this machine. Generate the final command, then run it in a terminal.')
    index = STEPS.index(step)
    if index != len(STEPS) - 1:
        state.pop('generated_command', None)
    previous_step = state.get('_rendered_step')
    if previous_step == 0 and index != 0:
        _capture_data_widgets(state)
    st.progress((index + 1) / len(STEPS), text=f'Step {index + 1} of {len(STEPS)}')
    try:
        if index == 0: data_page(state)
        elif index == 1: mask_page(state)
        elif index == 2: model_page(state)
        elif index == 3: contrast_page(state)
        elif index == 4: settings_page(state)
        else: run_page(state)
        if 'frame' in state:
            included_count.caption(f'{len(_selection(state)["frame"])} / {len(state["frame"])} observations included')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(str(exc))
    st.divider()
    back, _, next_ = st.columns([1, 5, 1])
    back.button('Back', disabled=index == 0, on_click=_jump, args=(max(0, index - 1),), width='stretch')
    next_.button('Next', disabled=index == len(STEPS) - 1, on_click=_jump, args=(min(len(STEPS) - 1, index + 1),), width='stretch')
    _remember_widgets(saved_widgets)
    state['_rendered_step'] = index
