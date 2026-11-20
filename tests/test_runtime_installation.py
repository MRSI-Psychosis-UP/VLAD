"""Installed paths work without activation or changes to a shell profile."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstalledRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vlad installed runtime ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "checkout with spaces and 'quotes'"
        self.caller = self.directory / "unrelated caller"
        self.caller.mkdir()
        (self.root / "workflows").mkdir(parents=True)
        shutil.copy2(ROOT / "vlad", self.root / "vlad")
        shutil.copy2(ROOT / "workflows/common.sh", self.root / "workflows/common.sh")
        self.env = os.environ.copy()
        for name in list(self.env):
            if name.startswith("VLAD_") or name in ("FSLDIR", "FSLOUTPUTTYPE"):
                self.env.pop(name)
        self.env["VLAD_TEST_LOAD_COUNTER"] = str(self.directory / "FSL load count")
        self.bash = shutil.which("bash")
        self.assertIsNotNone(self.bash)

    def fake_fsl(self, name="installed FSL with 'quotes'"):
        directory = self.directory / name
        (directory / "etc/fslconf").mkdir(parents=True)
        (directory / "share/fsl/bin").mkdir(parents=True)
        (directory / "bin").mkdir()
        (directory / "etc/fslconf/fsl.sh").write_text(
            'printf "%s\\n" "$FSLDIR" >> "$VLAD_TEST_LOAD_COUNTER"\n'
            'export VLAD_TEST_FSL_SOURCE="$FSLDIR"\n'
            'export FSLOUTPUTTYPE=NIFTI_GZ\n'
        )
        poison = directory / "bin/python3"
        poison.write_text("#!/bin/sh\nexit 96\n")
        poison.chmod(0o755)
        return directory

    def fake_installation(self, fsl=None, chosen_bash=None):
        binary = self.root / ".venv/bin"
        binary.mkdir(parents=True)
        (binary / "python").write_text("#!/bin/sh\nexit 0\n")
        (binary / "python").chmod(0o755)
        (binary / "vlad").write_text(
            f"#!{sys.executable}\n"
            "import json, os, shutil, subprocess, sys\n"
            "from pathlib import Path\n"
            "if os.environ.get('VLAD_TEST_CHILD_WORKFLOW'):\n"
            "    subprocess.run(['bash', os.environ['VLAD_TEST_CHILD_WORKFLOW']], check=True)\n"
            "print(json.dumps({'args':sys.argv[1:], 'cwd':os.getcwd(), 'path':os.environ['PATH'], "
            "'fsl':os.environ.get('FSLDIR'), 'fsl_source':os.environ.get('VLAD_TEST_FSL_SOURCE'), "
            "'output_type':os.environ.get('FSLOUTPUTTYPE'), 'python':os.environ.get('VLAD_PYTHON_BIN'), "
            "'pyenv':os.environ.get('VLAD_PYTHON_ENV'), 'python3':shutil.which('python3'), "
            "'python_only':os.environ.get('VLAD_RUNTIME_PYTHON_ONLY'), "
            "'home':os.environ.get('VLAD_HOME')}))\n"
        )
        (binary / "vlad").chmod(0o755)
        state = self.root / ".local/runtime.env"
        state.parent.mkdir(parents=True)
        state.write_text(
            "VLAD_INSTALL_BASH=" + shlex.quote(str(chosen_bash or self.bash)) + "\n"
            + "VLAD_INSTALL_FSLDIR=" + shlex.quote(str(fsl or "")) + "\n"
        )

    def launch(self, *args, env=None):
        return subprocess.run(
            [str(self.root / "vlad"), *args], cwd=self.caller,
            env=self.env if env is None else env, capture_output=True,
            text=True, timeout=15, check=False,
        )

    def payload(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def old_bash(self):
        for candidate in (os.environ.get("VLAD_TEST_BASH3"), "/bin/bash"):
            if not candidate or not Path(candidate).is_file():
                continue
            version = subprocess.run(
                [candidate, "-c", 'printf "%s" "${BASH_VERSINFO[0]}"'],
                capture_output=True, text=True, check=False,
            )
            if version.returncode == 0 and version.stdout == "3":
                return candidate
        self.skipTest("Bash 3 is unavailable; set VLAD_TEST_BASH3 to exercise macOS compatibility")

    def test_uninstalled_help_and_commands_explain_single_command_setup(self):
        help_result = self.launch("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn(str(self.root / "install.sh"), help_result.stdout)
        result = self.launch("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.root / "install.sh"), result.stderr)

    def test_installed_launcher_initialises_fsl_and_preserves_arguments_and_cwd(self):
        fsl = self.fake_fsl()
        self.fake_installation(fsl=fsl)
        arguments = ["doctor", "--profile", "full", "file with spaces", "*onCrPCr", "a'quote"]
        payload = self.payload(self.launch(*arguments))
        self.assertEqual(payload["args"], arguments)
        self.assertEqual(payload["cwd"], str(self.caller))
        self.assertEqual(payload["fsl"], str(fsl))
        self.assertEqual(payload["fsl_source"], str(fsl))
        self.assertEqual(payload["python"], str(self.root / ".venv/bin/python"))
        self.assertEqual(payload["home"], str(self.root))
        self.assertIsNone(payload["python_only"])
        self.assertEqual(payload["path"].split(os.pathsep)[0], str(fsl / "share/fsl/bin"))
        self.assertNotIn(str(fsl / "bin"), payload["path"].split(os.pathsep))
        self.assertNotEqual(payload["python3"], str(fsl / "bin/python3"))
        self.assertEqual(Path(self.env["VLAD_TEST_LOAD_COUNTER"]).read_text().splitlines(), [str(fsl)])

    def test_explicit_fsl_and_python_overrides_take_precedence(self):
        recorded = self.fake_fsl("recorded FSL")
        selected = self.fake_fsl("user selected FSL")
        self.fake_installation(fsl=recorded)
        env = self.env.copy()
        env.update(FSLDIR=str(selected), VLAD_PYTHON_BIN="relative interpreter/python",
                   FSLOUTPUTTYPE="NIFTI2_GZ")
        python = self.caller / "relative interpreter/python"
        python.parent.mkdir()
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        payload = self.payload(self.launch("report", "--help", env=env))
        self.assertEqual(payload["fsl"], str(selected))
        self.assertEqual(payload["fsl_source"], str(selected))
        self.assertEqual(payload["python"], str(python))
        self.assertEqual(payload["output_type"], "NIFTI2_GZ")
        self.assertEqual(Path(env["VLAD_TEST_LOAD_COUNTER"]).read_text().splitlines(), [str(selected)])

    def test_explicit_pyenv_selection_is_not_replaced_by_the_checkout_python(self):
        self.fake_installation()
        env = self.env.copy()
        env["VLAD_PYTHON_ENV"] = "my MRI environment"
        payload = self.payload(self.launch("run", "--help", env=env))
        self.assertEqual(payload["pyenv"], "my MRI environment")
        self.assertFalse(payload["python"])

    def test_recorded_bash_is_used_even_when_another_bash_started_the_launcher(self):
        chosen = self.directory / "selected tools/bash"
        chosen.parent.mkdir()
        marker = self.directory / "selected Bash was used"
        chosen.write_text(
            "#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nexec "
            + shlex.quote(self.bash) + ' "$@"\n'
        )
        chosen.chmod(0o755)
        self.fake_installation(chosen_bash=chosen)
        self.payload(self.launch("doctor"))
        self.assertTrue(marker.exists())

    def test_child_workflow_does_not_source_fsl_again(self):
        fsl = self.fake_fsl()
        self.fake_installation(fsl=fsl)
        child = self.root / "workflows/test-child.sh"
        child.write_text('source "$(dirname "${BASH_SOURCE[0]}")/common.sh"\n')
        env = self.env.copy()
        env["VLAD_TEST_CHILD_WORKFLOW"] = str(child)
        self.payload(self.launch("run", env=env))
        self.assertEqual(Path(env["VLAD_TEST_LOAD_COUNTER"]).read_text().splitlines(), [str(fsl)])

    def test_python_only_launcher_allowance_is_not_exported_to_the_cli_or_children(self):
        self.fake_installation()
        child = self.root / "workflows/test-child.sh"
        child.write_text(
            '[[ -z "${VLAD_RUNTIME_PYTHON_ONLY:-}" ]] || exit 93\n'
            'source "$(dirname "${BASH_SOURCE[0]}")/common.sh"\n'
        )
        env = self.env.copy()
        # Even an inherited export attribute must be cleared before exec.
        env["VLAD_RUNTIME_PYTHON_ONLY"] = "true"
        env["VLAD_TEST_CHILD_WORKFLOW"] = str(child)
        payload = self.payload(self.launch("report", "--help", env=env))
        self.assertIsNone(payload["python_only"])

    def test_missing_configured_bash_fails_with_reinstallation_instruction(self):
        self.fake_installation(chosen_bash=self.directory / "missing Bash")
        result = self.launch("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Re-run", result.stderr)
        self.assertIn(str(self.root / "install.sh"), result.stderr)

    def test_reports_only_works_with_real_bash3_but_analysis_still_requires_bash4(self):
        old_bash = self.old_bash()
        self.fake_installation(chosen_bash=old_bash)
        result = subprocess.run(
            [old_bash, str(self.root / "vlad"), "report", "--help"],
            cwd=self.caller, env=self.env, capture_output=True, text=True, check=False,
        )
        payload = self.payload(result)
        self.assertEqual(payload["args"], ["report", "--help"])
        self.assertIsNone(payload["python_only"])
        workflow = self.root / "workflows/test-analysis.sh"
        workflow.write_text('source "$(dirname "${BASH_SOURCE[0]}")/common.sh"\n')
        env = self.env.copy()
        env["VLAD_TEST_CHILD_WORKFLOW"] = str(workflow)
        result = self.launch("run", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Bash 4", result.stderr)

    def test_legacy_workflow_started_in_real_bash3_reexecutes_the_installed_bash(self):
        old_bash = self.old_bash()
        self.fake_installation(chosen_bash=self.bash)
        workflow = self.root / "workflows/test-analysis.sh"
        workflow.write_text(
            'source "$(dirname "${BASH_SOURCE[0]}")/common.sh"\n'
            'printf "%s\\n" "${BASH_VERSINFO[0]}" "$@"\n'
        )
        arguments = ["path with spaces", "*onCrPCr", "a'quote"]
        result = subprocess.run(
            [old_bash, str(workflow), *arguments], cwd=self.caller,
            env=self.env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(int(result.stdout.splitlines()[0]), 4)
        self.assertEqual(result.stdout.splitlines()[1:], arguments)


if __name__ == "__main__":
    unittest.main()
