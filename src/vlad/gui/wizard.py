"""A table-centred, six-step MRSI workflow inspired by the NBS preparation UI."""
from __future__ import annotations

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
from vlad.gui.design import filter_rows, load_contrasts, validate_contrasts
from vlad.gui.file_picker import path_input
from vlad.workflow.core import (build_model, discover_metabolites, parse_contrast_text,
                                read_covariates, scan_signals, scan_quality, signature,
                                validate_population_paths)
from vlad.workflow.runner import validate_plan

STEPS = ['1. Data', '2. Population mask', '3. Model', '4. Contrasts', '5. Randomise settings', '6. Command']
METRICS = [
    {'name': 'snr', 'desc': 'snr', 'scope': 'Global', 'unit': 'unitless', 'operator': '>', 'threshold': 10.},
    {'name': 'fwhm', 'desc': 'fwhm', 'scope': 'Global', 'unit': 'Hz', 'operator': '<', 'threshold': .1},
    {'name': 'crlb', 'desc': 'crlb', 'scope': 'Metabolite', 'unit': 'pc', 'operator': '<', 'threshold': 20.},
]
TRANSIENT_WIDGETS = {'wf_add_filter', 'wf_reset_filter', 'wf_scan_metabolites'}
TRANSIENT_PREFIXES = ('wf_remove_filter_', 'wf_table_', 'wf_roles_', 'wf_metrics')
DATA_WIDGETS = {'wf_covars', 'wf_images', 'wf_space', 'wf_metabolites', 'wf_desc',
                'wf_resolution', 'wf_gauge_correction'}


def _transient_widget(key):
    return key in TRANSIENT_WIDGETS or key.startswith(TRANSIENT_PREFIXES)


def _capture_data_widgets(state):
    """Copy Step 1 fields into durable workflow state before leaving the page."""
    values = {key: st.session_state.get(key) for key in DATA_WIDGETS}
    if values['wf_covars'] is not None:
        text = str(values['wf_covars']).strip()
        state['covars_path_current'] = str(Path(text).expanduser().resolve()) if text else ''
    previous = state.get('data', {})
    state['data'] = {
        'folder': str(values['wf_images'] if values['wf_images'] is not None else previous.get('folder', '')).strip(),
        'space': str(values['wf_space'] if values['wf_space'] is not None else previous.get('space', '')).strip().removeprefix('space-'),
        'metabolites': [item.removeprefix('met-') for item in re.split(
            r'[,\s]+', str(values['wf_metabolites'] if values['wf_metabolites'] is not None
                           else ' '.join(previous.get('metabolites', []))).strip()) if item],
        'desc': str(values['wf_desc'] if values['wf_desc'] is not None else previous.get('desc', '')).strip().removeprefix('desc-'),
        'resolution': str(values['wf_resolution'] if values['wf_resolution'] is not None
                          else previous.get('resolution', '')).strip().removeprefix('res-'),
        'gauge_correction': bool(values['wf_gauge_correction'] if values['wf_gauge_correction'] is not None
                                 else previous.get('gauge_correction', False)),
    }


def _restore_data_widgets(state):
    """Restore Step 1 from durable values, with the last valid scan as fallback."""
    current = state.get('data', {})
    scanned = state.get('signals', {}).get('config', {})

    widget_for = {'folder': 'wf_images', 'space': 'wf_space', 'metabolites': 'wf_metabolites',
                  'desc': 'wf_desc', 'resolution': 'wf_resolution'}

    def configured(name, default=''):
        value = current.get(name)
        if value not in (None, '', []):
            return value
        value = scanned.get(name)
        if value not in (None, '', []):
            return value
        value = st.session_state.get(widget_for[name])
        if value not in (None, '', []):
            if name == 'metabolites' and isinstance(value, str):
                return [item.removeprefix('met-') for item in re.split(r'[,\s]+', value.strip()) if item]
            return value
        return default

    values = {
        'wf_covars': (state.get('covars_path_current') or state.get('covars_path') or
                      st.session_state.get('wf_covars', '')),
        'wf_images': configured('folder', ''),
        'wf_space': configured('space', 'MNI152NLin2009cAsym'),
        'wf_metabolites': ' '.join(configured('metabolites', ['NAA', 'CrPCr'])),
        'wf_desc': configured('desc', 'signal'),
        'wf_resolution': configured('resolution', ''),
        'wf_gauge_correction': bool(current.get('gauge_correction', False)),
    }
    for key, value in values.items():
        st.session_state[key] = value
    _capture_data_widgets(state)


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
    return {key: config.get(key, '') for key in ('folder', 'space', 'resolution', 'desc')} | {'metabolites': config.get('metabolites', [])}


