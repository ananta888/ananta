from __future__ import annotations

import json
import subprocess
from pathlib import Path

import scripts.run_tdd_blueprint_smoke as run_tdd_blueprint_smoke


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


def _valid_smoke_report() -> dict:
    return {
        "schema": "tdd_blueprint_smoke_report.v1",
        "ok": True,
        "claims": {"red_phase_claimed": True, "green_phase_claimed": True},
        "phases": {
            "red": {"status": "red_expected", "evidence_path": "artifacts/tdd/red.json"},
            "patch": {"status": "applied", "evidence_path": "artifacts/tdd/patch.json"},
            "green": {"status": "green_passed", "evidence_path": "artifacts/tdd/green.json"},
            "degraded": {"status": "not_degraded", "reason": ""},
        },
        "evidence_refs": [
            "artifacts/tdd/red.json",
            "artifacts/tdd/patch.json",
            "artifacts/tdd/green.json",
        ],
    }


def test_run_tdd_blueprint_smoke_executes_pytest_and_validates_report(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "tdd_smoke_report.json"
    monkeypatch.setattr(run_tdd_blueprint_smoke, "_python_executable", lambda: "python3")

    def fake_run(command: list[str], *, env: dict[str, str]):  # noqa: ANN001
        assert command[0:3] == ["python3", "-m", "pytest"]
        Path(env["TDD_SMOKE_REPORT_PATH"]).write_text(json.dumps(_valid_smoke_report()), encoding="utf-8")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(run_tdd_blueprint_smoke, "_run_command", fake_run)
    report = run_tdd_blueprint_smoke.run_tdd_blueprint_smoke(out=str(out), strict=True)
    assert report["ok"] is True
    assert report["schema"] == "tdd_smoke_gate_report.v1"


def test_run_tdd_blueprint_smoke_fails_for_missing_green_evidence(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "tdd_smoke_report.json"
    monkeypatch.setattr(run_tdd_blueprint_smoke, "_python_executable", lambda: "python3")

    def fake_run(command: list[str], *, env: dict[str, str]):  # noqa: ANN001
        report = _valid_smoke_report()
        report["phases"]["green"]["status"] = "green_failed"
        Path(env["TDD_SMOKE_REPORT_PATH"]).write_text(json.dumps(report), encoding="utf-8")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(run_tdd_blueprint_smoke, "_run_command", fake_run)
    report = run_tdd_blueprint_smoke.run_tdd_blueprint_smoke(out=str(out), strict=True)
    assert report["ok"] is False
