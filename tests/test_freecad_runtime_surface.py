from __future__ import annotations

from client_surfaces.freecad.tests.smoke_freecad_workbench_load import run_smoke
from client_surfaces.freecad.workbench.client import FreecadHubClient
from client_surfaces.freecad.workbench.execution import execute_approved_macro
from client_surfaces.freecad.workbench.export_preview import format_export_plan
from client_surfaces.freecad.workbench.inspection_panel import format_inspection_result
from client_surfaces.freecad.workbench.macro_planning import format_macro_plan
from client_surfaces.freecad.workbench.runtime_limits import build_operation_state
from client_surfaces.freecad.workbench.settings import FreecadWorkbenchSettings


def test_freecad_workbench_smoke_contract() -> None:
    report = run_smoke()
    assert report["register"]["status"] == "registered"
    assert report["bridge_errors"] == []
    assert "AnantaSubmitGoal" in report["commands"]


def test_freecad_runtime_views_and_execution_stay_structured() -> None:
    client = FreecadHubClient(FreecadWorkbenchSettings(endpoint="https://hub.local"))
    blocked = execute_approved_macro(
        client,
        script_hash="abc123",
        session_id="s1",
        correlation_id="c1",
        approval_id=None,
        macro_text="print('x')",
    )
    approved = execute_approved_macro(
        client,
        script_hash="abc123",
        session_id="s1",
        correlation_id="c1",
        approval_id="APR-1",
        macro_text="print('x')",
    )
    export_view = format_export_plan(client.request_export_plan(fmt="step", target_path="/tmp/out.step"))
    macro_view = format_macro_plan(client.request_macro_plan(objective="reduce weight"))
    inspection_view = format_inspection_result({"document_name": "Doc", "object_count": 2, "visible_count": 1, "types": ["Body"], "warnings": []})
    progress = build_operation_state(status="running", progress_step=4)

    assert blocked["status"] == "blocked"
    assert approved["status"] == "accepted"
    assert export_view["execution_mode"] == "plan_only"
    assert macro_view["mode"] == "dry_run"
    assert inspection_view["mutation_claim"] == "none"
    assert progress["terminal"] is False
