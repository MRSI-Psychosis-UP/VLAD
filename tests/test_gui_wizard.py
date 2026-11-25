"""Exercise the six-step default interface and final command generation."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import nibabel as nib
import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_workflow_guided import GuidedFixture
from vlad.gui.jobs import JobStore
from vlad.gui.wizard import STEPS, _command_model_summary, _exclusive_checkbox_roles

ROOT = Path(__file__).resolve().parents[1]


class GuidedInterfaceTests(GuidedFixture):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {'VLAD_GUI_STATE_DIR': str(self.base / 'jobs'), 'VLAD_HOME': str(ROOT)})
        env.start(); self.addCleanup(env.stop)
        st.cache_resource.clear(); self.addCleanup(st.cache_resource.clear)

    def app(self): return AppTest.from_file(str(ROOT / 'src/vlad/gui/app.py'), default_timeout=25).run()

    def healthy(self, app):
        self.assertFalse(app.exception, str(app.exception))
        self.assertFalse(app.error, str(app.error))

    def click(self, app, label):
        next(button for button in app.button if button.label == label).click().run()
        self.healthy(app)

    def page(self, app, index):
        app.radio(key='wf_step').set_value(STEPS[index]).run(); self.healthy(app)

    def load_data(self, app):
        app.text_input(key='wf_covars').set_value(str(self.table)).run()
        self.click(app, 'Load covariates')
        app.text_input(key='wf_images').set_value(str(self.inputs))
        app.text_input(key='wf_space').set_value('MNI')
        app.text_input(key='wf_resolution').set_value('3').run()
        with patch('nibabel.load', side_effect=AssertionError('Path discovery must not open images')):
            self.click(app, 'Load NIfTI paths')

    def test_model_checkbox_roles_are_mutually_exclusive(self):
        previous = [
            {'Include': True, 'Name': 'Age', 'Type': 'Numeric',
             'Confound': True, 'Regressor': False, 'Reference': ''},
            {'Include': True, 'Name': 'Sex', 'Type': 'Categorical',
             'Confound': False, 'Regressor': True, 'Reference': 'F'},
        ]
        edited = pd.DataFrame([{**row, 'Confound': True, 'Regressor': True}
                               for row in previous])
        roles, corrected = _exclusive_checkbox_roles(edited, previous)
        self.assertTrue(corrected)
        self.assertEqual((roles[0]['Confound'], roles[0]['Regressor']), (False, True))
        self.assertEqual((roles[1]['Confound'], roles[1]['Regressor']), (True, False))

    def test_command_model_summary_counts_regressor_groups(self):
        plan = {'model': {
            'roles': [
                {'Include': True, 'Name': 'Age', 'Type': 'Numeric',
                 'Confound': True, 'Regressor': False, 'Reference': ''},
                {'Include': True, 'Name': 'Sex', 'Type': 'Categorical',
                 'Confound': False, 'Regressor': True, 'Reference': 'F'},
            ],
            'covariates': self.frame.to_dict('records'),
        }}
        summary = _command_model_summary(plan)
        self.assertEqual(summary['samples'], 8)
        self.assertEqual([row['Role'] for row in summary['variables']],
                         ['Confound', 'Regressor'])
        self.assertEqual({row['Group / value']: row['Samples'] for row in summary['counts']},
                         {'F': 4, 'M': 4})

    def test_ratio_and_gauge_inputs_live_on_data_and_persist(self):
        app = self.app(); self.healthy(app)
        app.text_input(key='wf_metabolites').set_value(
            'Glu GABA z_Glu Glu/GABA Glu/SUMOFMETABOLITES').run()
        self.assertFalse(any(item.key == 'wf_gauge_correction' for item in app.checkbox))
        self.page(app, 4)
        self.assertFalse(any(item.key == 'wf_gauge_correction' for item in app.checkbox))
        self.page(app, 0)
        self.assertEqual(app.text_input(key='wf_metabolites').value,
                         'Glu GABA z_Glu Glu/GABA Glu/SUMOFMETABOLITES')
        self.assertFalse(any(item.key == 'wf_gauge_correction' for item in app.checkbox))

    def test_tissue_fields_show_cached_source_after_download(self):
        cached = {}

        def template_maps(space, *, download=False):
            self.assertEqual(space, 'MNI')
            if download:
                cached.update(gm='/cache/gm.nii.gz', wm='/cache/wm.nii.gz')
            return cached

        with patch('vlad.gui.wizard.tissue_probabilities', side_effect=template_maps):
            app = self.app()
            self.load_data(app)
            self.page(app, 1)
            self.assertFalse(any('✅ TemplateFlow cache' in item.value for item in app.caption))
            self.click(app, 'Download TemplateFlow GM and WM')
            self.assertEqual(sum(item.value == '✅ TemplateFlow cache' for item in app.caption), 2)
            self.assertEqual(app.text_input(key='wf_tissue_gm').value, '')
            self.assertEqual(app.text_input(key='wf_tissue_wm').value, '')
            app.text_input(key='wf_tissue_gm').set_value('/custom/gm.nii.gz').run()
            self.healthy(app)
            self.assertFalse(any('✅ TemplateFlow cache' in item.value for item in app.caption))

    def test_default_workflow_has_six_steps_and_preserves_inputs_across_pages(self):
        app = self.app(); self.healthy(app)
        self.assertEqual(app.radio(key='wf_step').options, STEPS)
        self.load_data(app)
        self.page(app, 1)
        self.assertFalse(app.latex)
        self.assertFalse(any('pc = percent' in item.value for item in app.caption))
        gm, wm = self.base / 'gm.nii.gz', self.base / 'wm.nii.gz'
        tissue = np.zeros((2, 2, 2), dtype=np.uint8)
        tissue[0] = 1
        nib.save(nib.Nifti1Image(tissue, np.eye(4)), gm)
        nib.save(nib.Nifti1Image(1 - tissue, np.eye(4)), wm)
        app.text_input(key='wf_tissue_gm').set_value(str(gm))
        app.text_input(key='wf_tissue_wm').set_value(str(wm)).run()
        self.click(app, 'Load quality paths')
        self.page(app, 2)
        self.assertTrue(any('full rank' in item.value for item in app.success))
        role_columns = list(app.data_editor[0].value.columns)
        self.assertIn('Include', role_columns)
        self.assertIn('Confound', role_columns)
        self.assertIn('Regressor', role_columns)
        self.assertIn('Z-score', role_columns)
        workflow = app.session_state['guided_workflow']
        editor_key = f'wf_roles_{workflow["table_id"][:12]}_{workflow.get("roles_editor_revision", 0)}'
        app.session_state[editor_key] = {
            'edited_rows': {0: {'Z-score': True}}, 'added_rows': [], 'deleted_rows': []}
        app.run()
        self.healthy(app)
        self.assertTrue(app.session_state['guided_workflow']['roles'][0]['Z-score'])
        self.page(app, 3)
        selector = next(item for item in app.multiselect if item.label == 'Contrasts to test')
        self.assertEqual(len(selector.options), 2)
        self.assertEqual(len(selector.value), 2)
        selector.set_value([selector.options[0]]).run()
        self.healthy(app)
        self.page(app, 4)
        app.number_input(key='wf_nperm').set_value(4).run()
        self.page(app, 0)
        self.assertEqual(app.text_input(key='wf_images').value, str(self.inputs))
        self.assertEqual(app.text_input(key='wf_space').value, 'MNI')
        self.assertFalse(app.file_uploader)
        self.page(app, 5)
        self.assertEqual(app.text_input(key='wf_output').value, str(self.table.parent))
        app.text_input(key='wf_output').set_value(str(self.base / 'study'))
        app.text_input(key='wf_run_name').set_value('TestRun').run()
        self.healthy(app)
        self.assertFalse(app.code)
        self.assertFalse((self.base / 'study').exists())
        self.click(app, 'Generate command')
        plans = list((self.base / 'study' / 'plans').glob('TestRun-*.json'))
        self.assertEqual(len(plans), 1)
        saved_plan = json.loads(plans[0].read_text())
        self.assertFalse(Path(saved_plan['output_dir']).exists()) # Preparing the command does not run analysis.
        self.assertTrue((self.base / 'study' / 'runs').is_dir())
        self.assertEqual(len(app.code), 1)
        self.assertIn('-m vlad workflow --plan', app.code[0].value)
        self.assertIn(str(plans[0]), app.code[0].value)
        recap = '\n'.join(frame.value.to_string() for frame in app.dataframe)
        self.assertIn('Regressor', recap)
        self.assertIn('8', recap)
        plan = json.loads(plans[0].read_text())
        self.assertEqual(plan['randomise']['permutations'], 4)
        self.assertEqual(plan['randomise']['nproc'], 4)
        self.assertFalse(plan['randomise']['gauge_correction'])
        self.assertEqual(plan['model']['subjects'], self.visits)
        self.assertEqual(plan['model']['coding']['Age']['transform'], 'z-score')
        self.assertEqual(plan['tissue_masks'], {'gm': str(gm), 'wm': str(wm)})
        self.assertTrue({str(gm), str(wm)} <= plan['sources'].keys())
        self.assertEqual(plan['mask']['metrics'][1]['threshold'], .1)
        self.assertEqual(plan['mask']['metrics'][1]['unit'], 'Hz')
        self.assertEqual(len(plan['rows']), 8)
        self.assertEqual(len(plan['model']['contrasts']), 1)
        self.assertFalse(Path(plan['output_dir']).exists()) # Only the detached worker creates analysis outputs.
        # Sessions created by an earlier version may contain this invalid button
        # snapshot and stale blank field snapshots. Returning to Data must
        # migrate them away and recover the last successfully scanned selectors.
        workflow = app.session_state['guided_workflow']
        workflow.setdefault('widgets', {})['wf_scan_metabolites'] = True
        for key in ('wf_covars', 'wf_images', 'wf_space', 'wf_metabolites', 'wf_desc', 'wf_resolution'):
            workflow['widgets'][key] = ''
        workflow['data'] = {'folder': '', 'space': '', 'metabolites': [], 'desc': '', 'resolution': ''}
        self.page(app, 0)
        self.assertEqual(app.text_input(key='wf_covars').value, str(self.table))
        self.assertEqual(app.text_input(key='wf_images').value, str(self.inputs))
        self.assertEqual(app.text_input(key='wf_space').value, 'MNI')
        self.assertEqual(app.text_input(key='wf_metabolites').value, 'NAA CrPCr')
        self.assertEqual(app.text_input(key='wf_desc').value, 'signal')
        self.assertEqual(app.text_input(key='wf_resolution').value, '3')
        self.assertNotIn('wf_scan_metabolites', app.session_state['guided_workflow']['widgets'])
        self.click(app, 'Load covariates')
        self.assertEqual(len(app.session_state['guided_workflow']['frame']), 8)
        self.page(app, 5)
        self.assertEqual(app.text_input(key='wf_output').value, str(self.base / 'study'))

    def test_global_theme_bids_and_local_data_folder_persist(self):
        app = self.app()
        storage = self.base / 'custom-gui-data'
        app.text_input(key='global_bids_dir').set_value(str(self.base)).run()
        app.selectbox(key='global_theme').set_value('Dark').run()
        app.text_input(key='global_settings_dir').set_value(str(storage)).run()
        self.click(app, 'Save global settings')
        pointer = json.loads((self.base / 'jobs/settings-location.json').read_text())
        self.assertEqual(pointer['settings_dir'], str(storage.resolve()))
        settings = json.loads((storage / 'settings.json').read_text())
        self.assertEqual(settings['bids_dir'], str(self.base.resolve()))
        self.assertEqual(settings['theme'], 'Dark')
        self.assertEqual(settings['settings_dir'], str(storage.resolve()))
        self.assertEqual(app.text_input(key='wf_images').value, str(self.base.resolve()))

        # A new browser/server session reads the persisted machine-local setting.
        st.cache_resource.clear()
        restarted = self.app()
        self.assertEqual(restarted.text_input(key='global_bids_dir').value, str(self.base.resolve()))
        self.assertEqual(restarted.selectbox(key='global_theme').value, 'Dark')
        self.assertEqual(restarted.text_input(key='global_settings_dir').value, str(storage.resolve()))
        self.assertEqual(restarted.text_input(key='wf_images').value, str(self.base.resolve()))
        self.assertTrue(any('#111827' in item.value for item in restarted.markdown))

    def test_filters_affect_model_and_mask_population_without_silent_drops(self):
        app = self.app(); self.load_data(app)
        app.selectbox(key='wf_filter_column').set_value('Age')
        app.selectbox(key='wf_filter_mode').set_value('Numeric range')
        app.text_input(key='wf_filter_text').set_value('25:50').run()
        self.click(app, '+ Add filter')
        state = app.session_state['guided_workflow']
        self.assertEqual(len(state['filters']), 1)
        self.page(app, 2)
        self.assertTrue(any('6 observations' in item.value for item in app.success))
        self.page(app, 0)
        app.selectbox(key='wf_filter_column').set_value('Sex')
        app.selectbox(key='wf_filter_mode').set_value('Include values')
        app.text_input(key='wf_filter_text').set_value('M').run()
        self.click(app, '+ Add filter')
        self.assertEqual(len(app.session_state['guided_workflow']['filters']), 2)
        self.page(app, 2)
        self.assertTrue(any('4 observations' in item.value for item in app.success))
        self.page(app, 0)
        self.click(app, 'Reset all filters')
        self.page(app, 2)
        self.assertTrue(any('8 observations' in item.value for item in app.success))

    def test_image_folder_scan_proposes_metabolite_labels(self):
        app = self.app(); self.healthy(app)
        scan = next(button for button in app.button if button.label == 'Scan folder')
        self.assertTrue(scan.disabled)
        app.text_input(key='wf_images').set_value(str(self.inputs))
        app.text_input(key='wf_space').set_value('MNI')
        app.text_input(key='wf_desc').set_value('signal')
        app.text_input(key='wf_resolution').set_value('3')
        app.text_input(key='wf_metabolites').set_value('NAA/CrPCr').run()
        scan = next(button for button in app.button if button.label == 'Scan folder')
        self.assertFalse(scan.disabled)
        with patch('nibabel.load', side_effect=AssertionError('Filename scan must not open images')):
            scan.click().run()
        self.healthy(app)
        self.assertEqual(app.text_input(key='wf_metabolites').value,
                         'CrPCr NAA NAA/CrPCr')
        self.assertTrue(any('Found 2 metabolite labels' in item.value for item in app.success))
        self.assertTrue(any('current space, descriptor and resolution' in item.value for item in app.success))

    def test_command_page_does_not_read_or_render_jobs_or_results(self):
        with (patch.object(JobStore, 'list', side_effect=AssertionError('Command page must not list jobs')) as list_jobs,
              patch.object(JobStore, 'get', side_effect=AssertionError('Command page must not read jobs')) as get_job,
              patch.object(JobStore, 'read_log', side_effect=AssertionError('Command page must not read logs')) as read_log):
            app = self.app()
            self.page(app, 5)
        list_jobs.assert_not_called(); get_job.assert_not_called(); read_log.assert_not_called()
        self.assertFalse(app.toggle)
        self.assertFalse(app.dataframe)
        self.assertFalse(any('FSL corrected maps' in item.value for item in app.caption))

    def test_failed_covariate_reload_keeps_the_loaded_table(self):
        app = self.app(); self.load_data(app)
        self.page(app, 5)
        self.page(app, 0)
        app.text_input(key='wf_covars').set_value('').run()
        next(button for button in app.button if button.label == 'Load covariates').click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any('Choose a covariates TSV' in item.value for item in app.error))
        self.assertEqual(len(app.session_state['guided_workflow']['frame']), 8)
        self.assertTrue(app.data_editor)

    def test_changed_selectors_block_later_run_until_paths_are_reloaded(self):
        app = self.app(); self.load_data(app)
        app.text_input(key='wf_space').set_value('OtherSpace').run()
        self.page(app, 1)
        self.assertTrue(any('load' in item.value.lower() for item in app.info))
        self.assertFalse(any(button.label == 'Load quality paths' for button in app.button))
