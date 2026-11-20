"""Scientific/file contracts for the browser's noninteractive design builder."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np
import pandas as pd

from vlad.gui.design import DesignError, DesignOptions, load_table, preview_design, sheet_names, write_design
from vlad.reporting.io import ReportRequest, load_report, read_fsl_matrix


ROOT = Path(__file__).resolve().parents[1]


class BrowserDesignTests(unittest.TestCase):
    def continuous(self):
        rng = np.random.default_rng(21)
        return pd.DataFrame({"ID": [f"{i:03d}_V1" for i in range(18)],
                             "Score": np.linspace(-2, 2, 18), "Age": rng.uniform(20, 80, 18)})

    def options(self, **changes):
        values = dict(analysis_name="People", matrix_name="Model", subject_column="ID",
                      main_variable="Score", predictors=["Age"])
        values.update(changes)
        return DesignOptions(**values)

    def test_uploaded_csv_tsv_and_excel_preserve_text_identifiers(self):
        csv = b"Subject,Visit,Score\n001,1,0.4\n002,2,0.8\n"
        for filename, payload in (("table.csv", csv), ("table.tsv", csv.replace(b",", b"\t"))):
            with self.subTest(filename=filename):
                frame = load_table(payload, filename)
                self.assertEqual(frame["Subject"].tolist(), ["001", "002"])
                self.assertEqual(sheet_names(payload, filename), [])
        workbook = io.BytesIO()
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            pd.DataFrame({"Subject": ["001", "002"], "Score": [1, 2]}).to_excel(writer, sheet_name="People", index=False)
        payload = workbook.getvalue()
        self.assertEqual(sheet_names(payload, "study.xlsx"), ["People"])
        self.assertEqual(load_table(payload, "study.xlsx", "People")["Subject"].tolist(), ["001", "002"])

    def test_invalid_uploaded_excel_has_an_actionable_validation_error(self):
        with self.assertRaisesRegex(DesignError, "Cannot open the workbook"):
            sheet_names(b"not a zip workbook", "study.xlsx")
        with self.assertRaisesRegex(DesignError, "Cannot read the table"):
            load_table(b"not a zip workbook", "study.xlsx")

    def test_continuous_design_contrasts_and_centering_preserve_slope(self):
        frame = self.continuous()
        result = preview_design(frame, self.options())
        design = result["design"]
        self.assertEqual(list(design), ["Intercept", "Score", "Age"])
        self.assertAlmostEqual(design["Age"].mean(), 0, places=12)
        np.testing.assert_array_equal(design["Score"], frame["Score"])
        np.testing.assert_array_equal(result["contrasts"], [[0, 1, 0], [0, -1, 0]])
        y = 3 + frame["Score"].to_numpy() * .7 + (frame["Age"] - frame["Age"].mean()).to_numpy() * .02
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
        np.testing.assert_allclose(result["contrasts"] @ beta, [.7, -.7], atol=1e-12)

    def test_two_groups_are_cell_means_with_signed_between_group_contrasts(self):
        frame = self.continuous()
        frame["Group"] = np.tile(["Control", "Patient"], 9)
        result = preview_design(frame, self.options(main_variable="Group", predictors=[]))
        self.assertEqual(result["main_variables"], ["Group[Control]", "Group[Patient]"])
        np.testing.assert_array_equal(result["contrasts"], [[1, -1], [-1, 1]])
        y = np.where(frame["Group"] == "Control", 4., 7.)
        beta = np.linalg.lstsq(result["design"], y, rcond=None)[0]
        np.testing.assert_allclose(result["contrasts"] @ beta, [-3, 3], atol=1e-12)

    def test_anova_tests_group_equality_rather_than_nonzero_grand_mean(self):
        frame = self.continuous()
        frame["Group"] = np.tile(["A", "B", "C"], 6)
        result = preview_design(frame, self.options(main_variable="Group", predictors=[], analysis_type="anova"))
        self.assertEqual(result["contrasts"].shape, (2, 3))
        self.assertEqual(np.linalg.matrix_rank(result["contrasts"]), 2)
        np.testing.assert_allclose(result["contrasts"] @ np.array([4., 4., 4.]), [0, 0])
        np.testing.assert_array_equal(result["ftests"], [[1, 1]])

    def test_complete_case_selection_is_shared_by_all_exports(self):
        frame = self.continuous()
        frame.loc[2, "Age"] = np.nan
        frame.loc[7, "Score"] = np.nan
        result = preview_design(frame, self.options())
        self.assertEqual(result["dropped_rows"], [3, 8])
        self.assertEqual(result["subjects"], frame.dropna()["ID"].tolist())
        self.assertEqual(result["nobs"], len(frame) - 2)
        self.assertTrue(result["warnings"])

    def test_rank_deficiency_invalid_names_and_duplicate_observations_fail(self):
        frame = self.continuous()
        frame["CopyScore"] = frame["Score"]
        with self.assertRaisesRegex(DesignError, "rank deficient"):
            preview_design(frame, self.options(predictors=["CopyScore"]))
        with self.assertRaisesRegex(DesignError, "letters, numbers and dashes"):
            preview_design(frame, self.options(analysis_name="bad_name"))
        frame.loc[1, "ID"] = frame.loc[0, "ID"]
        with self.assertRaisesRegex(DesignError, "Duplicate subject"):
            preview_design(frame, self.options())

    def test_swE_visit_as_main_has_factor_contrasts_and_modified_subject_file(self):
        frame = pd.DataFrame({"Subject": np.repeat([f"{i:03d}" for i in range(12)], 3),
                              "visit": np.tile([1, 2, 3], 12)})
        options = self.options(kind="swe", subject_column="Subject", visit_column="visit",
                               main_variable="visit", predictors=[])
        result = preview_design(frame, options)
        self.assertEqual(list(result["design"]), ["Intercept", "visit[2]", "visit[3]"])
        np.testing.assert_array_equal(result["contrasts"], [[0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]])
        self.assertEqual(result["subjects"][:3], ["000_V1", "000_V2", "000_V3"])
        np.testing.assert_array_equal(result["sub_matrix"][:3], [[1, 1, 1], [1, 2, 1], [1, 3, 1]])
        self.assertEqual(result["exported"]["subject"].iloc[0], "000")
        with self.assertRaisesRegex(DesignError, "low-cardinality"):
            preview_design(frame, self.options(kind="swe", subject_column="Subject", visit_column="visit",
                                               main_variable="visit", predictors=[], analysis_type="continuous"))

    def test_swE_numeric_group_metadata_does_not_ambiguously_match_design_column(self):
        rng = np.random.default_rng(21)
        frame = pd.DataFrame({"Subject": np.repeat([f"{i:03d}" for i in range(16)], 3),
                              "Visit": np.tile([1, 2, 3], 16), "Group": np.repeat([0, 1] * 8, 3),
                              "Score": rng.normal(size=48)})
        options = self.options(kind="swe", subject_column="Subject", visit_column="Visit",
                               group_column="Group", predictors=["Group"])
        with tempfile.TemporaryDirectory(prefix="vlad browser design ") as folder:
            root = Path(folder)
            result = write_design(frame, options, root)
            self.assertEqual(set(result["exported"]["group"]), {"Group 0", "Group 1"})
            output = root / "Results/People"
            means = output / "meants__People__NAA__Model__signal__grey__contrast1.txt"
            np.savetxt(means, rng.normal(size=len(frame)))
            (output / "To_analyse_with_R_People_Model.txt").write_text(means.name + "\n")
            loaded = load_report(ReportRequest("swe", output, "People", "Model"))
            self.assertEqual(loaded.contrast_terms, list(result["design"]))

    def test_group_assignment_must_be_constant_within_subject(self):
        frame = pd.DataFrame({"Subject": np.repeat(["a", "b", "c", "d"], 3),
                              "Visit": np.tile([1, 2, 3], 4), "Group": np.tile([0, 1, 0], 4),
                              "Score": np.random.default_rng(1).normal(size=12)})
        with self.assertRaisesRegex(DesignError, "constant for each subject"):
            preview_design(frame, self.options(kind="swe", subject_column="Subject", visit_column="Visit",
                                               group_column="Group", predictors=[]))

    def test_files_are_readable_without_fsl_and_require_explicit_replacement(self):
        frame = self.continuous()
        with tempfile.TemporaryDirectory(prefix="browser files with spaces ") as folder:
            root = Path(folder)
            result = write_design(frame, self.options(), root)
            np.testing.assert_allclose(read_fsl_matrix(result["paths"]["mat"]), result["design"])
            np.testing.assert_allclose(read_fsl_matrix(result["paths"]["con"]), result["contrasts"])
            self.assertEqual(result["paths"]["subjects"].read_text().splitlines(), frame["ID"].tolist())
            self.assertEqual(pd.read_csv(result["paths"]["csv"], sep="\t").shape, (18, 3))
            metadata = json.loads(result["paths"]["metadata"].read_text())
            self.assertEqual(metadata["nobs"], 18)
            with self.assertRaisesRegex(DesignError, "overwrite"):
                write_design(frame, self.options(), root)
            write_design(frame, self.options(), root, overwrite=True)
            # A second model may reuse the unchanged subject list.
            write_design(frame, self.options(matrix_name="Another"), root)
            with self.assertRaisesRegex(DesignError, "different subject/visit order"):
                write_design(frame.iloc[::-1].copy(), self.options(matrix_name="Third"), root, overwrite=True)


class NonInteractiveWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="browser workflow ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = (ROOT / "workflows/VLAD_beta7.sh").read_text()
        self.domatrix = source[source.index("domatrix() {\n"):source.index("\ncheckmatrix_4d()", source.index("domatrix() {\n"))]

    def run_domatrix(self, kind="randomise", anova=False):
        code = self.domatrix + '''
NON_INTERACTIVE=true
GLOBAL_DIR="$1"
NAME=People
MATRIX=Model
LONGITUDINAL="$2"
LONGITUDINAL_TOOL=swe
ANOVA="$3"
domatrix || exit $?
printf 'subjects=%s\n' "${#SUBJECTS[@]}"
'''
        return subprocess.run(["bash", "-c", code, "test-domatrix", str(self.root),
                               str(kind == "swe").lower(), "1" if anova else "0"],
                              input="", text=True, capture_output=True, timeout=5, check=False)

    def test_missing_design_fails_promptly_without_reading_stdin(self):
        completed = self.run_domatrix()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Build the design in the browser", completed.stderr)

    def test_prebuilt_design_uses_subject_order_without_prompting(self):
        frame = BrowserDesignTests().continuous()
        write_design(frame, DesignOptions("People", "Model", "ID", "Score", ["Age"]), self.root)
        completed = self.run_domatrix()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("subjects=18", completed.stdout)
        self.assertNotIn("Do you want", completed.stdout)

    def test_noninteractive_dimension_mismatch_fails_before_fsl(self):
        frame = BrowserDesignTests().continuous()
        result = write_design(frame, DesignOptions("People", "Model", "ID", "Score", ["Age"]), self.root)
        result["paths"]["subjects"].write_text("000_V1\n")
        completed = self.run_domatrix()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("dimensions disagree", completed.stderr)

    def test_destructive_flag_is_rejected_before_cohort_loading(self):
        env = os.environ.copy()
        env["VLAD_CONFIG_FILE"] = str(self.root / "nonexistent-registry.txt")
        completed = subprocess.run(["bash", str(ROOT / "workflows/VLAD_beta7.sh"),
                                    "--non-interactive", "-c", "Study", "-n", "People", "-m", "Model",
                                    "--remakeall"], env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=5, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--remakeall is unavailable", completed.stderr)
        self.assertNotIn("registry not found", completed.stderr)

    def test_4d_subject_mismatch_has_a_failing_exit_status(self):
        source = (ROOT / "workflows/VLAD_beta7.sh").read_text()
        start = source.index("checkmatrix_4d() {\n")
        function = source[start:source.index("\n}", start) + 2]
        with tempfile.TemporaryDirectory(prefix="vlad-dimensions-") as folder:
            root = Path(folder)
            design = root / "Results/People/Model.mat"
            design.parent.mkdir(parents=True)
            design.write_text("/NumWaves 2\n/NumPoints 3\n/Matrix\n")
            code = function + '''
GLOBAL_DIR="$1"
NAME=People
MATRIX=Model
fslval() { echo 4; }
checkmatrix_4d image.nii.gz
'''
            completed = subprocess.run(["bash", "-c", code, "dimension-test", str(root)],
                                       capture_output=True, text=True, timeout=5, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("4D card has 4 subjects", completed.stdout)


if __name__ == "__main__":
    unittest.main()
