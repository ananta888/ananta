from __future__ import annotations

import logging
import time
from typing import Any

from agent.common.audit import log_audit
from agent.db_models import TerminalEventDB, TerminalSessionDB
from agent.services.repository_registry import get_repository_registry
from agent.services.tmux_backend import TmuxBackendError, get_tmux_session_backend

LOGGER = logging.getLogger("agent.terminal_cleanup")

_ACTIVE_STATUSES = {"created", "running", "attached", "detached"}


class TerminalCleanupService:
    def run_cleanup_tick(self, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        """Expire and kill sessions that have exceeded idle or max lifetime limits."""
        registry = get_repository_registry()
        sessions: list[TerminalSessionDB] = registry.terminal_session_repo.list_all()
        now = time.time()
        expired_ids: list[str] = []
        errors: list[str] = []

        for session in sessions:
            if session.status not in _ACTIVE_STATUSES:
                continue

            reason: str | None = None
            if session.expires_at and now >= session.expires_at:
                reason = "terminal_max_lifetime_exceeded"
            elif session.idle_expires_at and now >= session.idle_expires_at:
                reason = "terminal_idle_timeout_exceeded"

            if reason is None:
                continue

            try:
                self._expire_session(session, reason=reason, registry=registry)
                expired_ids.append(session.id)
            except Exception as exc:
                LOGGER.warning("cleanup failed for session %s: %s", session.id, exc)
                errors.append(session.id)

        return {"expired": expired_ids, "errors": errors}

    def _expire_session(self, session: TerminalSessionDB, *, reason: str, registry: Any) -> None:
        if session.tmux_session_name:
            try:
                get_tmux_session_backend().kill_session(session_name=session.tmux_session_name)
            except TmuxBackendError:
                pass  # already gone

        registry.terminal_session_repo.transition_status(session.id, "expired")

        registry.terminal_event_repo.append(
            TerminalEventDB(
                session_id=session.id,
                user_id=session.created_by_user_id,
                event_type="session_expired",
                target_type=session.target_type,
                target_id=session.target_id,
                operation="cleanup",
                allowed=True,
                reason_code=reason,
                summary=f"session expired: {reason}",
                metadata_json={"tmux_session_name": session.tmux_session_name},
            )
        )
        log_audit("terminal_session_expired", {"session_id": session.id, "reason": reason})
        LOGGER.info("terminal session %s expired (%s)", session.id, reason)


_SERVICE = TerminalCleanupService()


def get_terminal_cleanup_service() -> TerminalCleanupService:
    return _SERVICE
