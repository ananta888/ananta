from __future__ import annotations

import secrets
import time
from typing import Any

from agent.common.audit import log_audit
from agent.config import settings
from agent.db_models import TerminalEventDB, TerminalSessionDB
from agent.services.repository_registry import get_repository_registry
from agent.services.terminal_policy_service import get_terminal_policy_service
from agent.services.tmux_backend import TmuxBackendError, get_tmux_session_backend

# short-lived attach tokens: {token: (session_id, user_id, expires_at)}
_ATTACH_TOKENS: dict[str, tuple[str, str, float]] = {}


class TerminalSessionService:
    def list_sessions(self, *, user_ctx: dict[str, Any]) -> list[TerminalSessionDB]:
        registry = get_repository_registry()
        user_id = str(user_ctx.get("sub") or user_ctx.get("username") or "")
        is_admin = str(user_ctx.get("role") or "").lower() == "admin"
        if is_admin:
            return registry.terminal_session_repo.list_all()
        return registry.terminal_session_repo.list_by_user_id(user_id)

    def get_session(self, session_id: str, *, user_ctx: dict[str, Any]) -> TerminalSessionDB | None:
        registry = get_repository_registry()
        entry = registry.terminal_session_repo.get_by_id(session_id)
        if entry is None:
            return None
        user_id = str(user_ctx.get("sub") or user_ctx.get("username") or "")
        is_admin = str(user_ctx.get("role") or "").lower() == "admin"
        if not is_admin and entry.created_by_user_id != user_id:
            return None
        return entry

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
        idle_ttl = int(settings.terminal_idle_timeout_seconds or 900)
        max_ttl = int(settings.terminal_max_lifetime_seconds or 14400)
        is_hub = str(target_type) in {"hub", "hub_as_worker"}
        recording = bool(settings.terminal_recording_enabled_remote) if is_hub else False
        created_by = str(user_ctx.get("sub") or user_ctx.get("username") or "anonymous")
        session_entry = TerminalSessionDB(
            created_at=now,
            updated_at=now,
            expires_at=now + max_ttl,
            idle_expires_at=now + idle_ttl,
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
            recording_enabled=recording,
            policy_decision_id=policy.decision_id,
            risk_class="terminal_hub_runtime_access" if is_hub else "terminal_workspace_mutation",
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

    def send_input(
        self,
        session_id: str,
        *,
        text: str,
        user_ctx: dict[str, Any],
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self.get_session(session_id, user_ctx=user_ctx)
        if entry is None:
            return {"ok": False, "status": "not_found", "reason_code": "terminal_session_not_found"}
        if entry.status not in {"running", "attached"}:
            return {"ok": False, "status": "error", "reason_code": "terminal_session_not_active"}
        if entry.read_only:
            return {"ok": False, "status": "forbidden", "reason_code": "terminal_session_read_only"}

        policy = get_terminal_policy_service().evaluate(
            user_ctx=user_ctx,
            operation="write",
            target_type=entry.target_type,
            target_id=entry.target_id,
            session_id=session_id,
            cfg=cfg,
        )
        registry = get_repository_registry()
        if not policy.allow:
            registry.terminal_event_repo.append(
                TerminalEventDB(
                    session_id=session_id,
                    user_id=str(user_ctx.get("sub") or "anonymous"),
                    event_type="policy_denied",
                    target_type=entry.target_type,
                    target_id=entry.target_id,
                    operation="write",
                    allowed=False,
                    reason_code=policy.reason_code,
                    summary="input write denied by policy",
                )
            )
            return {"ok": False, "status": "forbidden", "reason_code": policy.reason_code}

        backend = get_tmux_session_backend()
        try:
            backend.send_input(session_name=str(entry.tmux_session_name or ""), text=text)
        except TmuxBackendError as exc:
            return {"ok": False, "status": "backend_error", "reason_code": str(exc)}

        now = time.time()
        idle_ttl = int(settings.terminal_idle_timeout_seconds or 900)
        entry.last_input_at = now
        entry.idle_expires_at = now + idle_ttl
        registry.terminal_session_repo.save(entry)

        registry.terminal_event_repo.append(
            TerminalEventDB(
                session_id=session_id,
                user_id=str(user_ctx.get("sub") or "anonymous"),
                event_type="session_input",
                target_type=entry.target_type,
                target_id=entry.target_id,
                operation="write",
                allowed=True,
                reason_code="terminal_input_sent",
                summary="input sent to session",
            )
        )
        return {"ok": True}

    def get_output(
        self,
        session_id: str,
        *,
        user_ctx: dict[str, Any],
        cfg: dict[str, Any] | None = None,
        lines: int = 200,
    ) -> dict[str, Any]:
        entry = self.get_session(session_id, user_ctx=user_ctx)
        if entry is None:
            return {"ok": False, "status": "not_found", "reason_code": "terminal_session_not_found"}

        policy = get_terminal_policy_service().evaluate(
            user_ctx=user_ctx,
            operation="read",
            target_type=entry.target_type,
            target_id=entry.target_id,
            session_id=session_id,
            cfg=cfg,
        )
        if not policy.allow:
            return {"ok": False, "status": "forbidden", "reason_code": policy.reason_code}

        backend = get_tmux_session_backend()
        try:
            raw = backend.capture_output(session_name=str(entry.tmux_session_name or ""), lines=lines)
        except TmuxBackendError as exc:
            return {"ok": False, "status": "backend_error", "reason_code": str(exc)}

        from agent.services.terminal_recording_service import redact_secrets
        output = redact_secrets(raw)

        now = time.time()
        idle_ttl = int(settings.terminal_idle_timeout_seconds or 900)
        entry.last_output_at = now
        entry.idle_expires_at = now + idle_ttl
        get_repository_registry().terminal_session_repo.save(entry)

        get_repository_registry().terminal_event_repo.append(
            TerminalEventDB(
                session_id=session_id,
                user_id=str(user_ctx.get("sub") or "anonymous"),
                event_type="session_output_read",
                target_type=entry.target_type,
                target_id=entry.target_id,
                operation="read",
                allowed=True,
                reason_code="terminal_output_read",
                summary="terminal output captured",
            )
        )
        return {"ok": True, "output": output}

    def generate_attach_token(
        self,
        session_id: str,
        *,
        user_ctx: dict[str, Any],
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self.get_session(session_id, user_ctx=user_ctx)
        if entry is None:
            return {"ok": False, "status": "not_found", "reason_code": "terminal_session_not_found"}

        policy = get_terminal_policy_service().evaluate(
            user_ctx=user_ctx,
            operation="attach",
            target_type=entry.target_type,
            target_id=entry.target_id,
            session_id=session_id,
            cfg=cfg,
        )
        if not policy.allow:
            return {"ok": False, "status": "forbidden", "reason_code": policy.reason_code}

        ttl = int(settings.terminal_attach_token_ttl_seconds or 60)
        token = secrets.token_urlsafe(32)
        user_id = str(user_ctx.get("sub") or user_ctx.get("username") or "anonymous")
        expires_at = time.time() + ttl
        _ATTACH_TOKENS[token] = (session_id, user_id, expires_at)

        get_repository_registry().terminal_event_repo.append(
            TerminalEventDB(
                session_id=session_id,
                user_id=user_id,
                event_type="session_attach_requested",
                target_type=entry.target_type,
                target_id=entry.target_id,
                operation="attach",
                allowed=True,
                reason_code="terminal_attach_token_issued",
                summary="attach token issued",
                metadata_json={"expires_at": expires_at},
            )
        )
        return {"ok": True, "attach_token": token, "expires_at": expires_at, "ttl_seconds": ttl}

    def resolve_attach_token(self, token: str) -> tuple[str, str] | None:
        """Return (session_id, user_id) if token is valid and not expired."""
        record = _ATTACH_TOKENS.get(token)
        if record is None:
            return None
        session_id, user_id, expires_at = record
        if time.time() > expires_at:
            del _ATTACH_TOKENS[token]
            return None
        del _ATTACH_TOKENS[token]  # single-use
        return session_id, user_id

    def kill_session(
        self,
        session_id: str,
        *,
        user_ctx: dict[str, Any],
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self.get_session(session_id, user_ctx=user_ctx)
        if entry is None:
            return {"ok": False, "status": "not_found", "reason_code": "terminal_session_not_found"}

        policy = get_terminal_policy_service().evaluate(
            user_ctx=user_ctx,
            operation="kill",
            target_type=entry.target_type,
            target_id=entry.target_id,
            session_id=session_id,
            cfg=cfg,
        )
        registry = get_repository_registry()
        if not policy.allow:
            registry.terminal_event_repo.append(
                TerminalEventDB(
                    session_id=session_id,
                    user_id=str(user_ctx.get("sub") or "anonymous"),
                    event_type="policy_denied",
                    target_type=entry.target_type,
                    target_id=entry.target_id,
                    operation="kill",
                    allowed=False,
                    reason_code=policy.reason_code,
                    summary="kill denied by policy",
                )
            )
            return {"ok": False, "status": "forbidden", "reason_code": policy.reason_code}

        if entry.tmux_session_name:
            backend = get_tmux_session_backend()
            try:
                backend.kill_session(session_name=entry.tmux_session_name)
            except TmuxBackendError:
                pass  # already gone — still mark as killed

        registry.terminal_session_repo.transition_status(session_id, "killed")
        registry.terminal_event_repo.append(
            TerminalEventDB(
                session_id=session_id,
                user_id=str(user_ctx.get("sub") or "anonymous"),
                event_type="session_killed",
                target_type=entry.target_type,
                target_id=entry.target_id,
                operation="kill",
                allowed=True,
                reason_code="terminal_session_killed",
                summary="session killed",
            )
        )
        log_audit("terminal_session_killed", {"session_id": session_id, "target_type": entry.target_type})
        return {"ok": True}


_SERVICE = TerminalSessionService()


def get_terminal_session_service() -> TerminalSessionService:
    return _SERVICE
