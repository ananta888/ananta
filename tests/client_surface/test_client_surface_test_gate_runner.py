from __future__ import annotations

import subprocess

import scripts.run_client_surface_test_gate as gate


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_gate_marks_failure_when_blocking_check_fails(monkeypatch) -> None:
    def fake_run(command, **kwargs):  # noqa: ANN001
        joined = " ".join(command)
        if "audit_client_surface_entrypoints.py" in joined:
            return _completed(2, stdout="warning")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    report = gate.run_gate(include_advisory=False)

    assert report["blocking"]["ok"] is False
    assert report["ok"] is False
    assert report["advisory"]["executed"] is False


def test_run_gate_separates_advisory_checks(monkeypatch) -> None:
    def fake_run(command, **kwargs):  # noqa: ANN001
        joined = " ".join(command)
        if "smoke_eclipse_runtime_headless.py" in joined:
            return _completed(2, stdout="docker missing")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    report = gate.run_gate(include_advisory=True)

    assert report["blocking"]["ok"] is True
    assert report["ok"] is True
    assert report["advisory"]["executed"] is True
    assert report["advisory"]["ok"] is False
    assert report["advisory"]["checks"][0]["name"] == "eclipse-headless-smoke"