def _quality_selection(mask):
    return {key: mask.get(key, '') for key in ('folder', 'space', 'resolution')} | {
        'metrics': [{key: row[key] for key in ('name', 'desc', 'scope')} for row in mask.get('metrics', [])]}


def _signals(state):
    result = state.get('signals')
    return result if result and result['fingerprint'] == signature(_paths(state.get('data', {}))) else None


def _mask_identity(state):
    return signature([state.get('mask', {}).get('precomputed', {}),
                      state.get('data', {}).get('space'), state.get('data', {}).get('resolution'),
                      state.get('data', {}).get('metabolites')])


def _quality(state):
    result = state.get('quality')
    mask = state.get('mask', {})
    if not _signals(state): return None
    return result if result and result['fingerprint'] == signature(_quality_selection(mask)) else None


def _selection(state):
    if 'frame' not in state: raise ValueError('Load the covariates TSV in Data first.')
    return filter_rows(state['frame'], state['subject'], state['session'], filters=state.get('filters', []),
                       excluded_rows=[index + 1 for index in state.get('excluded', [])])


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
    manual_exclusions = set(state.get('excluded', []))
    table = frame.copy()
    table.insert(0, 'Exclude', [index in manual_exclusions for index in range(len(frame))])
    table.insert(1, 'Status', [
        '🔴 Excluded' if index in manual_exclusions else ('🟢 Included' if keep else '🟠 Filtered')
        for index, keep in enumerate(included)
    ])
    table.insert(2, 'Images found', [
        '—' if signals is None else ('✅' if found.get(visit, {}).get('complete', False) else '❌')
        for visit in state['visits']
    ])
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
    frame = state['frame']
    count = len(_selection(state)['frame'])
    if signals:
        with st.expander('Inspect matched file paths'):
            matches = [{'Subject / session': row['visit'], 'Metabolite': met, 'Signal path': path,
                        **qc_found.get(row['visit'], {}).get('quality', {}).get(met, {})}
                       for row in signals['rows'] for met, path in row['images'].items()]
            st.dataframe(pd.DataFrame(matches), hide_index=True, width='stretch')
    st.caption(f'{len(frame)} observations · {count} included · {len(frame) - count} excluded. Checkmarks mean unique paths found; 3D shape, grids and mask values are checked when the command runs.')


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


