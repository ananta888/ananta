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
        self, *, timeout=False, remaining_container=False, report_present=True, logs_timeout=False, arguments=(),
        worker_exit_code=73, hub_exit_code=0, missing_loss_container=False, remaining_volume=False,
        source_changed=False
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
            if command[:5] == ['docker', 'image', 'inspect', '--format', '{{.Id}}']:
                return subprocess.CompletedProcess(command, 0, stdout='sha256:' + 'a' * 64, stderr='')
            if logs_timeout and "logs" in command:
                raise subprocess.TimeoutExpired("compose logs", 10)
            if "--quiet" in command and "--all" in command:
                return subprocess.CompletedProcess(
                    command, 0, stdout="" if missing_loss_container else command[-1] + "-isolated", stderr="")
            if command[:2] == ["docker", "inspect"]:
                code = worker_exit_code if command[-1].startswith("autopilot-worker") else hub_exit_code
                return subprocess.CompletedProcess(command, 0,
                    stdout=json.dumps({"Status": "exited", "ExitCode": code}), stderr="")
            output = "leftover\n" if remaining_container and command[:3] == ["docker", "ps", "-aq"] else ""
            if remaining_volume and command[:3] == ["docker", "volume", "ls"]:
                output = "leftover-volume\n"
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with tempfile.TemporaryDirectory(prefix="bpmn-runner-test-") as directory:
            report = Path(directory) / "report.json"
            with (
                patch.object(runner.subprocess, "run", side_effect=docker),
                patch.object(runner.subprocess, "Popen", return_value=process) as popen,
                patch.object(runner, 'repository_identity', side_effect=[('1' * 40, '2' * 64),
                    ('1' * 40, ('3' if source_changed else '2') * 64)]),
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

    def test_source_change_cannot_yield_a_successful_full_application_report(self):
        code, result, _calls, _popen, _process = self.run_runner(arguments=('--full-app-autopilot',),
                                                             source_changed=True)
        self.assertEqual(code, 1)
        self.assertTrue(result['cleanup_complete'])
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
        self.assertEqual(environment['BPMN_BROWSER_IMAGE'], 'sha256:' + 'a' * 64)
        self.assertEqual(environment['BPMN_BROWSER_IMAGE_ID'], environment['BPMN_BROWSER_IMAGE'])

    def test_browser_source_change_cannot_be_reported_as_success(self):
        code, result, *_ = self.run_runner(arguments=('--browser',), source_changed=True)
        self.assertEqual(code, 1)
        self.assertTrue(result['cleanup_complete'])

    def test_selected_cases_are_explicitly_partial_and_do_not_enable_browser(self):
        code, result, _calls, popen, _process = self.run_runner(arguments=("--case", "xor_true"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(popen.call_args.kwargs["env"]["BPMN_CASES"]), ["xor_true"])
        self.assertTrue(result["partial_case_selection"])
        self.assertFalse(result["browser_requested"])
        self.assertNotIn("--profile", popen.call_args.args[0])

    def test_full_app_targets_only_the_isolated_bootstrap_service(self):
        code, result, _calls, popen, _process = self.run_runner(arguments=("--full-app",))
        self.assertEqual(code, 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--profile") + 1], "full-app")
        self.assertEqual(command[command.index("--exit-code-from") + 1:], ["fullapp", "fullapp"])
        self.assertTrue(result["full_application_requested"])
        self.assertFalse(result["browser_requested"])

    def test_full_app_cannot_succeed_without_its_acceptance_report(self):
        code, result, _calls, _popen, _process = self.run_runner(arguments=("--full-app",), report_present=False)
        self.assertNotEqual(code, 0)
        self.assertTrue(result["cleanup_complete"])

    def test_full_app_autopilot_targets_its_own_profile_and_reports_scope(self):
        code, result, _calls, popen, _process = self.run_runner(arguments=("--full-app-autopilot",))
        self.assertEqual(code, 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--profile") + 1], "full-app-autopilot")
        self.assertEqual(command[command.index("--exit-code-from") + 1:],
                         ["fullapp-autopilot", "fullapp-autopilot"])
        self.assertTrue(result["full_application_requested"])
        self.assertTrue(result["autopilot_requested"])
        self.assertFalse(result["browser_requested"])

    def test_full_app_autopilot_missing_report_still_fails_with_cleanup(self):
        code, result, *_ = self.run_runner(arguments=("--full-app-autopilot",), report_present=False)
        self.assertEqual(code, 1)
        self.assertTrue(result["cleanup_complete"])

    def test_postgres_is_cached_and_ready_before_application_with_exact_owned_url(self):
        code, result, calls, popen, _ = self.run_runner(arguments=('--full-app-autopilot', '--postgres'))
        self.assertEqual(code, 0)
        env = popen.call_args.kwargs['env']
        self.assertEqual(env['BPMN_POSTGRES_ENABLED'], '1')
        self.assertEqual(env['BPMN_HUB_DATABASE_URL'],
            'postgresql+psycopg2://ananta_fixture:synthetic-postgres-fixture-password@postgres:5432/ananta_bpmn_fixture')
        self.assertEqual(result['hub_database_backend'], 'postgresql')
        startup = next(command for command, _ in calls if command[-1] == 'postgres')
        self.assertIn('--wait', startup)
        self.assertIn('--no-build', startup)
        self.assertEqual(startup[startup.index('--pull') + 1], 'never')
        self.assertIn(['docker', 'image', 'inspect', env['BPMN_POSTGRES_IMAGE']], [c for c, _ in calls])
        self.assertTrue(result['cleanup_complete'])

    def test_postgres_requires_native_mode_and_immutable_image_before_report_creation(self):
        for arguments in (('--postgres',),
                          ('--full-app-autopilot', '--postgres', '--postgres-image', 'postgres:latest')):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / 'report.json'
                with patch.object(runner.sys, 'argv', [str(RUNNER_PATH), '--report', str(report), *arguments]), \
                        contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    runner.main()
                self.assertFalse(report.exists())

    def test_worker_loss_detaches_and_verifies_actual_exit_before_cleanup(self):
        code, result, calls, popen, _ = self.run_runner(arguments=("--full-app-worker-loss",))
        self.assertEqual(code, 0)
        self.assertTrue(result["worker_container_exit_observed"])
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(popen.call_args.kwargs["env"]["BPMN_WORKER_LOSS_ENABLED"], "1")
        self.assertIn("--follow", popen.call_args.args[0])
        self.assertTrue(any("--detach" in command for command, _ in calls))
        inspect_index = next(i for i, (command, _) in enumerate(calls) if command[:2] == ["docker", "inspect"])
        down_index = next(i for i, (command, _) in enumerate(calls) if "down" in command)
        self.assertLess(inspect_index, down_index)

    def test_worker_loss_wrong_exit_or_failed_hub_cannot_pass(self):
        for worker_code, hub_code in ((0, 0), (73, 1)):
            with self.subTest(worker_code=worker_code, hub_code=hub_code):
                code, result, *_ = self.run_runner(arguments=("--full-app-worker-loss",),
                                                   worker_exit_code=worker_code, hub_exit_code=hub_code)
                self.assertEqual(code, 1)
                self.assertTrue(result["cleanup_complete"])

    def test_worker_loss_missing_report_cannot_pass_from_expected_exit(self):
        code, result, *_ = self.run_runner(arguments=("--full-app-worker-loss",), report_present=False)
        self.assertEqual(code, 1)
        self.assertTrue(result["cleanup_complete"])

    def test_worker_loss_missing_container_identity_fails_even_with_green_report(self):
        code, result, *_ = self.run_runner(arguments=("--full-app-worker-loss",), missing_loss_container=True)
        self.assertEqual(code, 1)
        self.assertFalse(result["worker_container_exit_observed"])
        self.assertTrue(result["cleanup_complete"])

    def test_worker_loss_timeout_terminates_log_client_and_cleans_detached_services(self):
        code, result, calls, _popen, process = self.run_runner(arguments=("--full-app-worker-loss",), timeout=True)
        self.assertEqual(code, 124)
        process.terminate.assert_called_once()
        self.assertTrue(any("down" in command for command, _ in calls))
        self.assertTrue(result["cleanup_complete"])

    def test_recreation_requires_private_request_and_expected_old_exit(self):
        lifecycle = runner.WorkerRecreation(["docker", "compose", "--project-name", "bpmn-container-synthetic"],
                                             {"BPMN_RESTART_TOKEN": "private"}, enabled=True)
        with patch.object(runner.subprocess, "run") as invoke:
            lifecycle.handle("foreign")
            invoke.assert_not_called()
        with patch.object(lifecycle, "observe", return_value=("old", {"Status": "exited", "ExitCode": 0})), \
                patch.object(runner.subprocess, "run") as invoke:
            lifecycle.handle("private")
            invoke.assert_not_called()
        self.assertFalse(lifecycle.recreated)

    def test_recreation_changes_only_worker_and_requires_new_container_identity(self):
        lifecycle = runner.WorkerRecreation(["docker", "compose", "--project-name", "bpmn-container-synthetic"],
                                             {"BPMN_RESTART_TOKEN": "private"}, enabled=True)
        states = [("old", {"Status": "exited", "ExitCode": 73}), ("new", {"Status": "running"})]
        with patch.object(lifecycle, "observe", side_effect=states), patch.object(runner.subprocess, "run") as invoke:
            lifecycle.handle("private")
            self.assertEqual(invoke.call_args.args[0][-1], "autopilot-worker")
            self.assertIn("--no-deps", invoke.call_args.args[0])
            self.assertIn("--force-recreate", invoke.call_args.args[0])
            lifecycle.handle("private")
            invoke.assert_called_once()
        self.assertTrue(lifecycle.exit_observed and lifecycle.recreated)

    def test_leftover_worker_volume_fails_green_acceptance(self):
        code, result, *_ = self.run_runner(remaining_volume=True)
        self.assertEqual(code, 1)
        self.assertFalse(result["cleanup_complete"])

    def test_full_app_rejects_mixed_browser_or_selected_case_before_docker(self):
        for arguments in (("--full-app", "--browser"), ("--full-app", "--case", "xor_true"),
                          ("--full-app-autopilot", "--browser"), ("--full-app-autopilot", "--case", "xor_true"),
                          ("--full-app-autopilot", "--full-app"),
                          ("--full-app-worker-loss", "--full-app-autopilot"),
                          ("--full-app-worker-loss", "--browser"),
                          ("--full-app-worker-loss", "--case", "xor_true"),
                          ("--full-app-worker-redelivery", "--full-app-worker-loss"),
                          ("--full-app-worker-redelivery", "--browser"),
                          ("--full-app-expired-retry", "--full-app-worker-redelivery"),
                          ("--full-app-expired-retry", "--full-app-worker-loss"),
                          ("--full-app-expired-retry", "--browser"),
                          ("--full-app-expired-retry", "--case", "xor_true"),
                          ("--full-app-tool-isolation", "--full-app-autopilot"),
                          ("--full-app-tool-isolation", "--full-app-expired-retry"),
                          ("--full-app-tool-isolation", "--browser"),
                          ("--full-app-tool-isolation", "--case", "xor_true")):
            with self.subTest(arguments=arguments):
                with self.assertRaises(SystemExit):
                    self.run_runner(arguments=arguments)


if __name__ == "__main__":
    unittest.main()
