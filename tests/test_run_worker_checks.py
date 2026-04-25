from __future__ import annotations

import subprocess

import scripts.run_worker_checks as worker_checks


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_worker_checks_executes_unit_and_e2e(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(worker_checks, "_python_executable", lambda: "python3")

    def fake_run(command: list[str]):  # noqa: ANN001
        commands.append(command)
        return _completed(0, stdout="ok")

    monkeypatch.setattr(worker_checks, "_run_command", fake_run)
    report = worker_checks.run_worker_checks(skip_unit=False, skip_e2e=False, strict=True)
    assert report["ok"] is True
    assert commands[0][0:3] == ["python3", "-m", "pytest"]
    assert commands[1][0:3] == ["python3", "-m", "pytest"]


def test_run_worker_checks_reports_failure(monkeypatch) -> None:
    monkeypatch.setattr(worker_checks, "_python_executable", lambda: "python3")

    def fake_run(command: list[str]):  # noqa: ANN001
        if "tests/worker" in " ".join(command):
            return _completed(1, stdout="failed")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(worker_checks, "_run_command", fake_run)
    report = worker_checks.run_worker_checks(skip_unit=False, skip_e2e=True, strict=True)
    assert report["ok"] is False
    assert report["checks"][0]["name"] == "worker-unit-tests"
