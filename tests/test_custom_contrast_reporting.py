"""Reports keep custom voxelwise hypotheses distinct from regional summaries."""

from __future__ import annotations

from html import escape
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from vlad.gui.design import DesignOptions, write_design
from vlad.reporting.io import ReportRequest
from vlad.reporting.render import CUSTOM_RANDOMISE_NOTE, generate_report


class CustomContrastReportingTests(unittest.TestCase):
    def write_region(self, result, values, contrast=1):
        directory = result["paths"]["mat"].parent
        filename = f"meants__People__NAA__Model__signal__grey__contrast{contrast}.txt"
        np.savetxt(directory / filename, values)
        (directory / "To_analyse_with_R_People_Model.txt").write_text(filename + "\n")
        return directory

    def test_randomise_report_names_custom_weights_and_labels_region_statistics_descriptive(self):
        rng = np.random.default_rng(817)
        frame = pd.DataFrame({"participant_id": [f"{i:03d}" for i in range(24)], "session_id": "01",
                              "Score": rng.normal(size=24), "Age": rng.uniform(20, 70, 24)})
        label = "Age slope <not Score>"
        with tempfile.TemporaryDirectory(prefix="vlad-custom-report-") as directory:
            root = Path(directory)
            result = write_design(frame, DesignOptions("People", "Model", "participant_id", "Score", ["Age"],
                                  contrasts=[[0, 0, 1]], contrast_labels=[label]), root)
            values = 2 + .8 * frame.Age + rng.normal(size=24)
            results = self.write_region(result, values)
            request = ReportRequest("randomise", results, "People", "Model", export_dir=root / "exports")
            report = generate_report(request)
            self.assertEqual(report["status"], "complete")
            hypotheses = report["voxelwise_hypotheses"]
            self.assertEqual(hypotheses["terms"], ["Intercept", "Score", "Age"])
            self.assertEqual(hypotheses["weights"], [[0, 0, 1]])
            self.assertEqual(hypotheses["labels"], [label])
            self.assertEqual(hypotheses["regional_summary_role"], "descriptive")
            self.assertIn(CUSTOM_RANDOMISE_NOTE, report["warnings"])
            region = report["regions"][0]
            self.assertEqual(region["summary_role"], "descriptive")
            self.assertEqual(region["voxelwise_contrast"]["weights"], [0, 0, 1])
            self.assertIn(CUSTOM_RANDOMISE_NOTE, region["diagnostics"])
            markup = request.output.read_text()
            self.assertIn("Custom voxelwise hypotheses", markup)
            self.assertIn("Regional statistics below are descriptive summaries", markup)
            self.assertIn(escape(label), markup)
            self.assertNotIn(label, markup)
            inputs = {Path(item["name"]).name for item in report["inputs"]}
            self.assertIn("Model.con", inputs)
            self.assertIn("Model_browser-design.json", inputs)

    def test_swe_still_applies_custom_contrast_without_randomise_summary_warning(self):
        rng = np.random.default_rng(16)
        frame = pd.DataFrame({"subject_id": np.repeat([f"{i:03d}" for i in range(12)], 3),
                              "session_id": np.tile(["01", "02", "03"], 12),
                              "Score": rng.normal(size=36)})
        values = .7 * frame.Score + np.repeat(rng.normal(size=12), 3) + rng.normal(scale=.2, size=36)
        with tempfile.TemporaryDirectory(prefix="vlad-custom-swe-report-") as directory:
            root = Path(directory)
            result = write_design(frame, DesignOptions("People", "Model", "subject_id", "Score", kind="swe",
                                  contrasts=[[0, -1]], contrast_labels=["Negative slope"]), root)
            results = self.write_region(result, values)
            report = generate_report(ReportRequest("swe", results, "People", "Model", export_dir=root / "exports"))
            self.assertEqual(report["status"], "complete")
            self.assertNotIn("voxelwise_hypotheses", report)
            self.assertNotIn(CUSTOM_RANDOMISE_NOTE, report["warnings"])
            contrast = report["regions"][0]["statistics"]["contrast"]
            self.assertTrue(contrast["supported"])
            self.assertLess(contrast["beta"], 0)
            self.assertAlmostEqual(contrast["beta"], -.7, delta=.1)


if __name__ == "__main__":
    unittest.main()
