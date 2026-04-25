from __future__ import annotations

import json
import subprocess
from pathlib import Path

import scripts.run_e2e_dogfood_checks as e2e_gate


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_e2e_dogfood_checks_passes_when_tests_reports_and_evidence_are_ok(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(e2e_gate, "ROOT", tmp_path)
    monkeypatch.setattr(e2e_gate, "_python_executable", lambda: "python3")

    def fake_run(command: list[str]):  # noqa: ANN001
        joined = " ".join(command)
        if "generate_e2e_report.py" in joined:
            aggregate = {
                "summary": {
                    "total": 1,
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "advisory": 0,
                    "blocking_failed": 0,
                },
                "flows": [
                    {
                        "flow_id": "core",
                        "status": "passed",
                        "blocking": True,
                        "logs": ["artifacts/e2e/run/flow.log"],
                        "snapshots": ["artifacts/e2e/run/snapshot.txt"],
                        "screenshots": [],
                    }
                ],
            }
            out_path = tmp_path / "artifacts" / "e2e" / "aggregate_report.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(aggregate), encoding="utf-8")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(e2e_gate, "_run_command", fake_run)

    report = e2e_gate.run_e2e_dogfood_checks(artifact_root=tmp_path / "artifacts" / "e2e")

    assert report["ok"] is True
    assert report["summary"]["passed"] == 1
    assert report["aggregate_report_path"] == "artifacts/e2e/aggregate_report.json"


def test_run_e2e_dogfood_checks_fails_on_missing_blocking_snapshots(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(e2e_gate, "ROOT", tmp_path)
    monkeypatch.setattr(e2e_gate, "_python_executable", lambda: "python3")

    def fake_run(command: list[str]):  # noqa: ANN001
        joined = " ".join(command)
        if "generate_e2e_report.py" in joined:
            aggregate = {
                "summary": {
                    "total": 1,
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "advisory": 0,
                    "blocking_failed": 0,
                },
                "flows": [{"flow_id": "core", "status": "passed", "blocking": True, "logs": ["x"], "snapshots": []}],
            }
            out_path = tmp_path / "artifacts" / "e2e" / "aggregate_report.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(aggregate), encoding="utf-8")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(e2e_gate, "_run_command", fake_run)

    report = e2e_gate.run_e2e_dogfood_checks(artifact_root=tmp_path / "artifacts" / "e2e")

    assert report["ok"] is False
    assert any(check["name"] == "required-evidence" and not check["ok"] for check in report["checks"])
