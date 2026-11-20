"""Browser launch must preserve runtime selection and bind locally by default."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from vlad import cli


class BrowserLaunchTests(unittest.TestCase):
    def environment(self, **overrides):
        environment = {name: value for name, value in os.environ.items()
                       if name not in ("VLAD_HOME", "VLAD_PYTHON_BIN", "VLAD_PYTHON_ENV", "PYTHONPATH")}
        environment.update(overrides)
        return environment

    def launch(self, arguments, environment=None, status=0):
        root = Path("/study checkout with spaces")
        with (patch.dict(os.environ, environment or self.environment(), clear=True),
              patch.object(cli, "project_root", return_value=root),
              patch.object(cli.subprocess, "call", return_value=status) as launch):
            result = cli.main(["gui", *arguments])
        return result, launch

    def test_default_launch_is_local_and_uses_own_interpreter(self):
        result, launch = self.launch([], status=27)
        self.assertEqual(result, 27)
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments[:4], [sys.executable, "-m", "streamlit", "run"])
        self.assertEqual(arguments[4], str(Path(cli.__file__).resolve().parent / "gui" / "app.py"))
        self.assertEqual(arguments[arguments.index("--server.address") + 1], "127.0.0.1")
        self.assertEqual(arguments[arguments.index("--server.port") + 1], "2342")
        self.assertEqual(arguments[arguments.index("--server.headless") + 1], "false")
        self.assertIn("--browser.gatherUsageStats=false", arguments)
        self.assertNotIn("shell", launch.call_args.kwargs)
        self.assertNotIn("cwd", launch.call_args.kwargs)
        environment = launch.call_args.kwargs["env"]
        self.assertEqual(environment["VLAD_HOME"], "/study checkout with spaces")
        self.assertEqual(environment["VLAD_PYTHON_BIN"], sys.executable)
        self.assertEqual(environment["STREAMLIT_SERVER_SHOW_EMAIL_PROMPT"], "false")

    def test_headless_and_no_browser_are_equivalent(self):
        for flag in ("--headless", "--no-browser"):
            with self.subTest(flag=flag):
                result, launch = self.launch([flag])
                self.assertEqual(result, 0)
                arguments = launch.call_args.args[0]
                self.assertEqual(arguments[arguments.index("--server.headless") + 1], "true")

    def test_custom_bind_and_port_are_forwarded_without_changing_browser_address(self):
        result, launch = self.launch(["--host", "0.0.0.0", "--port", "9876", "--headless"])
        self.assertEqual(result, 0)
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--server.address") + 1], "0.0.0.0")
        self.assertEqual(arguments[arguments.index("--server.port") + 1], "9876")
        self.assertNotIn("--browser.serverAddress", arguments)

    def test_preserves_explicit_runtime_and_python_search_path(self):
        environment = self.environment(
            VLAD_PYTHON_BIN="/custom environment/bin/python",
            PYTHONPATH="/custom Python modules",
            VLAD_CONFIG_FILE="/private study/cohorts.txt",
            FSLDIR="/installed FSL",
        )
        result, launch = self.launch([], environment)
        self.assertEqual(result, 0)
        forwarded = launch.call_args.kwargs["env"]
        for name in ("VLAD_PYTHON_BIN", "VLAD_CONFIG_FILE", "FSLDIR"):
            self.assertEqual(forwarded[name], environment[name])
        self.assertEqual(forwarded["PYTHONPATH"], str(Path(cli.__file__).resolve().parent.parent) + os.pathsep + "/custom Python modules")

    def test_pyenv_override_is_not_replaced_with_current_python(self):
        _, launch = self.launch([], self.environment(VLAD_PYTHON_ENV="study-env"))
        forwarded = launch.call_args.kwargs["env"]
        self.assertEqual(forwarded["VLAD_PYTHON_ENV"], "study-env")
        self.assertNotIn("VLAD_PYTHON_BIN", forwarded)

    def test_invalid_ports_hosts_and_unknown_flags_do_not_start_server(self):
        for arguments in (["--port", "0"], ["--port", "65536"], ["--host", ""],
                          ["--host", "bad host"], ["--unknown"]):
            with self.subTest(arguments=arguments), patch.object(cli.subprocess, "call") as launch, patch("sys.stderr"):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(["gui", *arguments])
                self.assertEqual(raised.exception.code, 2)
                launch.assert_not_called()

    def test_interrupt_returns_conventional_status(self):
        with (patch.object(cli, "project_root", return_value=Path("/checkout")),
              patch.object(cli.subprocess, "call", side_effect=KeyboardInterrupt)):
            self.assertEqual(cli.main(["gui"]), 130)

    def test_missing_resources_fail_without_starting_server(self):
        with (patch.object(cli, "project_root", side_effect=RuntimeError("Missing VLAD resources")),
              patch.object(cli.subprocess, "call") as launch, patch("sys.stderr")):
            self.assertEqual(cli.main(["gui"]), 1)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
