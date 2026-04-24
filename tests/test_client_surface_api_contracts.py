from __future__ import annotations

import json

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.profile_auth import build_client_profile


def test_external_client_api_contract_methods_cover_surface_flows() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method, url, _headers, body, _timeout):  # noqa: ANN001
        payload = json.loads(body.decode("utf-8")) if body else None
        calls.append((method, url, payload))
        path = url.split("http://localhost:8080", 1)[-1]
        responses = {
            ("GET", "/health"): (200, {"state": "ready"}),
            ("GET", "/capabilities"): (200, {"capabilities": ["goals", "tasks", "artifacts", "approvals"]}),
            ("GET", "/dashboard/read-model?benchmark_task_kind=analysis&include_task_snapshot=1"): (
                200,
                {"active_profile": "balanced", "governance_mode": "strict"},
            ),
            ("GET", "/assistant/read-model"): (200, {"active_mode": "operator"}),
            ("GET", "/config"): (200, {"runtime_profile": "balanced"}),
            ("POST", "/config"): (200, {"updated": True}),
            ("GET", "/providers"): (200, {"items": [{"id": "p-1", "provider": "ollama"}]}),
            ("GET", "/providers/catalog"): (200, {"providers": ["ollama"]}),
            ("GET", "/llm/benchmarks?task_kind=analysis&top_n=5"): (200, {"items": [{"model": "m1", "score": 0.8}]}),
            ("GET", "/llm/benchmarks/config"): (200, {"enabled": True}),
            ("GET", "/api/system/contracts"): (200, {"contracts_version": "v1"}),
            ("GET", "/api/system/agents"): (200, {"items": [{"id": "agent-1"}]}),
            ("GET", "/api/system/stats"): (200, {"queue_depth": 2}),
            ("GET", "/api/system/stats/history"): (200, {"items": [{"ts": 1, "queue_depth": 2}]}),
            ("GET", "/api/system/audit-logs?limit=30&offset=0"): (200, {"items": [{"id": "audit-1"}]}),
            ("GET", "/goals"): (200, {"items": [{"id": "goal-1"}]}),
            ("GET", "/goals/modes"): (200, {"items": [{"id": "guided"}]}),
            ("GET", "/tasks"): (200, {"items": [{"id": "task-1", "status": "queued", "title": "Analyze file"}]}),
            ("GET", "/artifacts"): (
                200,
                {"items": [{"id": "artifact-1", "type": "report", "title": "Result summary"}]},
            ),
            ("GET", "/knowledge/collections"): (200, {"items": [{"id": "kc-1"}]}),
            ("GET", "/knowledge/index-profiles"): (200, {"items": [{"id": "kip-1"}]}),
            ("GET", "/teams"): (200, {"items": [{"id": "team-1"}]}),
            ("GET", "/tasks/autopilot/status"): (200, {"running": False}),
            ("GET", "/tasks/auto-planner/status"): (200, {"enabled": True}),
            ("GET", "/triggers/status"): (200, {"enabled": True}),
            ("GET", "/approvals"): (200, {"items": [{"id": "approval-1", "scope": "deploy", "state": "pending"}]}),
            ("GET", "/repairs"): (200, {"items": [{"session_id": "repair-1"}]}),
            ("POST", "/goals"): (200, {"goal_id": "goal-1", "task_id": "task-1"}),
            ("POST", "/tasks/analyze"): (200, {"task_id": "task-analyze-1", "status": "queued"}),
            ("POST", "/tasks/review"): (200, {"task_id": "task-review-1", "status": "queued"}),
            ("POST", "/tasks/patch-plan"): (200, {"task_id": "task-patch-1", "status": "queued"}),
            ("POST", "/projects/new"): (200, {"task_id": "task-project-new-1", "status": "queued"}),
            ("POST", "/projects/evolve"): (200, {"task_id": "task-project-evolve-1", "status": "queued"}),
        }
        status, response_payload = responses[(method, path)]
        return status, json.dumps(response_payload)

    client = AnantaApiClient(
        build_client_profile({"profile_id": "api-contract", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    context = {"schema": "client_bounded_context_payload_v1", "selection_text": "print('x')"}

    assert client.get_health().data == {"state": "ready"}
    assert "goals" in client.get_capabilities().data["capabilities"]
    assert client.get_dashboard_read_model().data["active_profile"] == "balanced"
    assert client.get_assistant_read_model().data["active_mode"] == "operator"
    assert client.get_config().data["runtime_profile"] == "balanced"
    assert client.set_config({"runtime_profile": "strict"}).data["updated"] is True
    assert client.list_providers().data["items"][0]["provider"] == "ollama"
    assert client.list_provider_catalog().data["providers"][0] == "ollama"
    assert client.get_llm_benchmarks(task_kind="analysis", top_n=5).data["items"][0]["model"] == "m1"
    assert client.get_llm_benchmarks_config().data["enabled"] is True
    assert client.get_system_contracts().data["contracts_version"] == "v1"
    assert client.list_agents().data["items"][0]["id"] == "agent-1"
    assert client.get_stats().data["queue_depth"] == 2
    assert client.get_stats_history().data["items"][0]["ts"] == 1
    assert client.get_audit_logs(limit=30).data["items"][0]["id"] == "audit-1"
    assert client.list_goals().data["items"][0]["id"] == "goal-1"
    assert client.list_goal_modes().data["items"][0]["id"] == "guided"
    assert client.list_tasks().data["items"][0]["id"] == "task-1"
    assert client.list_artifacts().data["items"][0]["type"] == "report"
    assert client.list_knowledge_collections().data["items"][0]["id"] == "kc-1"
    assert client.list_knowledge_index_profiles().data["items"][0]["id"] == "kip-1"
    assert client.list_teams().data["items"][0]["id"] == "team-1"
    assert client.get_autopilot_status().data["running"] is False
    assert client.get_auto_planner_status().data["enabled"] is True
    assert client.get_triggers_status().data["enabled"] is True
    assert client.list_approvals().data["items"][0]["state"] == "pending"
    assert client.list_repairs().data["items"][0]["session_id"] == "repair-1"
    assert client.submit_goal("Demo Goal", context).data["goal_id"] == "goal-1"
    assert client.analyze_context(context).data["task_id"] == "task-analyze-1"
    assert client.review_context(context).data["task_id"] == "task-review-1"
    assert client.patch_plan(context).data["task_id"] == "task-patch-1"
    assert client.create_project_new("Create project", context).data["task_id"] == "task-project-new-1"
    assert client.create_project_evolve("Evolve project", context).data["task_id"] == "task-project-evolve-1"

    called_paths = {(method, url.split("http://localhost:8080", 1)[-1]) for method, url, _ in calls}
    assert ("GET", "/health") in called_paths
    assert ("GET", "/capabilities") in called_paths
    assert ("GET", "/dashboard/read-model?benchmark_task_kind=analysis&include_task_snapshot=1") in called_paths
    assert ("GET", "/assistant/read-model") in called_paths
    assert ("GET", "/config") in called_paths
    assert ("POST", "/config") in called_paths
    assert ("GET", "/providers") in called_paths
    assert ("GET", "/providers/catalog") in called_paths
    assert ("GET", "/llm/benchmarks?task_kind=analysis&top_n=5") in called_paths
    assert ("GET", "/llm/benchmarks/config") in called_paths
    assert ("GET", "/api/system/contracts") in called_paths
    assert ("GET", "/api/system/agents") in called_paths
    assert ("GET", "/api/system/stats") in called_paths
    assert ("GET", "/api/system/stats/history") in called_paths
    assert ("GET", "/api/system/audit-logs?limit=30&offset=0") in called_paths
    assert ("GET", "/goals") in called_paths
    assert ("GET", "/goals/modes") in called_paths
    assert ("GET", "/tasks") in called_paths
    assert ("GET", "/artifacts") in called_paths
    assert ("GET", "/knowledge/collections") in called_paths
    assert ("GET", "/knowledge/index-profiles") in called_paths
    assert ("GET", "/teams") in called_paths
    assert ("GET", "/tasks/autopilot/status") in called_paths
    assert ("GET", "/tasks/auto-planner/status") in called_paths
    assert ("GET", "/triggers/status") in called_paths
    assert ("GET", "/approvals") in called_paths
    assert ("GET", "/repairs") in called_paths
    assert ("POST", "/goals") in called_paths
    assert ("POST", "/tasks/analyze") in called_paths
    assert ("POST", "/tasks/review") in called_paths
    assert ("POST", "/tasks/patch-plan") in called_paths
    assert ("POST", "/projects/new") in called_paths
    assert ("POST", "/projects/evolve") in called_paths


def test_external_client_api_contract_exposes_degraded_response_shapes() -> None:
    def transport(method, url, _headers, _body, _timeout):  # noqa: ANN001
        path = url.split("http://localhost:8080", 1)[-1]
        routes = {
            ("GET", "/capabilities"): (422, '{"error":"capability_missing"}'),
            ("POST", "/goals"): (403, '{"error":"policy_denied"}'),
            ("POST", "/tasks/review"): (200, "not-json"),
            ("POST", "/tasks/analyze"): (401, '{"error":"auth_failed"}'),
        }
        return routes[(method, path)]

    client = AnantaApiClient(
        build_client_profile({"profile_id": "api-contract", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    context = {"schema": "client_bounded_context_payload_v1", "selection_text": "print('x')"}

    capabilities = client.get_capabilities()
    denied_goal = client.submit_goal("Denied Goal", context)
    malformed_review = client.review_context(context)
    unauthorized_analyze = client.analyze_context(context)

    assert capabilities.state == "capability_missing"
    assert denied_goal.state == "policy_denied"
    assert malformed_review.state == "malformed_response"
    assert unauthorized_analyze.state == "auth_failed"
    assert malformed_review.retriable is True
    assert denied_goal.retriable is False
