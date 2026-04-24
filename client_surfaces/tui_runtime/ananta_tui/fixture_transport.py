from __future__ import annotations

import json
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit


def build_fixture_transport():  # noqa: C901
    config_payload = {
        "runtime_profile": "balanced",
        "governance_mode": "strict",
        "goal_workflow_enabled": True,
        "persisted_plans_enabled": True,
        "feature_flags": {"goal_workflow_enabled": True, "persisted_plans_enabled": True},
        "providers": {"default": "ananta-default"},
        "api_token": "fixture-secret-token",
    }
    goals_payload = {
        "items": [
            {
                "id": "G-1",
                "title": "Runtime parity",
                "status": "in_progress",
                "team": "core",
                "mode": "guided",
                "summary": "Expand TUI parity shell",
            },
            {
                "id": "G-2",
                "title": "Schema hardening",
                "status": "todo",
                "team": "governance",
                "mode": "quick",
                "summary": "Align todo validators",
            },
        ]
    }
    goal_modes_payload = {"items": [{"id": "guided"}, {"id": "quick"}, {"id": "strict"}]}
    goal_detail_payload = {
        "id": "G-1",
        "title": "Runtime parity",
        "plan_ref": "GP-1",
        "trace_ref": "trace-11",
        "related_task_ids": ["T-1", "T-2"],
        "related_artifact_ids": ["A-1"],
    }
    goal_plan_payload = {
        "id": "GP-1",
        "nodes": [
            {"id": "N-1", "title": "Map APIs", "status": "done", "depends_on": []},
            {"id": "N-2", "title": "Render sections", "status": "in_progress", "depends_on": ["N-1"]},
            {"id": "N-3", "title": "Harden tests", "status": "todo", "depends_on": ["N-2"]},
        ],
    }
    goal_governance_payload = {
        "goal_id": "G-1",
        "governance_mode": "strict",
        "risk_level": "high",
        "policy_state": "approved_with_guards",
    }
    tasks_payload = {
        "items": [
            {
                "id": "T-1",
                "title": "Inspect runtime surface",
                "status": "in_progress",
                "team_id": "team-core",
                "agent": "agent-alpha",
                "execution_state": "running",
                "artifact_ids": ["A-1"],
            },
            {
                "id": "T-2",
                "title": "Run smoke flow",
                "status": "todo",
                "team_id": "team-core",
                "agent": "agent-beta",
                "execution_state": "queued",
                "artifact_ids": [],
            },
        ]
    }
    task_detail_payload = {
        "id": "T-1",
        "title": "Inspect runtime surface",
        "status": "in_progress",
        "owner": "team-core",
        "agent": "agent-alpha",
        "proposal_state": "accepted",
        "execution_state": "running",
        "artifact_ids": ["A-1"],
        "timeline_ref": "TL-1",
    }
    task_timeline_payload = {
        "items": [
            {"event_id": "TL-1", "task_id": "T-1", "status": "running", "agent": "agent-alpha"},
            {"event_id": "TL-2", "task_id": "T-2", "status": "queued", "agent": "agent-beta"},
        ]
    }
    task_orchestration_payload = {
        "state": "active",
        "queues": {
            "normal": [{"task_id": "T-2"}],
            "blocked": [{"task_id": "T-9", "reason": "awaiting_approval"}],
            "failed": [{"task_id": "T-8", "reason": "runtime_error"}],
            "stale": [{"task_id": "T-5", "reason": "heartbeat_timeout"}],
        },
    }
    task_logs_payload = {"items": [{"ts": "2026-04-24T22:00:00Z", "line": "step started"}]}
    archived_tasks_payload = {
        "items": [{"id": "TA-1", "title": "Old task", "status": "archived", "archived_at": "2026-04-20T10:00:00Z"}]
    }
    artifacts_payload = {
        "items": [
            {"id": "A-1", "title": "Runtime summary", "type": "markdown", "task_id": "T-1"},
            {"id": "A-2", "title": "Trace dump", "type": "text", "task_id": "T-2"},
        ]
    }
    artifact_detail_payload = {
        "id": "A-1",
        "title": "Runtime summary",
        "type": "markdown",
        "size_bytes": 1824,
        "preview": "### Runtime summary...",
        "task_id": "T-1",
    }
    artifact_rag_status_payload = {"artifact_id": "A-1", "indexed": True, "chunks": 12}
    artifact_rag_preview_payload = {"items": [{"chunk_id": "C-1", "score": 0.93, "text": "Runtime shell summary"}]}
    knowledge_collections_payload = {"items": [{"id": "KC-1", "name": "ops-notes", "documents": 12}]}
    knowledge_index_profiles_payload = {"items": [{"id": "KIP-1", "name": "default", "chunk_size": 600}]}
    knowledge_collection_detail_payload = {
        "id": "KC-1",
        "name": "ops-notes",
        "description": "Operator notes",
        "documents": 12,
        "last_indexed_at": "2026-04-24T20:00:00Z",
    }
    knowledge_search_payload = {"items": [{"source": "ops-notes.md", "score": 0.88, "snippet": "TUI parity baseline"}]}
    templates_payload = {
        "items": [
            {"id": "TPL-1", "name": "Planner Template", "kind": "planner", "version": 3},
            {"id": "TPL-2", "name": "Reviewer Template", "kind": "reviewer", "version": 2},
        ]
    }
    template_variable_registry_payload = {"variables": [{"name": "goal_text"}, {"name": "context"}]}
    template_sample_contexts_payload = {"samples": [{"name": "default-goal", "payload": {"goal_text": "Improve docs"}}]}
    providers_payload = {
        "items": [
            {"id": "ananta-default", "provider": "ollama", "model": "qwen2.5-coder:7b", "status": "healthy"},
            {"id": "ananta-smoke", "provider": "ollama", "model": "qwen2.5-coder:14b", "status": "healthy"},
        ]
    }
    fixture_payloads = {
        "/health": {"state": "ready"},
        "/capabilities": {
            "capabilities": [
                "dashboard",
                "goals",
                "tasks",
                "artifacts",
                "knowledge",
                "templates",
                "config",
                "system",
                "teams",
                "automation",
                "audit",
                "approvals",
                "repairs",
            ]
        },
        "/dashboard/read-model": {
            "health_state": "ready",
            "governance_mode": "strict",
            "active_profile": "balanced",
            "recent_tasks": [{"id": "T-1", "status": "in_progress"}],
            "warnings": [],
        },
        "/assistant/read-model": {"active_mode": "operator", "hint": "Terminal-safe control surface."},
        "/goals": goals_payload,
        "/goals/modes": goal_modes_payload,
        "/tasks": tasks_payload,
        "/tasks/timeline": task_timeline_payload,
        "/tasks/orchestration/read-model": task_orchestration_payload,
        "/tasks/archived": archived_tasks_payload,
        "/artifacts": artifacts_payload,
        "/knowledge/collections": knowledge_collections_payload,
        "/knowledge/index-profiles": knowledge_index_profiles_payload,
        "/templates": templates_payload,
        "/templates/variable-registry": template_variable_registry_payload,
        "/templates/sample-contexts": template_sample_contexts_payload,
        "/providers": providers_payload,
        "/providers/catalog": {"providers": ["ollama", "openai_compat"], "defaults": {"provider": "ollama"}},
        "/llm/benchmarks": {
            "items": [
                {"provider": "ollama", "model": "qwen2.5-coder:7b", "task_kind": "analysis", "score": 0.79},
                {"provider": "ollama", "model": "qwen2.5-coder:14b", "task_kind": "analysis", "score": 0.83},
            ]
        },
        "/llm/benchmarks/config": {"enabled": True, "providers": ["ollama"], "auto_trigger": {"enabled": True}},
        "/api/system/contracts": {"contracts_version": "v1", "compatibility": "ok"},
        "/api/system/agents": {
            "items": [{"id": "agent-alpha", "state": "ready"}, {"id": "agent-beta", "state": "idle"}]
        },
        "/api/system/stats": {"tasks_total": 22, "tasks_in_progress": 4, "queue_depth": 2},
        "/api/system/stats/history": {"items": [{"ts": 1, "queue_depth": 3}, {"ts": 2, "queue_depth": 2}]},
        "/api/system/audit-logs": {"items": [{"id": "AUD-1", "kind": "approval", "target_id": "T-1"}]},
        "/teams": {"items": [{"id": "team-core", "name": "Core Team", "mode": "active"}]},
        "/tasks/autopilot/status": {"running": False, "max_concurrency": 2, "security_level": "safe"},
        "/tasks/auto-planner/status": {"enabled": True, "last_plan_at": "2026-04-24T20:00:00Z"},
        "/triggers/status": {"enabled": True, "sources": ["webhook", "schedule"]},
        "/approvals": {"items": [{"id": "AP-1", "scope": "repair_step", "state": "pending"}]},
        "/repairs": {
            "items": [
                {
                    "session_id": "R-1",
                    "diagnosis": "disk pressure",
                    "proposed_steps": ["clean temp data"],
                    "verification_result": "pending",
                    "outcome": "not_executed",
                }
            ]
        },
    }

    def _merge_dict(target: dict, patch: dict) -> dict:
        merged = deepcopy(target)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _transport(  # noqa: C901
        method: str,
        url: str,
        _headers: dict[str, str],
        body: bytes | None,
        _timeout: float,
    ) -> tuple[int, str]:
        parsed_url = urlsplit(url)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if method == "POST" and path.endswith("/goals"):
            payload = json.loads((body or b"{}").decode("utf-8", "replace"))
            if isinstance(payload, dict) and payload.get("goal_text"):
                return 201, json.dumps(
                    {"goal_id": "G-3", "task_id": "T-3", "accepted": True, "mode": payload.get("mode")}
                )
            return 201, json.dumps({"goal_id": "G-1", "task_id": "T-1", "accepted": True})

        if path.endswith("/goals/G-1/detail"):
            return 200, json.dumps(goal_detail_payload)
        if path.endswith("/goals/G-1/plan"):
            return 200, json.dumps(goal_plan_payload)
        if path.endswith("/goals/G-1/governance-summary"):
            return 200, json.dumps(goal_governance_payload)

        if path.endswith("/tasks/T-1"):
            return 200, json.dumps(task_detail_payload)
        if path.endswith("/tasks/T-1/logs"):
            return 200, json.dumps(task_logs_payload)

        if path.endswith("/tasks/T-1/assign") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "assign"})
        if path.endswith("/tasks/T-1/step/propose") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "propose"})
        if path.endswith("/tasks/T-1/step/execute") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "execute"})
        if path.endswith("/tasks/T-1") and method == "PATCH":
            return 200, json.dumps({"updated": True, "action": "patch"})

        if path.endswith("/tasks/archived/TA-1/restore") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "restore"})
        if path.endswith("/tasks/archived/cleanup") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "cleanup", "affected": 1})
        if path.endswith("/tasks/archived/TA-1") and method == "DELETE":
            return 200, json.dumps({"updated": True, "action": "delete_archived"})

        if path.endswith("/artifacts/A-1"):
            return 200, json.dumps(artifact_detail_payload)
        if path.endswith("/artifacts/A-1/extract") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "extract"})
        if path.endswith("/artifacts/A-1/rag-index") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "rag-index"})
        if path.endswith("/artifacts/A-1/rag-status"):
            return 200, json.dumps(artifact_rag_status_payload)
        if path.endswith("/artifacts/A-1/rag-preview"):
            limit = int((query.get("limit") or ["5"])[0])
            payload = deepcopy(artifact_rag_preview_payload)
            payload["items"] = payload["items"][: max(1, limit)]
            return 200, json.dumps(payload)

        if path.endswith("/knowledge/collections/KC-1"):
            return 200, json.dumps(knowledge_collection_detail_payload)
        if path.endswith("/knowledge/collections/KC-1/index") and method == "POST":
            return 200, json.dumps({"updated": True, "action": "index_collection"})
        if path.endswith("/knowledge/collections/KC-1/search") and method == "POST":
            return 200, json.dumps(knowledge_search_payload)

        if path.endswith("/templates/validate") and method == "POST":
            return 200, json.dumps({"valid": True, "errors": []})
        if path.endswith("/templates/preview") and method == "POST":
            return 200, json.dumps({"rendered": "Preview output text"})
        if path.endswith("/templates/validation-diagnostics") and method == "POST":
            return 200, json.dumps({"diagnostics": [{"severity": "info", "message": "all good"}]})

        if method == "POST" and path.endswith("/config"):
            patch = json.loads((body or b"{}").decode("utf-8", "replace"))
            if isinstance(patch, dict):
                nonlocal config_payload
                config_payload = _merge_dict(config_payload, patch)
            return 200, json.dumps({"updated": True, "config": config_payload})
        if path.endswith("/config"):
            return 200, json.dumps(config_payload)

        for endpoint, payload in fixture_payloads.items():
            if path.endswith(endpoint):
                return 200, json.dumps(payload)
        return 404, json.dumps({"error": "not_found"})

    return _transport
