"""Exercise Python report generation and Bash integration without FSL or R."""

from __future__ import annotations

import base64
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REPORT_RUNNER = ROOT / "src" / "vlad" / "reporting" / "run.py"
REPORT_DATE = "2024-01-02"
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4n8AAAAASUVORK5CYII="
)


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.external_resources = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "img":
            self.images.append(attributes.get("src", ""))
        if tag == "script" and attributes.get("src"):
            self.external_resources.append(attributes["src"])
        if tag == "link" and attributes.get("href"):
            self.external_resources.append(attributes["href"])


class ReportingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad Python report test ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.results = self.work / "cohort results with spaces"
        self.exports = self.work / "export directory"
        self.caller = self.work / "unrelated caller directory"
        self.results.mkdir()
        self.caller.mkdir()
        self.analysis = "Smoke"
        self.matrix = "Model"
        self.env = os.environ.copy()
        self.env.pop("PYTHONPATH", None)
        for name in list(self.env):
            if name.startswith("VLAD_"):
                self.env.pop(name)
        self.env.update(MPLBACKEND="Agg", MPLCONFIGDIR=str(self.work / "matplotlib"),
                        PYTHONDONTWRITEBYTECODE="1")
        # Any accidental invocation through PATH would leave a marker and fail.
        tools = self.work / "sentinel tools"
        tools.mkdir()
        self.r_marker = self.work / "unexpected R runtime"
        for name in ("Rscript", "R", "pandoc"):
            executable = tools / name
            executable.write_text('#!/bin/sh\n: > "$VLAD_TEST_R_MARKER"\nexit 97\n')
            executable.chmod(0o755)
        self.env["PATH"] = str(tools) + os.pathsep + self.env.get("PATH", "")
        self.env["VLAD_TEST_R_MARKER"] = str(self.r_marker)

    @property
    def list_path(self):
        return self.results / f"To_analyse_with_R_{self.analysis}_{self.matrix}.txt"

    def write_design(self, design, main_variables=None):
        design.to_csv(self.results / f"{self.matrix}.csv", sep="\t", index=False)
        if main_variables is not None:
            (self.results / f"main_variable_{self.matrix}.txt").write_text(
                "\n".join(main_variables) + "\n"
            )

    def write_region(self, values, contrast=1, metabolite="NAA", append=False):
        name = f"meants__{self.analysis}__{metabolite}__{self.matrix}__signal__grey__contrast{contrast}.txt"
        path = self.results / name
        path.write_text("\n".join("NA" if pd.isna(value) else repr(float(value)) for value in values) + "\n")
        if append:
            with self.list_path.open("a") as handle:
                handle.write(name + "\n")
        else:
            self.list_path.write_text(name + "\n")
        return path

    def write_contrasts(self, rows):
        (self.results / f"{self.matrix}.con").write_text(
            f"/NumWaves {len(rows[0])}\n/NumContrasts {len(rows)}\n/Matrix\n"
            + "\n".join(" ".join(str(value) for value in row) for row in rows) + "\n"
        )

    def run_report(self, kind="randomise", extra=(), cli=False):
        command = [sys.executable, str(REPORT_RUNNER)]
        env = self.env.copy()
        if cli:
            command = [sys.executable, "-m", "vlad", "report"]
            env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [*command, "--kind", kind, "--results-dir", str(self.results),
             "--analysis-name", self.analysis, "--matrix-name", self.matrix,
             "--permutations", "500", "--modality", "mrsi", "--export-dir", str(self.exports),
             "--date", REPORT_DATE, *extra],
            cwd=self.caller, env=env, capture_output=True, text=True, timeout=90,
            check=False,
        )

    def manifest(self, kind="randomise", confirmation=False):
        suffix = "mrsi" + ("-swe" if kind == "swe" else "")
        if confirmation:
            suffix += "-confirmation"
        output = self.results / f"{self.analysis}_{self.matrix}_{suffix}.html"
        self.assertTrue(output.is_file(), output)
        self.assertGreater(output.stat().st_size, 1000)
        metadata = output.with_suffix(".json")
        self.assertTrue(metadata.is_file(), metadata)

        def reject_nonfinite(value):
            self.fail(f"Nonstandard JSON value: {value}")

        payload = json.loads(metadata.read_text(), parse_constant=reject_nonfinite)
        self.assertFalse(self.r_marker.exists(), "Reporting attempted to invoke R or Pandoc")
        return output, payload

    def assert_report_passed(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_continuous_report_exports_and_embeds_assets_from_another_directory(self):
        score = np.linspace(-2, 2, 30)
        values = 0.7 * score + np.random.default_rng(42).normal(0, 0.4, len(score))
        self.write_design(pd.DataFrame({"Intercept": 1, "Score": score}), ["Score"])
        self.write_region(values)
        for name in (f"{self.matrix}_visualisation.png", f"{self.matrix}_correlation_matrix.png",
                     f"NAA__{self.matrix}__signal__grey__contrast1.png"):
            (self.results / name).write_bytes(PIXEL_PNG)
        self.assert_report_passed(self.run_report())
        output, payload = self.manifest()
        self.assertEqual(len(payload["regions"]), 1)
        self.assertIn("Pearson", payload["regions"][0]["statistics"]["method"])
        feather = self.exports / f"Smoke_Model_{REPORT_DATE}.feather"
        merged = pd.read_feather(feather)
        np.testing.assert_allclose(merged["Score"], score)
        np.testing.assert_allclose(merged["NAA__Model__signal__grey__contrast1"], values)
        self.assertTrue((self.exports / f"NAA__Model__signal__grey__contrast1{REPORT_DATE}.svg").is_file())
        markup = output.read_text()
        parsed = AssetParser()
        parsed.feed(markup)
        self.assertTrue(parsed.images, "The existing PNG pictures were not included")
        self.assertTrue(all(source.startswith("data:image/") for source in parsed.images), parsed.images)
        self.assertEqual(parsed.external_resources, [])
        self.assertTrue("<svg" in markup or "data:image/svg" in markup)

    def test_two_group_report_uses_derived_group_order_and_welch_test(self):
        grouping = np.repeat([0, 1], 12)
        values = 4 * (1 - grouping) + np.random.default_rng(8).normal(size=24)
        self.write_design(pd.DataFrame({"Patients": 1 - grouping, "Controls": grouping,
                                        "Age": np.arange(24) + 20}), ["Patients", "Controls"])
        self.write_region(values)
        self.assert_report_passed(self.run_report(cli=True))
        _, payload = self.manifest()
        statistics = payload["regions"][0]["statistics"]
        self.assertIn("Welch", statistics["method"])
        self.assertLess(statistics["p"], 0.001)
        self.assertEqual([row["name"] for row in statistics["groups"]], ["Patients", "Controls"])
        merged = pd.read_feather(self.exports / f"Smoke_Model_{REPORT_DATE}.feather")
        self.assertEqual(merged["group"].astype(str).tolist(), ["Patients"] * 12 + ["Controls"] * 12)

    def test_three_group_report_outputs_welch_anova_and_all_pairwise_tests(self):
        grouping = np.repeat(np.arange(3), 10)
        labels = ["GroupA", "GroupB", "GroupC"]
        design = pd.DataFrame({label: (grouping == index).astype(int) for index, label in enumerate(labels)})
        self.write_design(design, labels)
        self.write_region(4 * grouping + np.random.default_rng(9).normal(size=30))
        self.assert_report_passed(self.run_report())
        _, payload = self.manifest()
        statistics = payload["regions"][0]["statistics"]
        self.assertIn("ANOVA", statistics["method"])
        self.assertLess(statistics["p"], 0.001)
        self.assertEqual(len(statistics["pairwise"]), 3)
        self.assertTrue(all(row["p_adjusted"] is not None for row in statistics["pairwise"]))

    def mixed_fixture(self):
        rng = np.random.default_rng(428)
        subject = np.repeat([f"S{index:02d}" for index in range(16)], 3)
        score = rng.normal(size=len(subject))
        values = 0.8 * score + np.repeat(rng.normal(size=16), 3) + rng.normal(0, 0.3, len(subject))
        design = pd.DataFrame({"subject": subject, "visit": np.tile([1, 2, 3], 16),
                               "Intercept": 1, "Score": score})
        self.write_design(design)
        return values

    def test_longitudinal_report_applies_both_signed_contrasts(self):
        values = self.mixed_fixture()
        self.write_region(values, 1)
        self.write_region(values, 2, append=True)
        self.write_contrasts([[0, 1], [0, -1]])
        self.assert_report_passed(self.run_report("swe"))
        _, payload = self.manifest("swe")
        self.assertEqual(len(payload["regions"]), 2)
        first, second = [region["statistics"] for region in payload["regions"]]
        self.assertEqual(first["n_subjects"], 16)
        self.assertEqual(first["nobs"], 48)
        self.assertGreater(first["random_variance"], 0)
        self.assertTrue(first["contrast"]["supported"])
        self.assertAlmostEqual(first["contrast"]["beta"], -second["contrast"]["beta"], places=8)
        self.assertAlmostEqual(first["contrast"]["p"], second["contrast"]["p"], places=8)
        self.assertTrue((self.exports / f"Smoke_Model_{REPORT_DATE}_swe.feather").is_file())
        self.assertEqual(len(list(self.exports.glob("*_lme.svg"))), 2)

    def test_single_contrast_longitudinal_report_and_confirmation_name(self):
        values = self.mixed_fixture()
        self.write_region(values)
        self.write_contrasts([[0, 1]])
        self.assert_report_passed(self.run_report("swe", extra=["--confirmation"]))
        _, payload = self.manifest("swe", confirmation=True)
        self.assertTrue(payload["regions"][0]["statistics"]["contrast"]["supported"])

    def test_unmapped_multilevel_contrast_preserves_partial_report_and_factor_export(self):
        values = self.mixed_fixture()
        path = self.results / f"{self.matrix}.csv"
        design = pd.read_csv(path, sep="\t")
        design["Category"] = np.tile([0, 1, 2], 16)
        self.write_design(design)
        self.write_region(values)
        self.write_contrasts([[0, 0, 1]])
        completed = self.run_report("swe")
        self.assertNotEqual(completed.returncode, 0)
        _, payload = self.manifest("swe")
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["regions"][0]["status"], "partial")
        model = payload["regions"][0]["statistics"]
        self.assertFalse(model["contrast"]["supported"])
        self.assertTrue(model["coefficients"])
        self.assertTrue(payload["errors"])
        merged = pd.read_feather(self.exports / f"Smoke_Model_{REPORT_DATE}_swe.feather")
        self.assertIsInstance(merged["Category"].dtype, pd.CategoricalDtype)

    def test_empty_regions_produce_a_report_and_merged_design(self):
        self.write_design(pd.DataFrame({"Intercept": [1, 1, 1], "Score": [1.2, 3.4, 5.6]}))
        self.list_path.write_text("# No significant regions\n")
        self.assert_report_passed(self.run_report())
        _, payload = self.manifest()
        self.assertEqual(payload["regions"], [])
        self.assertEqual(len(pd.read_feather(self.exports / f"Smoke_Model_{REPORT_DATE}.feather")), 3)

    def test_user_labels_are_escaped_in_html(self):
        self.analysis = "Study <script>window.pwned=1"
        self.matrix = "Model <unsafe>"
        self.write_design(pd.DataFrame({"Intercept": 1, "Score": np.arange(12)}), ["Score"])
        label = "NAA<img onerror=bad>"
        self.write_region(np.arange(12) + np.random.default_rng(1).normal(size=12), metabolite=label)
        self.assert_report_passed(self.run_report())
        output, _ = self.manifest()
        markup = output.read_text()
        self.assertNotIn(self.analysis, markup)
        self.assertNotIn(label, markup)
        self.assertIn(escape(self.analysis), markup)
        self.assertIn(escape(label), markup)

    def test_invalid_mean_length_and_duplicate_inputs_fail_before_export(self):
        self.write_design(pd.DataFrame({"Intercept": 1, "Score": np.arange(12)}), ["Score"])
        path = self.write_region(np.arange(11))
        completed = self.run_report()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("expected 12 rows", completed.stdout + completed.stderr)
        self.assertFalse(self.exports.exists())
        self.write_region(np.arange(12))
        self.list_path.write_text(path.name + "\n" + path.name + "\n")
        completed = self.run_report()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Duplicate", completed.stdout + completed.stderr)
        self.assertFalse(self.exports.exists())

    def test_invalid_contrast_headers_and_alignment_fail_instead_of_truncating(self):
        values = self.mixed_fixture()
        self.write_region(values)
        self.write_contrasts([[0, 1]])
        contrast = self.results / f"{self.matrix}.con"
        contrast.write_text(contrast.read_text().replace("/NumWaves 2", "/NumWaves 3"))
        completed = self.run_report("swe")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("header", (completed.stdout + completed.stderr).lower())
        self.write_contrasts([[0, 1, 0]])
        completed = self.run_report("swe")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Contrast/design", completed.stdout + completed.stderr)
        self.assertFalse(self.exports.exists())

    def test_mixed_prediction_ranges_and_covariate_means_use_only_complete_model_rows(self):
        from vlad.reporting.io import ReportRequest
        from vlad.reporting.render import generate_report

        values = self.mixed_fixture()
        path = self.results / f"{self.matrix}.csv"
        design = pd.read_csv(path, sep="\t")
        design["Age"] = np.linspace(20, 80, len(design))
        # One row has a missing response and an extreme x/covariate; another
        # has a finite response but a missing nuisance covariate and extreme x.
        design.loc[len(design) - 1, ["Score", "Age"]] = [1000, 10000]
        design.loc[len(design) - 2, ["Score", "Age"]] = [-1000, np.nan]
        values[-1] = np.nan
        self.write_design(design)
        self.write_region(values)
        self.write_contrasts([[0, 1, 0]])
        expected = design.iloc[:-2]
        payload = generate_report(ReportRequest(
            "swe", self.results, self.analysis, self.matrix,
            export_dir=self.exports, report_date=REPORT_DATE,
        ))
        self.assertEqual(payload["errors"], [])
        statistics = payload["regions"][0]["statistics"]
        self.assertEqual(statistics["nobs"], len(expected))
        self.assertEqual(statistics["rows_dropped"], 2)
        predictions = pd.DataFrame(payload["regions"][0]["predictions"])
        self.assertAlmostEqual(predictions["Score"].min(), expected["Score"].min())
        self.assertAlmostEqual(predictions["Score"].max(), expected["Score"].max())
        np.testing.assert_allclose(predictions["Age"], expected["Age"].mean())

    def test_ambiguous_value_identical_mat_columns_require_explicit_design_mapping(self):
        from vlad.reporting.io import ReportError, ReportRequest, load_report

        values = self.mixed_fixture()
        path = self.results / f"{self.matrix}.csv"
        design = pd.read_csv(path, sep="\t")
        design["ScoreAlias"] = design["Score"]
        self.write_design(design)
        self.write_region(values)
        self.write_contrasts([[0, 1]])
        rows = design[["Intercept", "Score"]].to_numpy()
        matrix_text = f"/NumWaves 2\n/NumPoints {len(rows)}\n/Matrix\n"
        matrix_text += "\n".join(" ".join(repr(float(value)) for value in row) for row in rows) + "\n"
        (self.results / f"{self.matrix}.mat").write_text(matrix_text)
        request = ReportRequest("swe", self.results, self.analysis, self.matrix)
        with self.assertRaisesRegex(ReportError, "Ambiguous.*design-columns"):
            load_report(request)
        mapped = load_report(ReportRequest(
            "swe", self.results, self.analysis, self.matrix,
            design_columns=["Intercept", "ScoreAlias"],
        ))
        self.assertEqual(mapped.contrast_terms, ["Intercept", "ScoreAlias"])
        np.testing.assert_allclose(mapped.design["ScoreAlias"], design["Score"])


class BashReportingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad shell reporting test ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.global_dir = self.work / "cohort directory"
        (self.global_dir / "Results" / "Smoke").mkdir(parents=True)
        self.home = self.work / "installed checkout"
        runner = self.home / "src" / "vlad" / "reporting" / "run.py"
        runner.parent.mkdir(parents=True)
        runner.write_text(
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['VLAD_TEST_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
            "if os.environ.get('VLAD_TEST_FAIL'): sys.exit(37)\n"
            "output = pathlib.Path(sys.argv[sys.argv.index('--output')+1])\n"
            "output.write_text('<html>synthetic report</html>')\n"
        )
        self.capture = self.work / "captured arguments.json"
        text = (ROOT / "workflows" / "VLAD_beta7.sh").read_text()
        start = text.index("rsumup() {\n")
        end = text.index("\n###############################################################", start)
        self.function = text[start:end]

    def invoke(self, kind="randomise", confirmation=False, failure=False):
        env = os.environ.copy()
        env["VLAD_TEST_CAPTURE"] = str(self.capture)
        if failure:
            env["VLAD_TEST_FAIL"] = "1"
        code = self.function + '''
GLOBAL_DIR="$1"
VLAD_HOME="$2"
VLAD_PYTHON_BIN="$3"
NAME=Smoke
MATRIX=Model
type_images=mrsi
NBPERMUT=500
BATCH_WORKER=true
CONFIRMATION="$4"
LONGITUDINAL="$5"
LONGITUDINAL_TOOL=swe
DIR_RANALYSES="$6"
VLAD_SUBJECT_COLUMN="Participant ID"
VLAD_VISIT_COLUMN="Visit ID"
rsumup || exit $?
'''
        return subprocess.run(
            ["bash", "-c", code, "test-rsumup", str(self.global_dir), str(self.home),
             sys.executable, str(confirmation).lower(), str(kind == "swe").lower(),
             str(self.work / "report exports")],
            cwd=self.work, env=env, text=True, capture_output=True, timeout=15, check=False,
        )

    def test_workflow_forwards_arguments_and_preserves_html_copy_names(self):
        for kind, confirmation in (("randomise", False), ("swe", True)):
            with self.subTest(kind=kind):
                completed = self.invoke(kind, confirmation)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                args = json.loads(self.capture.read_text())
                self.assertEqual(args[args.index("--kind") + 1], kind)
                self.assertEqual(args[args.index("--results-dir") + 1], str(self.global_dir / "Results" / "Smoke"))
                self.assertEqual(args[args.index("--subject-column") + 1], "Participant ID")
                self.assertEqual(args[args.index("--visit-column") + 1], "Visit ID")
                suffix = "mrsi" + ("-swe" if kind == "swe" else "") + ("-confirmation" if confirmation else "")
                report = self.global_dir / "Results" / "Smoke" / f"Smoke_Model_{suffix}.html"
                self.assertEqual(report.read_text(), report.parent.parent.joinpath(report.name).read_text())
                self.assertEqual("--confirmation" in args, confirmation)

    def test_reporting_failure_is_returned_without_copying_html(self):
        completed = self.invoke(failure=True)
        self.assertEqual(completed.returncode, 37, completed.stdout + completed.stderr)
        self.assertFalse((self.global_dir / "Results" / "Smoke_Model_mrsi.html").exists())


if __name__ == "__main__":
    unittest.main()