def data_page(state):
    st.header('Data')
    st.write('Choose the covariate table and locate one 3D signal map per metabolite and subject/session.')
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
                current_output = (state.get('study_output') or
                                  state.setdefault('widgets', {}).get('wf_output') or
                                  st.session_state.get('wf_output', ''))
                if not current_output or current_output == previous_default:
                    state['study_output'] = output_default
                    state.setdefault('widgets', {})['wf_output'] = output_default
                    st.session_state['wf_output'] = output_default
                else:
                    state['study_output'] = current_output
                    state.setdefault('widgets', {})['wf_output'] = current_output
                    st.session_state['wf_output'] = current_output
                state['study_output_default'] = output_default
                for key in ('signals', 'quality', 'model', 'roles', 'contrasts', 'mask_check'):
                    state.pop(key, None)
            except (OSError, ValueError, pd.errors.ParserError) as exc:
                st.error(str(exc))
        folder = path_input('MRSI image folder', key='wf_images',
                            value=state.get('data', {}).get('folder', ''), kind='directory',
                            placeholder='/data/derivatives/mrsiprep')
    data_defaults = {
        'wf_space': state.get('data', {}).get('space', 'MNI152NLin2009cAsym'),
        'wf_metabolites': ' '.join(state.get('data', {}).get('metabolites', ['NAA', 'CrPCr'])),
        'wf_desc': state.get('data', {}).get('desc', 'signal'),
        'wf_resolution': state.get('data', {}).get('resolution', ''),
        'wf_gauge_correction': bool(state.get('data', {}).get('gauge_correction', False)),
    }
    for key, value in data_defaults.items():
        st.session_state.setdefault(key, value)
    with second:
        space = st.text_input('Space · space-', key='wf_space')
        metabolite_field, metabolite_scan = st.columns([3, 1], vertical_alignment='bottom')
        metabolites = metabolite_field.text_input(
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
        st.caption('Plain labels (for example Glu) always test the raw metabolite map. Ratios are computed voxelwise from raw maps. A contrast beginning with z uses gauge-corrected maps; include each component map too, as a raw label or a z-prefixed label (for example: z_Glu z_GABA zGlu-zGABA).')
        gauge = st.checkbox(
            'Enable gauge correction for z maps and contrasts',
            key='wf_gauge_correction',
            help='Enables z_Glu and zGlu-zGABA expressions. Plain metabolite labels and ratios continue to use raw maps. Requires at least two selected map outputs; raw and diagnostic maps are retained.')
        a, b = st.columns(2)
        desc = a.text_input('Signal descriptor · desc- (optional)', key='wf_desc')
        resolution = b.text_input('Resolution · res- (optional)', key='wf_resolution')
    state['covars_path_current'] = str(Path(table_path).expanduser().resolve()) if table_path.strip() else ''
    state['data'] = {'folder': folder.strip(), 'space': space.strip().removeprefix('space-'),
                     'metabolites': [item.removeprefix('met-') for item in re.split(r'[,\s]+', metabolites.strip()) if item],
                     'desc': desc.strip().removeprefix('desc-'), 'resolution': resolution.strip().removeprefix('res-'),
                     'gauge_correction': gauge}
    if 'frame' not in state:
        st.info('Load the covariates TSV to display the subject/session table.')
        return
    if table_path.strip() and str(Path(table_path).expanduser().resolve()) != state['covars_path']:
        st.warning('The TSV path changed. Click Load covariates to use the new table.')
    st.caption(f'Identifiers: {state["subject"]} + {state["session"]}. BIDS prefixes are normalised; leading zeros are preserved.')
    if st.button('Load NIfTI paths', type='primary', help='Search filenames only. Does not read image headers or voxel arrays.'):
        with st.spinner('Matching NIfTI paths…'):
            state['signals'] = scan_signals(_paths(state['data']), state['visits'])
        state.pop('quality', None)
    if not _signals(state): st.info('Click Load NIfTI paths after setting or changing the image selectors.')
    _filter_bar(state)
    _table(state)


def mask_page(state):
    st.header('Population mask')
    signals = _signals(state)
    if not signals:
        st.info('First load the covariate table and NIfTI paths in Data.')
        return
    choice = st.radio('Mask source', ['Construct from quality maps', 'Precomputed mask per metabolite'], key='wf_mask_mode', horizontal=True)
    previous = state.get('mask', {})
    mask = {'mode': 'quality' if choice.startswith('Construct') else 'precomputed',
            'coverage': previous.get('coverage', 68.)}
    if mask['mode'] == 'precomputed':
        mapping = {}
        for analyte in signals['analytes']:
            mapping[analyte['id']] = path_input(
                f'{analyte["label"]} population mask', key='wf_mask_' + analyte['id'],
                extensions=('.nii', '.nii.gz'))
        mask['precomputed'] = mapping
        state['mask'] = mask
        if st.button('Load masks', type='primary'):
            validate_population_paths(mapping, [item['id'] for item in signals['analytes']],
                                      state['data']['space'], state['data']['resolution'])
            state['mask_check'] = _mask_identity(state)
            st.success('All metabolite mask paths found. Binary values and image grids will be checked when the command runs.')
    else:
        mask['folder'] = path_input('Quality-map folder', value=state['data']['folder'], key='wf_quality_folder', kind='directory')
        a, b = st.columns(2)
        mask['space'] = a.text_input('Quality space · space-', value=state['data']['space'], key='wf_quality_space')
        mask['resolution'] = b.text_input('Quality resolution · res- (optional)', value=state['data']['resolution'], key='wf_quality_res')
        st.caption('Global metrics match files without met-. Metabolite metrics require the same met- label as the signal. Add/remove rows for other QC metrics such as linewidth.')
        metrics = pd.DataFrame(previous.get('metrics', METRICS))
        edited = st.data_editor(metrics, hide_index=True, num_rows='dynamic', width='stretch', key='wf_metrics',
            column_config={'name': st.column_config.TextColumn('Metric', required=True),
                           'desc': st.column_config.TextColumn('Filename desc-', required=True),
                           'scope': st.column_config.SelectboxColumn('Applies to', options=['Global', 'Metabolite'], required=True),
                           'unit': st.column_config.SelectboxColumn('File values / cutoff units', options=['pc', 'Hz', 'ppm', 'unitless'], required=True),
                           'operator': st.column_config.SelectboxColumn('Pass criterion', options=['<', '>', '<=', '>='], required=True),
                           'threshold': st.column_config.NumberColumn('Cutoff', required=True, format='%.4f')})
        mask['metrics'] = edited.to_dict('records')
        mask['coverage'] = st.number_input('At least this percentage of included subjects must pass (%)', min_value=0.1, max_value=100., value=68., step=1., key='wf_coverage')
        st.write(f'For each metabolite, combine all criteria per subject, then keep voxels passing in at least **{mask["coverage"]:g}%** of the final included subjects.')
        state['mask'] = mask
        if st.button('Load quality paths', type='primary'):
            with st.spinner('Matching quality-map paths…'):
                state['quality'] = scan_quality(_quality_selection(mask), signals)
        if not _quality(state): st.info('Load quality paths to check the metric filenames. Changing folder, space, resolution or metric scope requires a new check.')
    _table(state, quality=True)


def _model(state):
    selection = _selection(state)
    if not state.get('roles'): raise ValueError('Choose the model variables in Model first.')
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
            'Type': ('Numeric' if pd.to_numeric(state['frame'][name], errors='coerce').notna().all()
                     and state['frame'][name].nunique(dropna=True) > 2 else 'Categorical'),
            'Confound': False, 'Regressor': name == default, 'Reference': ''} for name in measured]
    revision = state.get('roles_editor_revision', 0)
    roles = st.data_editor(
        pd.DataFrame(state['roles']), hide_index=True, width='stretch',
        key=f'wf_roles_{state["table_id"][:12]}_{revision}',
        disabled=['Name', 'Reference'], column_config={
            'Include': st.column_config.CheckboxColumn('Include'),
            'Confound': st.column_config.CheckboxColumn('Confound'),
            'Regressor': st.column_config.CheckboxColumn('Regressor'),
            'Type': st.column_config.SelectboxColumn(
                'Type', options=['Numeric', 'Categorical'], required=True),
        })
    state['roles'], corrected = _exclusive_checkbox_roles(roles, state['roles'])
    if corrected:
        state['roles_editor_revision'] = revision + 1
        st.rerun()
    for row in state['roles']:
        if row['Include'] and row['Type'] == 'Categorical':
            levels = sorted(selection['frame'][row['Name']].dropna().astype(str).unique())
            if levels:
                row['Reference'] = st.selectbox('Reference category · ' + row['Name'], levels,
                                               key='wf_reference_' + row['Name'])
    state['centre'] = st.checkbox('Centre numeric confounds', value=True, key='wf_centre')
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
    if st.session_state.get('wf_contrast_mode') == 'Suggested positive / negative':
        st.session_state['wf_contrast_mode'] = 'Suggested comparisons'
    mode = st.radio('Contrast input', ['Suggested comparisons', 'Enter weights', 'Load contrast file'], key='wf_contrast_mode', horizontal=True)
    labels = model['contrast_labels']; contrasts = model['contrasts']
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


