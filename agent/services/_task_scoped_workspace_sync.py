"""Workspace state sync record builder for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as part of
SPLIT-001 (refactor of the 4783-LOC monolith). This module owns exactly one
concern: translating a (task, materialization manifest, artifact refs, git-pushed)
tuple into the ``workspace_state_sync`` payload that downstream consumers
(persistence, audit, task status) rely on.

Single Responsibility: build the sync-record payload. No policy enforcement,
no DB writes, no remote calls.

Backward compatibility: ``agent.services.task_scoped_execution_service`` keeps
re-exporting ``_build_workspace_state_sync_record`` for 12 months (see the
deprecation shim in that file). Tests that import the symbol directly continue
to work; new code should import from this module.
"""
from __future__ import annotations

from typing import Any


def build_workspace_state_sync_record(
    *,
    task: dict,
    materialization_manifest: object,
    workspace_artifact_refs: list,
    git_pushed: bool,
) -> dict[str, Any]:
    """Build the ``workspace_state_sync`` payload for a task execution.

    Args:
        task: The task dict. Used to resolve the sync policy.
        materialization_manifest: A list of dicts (or None) describing the
            artifacts materialized into the workspace. Each entry must contain
            ``artifact_id`` and ``workspace_relative_path``.
        workspace_artifact_refs: A list of dicts describing artifact refs the
            task produced. Only entries with ``kind == "workspace_file"`` are
            recorded as outputs.
        git_pushed: Whether the workspace was pushed to a remote.

    Returns:
        A dict with keys ``sync_mode``, ``source_of_truth``, ``input_artifacts``,
        ``output_artifacts``, ``git_pushed``. On any internal error, returns a
        safe fallback (``sync_mode='none'``) so the caller can persist a
        degraded record rather than crash the task execution.
    """
    try:
        from agent.services.workspace_state_sync_policy import (
            WorkspaceStateSyncPolicy,
        )

        policy = WorkspaceStateSyncPolicy.resolve(task)
        input_artifacts = [
            {
                "artifact_id": r.get("artifact_id"),
                "path": r.get("workspace_relative_path"),
            }
            for r in (materialization_manifest or [])
            if isinstance(r, dict)
        ]
        output_artifacts = [
            {
                "artifact_id": r.get("artifact_id"),
                "path": r.get("workspace_relative_path"),
            }
            for r in (workspace_artifact_refs or [])
            if isinstance(r, dict) and r.get("kind") == "workspace_file"
        ]
        return {
            "sync_mode": policy.sync_mode,
            "source_of_truth": policy.source_of_truth,
            "input_artifacts": input_artifacts,
            "output_artifacts": output_artifacts,
            "git_pushed": git_pushed,
        }
    except Exception:
        return {
            "sync_mode": "none",
            "source_of_truth": "task_local",
            "input_artifacts": [],
            "output_artifacts": [],
            "git_pushed": git_pushed,
        }


# Public alias that matches the historical symbol name in the parent module.
# The shim re-exports both names for backward compatibility.
_build_workspace_state_sync_record = build_workspace_state_sync_record
