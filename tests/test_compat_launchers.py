"""Compatibility launchers stay relocatable without importing scientific code."""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
HELPERS = (
    "create_matrix_randoMRSI_beta6.py",
    "create_matrix_randoMRSI_beta7.py",
    "create_matrix_VLADlov_swe_alpha2.py",
    "create_matrix_VLADlov_afni_alpha1.py",
    "resample_masks_b3_replace.py",
    "snapshot_results_randomise_beta5.py",
)


@contextmanager
def working_directory(path):
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


class CompatibilityPythonLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad compatibility test ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.checkout = self.work / "checkout with spaces and 'quotes'"
        self.compat = self.checkout / "scripts" / "compat"
        self.compat.mkdir(parents=True)
        self.caller = self.work / "unrelated caller directory"
        self.caller.mkdir()

    def copy_wrapper(self, name):
        destination = self.compat / name
        shutil.copy2(ROOT / "scripts" / "compat" / name, destination)
        return destination

    def test_helpers_resolve_package_source_and_preserve_arguments_and_cwd(self):
        arguments = ["path with spaces", "literal*glob", "quote'argument"]
        for name in HELPERS:
            with self.subTest(helper=name):
                wrapper = self.copy_wrapper(name)
                observations = {}

                def observe_target(*args, **kwargs):
                    observations.update(argv=list(sys.argv), cwd=Path.cwd())

                with (
                    working_directory(self.caller),
                    patch.object(sys, "argv", [str(wrapper), *arguments]),
                    patch("runpy.run_path", side_effect=observe_target) as launch,
                ):
                    exec(compile(wrapper.read_text(), str(wrapper), "exec"),
                         {"__file__": str(wrapper), "__name__": "__main__"})
                    self.assertEqual(Path.cwd(), self.caller)
                    self.assertEqual(sys.argv, [str(wrapper), *arguments])

                launch.assert_called_once_with(
                    str(self.checkout / "src" / "vlad" / "legacy" / name),
                    run_name="__main__",
                )
                self.assertEqual(observations["cwd"], self.caller)
                self.assertEqual(observations["argv"], [str(wrapper), *arguments])

    def test_report_wrapper_routes_to_cli_without_changing_caller_paths(self):
        wrapper = self.copy_wrapper("create_report.py")
        arguments = ["--results-dir", "relative results", "--analysis-name", "A study"]
        observations = {}

        def observe_main():
            observations.update(argv=list(sys.argv), cwd=Path.cwd(), source_path=sys.path[0])
            return 23

        package = ModuleType("vlad")
        package.__path__ = []
        reporting = ModuleType("vlad.reporting")
        reporting.__path__ = []
        cli = ModuleType("vlad.reporting.cli")
        cli.main = Mock(side_effect=observe_main)
        with (
            working_directory(self.caller),
            patch.object(sys, "path", list(sys.path)),
            patch.object(sys, "argv", [str(wrapper), *arguments]),
            patch.dict(sys.modules, {"vlad": package, "vlad.reporting": reporting,
                                     "vlad.reporting.cli": cli}),
            self.assertRaises(SystemExit) as stopped,
        ):
            exec(compile(wrapper.read_text(), str(wrapper), "exec"),
                 {"__file__": str(wrapper), "__name__": "__main__"})
        self.assertEqual(stopped.exception.code, 23)
        cli.main.assert_called_once_with()
        self.assertEqual(observations["source_path"], str(self.checkout / "src"))
        self.assertEqual(observations["cwd"], self.caller)
        self.assertEqual(observations["argv"], [str(wrapper), *arguments])


class LegacyConfiguredHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad helper configuration test ")
        self.addCleanup(self.temp.cleanup)
        self.checkout = Path(self.temp.name) / "checkout with 'quoted' spaces"
        self.fallback = self.checkout / "src" / "vlad" / "legacy" / HELPERS[0]
        self.fallback.parent.mkdir(parents=True)
        self.fallback.write_text("# canonical helper\n")
        self.caller = Path(self.temp.name) / "external caller"
        self.caller.mkdir()
        self.env = os.environ.copy()
        for name in list(self.env):
            if name.startswith(("VLAD_", "FSL")):
                self.env.pop(name)
        self.env.update(VLAD_HOME=str(self.checkout), FSLDIR="",
                        PYTHONDONTWRITEBYTECODE="1")

    def resolve(self, configured):
        completed = subprocess.run(
            ["bash", "-c", 'source "$1" || exit; vlad_resolve_helper_path "$2" "$3"',
             "legacy-helper-path", str(ROOT / "workflows" / "common.sh"),
             str(configured), str(self.fallback)],
            cwd=self.caller, env=self.env, capture_output=True, text=True,
            timeout=15, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout.rstrip("\n")

    def test_unset_or_missing_former_root_path_uses_canonical_helper(self):
        for configured in ("", str(self.checkout / self.fallback.name)):
            with self.subTest(configured=configured):
                self.assertEqual(self.resolve(configured), str(self.fallback))

    def test_existing_root_override_is_preserved(self):
        configured = self.checkout / self.fallback.name
        configured.write_text("# custom root helper\n")
        self.assertEqual(self.resolve(configured), str(configured))
        self.assertEqual(configured.read_text(), "# custom root helper\n")

    def test_custom_paths_are_preserved_even_when_missing(self):
        for configured in (self.caller / self.fallback.name,
                           self.checkout / "a different custom helper.py"):
            with self.subTest(configured=configured):
                self.assertEqual(self.resolve(configured), str(configured))
                self.assertFalse(configured.exists())


if __name__ == "__main__":
    unittest.main()
