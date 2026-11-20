"""Visual report generation for completed guided Randomise runs."""

import json
from pathlib import Path
import tempfile
import unittest

import nibabel as nib
import numpy as np

from vlad.workflow.report import generate_guided_report
from vlad.workflow.runner import main as workflow_main


class GuidedVisualReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vlad-visual-report-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.output = self.base / 'run'; self.output.mkdir()
        directory = self.output / 'NAA'; directory.mkdir()
        affine = np.diag([2., 2., 2., 1.])
        population = np.ones((5, 5, 5), dtype=np.uint8)
        nib.save(nib.Nifti1Image(population, affine), directory / 'population_mask.nii.gz')
        signals = np.ones((5, 5, 5, 6), dtype=np.float32)
        signals[1:3, 1:3, 1:3, :3] = 2.
        signals[1:3, 1:3, 1:3, 3:] = 5.
        nib.save(nib.Nifti1Image(signals, affine), directory / 'signals.nii.gz')
        corrected = np.zeros((5, 5, 5), dtype=np.float32)
        corrected[1:3, 1:3, 1:3] = .99
        corrected[4, 4, 4] = .97
        self.stat = directory / 'randomise_tfce_corrp_tstat1.nii.gz'
        nib.save(nib.Nifti1Image(corrected, affine), self.stat)
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
        manifest = generate_guided_report(self.plan, self.output, self.summary)
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
        plan_path = self.base / 'plan.json'
        plan_path.write_text(json.dumps(self.plan))
        self.assertEqual(workflow_main(['--plan', str(plan_path), '--report-only']), 0)
        self.assertTrue((self.output / 'report.html').is_file())


if __name__ == '__main__':
    unittest.main()
