"""Input formats and cached template selection must preserve study identity."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import nibabel as nib
import numpy as np

from vlad.gui.cohorts import save_cohort
from vlad.gui.commands import AnalysisOptions, analysis_command, preparation_command, validate_analysis
from vlad.gui.subjects import read_subject_visits
from vlad.gui import templates


class SubjectTableTests(unittest.TestCase):
    def test_bids_aliases_prefixes_and_leading_zeros_preserve_table_order(self):
        table = b"participant_id\tsession_id\tAge\nsub-002\tses-01\t30\n001\tV7\t25\n"
        self.assertEqual(read_subject_visits(table, "covars.tsv"), ["002_01", "001_V7"])
        self.assertEqual(read_subject_visits(b"subject_id,session_id\n001,02\n", "covars.csv"), ["001_02"])
        self.assertEqual(read_subject_visits(b"# subjects\n001_V1\n002_V2\n", "subjects.txt"), ["001_V1", "002_V2"])

    def test_invalid_missing_and_duplicate_identifiers_are_rejected(self):
        for table in (b"participant_id\tAge\n001\t20\n", b"subject_id\tsession_id\n001\t\n",
                      b"subject_id\tsession_id\n001\tV1\nsub-001\tses-V1\n",
                      b"subject_id\tsession_id\n$(touch bad)\tV1\n"):
            with self.subTest(table=table), self.assertRaises(ValueError):
                read_subject_visits(table, "covars.tsv")

    def test_cohort_keeps_covariates_and_defaults_exports_under_study(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "T1w.nii.gz"
            nib.save(nib.Nifti1Image(np.ones((2, 2, 2)), np.eye(4)), reference)
            covars = root / "covars.tsv"
            original = "subject_id\tsession_id\tAge\nsub-002\tses-01\t25\n001\tV1\t30\n"
            covars.write_text(original)
            inputs = root / "derivatives"
            inputs.mkdir()
            study = root / "study"
            cohort = save_cohort(root / "registry.txt", "Test", study, reference, covars,
                                 input_dir=inputs, input_mode="bids", space="MNI152NLin2009cAsym",
                                 resolution="3", metabolites=["NAA"], masks=[])
            self.assertEqual(covars.read_text(), original)
            self.assertEqual(Path(cohort.values["LIST_ALL_SUBJECTS"]).read_text(), "002_01\n001_V1\n")
            self.assertEqual(cohort.values["COVARIATES_FILE"], str(covars))
            self.assertEqual(cohort.values["DIR_RANALYSES"], str(study / "reports"))
            self.assertEqual(cohort.values["MRSI_SNR_MIN"], "10.0")
            self.assertEqual(cohort.values["MRSI_RESOLUTION"], "3")

    def test_settings_changes_require_preparation_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "T1w.nii.gz"
            reference.write_bytes(b"reference")
            subjects = root / "subjects.txt"
            subjects.write_text("001_V1\n")
            study = root / "study"
            kwargs = dict(registry=root / "registry.txt", name="Test", global_dir=study,
                          reference_image=reference, subjects_file=subjects, input_dir=root)
            save_cohort(**kwargs)
            with (study / "config_mrsi.sh").open("a") as handle:
                handle.write("MRSI_FILES_VALIDATED=(signal)\n")
            cohort = save_cohort(**kwargs, overwrite=True, snr_min=15.)
            self.assertNotIn("MRSI_FILES_VALIDATED", cohort.values)

    def test_anatomy_or_covariate_population_change_invalidates_all_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "T1w.nii.gz"
            reference.write_bytes(b"reference")
            table = root / "covars.tsv"
            table.write_text("subject_id\tsession_id\n001\t01\n002\t01\n")
            study = root / "study"
            kwargs = dict(registry=root / "registry.txt", name="Test", global_dir=study,
                          reference_image=reference, subjects_file=table, input_dir=root)
            save_cohort(**kwargs, reference_metadata={"template":"Original"})
            (study / "config_dti.sh").write_text("CUSTOM='keep me'\nDTI_FILES_VALIDATED=(gFA)\n")
            with (study / "config_mrsi.sh").open("a") as handle:
                handle.write("MRSI_FILES_VALIDATED=(signal)\n")
            table.write_text("subject_id\tsession_id\n001\t01\n003\t01\n")
            updated = save_cohort(**kwargs, overwrite=True)
            self.assertNotIn("MRSI_FILES_VALIDATED", updated.values)
            self.assertFalse(updated.values.get("DTI_FILES_VALIDATED"))
            self.assertEqual(updated.values["CUSTOM"], "keep me")
            self.assertNotIn("REFERENCE_TEMPLATE_METADATA", updated.values)
            with (study / "config_mrsi.sh").open("a") as handle:
                handle.write("MRSI_FILES_VALIDATED=(signal)\n")
            reference.write_bytes(b"new reference")
            updated = save_cohort(**kwargs, overwrite=True)
            self.assertNotIn("MRSI_FILES_VALIDATED", updated.values)

    def test_bids_setup_requires_matching_template_and_confirmed_tissue_space(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "T1w.nii.gz"
            reference.write_bytes(b"reference")
            subjects = root / "subjects.txt"
            subjects.write_text("001_V1\n")
            kwargs = dict(registry=root / "registry.txt", name="Test", global_dir=root / "study",
                          reference_image=reference, subjects_file=subjects, input_dir=root,
                          input_mode="bids", space="MNI152NLin2009cAsym")
            with self.assertRaisesRegex(ValueError, "TemplateFlow reference"):
                save_cohort(**kwargs, reference_metadata={"template":"MNI152NLin6Asym"})
            with self.assertRaisesRegex(ValueError, "already registered"):
                save_cohort(**kwargs, masks=[str(root / "tissue.nii.gz")])
            self.assertFalse((root / "study" / "config_global.sh").exists())

    def test_run_readiness_checks_prepared_visits_and_supports_population_only_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "T1w.nii.gz"
            reference.write_bytes(b"reference")
            subjects = root / "subjects.txt"
            subjects.write_text("001_V1\n")
            cohort = save_cohort(root / "registry.txt", "Test", root / "study", reference, subjects,
                                 input_dir=root, input_mode="bids", metabolites=["NAA"], masks=[])
            cohort.values["MRSI_FILES_VALIDATED"] = ["signal"]
            prepared = cohort.global_dir / "prepared/mrsi"
            prepared.mkdir(parents=True)
            manifest = prepared / "manifest.json"
            manifest.write_text(json.dumps({"subjects":["001_V1"],"series":["NAA"]}))
            population = cohort.global_dir / "Listes/List_Population.txt"
            population.write_text("001_V1\n")
            results = cohort.global_dir / "Results/Population"
            results.mkdir()
            (results / "Model.mat").write_text("matrix")
            (results / "Model.con").write_text("contrast")
            options = AnalysisOptions(cohort, "Population", "Model", mask="population")
            self.assertEqual(validate_analysis(options), [])
            self.assertIn("population", analysis_command(root, options))
            prepare_argv = preparation_command(root, cohort, subjects_file=population)
            self.assertEqual(prepare_argv[prepare_argv.index("--subjects") + 1], str(population))
            options.mask = "default"
            self.assertTrue(any("Prepare tissue masks" in message for message in validate_analysis(options)))
            options.mask = "population"
            population.write_text("002_V1\n")
            self.assertTrue(any("without prepared images" in message for message in validate_analysis(options)))


class TemplateSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.first = "tpl-Test/tpl-Test_res-01_T1w.nii.gz"
        self.second = "tpl-Test/tpl-Test_res-02_T1w.nii.gz"
        self.patch = patch.object(templates, "_bundled_catalogue", return_value=((self.first, self.second), {}))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.roots = patch.object(templates, "_cache_roots", return_value=[self.root])
        self.roots.start()
        self.addCleanup(self.roots.stop)

    def test_catalogue_and_options_never_start_a_download(self):
        with patch.object(templates.subprocess, "run", side_effect=AssertionError("Unexpected network client")):
            self.assertEqual(templates.list_templates(), ["Test"])
            self.assertEqual(templates.template_options("Test")["resolutions"], ["01", "02"])

    def test_surface_density_variants_do_not_make_volume_selection_ambiguous(self):
        extra = "tpl-Test/tpl-Test_res-01_den-41k_T1w.nii.gz"
        with patch.object(templates, "_bundled_catalogue", return_value=((self.first, extra), {})):
            self.assertEqual(templates.template_options("Test")["files"], [self.first])

    def test_cached_reference_is_loaded_with_normalized_resolution(self):
        path = self.root / self.first
        path.parent.mkdir()
        nib.save(nib.Nifti1Image(np.ones((2, 3, 4)), np.eye(4)), path)
        with patch.object(templates.subprocess, "run", side_effect=AssertionError("Unexpected download")):
            self.assertEqual(templates.fetch_template("Test", resolution=1), path)

    def test_ambiguous_selection_and_invalid_nifti_fail(self):
        with self.assertRaisesRegex(ValueError, "2 anatomical files"):
            templates.fetch_template("Test")
        path = self.root / self.first
        path.parent.mkdir()
        path.write_bytes(b"not nifti")
        with self.assertRaisesRegex(ValueError, "not a readable NIfTI"):
            templates.fetch_template("Test", resolution="01")


if __name__ == "__main__":
    unittest.main()
