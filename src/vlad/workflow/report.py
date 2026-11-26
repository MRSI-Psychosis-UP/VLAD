"""Self-contained visual report for guided MRSI and DWI Randomise runs."""

from __future__ import annotations

import base64
import html
import io
import json
import os
from pathlib import Path
import re

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import nibabel as nib
from nilearn import plotting
import numpy as np
import pandas as pd
from scipy import ndimage

from vlad.gui.templates import validate_reference_anatomy
from vlad.workflow.core import local_nifti_path, validate_output_location
from vlad.workflow.tissues import TISSUES, align_tissue_masks, report_tissue_inputs


SIGNIFICANCE = .95  # FSL corrected-p maps contain 1-p.


def _escape(value):
    return html.escape(str(value), quote=True)


def _table(rows):
    if not rows:
        return '<p>No rows.</p>'
    columns = list(dict.fromkeys(key for row in rows for key in row))
    head = ''.join(f'<th>{_escape(name)}</th>' for name in columns)
    body = ''.join('<tr>' + ''.join(f'<td>{_escape(row.get(name, ""))}</td>' for name in columns) + '</tr>'
                   for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _embedded(path, mime):
    encoded = base64.b64encode(Path(path).read_bytes()).decode('ascii')
    return f'<img src="data:{mime};base64,{encoded}" alt="{_escape(Path(path).stem)}">'


def _safe(value):
    return re.sub(r'[^A-Za-z0-9._-]+', '-', str(value)).strip('-') or 'result'


def _cached_reference(plan):
    if reference := plan.get('report_reference'):
        return validate_reference_anatomy(local_nifti_path(reference))
    custom = plan.get('data', {}).get('reference_anatomy')
    if custom and Path(custom).is_file():
        return Path(custom)
    space = str(plan.get('data', {}).get('space', '')).removeprefix('space-')
    if not space:
        return None
    roots = []
    if os.environ.get('TEMPLATEFLOW_HOME'):
        roots.append(Path(os.environ['TEMPLATEFLOW_HOME']).expanduser())
    try:
        from vlad.paths import project_root
        roots.append(project_root() / '.cache' / 'templateflow')
    except RuntimeError:
        pass
    roots.append(Path.home() / '.cache' / 'templateflow')
    candidates = []
    for root in dict.fromkeys(roots):
        folder = root / f'tpl-{space}'
        if folder.is_dir():
            candidates.extend(path for path in folder.rglob('*T1w.nii*')
                              if path.name.endswith(('.nii', '.nii.gz')) and path.stat().st_size)
    if not candidates:
        return None
    return sorted(set(candidates), key=lambda path: (
        'res-02' not in path.name, 'desc-brain' not in path.name, len(path.parts), str(path)
    ))[0]


def _cluster_rows(mask, corrected_map, affine, connectivity=26):
    structure = ndimage.generate_binary_structure(3, 1 if connectivity == 6 else 3)
    labels, count = ndimage.label(mask, structure=structure)
    rows = []
    for label in range(1, count + 1):
        indices = np.argwhere(labels == label)
        values = corrected_map[labels == label]
        peak_index = indices[int(np.argmax(values))]
        peak_xyz = nib.affines.apply_affine(affine, peak_index)
        rows.append({'Cluster': label, 'Voxels': len(indices),
                     'Peak corrected p': f'{1 - float(values.max()):.5g}',
                     'Peak x (mm)': f'{peak_xyz[0]:.2f}',
                     'Peak y (mm)': f'{peak_xyz[1]:.2f}',
                     'Peak z (mm)': f'{peak_xyz[2]:.2f}'})
    rows.sort(key=lambda row: row['Voxels'], reverse=True)
    return rows, labels


def _slice_figure(image, values, significant, reference, output, title, cmap):
    thresholded = np.where(significant, values, 0.).astype(np.float32)
    overlay = nib.as_closest_canonical(nib.Nifti1Image(thresholded, image.affine, image.header))
    figure = plt.figure(figsize=(15, 5.8))
    display = plotting.plot_stat_map(
        overlay, bg_img=str(reference) if reference else None,
        threshold=SIGNIFICANCE - 1e-7, display_mode='mosaic',
        cut_coords=tuple(min(8, size) for size in overlay.shape), figure=figure,
        cmap=cmap, vmin=SIGNIFICANCE, vmax=1., colorbar=True,
        annotate=True, draw_cross=False, title=title,
    )
    display.savefig(output, dpi=140)
    display.close()


def _significant_statistics(corrected_path, image, significant):
    match = re.fullmatch(r'(.+)_(?:tfce|vox|clustere|clusterm)_corrp_([tf]stat\d+)\.nii(?:\.gz)?',
                         corrected_path.name)
    if not match:
        raise ValueError('The corrected map filename does not identify a matching raw statistic map.')
    base = f'{match[1]}_{match[2]}'
    statistic = 'F' if match[2].startswith('f') else 't'
    paths = [corrected_path.with_name(base + suffix) for suffix in ('.nii.gz', '.nii')]
    path = next((path for path in paths if path.is_file()), None)
    if path is None:
        raise ValueError(f'The raw {statistic}-statistic map {base}.nii.gz (or .nii) is missing.')
    stats = nib.load(path)
    if stats.shape != image.shape or not np.allclose(stats.affine, image.affine, atol=1e-4, rtol=0):
        raise ValueError(f'The raw {statistic}-statistic map and corrected p-value map use different grids.')
    return stats.get_fdata(dtype=np.float32)[significant]


def _tissue_histogram(statistics, significant, tissues, output, *, statistic='t'):
    """Compare raw statistics on shared bins, normalized per tissue."""
    finite = np.isfinite(statistics)
    groups = {key: statistics[finite & mask[significant]] for key, mask in tissues.items()}
    counts = {key: len(values) for key, values in groups.items()}
    if any(counts.values()):
        edges = np.histogram_bin_edges(np.concatenate(list(groups.values())), bins=24)
        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        for (key, values), color in zip(groups.items(), ('#087f8c', '#b94f15')):
            label = f'{TISSUES[key]} · {len(values):,} voxels'
            if len(values):
                bins, _ = np.histogram(values, bins=edges)
                ax.stairs(100. * bins / len(values), edges, linewidth=2, color=color, label=label)
            else:
                ax.plot([], [], color=color, label=label)
        ax.legend(frameon=False)
        ax.set_xlabel(f'Raw {statistic}-statistic')
        ax.set_ylabel('Voxels within tissue (%)')
        ax.spines[['top', 'right']].set_visible(False)
        fig.tight_layout()
        fig.savefig(output, format='svg', bbox_inches='tight', metadata={'Date': None})
        plt.close(fig)
    classified = (tissues['gm'] | tissues['wm'])[significant]
    return counts, int((~classified).sum()), int((classified & ~finite).sum())


def _regional_figure(values, covariates, role, coding, output, title):
    name = role['Name']
    raw = pd.Series([row.get(name) for row in covariates], name=name)
    values = np.asarray(values, dtype=float)
    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    if role.get('Type') == 'Categorical':
        observed = raw.astype(str)
        levels = [str(value) for value in coding.get(name, {}).get('levels', [])]
        levels.extend(value for value in observed.dropna().unique() if value not in levels)
        groups = [values[observed.to_numpy() == level] for level in levels]
        groups = [group[np.isfinite(group)] for group in groups]
        positions = np.arange(len(levels))
        ax.boxplot(groups, positions=positions, widths=.5, showfliers=False,
                   medianprops={'color': '#087f8c', 'linewidth': 2})
        rng = np.random.default_rng(42)
        for position, group in zip(positions, groups):
            ax.scatter(position + rng.uniform(-.12, .12, len(group)), group,
                       alpha=.7, s=28, color='#28536b')
        ax.set_xticks(positions, levels)
        ax.set_xlabel(name)
    else:
        x = pd.to_numeric(raw, errors='coerce').to_numpy(dtype=float)
        encoding = coding.get(name, {})
        if encoding.get('transform') == 'z-score':
            x = (x - encoding['subtracted_mean']) / encoding['standard_deviation']
        valid = np.isfinite(x) & np.isfinite(values)
        ax.scatter(x[valid], values[valid], alpha=.72, s=30, color='#28536b')
        if valid.sum() >= 3 and np.ptp(x[valid]) > 0:
            slope, intercept = np.polyfit(x[valid], values[valid], 1)
            grid = np.linspace(x[valid].min(), x[valid].max(), 100)
            ax.plot(grid, intercept + slope * grid, color='#d1495b', linewidth=2)
        ax.set_xlabel(f'{name} (z-score)' if encoding.get('transform') == 'z-score' else name)
    ax.set_ylabel('Mean signal in significant voxels')
    ax.set_title(title + '\nDescriptive post-selection summary', fontsize=10)
    ax.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, format='svg', bbox_inches='tight', metadata={'Date': None})
    plt.close(fig)


