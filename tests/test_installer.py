"""Exercise installation without real downloads, sudo, or package changes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vlad installer test ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.checkout = self.work / "checkout with spaces and 'quote'"
        self.checkout.mkdir()
        (self.checkout / "scripts").mkdir()
        for source in (ROOT / "scripts").glob("*.sh"):
            shutil.copy2(source, self.checkout / "scripts" / source.name)
        (self.checkout / "workflows").mkdir()
        shutil.copy2(ROOT / "workflows/common.sh", self.checkout / "workflows/common.sh")
        for name in ("install.sh", "vlad", "pyproject.toml", "uv.lock", ".python-version"):
            shutil.copy2(ROOT / name, self.checkout / name)
        self.caller = self.work / "different launch directory"
        self.caller.mkdir()
        self.tools = self.work / "mock tools"
        self.tools.mkdir()
        self.log = self.work / "actions.jsonl"
        self.marker = self.work / "partial-download-executed"
        self.python = self.tools / "python3.12"
        self._tool("python3.12", "python")
        self._tool("uv", "uv")
        self._tool("curl", "curl")
        for name in ("sudo", "apt-get", "brew", "dnf", "yum"):
            self._tool(name, "forbidden")
        self.env = os.environ.copy()
        for name in list(self.env):
            if name.startswith(("VLAD_", "FSL", "UV_")):
                self.env.pop(name)
        self.env.update(
            PATH=str(self.tools) + os.pathsep + "/usr/bin:/bin",
            VLAD_TEST_INSTALL_ROOT=str(self.checkout),
            VLAD_TEST_LOG=str(self.log),
            VLAD_TEST_MARKER=str(self.marker),
            PYTHONDONTWRITEBYTECODE="1",
        )

    def _tool(self, name, role):
        script = self.tools / name
        script.write_text(
            f"#!{sys.executable}\nROLE = {role!r}\n" + r'''
import json
import os
from pathlib import Path
import shlex
import stat
import sys

args = sys.argv[1:]
root = Path(os.environ["VLAD_TEST_INSTALL_ROOT"])
def record(action, **extra):
    with open(os.environ["VLAD_TEST_LOG"], "a") as handle:
        handle.write(json.dumps({"action": action, "args": args, "cwd": os.getcwd(), **extra}) + "\n")

def executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

def cli():
    record("cli", fsldir=os.environ.get("FSLDIR"), python=os.environ.get("VLAD_PYTHON_BIN"),
           path=os.environ.get("PATH"), output_type=os.environ.get("FSLOUTPUTTYPE"))
    if args and args[0] == "doctor":
        sys.exit(int(os.environ.get("VLAD_TEST_DOCTOR_STATUS", "0")))
    print("mock-vlad-ok")

if ROLE in ("python", "managed-python"):
    if args and args[0].endswith("/fslinstaller.py"):
        record("fsl-install")
        destination = Path(args[args.index("-d") + 1])
        if destination.exists():
            print("Refusing to overwrite a mocked FSL destination", file=sys.stderr)
            sys.exit(96)
        (destination / "etc/fslconf").mkdir(parents=True)
        for name in ("fslmaths", "fslmerge", "fslsplit", "fslstats", "fslval", "fslnvols", "fslmeants", "flirt", "Text2Vest", "randomise", "swe"):
            executable(destination / "share/fsl/bin" / name, "#!/bin/sh\nexit 0\n")
        (destination / "etc/fslconf/fsl.sh").write_text('export PATH="$FSLDIR/share/fsl/bin:$PATH"\nexport FSLOUTPUTTYPE=NIFTI_GZ\n')
    elif len(args) >= 2 and args[:2] == ["-m", "vlad"]:
        args = args[2:]
        cli()
    elif args and args[0] in ("--version", "-V"):
        print("Python 3.12.3")
    elif args and args[0] == "-c":
        code = args[1]
        if "sys.executable" in code and "print" in code:
            print(str(Path(sys.argv[0]).absolute()))
        elif "print" in code and "sys.version_info" in code:
            print("3.12")
    else:
        record("python", invocation=args)
elif ROLE == "unsupported-python":
    sys.exit(1)
elif ROLE == "uv":
    if "--version" in args:
        print("uv 0.13.7")
    elif args[:2] == ["python", "install"]:
        record("managed-python-install")
        here = Path(__file__).read_text()
        executable(root / ".local/python/test-python", here.replace("ROLE = 'uv'", "ROLE = 'managed-python'", 1))
    elif args[:2] == ["python", "find"]:
        print(root / ".local/python/test-python")
    elif "sync" in args:
        record("sync")
        status = int(os.environ.get("VLAD_TEST_SYNC_STATUS", "0"))
        if status:
            sys.exit(status)
        here = Path(__file__).read_text()
        executable(root / ".venv/bin/python", here.replace("ROLE = 'uv'", "ROLE = 'python'", 1))
        executable(root / ".venv/bin/vlad", here.replace("ROLE = 'uv'", "ROLE = 'cli'", 1))
    else:
        record("uv")
elif ROLE == "cli":
    cli()
elif ROLE == "curl":
    if "--version" in args:
        print("curl mock")
        sys.exit(0)
    record("download")
    partial = "#!/bin/sh\ntouch " + shlex.quote(os.environ["VLAD_TEST_MARKER"]) + "\n"
    destination = None
    for option in ("-o", "--output"):
        if option in args:
            destination = Path(args[args.index(option) + 1])
    if os.environ.get("VLAD_TEST_DOWNLOAD_SUCCESS") == "1" and any("fslinstaller.py" in value for value in args):
        if destination:
            destination.write_text("# Mock FSL installer; interpreted only by the test Python shim.\n")
        else:
            print("# Mock FSL installer")
        sys.exit(0)
    if destination:
        destination.write_text(partial)
    else:
        print(partial)
    sys.exit(23)
elif ROLE == "forbidden":
    record("forbidden", command=Path(sys.argv[0]).name)
    print("A test must never call a real system package manager.", file=sys.stderr)
    sys.exit(99)
elif ROLE == "dpkg":
    if args[-1] == os.environ.get("VLAD_TEST_MISSING_PACKAGE"):
        sys.exit(1)
    print("install ok installed", end="")
elif ROLE == "apt":
    record("apt")
    sys.exit(int(os.environ.get("VLAD_TEST_APT_STATUS", "0")))
elif ROLE == "admin":
    import subprocess
    record("admin")
    if not args or args[0] not in ("apt-get", "env"):
        sys.exit(99)
    sys.exit(subprocess.call(args))
'''
        )
        script.chmod(0o755)

    def actions(self, name=None):
        values = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [value for value in values if name is None or value["action"] == name]

    def install(self, *arguments, env=None):
        result = subprocess.run(
            ["bash", str(self.checkout / "install.sh"), *arguments],
            cwd=self.caller, env=self.env if env is None else env,
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertFalse(self.actions("forbidden"), result.stdout + result.stderr)
        self.assertFalse(self.marker.exists(), "Installer executed a failed partial download")
        return result

    def reports(self, *arguments, env=None):
        return self.install("--reports-only", "--skip-system", "--python", str(self.python), *arguments, env=env)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def fsl_fixture(self):
        fsldir = self.work / "existing FSL with 'quoted' spaces"
        (fsldir / "share/fsl/bin").mkdir(parents=True)
        (fsldir / "etc/fslconf").mkdir(parents=True)
        for name in ("fslmaths", "fslmerge", "fslsplit", "fslstats", "fslval", "fslnvols", "fslmeants", "flirt", "Text2Vest", "randomise", "randomise_parallel", "swe"):
            command = fsldir / "share/fsl/bin" / name
            command.write_text("#!/bin/sh\nexit 0\n")
            command.chmod(0o755)
        (fsldir / "etc/fslconf/fsl.sh").write_text('export PATH="$FSLDIR/share/fsl/bin:$PATH"\nexport FSLOUTPUTTYPE=NIFTI_GZ\n')
        (fsldir / "etc/fslversion").write_text("6.0.mock\n")
        return fsldir

    def test_help_and_invalid_options_do_not_install_or_download(self):
        result = self.install("--help")
        self.assert_ok(result)
        self.assertIn("--reports-only", result.stdout)
        self.assertFalse(self.actions())
        result = self.install("--unknown-option")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.actions())

    def test_reports_only_installs_locked_environment_without_activation(self):
        self.assert_ok(self.reports())
        sync = self.actions("sync")
        self.assertEqual(len(sync), 1)
        self.assertIn("--locked", sync[0]["args"])
        self.assertIn("--no-dev", sync[0]["args"])
        arguments = sync[0]["args"]
        self.assertEqual(arguments[arguments.index("--python") + 1], str(self.python))
        self.assertTrue((self.checkout / ".local/runtime.env").is_file())
        doctor = self.actions("cli")
        self.assertTrue(any(value["args"] == ["doctor", "--profile", "reports"] for value in doctor), doctor)
        self.assertFalse(self.actions("download"))

    def test_launcher_preserves_spaces_globs_and_caller_working_directory(self):
        self.assert_ok(self.reports())
        arguments = ["report", "--analysis-name", "Study with spaces", "--matrix-name", "Group 'quoted'", "--literal", "*.nii.gz"]
        result = subprocess.run([str(self.checkout / "vlad"), *arguments], cwd=self.caller,
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assert_ok(result)
        invocation = self.actions("cli")[-1]
        self.assertEqual(invocation["args"], arguments)
        self.assertEqual(invocation["cwd"], str(self.caller))

    def test_relative_python_is_resolved_before_installer_changes_directory(self):
        relative = self.caller / "local tools" / "python"
        relative.parent.mkdir()
        shutil.copy2(self.python, relative)
        self.assert_ok(self.install("--reports-only", "--skip-system", "--python", "local tools/python"))
        arguments = self.actions("sync")[-1]["args"]
        self.assertEqual(arguments[arguments.index("--python") + 1], str(relative))

    def test_reuses_existing_fsl_and_launcher_initialises_it(self):
        fsldir = self.fsl_fixture()
        self.assert_ok(self.install("--skip-system", "--skip-fsl", "--python", str(self.python), "--fsl-dir", str(fsldir)))
        self.assertFalse(self.actions("download"))
        doctor = self.actions("cli")
        self.assertTrue(any(value["args"] == ["doctor", "--profile", "full"] for value in doctor), doctor)
        result = subprocess.run([str(self.checkout / "vlad"), "run", "--help"], cwd=self.caller,
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assert_ok(result)
        self.assertEqual(self.actions("cli")[-1]["fsldir"], str(fsldir))
        self.assertIn(str(fsldir / "share/fsl/bin"), self.actions("cli")[-1]["path"])

    def test_new_fsl_install_uses_noninteractive_flags_and_new_destination(self):
        # Upstream FSL prohibits whitespace in new installation paths.
        target = tempfile.TemporaryDirectory(prefix="vlad-fsl-test-")
        self.addCleanup(target.cleanup)
        fsldir = Path(target.name) / "new-fsl"
        env = self.env.copy()
        env["VLAD_TEST_DOWNLOAD_SUCCESS"] = "1"
        result = self.install("--skip-system", "--python", str(self.python),
                              "--fsl-dir", str(fsldir), env=env)
        self.assert_ok(result)
        installs = self.actions("fsl-install")
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0]["args"][1:], ["-d", str(fsldir), "-n", "-r", "-c", "none"])
        self.assertTrue(any(value.startswith("https://fsl.fmrib.ox.ac.uk/") and value.endswith("fslinstaller.py")
                            for value in self.actions("download")[0]["args"]))
        self.assertTrue((fsldir / "share/fsl/bin/swe").is_file())
        self.assertTrue(any(value["args"] == ["doctor", "--profile", "full"] for value in self.actions("cli")))

    def test_existing_fsl_without_swe_fails_without_download_or_replacement(self):
        fsldir = self.fsl_fixture()
        (fsldir / "share/fsl/bin/swe").unlink()
        sentinel = fsldir / "existing user settings"
        sentinel.write_text("preserve\n")
        result = self.install("--skip-system", "--python", str(self.python),
                              "--fsl-dir", str(fsldir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("swe", result.stdout + result.stderr)
        self.assertEqual(sentinel.read_text(), "preserve\n")
        self.assertFalse(self.actions("download"))
        self.assertFalse(self.actions("fsl-install"))

    def test_rerun_preserves_cohort_registry_and_batch_queue(self):
        registry = self.checkout / "VLAD_configs.txt"
        queue = self.checkout / "VLAD_batch_commands.txt"
        registry.write_text("/private/cohort/config_global.sh\n")
        queue.write_text("# private queued work\n")
        self.assert_ok(self.reports())
        self.assert_ok(self.reports())
        self.assertEqual(registry.read_text(), "/private/cohort/config_global.sh\n")
        self.assertEqual(queue.read_text(), "# private queued work\n")
        self.assertEqual(len(self.actions("sync")), 2)

    def test_partial_fsl_directory_is_not_overwritten_or_reinstalled(self):
        fsldir = self.work / "partial FSL installation"
        fsldir.mkdir()
        sentinel = fsldir / "existing user file.txt"
        sentinel.write_text("preserve incomplete installation for repair\n")
        result = self.install("--skip-system", "--python", str(self.python),
                              "--fsl-dir", str(fsldir))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "preserve incomplete installation for repair\n")
        self.assertFalse(self.actions("download"), "Existing incomplete FSL must not be overlaid by an installer")

    def test_failed_download_never_executes_partial_installer(self):
        (self.tools / "uv").unlink()
        result = self.reports()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.actions("download"))
        self.assertFalse(self.actions("sync"))
        self.assertFalse(self.marker.exists())

    def test_sync_failure_stops_before_verification(self):
        env = self.env.copy()
        env["VLAD_TEST_SYNC_STATUS"] = "17"
        result = self.reports(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.actions("sync"))
        self.assertFalse(self.actions("cli"))

    def test_failed_dependency_check_does_not_claim_success(self):
        env = self.env.copy()
        env["VLAD_TEST_DOCTOR_STATUS"] = "8"
        result = self.reports(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.actions("cli"))
        self.assertNotIn("Installation complete", result.stdout)

    def test_skip_fsl_with_no_existing_installation_cannot_claim_full_setup(self):
        result = self.install("--skip-system", "--skip-fsl", "--python", str(self.python),
                              "--fsl-dir", str(self.work / "missing FSL"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.actions("download"))
        self.assertNotIn("Installation complete", result.stdout)

    def test_no_check_is_explicitly_unverified(self):
        self.assert_ok(self.reports("--no-check"))
        self.assertFalse(self.actions("cli"))
        result = self.reports("--no-check")
        self.assert_ok(result)
        self.assertIn("unverified", result.stdout.lower())

    def test_missing_system_prerequisites_use_apt_without_real_administrator_changes(self):
        self._tool("dpkg-query", "dpkg")
        self._tool("apt-get", "apt")
        self._tool("sudo", "admin")
        env = self.env.copy()
        env["VLAD_TEST_MISSING_PACKAGE"] = "curl"
        self.assert_ok(self.install("--reports-only", "--python", str(self.python), env=env))
        commands = self.actions("apt")
        self.assertEqual(commands[0]["args"], ["update"])
        self.assertIn("install", commands[1]["args"])
        self.assertIn("curl", commands[1]["args"])
        self.assertIn("--no-install-recommends", commands[1]["args"])
        self.assertNotIn("r-base", commands[1]["args"])

    def test_system_package_failure_stops_before_python_environment_install(self):
        self._tool("dpkg-query", "dpkg")
        self._tool("apt-get", "apt")
        self._tool("sudo", "admin")
        env = self.env.copy()
        env.update(VLAD_TEST_MISSING_PACKAGE="curl", VLAD_TEST_APT_STATUS="13")
        result = self.install("--reports-only", "--python", str(self.python), env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.actions("apt"))
        self.assertFalse(self.actions("sync"))

    def test_private_python_is_installed_when_existing_interpreters_are_unsupported(self):
        for name in ("python3.12", "python3.11", "python3.10", "python3"):
            self._tool(name, "unsupported-python")
        self.assert_ok(self.install("--reports-only", "--skip-system"))
        actions = self.actions("managed-python-install")
        self.assertEqual(len(actions), 1)
        self.assertIn("3.12", actions[0]["args"])
        self.assertIn("--no-bin", actions[0]["args"])
        arguments = self.actions("sync")[-1]["args"]
        self.assertEqual(arguments[arguments.index("--python") + 1], str(self.checkout / ".local/python/test-python"))


if __name__ == "__main__":
    unittest.main()