def settings_page(state):
    st.header('Randomise settings')
    a, b = st.columns(2)
    with a:
        nperm = st.number_input('Permutations', min_value=0, max_value=1000000, value=5000, step=500, key='wf_nperm', help='0 requests exhaustive permutations.')
        tfce = st.checkbox('TFCE · threshold-free cluster enhancement', value=True, key='wf_tfce')
        voxelwise = st.checkbox('Also output voxelwise corrected p-values', value=False, key='wf_voxelwise')
        uncorr = st.checkbox('Output uncorrected p-values', value=False, key='wf_uncorrected')
    with b:
        nproc = st.number_input('Concurrent metabolites (nproc)', min_value=1, max_value=64, value=4, step=1,
                                key='wf_nproc', help='Maximum simultaneous Randomise processes. Contrasts within each metabolite remain sequential.')
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
                          'gauge_correction': bool(state.get('data', {}).get('gauge_correction', False)),
                          'variance_smoothing': float(smoothing), 'seed': int(seed), 'tfce_h': float(h), 'tfce_e': float(extent),
                          'tfce_c': connectivity, 'block_column': blocks_column}
    st.caption('The model includes an intercept. Image/mask preparation and optional gauge correction are sequential to limit memory and disk pressure; Randomise then runs up to nproc metabolites concurrently. Contrasts within one metabolite remain sequential.')


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
    mask = state.get('mask')
    if not mask: raise ValueError('Configure the population mask.')
    qc = _quality(state)
    if mask['mode'] == 'quality' and not qc:
        raise ValueError('Population mask: load quality paths for the current selectors.')
    if mask['mode'] == 'precomputed' and state.get('mask_check') != _mask_identity(state):
        raise ValueError('Population mask: click Load masks to check each metabolite mask.')
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
    options['gauge_correction'] = bool(state.get('data', {}).get('gauge_correction', False))
    column = options.pop('block_column', 'None')
    if column != 'None':
        values = _selection(state)['frame'][column].astype(str)
        if values.str.strip().eq('').any(): raise ValueError('Exchangeability labels cannot be empty.')
        options['blocks'] = (pd.factorize(values, sort=True)[0] + 1).tolist()
    plan_data = dict(state['data'])
    plan_data.update(metabolites=[item['id'] for item in signals['analytes']],
                     analytes=signals['analytes'],
                     requested_analytes=signals['config']['metabolites'],
                     source_metabolites=signals['source_metabolites'])
    plan = {'version': 1, 'output_dir': str(output), 'data': plan_data, 'mask': mask, 'rows': rows, 'model': model,
            'randomise': options, 'covariates_source': state['covars_path']}
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
    run_name = st.text_input('Run name', value='MRSI-' + datetime.now().strftime('%Y%m%d-%H%M'), key='wf_run_name')
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
    # This key was accidentally persisted by the first Scan-folder release.
    # It is safe to remove because scanning now uses a callback-only button
    # without a session-state key. Other transient editors must retain their
    # current event value until their page consumes it.
    st.session_state.pop('wf_scan_metabolites', None)
    # Streamlit removes widgets on other pages; keep their values independently.
    for key, value in saved_widgets.items():
        if key not in DATA_WIDGETS and key not in st.session_state:
            st.session_state[key] = value
    if bids_dir and 'wf_images' not in st.session_state and not state.get('widgets', {}).get('wf_images'):
        st.session_state['wf_images'] = bids_dir
    with st.sidebar:
        st.markdown('## VLAD workflow')
        st.caption('MRSI → population masks → voxelwise statistics')
        step = st.radio('Workflow', STEPS, key='wf_step', label_visibility='collapsed')
        if 'frame' in state:
            st.caption(f'{len(_selection(state)["frame"])} / {len(state["frame"])} observations included')
        st.caption('Paths stay on this machine. Generate the final command, then run it in a terminal.')
    index = STEPS.index(step)
    if index != len(STEPS) - 1:
        state.pop('generated_command', None)
    previous_step = state.get('_rendered_step')
    if previous_step == 0 and index != 0:
        _capture_data_widgets(state)
    if index == 0 and previous_step != 0:
        _restore_data_widgets(state)
    st.progress((index + 1) / len(STEPS), text=f'Step {index + 1} of {len(STEPS)}')
    try:
        if index == 0: data_page(state)
        elif index == 1: mask_page(state)
        elif index == 2: model_page(state)
        elif index == 3: contrast_page(state)
        elif index == 4: settings_page(state)
        else: run_page(state)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(str(exc))
    st.divider()
    back, _, next_ = st.columns([1, 5, 1])
    back.button('Back', disabled=index == 0, on_click=_jump, args=(max(0, index - 1),), width='stretch')
    next_.button('Next', disabled=index == len(STEPS) - 1, on_click=_jump, args=(min(len(STEPS) - 1, index + 1),), width='stretch')
    saved_widgets.update({
        key: value for key, value in st.session_state.items()
        if key.startswith('wf_') and key != 'wf_step'
        and key not in DATA_WIDGETS
        and not _transient_widget(key)
    })
    for key in DATA_WIDGETS:
        saved_widgets.pop(key, None)
    state['_rendered_step'] = index
