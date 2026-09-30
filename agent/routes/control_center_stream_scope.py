"""Tenant/user/project/session scoping for the Control-Center event stream.

Resolves which stream identity may observe an event, strictly from Hub-owned
agent-session records. The resolver owns no event log or poller state; the
Control-Center API injects the repository provider.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.db_models import AgentSessionDB, TaskDB


def session_stream_scope(session: AgentSessionDB) -> dict[str, Any]:
    owner_user_id = str(getattr(session, "owner_user_id", "") or "").strip()
    permissions = dict(getattr(session, "permissions", None) or {})
    persisted_scope = permissions.get("_control_center_stream_scope")
    if not isinstance(persisted_scope, dict):
        persisted_scope = {}
    persisted_user_id = str(persisted_scope.get("user_id") or "").strip()
    persisted_tenant_id = str(persisted_scope.get("tenant_id") or "").strip()
    tenant_id = (
        persisted_tenant_id
        if persisted_user_id == owner_user_id and persisted_tenant_id
        else owner_user_id
    )
    return {
        "tenant_id": tenant_id,
        "user_id": owner_user_id,
        "project_id": str(getattr(session, "team_id", "") or "").strip(),
        "session_ids": (str(getattr(session, "id", "") or "").strip(),),
    }


def event_visible_to_stream_identity(
    event: dict[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    project_id: str,
    session_id: str,
) -> bool:
    scope = event.get("_scope")
    if not isinstance(scope, dict):
        return False
    if str(scope.get("tenant_id") or "") != tenant_id:
        return False
    if str(scope.get("user_id") or "") != user_id:
        return False
    if project_id and str(scope.get("project_id") or "") != project_id:
        return False
    session_ids = {
        str(item).strip()
        for item in scope.get("session_ids") or ()
        if str(item).strip()
    }
    return not session_id or session_id in session_ids


class ControlCenterStreamScopeResolver:
    """Derive stream scopes for tasks, policy decisions and stream requests."""

    def __init__(self, *, repository_provider: Callable[[], Any]) -> None:
        self._repository_provider = repository_provider

    def task_scopes(self, task: TaskDB) -> tuple[dict[str, Any], ...]:
        grouped: dict[tuple[str, str, str], set[str]] = {}
        for session in self._repository_provider().agent_session_repo.get_by_task_id(str(task.id or "")):
            scope = session_stream_scope(session)
            if not scope["tenant_id"] or not scope["user_id"]:
                continue
            key = (scope["tenant_id"], scope["user_id"], scope["project_id"])
            grouped.setdefault(key, set()).update(scope["session_ids"])
        return tuple(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "project_id": project_id,
                "session_ids": tuple(sorted(session_ids)),
            }
            for (tenant_id, user_id, project_id), session_ids in sorted(grouped.items())
        )

    def policy_scopes(self, decision: Any) -> tuple[dict[str, Any], ...]:
        details = dict(getattr(decision, "details", None) or {})
        session_id = str(details.get("session_id") or "").strip()
        if session_id:
            session = self._repository_provider().agent_session_repo.get_by_id(session_id)
            return (session_stream_scope(session),) if session is not None else ()
        task_id = str(getattr(decision, "task_id", "") or "").strip()
        task = self._repository_provider().task_repo.get_by_id(task_id) if task_id else None
        return self.task_scopes(task) if task is not None else ()

    def authorize(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        session_id: str,
    ) -> tuple[bool, str, str]:
        """Resolve a requested stream scope only from Hub-owned session records."""

        if session_id:
            session = self._repository_provider().agent_session_repo.get_by_id(session_id)
            if session is None:
                return False, "", ""
            scope = session_stream_scope(session)
            if scope["tenant_id"] != tenant_id or scope["user_id"] != user_id:
                return False, "", ""
            resolved_project = str(scope["project_id"] or "")
            if project_id and project_id != resolved_project:
                return False, "", ""
            return True, project_id or resolved_project, session_id

        if project_id:
            for session in self._repository_provider().agent_session_repo.get_all() or ():
                scope = session_stream_scope(session)
                if (
                    scope["tenant_id"] == tenant_id
                    and scope["user_id"] == user_id
                    and scope["project_id"] == project_id
                ):
                    return True, project_id, ""
            return False, "", ""

        # A user-wide stream is still explicitly bound to its signed user/tenant
        # pair and sees only events projected from that user's Hub sessions.
        return True, "", ""
