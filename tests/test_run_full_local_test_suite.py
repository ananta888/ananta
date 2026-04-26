from __future__ import annotations

import subprocess

import scripts.run_full_local_test_suite as full_suite


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_full_test_suite_default_flow_executes_all_steps(monkeypatch) -> None:
    commands: list[list[str]] = []
    vulkan_flags: list[str] = []

    monkeypatch.setattr(full_suite, "_python_executable", lambda: "python3")

    def fake_run(command: list[str], *, env: dict[str, str]):  # noqa: ANN001
        commands.append(list(command))
        vulkan_flags.append(str(env.get("ANANTA_USE_WSL_VULKAN")))
        return _completed(0, stdout="ok")

    monkeypatch.setattr(full_suite, "_run_command", fake_run)
    report = full_suite.run_full_test_suite()

    assert report["ok"] is True
    assert commands[0] == ["python3", "scripts/check_pipeline.py", "--mode", "deep"]
    assert commands[1] == ["bash", "scripts/compose-test-stack.sh", "up"]
    assert commands[2] == ["bash", "scripts/compose-test-stack.sh", "run-backend-test"]
    assert commands[3] == ["bash", "scripts/compose-test-stack.sh", "run-backend-live-llm-test"]
    assert commands[4] == ["bash", "scripts/compose-test-stack.sh", "run-frontend-test"]
    assert commands[5] == ["bash", "scripts/compose-test-stack.sh", "run-frontend-live-llm-test"]
    assert commands[6] == ["python3", "scripts/run_e2e_dogfood_checks.py", "--out", "artifacts/e2e/dogfood_gate_report.json"]
    assert commands[7] == ["bash", "scripts/start-firefox-vnc.sh", "start"]
    assert commands[8][0:2] == ["python3", "scripts/firefox_live_click_extended.py"]
    assert commands[9] == ["bash", "scripts/start-firefox-vnc.sh", "stop"]
    assert commands[10] == ["bash", "scripts/compose-test-stack.sh", "down"]
    assert set(vulkan_flags) == {"1"}


def test_run_full_test_suite_stops_main_flow_on_failure_and_cleans_up_stack(monkeypatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(full_suite, "_python_executable", lambda: "python3")

    def fake_run(command: list[str], *, env: dict[str, str]):  # noqa: ANN001
        commands.append(list(command))
        if command == ["bash", "scripts/compose-test-stack.sh", "run-backend-live-llm-test"]:
            return _completed(1, stdout="failed")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(full_suite, "_run_command", fake_run)
    report = full_suite.run_full_test_suite(skip_deep_checks=True)

    assert report["ok"] is False
    assert ["bash", "scripts/compose-test-stack.sh", "run-frontend-test"] not in commands
    assert commands[-1] == ["bash", "scripts/compose-test-stack.sh", "down"]


def test_run_full_test_suite_can_reuse_running_stack_and_use_cpu_mode(monkeypatch) -> None:
    commands: list[list[str]] = []
    vulkan_flags: list[str] = []

    monkeypatch.setattr(full_suite, "_python_executable", lambda: "python3")

    def fake_run(command: list[str], *, env: dict[str, str]):  # noqa: ANN001
        commands.append(list(command))
        vulkan_flags.append(str(env.get("ANANTA_USE_WSL_VULKAN")))
        return _completed(0, stdout="ok")

    monkeypatch.setattr(full_suite, "_run_command", fake_run)
    report = full_suite.run_full_test_suite(
        skip_deep_checks=True,
        skip_compose_tests=True,
        skip_dogfood=True,
        skip_live_click=False,
        live_click_mode="dual",
        reuse_running_stack=True,
        keep_stack_running=True,
        use_wsl_vulkan=False,
    )

    assert report["ok"] is True
    assert commands[0] == ["bash", "scripts/start-firefox-vnc.sh", "start"]
    assert commands[1] == ["python3", "scripts/run_live_click_dual_benchmark.py"]
    assert commands[2] == ["bash", "scripts/start-firefox-vnc.sh", "stop"]
    assert ["bash", "scripts/compose-test-stack.sh", "up"] not in commands
    assert ["bash", "scripts/compose-test-stack.sh", "down"] not in commands
    assert set(vulkan_flags) == {"0"}
