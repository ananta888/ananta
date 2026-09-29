from __future__ import annotations

from client_surfaces.common.profile_auth import build_client_profile
from client_surfaces.common.types import ClientResponse
from client_surfaces.tui_runtime.ananta_tui.views import (
    render_approval_repair_view,
    render_artifact_explorer_view,
    render_dashboard_view,
    render_task_workbench_view,
)


def test_tui_dashboard_view_renders_explicit_degraded_states() -> None:
    profile = build_client_profile({"profile_id": "ops", "base_url": "http://localhost:8080"})
    dashboard = ClientResponse(
        ok=False,
        status_code=401,
        state="auth_failed",
        data={},
        error="request_failed:auth_failed",
        retriable=False,
    )
    assistant = ClientResponse(ok=True, status_code=200, state="healthy", data={"active_mode": "operator"})
    health = ClientResponse(
        ok=False,
        status_code=None,
        state="backend_unreachable",
        data=None,
        error="request_failed:backend_unreachable",
        retriable=True,
    )

    rendered = render_dashboard_view(profile, dashboard, assistant, health)

    assert "dashboard_state=auth_failed assistant_state=healthy" in rendered
    assert "health_state=backend_unreachable health_status=None" in rendered
    assert "dashboard_degraded=request_failed:auth_failed" in rendered
    assert "[HEALTH]" in rendered


def test_tui_task_and_artifact_views_cover_empty_and_malformed_states() -> None:
    task_render = render_task_workbench_view(
        tasks=ClientResponse(ok=False, status_code=200, state="malformed_response", data="not-json"),
        task_timeline=ClientResponse(ok=False, status_code=None, state="backend_unreachable", data=None),
        selected_task=ClientResponse(ok=False, status_code=401, state="auth_failed", data={}),
        task_logs=ClientResponse(ok=False, status_code=401, state="auth_failed", data=None),
        task_action_summary=None,
    )
    assert "- no_tasks_available" in task_render
    assert "selected_task=none_or_missing" in task_render
    assert "timeline_empty_or_unavailable" in task_render
    assert "logs_unavailable_or_not_selected" in task_render

    artifact_render = render_artifact_explorer_view(
        artifacts=ClientResponse(ok=False, status_code=401, state="auth_failed", data={"items": []}),
        artifact_detail=ClientResponse(ok=False, status_code=200, state="malformed_response", data="bad"),
        rag_status=ClientResponse(ok=False, status_code=401, state="auth_failed", data={}),
        rag_preview=ClientResponse(ok=False, status_code=None, state="backend_unreachable", data="bad"),
        artifact_action_summary=None,
    )
    assert "- no_artifacts_available" in artifact_render
    assert "artifact_binary_strategy=browser_fallback" in artifact_render
    assert "artifact_upload_strategy=deferred_browser_fallback" in artifact_render


def test_tui_approval_and_repair_view_enforces_explicit_safe_action_model() -> None:
    approvals = ClientResponse(
        ok=True,
        status_code=200,
        state="healthy",
        data={
            "items": [
                {"id": "A1", "state": "pending", "scope": "goal", "task_id": "T1"},
                {"id": "A2", "state": "stale", "scope": "task", "task_id": "T2"},
                {"id": "A3", "state": "policy_denied", "scope": "task", "task_id": "T3"},
            ]
        },
    )
    repairs = ClientResponse(
        ok=True,
        status_code=200,
        state="healthy",
        data={"items": [{"session_id": "R1", "diagnosis": "x", "risk_level": "high"}]},
    )
    tasks = ClientResponse(
        ok=True,
        status_code=200,
        state="healthy",
        data={"items": [{"id": "T1", "proposal_state": "pending_review"}]},
    )

    rendered = render_approval_repair_view(
        approvals,
        repairs,
        tasks,
        approval_action_summary="[APPROVAL-ACTION] preview_only action=approve task_id=T1",
        repair_action_summary="[REPAIR-ACTION] blocked=browser_fallback_required action=execute session_id=R1",
    )

    assert "approval_pending=1 approval_stale=1 approval_denied=1" in rendered
    assert "reviewable_proposals=1" in rendered
    assert "approval_actions_confirmation=required" in rendered
    assert "repair_execution_mode=explicit_only_never_implicit" in rendered
    assert "repair_unknown_or_unsafe_actions=blocked_visible" in rendered
