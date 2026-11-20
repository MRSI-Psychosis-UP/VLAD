"""Verify that dispatch preserves scientific workflow arguments and resources."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, "path", [str(ROOT / "src"), *sys.path]):
    from vlad import cli, paths


class CommandRoutingTests(unittest.TestCase):
    def test_workflow_preserves_arguments_environment_and_exit_status(self):
        root = Path("/virtual checkout with spaces")
        arguments = [
            "-c", "My Cohort", "-q", "*onCrPCr", "--batch-file", "relative queue.txt"
        ]
        overrides = {
            "VLAD_CONFIG_FILE": "relative registry.txt",
            "VLAD_BATCH_FILE": "other queue.txt",
            "VLAD_PYTHON_BIN": "/custom environment/bin/python",
        }
        with (
            patch.dict(os.environ, overrides),
            patch.object(cli, "project_root", return_value=root),
            patch.object(cli.subprocess, "call", return_value=29) as launch,
        ):
            result = cli.main(["run", *arguments])

        self.assertEqual(result, 29)
        self.assertEqual(
            launch.call_args.args[0],
            ["bash", str(root / "workflows" / "VLAD_beta7.sh"), *arguments],
        )
        self.assertNotIn("cwd", launch.call_args.kwargs)
        forwarded = launch.call_args.kwargs["env"]
        for name, value in overrides.items():
            self.assertEqual(forwarded[name], value)
        self.assertEqual(forwarded["VLAD_HOME"], str(root))

    def test_helper_preserves_paths_and_runs_only_selected_module(self):
        arguments = ["--masks", "mask with spaces.nii.gz", "--t1", "reference image.nii.gz"]
        with (
            patch.object(sys, "argv", ["test-runner"]),
            patch.object(cli.runpy, "run_module") as launch,
        ):
            self.assertEqual(cli.main(["resample-masks", *arguments]), 0)
            self.assertEqual(sys.argv, ["resample-masks", *arguments])
            launch.assert_called_once_with(
                "vlad.legacy.resample_masks_b3_replace", run_name="__main__"
            )

    def test_missing_bash_reports_actionable_failure(self):
        with (
            patch.object(cli, "project_root", return_value=ROOT),
            patch.object(cli.subprocess, "call", side_effect=FileNotFoundError),
            patch("sys.stderr") as stderr,
        ):
            self.assertEqual(cli.main(["prep", "--help"]), 1)
            self.assertTrue(stderr.write.called)


class ResourceLocationTests(unittest.TestCase):
    def test_explicit_checkout_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="vlad resource test ") as directory:
            root = Path(directory)
            (root / "workflows").mkdir()
            (root / "workflows" / "common.sh").touch()
            with patch.dict(os.environ, {"VLAD_HOME": str(root)}):
                self.assertEqual(paths.project_root(), root.resolve())

    def test_invalid_explicit_checkout_does_not_silently_use_other_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"VLAD_HOME": directory}):
                with self.assertRaisesRegex(RuntimeError, "VLAD_HOME"):
                    paths.project_root()

    def test_wheel_resources_are_found_outside_source_checkout(self):
        with tempfile.TemporaryDirectory(prefix="vlad wheel test ") as directory:
            package = Path(directory) / "site-packages" / "vlad"
            resources = package / "resources"
            (resources / "workflows").mkdir(parents=True)
            (resources / "workflows" / "common.sh").touch()
            env = {name: value for name, value in os.environ.items() if name != "VLAD_HOME"}
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(paths, "__file__", str(package / "paths.py")),
            ):
                self.assertEqual(paths.project_root(), resources.resolve())


if __name__ == "__main__":
    unittest.main()
