"""Command composition retains original paths and never stages uploads."""

from pathlib import Path
import json
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from vlad.gui.file_picker import choose_native_paths
from vlad.gui.path_dialog import choose_path
from vlad.gui.settings import load_gui_settings, save_gui_settings

from vlad.gui.cohorts import load_cohort
from vlad.gui.commands import command_text, runtime_environment, settings_command

ROOT = Path(__file__).resolve().parents[1]


class SettingsCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vlad-command-settings-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source = self.base / "source 'literal' $DATA"
        self.source.mkdir()
        self.reference = self.source / "reference.nii.gz"
        self.reference.write_bytes(b"reference fixture")
        self.subjects = self.source / "covars.tsv"
        self.subjects.write_text("subject_id\tsession_id\nsub-001\tses-01\nsub-002\tses-01\n")
        self.registry = self.base / "registry.txt"
        self.settings = dict(registry=self.registry, name="Study", global_dir=self.base / "study",
                             reference_image=self.reference, subjects_file=self.subjects, input_dir=self.source,
                             space="MNI152NLin2009cAsym", resolution="3", input_mode="bids", metabolites=["NAA", "CrPCr"],
                             masks=[], dwi_files=["*desc-dipygfa*_GFA.nii.gz"], crlb_max=18., fwhm_max=.09,
                             snr_min=11., mask_space_confirmed=True, compression=False,
                             reference_metadata={"template": "MNI152NLin2009cAsym", "resolution": "2"})

    def execute(self):
        return subprocess.run(settings_command(self.settings), cwd=ROOT,
                              env=runtime_environment(ROOT, self.registry), capture_output=True, text=True)

    def test_preview_is_read_only_and_exact_argv_round_trips_through_cli(self):
        before = {path: path.read_bytes() for path in self.source.iterdir() if path.is_file()}
        command = settings_command(self.settings)
        copied = shlex.split(command_text(command, ROOT, self.registry))
        self.assertEqual(copied[3:], command)
        self.assertFalse(self.settings["global_dir"].exists())
        self.assertFalse(self.registry.exists())
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        cohort = load_cohort(self.settings["global_dir"] / "config_global.sh")
        self.assertEqual(cohort.values["T1_MNI"], str(self.reference))
        self.assertEqual(cohort.values["COVARIATES_FILE"], str(self.subjects))
        self.assertEqual(cohort.values["METAB"], ["NAA", "CrPCr"])
        self.assertEqual(cohort.values["MRSI_RESOLUTION"], "3")
        self.assertEqual(float(cohort.values["MRSI_CRLB_MAX"]), 18.)
        self.assertEqual(float(cohort.values["MRSI_FWHM_MAX"]), .09)
        self.assertEqual(float(cohort.values["MRSI_SNR_MIN"]), 11.)
        self.assertEqual(json.loads(cohort.values["REFERENCE_TEMPLATE_METADATA"]), self.settings["reference_metadata"])
        self.assertEqual(Path(cohort.values["LIST_ALL_SUBJECTS"]).read_text(), "001_01\n002_01\n")
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_invalid_input_fails_without_creating_study_and_overwrite_is_explicit(self):
        self.settings["reference_image"] = self.base / "missing.nii.gz"
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing NIfTI", result.stderr)
        self.assertFalse(self.settings["global_dir"].exists())
        self.settings["reference_image"] = self.reference
        self.assertEqual(self.execute().returncode, 0)
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("overwrite", result.stderr)
        self.settings["overwrite"] = True
        self.assertEqual(self.execute().returncode, 0)


