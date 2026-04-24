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
            ("GET", "/goals/goal-1"): (200, {"id": "goal-1", "title": "Goal"}),
            ("GET", "/goals/goal-1/detail"): (200, {"id": "goal-1", "trace_ref": "trace-1"}),
            ("GET", "/goals/goal-1/plan"): (200, {"nodes": [{"id": "n-1"}]}),
            ("PATCH", "/goals/goal-1/plan/nodes/n-1"): (200, {"updated": True}),
            ("GET", "/goals/goal-1/governance-summary"): (200, {"governance_mode": "strict"}),
            ("GET", "/tasks"): (200, {"items": [{"id": "task-1", "status": "queued", "title": "Analyze file"}]}),
            ("GET", "/tasks/task-1"): (200, {"id": "task-1", "status": "queued"}),
            ("PATCH", "/tasks/task-1"): (200, {"updated": True}),
            ("POST", "/tasks/task-1/assign"): (200, {"updated": True}),
            ("POST", "/tasks/task-1/step/propose"): (200, {"updated": True}),
            ("POST", "/tasks/task-1/step/execute"): (200, {"updated": True}),
            ("GET", "/tasks/timeline?limit=10&team_id=team-1&agent=agent-a&status=queued&error_only=1"): (
                200,
                {"items": [{"task_id": "task-1"}]},
            ),
            ("GET", "/tasks/orchestration/read-model"): (200, {"state": "active"}),
            ("GET", "/tasks/task-1/logs"): (200, {"items": [{"line": "ok"}]}),
            ("GET", "/tasks/archived?limit=5&offset=2"): (200, {"items": [{"id": "task-archived-1"}]}),
            ("POST", "/tasks/archived/task-archived-1/restore"): (200, {"updated": True}),
            ("POST", "/tasks/archived/cleanup"): (200, {"updated": True}),
            ("DELETE", "/tasks/archived/task-archived-1"): (200, {"updated": True}),
            ("GET", "/artifacts"): (
                200,
                {"items": [{"id": "artifact-1", "type": "report", "title": "Result summary"}]},
            ),
            ("GET", "/artifacts/artifact-1"): (200, {"id": "artifact-1", "type": "report"}),
            ("POST", "/artifacts/artifact-1/extract"): (200, {"updated": True}),
            ("POST", "/artifacts/artifact-1/rag-index"): (200, {"updated": True}),
            ("GET", "/artifacts/artifact-1/rag-status"): (200, {"indexed": True}),
            ("GET", "/artifacts/artifact-1/rag-preview?limit=2"): (200, {"items": [{"chunk_id": "c1"}]}),
            ("GET", "/knowledge/collections"): (200, {"items": [{"id": "kc-1"}]}),
            ("GET", "/knowledge/index-profiles"): (200, {"items": [{"id": "kip-1"}]}),
            ("GET", "/knowledge/collections/kc-1"): (200, {"id": "kc-1"}),
            ("POST", "/knowledge/collections/kc-1/index"): (200, {"updated": True}),
            ("POST", "/knowledge/collections/kc-1/search"): (200, {"items": [{"source": "doc-1"}]}),
            ("GET", "/templates"): (200, {"items": [{"id": "tpl-1"}]}),
            ("GET", "/templates/variable-registry"): (200, {"variables": [{"name": "goal_text"}]}),
            ("GET", "/templates/sample-contexts"): (200, {"samples": [{"name": "sample-1"}]}),
            ("POST", "/templates/validate"): (200, {"valid": True}),
            ("POST", "/templates/preview"): (200, {"rendered": "ok"}),
            ("POST", "/templates/validation-diagnostics"): (200, {"diagnostics": []}),
            ("GET", "/teams"): (200, {"items": [{"id": "team-1"}]}),
            ("GET", "/teams/blueprints"): (200, {"items": [{"id": "bp-1"}]}),
            ("GET", "/teams/blueprints/catalog"): (200, {"items": [{"id": "catalog-1"}]}),
            ("GET", "/teams/blueprints/bp-1"): (200, {"id": "bp-1", "name": "Core BP"}),
            ("GET", "/teams/types"): (200, {"items": [{"id": "tt-1"}]}),
            ("GET", "/teams/roles"): (200, {"items": [{"id": "role-1"}]}),
            ("GET", "/teams/types/tt-1/roles"): (200, {"items": [{"id": "role-1"}]}),
            ("POST", "/teams/team-1/activate"): (200, {"updated": True}),
            ("GET", "/instruction-layers/model"): (200, {"layers": [{"id": "base"}]}),
            (
                "GET",
                "/instruction-layers/effective?owner_username=ops&task_id=task-1&goal_id=goal-1&session_id=s1&usage_key=u1&profile_id=ip-1&overlay_id=io-1",
            ): (200, {"effective_stack": [{"layer": "base"}]}),
            ("GET", "/instruction-profiles?owner_username=ops"): (200, {"items": [{"id": "ip-1"}]}),
            (
                "GET",
                "/instruction-overlays?owner_username=ops&attachment_kind=task&attachment_id=task-1",
            ): (200, {"items": [{"id": "io-1"}]}),
            ("POST", "/instruction-profiles/ip-1/select"): (200, {"updated": True}),
            ("POST", "/instruction-overlays/io-1/select"): (200, {"updated": True}),
            ("POST", "/instruction-overlays/io-1/attach"): (200, {"updated": True}),
            ("POST", "/instruction-overlays/io-1/detach"): (200, {"updated": True}),
            ("POST", "/goals/goal-1/instruction-selection"): (200, {"updated": True}),
            ("POST", "/tasks/task-1/instruction-selection"): (200, {"updated": True}),
            ("GET", "/tasks/autopilot/status"): (200, {"running": False}),
            ("GET", "/tasks/auto-planner/status"): (200, {"enabled": True}),
            ("GET", "/triggers/status"): (200, {"enabled": True}),
            ("POST", "/tasks/autopilot/start"): (200, {"updated": True}),
            ("POST", "/tasks/autopilot/stop"): (200, {"updated": True}),
            ("POST", "/tasks/autopilot/tick"): (200, {"updated": True}),
            ("POST", "/tasks/auto-planner/configure"): (200, {"updated": True}),
            ("POST", "/triggers/configure"): (200, {"updated": True}),
            ("POST", "/api/system/audit/analyze?limit=25"): (200, {"summary": {"total": 1}}),
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
    assert client.get_goal("goal-1").data["id"] == "goal-1"
    assert client.get_goal_detail("goal-1").data["trace_ref"] == "trace-1"
    assert client.get_goal_plan("goal-1").data["nodes"][0]["id"] == "n-1"
    assert client.patch_goal_plan_node("goal-1", "n-1", {"status": "done"}).data["updated"] is True
    assert client.get_goal_governance_summary("goal-1").data["governance_mode"] == "strict"
    assert client.create_goal({"goal_text": "Ship parity", "mode": "guided"}).data["goal_id"] == "goal-1"
    assert client.list_tasks().data["items"][0]["id"] == "task-1"
    assert client.get_task("task-1").data["id"] == "task-1"
    assert client.patch_task("task-1", {"status": "in_progress"}).data["updated"] is True
    assert client.assign_task("task-1", {"agent": "agent-a"}).data["updated"] is True
    assert client.propose_task_step("task-1", {"proposal": "x"}).data["updated"] is True
    assert client.execute_task_step("task-1", {"dry_run": True}).data["updated"] is True
    assert (
        client.get_task_timeline(team_id="team-1", agent="agent-a", status="queued", error_only=True, limit=10).data[
            "items"
        ][0]["task_id"]
        == "task-1"
    )
    assert client.get_task_orchestration_read_model().data["state"] == "active"
    assert client.get_task_logs("task-1").data["items"][0]["line"] == "ok"
    assert client.list_archived_tasks(limit=5, offset=2).data["items"][0]["id"] == "task-archived-1"
    assert client.restore_archived_task("task-archived-1").data["updated"] is True
    assert client.cleanup_archived_tasks({"dry_run": True}).data["updated"] is True
    assert client.delete_archived_task("task-archived-1").data["updated"] is True
    assert client.list_artifacts().data["items"][0]["type"] == "report"
    assert client.get_artifact("artifact-1").data["id"] == "artifact-1"
    assert client.extract_artifact("artifact-1").data["updated"] is True
    assert client.index_artifact("artifact-1", {"profile": "default"}).data["updated"] is True
    assert client.get_artifact_rag_status("artifact-1").data["indexed"] is True
    assert client.get_artifact_rag_preview("artifact-1", limit=2).data["items"][0]["chunk_id"] == "c1"
    assert client.list_knowledge_collections().data["items"][0]["id"] == "kc-1"
    assert client.list_knowledge_index_profiles().data["items"][0]["id"] == "kip-1"
    assert client.get_knowledge_collection("kc-1").data["id"] == "kc-1"
    assert client.index_knowledge_collection("kc-1", {"profile": "default"}).data["updated"] is True
    assert client.search_knowledge_collection("kc-1", query="parity", top_k=5).data["items"][0]["source"] == "doc-1"
    assert client.list_templates().data["items"][0]["id"] == "tpl-1"
    assert client.get_template_variable_registry().data["variables"][0]["name"] == "goal_text"
    assert client.get_template_sample_contexts().data["samples"][0]["name"] == "sample-1"
    assert client.validate_template({"template": "x"}).data["valid"] is True
    assert client.preview_template({"template": "x"}).data["rendered"] == "ok"
    assert client.template_validation_diagnostics({"template": "x"}).data["diagnostics"] == []
    assert client.list_teams().data["items"][0]["id"] == "team-1"
    assert client.list_blueprints().data["items"][0]["id"] == "bp-1"
    assert client.list_blueprint_catalog().data["items"][0]["id"] == "catalog-1"
    assert client.get_blueprint("bp-1").data["id"] == "bp-1"
    assert client.list_team_types().data["items"][0]["id"] == "tt-1"
    assert client.list_team_roles().data["items"][0]["id"] == "role-1"
    assert client.list_roles_for_team_type("tt-1").data["items"][0]["id"] == "role-1"
    assert client.activate_team("team-1").data["updated"] is True
    assert client.get_instruction_layer_model().data["layers"][0]["id"] == "base"
    assert (
        client.get_instruction_layers_effective(
            owner_username="ops",
            task_id="task-1",
            goal_id="goal-1",
            session_id="s1",
            usage_key="u1",
            profile_id="ip-1",
            overlay_id="io-1",
        ).data["effective_stack"][0]["layer"]
        == "base"
    )
    assert client.list_instruction_profiles(owner_username="ops").data["items"][0]["id"] == "ip-1"
    assert (
        client.list_instruction_overlays(owner_username="ops", attachment_kind="task", attachment_id="task-1").data[
            "items"
        ][0]["id"]
        == "io-1"
    )
    assert client.select_instruction_profile("ip-1").data["updated"] is True
    assert client.select_instruction_overlay("io-1").data["updated"] is True
    assert client.link_instruction_overlay("io-1", {"attachment_kind": "task"}).data["updated"] is True
    assert client.unlink_instruction_overlay("io-1").data["updated"] is True
    assert client.set_goal_instruction_selection("goal-1", {"profile_id": "ip-1"}).data["updated"] is True
    assert client.set_task_instruction_selection("task-1", {"overlay_id": "io-1"}).data["updated"] is True
    assert client.get_autopilot_status().data["running"] is False
    assert client.get_auto_planner_status().data["enabled"] is True
    assert client.get_triggers_status().data["enabled"] is True
    assert client.start_autopilot({"max_concurrency": 2}).data["updated"] is True
    assert client.stop_autopilot().data["updated"] is True
    assert client.tick_autopilot().data["updated"] is True
    assert client.configure_auto_planner({"enabled": True}).data["updated"] is True
    assert client.configure_triggers({"enabled": True}).data["updated"] is True
    assert client.analyze_audit_logs(limit=25).data["summary"]["total"] == 1
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
    assert ("GET", "/goals/goal-1") in called_paths
    assert ("GET", "/goals/goal-1/detail") in called_paths
    assert ("GET", "/goals/goal-1/plan") in called_paths
    assert ("PATCH", "/goals/goal-1/plan/nodes/n-1") in called_paths
    assert ("GET", "/goals/goal-1/governance-summary") in called_paths
    assert ("GET", "/tasks") in called_paths
    assert ("GET", "/tasks/task-1") in called_paths
    assert ("PATCH", "/tasks/task-1") in called_paths
    assert ("POST", "/tasks/task-1/assign") in called_paths
    assert ("POST", "/tasks/task-1/step/propose") in called_paths
    assert ("POST", "/tasks/task-1/step/execute") in called_paths
    assert ("GET", "/tasks/timeline?limit=10&team_id=team-1&agent=agent-a&status=queued&error_only=1") in called_paths
    assert ("GET", "/tasks/orchestration/read-model") in called_paths
    assert ("GET", "/tasks/task-1/logs") in called_paths
    assert ("GET", "/tasks/archived?limit=5&offset=2") in called_paths
    assert ("POST", "/tasks/archived/task-archived-1/restore") in called_paths
    assert ("POST", "/tasks/archived/cleanup") in called_paths
    assert ("DELETE", "/tasks/archived/task-archived-1") in called_paths
    assert ("GET", "/artifacts") in called_paths
    assert ("GET", "/artifacts/artifact-1") in called_paths
    assert ("POST", "/artifacts/artifact-1/extract") in called_paths
    assert ("POST", "/artifacts/artifact-1/rag-index") in called_paths
    assert ("GET", "/artifacts/artifact-1/rag-status") in called_paths
    assert ("GET", "/artifacts/artifact-1/rag-preview?limit=2") in called_paths
    assert ("GET", "/knowledge/collections") in called_paths
    assert ("GET", "/knowledge/index-profiles") in called_paths
    assert ("GET", "/knowledge/collections/kc-1") in called_paths
    assert ("POST", "/knowledge/collections/kc-1/index") in called_paths
    assert ("POST", "/knowledge/collections/kc-1/search") in called_paths
    assert ("GET", "/templates") in called_paths
    assert ("GET", "/templates/variable-registry") in called_paths
    assert ("GET", "/templates/sample-contexts") in called_paths
    assert ("POST", "/templates/validate") in called_paths
    assert ("POST", "/templates/preview") in called_paths
    assert ("POST", "/templates/validation-diagnostics") in called_paths
    assert ("GET", "/teams") in called_paths
    assert ("GET", "/teams/blueprints") in called_paths
    assert ("GET", "/teams/blueprints/catalog") in called_paths
    assert ("GET", "/teams/blueprints/bp-1") in called_paths
    assert ("GET", "/teams/types") in called_paths
    assert ("GET", "/teams/roles") in called_paths
    assert ("GET", "/teams/types/tt-1/roles") in called_paths
    assert ("POST", "/teams/team-1/activate") in called_paths
    assert ("GET", "/instruction-layers/model") in called_paths
    assert (
        "GET",
        "/instruction-layers/effective?owner_username=ops&task_id=task-1&goal_id=goal-1&session_id=s1&usage_key=u1&profile_id=ip-1&overlay_id=io-1",
    ) in called_paths
    assert ("GET", "/instruction-profiles?owner_username=ops") in called_paths
    assert ("GET", "/instruction-overlays?owner_username=ops&attachment_kind=task&attachment_id=task-1") in called_paths
    assert ("POST", "/instruction-profiles/ip-1/select") in called_paths
    assert ("POST", "/instruction-overlays/io-1/select") in called_paths
    assert ("POST", "/instruction-overlays/io-1/attach") in called_paths
    assert ("POST", "/instruction-overlays/io-1/detach") in called_paths
    assert ("POST", "/goals/goal-1/instruction-selection") in called_paths
    assert ("POST", "/tasks/task-1/instruction-selection") in called_paths
    assert ("GET", "/tasks/autopilot/status") in called_paths
    assert ("GET", "/tasks/auto-planner/status") in called_paths
    assert ("GET", "/triggers/status") in called_paths
    assert ("POST", "/tasks/autopilot/start") in called_paths
    assert ("POST", "/tasks/autopilot/stop") in called_paths
    assert ("POST", "/tasks/autopilot/tick") in called_paths
    assert ("POST", "/tasks/auto-planner/configure") in called_paths
    assert ("POST", "/triggers/configure") in called_paths
    assert ("POST", "/api/system/audit/analyze?limit=25") in called_paths
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