def _tested_role(plan):
    roles = [row for row in plan['model'].get('roles', [])
             if row.get('Include') and row.get('Regressor')]
    if len(roles) != 1:
        raise ValueError('The visual report needs exactly one tested regressor role.')
    return roles[0]


def generate_guided_report(plan, output, summary=None):
    """Generate report.html plus reproducible visual-report assets."""
    output = Path(output)
    validate_output_location(plan, output)
    raw_before_stack_metadata = False
    if summary is None:
        results_path = output / 'results.json'
        if not results_path.is_file():
            raise ValueError(f'Results metadata was not found: {results_path}')
        metadata = json.loads(results_path.read_text())
        summary = metadata.get('results', [])
        raw_before_stack_metadata = 'unprefixed metabolite and ratio analyses use raw stacks' in metadata.get('note', '')
    assets = output / 'report_assets'
    validate_output_location(plan, assets)
    validate_output_location(plan, output / 'report.html')
    assets.mkdir(exist_ok=True)
    reference = _cached_reference(plan)
    selected_template = plan.get('tfce_slice_template')
    tfce_template = validate_reference_anatomy(local_nifti_path(selected_template)) if selected_template else reference
    tissue_paths, probabilities = report_tissue_inputs(plan)
    views = {item['id']: item.get('view', 'raw') for item in plan['data'].get('analytes', [])}
    tissue_grids = {}
    role = _tested_role(plan)
    covariates = plan['model'].get('covariates', [])
    subjects = plan['model'].get('subjects', [])
    if len(covariates) != len(subjects):
        raise ValueError('Report covariates and subject rows are not aligned.')
    sections = []
    significant_maps = 0
    generated_files = []
    overview = []
    dwi = plan['data'].get('modality') == 'DWI'
    map_column = 'DWI series' if dwi else 'Metabolite'
    for row_number, result in enumerate(summary, 1):
        stat_path = Path(result['map'])
        if not stat_path.is_file():
            overview.append({**result, 'report status': 'map missing'})
            continue
        metabolite = result['metabolite']
        analyte_id = result.get('analyte', metabolite)
        contrast = result['contrast']
        image = nib.load(stat_path)
        corrected_map = image.get_fdata(dtype=np.float32)
        population_path = output / analyte_id / 'population_mask.nii.gz'
        population_image = nib.load(population_path)
        population = population_image.get_fdata().astype(bool)
        if (corrected_map.shape != population.shape or
                not np.allclose(image.affine, population_image.affine, atol=1e-4, rtol=0)):
            raise ValueError(f'{metabolite}: corrected map and population mask grids differ.')
        significant = np.isfinite(corrected_map) & population & (corrected_map >= SIGNIFICANCE)
        voxel_count = int(significant.sum())
        overview.append({map_column: metabolite, 'Contrast': contrast,
                         'Corrected map': stat_path.name, 'Significant voxels': voxel_count})
        if not voxel_count:
            continue
        significant_maps += 1
        kind = 'tfce' if 'tfce' in stat_path.name else ('voxelwise' if 'vox' in stat_path.name else 'corrected')
        match = re.search(r'_corrp_([tf]stat\d+)\.nii', stat_path.name)
        statistic_id = match[1] if match else f'tstat{row_number}'
        statistic = 'F' if statistic_id.startswith('f') else 't'
        prefix = f'{row_number:03d}_{_safe(analyte_id if dwi else metabolite)}_{kind}_{statistic_id}'
        tissue_panel = '' if dwi else '<p class="meta">GM/WM tissue maps are unavailable for this space.</p>'
        if tissue_paths:
            try:
                statistics = _significant_statistics(stat_path, image, significant)
            except (OSError, ValueError, nib.filebasedimages.ImageFileError) as exc:
                tissue_panel = f'<p class="meta">GM/WM histogram unavailable. {_escape(exc)}</p>'
            else:
                grid = (image.shape, image.affine.tobytes())
                if grid not in tissue_grids:
                    tissue_grids[grid] = align_tissue_masks(tissue_paths, image, probabilities=probabilities)
                histogram_path = assets / f'{prefix}_tissue-histogram.svg'
                counts, unclassified, nonfinite = _tissue_histogram(
                    statistics, significant, tissue_grids[grid], histogram_path, statistic=statistic)
                if any(counts.values()):
                    generated_files.append(histogram_path.name)
                    histogram = _embedded(histogram_path, 'image/svg+xml')
                    limits = f'Outside both masks: {unclassified:,}'
                    if nonfinite:
                        limits += f' · Nonfinite {statistic}-statistics: {nonfinite:,}'
                    tissue_panel = (
                        f'<figure class="plot"><h3>GM/WM {statistic}-statistics in significant voxels</h3>'
                        f'{histogram}<figcaption class="meta">GM: {counts["gm"]:,} voxels · '
                        f'WM: {counts["wm"]:,} voxels · {limits}.</figcaption></figure>'
                    )
                else:
                    tissue_panel = f'<p class="meta">No significant voxels have both an assigned tissue and a finite {statistic}-statistic.</p>'
        connectivity = plan.get('randomise', {}).get('tfce_c', 26)
        clusters, _ = _cluster_rows(significant, corrected_map, image.affine, connectivity)
        mask_path = assets / f'{prefix}_significant-mask.nii.gz'
        header = image.header.copy(); header.set_data_dtype(np.uint8)
        nib.save(nib.Nifti1Image(significant.astype(np.uint8), image.affine, header), mask_path)
        slice_path = assets / f'{prefix}_slices.png'
        _slice_figure(image, corrected_map, significant, tfce_template if kind == 'tfce' else reference, slice_path,
                      f'{metabolite} · {contrast} · {kind} corrected p ≤ 0.05',
                      'autumn' if row_number % 2 else 'winter')
        stack_name = result.get('signal_stack', 'signals.nii.gz')
        if raw_before_stack_metadata and 'signal_stack' not in result and views.get(analyte_id, 'raw') == 'raw':
            stack_name = 'signals_raw.nii.gz'
        if stack_name not in {'signals.nii.gz', 'signals_raw.nii.gz'}:
            raise ValueError(f'{metabolite}: invalid signal stack name in results metadata.')
        signals_image = nib.load(output / analyte_id / stack_name, keep_file_open=True)
        if (len(signals_image.shape) != 4 or signals_image.shape[:3] != significant.shape or
                signals_image.shape[3] != len(subjects) or
                not np.allclose(image.affine, signals_image.affine, atol=1e-4, rtol=0)):
            raise ValueError(f'{metabolite}: 4D signal stack does not match the report rows/grid.')
        regional = [np.nanmean(np.asarray(signals_image.dataobj[..., index], dtype=np.float32)[significant])
                    for index in range(len(subjects))]
        extracted = pd.DataFrame({'subject_session': subjects,
                                  role['Name']: [item.get(role['Name']) for item in covariates],
                                  'mean_signal': regional})
        values_path = assets / f'{prefix}_values.tsv'
        extracted.to_csv(values_path, sep='\t', index=False)
        plot_path = assets / f'{prefix}_boxplot.svg'
        _regional_figure(regional, covariates, role, plan['model'].get('coding', {}),
                         plot_path, f'{metabolite} · {contrast}')
        generated_files.extend([mask_path.name, slice_path.name, values_path.name, plot_path.name])
        sections.append(
            f'<section><h2>{_escape(metabolite)} · {_escape(contrast)}</h2>'
            f'<p class="meta">{_escape(kind)} corrected map · {voxel_count} significant voxels · '
            f'{len(clusters)} connected cluster(s). Threshold: corrected p ≤ 0.05.</p>'
            f'{_embedded(slice_path, "image/png")}'
            f'{tissue_panel}'
            f'<figure class="plot">{_embedded(plot_path, "image/svg+xml")}'
            '<figcaption class="meta">Each point is one included subject/session, averaged across the significant '
            'voxels for this result.</figcaption></figure>'
            '<p class="warning"><strong>Descriptive only:</strong> the regional values use voxels selected by this same '
            'Randomise result. Do not interpret the boxplot/regression as an independent confirmatory test.</p>'
            '<h3>Significant clusters</h3>' + _table(clusters) +
            f'<p class="meta">Assets: {_escape(mask_path.name)}, {_escape(values_path.name)}</p></section>'
        )
    contrasts = []
    for label, weights in zip(plan['model'].get('contrast_labels', []), plan['model'].get('contrasts', [])):
        contrasts.append({'Contrast': label, **dict(zip(plan['model'].get('columns', []), weights))})
    standardized = [{'Variable': name, 'Mean': f'{encoding["subtracted_mean"]:.6g}',
                     'Standard deviation': f'{encoding["standard_deviation"]:.6g}'}
                    for name, encoding in plan['model'].get('coding', {}).items()
                    if encoding.get('transform') == 'z-score']
    encoding_note = '<h3>Z-scored variables</h3>' + _table(standardized) if standardized else ''
    gauge = plan.get('randomise', {}).get('gauge_correction', False) or 'gauge' in views.values()
    map_kind = 'DWI scalar maps' if dwi else 'metabolites'
    gauge_note = '' if dwi else f' · gauge correction {"enabled" if gauge else "disabled"}'
    template_heading = 'TFCE slice template' if selected_template else 'Slice template'
    template_note = str(tfce_template) if tfce_template else 'Nilearn default anatomy'
    empty = ('' if significant_maps else
             '<section><h2>No significant clusters</h2><p>No corrected map contained voxels '
             'with corrected p ≤ 0.05.</p></section>')
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>VLAD visual report</title>
<style>:root{{font-family:system-ui,sans-serif;color:#183448;background:#edf3f6;line-height:1.45}}body{{max-width:1300px;margin:2rem auto;padding:0 1rem}}header,section{{background:white;padding:1.4rem 1.8rem;margin:1rem 0;border-radius:9px}}h1{{font-size:1.8rem}}h2{{color:#087f8c}}.meta{{color:#526b78;font-size:.9rem}}.plot{{max-width:720px;margin:1rem auto}}img{{width:100%;height:auto}}.table-scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:.85rem}}th,td{{padding:.5rem;border-bottom:1px solid #d7e2e8;text-align:left}}th{{background:#eef5f7}}.warning{{background:#fff4d6;padding:.8rem;border-left:4px solid #d99b22}}</style></head>
<body><header><h1>VLAD Randomise visual report</h1><p>{_escape(output)}</p>
<p>{len(subjects)} observations · {len(plan['data']['metabolites'])} {map_kind} · {len(plan['model']['contrasts'])} contrasts{gauge_note}.</p>
<p class="meta">{template_heading}: {_escape(template_note)}</p></header>
<section><h2>Model and contrasts</h2><p>Design columns: {_escape(', '.join(plan['model']['columns']))}</p>{_table(contrasts)}{encoding_note}</section>
<section><h2>Corrected-map overview</h2>{_table(overview)}</section>{empty}{''.join(sections)}
<section><h2>Interpretation</h2><p>FSL corrected maps store 1-p; values ≥ 0.95 are corrected p ≤ 0.05 within each map. No additional correction across metabolites or contrasts is applied.</p><p>Cluster slices show corrected significance. Extracted regional plots are post-selection descriptive summaries.</p></section>
</body></html>'''
    (output / 'report.html').write_text(document, encoding='utf-8')
    manifest = {'report': str(output / 'report.html'), 'assets': str(assets),
                'significant_maps': significant_maps,
                'generated_files': generated_files,
                'reference': str(reference) if reference else None,
                'tfce_slice_template': str(tfce_template) if selected_template else None,
                'tissue_masks': tissue_paths,
                'tissue_source': ('TemplateFlow' if probabilities else 'custom') if tissue_paths else None}
    (assets / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
