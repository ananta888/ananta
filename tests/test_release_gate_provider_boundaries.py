from __future__ import annotations

import subprocess
import sys

import scripts.run_release_gate as run_release_gate


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout="", stderr="")


def test_run_release_gate_report_mode_runs_provider_boundary_check_without_blocking(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        if command[0:2] == ["python3", "scripts/check_core_provider_boundaries.py"]:
            return _completed(0)
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
            "--provider-boundary-check",
            "report",
        ],
    )

    assert run_release_gate.main() == 0
    assert any(command[0:2] == ["python3", "scripts/check_core_provider_boundaries.py"] for command in commands)


def test_run_release_gate_strict_mode_blocks_on_provider_boundary_failure(monkeypatch) -> None:
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        if command[0:2] == ["python3", "scripts/check_core_provider_boundaries.py"]:
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
            "--provider-boundary-check",
            "strict",
        ],
    )

    assert run_release_gate.main() == 1
