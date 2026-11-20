"""Mixed-model parity against frozen lmerTest outputs; tests never execute R."""

from pathlib import Path
import json
import unittest

import numpy as np
import pandas as pd
from scipy import stats

from vlad.reporting.mixed import fit_random_intercept, is_categorical


REFERENCE = Path(__file__).parent / "fixtures" / "mixed_reference.json"


class MixedModelReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads(REFERENCE.read_text())
        cls.frame = pd.DataFrame(cls.reference["data"])

    def fit_case(self, case):
        predictors = self.reference[case]["predictors"]
        if isinstance(predictors, str):
            predictors = [predictors]
        return fit_random_intercept(self.frame, "y", predictors, "subject")

    def test_continuous_categorical_and_product_terms_match_lmerTest(self):
        for case in ("continuous", "categorical", "interaction"):
            with self.subTest(case=case):
                model = self.fit_case(case)
                actual = np.array([[row[name] for name in ("beta", "se", "df", "t", "p")]
                                   for row in model.statistics["coefficients"]])
                expected = np.array(self.reference[case]["coefficients"])
                # References are generated with lmerTest 3.2-1 / lme4 2.1-0.
                # Independent optimisers and platform BLAS can differ slightly.
                np.testing.assert_allclose(actual[:, :2], expected[:, :2], rtol=2e-5, atol=2e-6)
                np.testing.assert_allclose(actual[:, 2:4], expected[:, 2:4], rtol=2e-5, atol=2e-4)
                np.testing.assert_allclose(actual[:, 4], expected[:, 4], rtol=2e-5, atol=1e-12)
                self.assertTrue(model.statistics["converged"])
                self.assertFalse(model.statistics["singular"])

    def test_likelihood_covariance_variance_and_r2_match_reference(self):
        for case in ("continuous", "categorical", "interaction"):
            with self.subTest(case=case):
                model = self.fit_case(case)
                reference = self.reference[case]
                for field in ("random_variance", "residual_variance", "log_likelihood"):
                    self.assertAlmostEqual(model.statistics[field], reference[field], delta=2e-6)
                np.testing.assert_allclose(model.statistics["covariance"], reference["covariance"],
                                           rtol=2e-5, atol=2e-7)
                expected_r2 = np.array(reference["r2"]).reshape(-1)
                np.testing.assert_allclose([model.statistics["r2_marginal"], model.statistics["r2_conditional"]],
                                           expected_r2, rtol=2e-5, atol=2e-7)

    def test_population_predictions_and_standard_errors_match_lmer(self):
        for case in ("continuous", "categorical", "interaction"):
            with self.subTest(case=case):
                model = self.fit_case(case)
                prediction = model.predict(self.frame.iloc[:3])
                np.testing.assert_allclose(prediction["mean"], self.reference[case]["predictions"], atol=2e-7)
                np.testing.assert_allclose(prediction["se"], self.reference[case]["prediction_se"], atol=2e-7)
                critical = stats.t.ppf(0.975, len(self.frame) - len(model.design_columns) - 2)
                np.testing.assert_allclose(prediction["ci_high"] - prediction["mean"],
                                           critical * prediction["se"], atol=1e-12)

    def test_signed_contrasts_preserve_sign_and_match_lmerTest(self):
        results = []
        for sign, reference_name in ((1, "contrast_positive"), (-1, "contrast_negative")):
            model = fit_random_intercept(self.frame, "y", ["x"], "subject",
                                         contrast=np.array([0, sign]), contrast_terms=["Intercept", "x"])
            result = model.statistics["contrast"]
            self.assertTrue(result["supported"])
            expected = np.array(self.reference["continuous"][reference_name]).reshape(-1)
            np.testing.assert_allclose([result[name] for name in ("beta", "se", "df", "t", "p")],
                                       expected, rtol=2e-5, atol=2e-6)
            results.append(result)
        self.assertAlmostEqual(results[0]["beta"], -results[1]["beta"], places=12)
        self.assertAlmostEqual(results[0]["p"], results[1]["p"], places=14)
        self.assertAlmostEqual(results[0]["ci_low"], -results[1]["ci_high"], places=12)

    def test_missing_rows_are_removed_jointly_with_diagnostics(self):
        frame = self.frame.copy()
        frame.loc[0, "y"] = np.nan
        frame.loc[1, "x"] = np.inf
        frame.loc[2, "subject"] = None
        model = fit_random_intercept(frame, "y", ["x"], "subject")
        self.assertEqual(model.statistics["nobs"], len(frame) - 3)
        self.assertEqual(model.statistics["rows_dropped"], 3)
        self.assertIn("Removed 3", model.statistics["warnings"][0])
        json.dumps(model.statistics, allow_nan=False)

    def test_unknown_prediction_categories_are_not_silently_recoded(self):
        model = self.fit_case("categorical")
        newdata = self.frame.iloc[:2].copy()
        newdata["group"] = "Unseen"
        with self.assertRaisesRegex(ValueError, "Unknown levels"):
            model.predict(newdata)


