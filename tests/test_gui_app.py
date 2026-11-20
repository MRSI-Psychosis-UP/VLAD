"""Exercise the browser workflow with Streamlit's actual app test runner."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import nibabel as nib
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit.testing.v1 import AppTest

from vlad.gui.cohorts import load_cohort, save_cohort
from vlad.gui.jobs import JobStore


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/vlad/gui/app.py"


class BrowserWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad-gui-app-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.registry = self.folder / "registry.txt"
        self.study = self.folder / "study"
        self.inputs = self.folder / "processed"
        self.inputs.mkdir()
        self.exports = self.folder / "exports"
        self.reference = self.folder / "T1.nii.gz"
        nib.save(nib.Nifti1Image(np.ones((3, 3, 3), dtype=np.float32), np.eye(4)), self.reference)
        self.subjects = self.folder / "subjects.txt"
        self.subjects.write_text("".join(f"{index:03d}_V1\n" for index in range(1, 13)))
        self.table = self.folder / "participants.csv"
        pd.DataFrame({"subject_visit": [f"{index:03d}_V1" for index in range(1, 13)],
                      "Score": np.linspace(1.1, 5.3, 12)}).to_csv(self.table, index=False)
        environment = {"VLAD_HOME": str(ROOT), "VLAD_CONFIG_FILE": str(self.registry),
                       "VLAD_GUI_STATE_DIR": str(self.folder / "state"), "MPLBACKEND": "Agg"}
        self.env = patch.dict(os.environ, environment)
        self.env.start()
        self.addCleanup(self.env.stop)
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)

    def app(self):
        app = AppTest.from_file(str(APP), default_timeout=20)
        app.session_state["interface_mode"] = "Existing studies"
        return app.run()

    def assert_healthy(self, app):
        self.assertFalse(app.exception, str(app.exception))

    def page(self, app, name):
        app.radio(key="navigation").set_value(name).run()
        self.assert_healthy(app)

    @staticmethod
    def input(app, label):
        return next(widget for widget in app.text_input if widget.label == label)

    @staticmethod
    def button(app, label):
        return next(widget for widget in app.button if widget.label == label)

    def register(self, prepared=True):
        cohort = save_cohort(self.registry, "Smoke", self.study, self.reference, self.subjects,
                             self.exports, input_dir=self.inputs)
        if prepared:
            with (self.study / "config_mrsi.sh").open("a") as handle:
                handle.write("MRSI_FILES_VALIDATED=(signal_filtbiharmonic_pvcorr)\n")
        return cohort

    def design_from_browser(self, app):
        self.page(app, "Design")
        self.input(app, "Study table path").set_value(str(self.table)).run()
        self.button(app, "Preview design").click().run()
        self.assert_healthy(app)
        self.assertFalse(app.error, str(app.error))
        self.button(app, "Save design").click().run()
        self.assert_healthy(app)
        self.assertFalse(app.error, str(app.error))

    def test_empty_workspace_has_working_onboarding_on_every_page(self):
        app = self.app()
        self.assert_healthy(app)
        self.assertTrue(any("Add your first study" in item.value for item in app.info))
        for name in ("Study setup", "Design", "Run analysis", "Reports", "Overview"):
            with self.subTest(page=name):
                self.page(app, name)
                self.assertFalse(app.file_uploader)
        self.assertFalse(self.registry.exists())

    def test_create_cohort_settings_from_the_browser(self):
        app = self.app()
        self.page(app, "Study setup")
        next(widget for widget in app.radio if widget.label == "Reference source").set_value("Custom NIfTI file").run()
        values = {"Cohort name": "Smoke", "Study folder": str(self.study),
                  "Processed image folder": str(self.inputs), "Reference anatomical image": str(self.reference),
                  "All subject visits · covariates or text file": str(self.subjects), "Report export folder": str(self.exports)}
        for label, value in values.items():
            self.input(app, label).set_value(value)
        masks = next(widget for widget in app.multiselect if widget.label == "Standard masks")
        masks.set_value([str(sorted((ROOT / "Masks").glob("*.nii.gz"))[0])])
        app.run()
        self.button(app, "Save study settings").click().run()
        self.assert_healthy(app)
        self.assertFalse(self.registry.exists())
        self.assertTrue(any("Confirm" in item.value for item in app.error))
        next(widget for widget in app.checkbox if widget.label == "I confirm the selected tissue masks use the image space").set_value(True)
        app.run()
        self.button(app, "Save study settings").click().run()
        self.assert_healthy(app)
        self.assertFalse(app.error, str(app.error))
        self.assertTrue((self.study / "config_global.sh").is_file())
        self.assertTrue((self.study / "config_mrsi.sh").is_file())
        self.assertIn(str(self.study / "config_global.sh"), self.registry.read_text())
        self.assertTrue(any(widget.label == "Prepare cohort data" for widget in app.button))

    def test_export_folder_tracks_study_folder_until_customised(self):
        app = self.app()
        self.page(app, "Study setup")
        self.input(app, "Study folder").set_value(str(self.study)).run()
        self.assertEqual(self.input(app, "Report export folder").value, str(self.study / "reports"))
        self.input(app, "Study folder").set_value(str(self.folder / "other-study")).run()
        self.assertEqual(self.input(app, "Report export folder").value, str(self.folder / "other-study/reports"))
        self.input(app, "Report export folder").set_value(str(self.exports)).run()
        self.input(app, "Study folder").set_value(str(self.study)).run()
        self.assertEqual(self.input(app, "Report export folder").value, str(self.exports))
        self.assert_healthy(app)

    def test_paths_compose_a_command_without_uploading_or_writing_inputs(self):
        with patch("subprocess.run", side_effect=AssertionError("Composition must not launch a process")):
            app = self.app()
            self.page(app, "Study setup")
            next(widget for widget in app.radio if widget.label == "Reference source").set_value("Custom NIfTI file").run()
            for label, value in {"Cohort name": "CommandStudy", "Study folder": str(self.study),
                                 "Processed image folder": str(self.inputs), "Reference anatomical image": str(self.reference),
                                 "All subject visits · covariates or text file": str(self.subjects)}.items():
                self.input(app, label).set_value(value)
            app.run()
        self.assert_healthy(app)
        self.assertFalse(app.file_uploader)
        self.assertFalse(self.registry.exists())
        self.assertFalse(self.study.exists())
        command = next(item.value for item in app.code if " configure " in item.value)
        tokens = shlex.split(command)
        self.assertEqual(tokens[tokens.index("--reference-image") + 1], str(self.reference))
        self.assertEqual(tokens[tokens.index("--subjects-file") + 1], str(self.subjects))
        self.assertNotIn("/uploads/", command)
        with patch("subprocess.run", wraps=subprocess.run) as run:
            self.button(app, "Save study settings").click().run()
        self.assert_healthy(app)
        self.assertFalse(app.error, str(app.error))
        self.assertEqual(run.call_args.args[0], tokens[3:])
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        cohort = load_cohort(self.study / "config_global.sh")
        self.assertEqual(cohort.values["T1_MNI"], str(self.reference))
        self.assertFalse((self.folder / "state/uploads").exists())

    def test_browse_returns_original_file_path_without_upload_or_execution(self):
        app = self.app()
        self.page(app, "Study setup")
        with patch("vlad.gui.file_picker.choose_native_paths", return_value=[str(self.subjects)]) as chooser:
            app.button(key="browse:setup_subjects_path").click().run()
        self.assert_healthy(app)
        self.assertEqual(app.text_input(key="setup_subjects_path").value, str(self.subjects))
        self.assertEqual(chooser.call_args.kwargs["kind"], "file")
        self.assertIn(".tsv", chooser.call_args.kwargs["extensions"])
        self.assertFalse(app.file_uploader)
        self.assertFalse(self.registry.exists())
        self.assertFalse((self.folder / "state/uploads").exists())

    def test_browse_folder_updates_export_default_and_cancel_preserves_path(self):
        app = self.app()
        self.page(app, "Study setup")
        with patch("vlad.gui.file_picker.choose_native_paths", return_value=[str(self.study)]) as chooser:
            app.button(key="browse:setup_study_dir").click().run()
        self.assert_healthy(app)
        self.assertEqual(chooser.call_args.kwargs["kind"], "directory")
        self.assertTrue(chooser.call_args.kwargs["allow_new"])
        self.assertEqual(app.text_input(key="setup_exports").value, str(self.study / "reports"))
        with patch("vlad.gui.file_picker.choose_native_paths", return_value=[]):
            app.button(key="browse:setup_study_dir").click().run()
        self.assertEqual(app.text_input(key="setup_study_dir").value, str(self.study))
        self.assertFalse(self.study.exists())

    def test_browse_masks_appends_once_and_desktop_failure_preserves_input(self):
        app = self.app()
        self.page(app, "Study setup")
        with patch("vlad.gui.file_picker.choose_native_paths", return_value=[str(self.reference)]):
            app.button(key="browse:setup_extra_masks").click().run()
            app.button(key="browse:setup_extra_masks").click().run()
        self.assertEqual(app.text_area(key="setup_extra_masks").value, str(self.reference))
        with patch("vlad.gui.file_picker.choose_native_paths", side_effect=RuntimeError("No display")):
            app.button(key="browse:setup_extra_masks").click().run()
        self.assert_healthy(app)
        self.assertEqual(app.text_area(key="setup_extra_masks").value, str(self.reference))
        self.assertTrue(any("desktop session" in item.value for item in app.warning))
        self.assertTrue(app.button(key="browse:setup_population_mask").disabled)

    def test_preparation_preview_updates_and_runs_only_on_click(self):
        self.register()
        app = self.app()
        with patch.object(JobStore, "start", return_value={"id": "prep-test", "status": "running"}) as launch:
            self.page(app, "Study setup")
            next(widget for widget in app.checkbox if widget.label == "Recompute existing prepared files").set_value(True).run()
            command = next(item.value for item in app.code if " prep " in item.value)
            self.assertIn("--overwrite", shlex.split(command))
            launch.assert_not_called()
            self.button(app, "Prepare cohort data").click().run()
        self.assert_healthy(app)
        self.assertEqual(launch.call_args.args[0], shlex.split(command)[3:])

    def test_templateflow_fetch_requires_explicit_action_and_selection_changes_invalidate_it(self):
        with (patch("vlad.gui.templates.list_templates", return_value=["MNI152NLin2009cAsym"]),
              patch("vlad.gui.templates.template_options", return_value={"resolutions": ["1", "2"], "cohorts": [], "suffixes": ["T1w"]}),
              patch("vlad.gui.templates.fetch_template", return_value=self.reference) as fetch):
            app = self.app()
            self.page(app, "Study setup")
            fetch.assert_not_called()
            self.button(app, "Load template").click().run()
            self.assert_healthy(app)
            fetch.assert_called_once_with("MNI152NLin2009cAsym", resolution="1", cohort=None, suffix="T1w")
            self.assertTrue(any("Reference ready" in item.value for item in app.success))
            next(widget for widget in app.selectbox if widget.label == "Template resolution").set_value("2").run()
            self.assert_healthy(app)
            self.assertFalse(any("Reference ready" in item.value for item in app.success))
            self.assertEqual(fetch.call_count, 1)

    def test_covars_subject_table_is_normalised_when_saving_settings(self):
        covars = self.folder / "covars.tsv"
        covars.write_text("subject_id\tsession_id\tAge\nsub-001\tses-01\t21\nsub-002\tses-02\t24\n")
        app = self.app()
        self.page(app, "Study setup")
        next(widget for widget in app.radio if widget.label == "Reference source").set_value("Custom NIfTI file").run()
        for label, value in {"Cohort name": "Covars", "Study folder": str(self.study),
                             "Processed image folder": str(self.inputs), "Reference anatomical image": str(self.reference),
                             "All subject visits · covariates or text file": str(covars)}.items():
            self.input(app, label).set_value(value)
        next(widget for widget in app.checkbox if widget.label == "I confirm the selected tissue masks use the image space").set_value(True)
        app.run()
        self.button(app, "Save study settings").click().run()
        self.assert_healthy(app)
        self.assertFalse(app.error, str(app.error))
        self.assertEqual((self.study / "Listes/AllSubjects.txt").read_text(), "001_01\n002_02\n")
        settings = (self.study / "config_global.sh").read_text()
        self.assertIn(str(covars), settings)
        self.assertIn(str(self.study / "reports"), settings)

    def test_covars_identifiers_filters_and_imported_contrast_reach_saved_design(self):
        self.register()
        covars = self.folder / "covars.tsv"
        pd.DataFrame({"participant_id": [f"sub-{index:03d}" for index in range(1, 13)],
                      "session_id": ["ses-V1"] * 12, "Score": np.linspace(1.1, 5.3, 12),
                      "Inclusion": ["0", "1"] + ["1"] * 10}).to_csv(covars, sep="\t", index=False)
        contrast = self.folder / "contrasts.tsv"
        contrast.write_text("name\tScore\tIntercept\nPositive association\t1\t0\n")
        app = self.app()
        self.page(app, "Design")
        self.input(app, "Study table path").set_value(str(covars)).run()
        self.assertEqual(next(widget for widget in app.selectbox if widget.label == "Subject or subject-visit column").value, "participant_id")
        self.assertEqual(next(widget for widget in app.selectbox if widget.label == "Session / visit column").value, "session_id")
        next(widget for widget in app.multiselect if widget.label == "Filter covariate columns").set_value(["Inclusion"]).run()
        next(widget for widget in app.multiselect if widget.label == "Values for Inclusion").set_value(["1"]).run()
        next(widget for widget in app.text_area if widget.label == "Exclude these subjects / visits").set_value("sub-002").run()
        next(widget for widget in app.radio if widget.label == "Contrast definition").set_value("Load a contrast file").run()
        self.input(app, "Contrast file path").set_value(str(contrast)).run()
        self.button(app, "Save design").click().run()
        self.assert_healthy(app)
        self.assertFalse(app.error, str(app.error))
        saved = json.loads((self.study / "Results/AllSubjects/MainEffect_browser-design.json").read_text())
        self.assertEqual(saved["subject_order"], [f"{index:03d}_V1" for index in range(3, 13)])
        self.assertEqual(saved["contrast_labels"], ["Positive association"])
        self.assertEqual(saved["columns"], ["Intercept", "Score"])
        audit = pd.read_csv(self.study / "Results/AllSubjects/MainEffect_selection.tsv", sep="\t")
        self.assertEqual(int(audit["included"].sum()), 10)

    def test_precomputed_quality_mask_settings_can_be_selected(self):
        app = self.app()
        self.page(app, "Study setup")
        next(widget for widget in app.radio if widget.label == "Reference source").set_value("Custom NIfTI file").run()
        next(widget for widget in app.radio if widget.label == "MRSI quality mask").set_value("Use a precomputed population mask").run()
        self.assertFalse(self.input(app, "Population binary mask").disabled)
        for label in ("CRLB must be below", "FWHM must be below", "SNR must be above"):
            self.assertTrue(next(widget for widget in app.number_input if widget.label == label).disabled)
        self.assert_healthy(app)

    def test_design_preview_save_and_run_review_without_desktop_dialogs(self):
        self.register()
        app = self.app()
        self.design_from_browser(app)
        self.assertTrue((self.study / "Results/AllSubjects/MainEffect.mat").is_file())
        self.assertEqual((self.study / "Listes/List_AllSubjects.txt").read_text(), self.subjects.read_text())
        self.button(app, "Use this design in an analysis").click().run()
        self.assert_healthy(app)
        self.assertEqual(app.radio(key="navigation").value, "Run analysis")
        self.assert_healthy(app)
        self.assertTrue(any("Ready:" in item.value for item in app.success))

    def test_missing_design_is_explained_and_never_starts_a_process(self):
        self.register()
        app = self.app()
        self.page(app, "Run analysis")
        with patch.object(JobStore, "start") as launch:
            self.assertTrue(self.button(app, "Start analysis").disabled)
        self.assert_healthy(app)
        launch.assert_not_called()
        self.assertTrue(any("missing" in item.value.lower() for item in app.warning))

    def test_start_analysis_forwards_a_validated_literal_background_command(self):
        self.register()
        app = self.app()
        self.design_from_browser(app)
        self.button(app, "Use this design in an analysis").click().run()
        with patch.object(JobStore, "start", return_value={"id": "browser-test-job", "status": "running"}) as launch:
            next(widget for widget in app.number_input if widget.label == "Permutations").set_value(777).run()
            command = next(item.value for item in app.code if " run " in item.value)
            self.assertIn("777", shlex.split(command))
            launch.assert_not_called()
            self.button(app, "Start analysis").click().run()
        self.assert_healthy(app)
        launch.assert_called_once()
        args = launch.call_args.args[0]
        self.assertIsInstance(args, list)
        self.assertEqual(args, shlex.split(command)[3:])
        self.assertIn("--non-interactive", args)
        self.assertIn("--noparallel", args)
        self.assertIn("Smoke", args)
        self.assertEqual(launch.call_args.kwargs["metadata"]["workspace"], str(self.study))
        self.assertEqual(launch.call_args.kwargs["metadata"]["action"], "analysis")
        self.assertTrue(any("Started:" in item.value for item in app.success))

    def test_unprepared_study_is_explained_before_an_analysis_can_start(self):
        self.register(prepared=False)
        app = self.app()
        self.design_from_browser(app)
        self.button(app, "Use this design in an analysis").click().run()
        with patch.object(JobStore, "start") as launch:
            self.assertTrue(self.button(app, "Start analysis").disabled)
        self.assert_healthy(app)
        launch.assert_not_called()
        self.assertTrue(any("prepar" in item.value.lower() for item in app.warning))

    def test_saved_swe_design_selects_the_longitudinal_analysis_engine(self):
        subjects = np.repeat([f"S{index:02d}" for index in range(1, 13)], 3)
        visits = np.tile([1, 2, 3], 12)
        self.subjects.write_text("".join(f"{subject}_V{visit}\n" for subject, visit in zip(subjects, visits)))
        pd.DataFrame({"subject": subjects, "visit": visits,
                      "Score": np.random.default_rng(42).normal(size=36)}).to_csv(self.table, index=False)
        self.register()
        app = self.app()
        self.page(app, "Design")
        self.input(app, "Study table path").set_value(str(self.table)).run()
        app.radio(key="design_family").set_value("SwE · repeated measures").run()
        self.button(app, "Save design").click().run()
        self.assert_healthy(app)
        self.assertFalse(app.error, str(app.error))
        self.assertTrue((self.study / "Results/AllSubjects/MainEffect.sub").is_file())
        self.button(app, "Use this design in an analysis").click().run()
        self.assert_healthy(app)
        engine = next(widget for widget in app.selectbox if widget.label == "Statistical engine")
        self.assertEqual(engine.value, "SwE · repeated measures")
        self.assert_healthy(app)
        self.assertTrue(any("Ready:" in item.value for item in app.success))

    def test_report_command_updates_and_only_executes_on_request(self):
        self.register()
        app = self.app()
        with patch.object(JobStore, "start", return_value={"id": "report-test", "status": "running"}) as launch:
            self.page(app, "Reports")
            next(widget for widget in app.number_input if widget.label == "Permutations used").set_value(999).run()
            command = next(item.value for item in app.code if " report " in item.value)
            self.assertIn("999", shlex.split(command))
            launch.assert_not_called()
            self.button(app, "Generate report").click().run()
        self.assert_healthy(app)
        self.assertEqual(launch.call_args.args[0], shlex.split(command)[3:])

    def test_existing_report_preview_and_downloads_are_available(self):
        self.register()
        results = self.study / "Results/AllSubjects"
        results.mkdir()
        report = results / "Smoke_Model_mrsi.html"
        markup = "<!doctype html><html><title>Report test</title><body>Study results</body></html>"
        report.write_text(markup)
        feather = self.exports / "merged.feather"
        pd.DataFrame({"Score": [1.2, 2.3]}).to_feather(feather)
        report.with_suffix(".json").write_text(json.dumps({"outputs": {"feather": str(feather)}}))
        app = self.app()
        with patch.object(components, "html") as preview:
            self.page(app, "Reports")
        self.assert_healthy(app)
        preview.assert_called_once()
        self.assertEqual(preview.call_args.args[0], markup)
        labels = {widget.label for widget in app.download_button}
        self.assertIn("Download HTML report", labels)
        self.assertIn("Download statistical JSON", labels)
        self.assertIn("Download merged data", labels)


if __name__ == "__main__":
    unittest.main()
