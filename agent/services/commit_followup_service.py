from __future__ import annotations

import uuid
from typing import Any


_GIT_COMMIT_KINDS = {"coding", "ops"}


def _auto_commit_enabled(goal_config: dict) -> bool:
    policy = dict((goal_config or {}).get("git_workspace") or {})
    return bool(policy.get("auto_commit", False))


def maybe_create_git_commit_followup(
    *,
    task: dict[str, Any],
    task_queue_service: Any,
    actor: str = "hub",
) -> dict[str, Any] | None:
    """Create a git_commit followup task if the completed task has commit_metadata.

    Returns the created task dict or None if no followup was created.
    """
    task_kind = str(task.get("task_kind") or "").strip().lower()
    commit_metadata = task.get("commit_metadata")
    effective_config = dict(task.get("effective_config") or {})

    if not commit_metadata:
        return None
    if task_kind and task_kind not in _GIT_COMMIT_KINDS and task_kind != "":
        return None
    if not _auto_commit_enabled(effective_config):
        return None

    followup_id = f"sub-commit-{uuid.uuid4()}"
    commit_type = str((commit_metadata or {}).get("commit_type") or "chore")
    commit_scope = str((commit_metadata or {}).get("commit_scope") or "")
    hint = str((commit_metadata or {}).get("commit_subject_hint") or "").strip()

    scope_part = f"({commit_scope})" if commit_scope else ""
    desc = f"git_commit: {commit_type}{scope_part}: {hint}" if hint else f"git_commit: {commit_type}{scope_part}"

    task_queue_service.ingest_task(
        task_id=followup_id,
        status="todo",
        title=desc[:200],
        description=desc,
        priority=str(task.get("priority") or "medium"),
        created_by=actor,
        source="hub_commit_followup",
        team_id=task.get("team_id"),
        goal_id=task.get("goal_id"),
        event_type="task_ingested",
        event_channel="commit_followup",
        event_details={"parent_task_id": task.get("id")},
        extra_fields={
            "task_kind": "git_commit",
            "parent_task_id": task.get("id"),
            "source_task_id": task.get("id"),
            "derivation_reason": "auto_commit_followup",
            "commit_metadata": commit_metadata,
        },
    )
    return {"id": followup_id, "task_kind": "git_commit"}
