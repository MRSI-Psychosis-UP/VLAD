"""Derivative selection, quality masks and scientific row ordering."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import nibabel as nib
import numpy as np

from vlad.gui.cohorts import _shell_file, read_assignments
from vlad.preparation.bids import discover_inputs, prepare_inputs, preview_inputs, quality_mask

ROOT = Path(__file__).resolve().parents[1]


class BidsPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vlad-bids-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.study = self.folder / 'study'
        self.study.mkdir()
        self.source = self.folder / 'derivatives'
        self.source.mkdir()
        self.subjects = ['002_V2', '001_V1']
        self.subject_file = self.folder / 'subjects.txt'
        self.subject_file.write_text('\n'.join(self.subjects) + '\n')
        self.reference = self.folder / 'reference.nii.gz'
        self.image(self.reference, 1)
        self.config = self.study / 'config_global.sh'
        self.config.write_text(_shell_file({'COHORT':'Synthetic', 'GLOBAL_DIR':str(self.study),
                                          'LIST_ALL_SUBJECTS':str(self.subject_file), 'T1_MNI':str(self.reference)}))
        self.settings = {'GLOBAL_DIR':str(self.study), 'MRSI_INPUT_MODE':'bids', 'NII_DIR_MRSI':str(self.source),
                         'MRSI_SPACE_LABEL':'MNI152NLin2009cAsym', 'MRSI_RESOLUTION':'3',
                         'MRSI_SIGNAL_DESC':'signal', 'METAB':['NAA'], 'MRSI_MASK_LIST':'',
                         'MRSI_QMASK_MODE':'thresholds'}
        self.mode = self.study / 'config_mrsi.sh'
        self.save_settings()

    def image(self, path, value, affine=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = np.full((2, 2, 2), value, dtype=np.float32) if np.isscalar(value) else np.asarray(value, dtype=np.float32)
        nib.save(nib.Nifti1Image(data, np.eye(4) if affine is None else affine), path)
        return path

    def save_settings(self):
        self.mode.write_text(_shell_file({key:value for key,value in self.settings.items() if key != 'GLOBAL_DIR'}))

    def maps(self, subject, signal=2, resolution='3', space='MNI152NLin2009cAsym'):
        sub, ses = subject.split('_')
        folder = self.source / ('sub-' + sub) / ('ses-' + ses) / 'mrsiprep'
        base = f'sub-{sub}_ses-{ses}_space-{space}_res-{resolution}'
        output = {}
        for desc, value in [('signal', signal), ('crlb', 10), ('snr', 15), ('fwhm', .05)]:
            name = base + ('_met-NAA' if desc in {'signal','crlb'} else '') + f'_desc-{desc}_mrsi.nii.gz'
            output[desc] = self.image(folder / name, value)
        return output

    def populate(self):
        return [self.maps(subject, signal=index+1) for index,subject in enumerate(self.subjects)]

    def test_recursive_exact_space_resolution_and_subject_order(self):
        self.populate()
        self.maps('002_V2', signal=99, resolution='1')
        self.maps('002_V2', signal=99, resolution='3', space='OtherSpace')
        result = preview_inputs(self.config, 'MRSI')
        self.assertEqual(result['errors'] + result['missing'] + result['ambiguous'], [])
        self.assertEqual([row['subject'] for row in result['rows']], self.subjects)
        self.assertTrue(all('res-3_' in row['image'] and 'MNI152NLin2009cAsym' in row['image'] for row in result['rows']))

    def test_missing_and_duplicate_matches_are_reported_before_writing(self):
        maps = self.maps('002_V2')
        duplicate = self.source / 'duplicate' / maps['signal'].name
        self.image(duplicate, 3)
        result = preview_inputs(self.config, 'MRSI')
        self.assertIn('002_V2', result['ambiguous'][0])
        self.assertIn('001_V1', result['missing'][0])
        with self.assertRaisesRegex(ValueError, 'matches'):
            prepare_inputs(self.config, 'MRSI')
        self.assertFalse((self.study/'prepared').exists())

    def test_unspecified_resolution_rejects_multiple_resolutions(self):
        self.populate()
        self.maps('002_V2', resolution='1')
        self.settings['MRSI_RESOLUTION'] = ''
        result = discover_inputs(self.settings, 'MRSI', self.subjects)
        self.assertTrue(result['ambiguous'])

    def test_quality_requires_the_selected_resolution_and_grid(self):
        maps = self.populate()
        maps[0]['crlb'].unlink()
        self.maps('002_V2', resolution='1')
        result = preview_inputs(self.config, 'MRSI')
        self.assertTrue(any('crlb' in item for item in result['missing']))
        self.image(maps[0]['crlb'], 10, affine=np.diag([2,2,2,1]))
        result = preview_inputs(self.config, 'MRSI')
        self.assertTrue(any('grid/affine' in item for item in result['errors']))

    def test_strict_quality_thresholds_and_nonfinite_data(self):
        signal = np.ones(8)
        crlb = np.array([19,20,19,19,19,np.nan,19,19.])
        fwhm = np.array([.09,.09,.1,.09,.09,.09,np.inf,.09])
        snr = np.array([11,11,11,10,11,11,11,11.])
        signal[4] = np.nan
        expected = [1,0,0,0,0,0,0,1]
        np.testing.assert_array_equal(quality_mask(signal,crlb,fwhm,snr), expected)
        np.testing.assert_array_equal(quality_mask([1],[21],[.12],[8],crlb_max=25,fwhm_max=.2,snr_min=5), [1])

    def test_preparation_is_local_and_saves_binary_population_mask(self):
        maps = self.populate()
        crlb = np.full((2,2,2),10,dtype=np.float32)
        crlb[0,0,0] = 20
        self.image(maps[0]['crlb'], crlb)
        original = {path:path.read_bytes() for group in maps for path in group.values()}
        self.settings['MRSI_POPULATION_COVERAGE'] = '68'
        self.save_settings()
        result = prepare_inputs(self.config,'MRSI')
        self.assertEqual(result['subjects'],self.subjects)
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)
        prepared = self.study/'prepared/mrsi'
        mask = nib.load(prepared/'population_met-NAA_QMask.nii.gz').get_fdata()
        self.assertEqual(mask[0,0,0],0)
        self.assertEqual(mask[1,1,1],1)
        values,_ = read_assignments(self.mode)
        self.assertEqual(values['NII_DIR_MRSI'],str(prepared))
        self.assertEqual(values['MRSI_SOURCE_DIR'],str(self.source))
        self.assertEqual(values['MRSI_FILES_VALIDATED'],['signal'])
        prepare_inputs(self.config,'MRSI')  # An unchanged selection can be reused.
        self.image(maps[0]['signal'],7)
        with self.assertRaisesRegex(ValueError,'changed source'):
            prepare_inputs(self.config,'MRSI')

    def test_precomputed_population_mask_avoids_needing_quality_maps(self):
        groups = self.populate()
        for group in groups:
            for key in ['crlb','snr','fwhm']:
                group[key].unlink()
        population = self.folder/'provided-mask.nii.gz'
        data = np.ones((2,2,2),dtype=np.float32)
        data[0,0,0]=0
        self.image(population,data)
        self.settings.update(MRSI_QMASK_MODE='population',MRSI_POPULATION_MASK=str(population))
        self.save_settings()
        result = prepare_inputs(self.config,'MRSI')
        self.assertEqual(len(result['rows']),2)
        for path in (self.study/'prepared/mrsi').glob('*desc-QMask*'):
            np.testing.assert_array_equal(nib.load(path).get_fdata(),data)

    def test_population_mask_rejects_probabilities_and_wrong_affine(self):
        self.populate()
        population = self.folder/'provided-mask.nii.gz'
        self.settings.update(MRSI_QMASK_MODE='population',MRSI_POPULATION_MASK=str(population))
        self.image(population,.5)
        result = discover_inputs(self.settings,'MRSI',self.subjects)
        self.assertTrue(any('binary' in message for message in result['errors']))
        self.image(population,1,affine=np.diag([2,2,2,1]))
        result = discover_inputs(self.settings,'MRSI',self.subjects)
        self.assertTrue(any('grid/affine' in message for message in result['errors']))
        wrong_space=self.folder/'space-Other_res-3_mask.nii.gz'
        self.image(wrong_space,1)
        self.settings['MRSI_POPULATION_MASK']=str(wrong_space)
        result=discover_inputs(self.settings,'MRSI',self.subjects)
        self.assertTrue(any('different space' in message for message in result['errors']))

    def test_dwi_descriptor_suffix_and_filename_glob_select_scalars(self):
        self.settings.update(NII_DIR_DTI=str(self.source),DTI_SPACE_LABEL='MNI152NLin2009cAsym',DTI_RESOLUTION='native',FILES_DTI=['gFA'])
        for subject in self.subjects:
            sub,ses=subject.split('_')
            self.image(self.source/f'sub-{sub}/ses-{ses}/dwi/odfreconst/sub-{sub}_ses-{ses}_run-01_acq-dsiNdir129_space-MNI152NLin2009cAsym_model-SHORE_desc-dipygfa_res-native_GFA.nii.gz',1)
        result = discover_inputs(self.settings,'DTI',self.subjects)
        self.assertEqual(result['errors']+result['missing']+result['ambiguous'],[])
        self.settings['FILES_DTI']=['*desc-dipygfa*_GFA.nii.gz']
        result = discover_inputs(self.settings,'DWI',self.subjects)
        self.assertEqual(result['series'],['dipygfa'])
        self.settings['FILES_DTI']=['dipygfa']
        result = discover_inputs(self.settings,'DTI',self.subjects)
        self.assertEqual(result['series'],['dipygfa'])

    def test_native_space_cannot_be_used_for_group_stacking(self):
        self.settings.update(NII_DIR_DTI=str(self.source),DTI_SPACE_LABEL='dwi',FILES_DTI=['gFA'])
        result=discover_inputs(self.settings,'DTI',self.subjects)
        self.assertTrue(any('subject-specific' in message for message in result['errors']))

    def test_dwi_preparation_stages_scalar_maps_and_can_be_reused(self):
        source=self.source
        settings={'DTI_INPUT_MODE':'bids','NII_DIR_DTI':str(source),'DTI_SPACE_LABEL':'MNI152NLin2009cAsym',
                  'DTI_RESOLUTION':'3','FILES_DTI':['*desc-dipygfa*_GFA.nii.gz'],'DTI_MASK_LIST':''}
        mode=self.study/'config_dti.sh'
        mode.write_text(_shell_file(settings))
        for index,subject in enumerate(self.subjects):
            sub,ses=subject.split('_')
            self.image(source/f'sub-{sub}_ses-{ses}_run-01_space-MNI152NLin2009cAsym_desc-dipygfa_res-3_GFA.nii.gz',index+1)
        result=prepare_inputs(self.config,'DTI')
        self.assertEqual(result['series'],['dipygfa'])
        values,_=read_assignments(mode)
        self.assertEqual(values['DTI_FILES_VALIDATED'],['dipygfa'])
        self.assertEqual(values['DTI_SELECTORS'],settings['FILES_DTI'])
        self.assertTrue((self.study/'prepared/dti/sub-002_ses-V2_space-MNI152NLin2009cAsym_res-3_acq-dti_desc-dipygfa_dwi.nii.gz').is_file())
        prepare_inputs(self.config,'DTI')

    def test_conflicting_quality_acquisition_or_run_is_rejected(self):
        groups=self.populate()
        quality=groups[0]['crlb']
        quality.rename(quality.with_name(quality.name.replace('_met-NAA','_run-02_acq-other_met-NAA')))
        result=discover_inputs(self.settings,'MRSI',self.subjects)
        self.assertTrue(any('crlb' in message for message in result['missing']))

    def test_reused_preparation_restores_prepared_paths_after_settings_save(self):
        self.populate()
        prepare_inputs(self.config,'MRSI')
        self.save_settings()  # The UI wrote original source settings again.
        prepare_inputs(self.config,'MRSI')
        settings,_=read_assignments(self.mode)
        self.assertEqual(settings['NII_DIR_MRSI'],str(self.study/'prepared/mrsi'))
        self.assertEqual(settings['MRSI_FILES_VALIDATED'],['signal'])

    def test_nonfinite_signals_are_zeroed_and_fail_quality(self):
        groups=self.populate()
        values=np.ones((2,2,2),dtype=np.float32)
        values[0,0,0]=np.inf
        self.image(groups[0]['signal'],values)
        prepare_inputs(self.config,'MRSI')
        prepared=self.study/'prepared/mrsi'
        signal=nib.load(next(prepared.glob('sub-002*desc-signal*'))).get_fdata()
        mask=nib.load(next(prepared.glob('sub-002*desc-QMask*'))).get_fdata()
        self.assertEqual(signal[0,0,0],0)
        self.assertEqual(mask[0,0,0],0)

    def test_population_mask_rejects_nonfinite_signal_inside_valid_voxels(self):
        groups=self.populate()
        population=self.folder/'population.nii.gz'
        self.image(population,1)
        self.settings.update(MRSI_QMASK_MODE='population',MRSI_POPULATION_MASK=str(population))
        self.save_settings()
        values=np.ones((2,2,2),dtype=np.float32)
        values[0,0,0]=np.inf
        self.image(groups[0]['signal'],values)
        with self.assertRaisesRegex(ValueError,'inside the supplied population'):
            prepare_inputs(self.config,'MRSI')
        self.assertFalse((self.study/'prepared/mrsi').exists())

    def test_direct_covariate_tsv_subject_source_preserves_order_and_source(self):
        self.populate()
        self.subject_file.write_text('participant_id\tsession_id\tAge\n002\tV2\t21\n001\tV1\t23\n')
        original=self.subject_file.read_bytes()
        result=prepare_inputs(self.config,'MRSI')
        self.assertEqual(result['subjects'],self.subjects)
        self.assertEqual(self.subject_file.read_bytes(),original)

    def test_selected_population_previews_and_prepares_without_missing_cohort_visits(self):
        self.populate()
        self.subject_file.write_text('\n'.join([*self.subjects,'999_V9'])+'\n')
        before_global=self.config.read_bytes()
        before_source=self.subject_file.read_bytes()
        selected=self.folder/'selected.txt'
        selected.write_text('001_V1\n002_V2\n')
        self.assertTrue(preview_inputs(self.config,'MRSI')['missing'])
        preview=preview_inputs(self.config,'MRSI',subjects_file=selected)
        self.assertEqual(preview['errors']+preview['missing']+preview['ambiguous'],[])
        self.assertEqual(preview['subjects'],['001_V1','002_V2'])
        prepared=prepare_inputs(self.config,'MRSI',subjects_file=selected)
        self.assertEqual(prepared['subjects'],['001_V1','002_V2'])
        self.assertEqual([row['subject'] for row in prepared['rows']],['001_V1','002_V2'])
        self.assertEqual(self.config.read_bytes(),before_global)
        self.assertEqual(self.subject_file.read_bytes(),before_source)
        self.assertEqual(selected.read_text(),'001_V1\n002_V2\n')

    def test_headless_selected_population_is_forwarded_to_bids_preparation(self):
        self.populate()
        self.subject_file.write_text('\n'.join([*self.subjects,'999_V9'])+'\n')
        selected=self.folder/'selected.txt'
        selected.write_text('001_V1\n')
        env=os.environ.copy()
        env.update(VLAD_HOME=str(ROOT),VLAD_PYTHON_BIN=sys.executable,PYTHONPATH=str(ROOT/'src'))
        result=subprocess.run(['bash',str(ROOT/'workflows/Prep_VLAD_b6.1.sh'),'--config',str(self.config),
                               '--modality','MRSI','--non-interactive','--subjects',str(selected)],
                              env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        manifest=json.loads((self.study/'prepared/mrsi/manifest.json').read_text())
        self.assertEqual(manifest['subjects'],['001_V1'])
        self.assertEqual(len(manifest['rows']),1)

    def test_legacy_preparation_rejects_selected_population_flag(self):
        self.populate()
        self.settings.pop('MRSI_INPUT_MODE')
        self.save_settings()
        env=os.environ.copy()
        env.update(VLAD_HOME=str(ROOT),VLAD_PYTHON_BIN=sys.executable,PYTHONPATH=str(ROOT/'src'))
        result=subprocess.run(['bash',str(ROOT/'workflows/Prep_VLAD_b6.1.sh'),'--config',str(self.config),
                               '--modality','MRSI','--non-interactive','--subjects',str(self.subject_file)],
                              env=env,capture_output=True,text=True,timeout=15)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('only for BIDS',result.stderr)

    def test_analysis_masks_require_a_matching_space_declaration(self):
        self.populate()
        mask=self.folder/'tissue.nii.gz'
        self.image(mask,1)
        self.settings['MRSI_MASK_LIST']=str(mask)
        result=discover_inputs(self.settings,'MRSI',self.subjects)
        self.assertTrue(any('registered' in message for message in result['errors']))
        self.settings.update(MRSI_MASK_SPACE='MNI152NLin2009cAsym',MRSI_MASK_SPACE_CONFIRMED='yes')
        result=discover_inputs(self.settings,'MRSI',self.subjects)
        self.assertEqual(result['errors'],[])
        duplicate=self.folder/'another'/'tissue.nii.gz'
        self.image(duplicate,1)
        self.settings['MRSI_MASK_LIST']=str(mask)+'|'+str(duplicate)
        result=discover_inputs(self.settings,'MRSI',self.subjects)
        self.assertTrue(any('duplicate basenames' in message for message in result['errors']))

    def test_recomputed_masks_replace_only_the_current_managed_selection(self):
        self.populate()
        mask=self.folder/'HarvardOxford-white-grey-matter.nii.gz'
        self.image(mask,1)
        self.settings.update(MRSI_MASK_LIST=str(mask),MRSI_MASK_SPACE='MNI152NLin2009cAsym',MRSI_MASK_SPACE_CONFIRMED='yes')
        self.save_settings()
        prepare_inputs(self.config,'MRSI')
        values,_=read_assignments(self.mode)
        managed=Path(values['MRSI_MASK_DIR'])
        self.assertTrue((managed/mask.name).is_file())
        user_owned=self.study/'Masques/Synthetic/mrsi'/mask.name
        self.image(user_owned,1)
        self.settings['MRSI_MASK_LIST']=''
        self.save_settings()
        prepare_inputs(self.config,'MRSI',overwrite=True)
        self.assertFalse((managed/mask.name).exists())
        self.assertTrue(user_owned.is_file())

    def test_four_dimensional_dwi_is_rejected(self):
        self.settings.update(NII_DIR_DTI=str(self.source),DTI_SPACE_LABEL='dwi',FILES_DTI=['dwi'])
        self.image(self.source/'sub-002_ses-V2_space-dwi_desc-fullcorr_dwi.nii.gz',np.ones((2,2,2,4)))
        result=discover_inputs(self.settings,'DTI',['002_V2'])
        self.assertTrue(any('scalar 3D' in message for message in result['errors']))

    def test_headless_prep_routes_bids_without_fsl_or_native_dialogs(self):
        self.populate()
        env=os.environ.copy()
        env.update(VLAD_HOME=str(ROOT),VLAD_PYTHON_BIN=sys.executable,PYTHONPATH=str(ROOT/'src'))
        result=subprocess.run(['bash',str(ROOT/'workflows/Prep_VLAD_b6.1.sh'),'--config',str(self.config),
                               '--modality','MRSI','--non-interactive'],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue((self.study/'prepared/mrsi/manifest.json').exists())

    def test_workflow_stacks_preserve_order_refresh_and_propagate_failures(self):
        groups=self.populate()
        prepare_inputs(self.config,'MRSI')
        (self.study/'Listes').mkdir(exist_ok=True)
        (self.study/'Listes/List_Population.txt').write_text('\n'.join(self.subjects)+'\n')
        results=self.study/'Results/Population'
        results.mkdir(parents=True)
        (results/'Model.mat').write_text('/NumWaves 1\n/NumPoints 2\n/Matrix\n1\n1\n')
        (results/'Model.con').write_text('/NumWaves 1\n/NumContrasts 1\n/Matrix\n1\n')
        tools=self.folder/'fake-fsl/share/fsl/bin'
        tools.mkdir(parents=True)
        merger=tools/'fslmerge'
        merger.write_text('#!'+sys.executable+'\nimport sys,nibabel as nib,numpy as np\n'
                          'images=[nib.load(p) for p in sys.argv[3:]]\n'
                          'nib.save(nib.Nifti1Image(np.stack([i.get_fdata() for i in images],axis=-1),images[0].affine),sys.argv[2])\n')
        merger.chmod(0o755)
        val=tools/'fslval'
        val.write_text('#!'+sys.executable+'\nimport sys,nibabel as nib\nprint(nib.load(sys.argv[1]).shape[3])\n')
        val.chmod(0o755)
        registry=self.folder/'registry.txt'
        registry.write_text(str(self.config)+'\n')
        env=os.environ.copy()
        env.update(VLAD_HOME=str(ROOT),VLAD_PYTHON_BIN=sys.executable,FSLDIR=str(self.folder/'fake-fsl'),
                   VLAD_CONFIG_FILE=str(registry),PYTHONPATH=str(ROOT/'src'))
        env.pop('VLAD_FSL_INITIALIZED_FOR',None)
        result=subprocess.run(['bash',str(ROOT/'workflows/VLAD_beta7.sh'),'-c','Synthetic','-t','MRSI',
                               '-n','Population','-m','Model','--prepare','--non-interactive'],
                              env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        image=next((self.study/'Cartes4D/Population').glob('*.nii.gz'))
        np.testing.assert_array_equal(nib.load(image).get_fdata()[0,0,0,:],[1,2])
        # Changed preparation invalidates a cached 4D stack, even at the same path.
        self.image(groups[0]['signal'],7)
        prepare_inputs(self.config,'MRSI',overwrite=True)
        rerun=subprocess.run(['bash',str(ROOT/'workflows/VLAD_beta7.sh'),'-c','Synthetic','-t','MRSI',
                              '-n','Population','-m','Model','--prepare','--non-interactive'],
                             env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(rerun.returncode,0,rerun.stdout+rerun.stderr)
        np.testing.assert_array_equal(nib.load(image).get_fdata()[0,0,0,:],[7,2])
        # Different quality maps must follow that same order, and an analysis
        # engine failure must mark the browser job failed rather than succeed.
        quality=np.full((2,2,2),10,dtype=np.float32)
        quality[0,0,0]=20
        self.image(groups[0]['crlb'],quality)
        prepare_inputs(self.config,'MRSI',overwrite=True)
        maths=tools/'fslmaths'
        maths.write_text('#!'+sys.executable+'\nimport sys,nibabel as nib,numpy as np\n'
                         'image=nib.load(sys.argv[1]);data=image.get_fdata();ops=sys.argv[2:-1]\n'
                         'if "-Tmean" in ops:data=data.mean(axis=3)\n'
                         'if "-thr" in ops:data=np.where(data>=float(ops[ops.index("-thr")+1]),data,0)\n'
                         'if "-bin" in ops:data=(data!=0).astype(float)\n'
                         'if "-mas" in ops:\n'
                         ' mask=nib.load(ops[ops.index("-mas")+1]).get_fdata();data=data*mask[...,None]\n'
                         'out=sys.argv[-1];out=out if out.endswith((".nii",".nii.gz")) else out+".nii.gz"\n'
                         'nib.save(nib.Nifti1Image(data,image.affine),out)\n')
        maths.chmod(0o755)
        nvols=tools/'fslnvols'
        nvols.write_text('#!'+sys.executable+'\nimport sys,nibabel as nib\nprint(nib.load(sys.argv[1]).shape[3])\n')
        nvols.chmod(0o755)
        engine=tools/'randomise'
        engine.write_text('#!/bin/sh\nexit 23\n')
        engine.chmod(0o755)
        failed=subprocess.run(['bash',str(ROOT/'workflows/VLAD_beta7.sh'),'-c','Synthetic','-t','MRSI',
                               '-n','Population','-m','Model','--mask','population','--noparallel','--non-interactive'],
                              env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(failed.returncode,23,failed.stdout+failed.stderr)
        qmask=next((self.study/'Cartes4D/Population').glob('*desc-QMask*'))
        np.testing.assert_array_equal(nib.load(qmask).get_fdata()[0,0,0,:],[0,1])


if __name__=='__main__':
    unittest.main()
