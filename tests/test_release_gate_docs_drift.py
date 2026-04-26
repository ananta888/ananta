from __future__ import annotations

import subprocess
import sys

import scripts.run_release_gate as run_release_gate


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout="", stderr="")


def test_run_release_gate_skips_docs_drift_by_default(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_release_gate.py", "--skip-e2e", "--skip-security-invariants", "--skip-domain-audit"],
    )

    assert run_release_gate.main() == 0
    assert all("tests/test_cli_docs_contract.py" not in command for command in commands)


def test_run_release_gate_report_mode_runs_docs_drift_without_blocking(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        if command[0:3] == ["python3", "-m", "pytest"] and "tests/test_cli_docs_contract.py" in command:
            return _completed(1)
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_release_gate.py",
            "--skip-e2e",
            "--skip-security-invariants",
            "--skip-domain-audit",
            "--docs-drift-check",
            "report",
        ],
    )

    assert run_release_gate.main() == 0
    assert any(command[0:3] == ["python3", "-m", "pytest"] for command in commands)


def test_run_release_gate_strict_mode_blocks_on_docs_drift_failure(monkeypatch) -> None:
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        if command[0:3] == ["python3", "-m", "pytest"] and "tests/test_cli_docs_contract.py" in command:
            return _completed(1)
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_release_gate.py",
            "--skip-e2e",
            "--skip-security-invariants",
            "--skip-domain-audit",
            "--docs-drift-check",
            "strict",
        ],
    )

    assert run_release_gate.main() == 1


def test_run_release_gate_uses_custom_docs_drift_targets(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_release_gate.py",
            "--skip-e2e",
            "--skip-security-invariants",
            "--skip-domain-audit",
            "--docs-drift-check",
            "strict",
            "--docs-drift-test",
            "tests/test_cli_docs_contract.py",
        ],
    )

    assert run_release_gate.main() == 0
    docs_command = next(command for command in commands if command[0:3] == ["python3", "-m", "pytest"])
    assert docs_command == ["python3", "-m", "pytest", "-q", "tests/test_cli_docs_contract.py"]
