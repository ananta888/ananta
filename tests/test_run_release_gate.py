from __future__ import annotations

import subprocess
import sys

import scripts.run_release_gate as run_release_gate


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout="", stderr="")


def test_run_release_gate_runs_release_gate_then_e2e(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--strict"])

    assert run_release_gate.main() == 0
    assert commands[0] == ["python3", "scripts/release_gate.py", "--strict"]
    assert commands[1][0:2] == ["python3", "scripts/run_e2e_dogfood_checks.py"]


def test_run_release_gate_stops_when_release_gate_fails(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        if any(str(part).endswith("release_gate.py") for part in command):
            return _completed(1)
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py"])

    assert run_release_gate.main() == 1
    assert len(commands) == 1
