from __future__ import annotations

import time
from typing import Any

from agent.common.audit import log_audit
from agent.db_models import TerminalEventDB, TerminalSessionDB
from agent.services.repository_registry import get_repository_registry
from agent.services.terminal_policy_service import get_terminal_policy_service
from agent.services.tmux_backend import TmuxBackendError, get_tmux_session_backend


class TerminalSessionService:
    def list_sessions(self, *, user_ctx: dict[str, Any]) -> list[TerminalSessionDB]:
        registry = get_repository_registry()
        user_id = str(user_ctx.get("sub") or user_ctx.get("username") or "")
        is_admin = str(user_ctx.get("role") or "").lower() == "admin"
        if is_admin:
            return registry.terminal_session_repo.list_all()
        return registry.terminal_session_repo.list_by_user_id(user_id)

    def create_session(
        self,
        *,
        user_ctx: dict[str, Any],
        target_type: str,
        target_id: str,
        workspace_path: str | None = None,
        goal_id: str | None = None,
        task_id: str | None = None,
        read_only: bool = False,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = get_terminal_policy_service().evaluate(
            user_ctx=user_ctx,
            operation="create",
            target_type=target_type,
            target_id=target_id,
            cfg=cfg,
        )
        registry = get_repository_registry()

        event_requested = TerminalEventDB(
            session_id="pending",
            user_id=str(user_ctx.get("sub") or user_ctx.get("username") or "anonymous"),
            event_type="session_create_requested",
            target_type=str(target_type),
            target_id=str(target_id),
            operation="create",
            allowed=policy.allow,
            reason_code=policy.reason_code,
            summary="terminal session create requested",
            metadata_json={"policy_decision_id": policy.decision_id},
        )
        registry.terminal_event_repo.append(event_requested)

        if not policy.allow:
            return {
                "ok": False,
                "status": "forbidden",
                "reason_code": policy.reason_code,
                "policy_decision_id": policy.decision_id,
            }

        backend = get_tmux_session_backend()
        try:
            backend_session = backend.create_session(name_hint=f"{target_type}-{target_id}", cwd=workspace_path)
        except TmuxBackendError as exc:
            return {
                "ok": False,
                "status": "backend_error",
                "reason_code": str(exc),
                "policy_decision_id": policy.decision_id,
            }

        now = time.time()
        created_by = str(user_ctx.get("sub") or user_ctx.get("username") or "anonymous")
        session_entry = TerminalSessionDB(
            created_at=now,
            updated_at=now,
            created_by_user_id=created_by,
            created_by_username=str(user_ctx.get("username") or created_by),
            auth_source="user_jwt",
            target_type=str(target_type),
            target_id=str(target_id),
            target_display_name=str(target_id),
            workspace_path=str(workspace_path) if workspace_path else None,
            goal_id=goal_id,
            task_id=task_id,
            tmux_session_name=backend_session.tmux_session_name,
            status="running",
            read_only=bool(read_only),
            recording_enabled=False,
            policy_decision_id=policy.decision_id,
            risk_class="terminal_hub_runtime_access" if str(target_type) in {"hub", "hub_as_worker"} else "terminal_workspace_mutation",
            metadata_json={"pane_target": backend_session.pane_target},
        )
        saved = registry.terminal_session_repo.save(session_entry)

        registry.terminal_event_repo.append(
            TerminalEventDB(
                session_id=saved.id,
                user_id=created_by,
                event_type="session_created",
                target_type=saved.target_type,
                target_id=saved.target_id,
                operation="create",
                allowed=True,
                reason_code="terminal_session_created",
                summary="terminal session created",
                metadata_json={"tmux_session_name": saved.tmux_session_name},
            )
        )
        log_audit(
            "terminal_session_created",
            {
                "session_id": saved.id,
                "target_type": saved.target_type,
                "target_id": saved.target_id,
                "policy_decision_id": policy.decision_id,
                "task_id": saved.task_id,
                "goal_id": saved.goal_id,
            },
        )

        return {"ok": True, "session": saved.model_dump()}


_SERVICE = TerminalSessionService()


def get_terminal_session_service() -> TerminalSessionService:
    return _SERVICE
