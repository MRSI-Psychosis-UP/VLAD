"""Visual report generation for completed guided Randomise runs."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import nibabel as nib
import numpy as np

from vlad.workflow.report import _tissue_histogram, generate_guided_report
from vlad.workflow.runner import main as workflow_main
from vlad.workflow.tissues import align_tissue_masks


class GuidedVisualReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vlad-visual-report-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.output = self.base / 'run'; self.output.mkdir()
        directory = self.output / 'NAA'; directory.mkdir()
        affine = np.diag([2., 2., 2., 1.])
        population = np.ones((9, 9, 9), dtype=np.uint8)
        nib.save(nib.Nifti1Image(population, affine), directory / 'population_mask.nii.gz')
        signals = np.ones((*population.shape, 6), dtype=np.float32)
        signals[1:3, 1:3, 1:3, :3] = 2.
        signals[1:3, 1:3, 1:3, 3:] = 5.
        signals[2, 1:3, 1:3, :] += 1.
        nib.save(nib.Nifti1Image(signals, affine), directory / 'signals.nii.gz')
        corrected = np.zeros(population.shape, dtype=np.float32)
        corrected[1:3, 1:3, 1:3] = .99
        corrected[4, 4, 4] = .97
        self.stat = directory / 'randomise_tfce_corrp_tstat1.nii.gz'
        nib.save(nib.Nifti1Image(corrected, affine), self.stat)
        raw_statistics = np.full(population.shape, 1000., dtype=np.float32)
        raw_statistics[1:3, 1:3, 1:3] = np.arange(3., 11.).reshape(2, 2, 2)
        raw_statistics[4, 4, 4] = 11.
        self.raw_stat = directory / 'randomise_tstat1.nii'
        nib.save(nib.Nifti1Image(raw_statistics, affine), self.raw_stat)
        nib.save(nib.Nifti1Image(raw_statistics + 1000, affine),
                 directory / 'randomise_tfce_tstat1.nii.gz')
        subjects = [f'{number:03d}_01' for number in range(6)]
        covariates = [{'Sex': 'F' if number < 3 else 'M'} for number in range(6)]
        self.plan = {
            'version': 1, 'output_dir': str(self.output),
            'data': {'metabolites': ['NAA'], 'space': 'NoSuchTemplate'},
            'model': {'subjects': subjects, 'covariates': covariates,
                      'columns': ['Intercept', 'Sex[M]'],
                      'contrasts': [[0., 1.]], 'contrast_labels': ['Sex: M > F'],
                      'coding': {'Sex': {'type': 'categorical', 'reference': 'F',
                                         'levels': ['F', 'M']}},
                      'roles': [{'Include': True, 'Name': 'Sex', 'Type': 'Categorical',
                                 'Confound': False, 'Regressor': True, 'Reference': 'F'}]},
            'randomise': {'permutations': 500, 'gauge_correction': False},
        }
        self.summary = [{'metabolite': 'NAA', 'contrast': 'Sex: M > F',
                         'map': str(self.stat), 'voxels_p_le_0.05': 9}]
        (self.output / 'results.json').write_text(json.dumps({'results': self.summary}))

    def test_report_embeds_cluster_slices_and_boxplot_and_exports_values(self):
        from nilearn import plotting
        with patch.object(plotting, 'plot_stat_map', wraps=plotting.plot_stat_map) as plot:
            manifest = generate_guided_report(self.plan, self.output, self.summary)
        self.assertEqual(plot.call_args.kwargs['cut_coords'], (8, 8, 8))
        report = (self.output / 'report.html').read_text()
        self.assertEqual(manifest['significant_maps'], 1)
        self.assertIn('Significant clusters', report)
        self.assertIn('Sex: M &gt; F', report)
        self.assertIn('data:image/png;base64,', report)
        self.assertIn('data:image/svg+xml;base64,', report)
        self.assertIn('Descriptive only', report)
        assets = self.output / 'report_assets'
        self.assertEqual(len(list(assets.glob('*_slices.png'))), 1)
        self.assertEqual(len(list(assets.glob('*_boxplot.svg'))), 1)
        values = list(assets.glob('*_values.tsv'))
        self.assertEqual(len(values), 1)
        self.assertEqual(len(values[0].read_text().splitlines()), 7)
        self.assertEqual(len(list(assets.glob('*_significant-mask.nii.gz'))), 1)

    def test_report_only_cli_regenerates_an_existing_run(self):
        from nilearn import plotting

        plan_path = self.base / 'plan.json'
        plan_path.write_text(json.dumps(self.plan))
        background = self.base / 'report background.nii.gz'
        nib.save(nib.Nifti1Image(np.ones((11, 11, 11), dtype=np.float32), nib.load(self.stat).affine), background)
        mask_path = self.output / 'NAA' / 'population_mask.nii.gz'
        original_mask = mask_path.read_bytes()
        with patch.object(plotting, 'plot_stat_map', wraps=plotting.plot_stat_map) as plot:
            self.assertEqual(workflow_main(['--plan', str(plan_path), '--report-only',
                                            '--reference-anatomy', background.as_uri()]), 0)
        self.assertEqual(plot.call_args.kwargs['bg_img'], str(background))
        self.assertEqual(mask_path.read_bytes(), original_mask)
        self.assertTrue((self.output / 'report.html').is_file())

    def test_selected_template_is_used_for_tfce_slices_only(self):
        from nilearn import plotting

        template = self.base / 'tfce_T1w.nii.gz'
        stat = nib.load(self.stat)
        nib.save(nib.Nifti1Image(np.ones((11, 11, 11), dtype=np.float32), stat.affine), template)
        voxelwise = self.stat.with_name('randomise_vox_corrp_tstat1.nii.gz')
        nib.save(stat, voxelwise)
        self.plan['tfce_slice_template'] = str(template)
        with patch.object(plotting, 'plot_stat_map', wraps=plotting.plot_stat_map) as plot:
            manifest = generate_guided_report(self.plan, self.output, [*self.summary, {**self.summary[0], 'map': str(voxelwise)}])
        self.assertEqual([call.kwargs['bg_img'] for call in plot.call_args_list], [str(template), None])
        self.assertEqual(manifest['tfce_slice_template'], str(template))

    def test_report_only_adds_tissue_histograms_to_an_existing_run(self):
        gm, wm = self.base / 'gm.nii.gz', self.base / 'wm.nii.gz'
        gm_values = np.zeros((17, 17, 17), dtype=np.uint8)
        gm_values[2, 2:6, 2:6] = 1
        nib.save(nib.Nifti1Image(gm_values, np.eye(4)), gm)
        stat = nib.load(self.stat)
        wm_values = np.zeros(stat.shape, dtype=np.uint8)
        wm_values[2, 1:3, 1:3] = 1
        nib.save(nib.Nifti1Image(wm_values, stat.affine), wm)
        plan_path = self.base / 'plan.json'
        plan_path.write_text(json.dumps(self.plan))
        with patch('vlad.workflow.report._tissue_histogram', wraps=_tissue_histogram) as plot:
            self.assertEqual(workflow_main(['--plan', str(plan_path), '--report-only',
                                            '--gm-mask', str(gm), '--wm-mask', str(wm)]), 0)
        np.testing.assert_array_equal(plot.call_args.args[0], np.arange(3., 12.))
        report = (self.output / 'report.html').read_text()
        self.assertNotIn('Signal distribution by tissue', report)
        self.assertLess(report.index('data:image/png;base64,'), report.index('GM/WM t-statistics in significant voxels'))
        self.assertLess(report.index('GM/WM t-statistics in significant voxels'), report.index('Each point is one included subject/session'))
        self.assertIn('GM: 4 voxels · WM: 4 voxels · Outside both masks: 1', report)
        assets = self.output / 'report_assets'
        self.assertEqual(len(list(assets.glob('*_tissue-histogram.svg'))), 1)
        self.assertEqual(json.loads((assets / 'manifest.json').read_text())['tissue_masks'],
                         {'gm': str(gm), 'wm': str(wm)})
        wm_values[1, 1, 1] = 1
        nib.save(nib.Nifti1Image(wm_values, stat.affine), wm)
        with self.assertRaisesRegex(ValueError, 'masks overlap'):
            align_tissue_masks({'gm': gm, 'wm': wm}, stat)

    def test_nonsignificant_result_has_no_tissue_panel_with_cached_maps(self):
        stat = nib.load(self.stat)
        nib.save(nib.Nifti1Image(np.zeros(stat.shape), stat.affine), self.stat)
        probability = {}
        for tissue, section in [('gm', slice(0, 3)), ('wm', slice(3, 6))]:
            values = np.full(stat.shape, .2, dtype=np.float32)
            values[section] = .8
            path = self.base / f'{tissue}_probseg.nii.gz'
            nib.save(nib.Nifti1Image(values, stat.affine), path)
            probability[tissue] = str(path)
        with patch('vlad.workflow.tissues.tissue_probabilities', return_value=probability):
            manifest = generate_guided_report(self.plan, self.output, self.summary)
        self.assertEqual(manifest['significant_maps'], 0)
        self.assertEqual(manifest['tissue_source'], 'TemplateFlow')
        self.assertFalse(any(name.endswith('_tissue-histogram.svg') for name in manifest['generated_files']))
        report = (self.output / 'report.html').read_text()
        self.assertIn('No significant clusters', report)
        self.assertNotIn('GM/WM', report)

    def test_missing_raw_t_statistic_preserves_the_rest_of_the_report(self):
        self.raw_stat.unlink()
        with patch('vlad.workflow.report.report_tissue_inputs', return_value=({'gm': 'cached', 'wm': 'cached'}, True)):
            manifest = generate_guided_report(self.plan, self.output, self.summary)
        self.assertFalse(any(name.endswith('_tissue-histogram.svg') for name in manifest['generated_files']))
        report = (self.output / 'report.html').read_text()
        self.assertIn('GM/WM histogram unavailable', report)
        self.assertIn('Significant clusters', report)


if __name__ == '__main__':
    unittest.main()
