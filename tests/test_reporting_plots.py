"""Check that interaction prediction plots vary the actual design products."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from vlad.reporting.mixed import fit_random_intercept
from vlad.reporting.plots import mixed_prediction_grid


class InteractionPredictionTests(unittest.TestCase):
    def test_all_legacy_product_labels_recompute_products_and_produce_distinct_slopes(self):
        rng = np.random.default_rng(1842)
        n_subjects, visits = 16, 4
        subject = np.repeat([f"S{index:02d}" for index in range(n_subjects)], visits)
        group = np.repeat(np.tile([0, 1], n_subjects // 2), visits)
        score = rng.normal(size=len(subject))
        product = score * group
        response = (0.6 * score + 2 * group + 1.5 * product
                    + np.repeat(rng.normal(size=n_subjects), visits)
                    + rng.normal(scale=0.15, size=len(subject)))
        for product_name in ("Score_x_Group", "Score:Group", "Score*Group"):
            with self.subTest(product=product_name):
                frame = pd.DataFrame({"subject": subject, "Score": score, "Group": group,
                                      product_name: product, "response": response})
                model = fit_random_intercept(
                    frame, "response", ["Score", "Group", product_name], "subject"
                )
                grid, x_variable, grouping = mixed_prediction_grid(frame, model, product_name)
                self.assertEqual((x_variable, grouping), ("Score", "Group"))
                self.assertEqual(len(grid), 200)
                np.testing.assert_allclose(grid[product_name], grid["Score"] * grid["Group"])
                expected = model.predict(grid)
                first = grid["Group"] == model.categorical_levels["Group"][0]
                second = grid["Group"] == model.categorical_levels["Group"][1]
                np.testing.assert_allclose(grid.loc[first, "Score"], grid.loc[second, "Score"])
                difference = (expected.loc[second, "mean"].to_numpy()
                              - expected.loc[first, "mean"].to_numpy())
                # Holding the product at its mean would draw parallel curves;
                # this fixture has an interaction large enough to expose that.
                self.assertGreater(np.ptp(difference), 1.0)


if __name__ == "__main__":
    unittest.main()
