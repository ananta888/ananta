from __future__ import annotations

import json
from copy import deepcopy
from urllib.parse import urlsplit


def build_fixture_transport():
    config_payload = {
        "runtime_profile": "balanced",
        "governance_mode": "strict",
        "goal_workflow_enabled": True,
        "persisted_plans_enabled": True,
        "feature_flags": {"goal_workflow_enabled": True, "persisted_plans_enabled": True},
        "providers": {"default": "ananta-default"},
        "api_token": "fixture-secret-token",
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
        "/goals": {"items": [{"id": "G-1", "title": "Runtime parity", "status": "in_progress", "team": "core"}]},
        "/goals/modes": {"items": [{"id": "guided"}, {"id": "quick"}]},
        "/tasks": {
            "items": [
                {"id": "T-1", "title": "Inspect runtime surface", "status": "in_progress"},
                {"id": "T-2", "title": "Run smoke flow", "status": "todo"},
            ]
        },
        "/artifacts": {
            "items": [
                {"id": "A-1", "title": "Runtime summary", "type": "markdown", "task_id": "T-1"},
            ]
        },
        "/knowledge/collections": {"items": [{"id": "KC-1", "name": "ops-notes", "documents": 12}]},
        "/knowledge/index-profiles": {"items": [{"id": "KIP-1", "name": "default", "chunk_size": 600}]},
        "/providers": {
            "items": [
                {"id": "ananta-default", "provider": "ollama", "model": "qwen2.5-coder:7b", "status": "healthy"},
                {"id": "ananta-smoke", "provider": "ollama", "model": "qwen2.5-coder:14b", "status": "healthy"},
            ]
        },
        "/providers/catalog": {"providers": ["ollama", "openai_compat"], "defaults": {"provider": "ollama"}},
        "/llm/benchmarks": {
            "items": [
                {"provider": "ollama", "model": "qwen2.5-coder:7b", "task_kind": "analysis", "score": 0.79},
                {"provider": "ollama", "model": "qwen2.5-coder:14b", "task_kind": "analysis", "score": 0.83},
            ]
        },
        "/llm/benchmarks/config": {"enabled": True, "providers": ["ollama"], "auto_trigger": {"enabled": True}},
        "/api/system/contracts": {"contracts_version": "v1", "compatibility": "ok"},
        "/api/system/agents": {"items": [{"id": "agent-alpha", "state": "ready"}, {"id": "agent-beta", "state": "idle"}]},
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

    def _transport(
        method: str,
        url: str,
        _headers: dict[str, str],
        body: bytes | None,
        _timeout: float,
    ) -> tuple[int, str]:
        parsed_url = urlsplit(url)
        path = parsed_url.path
        if method == "POST" and path.endswith("/goals"):
            _ = json.loads((body or b"{}").decode("utf-8", "replace"))
            return 201, json.dumps({"goal_id": "G-1", "task_id": "T-1", "accepted": True})
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
