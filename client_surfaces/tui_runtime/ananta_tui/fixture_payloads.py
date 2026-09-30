"""Static response payloads of the ``--fixture`` transport of the TUI runtime.

The payloads mirror the Hub read models closely enough for offline smoke
runs; :func:`build_fixture_payloads` returns a fresh, independent set for every
transport instance so that one run cannot leak state into another.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FixturePayloads:
    """Payloads answered by dedicated routes plus the suffix-matched static endpoints."""

    config: dict[str, Any]
    goal_detail: dict[str, Any]
    goal_plan: dict[str, Any]
    goal_governance: dict[str, Any]
    task_detail: dict[str, Any]
    task_detail_stale: dict[str, Any]
    task_detail_denied: dict[str, Any]
    task_logs: dict[str, Any]
    artifact_detail: dict[str, Any]
    artifact_rag_status: dict[str, Any]
    artifact_rag_preview: dict[str, Any]
    knowledge_collection_detail: dict[str, Any]
    knowledge_search: dict[str, Any]
    blueprint_detail: dict[str, Any]
    static_endpoints: dict[str, Any]


def build_fixture_payloads() -> FixturePayloads:
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
                "proposal_state": "pending_review",
                "execution_state": "running",
                "artifact_ids": ["A-1"],
            },
            {
                "id": "T-2",
                "title": "Run smoke flow",
                "status": "todo",
                "team_id": "team-core",
                "agent": "agent-beta",
                "proposal_state": "stale",
                "execution_state": "queued",
                "artifact_ids": [],
            },
            {
                "id": "T-3",
                "title": "Policy-gated review case",
                "status": "in_progress",
                "team_id": "team-core",
                "agent": "agent-gamma",
                "proposal_state": "pending_review",
                "execution_state": "running",
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
        "proposal_state": "pending_review",
        "execution_state": "running",
        "artifact_ids": ["A-1"],
        "timeline_ref": "TL-1",
    }
    task_detail_stale_payload = {
        "id": "T-2",
        "title": "Run smoke flow",
        "status": "todo",
        "owner": "team-core",
        "agent": "agent-beta",
        "proposal_state": "stale",
        "execution_state": "queued",
        "artifact_ids": [],
        "timeline_ref": "TL-2",
    }
    task_detail_denied_payload = {
        "id": "T-3",
        "title": "Policy-gated review case",
        "status": "in_progress",
        "owner": "team-core",
        "agent": "agent-gamma",
        "proposal_state": "pending_review",
        "execution_state": "running",
        "artifact_ids": [],
        "timeline_ref": "TL-3",
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
    teams_payload = {"items": [{"id": "team-core", "name": "Core Team", "mode": "active", "blueprint_id": "BP-1"}]}
    blueprints_payload = {
        "items": [
            {"id": "BP-1", "name": "Core Blueprint", "team_type_id": "TT-1", "version": 4},
            {"id": "BP-2", "name": "Ops Blueprint", "team_type_id": "TT-2", "version": 2},
        ]
    }
    blueprint_catalog_payload = {"items": [{"id": "BPC-1", "name": "Default Catalog", "blueprint_count": 2}]}
    blueprint_detail_payload = {
        "id": "BP-1",
        "name": "Core Blueprint",
        "team_type_id": "TT-1",
        "roles": ["RL-1", "RL-2"],
        "composition": {"agents": 3, "mode": "balanced"},
    }
    team_types_payload = {"items": [{"id": "TT-1", "name": "Engineering"}, {"id": "TT-2", "name": "Operations"}]}
    team_roles_payload = {"items": [{"id": "RL-1", "name": "Architect"}, {"id": "RL-2", "name": "Reviewer"}]}
    roles_for_type_payload = {"items": [{"id": "RL-1", "name": "Architect"}, {"id": "RL-2", "name": "Reviewer"}]}
    instruction_model_payload = {
        "schema": "instruction_layer_model_v1",
        "layers": [
            {"id": "base", "kind": "system", "overridable": False},
            {"id": "governance", "kind": "safety", "overridable": False},
            {"id": "profile", "kind": "user_profile", "overridable": True},
            {"id": "overlay", "kind": "task_overlay", "overridable": True},
        ],
    }
    instruction_effective_payload = {
        "effective_stack": [
            {"layer": "base", "source": "system"},
            {"layer": "governance", "source": "strict"},
            {"layer": "profile", "source": "IP-1"},
            {"layer": "overlay", "source": "IO-1"},
        ],
        "non_overridable_layers": ["base", "governance"],
    }
    instruction_profiles_payload = {"items": [{"id": "IP-1", "name": "Default Profile", "owner_username": "ops"}]}
    instruction_overlays_payload = {
        "items": [
            {"id": "IO-1", "name": "Task Overlay", "attachment_kind": "task", "attachment_id": "T-1"},
            {"id": "IO-2", "name": "Goal Overlay", "attachment_kind": "goal", "attachment_id": "G-1"},
        ]
    }
    audit_logs_payload = {
        "items": [
            {
                "id": "AUD-1",
                "kind": "approval",
                "target_id": "T-1",
                "task_id": "T-1",
                "goal_id": "G-1",
                "artifact_id": "A-1",
                "trace_ref": "trace-11",
                "message": "token=abc123 decision=approved",
            },
            {
                "id": "AUD-2",
                "kind": "automation",
                "target_id": "G-1",
                "task_id": "T-2",
                "goal_id": "G-1",
                "trace_ref": "trace-22",
                "message": "password=very-secret trigger=fired",
            },
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
        "/api/system/audit-logs": audit_logs_payload,
        "/teams": teams_payload,
        "/teams/blueprints": blueprints_payload,
        "/teams/blueprints/catalog": blueprint_catalog_payload,
        "/teams/types": team_types_payload,
        "/teams/roles": team_roles_payload,
        "/teams/types/TT-1/roles": roles_for_type_payload,
        "/instruction-layers/model": instruction_model_payload,
        "/instruction-layers/effective": instruction_effective_payload,
        "/instruction-profiles": instruction_profiles_payload,
        "/instruction-overlays": instruction_overlays_payload,
        "/tasks/autopilot/status": {
            "running": False,
            "max_concurrency": 2,
            "security_level": "safe",
            "budget_label": "daily-default",
        },
        "/tasks/auto-planner/status": {"enabled": True, "last_plan_at": "2026-04-24T20:00:00Z"},
        "/triggers/status": {"enabled": True, "sources": ["webhook", "schedule"]},
        "/approvals": {
            "items": [
                {
                    "id": "AP-1",
                    "scope": "task_proposal",
                    "state": "pending",
                    "risk_level": "high",
                    "task_id": "T-1",
                    "goal_id": "G-1",
                },
                {
                    "id": "AP-2",
                    "scope": "task_proposal",
                    "state": "stale",
                    "risk_level": "medium",
                    "task_id": "T-2",
                    "goal_id": "G-1",
                },
                {
                    "id": "AP-3",
                    "scope": "task_proposal",
                    "state": "denied",
                    "risk_level": "critical",
                    "task_id": "T-3",
                    "goal_id": "G-1",
                },
            ]
        },
        "/repairs": {
            "items": [
                {
                    "session_id": "R-1",
                    "diagnosis": "disk pressure",
                    "proposed_steps": ["clean temp data"],
                    "risk_level": "high",
                    "dry_run_status": "available",
                    "approval_state": "pending",
                    "execution_result": "not_started",
                    "verification_result": "pending",
                    "outcome": "not_executed",
                    "blocked_reason": "approval_required",
                }
            ]
        },
    }

    return FixturePayloads(
        config=config_payload,
        goal_detail=goal_detail_payload,
        goal_plan=goal_plan_payload,
        goal_governance=goal_governance_payload,
        task_detail=task_detail_payload,
        task_detail_stale=task_detail_stale_payload,
        task_detail_denied=task_detail_denied_payload,
        task_logs=task_logs_payload,
        artifact_detail=artifact_detail_payload,
        artifact_rag_status=artifact_rag_status_payload,
        artifact_rag_preview=artifact_rag_preview_payload,
        knowledge_collection_detail=knowledge_collection_detail_payload,
        knowledge_search=knowledge_search_payload,
        blueprint_detail=blueprint_detail_payload,
        static_endpoints=fixture_payloads,
    )
