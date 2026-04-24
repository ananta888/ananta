from scripts.audit_client_surface_entrypoints import (
    build_blocking_warnings,
    classify_surface,
    collect_done_claims,
)


def test_classify_surface_reports_real_implementation_when_runtime_exists() -> None:
    paths = {
        "client_surfaces/tui_runtime/ananta_tui/__main__.py",
        "agent/services/editor_tui_surface_foundation_service.py",
    }
    report = classify_surface("tui_surface", paths)
    assert report["classification"] == "real_implementation"
    assert report["runtime_evidence"] == ["client_surfaces/tui_runtime/ananta_tui/__main__.py"]


def test_classify_surface_reports_foundation_only_without_runtime() -> None:
    paths = {
        "agent/services/editor_tui_surface_foundation_service.py",
        "docs/tui-user-operator-guide.md",
    }
    report = classify_surface("tui_surface", paths)
    assert report["classification"] == "foundation_only"
    assert report["runtime_evidence"] == []
    assert "agent/services/editor_tui_surface_foundation_service.py" in report["foundation_evidence"]


def test_done_claims_and_blocking_warning_detect_mismatch() -> None:
    todo_payload = {
        "tasks": [
            {"id": "CSH-T05", "status": "done"},
            {"id": "TVM-T29", "status": "done"},
            {"id": "CSH-T03", "status": "done"},
        ]
    }
    done_claims = collect_done_claims(todo_payload)
    surface_reports = {
        "tui_surface": {"classification": "foundation_only"},
        "eclipse_plugin": {"classification": "foundation_only"},
        "eclipse_views_extension": {"classification": "foundation_only"},
        "nvim_plugin": {"classification": "foundation_only"},
        "vim_plugin": {"classification": "missing"},
    }
    warnings = build_blocking_warnings(surface_reports, done_claims)

    assert done_claims["tui_surface"] == ["CSH-T05", "TVM-T29"]
    assert warnings == ["surface=tui_surface has done claims (CSH-T05, TVM-T29) but classification=foundation_only"]
