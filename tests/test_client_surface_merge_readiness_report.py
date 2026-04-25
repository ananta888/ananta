from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "client_surface_merge_readiness_report.json"


def _load_report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_merge_readiness_report_covers_tui_nvim_vim_and_eclipse_statuses() -> None:
    report = _load_report()
    surface_status = dict(report.get("surface_status") or {})

    for key in ("tui_surface", "nvim_plugin", "vim_plugin", "eclipse_plugin"):
        assert key in surface_status
        assert "classification" in surface_status[key]
        assert "declared_status" in surface_status[key]

    assert surface_status["tui_surface"]["declared_status"] in {"runtime_mvp", "runtime_complete"}
    assert surface_status["nvim_plugin"]["declared_status"] in {"runtime_mvp", "runtime_complete"}
    assert surface_status["vim_plugin"]["declared_status"] == "deferred"
    assert surface_status["eclipse_plugin"]["declared_status"] in {"runtime_mvp", "runtime_complete"}


def test_merge_readiness_report_includes_runtime_evidence_commands_and_results() -> None:
    report = _load_report()
    runtime_evidence = dict(report.get("runtime_evidence") or {})
    verification = dict(report.get("verification") or {})

    assert "scripts/smoke_tui_runtime.py" in runtime_evidence.get("tui_surface", [])
    assert "scripts/smoke_nvim_runtime.py" in runtime_evidence.get("nvim_plugin", [])
    assert "scripts/smoke_eclipse_runtime_headless.py" in runtime_evidence.get("eclipse_plugin", [])
    assert "python3 scripts/smoke_client_golden_paths.py" in list(verification.get("smoke_commands") or [])
    assert "tests/test_client_surface_golden_path_smoke.py" in list(verification.get("test_targets") or [])


def test_merge_readiness_report_separates_blockers_from_later_enhancements() -> None:
    report = _load_report()
    blockers = report.get("main_merge_blockers")
    later = report.get("later_enhancements")
    recommendation = dict(report.get("recommendation") or {})

    assert isinstance(blockers, list)
    assert isinstance(later, list)
    assert isinstance(recommendation.get("merge_safe"), bool)
    assert recommendation.get("summary")