class MixedModelValidationTests(unittest.TestCase):
    def boundary_frame(self):
        x = np.tile([-1.5, -0.5, 0.5, 1.5], 6)
        residual = np.tile([0.1, -0.1, -0.1, 0.1], 6)
        return pd.DataFrame({"subject": np.repeat(np.arange(6), 4), "x": x,
                             "y": 1 + 0.5 * x + residual})

    def test_zero_random_variance_is_reported_as_singular(self):
        model = fit_random_intercept(self.boundary_frame(), "y", ["x"], "subject")
        self.assertTrue(model.statistics["singular"])
        self.assertEqual(model.statistics["random_variance"], 0)
        self.assertTrue(any("boundary" in warning for warning in model.statistics["warnings"]))
        self.assertTrue(all(row["df"] > 0 for row in model.statistics["coefficients"]))

    def test_rank_deficiency_and_no_repeated_subjects_fail_clearly(self):
        frame = self.boundary_frame()
        frame["copy_x"] = frame["x"]
        with self.assertRaisesRegex(ValueError, "rank deficient"):
            fit_random_intercept(frame, "y", ["x", "copy_x"], "subject")
        frame["subject"] = range(len(frame))
        with self.assertRaisesRegex(ValueError, "repeated observations"):
            fit_random_intercept(frame, "y", ["x"], "subject")

    def test_ambiguous_factor_contrast_is_explicitly_unsupported(self):
        frame = self.boundary_frame()
        frame["category"] = np.tile(["a", "b", "c", "a"], 6)
        model = fit_random_intercept(frame, "y", ["category"], "subject",
                                     contrast=np.array([0, 1]), contrast_terms=["Intercept", "category"])
        self.assertFalse(model.statistics["contrast"]["supported"])
        self.assertIn("category", model.statistics["contrast"]["reason"])

    def test_contrast_dimensions_must_match_declared_terms(self):
        with self.assertRaisesRegex(ValueError, "Contrast length"):
            fit_random_intercept(self.boundary_frame(), "y", ["x"], "subject",
                                 contrast=np.array([1]), contrast_terms=["Intercept", "x"])

    def test_zero_contrast_reports_unavailable_inference_explicitly(self):
        model = fit_random_intercept(self.boundary_frame(), "y", ["x"], "subject",
                                     contrast=np.array([0, 0]), contrast_terms=["Intercept", "x"])
        self.assertFalse(model.statistics["contrast"]["supported"])
        self.assertIn("identically zero", model.statistics["contrast"]["reason"])
        self.assertTrue(any("identically zero" in warning for warning in model.statistics["warnings"]))

    def test_factor_coercion_matches_legacy_policy(self):
        self.assertTrue(is_categorical(pd.Series([0.1, 0.2] * 3)))
        self.assertTrue(is_categorical(pd.Series([1, 2, 3, 4, 5, 6])))
        self.assertFalse(is_categorical(pd.Series([1, 2, 3, 4, 5, 6, 7])))
        self.assertFalse(is_categorical(pd.Series([0.1, 0.2, 0.3])))

    def test_global_continuous_type_is_preserved_after_complete_case_deletion(self):
        subject = np.repeat(np.arange(8), 9)
        x = np.tile(np.arange(1, 10), 8)
        y = 1 + 0.4 * x + np.cos(subject * 0.7) + np.sin(np.arange(len(x))) * 0.2
        frame = pd.DataFrame({"subject": subject, "x": x, "y": y})
        frame.loc[frame["x"] > 6, "y"] = np.nan
        model = fit_random_intercept(frame, "y", ["x"], "subject")
        self.assertEqual(model.predictor_types["x"], "continuous")
        self.assertEqual(model.design_columns, ["(Intercept)", "x"])
        self.assertEqual(model.statistics["nobs"], 48)

    def test_unused_factor_levels_are_dropped_from_complete_model_frame(self):
        frame = self.boundary_frame()
        frame["category"] = pd.Categorical(np.tile(["a", "b", "c", "a"], 6),
                                            categories=["unused", "a", "b", "c"])
        frame.loc[frame["category"] == "c", "y"] = np.nan
        model = fit_random_intercept(frame, "y", ["category"], "subject")
        self.assertEqual(model.categorical_levels["category"], ["a", "b"])
        self.assertEqual(model.design_columns, ["(Intercept)", "category[b]"])
        self.assertEqual(model.statistics["nobs"], 18)

    def test_nonpositive_prediction_df_keeps_means_but_has_no_invented_interval(self):
        frame = pd.DataFrame({"subject": ["a", "a", "b", "b"],
                              "x": [0.11, 0.4, 0.7, 1.21], "y": [1.0, 1.2, 2.3, 2.1]})
        model = fit_random_intercept(frame, "y", ["x"], "subject")
        self.assertEqual(model.statistics["prediction_df"], 0)
        prediction = model.predict(frame)
        self.assertTrue(np.isfinite(prediction[["mean", "se"]].to_numpy()).all())
        self.assertTrue(prediction[["ci_low", "ci_high"]].isna().all().all())
        self.assertTrue(any("prediction intervals are unavailable" in warning
                            for warning in model.statistics["warnings"]))


if __name__ == "__main__":
    unittest.main()
