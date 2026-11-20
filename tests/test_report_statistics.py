"""Numerical parity checks against frozen output from the original R packages."""

import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
from scipy import stats

from vlad.reporting.statistics import analyse_continuous, analyse_groups, describe_design, _fisher_rxc


REFERENCE = json.loads((Path(__file__).parent / "fixtures/randomise_reference.json").read_text())


class RegionalStatisticsTests(unittest.TestCase):
    def test_pearson_and_confidence_interval_match_r_cor_test(self):
        reference = REFERENCE["correlation"]
        result = analyse_continuous(reference["x"], reference["y"])
        for key in ("estimate", "statistic", "df", "p"):
            self.assertAlmostEqual(result[key], reference[key], places=12)
        self.assertAlmostEqual(result["ci_low"], reference["ci"][0], places=12)
        self.assertAlmostEqual(result["ci_high"], reference["ci"][1], places=12)
        self.assertAlmostEqual(result["regression"]["slope"], 1.1696969696969697, places=12)

    def test_welch_and_unpooled_hedges_g_match_r(self):
        fixture = REFERENCE["groups"]
        reference = fixture["two"]
        result = analyse_groups(fixture["values"][:12], fixture["labels"][:12])
        self.assertAlmostEqual(result["statistic"], reference["statistic"], places=12)
        self.assertAlmostEqual(result["df"], reference["df.error"], places=12)
        self.assertAlmostEqual(result["p"], reference["p.value"], places=12)
        self.assertAlmostEqual(result["effect_size"]["estimate"], reference["estimate"], places=12)
        # R's effectsize solves quantiles with optim; Python brackets CDF roots.
        self.assertAlmostEqual(result["effect_size"]["ci_low"], reference["conf.low"], places=6)
        self.assertAlmostEqual(result["effect_size"]["ci_high"], reference["conf.high"], places=6)

    def test_welch_anova_omega_and_holm_games_howell_match_r(self):
        fixture = REFERENCE["groups"]
        reference = fixture["three"]
        result = analyse_groups(fixture["values"], fixture["labels"])
        self.assertAlmostEqual(result["statistic"], reference["statistic"], places=12)
        self.assertAlmostEqual(result["df_num"], reference["df"], places=12)
        self.assertAlmostEqual(result["df_denom"], reference["df.error"], places=12)
        self.assertAlmostEqual(result["p"], reference["p.value"], places=12)
        self.assertAlmostEqual(result["effect_size"]["estimate"], reference["estimate"], places=12)
        self.assertAlmostEqual(result["effect_size"]["ci_low"], reference["conf.low"], places=6)
        self.assertEqual(result["effect_size"]["ci_high"], 1)
        for actual, expected in zip(result["pairwise"], fixture["pairwise"]):
            self.assertEqual(actual["group1"], expected["group1"])
            self.assertEqual(actual["group2"], expected["group2"])
            self.assertAlmostEqual(actual["statistic"], expected["statistic"], places=12)
            self.assertAlmostEqual(actual["p_adjusted"], expected["p.value"], places=8)

    def test_group_order_reverses_signed_effect_and_keeps_p_value(self):
        fixture = REFERENCE["groups"]
        forward = analyse_groups(fixture["values"][:12], fixture["labels"][:12], levels=["A", "B"])
        reverse = analyse_groups(fixture["values"][:12], fixture["labels"][:12], levels=["B", "A"])
        self.assertAlmostEqual(forward["p"], reverse["p"])
        self.assertAlmostEqual(forward["effect_size"]["estimate"], -reverse["effect_size"]["estimate"])
        self.assertAlmostEqual(forward["effect_size"]["ci_low"], -reverse["effect_size"]["ci_high"])

    def test_complete_case_counts_and_constant_inputs_are_json_safe(self):
        result = analyse_continuous([1, 2, 3, np.nan, np.inf], [1, 1, 1, 2, 3])
        self.assertEqual((result["n"], result["n_missing"]), (3, 2))
        self.assertIsNone(result["p"])
        self.assertTrue(result["warnings"])
        grouped = analyse_groups([1, 1, 2, 2, np.nan, 3], ["A", "A", "B", "B", "A", None])
        self.assertEqual((grouped["n"], grouped["n_missing"]), (4, 2))
        self.assertIsNone(grouped["p"])
        json.dumps([result, grouped], allow_nan=False)

    def test_insufficient_groups_and_invalid_lengths_are_diagnosed(self):
        result = analyse_groups([2, 3, 4], ["A", "A", "B"])
        self.assertIsNone(result["statistic"])
        self.assertTrue(result["warnings"])
        with self.assertRaises(ValueError):
            analyse_continuous([1, 2], [1])
        with self.assertRaises(ValueError):
            analyse_groups([1, 2], ["A"])


