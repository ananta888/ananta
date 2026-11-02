"""Pure read-model builders for artifact-first task status and preview.

CLN-031 extracted the tested logic from the never-registered
``artifact_status_bp`` blueprint before the dead HTTP surface was removed.
The builders are pure functions over a task dict: no Flask, no repository
access, no I/O (SRP). Callers fetch the task and pass ``model_dump()``.

The artifact reconciliation itself lives in
``agent.services.artifact_reconciliation_service``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_task_artifact_preview", "build_task_artifact_status"]

_ARTIFACT_COMPLETION_EVENTS = (
    "artifact_first_completion",
    "artifact_first_finalization",
    "artifact_reconciliation_applied",
)


def build_task_artifact_status(task_dict: dict[str, Any], tid: str) -> dict[str, Any]:
    """Return artifact-first completion status for a task (FA-T019 fields).

    Includes propose-strategy observability, artifact summary
    (completion_decision, reason_codes, manifest/verification status) and the
    last artifact-first completion event. Never includes artifact content.
    """
    verification_status = dict(task_dict.get("verification_status") or {})

    artifact_completion: dict[str, Any] | None = None
    for evt in reversed(list(task_dict.get("history") or [])):
        if isinstance(evt, dict) and evt.get("event_type") in _ARTIFACT_COMPLETION_EVENTS:
            artifact_completion = dict(evt.get("details") or {})
            break

    last_proposal = dict(task_dict.get("last_proposal") or {})
    routing = dict(last_proposal.get("routing") or {})
    propose_strategy_meta = dict(routing.get("propose_strategy_meta") or {})

    if not propose_strategy_meta:
        for evt in reversed(list(task_dict.get("history") or [])):
            if isinstance(evt, dict) and evt.get("event_type") == "proposal_result":
                candidate = dict(evt.get("propose_strategy_meta") or {})
                if candidate:
                    propose_strategy_meta = candidate
                    break

    return {
        "task_id": tid,
        "status": task_dict.get("status"),
        # Propose strategy observability (FA-T019)
        "attempted_strategies": propose_strategy_meta.get("attempted_strategies") or [],
        "selected_strategy": propose_strategy_meta.get("selected_strategy"),
        "proposal_status": propose_strategy_meta.get("proposal_status"),
        "proposal_reason": propose_strategy_meta.get("proposal_reason") or last_proposal.get("reason"),
        "normalization_format": propose_strategy_meta.get("normalization_format"),
        "effective_propose_policy": routing.get("task_kind"),
        # Artifact completion observability
        "artifact_summary": {
            "completion_decision": (artifact_completion or {}).get("completion_decision"),
            "reason_codes": (artifact_completion or {}).get("reason_codes") or [],
            "manifest_status": "valid" if (artifact_completion or {}).get("manifest_id") else "unknown",
            "artifact_ids": (artifact_completion or {}).get("artifact_ids") or [],
            "manifest_id": (artifact_completion or {}).get("manifest_id"),
            "verification_status": verification_status.get("status"),
        },
        "verification_status": verification_status.get("status"),
        "advisory_parse_status": (artifact_completion or {}).get("advisory_parse_status"),
        "artifact_first_completion": artifact_completion,
        "completion_decision": (artifact_completion or {}).get("completion_decision"),
    }


def build_task_artifact_preview(
    task_dict: dict[str, Any],
    tid: str,
    agent_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview expected artifacts and completion rules before execution (AFH-T022).

    Does NOT execute a worker. ``agent_cfg`` mirrors the app config shape
    ``AGENT_CONFIG["worker_runtime"]["todo_contract"]``.
    """
    workspace_dir = str(task_dict.get("workspace_dir") or "")
    todo_contract = dict(task_dict.get("todo_contract") or {})
    todo_tasks = list((todo_contract.get("todo") or {}).get("tasks") or [])

    expected_artifacts: list[dict[str, Any]] = []
    for t in todo_tasks:
        for a in list(t.get("expected_artifacts") or []):
            expected_artifacts.append(
                {
                    "kind": str(a.get("kind") or ""),
                    "relative_path": str(a.get("relative_path") or a.get("description") or ""),
                    "required": bool(a.get("required", False)),
                    "description": str(a.get("description") or ""),
                }
            )

    runtime_cfg = (agent_cfg or {}).get("worker_runtime") or {}
    todo_cfg = (runtime_cfg.get("todo_contract") or {}) if isinstance(runtime_cfg, dict) else {}
    planner_llm_mode = "disabled" if not todo_cfg.get("planner_llm_enabled", False) else "enabled"

    return {
        "task_id": tid,
        "expected_artifacts": expected_artifacts,
        "manifest_output_path": ".ananta/handoff/<execution_id>/artifact_manifest.v1.json",
        "completion_policy_summary": {
            "completion_on_valid_manifest": True,
            "verification_required": bool(task_dict.get("verification_spec", {}) or {}),
            "allow_synthesized_manifest": False,
            "max_retries": 3,
        },
        "verification_requirements": {
            "required": bool(task_dict.get("verification_spec")),
            "spec": task_dict.get("verification_spec") or {},
        },
        "planner_llm_mode": planner_llm_mode,
        "workspace_output_boundary": workspace_dir or "<workspace_dir not set>",
        "note": "This preview does not execute the worker.",
    }
