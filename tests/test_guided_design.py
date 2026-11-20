"""Subject selection and custom hypotheses preserve the observation/model contract."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from vlad.gui.design import (
    DesignError, DesignOptions, detect_identifier_columns, filter_rows,
    load_contrasts, preview_design, validate_contrasts, write_design,
)
from vlad.reporting.io import read_fsl_matrix


class GuidedDesignTests(unittest.TestCase):
    def frame(self):
        rng = np.random.default_rng(45)
        return pd.DataFrame({
            "participant_id": [f"sub-{i:03d}" for i in range(18)],
            "session_id": ["ses-01"] * 18,
            "group": ["Control", "Patient", "Other"] * 6,
            "sex": ["F", "M"] * 9,
            "age": rng.uniform(15, 65, 18),
            "score": rng.normal(size=18),
            "inclusion": ["1"] * 17 + ["0"],
        }, index=np.arange(100, 118))

    def options(self, **changes):
        values = dict(analysis_name="Included", matrix_name="Model",
                      subject_column="participant_id", main_variable="score", predictors=["age"])
        values.update(changes)
        return DesignOptions(**values)

    def test_identifier_detection_prefers_subject_id_and_preserves_bids_session(self):
        frame = self.frame()
        frame["subject_id"] = frame["participant_id"]
        self.assertEqual(detect_identifier_columns(frame), ("subject_id", "session_id"))
        result = preview_design(frame, self.options(subject_column="subject_id"))
        self.assertEqual(result["subjects"][:2], ["000_01", "001_01"])
        # Prefix-free strings from covars.tsv identify the very same files.
        frame["subject_id"] = frame["subject_id"].str.removeprefix("sub-")
        frame["session_id"] = frame["session_id"].str.removeprefix("ses-")
        self.assertEqual(preview_design(frame, self.options(subject_column="subject_id"))["subjects"], result["subjects"])

    def test_category_numeric_manual_and_subject_filters_preserve_source_order(self):
        frame = self.frame()
        result = filter_rows(frame, "participant_id", "session_id",
                             filters=[{"column": "inclusion", "operator": "in", "values": ["1"]},
                                      {"column": "age", "operator": "between", "values": [20, 60]}],
                             include_subjects=[f"{i:03d}" for i in range(16)],
                             exclude_subjects=["sub-004_ses-01", "sub-005"], excluded_rows=[8])
        expected = frame.loc[(frame.inclusion == "1") & frame.age.between(20, 60)].copy()
        expected = expected.iloc[[int(value.removeprefix("sub-")) < 16 and value not in {"sub-004", "sub-005", "sub-007"}
                                  for value in expected.participant_id]]
        self.assertEqual(result["frame"].index.tolist(), expected.index.tolist())
        audit = result["audit"]
        self.assertEqual(audit.loc[4, "reason"], "Explicitly excluded subject/visit")
        self.assertIn("Manually excluded", audit.loc[7, "reason"])
        self.assertIn("Filter: inclusion", audit.loc[17, "reason"])
        self.assertEqual(result["included_rows"], [int(index - 99) for index in expected.index])
        self.assertEqual(len(result["included_rows"]) + len(result["dropped_rows"]), len(frame))

    def test_complete_case_exclusions_join_original_row_selection_audit(self):
        frame = self.frame()
        frame.loc[frame.index[3], "score"] = np.nan
        filtered = filter_rows(frame, "participant_id", "session_id", exclude_subjects=["001"])
        options = self.options(selection_audit=filtered["audit"].to_dict("records"))
        result = preview_design(filtered["frame"], options)
        self.assertEqual(result["dropped_rows"], [2, 4])
        self.assertEqual(result["subjects"][:4], ["000_01", "002_01", "004_01", "005_01"])
        self.assertEqual(result["selection_audit"][3]["reason"], "Missing selected model value")
        self.assertFalse(result["selection_audit"][3]["included"])
        # Calling preview does not modify the user's original selection state.
        self.assertTrue(filtered["audit"].iloc[3]["included"])

    def test_nuisance_and_main_reference_choices_change_the_correct_columns(self):
        frame = self.frame()
        result = preview_design(frame, self.options(main_variable="group", predictors=["sex", "age"],
                                                     reference_levels={"group": "Patient", "sex": "M"}))
        self.assertEqual(result["columns"][:3], ["group[Patient]", "group[Control]", "group[Other]"])
        self.assertIn("sex[F]", result["columns"])
        self.assertEqual(result["coding"]["sex"]["reference"], "M")
        # A known difference of means has the chosen reference's direction.
        y = frame["group"].map({"Patient": 7., "Control": 3., "Other": 5.})
        beta = np.linalg.lstsq(result["design"], y, rcond=None)[0]
        np.testing.assert_allclose(result["contrasts"] @ beta, [4., 2.], atol=1e-10)
        with self.assertRaisesRegex(DesignError, "absent"):
            preview_design(frame, self.options(main_variable="group", reference_levels={"group": "Unknown"}))

    def test_named_import_reorders_weights_and_applies_exact_user_hypothesis(self):
        frame = self.frame()
        base = preview_design(frame, self.options())
        imported = load_contrasts(b"name\tage\tIntercept\tscore\nAge slope\t1\t0\t0\n", base["columns"], "contrast.tsv")
        np.testing.assert_array_equal(imported["contrasts"], [[0, 0, 1]])
        result = preview_design(frame, self.options(contrasts=imported["contrasts"].tolist(),
                                                    contrast_labels=imported["labels"]))
        np.testing.assert_array_equal(result["contrasts"], [[0, 0, 1]])
        self.assertEqual(result["contrast_labels"], ["Age slope"])
        y = 4 + frame.score * 0.3 + (frame.age - frame.age.mean()) * -0.8
        beta = np.linalg.lstsq(result["design"], y, rcond=None)[0]
        np.testing.assert_allclose(result["contrasts"] @ beta, [-.8], atol=1e-12)
        self.assertTrue(result["custom_contrasts"])
        self.assertTrue(any("descriptive" in warning for warning in result["warnings"]))

    def test_fsl_and_numeric_imports_validate_headers_dimensions_and_values(self):
        columns = ["Intercept", "score", "age"]
        imported = load_contrasts(b"/ContrastName1 Score positive\n/NumWaves 3\n/NumContrasts 1\n/Matrix\n0 1 0\n", columns, "my.con")
        np.testing.assert_array_equal(imported["contrasts"], [[0, 1, 0]])
        self.assertEqual(imported["labels"], ["Score positive"])
        imported = load_contrasts(b"0,1,0\n0,-1,0\n", columns, "numeric.csv")
        np.testing.assert_array_equal(imported["contrasts"], [[0, 1, 0], [0, -1, 0]])
        for payload in (b"/NumWaves 3\n/NumContrasts 2\n/Matrix\n0 1 0\n",
                        b"0 1\n", b"0 inf 0\n", b"0 0 0\n",
                        b"name,score,Intercept\nWrong,1,0\n"):
            with self.subTest(payload=payload), self.assertRaises(DesignError):
                load_contrasts(payload, columns, "contrast.con")

    def test_nonestimable_zero_and_mislabelled_contrasts_fail(self):
        rank_deficient = np.array([[1., 1.], [2., 2.], [3., 3.]])
        with self.assertRaisesRegex(DesignError, "not estimable"):
            validate_contrasts([[1, -1]], rank_deficient)
        validate_contrasts([[1, 1]], rank_deficient)
        for matrix, labels in (([[0, 0]], None), ([[1, 0]], [""]),
                               ([[1, 0], [-1, 0]], ["Same", "Same"])):
            with self.subTest(matrix=matrix, labels=labels), self.assertRaises(DesignError):
                validate_contrasts(matrix, np.eye(2), labels)

    def test_custom_anova_replaces_omnibus_and_selection_is_exported(self):
        frame = self.frame()
        filtered = filter_rows(frame, "participant_id", "session_id", exclude_subjects=["017"])
        automatic = self.options(main_variable="group", predictors=[])
        with tempfile.TemporaryDirectory(prefix="vlad-guided-design-") as directory:
            root = Path(directory)
            original = write_design(filtered["frame"], automatic, root)
            self.assertTrue(original["paths"]["fts"].exists())
            selected = self.options(main_variable="group", predictors=[], contrasts=[[0, 1, -1]],
                                    contrast_labels=["Other minus Patient"],
                                    selection_audit=filtered["audit"].to_dict("records"))
            result = write_design(filtered["frame"], selected, root, overwrite=True)
            self.assertFalse(original["paths"]["fts"].exists())
            np.testing.assert_array_equal(read_fsl_matrix(result["paths"]["con"]), [[0, 1, -1]])
            audit = pd.read_csv(result["paths"]["selection"], sep="\t")
            self.assertEqual(audit.loc[17, "reason"], "Explicitly excluded subject/visit")
            metadata = json.loads(result["paths"]["metadata"].read_text())
            self.assertTrue(metadata["custom_contrasts"])
            self.assertEqual(metadata["contrast_labels"], ["Other minus Patient"])
            self.assertEqual(metadata["dropped_rows"], [18])

    def test_invalid_filters_do_not_silently_select_different_subjects(self):
        frame = self.frame()
        for rule in ({"column": "age", "operator": "between", "values": [40, 20]},
                     {"column": "age", "operator": ">", "values": ["nan"]},
                     {"column": "missing", "operator": "in", "values": ["a"]},
                     {"column": "sex", "operator": "in", "values": []}):
            with self.subTest(rule=rule), self.assertRaises(DesignError):
                filter_rows(frame, "participant_id", "session_id", filters=[rule])


if __name__ == "__main__":
    unittest.main()
