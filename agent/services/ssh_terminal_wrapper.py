"""Server-side SSH terminal wrapper — ForceCommand-style entry point.

sshd sets ForceCommand to this wrapper. It reads the validated SSH certificate
identity from the SSH_ORIGINAL_COMMAND / SSH_USER_AUTH environment, calls
TerminalPolicyService, and starts a controlled tmux session only after approval.

Never opens a raw shell for managed terminal accounts.
Never exposes raw tmux sockets.
Path traversal for workspace paths is rejected.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger("agent.ssh_terminal_wrapper")

_SAFE_SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_SAFE_TARGET_ID = re.compile(r"^[a-zA-Z0-9_.-]{1,128}$")
_SAFE_TARGET_TYPE = {"worker", "hub", "hub_as_worker"}
_WORKSPACE_BASE = os.environ.get("ANANTA_WORKSPACE_BASE", "/workspace")


@dataclass(frozen=True)
class WrapperContext:
    user_id: str
    principal: str
    target_type: str
    target_id: str
    workspace_path: str | None
    goal_id: str | None
    task_id: str | None
    operation: str  # "create" or "attach"
    session_id: str | None


@dataclass(frozen=True)
class WrapperDecision:
    allowed: bool
    reason_code: str
    tmux_session_name: str | None


def _sanitize_path(raw: str | None) -> str | None:
    """Reject path traversal; resolve to absolute path under WORKSPACE_BASE."""
    if not raw:
        return None
    # Normalize and reject traversal
    resolved = os.path.normpath(raw)
    if ".." in resolved.split(os.sep):
        return None
    # Must stay under workspace base or be absolute within allowed mount
    if not os.path.isabs(resolved):
        resolved = os.path.join(_WORKSPACE_BASE, resolved)
        resolved = os.path.normpath(resolved)
    if not resolved.startswith(_WORKSPACE_BASE):
        return None
    return resolved


def _parse_env_context() -> WrapperContext | None:
    """Parse SSH session context from environment variables set by sshd."""
    user_id = os.environ.get("ANANTA_SSH_USER_ID", "").strip()
    principal = os.environ.get("ANANTA_SSH_PRINCIPAL", "").strip()
    target_type = os.environ.get("ANANTA_SSH_TARGET_TYPE", "worker").strip().lower()
    target_id = os.environ.get("ANANTA_SSH_TARGET_ID", "").strip()
    workspace_raw = os.environ.get("ANANTA_SSH_WORKSPACE", None)
    goal_id = os.environ.get("ANANTA_SSH_GOAL_ID", None) or None
    task_id = os.environ.get("ANANTA_SSH_TASK_ID", None) or None
    operation = os.environ.get("ANANTA_SSH_OPERATION", "create").strip().lower()
    session_id = os.environ.get("ANANTA_SSH_SESSION_ID", None) or None

    if not user_id or not principal:
        return None
    if target_type not in _SAFE_TARGET_TYPE:
        return None
    if target_id and not _SAFE_TARGET_ID.match(target_id):
        return None
    if operation not in ("create", "attach"):
        operation = "create"
    if session_id and not _SAFE_SESSION_ID.match(session_id):
        session_id = None

    workspace_path = _sanitize_path(workspace_raw)

    return WrapperContext(
        user_id=user_id,
        principal=principal,
        target_type=target_type,
        target_id=target_id or target_type,
        workspace_path=workspace_path,
        goal_id=goal_id,
        task_id=task_id,
        operation=operation,
        session_id=session_id,
    )


class AnantaSshTerminalWrapper:
    """ForceCommand wrapper that enforces Ananta policy before starting tmux."""

    def run(self, ctx: WrapperContext | None = None) -> int:
        """Main entry point. Returns exit code."""
        from agent.config import settings

        if not settings.native_ssh_enabled:
            self._print_error("ssh_wrapper_native_ssh_disabled")
            return 1

        if ctx is None:
            ctx = _parse_env_context()

        if ctx is None:
            self._print_error("ssh_wrapper_missing_identity_context")
            return 1

        # Reject direct shell requests for managed targets (ForceCommand means SSH_ORIGINAL_COMMAND is empty for direct shell)
        if settings.ssh_terminal_wrapper_required:
            original_cmd = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
            if original_cmd and not original_cmd.startswith("ananta-"):
                self._print_error("ssh_wrapper_direct_shell_denied")
                LOGGER.warning("Direct shell denied for user=%s principal=%s", ctx.user_id, ctx.principal)
                return 1

        user_ctx = self._build_user_ctx_from_principal(ctx)
        decision = self._evaluate_policy(ctx, user_ctx)

        if not decision.allowed:
            self._print_error(decision.reason_code)
            self._audit_denied(ctx, decision)
            return 1

        return self._start_tmux(ctx, decision, user_ctx)

    def _build_user_ctx_from_principal(self, ctx: WrapperContext) -> dict[str, Any]:
        """Reconstruct a minimal auth context from the SSH certificate principal."""
        # Derive target permissions from principal scoping
        target_type = ctx.target_type
        terminal_permissions: list[str] = []

        if target_type == "worker":
            terminal_permissions = [
                "terminal.worker.create", "terminal.worker.attach",
                "terminal.worker.read", "terminal.worker.write",
            ]
        elif target_type == "hub":
            terminal_permissions = [
                "terminal.hub.create", "terminal.hub.attach",
                "terminal.hub.read", "terminal.hub.write",
            ]
        elif target_type == "hub_as_worker":
            terminal_permissions = [
                "terminal.hub_as_worker.create", "terminal.hub_as_worker.attach",
            ]

        return {
            "sub": ctx.user_id,
            "username": ctx.user_id,
            "role": "user",
            "roles": ["user"],
            "terminal_permissions": terminal_permissions,
            "auth_source": "ssh_certificate",
            "ssh_principal": ctx.principal,
        }

    def _evaluate_policy(self, ctx: WrapperContext, user_ctx: dict[str, Any]) -> WrapperDecision:
        from agent.services.terminal_policy_service import get_terminal_policy_service

        # Worker principal must not access hub — double-check even if user_ctx already limits it
        if ctx.target_type in ("hub", "hub_as_worker"):
            if "ananta-worker" in ctx.principal and "ananta-hub" not in ctx.principal:
                return WrapperDecision(
                    allowed=False,
                    reason_code="ssh_wrapper_worker_principal_cannot_access_hub",
                    tmux_session_name=None,
                )

        policy = get_terminal_policy_service()
        decision = policy.evaluate(
            user_ctx=user_ctx,
            operation=ctx.operation,
            target_type=ctx.target_type,
            target_id=ctx.target_id,
        )

        if not decision.allow:
            return WrapperDecision(
                allowed=False,
                reason_code=decision.reason_code,
                tmux_session_name=None,
            )

        from agent.services.tmux_backend import TmuxSessionBackend
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", f"ananta-{ctx.target_type}-{ctx.user_id[:16]}")[:48]
        return WrapperDecision(
            allowed=True,
            reason_code="ok",
            tmux_session_name=safe_name,
        )

    def _start_tmux(
        self,
        ctx: WrapperContext,
        decision: WrapperDecision,
        user_ctx: dict[str, Any],
    ) -> int:
        from agent.services.terminal_session_service import TerminalSessionService

        svc = TerminalSessionService()
        try:
            result = svc.create_session(
                user_ctx=user_ctx,
                target_type=ctx.target_type,
                target_id=ctx.target_id,
                workspace_path=ctx.workspace_path,
                goal_id=ctx.goal_id,
                task_id=ctx.task_id,
                read_only=False,
            )
        except Exception as exc:
            LOGGER.error("SSH wrapper: session create failed: %s", exc)
            self._print_error("ssh_wrapper_session_create_failed")
            return 1

        self._audit_started(ctx, result.get("session", {}).get("id", "unknown"))
        session_id = result.get("session", {}).get("id")
        if not session_id:
            self._print_error("ssh_wrapper_no_session_id")
            return 1

        # Exec into the tmux attach — no raw shell, only controlled tmux
        tmux_name = decision.tmux_session_name or f"ananta-{ctx.target_type}"
        os.execvp("tmux", ["tmux", "attach-session", "-t", tmux_name])
        return 0  # unreachable

    @staticmethod
    def _print_error(reason: str) -> None:
        print(f"[ananta-ssh] access denied: {reason}", file=sys.stderr)

    def _audit_denied(self, ctx: WrapperContext, decision: WrapperDecision) -> None:
        from agent.config import settings
        if not settings.ssh_audit_enabled:
            return
        try:
            from agent.common.audit import log_audit
            log_audit("ssh_terminal_wrapper_denied", {
                "user_id": ctx.user_id[:40],
                "principal": ctx.principal[:80],
                "target_type": ctx.target_type,
                "target_id": ctx.target_id[:40],
                "reason_code": decision.reason_code,
            })
        except Exception:
            pass

    def _audit_started(self, ctx: WrapperContext, session_id: str) -> None:
        from agent.config import settings
        if not settings.ssh_audit_enabled:
            return
        try:
            from agent.common.audit import log_audit
            log_audit("ssh_terminal_session_started", {
                "user_id": ctx.user_id[:40],
                "principal": ctx.principal[:80],
                "target_type": ctx.target_type,
                "target_id": ctx.target_id[:40],
                "session_id": session_id,
            })
        except Exception:
            pass


def main() -> None:
    wrapper = AnantaSshTerminalWrapper()
    sys.exit(wrapper.run())


if __name__ == "__main__":
    main()
