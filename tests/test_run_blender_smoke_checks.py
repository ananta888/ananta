from __future__ import annotations

import json
from pathlib import Path

from scripts.run_blender_smoke_checks import evaluate_blender_runtime, write_report

ROOT = Path(__file__).resolve().parents[1]


def test_blender_smoke_checks_pass_with_repo_runtime_skeleton(tmp_path: Path) -> None:
    report = evaluate_blender_runtime(root=ROOT)
    assert report["schema"] == "blender_runtime_smoke_report_v1"
    assert report["ok"] is True
    assert any(entry.get("check_id") == "bridge_envelope_contract" for entry in list(report.get("checks") or []))

    out_path = tmp_path / "blender-smoke-report.json"
    write_report(report, report_path=out_path)
    stored = json.loads(out_path.read_text(encoding="utf-8"))
    assert stored["ok"] is True


def test_blender_smoke_checks_detect_missing_runtime_files(tmp_path: Path) -> None:
    report = evaluate_blender_runtime(root=tmp_path)
    assert report["ok"] is False
    missing_checks = [entry for entry in list(report.get("checks") or []) if not entry.get("ok")]
    assert missing_checks