class DemographicSummaryTests(unittest.TestCase):
    def test_default_tests_match_gtsummary_frozen_reference(self):
        fixture = REFERENCE["demographics"]
        result = describe_design(pd.DataFrame(fixture["data"]), group="group")
        actual = {variable["name"]: variable for variable in result["variables"]}
        self.assertEqual(result["group_counts"], {"A": 12, "B": 12})
        for expected in fixture["tests"]:
            self.assertAlmostEqual(actual[expected["variable"]]["test"]["p"], expected["p.value"], places=12)
        self.assertEqual(actual["age"]["type"], "continuous")
        self.assertIn("Wilcoxon", actual["age"]["test"]["method"])
        self.assertIn("Fisher exact", actual["binary"]["test"]["method"])
        self.assertIn("Fisher–Freeman–Halton exact", actual["category"]["test"]["method"])

    def test_summary_types_missing_counts_and_category_denominators(self):
        frame = pd.DataFrame({"group": ["A", "A", "A", "B", "B", "B"],
                              "code": [1, 2, None, 2, 2, 1], "flag": [True, False, None, True, True, False],
                              "Intercept": 1})
        result = describe_design(frame, group="group", exclude=["Intercept"])
        actual = {variable["name"]: variable for variable in result["variables"]}
        self.assertEqual(set(actual), {"code", "flag"})
        self.assertEqual(actual["code"]["type"], "categorical")
        self.assertEqual(actual["code"]["missing"], 1)
        counts = actual["code"]["groups"]["A"]["levels"]
        self.assertEqual([entry["denominator"] for entry in counts], [2, 2])
        self.assertEqual([entry["percent"] for entry in counts], [50, 50])
        json.dumps(result, allow_nan=False)

    def test_large_expected_counts_use_uncorrected_pearson(self):
        frame = pd.DataFrame({"group": ["A"] * 40 + ["B"] * 40,
                              "category": ["F"] * 25 + ["M"] * 15 + ["F"] * 15 + ["M"] * 25})
        result = describe_design(frame, group="group")["variables"][0]["test"]
        self.assertEqual(result["method"], "Pearson chi-square test (no continuity correction)")
        self.assertAlmostEqual(result["statistic"], 5)
        self.assertAlmostEqual(result["p"], stats.chi2.sf(5, 1))

    def test_three_group_continuous_summary_uses_kruskal(self):
        frame = pd.DataFrame({"group": ["A"] * 4 + ["B"] * 4 + ["C"] * 4, "age": range(12)})
        result = describe_design(frame, group="group")["variables"][0]["test"]
        self.assertEqual(result["method"], "Kruskal–Wallis test")
        expected = stats.kruskal(np.arange(4), np.arange(4, 8), np.arange(8, 12))
        self.assertAlmostEqual(result["p"], expected.pvalue)

    def test_fisher_fallback_is_reproducible_and_labels_monte_carlo(self):
        table = np.array([[3, 1, 2], [2, 4, 1], [1, 1, 3]])
        first = _fisher_rxc(table, max_tables=1)
        second = _fisher_rxc(table, max_tables=1)
        self.assertEqual(first, second)
        self.assertIn("Monte Carlo", first["method"])
        self.assertGreater(first["p"], 0)
        self.assertIn("monte_carlo_standard_error", first)


if __name__ == "__main__":
    unittest.main()
