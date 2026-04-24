from scripts.audit_client_surface_entrypoints import (
    build_blocking_warnings,
    classify_surface,
    collect_done_claims,
    collect_done_task_ids,
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
    warnings = build_blocking_warnings(surface_reports, done_claims, set(), {})

    assert done_claims["tui_surface"] == ["CSH-T05", "TVM-T29"]
    assert warnings == ["surface=tui_surface has done claims (CSH-T05, TVM-T29) but classification=foundation_only"]


def test_audit_enforces_vim_deferred_status_for_crt_t18() -> None:
    todo_payload = {
        "tasks": [
            {"id": "CRT-T18", "status": "done"},
        ]
    }
    done_claims = collect_done_claims(todo_payload)
    done_task_ids = collect_done_task_ids(todo_payload)
    surface_reports = {
        "tui_surface": {"classification": "real_implementation"},
        "eclipse_plugin": {"classification": "foundation_only"},
        "eclipse_views_extension": {"classification": "foundation_only"},
        "nvim_plugin": {"classification": "foundation_only"},
        "vim_plugin": {"classification": "foundation_only"},
    }

    warnings_missing_status = build_blocking_warnings(
        surface_reports,
        done_claims,
        done_task_ids,
        {},
    )
    warnings_with_deferred = build_blocking_warnings(
        surface_reports,
        done_claims,
        done_task_ids,
        {"vim_plugin": "deferred"},
    )

    assert "CRT-T18 done requires surface_status.vim_plugin=deferred" in warnings_missing_status
    assert warnings_with_deferred == []


def test_done_claims_include_current_crt_runtime_ranges() -> None:
    todo_payload = {
        "tasks": [
            {"id": "CRT-T09", "status": "done"},
            {"id": "CRT-T14", "status": "done"},
            {"id": "CRT-T20", "status": "done"},
            {"id": "CRT-T19", "status": "done"},
        ]
    }
    done_claims = collect_done_claims(todo_payload)
    assert done_claims["tui_surface"] == ["CRT-T09"]
    assert done_claims["nvim_plugin"] == ["CRT-T14", "CRT-T19"]
    assert done_claims["eclipse_plugin"] == []
    assert done_claims["vim_plugin"] == []


def test_nvim_requires_smoke_evidence_for_runtime_classification() -> None:
    without_smoke = {
        "client_surfaces/nvim_runtime/plugin/ananta.vim",
        "client_surfaces/nvim_runtime/lua/ananta/init.lua",
        "agent/services/editor_tui_surface_foundation_service.py",
    }
    with_smoke = without_smoke | {"scripts/smoke_nvim_runtime.py"}

    report_without_smoke = classify_surface("nvim_plugin", without_smoke)
    report_with_smoke = classify_surface("nvim_plugin", with_smoke)

    assert report_without_smoke["classification"] == "foundation_only"
    assert report_with_smoke["classification"] == "real_implementation"


def test_eclipse_requires_command_registry_for_runtime_classification() -> None:
    bootstrap_only = {
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/META-INF/MANIFEST.MF",
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/build.properties",
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/core/AnantaApiClient.java",
        "scripts/smoke_eclipse_runtime_bootstrap.py",
        "agent/services/eclipse_plugin_adapter_foundation_service.py",
    }
    with_runtime_registry = bootstrap_only | {
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/commands/EclipseCommandRegistry.java"
    }

    report_bootstrap_only = classify_surface("eclipse_plugin", bootstrap_only)
    report_with_registry = classify_surface("eclipse_plugin", with_runtime_registry)

    assert report_bootstrap_only["classification"] == "foundation_only"
    assert report_with_registry["classification"] == "real_implementation"


def test_eclipse_views_extension_requires_runtime_registry_for_classification() -> None:
    bootstrap_only = {
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/META-INF/MANIFEST.MF",
        "scripts/smoke_eclipse_runtime_bootstrap.py",
        "agent/services/eclipse_plugin_adapter_foundation_service.py",
    }
    with_views_registry = bootstrap_only | {
        "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/views/EclipseViewsExtensionRegistry.java"
    }

    report_bootstrap_only = classify_surface("eclipse_views_extension", bootstrap_only)
    report_with_registry = classify_surface("eclipse_views_extension", with_views_registry)

    assert report_bootstrap_only["classification"] == "foundation_only"
    assert report_with_registry["classification"] == "real_implementation"
