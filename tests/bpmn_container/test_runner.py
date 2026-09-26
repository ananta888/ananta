"""Host-runner safety tests only; these doubles never replace the container chain."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts/test_bpmn_container.py"
SPEC = importlib.util.spec_from_file_location("bpmn_container_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RunnerSafetyTests(unittest.TestCase):
    def run_runner(
        self, *, timeout=False, remaining_container=False, report_present=True, logs_timeout=False, arguments=()
    ):
        process = Mock()
        process.stdout = io.StringIO(
            'hub-1 | BPMN_CONTAINER_REPORT={"passed":true,"cases":{"test":{"passed":true}}}\n' if report_present else ""
        )
        process.poll.return_value = None if timeout else 0
        process.wait.side_effect = [subprocess.TimeoutExpired("compose", 30), 0] if timeout else [0]
        calls = []

        def docker(command, **kwargs):
            calls.append((command, kwargs))
            if logs_timeout and "logs" in command:
                raise subprocess.TimeoutExpired("compose logs", 10)
            output = "leftover\n" if remaining_container and command[:3] == ["docker", "ps", "-aq"] else ""
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with tempfile.TemporaryDirectory(prefix="bpmn-runner-test-") as directory:
            report = Path(directory) / "report.json"
            with (
                patch.object(runner.subprocess, "run", side_effect=docker),
                patch.object(runner.subprocess, "Popen", return_value=process) as popen,
                patch.object(
                    runner.sys, "argv", [str(RUNNER_PATH), "--timeout", "30", "--report", str(report), *arguments]
                ),
                patch.dict(runner.os.environ, {"COMPOSE_PROJECT_NAME": "production", "DATABASE_URL": "real-secret"}),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = runner.main()
            result = json.loads(report.read_text())
        return code, result, calls, popen, process

    def test_unique_project_drops_host_configuration_and_cleans_exact_project(self):
        code, result, calls, popen, _process = self.run_runner()
        self.assertEqual(code, 0)
        args, kwargs = popen.call_args
        command = args[0]
        self.assertEqual(command[command.index("--env-file") + 1], "/dev/null")
        project = command[command.index("--project-name") + 1]
        self.assertTrue(project.startswith("bpmn-container-"))
        self.assertNotIn("COMPOSE_PROJECT_NAME", kwargs["env"])
        self.assertNotIn("DATABASE_URL", kwargs["env"])
        self.assertNotEqual(kwargs["env"]["BPMN_WORKER_TOKEN"], kwargs["env"]["BPMN_DISPATCH_TOKEN"])
        down = next(command for command, _kwargs in calls if "down" in command)
        self.assertEqual(down[down.index("--project-name") + 1], project)
        self.assertTrue(result["cleanup_complete"])

    def test_timeout_terminates_client_and_still_cleans_containers(self):
        code, result, calls, _popen, process = self.run_runner(timeout=True)
        self.assertEqual(code, 124)
        process.terminate.assert_called_once()
        self.assertTrue(any("down" in command for command, _kwargs in calls))
        self.assertTrue(result["cleanup_complete"])

    def test_leftover_container_fails_even_with_green_acceptance(self):
        code, result, *_ = self.run_runner(remaining_container=True)
        self.assertEqual(code, 1)
        self.assertEqual(result["exit_code"], 1)
        self.assertFalse(result["cleanup_complete"])

    def test_missing_acceptance_report_cannot_pass_from_zero_exit_alone(self):
        code, result, *_ = self.run_runner(report_present=False)
        self.assertEqual(code, 1)
        self.assertEqual(result["acceptance"], {})

    def test_diagnostic_timeout_does_not_skip_cleanup(self):
        code, result, calls, *_ = self.run_runner(report_present=False, logs_timeout=True)
        self.assertEqual(code, 1)
        self.assertTrue(any("down" in command for command, _kwargs in calls))
        self.assertTrue(result["cleanup_complete"])

    def test_browser_uses_opt_in_profile_cached_image_and_distinct_bootstrap_token(self):
        code, result, calls, popen, _process = self.run_runner(arguments=("--browser",))
        self.assertEqual(code, 0)
        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(command[command.index("--profile") + 1], "browser")
        self.assertEqual(environment["BPMN_BROWSER_ENABLED"], "1")
        self.assertNotIn(
            environment["BPMN_BROWSER_TOKEN"], {environment["BPMN_WORKER_TOKEN"], environment["BPMN_DISPATCH_TOKEN"]}
        )
        self.assertTrue(
            any(command == ["docker", "image", "inspect", "ananta-frontend-tests:local"] for command, _ in calls)
        )
        self.assertTrue(result["browser_requested"])

    def test_selected_cases_are_explicitly_partial_and_do_not_enable_browser(self):
        code, result, _calls, popen, _process = self.run_runner(arguments=("--case", "xor_true"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(popen.call_args.kwargs["env"]["BPMN_CASES"]), ["xor_true"])
        self.assertTrue(result["partial_case_selection"])
        self.assertFalse(result["browser_requested"])
        self.assertNotIn("--profile", popen.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
