"""Path-only discovery, model ordering and actual mask/stack execution."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import nibabel as nib
import numpy as np
import pandas as pd

from vlad.gui.wizard import (METRICS, compile_plan, _contrast_identity,
                             _mask_identity, _table_data)
from vlad.workflow.core import (read_covariates, discover_metabolites, scan_signals,
                               scan_quality, build_model, parse_contrast_text,
                               population_mask, parse_analytes)
from vlad.workflow.runner import (execute, randomise_command, validate_plan,
                                  _run_randomise, _emit_progress,
                                  _metabolite_progress)
from vlad.workflow.gauge import compute_gauge, correct_map_voxelwise
import vlad.workflow.runner as workflow_runner
from vlad.gui.jobs import parse_progress


class GuidedFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vlad-guided-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.inputs = self.base / 'inputs'; self.inputs.mkdir()
        self.table = self.base / 'covars.tsv'
        pd.DataFrame({'participant_id': [f'sub-{i:03d}' for i in range(8)], 'session_id': ['ses-01'] * 8,
                      'Age': [21, 27, 28, 39, 24, 35, 48, 40], 'Sex': ['F', 'M'] * 4}).to_csv(self.table, sep='\t', index=False)
        self.frame, self.visits, self.subject, self.session = read_covariates(self.table)
        self.data = {'folder': str(self.inputs), 'space': 'MNI', 'resolution': '3', 'desc': 'signal', 'metabolites': ['NAA', 'CrPCr']}
        rng = np.random.default_rng(12)
        for index, visit in enumerate(self.visits):
            for metric, value in [('snr', 20.), ('fwhm', .05)]:
                self.save(visit, metric, np.full((2, 2, 2), value))
            for met in self.data['metabolites']:
                self.save(visit, 'signal', rng.normal(size=(2, 2, 2)) + index, met)
                values = np.full((2, 2, 2), 10.)
                if index < 2 and met == 'NAA': values[0, 0, 0] = 30
                self.save(visit, 'crlb', values, met)
        self.metrics = deepcopy(METRICS)

    def save(self, visit, desc, values, met=None, resolution='3', affine=None):
        sub, ses = visit.split('_')
        path = self.inputs / f'sub-{sub}_ses-{ses}_space-MNI_res-{resolution}{"_met-" + met if met else ""}_desc-{desc}_mrsi.nii.gz'
        nib.save(nib.Nifti1Image(np.asarray(values, dtype=np.float32), np.eye(4) if affine is None else affine), path)
        return path

    def state(self):
        signals = scan_signals(self.data, self.visits)
        mask = {'mode': 'quality', 'folder': str(self.inputs), 'space': 'MNI', 'resolution': '3',
                'coverage': 75., 'metrics': self.metrics}
        qc_config = {key: mask[key] for key in ('folder', 'space', 'resolution')}
        qc_config['metrics'] = [{key: row[key] for key in ('name', 'desc', 'scope')} for row in self.metrics]
        roles = [{'Include': True, 'Name': 'Age', 'Type': 'Numeric', 'Confound': False, 'Regressor': True, 'Reference': ''},
                 {'Include': True, 'Name': 'Sex', 'Type': 'Categorical', 'Confound': True, 'Regressor': False, 'Reference': 'F'}]
        model = build_model(self.frame, roles)
        return dict(data=self.data, frame=self.frame, visits=self.visits,
                    subject=self.subject, session=self.session, signals=signals,
                    mask=mask, quality=scan_quality(qc_config, signals), roles=roles,
                    covars_path=str(self.table), covars_path_current=str(self.table),
                    contrasts={'identity': _contrast_identity({**model, 'roles': roles}),
                               'weights': model['contrasts'], 'labels': model['contrast_labels']},
                    randomise={'permutations': 2, 'tfce': True, 'voxelwise': True, 'uncorrected': True,
                               'tfce_h': 2., 'tfce_e': .5, 'tfce_c': 6, 'seed': 42, 'variance_smoothing': 0.})

    def plan(self): return compile_plan(self.state(), self.base / 'output')


class GuidedWorkflowTests(GuidedFixture):
    def test_analyte_parser_supports_pairwise_and_sum_ratios(self):
        pairwise = parse_analytes(['Glu/GABA'])
        self.assertEqual(pairwise['source_metabolites'], ['Glu', 'GABA'])
        self.assertEqual(pairwise['analytes'], [{
            'id': 'ratio-Glu-over-GABA', 'label': 'Glu/GABA',
            'operation': 'ratio', 'numerator': 'Glu',
            'denominator': ['GABA'], 'components': ['Glu', 'GABA'],
        }])

        summed = parse_analytes(['Glu', 'GABA', 'Glu/SUMOFMETABOLITES'])
        self.assertEqual([item['id'] for item in summed['analytes']],
                         ['Glu', 'GABA', 'ratio-Glu-over-SUMOFMETABOLITES'])
        self.assertEqual(summed['analytes'][-1]['denominator'], ['Glu', 'GABA'])
        with self.assertRaisesRegex(ValueError, 'explicitly selected'):
            parse_analytes(['Glu/SUMOFMETABOLITES'])
        with self.assertRaisesRegex(ValueError, 'same numerator'):
            parse_analytes(['Glu/Glu'])

    def test_ratio_maps_are_derived_voxelwise_and_use_safe_output_names(self):
        state = self.state()
        state['data'] = {**self.data, 'metabolites': [
            'NAA', 'CrPCr', 'NAA/CrPCr', 'NAA/SUMOFMETABOLITES',
        ]}
        state['signals'] = scan_signals(state['data'], self.visits)
        state['quality'] = scan_quality(state['quality']['config'], state['signals'])
        plan = compile_plan(state, self.base / 'ratios')
        self.assertEqual(plan['data']['metabolites'], [
            'NAA', 'CrPCr', 'ratio-NAA-over-CrPCr',
            'ratio-NAA-over-SUMOFMETABOLITES',
        ])
        execute(plan, prepare_only=True)

        numerator = nib.load(plan['rows'][0]['images']['NAA']).get_fdata()
        denominator = nib.load(plan['rows'][0]['images']['CrPCr']).get_fdata()
        pairwise = nib.load(
            self.base / 'ratios/ratio-NAA-over-CrPCr/signals.nii.gz').get_fdata()[..., 0]
        summed = nib.load(
            self.base / 'ratios/ratio-NAA-over-SUMOFMETABOLITES/signals.nii.gz').get_fdata()[..., 0]
        np.testing.assert_allclose(pairwise, numerator / denominator, rtol=1e-5, atol=1e-6)
        np.testing.assert_allclose(summed, numerator / (numerator + denominator),
                                   rtol=1e-5, atol=1e-6)
        self.assertTrue((self.base / 'ratios/report.html').is_file())

    def test_metabolite_scan_reads_filename_entities_without_opening_images(self):
        (self.inputs / 'ignore_met-Asp_desc-signal_mrsi.txt').write_text('not a NIfTI')
        with patch('nibabel.load', side_effect=AssertionError('Filename scan must not open images')):
            self.assertEqual(discover_metabolites(self.inputs), ['CrPCr', 'NAA'])
            self.assertEqual(discover_metabolites(
                self.inputs, space='MNI', desc='signal', resolution='3'), ['CrPCr', 'NAA'])
            self.assertEqual(discover_metabolites(
                self.inputs, space='MNI', desc='signal', resolution='1'), [])
        with self.assertRaisesRegex(ValueError, 'existing image folder'):
            discover_metabolites(self.base / 'missing')

    def test_table_uses_visible_match_and_population_indicators(self):
        state = self.state()
        state.update(filters=[], excluded=[])
        table, _, _ = _table_data(state, quality=True)
        self.assertEqual(set(table['Images found']), {'✅'})
        self.assertEqual(set(table['Quality files found']), {'✅'})
        self.assertEqual(set(table['Status']), {'🟢 Included'})
        state['excluded'] = [0]
        table, _, _ = _table_data(state, quality=True)
        self.assertEqual(table.loc[0, 'Status'], '🔴 Excluded')

    def test_covariates_require_participant_and_session_columns(self):
        wrong = self.base / 'subject-id.tsv'
        pd.DataFrame({'subject_id': ['sub-001'], 'session_id': ['ses-01']}).to_csv(
            wrong, sep='\t', index=False)
        with self.assertRaisesRegex(ValueError, 'uses participant_id, not subject_id'):
            read_covariates(wrong)

    def test_covariate_path_errors_are_explicit_instead_of_reading_dot(self):
        with self.assertRaisesRegex(ValueError, 'Choose a covariates TSV'):
            read_covariates('')
        with self.assertRaisesRegex(ValueError, 'not a directory'):
            read_covariates(self.base)

    def test_discovery_never_opens_images_and_tracks_all_metabolites(self):
        with patch('nibabel.load', side_effect=AssertionError('Discovery must not read NIfTI headers/data')):
            state = self.state()
        self.assertTrue(all(row['complete'] for row in state['signals']['rows']))
        self.assertTrue(all(row['complete'] for row in state['quality']['rows']))
        missing = Path(state['signals']['rows'][0]['images']['NAA']); missing.unlink()
        scanned = scan_signals(self.data, self.visits)
        self.assertFalse(scanned['rows'][0]['complete'])
        self.assertTrue(scanned['rows'][1]['complete'])

    def test_missing_desc_is_optional_but_ambiguity_is_never_silently_resolved(self):
        scanned = scan_signals({**self.data, 'desc': ''}, self.visits)
        self.assertFalse(scanned['rows'][0]['complete'])
        self.assertIn('2 matches', scanned['rows'][0]['issues'][0])

    def test_quality_scope_and_resolution_are_explicit(self):
        state = self.state()
        self.save(self.visits[0], 'snr', np.ones((2, 2, 2)), met='NAA')
        qc = scan_quality(state['quality']['config'], state['signals'])
        self.assertTrue(qc['rows'][0]['complete'])
        self.assertNotIn('met-NAA', qc['rows'][0]['quality']['NAA']['snr'])
        Path(qc['rows'][0]['quality']['NAA']['snr']).unlink()
        self.save(self.visits[0], 'snr', np.ones((2, 2, 2)), resolution='1')
        qc = scan_quality(state['quality']['config'], state['signals'])
        self.assertFalse(qc['rows'][0]['complete'])

    def test_ordered_design_and_custom_contrasts(self):
        state = self.state(); model = build_model(self.frame, state['roles'])
        self.assertEqual(model['columns'], ['Intercept', 'Sex[M]', 'Age'])
        self.assertEqual(model['contrast_labels'], ['Age: slope > 0', 'Age: slope < 0'])
        rows, labels = parse_contrast_text('0 0 1\n0 0 -1', model)
        self.assertEqual(rows, [[0., 0., 1.], [0., 0., -1.]])
        self.assertEqual(labels, ['Age: slope > 0', 'Age: slope < 0'])
        with self.assertRaises(ValueError): parse_contrast_text('0 1', model)
        state['roles'][0]['Confound'] = True
        with self.assertRaises(ValueError): build_model(self.frame, state['roles'])

    def test_contrast_labels_explain_categorical_and_binary_numeric_directions(self):
        roles = [
            {'Include': True, 'Name': 'Age', 'Type': 'Numeric', 'Confound': True,
             'Regressor': False, 'Reference': ''},
            {'Include': True, 'Name': 'Sex', 'Type': 'Categorical', 'Confound': False,
             'Regressor': True, 'Reference': 'F'},
        ]
        categorical = build_model(self.frame, roles)
        self.assertEqual(categorical['contrast_labels'],
                         ['Sex: M > F', 'Sex: M < F'])

        numeric_frame = self.frame.copy()
        numeric_frame['Sex'] = [0, 1] * 4
        roles[1]['Type'] = 'Numeric'
        numeric = build_model(numeric_frame, roles)
        self.assertEqual(numeric['contrast_labels'],
                         ['Sex: 1 > 0', 'Sex: 1 < 0'])

    def test_stale_scans_and_changed_design_require_review(self):
        state = self.state(); state['data'] = {**state['data'], 'desc': 'other'}
        with self.assertRaisesRegex(ValueError, 'current selectors'): compile_plan(state, self.base / 'output')
        state = self.state(); state['roles'][1]['Include'] = False
        with self.assertRaisesRegex(ValueError, 'review and confirm'): compile_plan(state, self.base / 'output')
        state = self.state()
        state['signals']['rows'][0].update(complete=False, issues=['water: 0 matches'])
        with self.assertRaisesRegex(ValueError, 'water: 0 matches'):
            compile_plan(state, self.base / 'output')

    def test_population_threshold_uses_at_least_and_final_included_subjects(self):
        np.testing.assert_array_equal(population_mask([[1, 1], [1, 0]], 50), [1, 1])
        np.testing.assert_array_equal(population_mask([[1, 1], [1, 0]], 51), [1, 0])
        state = self.state(); state['excluded'] = [0]
        state['mask']['coverage'] = 85.
        plan = compile_plan(state, self.base / 'output')
        self.assertEqual(len(plan['rows']), 7)
        execute(plan, prepare_only=True)
        mask = nib.load(self.base / 'output/NAA/population_mask.nii.gz').get_fdata()
        self.assertEqual(mask[0, 0, 0], 1) # 6 / 7 pass >= 85%; 6 / 8 would fail.
        stack = nib.load(self.base / 'output/NAA/signals.nii.gz').get_fdata()
        self.assertEqual(stack.shape, (2, 2, 2, 7))
        np.testing.assert_allclose(stack[..., 0], nib.load(plan['rows'][0]['images']['NAA']).get_fdata())
        self.assertEqual((self.base / 'output/subjects.txt').read_text().splitlines(), plan['model']['subjects'])

    def test_precomputed_masks_are_per_metabolite_and_binary(self):
        state = self.state(); mapping = {}
        for i, met in enumerate(self.data['metabolites']):
            values = np.zeros((2, 2, 2)); values[i, :, :] = 1
            path = self.base / f'met-{met}_mask.nii.gz'
            nib.save(nib.Nifti1Image(values, np.eye(4)), path); mapping[met] = str(path)
        state['mask'] = {'mode': 'precomputed', 'precomputed': mapping, 'coverage': 68.}
        state['mask_check'] = _mask_identity(state)
        plan = compile_plan(state, self.base / 'output'); execute(plan, prepare_only=True)
        for met in self.data['metabolites']:
            np.testing.assert_array_equal(nib.load(self.base / f'output/{met}/population_mask.nii.gz').get_fdata(), nib.load(mapping[met]).get_fdata())

    def test_grid_mismatch_and_source_changes_fail(self):
        plan = self.plan()
        path = Path(plan['rows'][0]['images']['NAA']); path.touch()
        with self.assertRaisesRegex(ValueError, 'changed'): validate_plan(plan)
        plan = self.plan()
        quality = plan['rows'][0]['quality']['NAA']['crlb']
        nib.save(nib.Nifti1Image(np.ones((3, 3, 3)), np.eye(4)), quality)
        plan.pop('sources')
        with self.assertRaisesRegex(ValueError, 'grid'): execute(plan, prepare_only=True)

    def test_randomise_settings_and_failure_propagation(self):
        plan = self.plan(); command = randomise_command(plan, 'NAA')
        self.assertIn('-T', command); self.assertIn('-x', command); self.assertIn('--uncorrp', command)
        self.assertEqual(command[command.index('-n') + 1], '2')
        import subprocess
        with (patch('vlad.workflow.runner.shutil.which', return_value='/fake/randomise'),
              patch('vlad.workflow.runner._run_randomise',
                    side_effect=subprocess.CalledProcessError(9, command))):
            with self.assertRaises(subprocess.CalledProcessError): execute(plan)
        self.assertFalse((self.base / 'output/results.json').exists())

    def test_randomise_permutations_become_compact_progress_events(self):
        class Process:
            stdout = iter([*(f'Starting permutation {number}\n' for number in range(1, 6)),
                           *(f'Starting permutation {number}\n' for number in range(1, 6)),
                           'Critical Value for: result is: 4.2\n', 'Finished, exiting.\n'])

            @staticmethod
            def wait(): return 0

        stream = io.StringIO()
        with patch('vlad.workflow.runner.subprocess.Popen', return_value=Process()), redirect_stdout(stream):
            _run_randomise(['/fake/randomise'], environment={}, metabolite='NAA',
                           permutations=5, contrasts=2)
        output = stream.getvalue()
        self.assertNotIn('Starting permutation', output)
        self.assertIn('Critical Value', output)
        event = parse_progress(output)[0]
        self.assertEqual((event['metabolite'], event['stage'], event['current'], event['total']),
                         ('NAA', 'Complete', 10, 10))

        class Terminal(io.StringIO):
            @staticmethod
            def isatty(): return True

        terminal = Terminal()
        with redirect_stdout(terminal):
            _emit_progress('NAA', 'Randomise', 250, 500, 'contrast 1/1')
        self.assertNotIn('VLAD_PROGRESS', terminal.getvalue())
        self.assertIn('50%', terminal.getvalue())
        self.assertIn('NAA · Randomise', terminal.getvalue())

        plan = self.plan(); plan['randomise']['nproc'] = 0
        with self.assertRaisesRegex(ValueError, 'nproc'):
            validate_plan(plan)

    def test_nproc_runs_metabolites_concurrently_but_once_each(self):
        plan = self.plan()
        plan['randomise']['nproc'] = 2
        barrier = threading.Barrier(2)
        calls = []
        lock = threading.Lock()

        def fake_randomise(command, **kwargs):
            with lock:
                calls.append((kwargs['metabolite'], kwargs['contrasts']))
            barrier.wait(timeout=5)

        with (patch('vlad.workflow.runner.shutil.which', return_value='/fake/randomise'),
              patch('vlad.workflow.runner._run_randomise', side_effect=fake_randomise)):
            execute(plan)
        self.assertEqual(sorted(calls), [('CrPCr', 2), ('NAA', 2)])

    def test_rich_terminal_has_one_bar_per_metabolite_and_combines_contrasts(self):
        class Terminal(io.StringIO):
            @staticmethod
            def isatty(): return True

        terminal = Terminal()
        with redirect_stdout(terminal), _metabolite_progress(['NAA', 'CrPCr'], 500, 2):
            progress = workflow_runner._RICH_PROGRESS
            self.assertEqual(set(workflow_runner._RICH_TASKS), {'NAA', 'CrPCr'})
            self.assertEqual(len(progress.tasks), 2)
            self.assertTrue(all(task.total == 1000 for task in progress.tasks))

            _emit_progress('NAA', 'Randomise', 500, 1000, 'contrast 1/2')
            naa = progress.tasks[workflow_runner._RICH_TASKS['NAA']]
            self.assertEqual((naa.completed, naa.total, naa.fields['detail']),
                             (500, 1000, 'contrast 1/2'))

            _emit_progress('NAA', 'Randomise', 501, 1000, 'contrast 2/2')
            naa = progress.tasks[workflow_runner._RICH_TASKS['NAA']]
            self.assertEqual((naa.completed, naa.fields['detail']), (501, 'contrast 2/2'))

            _emit_progress('NAA', 'Complete', 1000, 1000, '2 contrasts completed')
            naa = progress.tasks[workflow_runner._RICH_TASKS['NAA']]
            self.assertEqual((naa.completed, naa.fields['detail']), (1000, 'Complete'))

        rendered = terminal.getvalue()
        self.assertIn('NAA', rendered)
        self.assertIn('CrPCr', rendered)
        self.assertNotIn('Starting permutation', rendered)

    def test_gauge_formula_and_zero_preservation(self):
        stack = np.asarray([
            [[[[-1., 0.], [1., 2.]]], [[[0., 0.], [2., 4.]]]],
            [[[[2., 3.], [4., 5.]]], [[[4., 5.], [6., 7.]]]],
        ])
        group_mean, met_std, z, gauge = compute_gauge(stack)
        expected_mean = np.nanmean(stack, axis=1)
        expected_std = np.nanstd(expected_mean, axis=(1, 2, 3))
        expected_z = ((expected_mean - np.nanmean(expected_mean, axis=(1, 2, 3))[:, None, None, None]) /
                      expected_std[:, None, None, None])
        np.testing.assert_allclose(group_mean, expected_mean)
        np.testing.assert_allclose(met_std, expected_std)
        np.testing.assert_allclose(z, expected_z)
        np.testing.assert_allclose(gauge, np.nanmean(expected_z, axis=0))
        corrected = correct_map_voxelwise(stack[0, 0], met_std[0], gauge)
        self.assertEqual(corrected[0, 0, 1], 0.)

    def test_gauge_correction_saves_raw_corrected_and_diagnostic_images(self):
        plan = self.plan()
        plan['randomise']['gauge_correction'] = True
        execute(plan, prepare_only=True)
        raw_stacks = []
        for metabolite in plan['data']['metabolites']:
            directory = self.base / 'output' / metabolite
            self.assertTrue((directory / 'signals_raw.nii.gz').is_file())
            self.assertTrue((directory / 'signals.nii.gz').is_file())
            self.assertTrue((directory / 'gauge_group_mean.nii.gz').is_file())
            self.assertTrue((directory / 'gauge_z.nii.gz').is_file())
            raw = nib.load(directory / 'signals_raw.nii.gz').get_fdata()
            raw_stacks.append(np.moveaxis(raw, -1, 0))
        group_mean, met_std, z, gauge = compute_gauge(np.stack(raw_stacks))
        np.testing.assert_allclose(nib.load(self.base / 'output/gauge_term.nii.gz').get_fdata(),
                                   gauge, rtol=1e-5, atol=1e-6)
        for index, metabolite in enumerate(plan['data']['metabolites']):
            directory = self.base / 'output' / metabolite
            np.testing.assert_allclose(nib.load(directory / 'gauge_group_mean.nii.gz').get_fdata(),
                                       group_mean[index], rtol=1e-5, atol=1e-6)
            np.testing.assert_allclose(nib.load(directory / 'gauge_z.nii.gz').get_fdata(),
                                       z[index], rtol=1e-5, atol=1e-6)
            raw = nib.load(directory / 'signals_raw.nii.gz').get_fdata()
            corrected = nib.load(directory / 'signals.nii.gz').get_fdata()
            expected = np.stack([correct_map_voxelwise(raw[..., subject], met_std[index], gauge)
                                 for subject in range(raw.shape[-1])], axis=-1)
            np.testing.assert_allclose(corrected, expected, rtol=1e-5, atol=1e-6)
        metadata = json.loads((self.base / 'output/gauge_metadata.json').read_text())
        self.assertEqual(metadata['metabolites'], plan['data']['metabolites'])

        one = self.plan()
        one['data']['metabolites'] = ['NAA']
        one['randomise']['gauge_correction'] = True
        with self.assertRaisesRegex(ValueError, 'at least two'):
            validate_plan(one)


if __name__ == '__main__': unittest.main()
