"""Configured preparation must work without terminal responses or native dialogs."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HeadlessPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad-prep-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = self.root / "study"
        self.study.mkdir()
        self.inputs = self.root / "images"
        self.inputs.mkdir()
        self.reference = self.root / "reference.nii.gz"
        self.reference.write_bytes(b"synthetic test file")
        self.subjects = self.root / "subjects.txt"
        self.subjects.write_text("001_V1\n")
        self.config = self.study / "config_global.sh"
        values = {"GLOBAL_DIR": str(self.study), "COHORT": "Smoke", "T1_MNI": str(self.reference), "LIST_ALL_SUBJECTS": str(self.subjects)}
        self.config.write_text("".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items()))
        (self.study / "config_dti.sh").write_text(f"NII_DIR_DTI={shlex.quote(str(self.inputs))}\nNII_DIR_DTI_compression=yes\nFILES_DTI=(gFA)\n")
        fsl = self.root / "fsl"
        (fsl / "etc/fslconf").mkdir(parents=True)
        (fsl / "etc/fslconf/fsl.sh").write_text("# Isolated test tools.\n")
        tools = fsl / "share/fsl/bin"
        tools.mkdir(parents=True)
        stats = tools / "fslstats"
        stats.write_text('#!/bin/sh\ncase "$*" in *-R*) echo "1 1" ;; *) echo "1" ;; esac\n')
        stats.chmod(0o755)
        marker = tools / "zenity"
        self.marker = self.root / "native-dialog-called"
        marker.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(self.marker))}\nexit 99\n")
        marker.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(FSLDIR=str(fsl), VLAD_HOME=str(ROOT), VLAD_CONFIG_FILE=str(self.root / "registry.txt"), VLAD_PYTHON_BIN=sys.executable)
        self.env.pop("VLAD_FSL_INITIALIZED_FOR", None)

    def run_prep(self, *args):
        result = subprocess.run(["bash", str(ROOT / "workflows/Prep_VLAD_b6.1.sh"), *args],
                                cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=10)
        self.assertFalse(self.marker.exists(), "Headless preparation opened a native dialog")
        return result

    def test_explicit_config_and_modality_are_required(self):
        result = self.run_prep("--non-interactive")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--config", result.stderr)
        result = self.run_prep("--non-interactive", "--config", str(self.config))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--modality", result.stderr)

    def test_existing_dti_configuration_prepares_without_any_dialog(self):
        (self.inputs / "sub-001_ses-V1_space-mni_acq-dti_desc-gFA_dwi.nii.gz").write_bytes(b"test image")
        result = self.run_prep("--config", str(self.config), "--modality", "DTI", "--non-interactive")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Cohort data preparation completed", result.stdout)
        self.assertIn("DTI_FILES_VALIDATED=(gFA)", (self.study / "config_dti.sh").read_text())

    def test_missing_inputs_fail_instead_of_reporting_preparation_success(self):
        result = self.run_prep("--config", str(self.config), "--modality", "DTI", "--non-interactive")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not pass validation", result.stderr)

    def test_legacy_mrsi_prepares_derived_maps_outside_the_source_tree(self):
        (self.study / "config_mrsi.sh").write_text(
            f"NII_DIR_MRSI={shlex.quote(str(self.inputs))}\nNII_DIR_MRSI_compression=yes\n"
            "MRSI_ANALYSIS_DESCRIPTION=signal\nFILES_MRSI_MET=(signal crlb)\nFILES_MRSI_GLOBAL=(fwhm snr)\n")
        prefix = "sub-001_ses-V1_space-mni"
        for label in ("NAANAAG", "CrPCr", "GPCPCh", "Ins", "GluGln"):
            for metric in ("signal", "crlb"):
                (self.inputs / f"{prefix}_met-{label}_desc-{metric}_mrsi.nii.gz").write_text("source")
        for metric in ("fwhm", "snr"):
            (self.inputs / f"{prefix}_desc-{metric}_mrsi.nii.gz").write_text("source")
        original = {path.name: path.read_bytes() for path in self.inputs.iterdir()}
        maths = Path(self.env["FSLDIR"]) / "share/fsl/bin/fslmaths"
        maths.write_text(
            '#!/bin/bash\n'
            '[[ -f "$1" || -f "$1.nii.gz" ]] || exit 1\n'
            'output="${!#}"\n'
            '[[ "$output" == *.nii.gz ]] || output="$output.nii.gz"\n'
            'printf "derived" >"$output"\n')
        maths.chmod(0o755)
        result = self.run_prep("--config", str(self.config), "--modality", "MRSI", "--non-interactive")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in self.inputs.iterdir()}, original)
        prepared = self.study / "Results/prepared-mrsi/sub-001/ses-V1"
        self.assertTrue((prepared / f"{prefix}_met-SumMetabs_desc-signal_mrsi.nii.gz").is_file())
        self.assertTrue((prepared / f"{prefix}_met-CrPCrRatioSum_desc-signal_mrsi.nii.gz").is_file())
        self.assertTrue((prepared / f"{prefix}_met-CrPCr_desc-QMask_mrsi.nii.gz").is_file())


if __name__ == "__main__":
    unittest.main()
