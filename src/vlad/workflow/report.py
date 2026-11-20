"""Self-contained visual report for the guided MRSI Randomise workflow."""

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


def _slice_figure(stat_path, significant, reference, output, title, cmap):
    image = nib.load(stat_path)
    values = image.get_fdata(dtype=np.float32)
    thresholded = np.where(significant, values, 0.).astype(np.float32)
    overlay = nib.Nifti1Image(thresholded, image.affine, image.header)
    display = plotting.plot_stat_map(
        overlay, bg_img=str(reference) if reference else None,
        threshold=SIGNIFICANCE - 1e-7, display_mode='mosaic', cut_coords=4,
        cmap=cmap, vmin=SIGNIFICANCE, vmax=1., colorbar=True,
        annotate=True, draw_cross=False, title=title,
    )
    display.savefig(output, dpi=140)
    display.close()


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
        valid = np.isfinite(x) & np.isfinite(values)
        ax.scatter(x[valid], values[valid], alpha=.72, s=30, color='#28536b')
        if valid.sum() >= 3 and np.ptp(x[valid]) > 0:
            slope, intercept = np.polyfit(x[valid], values[valid], 1)
            grid = np.linspace(x[valid].min(), x[valid].max(), 100)
            ax.plot(grid, intercept + slope * grid, color='#d1495b', linewidth=2)
        ax.set_xlabel(name)
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
    if summary is None:
        results_path = output / 'results.json'
        if not results_path.is_file():
            raise ValueError(f'Results metadata was not found: {results_path}')
        summary = json.loads(results_path.read_text()).get('results', [])
    assets = output / 'report_assets'
    assets.mkdir(exist_ok=True)
    reference = _cached_reference(plan)
    role = _tested_role(plan)
    covariates = plan['model'].get('covariates', [])
    subjects = plan['model'].get('subjects', [])
    if len(covariates) != len(subjects):
        raise ValueError('Report covariates and subject rows are not aligned.')
    significant_sections = []
    generated_files = []
    overview = []
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
        overview.append({'Metabolite': metabolite, 'Contrast': contrast,
                         'Corrected map': stat_path.name, 'Significant voxels': voxel_count})
        if not voxel_count:
            continue
        connectivity = plan.get('randomise', {}).get('tfce_c', 26)
        clusters, labels = _cluster_rows(significant, corrected_map, image.affine, connectivity)
        kind = 'tfce' if 'tfce' in stat_path.name else ('voxelwise' if 'vox' in stat_path.name else 'corrected')
        match = re.search(r'tstat(\d+)', stat_path.name)
        contrast_id = match.group(1) if match else str(row_number)
        prefix = f'{row_number:03d}_{_safe(metabolite)}_{kind}_tstat{contrast_id}'
        mask_path = assets / f'{prefix}_significant-mask.nii.gz'
        header = image.header.copy(); header.set_data_dtype(np.uint8)
        nib.save(nib.Nifti1Image(significant.astype(np.uint8), image.affine, header), mask_path)
        slice_path = assets / f'{prefix}_slices.png'
        _slice_figure(stat_path, significant, reference, slice_path,
                      f'{metabolite} · {contrast} · {kind} corrected p ≤ 0.05',
                      'autumn' if row_number % 2 else 'winter')
        signals_path = output / analyte_id / 'signals.nii.gz'
        signals_image = nib.load(signals_path)
        if (len(signals_image.shape) != 4 or signals_image.shape[:3] != significant.shape or
                signals_image.shape[3] != len(subjects) or
                not np.allclose(image.affine, signals_image.affine, atol=1e-4, rtol=0)):
            raise ValueError(f'{metabolite}: 4D signal stack does not match the report rows/grid.')
        signals = signals_image.get_fdata(dtype=np.float32)
        regional = np.nanmean(np.where(significant[..., None], signals, np.nan), axis=(0, 1, 2))
        extracted = pd.DataFrame({'subject_session': subjects,
                                  role['Name']: [item.get(role['Name']) for item in covariates],
                                  'mean_signal': regional})
        values_path = assets / f'{prefix}_values.tsv'
        extracted.to_csv(values_path, sep='\t', index=False)
        plot_path = assets / f'{prefix}_boxplot.svg'
        _regional_figure(regional, covariates, role, plan['model'].get('coding', {}),
                         plot_path, f'{metabolite} · {contrast}')
        generated_files.extend([mask_path.name, slice_path.name, values_path.name, plot_path.name])
        significant_sections.append(
            f'<section><h2>{_escape(metabolite)} · {_escape(contrast)}</h2>'
            f'<p class="meta">{_escape(kind)} corrected map · {voxel_count} significant voxels · '
            f'{len(clusters)} connected cluster(s). Threshold: corrected p ≤ 0.05.</p>'
            f'<div class="figures">{_embedded(slice_path, "image/png")}{_embedded(plot_path, "image/svg+xml")}</div>'
            '<p class="warning"><strong>Descriptive only:</strong> the regional values use voxels selected by this same '
            'Randomise result. Do not interpret the boxplot/regression as an independent confirmatory test.</p>'
            '<h3>Significant clusters</h3>' + _table(clusters) +
            f'<p class="meta">Assets: {_escape(mask_path.name)}, {_escape(values_path.name)}</p></section>'
        )
    contrasts = []
    for label, weights in zip(plan['model'].get('contrast_labels', []), plan['model'].get('contrasts', [])):
        contrasts.append({'Contrast': label, **dict(zip(plan['model'].get('columns', []), weights))})
    gauge = plan.get('randomise', {}).get('gauge_correction', False)
    reference_note = str(reference) if reference else 'No cached/custom anatomy found; slice plots use Nilearn background.'
    empty = ('' if significant_sections else
             '<section><h2>No significant clusters</h2><p>No corrected map contained voxels '
             'with corrected p ≤ 0.05.</p></section>')
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>VLAD visual report</title>
<style>:root{{font-family:system-ui,sans-serif;color:#183448;background:#edf3f6;line-height:1.45}}body{{max-width:1300px;margin:2rem auto;padding:0 1rem}}header,section{{background:white;padding:1.4rem 1.8rem;margin:1rem 0;border-radius:9px}}h1{{font-size:1.8rem}}h2{{color:#087f8c}}.meta{{color:#526b78;font-size:.9rem}}.figures{{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,1fr);gap:1rem;align-items:center}}img{{width:100%;height:auto}}.table-scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:.85rem}}th,td{{padding:.5rem;border-bottom:1px solid #d7e2e8;text-align:left}}th{{background:#eef5f7}}.warning{{background:#fff4d6;padding:.8rem;border-left:4px solid #d99b22}}@media(max-width:800px){{.figures{{grid-template-columns:1fr}}}}</style></head>
<body><header><h1>VLAD Randomise visual report</h1><p>{_escape(output)}</p>
<p>{len(subjects)} observations · {len(plan['data']['metabolites'])} metabolites · {len(plan['model']['contrasts'])} contrasts · gauge correction {'enabled' if gauge else 'disabled'}.</p>
<p class="meta">Anatomical reference: {_escape(reference_note)}</p></header>
<section><h2>Model and contrasts</h2><p>Design columns: {_escape(', '.join(plan['model']['columns']))}</p>{_table(contrasts)}</section>
<section><h2>Corrected-map overview</h2>{_table(overview)}</section>{empty}{''.join(significant_sections)}
<section><h2>Interpretation</h2><p>FSL corrected maps store 1-p; values ≥ 0.95 are corrected p ≤ 0.05 within each map. No additional correction across metabolites or contrasts is applied.</p><p>Cluster slices show corrected significance. Extracted regional plots are post-selection descriptive summaries.</p></section>
</body></html>'''
    (output / 'report.html').write_text(document, encoding='utf-8')
    manifest = {'report': str(output / 'report.html'), 'assets': str(assets),
                'significant_maps': len(significant_sections),
                'generated_files': generated_files,
                'reference': str(reference) if reference else None}
    (assets / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
