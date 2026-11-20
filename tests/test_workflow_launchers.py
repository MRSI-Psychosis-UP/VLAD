"""Regression checks for source relocation without MRI data or external tools."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkflowLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad launch test ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.env = os.environ.copy()
        for name in tuple(self.env):
            if name.startswith("VLAD_"):
                self.env.pop(name)
        self.env.update(
            PYTHONPATH=str(ROOT / "src"),
            PYTHONDONTWRITEBYTECODE="1",
            VLAD_CONFIG_FILE=str(self.work / "missing registry.txt"),
            VLAD_BATCH_FILE=str(self.work / "queue with spaces.txt"),
        )

    def run_command(self, command, *arguments, input="", env=None):
        return subprocess.run(
            [*command, *arguments],
            cwd=self.work,
            env=self.env if env is None else env,
            input=input,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )

    def run_commands(self):
        return [
            ["bash", str(ROOT / "scripts" / "compat" / "VLAD_beta7.sh")],
            ["bash", str(ROOT / "workflows" / "VLAD_beta7.sh")],
            [sys.executable, "-m", "vlad", "run"],
        ]

    def prep_commands(self):
        return [
            ["bash", str(ROOT / "scripts" / "compat" / "Prep_VLAD_b6.1.sh")],
            ["bash", str(ROOT / "workflows" / "Prep_VLAD_b6.1.sh")],
            [sys.executable, "-m", "vlad", "prep"],
        ]

    def assert_success(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def check_runtime_paths_after_cwd_change(self, relative):
        interpreter = self.work / ".venv" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("#!/bin/sh\nprintf '%s\\n' selected-test-interpreter\n")
        interpreter.chmod(0o755)
        cohort = self.work / "different cohort directory"
        cohort.mkdir()
        expected = {
            "VLAD_PYTHON_BIN": interpreter,
            "VLAD_CONFIG_FILE": self.work / "config" / "registry file.txt",
            "VLAD_BATCH_FILE": self.work / "local state" / "queue.txt",
        }
        env = self.env.copy()
        env.update(
            {
                name: str(path.relative_to(self.work) if relative else path)
                for name, path in expected.items()
            }
        )
        completed = self.run_command(
            ["bash", "-c"],
            'source "$1"; cd "$2" || exit; '
            'printf "%s\\n" "$VLAD_PYTHON_BIN" "$VLAD_CONFIG_FILE" '
            '"$VLAD_BATCH_FILE"; '
            '"$VLAD_PYTHON_BIN"',
            "runtime-path-test",
            str(ROOT / "workflows" / "common.sh"),
            str(cohort),
            env=env,
        )
        self.assert_success(completed)
        self.assertEqual(
            completed.stdout.splitlines(),
            [str(path) for path in expected.values()] + ["selected-test-interpreter"],
        )

    def test_common_resolves_relative_runtime_paths_before_cohort_cwd_change(self):
        self.check_runtime_paths_after_cwd_change(relative=True)

    def test_common_preserves_absolute_runtime_path_overrides(self):
        self.check_runtime_paths_after_cwd_change(relative=False)

    def test_cli_help_does_not_need_cohort_or_scientific_dependencies(self):
        completed = self.run_command([sys.executable, "-m", "vlad"], "--help")
        self.assert_success(completed)
        self.assertIn("doctor", completed.stdout)
        self.assertIn("prep", completed.stdout)

    def test_analysis_help_from_any_directory_without_registry(self):
        for command in self.run_commands():
            with self.subTest(command=command):
                completed = self.run_command(command, "--help")
                self.assert_success(completed)
                self.assertIn("--cohort", completed.stdout)
                self.assertFalse(Path(self.env["VLAD_CONFIG_FILE"]).exists())

    def test_preparation_help_does_not_enter_interactive_setup(self):
        for command in self.prep_commands():
            with self.subTest(command=command):
                completed = self.run_command(command, "--help")
                self.assert_success(completed)
                self.assertIn("--b0", completed.stdout)
                self.assertFalse(Path(self.env["VLAD_CONFIG_FILE"]).exists())

    def test_missing_registry_reports_selected_path(self):
        for command in self.run_commands():
            with self.subTest(command=command):
                completed = self.run_command(
                    command, "-c", "Missing", "-t", "MRSI", "-n", "Smoke", "-m", "Group"
                )
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn(self.env["VLAD_CONFIG_FILE"], completed.stdout)

    def test_registry_with_comments_and_no_final_newline_selects_cohort(self):
        config = self.work / "cohort config.sh"
        config.write_text("COHORT=Smoke\nprintf '%s\\n' selected-test-cohort\nexit 23\n")
        registry = Path(self.env["VLAD_CONFIG_FILE"])
        registry.write_text("# Local cohorts\n\n" + str(config))
        for command in self.run_commands():
            with self.subTest(command=command):
                completed = self.run_command(command, "-c", "Smoke", "-t", "MRSI")
                self.assertEqual(completed.returncode, 23, completed.stdout)
                self.assertIn("selected-test-cohort", completed.stdout)
                self.assertNotIn("No such file", completed.stdout)

    def test_batch_execution_uses_environment_queue_without_registry(self):
        queue = Path(self.env["VLAD_BATCH_FILE"])
        output = self.work / "output with spaces.txt"
        for command in self.run_commands():
            with self.subTest(command=command):
                queue.write_text(
                    "# Keep queue notes\n\n"
                    f"printf '%s' completed > {shlex.quote(str(output))}\n"
                )
                completed = self.run_command(command, "--batch", "run")
                self.assert_success(completed)
                self.assertEqual(output.read_text(), "completed")
                self.assertEqual(queue.read_text(), "# Keep queue notes\n\n")
                output.unlink()

    def test_batch_preserves_failed_commands_and_returns_failure(self):
        queue = Path(self.env["VLAD_BATCH_FILE"])
        for command in self.run_commands():
            with self.subTest(command=command):
                queue.write_text("# retry failed work\ntrue\n   false   ")
                completed = self.run_command(command, "--batch", "run")
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(queue.read_text(), "# retry failed work\n   false   \n")

    def test_explicit_batch_path_is_relative_to_caller_and_overrides_environment(self):
        queue = self.work / "relative queue.txt"
        default_queue = Path(self.env["VLAD_BATCH_FILE"])
        default_queue.write_text("# leave this queue alone\n")
        for command in self.run_commands():
            with self.subTest(command=command):
                queue.write_text("true\n")
                completed = self.run_command(
                    command, "--batch", "run", "--batch-file", queue.name
                )
                self.assert_success(completed)
                self.assertEqual(queue.read_text(), "")
                self.assertEqual(default_queue.read_text(), "# leave this queue alone\n")

    def test_preparation_registers_existing_cohort_in_selected_registry(self):
        cohort = self.work / "cohort directory"
        cohort.mkdir()
        config = cohort / "config_global.sh"
        config.write_text(
            f"GLOBAL_DIR={shlex.quote(str(cohort))}\nCOHORT=Smoke\n"
        )
        tools = self.work / "mock tools"
        tools.mkdir()
        zenity = tools / "zenity"
        zenity.write_text("#!/bin/sh\nprintf '%s\\n' \"$VLAD_TEST_COHORT\"\n")
        zenity.chmod(0o755)
        env = self.env.copy()
        env.update(
            PATH=str(tools) + os.pathsep + env["PATH"],
            VLAD_TEST_COHORT=str(cohort),
        )
        registry = Path(env["VLAD_CONFIG_FILE"])
        for command in self.prep_commands():
            with self.subTest(command=command):
                registry.unlink(missing_ok=True)
                completed = self.run_command(command, input="continue\nuse\nexit\n", env=env)
                self.assert_success(completed)
                self.assertEqual(registry.read_text(), str(config) + "\n")
                self.assertFalse((self.work / "VLAD_configs.txt").exists())


if __name__ == "__main__":
    unittest.main()
