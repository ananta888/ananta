"""Ananta audit-log provenance for the Git Ops activity feed.

Reads persisted Git audit events and attributes them to one registered
workspace. Persistence access is isolated here so the read service only
depends on the small ``GitAuditActivitySource`` port (DIP).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from agent.common.audit import log_audit
from agent.services.git_audit_service import git_workspace_fingerprint
from agent.services.ops_models import GitActivityEvent
from agent.services.ops_registry_service import WorkspaceRef

_GIT_AUDIT_ACTIONS = (
    "ops_git_stage",
    "ops_git_unstage",
    "ops_git_discard",
    "ops_git_commit",
    "ops_git_fetch",
    "ops_git_pull",
    "ops_git_push",
    "workspace_git_commit_push",
    "git_commit",
    "git_push",
)


class GitAuditActivitySource(Protocol):
    def events(self, workspace: WorkspaceRef, limit: int) -> list[GitActivityEvent]: ...


class GitAuditRecorder(Protocol):
    def __call__(self, action: str, **details: Any) -> str: ...


def record_git_audit(action: str, **details: Any) -> str:
    """Write one Git Ops audit event and return its correlation reference."""

    audit_ref = f"git-{uuid.uuid4().hex[:16]}"
    log_audit(action, {"audit_ref": audit_ref, **details})
    return audit_ref


class AuditLogGitActivitySource:
    """Projects ``AuditLogDB`` rows into workspace-scoped activity events."""

    def __init__(self, *, fingerprint: Callable[[Any], str] = git_workspace_fingerprint) -> None:
        self._fingerprint = fingerprint

    def events(self, workspace: WorkspaceRef, limit: int) -> list[GitActivityEvent]:
        try:
            from sqlmodel import Session, select

            from agent.database import engine
            from agent.db_models import AuditLogDB

            with Session(engine) as session:
                rows = session.exec(
                    select(AuditLogDB)
                    .where(AuditLogDB.action.in_(_GIT_AUDIT_ACTIONS))  # type: ignore[attr-defined]
                    .order_by(AuditLogDB.timestamp.desc())  # type: ignore[attr-defined]
                    .limit(limit)
                ).all()
            events: list[GitActivityEvent] = []
            workspace_id = workspace.workspace_id
            workspace_fingerprint = self._fingerprint(workspace.root)
            for row in rows:
                details = dict(row.details or {})
                row_workspace = str(details.get("workspace_id") or "")
                row_fingerprint = str(details.get("workspace_fingerprint") or "")
                if row_workspace and row_workspace != workspace_id:
                    continue
                if row_fingerprint and row_fingerprint != workspace_fingerprint:
                    continue
                if not row_workspace and not row_fingerprint:
                    # Legacy global git_commit/git_push events cannot safely be
                    # attributed to a particular registered workspace.
                    continue
                events.append(_activity_event(row, details, workspace_id))
            return events
        except Exception:
            return []


def _activity_event(row: Any, details: dict[str, Any], workspace_id: str) -> GitActivityEvent:
    timestamp = datetime.fromtimestamp(float(row.timestamp), tz=UTC).isoformat().replace("+00:00", "Z")
    explicit_outcome = str(details.get("outcome") or "").strip().lower()
    if explicit_outcome:
        outcome = explicit_outcome
    elif "ok" in details:
        outcome = "success" if bool(details.get("ok")) else "failed"
    else:
        outcome = "observed"
    operation = str(
        details.get("operation")
        or ("commit_push" if row.action == "workspace_git_commit_push" else row.action)
        or "git"
    )
    summary = str(details.get("summary") or "").strip()
    if not summary:
        parts = [outcome]
        if details.get("branch"):
            parts.append(f"branch={details['branch']}")
        commit_sha = str(details.get("commit_sha") or "")
        if commit_sha:
            parts.append(f"commit={commit_sha[:12]}")
        summary = ", ".join(parts)
    return GitActivityEvent(
        id=f"audit-{row.id}",
        timestamp=timestamp,
        actor=str(row.username or "ananta"),
        operation=operation,
        action=str(row.action or "git"),
        outcome=outcome,
        source="ananta_audit",
        workspace_id=workspace_id,
        task_id=str(row.task_id or ""),
        goal_id=str(row.goal_id or ""),
        trace_id=str(row.trace_id or ""),
        approval_id=str(details.get("approval_id") or ""),
        summary=summary or str(row.action or "Git action"),
    )