class NativeChooserProcessTests(unittest.TestCase):
    def test_dialog_runs_outside_streamlit_and_returns_literal_paths(self):
        selected = "/data/subject 'one' $literal/covars.tsv"
        response = subprocess.CompletedProcess([], 0, json.dumps({"paths": [selected]}), "")
        with patch("vlad.gui.file_picker.subprocess.run", return_value=response) as run:
            self.assertEqual(choose_native_paths(title="Covariates", current=selected, kind="file"), [selected])
        argv = run.call_args.args[0]
        self.assertEqual(Path(argv[1]).name, "path_dialog.py")
        self.assertEqual(json.loads(argv[2])["current"], selected)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_global_bids_directory_is_forwarded_to_the_dialog(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({"paths": []}), "")
        with patch("vlad.gui.file_picker.subprocess.run", return_value=response) as run:
            choose_native_paths(kind="file", current="/elsewhere/covars.tsv",
                                default_dir="/data/main-bids")
        options = json.loads(run.call_args.args[0][2])
        self.assertEqual(options["current"], "/elsewhere/covars.tsv")
        self.assertEqual(options["default_dir"], "/data/main-bids")

    def test_cancel_and_desktop_unavailability_are_distinct(self):
        cancelled = subprocess.CompletedProcess([], 0, '{"paths": []}', "")
        with patch("vlad.gui.file_picker.subprocess.run", return_value=cancelled):
            self.assertEqual(choose_native_paths(kind="file"), [])
        unavailable = subprocess.CompletedProcess([], 1, '{"error": "cannot open display"}', "")
        with patch("vlad.gui.file_picker.subprocess.run", return_value=unavailable):
            with self.assertRaisesRegex(RuntimeError, "cannot open display"):
                choose_native_paths(kind="file")

    def test_desktop_chooser_uses_global_bids_directory_as_initial_folder(self):
        root = MagicMock()
        root.tk.splitlist.return_value = []
        with (patch("tkinter.Tk", return_value=root),
              patch("tkinter.filedialog.askopenfilename", return_value="") as dialog):
            self.assertEqual(
                choose_path(
                    title="Covariates", current="/somewhere/old.tsv", kind="file",
                    extensions=[".tsv"], default_dir="/tmp",
                ),
                [],
            )
        self.assertEqual(dialog.call_args.kwargs["initialdir"], "/tmp")
        self.assertEqual(dialog.call_args.kwargs["initialfile"], "old.tsv")
        root.destroy.assert_called_once()

    def test_timed_out_dialog_has_an_actionable_error(self):
        with patch("vlad.gui.file_picker.subprocess.run", side_effect=subprocess.TimeoutExpired("chooser", 180)):
            with self.assertRaisesRegex(RuntimeError, "Click Browse"):
                choose_native_paths(kind="file")


class GlobalSettingsTests(unittest.TestCase):
    def test_bids_directory_persists_and_must_exist(self):
        with tempfile.TemporaryDirectory(prefix="vlad-global-settings-") as temporary:
            state = Path(temporary) / "state"
            bids = Path(temporary) / "BIDS project"
            bids.mkdir()
            self.assertEqual(load_gui_settings(state), {
                "bids_dir": "", "theme": "Light", "settings_dir": str(state.resolve())
            })
            saved = save_gui_settings(state, bids_dir=str(bids))
            self.assertEqual(saved, {
                "bids_dir": str(bids.resolve()), "theme": "Light",
                "settings_dir": str(state.resolve()),
            })
            self.assertEqual(load_gui_settings(state), saved)
            with self.assertRaisesRegex(ValueError, "existing main BIDS"):
                save_gui_settings(state, bids_dir=str(Path(temporary) / "missing"))

    def test_theme_and_custom_gui_data_folder_survive_restart_pointer(self):
        with tempfile.TemporaryDirectory(prefix="vlad-global-settings-") as temporary:
            bootstrap = Path(temporary) / "bootstrap"
            storage = Path(temporary) / "custom GUI data"
            saved = save_gui_settings(
                bootstrap, bids_dir="", theme="Dark", settings_dir=str(storage)
            )
            self.assertEqual(saved["theme"], "Dark")
            self.assertEqual(saved["settings_dir"], str(storage.resolve()))
            self.assertTrue((storage / "settings.json").is_file())
            pointer = json.loads((bootstrap / "settings-location.json").read_text())
            self.assertEqual(pointer["settings_dir"], str(storage.resolve()))
            self.assertEqual(load_gui_settings(bootstrap), saved)


if __name__ == "__main__":
    unittest.main()
