"""Run-Control control-state read model (dashboard / Control-Center views).

Read-only projections over the command, instruction and branch stores owned
by ``RunControlService``.  The service is supplied through the narrow
``RunControlStateSource`` port, so this module never mutates Run-Control state.
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Protocol

from agent.services.run_control_models import (
    BranchCandidate,
    OperatorInstruction,
    RunCommand,
    RunControlAuthorizationError,
    RunControlPrincipal,
)


class RunControlStateSource(Protocol):
    """What the read model needs from the owning Run-Control service."""

    def authorize_resources(
        self,
        *,
        principal: RunControlPrincipal,
        task_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
    ) -> bool: ...

    def get_active_instruction(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        principal: RunControlPrincipal | None = None,
    ) -> OperatorInstruction | None: ...

    def list_branches(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        principal: RunControlPrincipal | None = None,
    ) -> list[BranchCandidate]: ...


def _owned_by(record: Any, principal: RunControlPrincipal | None) -> bool:
    return principal is None or (record.tenant_id, record.subject_id) == (
        principal.tenant_id,
        principal.subject_id,
    )


def compute_run_status(
    task_status: str | None,
    pending_approvals: list[dict],
    branches: list[dict],
    active_instruction: dict | None,
) -> str | None:
    if not task_status:
        return None
    mapping = {
        "paused": "paused",
        "cancelled": "cancelled",
        "completed": "completed",
        "failed": "failed",
        "verification_failed": "failed",
    }
    if task_status in mapping:
        return mapping[task_status]
    if pending_approvals:
        return "waiting_for_approval"
    if any(b["status"] == "proposed" for b in branches):
        return "waiting_for_branch_selection"
    if active_instruction:
        return "applying_intervention"
    if task_status in ("in_progress", "assigned", "delegated", "proposing"):
        return "running"
    if task_status in ("todo", "created"):
        return "planning"
    return task_status


def _pending_approval_view(a: Any) -> dict[str, Any]:
    return {
        "request_id": a.id,
        "tool_name": a.tool_name,
        "risk_class": a.risk_class,
        "k_class": a.k_class,
        "digest_prefix": str(a.arguments_digest or "")[:12],
        "target_fingerprint_prefix": str(a.target_fingerprint or "")[:12],
        "scope_summary": {
            k: v for k, v in dict(a.scope or {}).items()
            if k in {"approval_class", "pre_approval", "goal_id", "source", "reason_code"}
        },
        "expires_at": a.expires_at,
        "created_at": a.created_at,
        "has_content_payload": bool(a.content_artifact_ref),
    }


class RunControlReadModel:
    """Aggregate control-state projections for one Run-Control service."""

    def __init__(
        self,
        *,
        source: RunControlStateSource,
        commands: Mapping[str, RunCommand],
        instructions: Mapping[str, OperatorInstruction],
    ) -> None:
        self._source = source
        self._commands = commands
        self._instructions = instructions

    def get_control_state(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        *,
        principal: RunControlPrincipal | None = None,
    ) -> dict[str, Any]:
        """Aggregate read model: task status + pending approvals + instruction + branches + command history."""
        from agent.services.approval_request_service import get_approval_request_service

        if principal is not None and not self._source.authorize_resources(
            principal=principal,
            task_id=task_id,
            goal_id=goal_id,
            run_id=run_id,
        ):
            raise RunControlAuthorizationError(RunControlAuthorizationError.reason_code)

        task_status: str | None = None
        if task_id:
            try:
                from agent.services.repository_registry import get_repository_registry
                task = get_repository_registry().task_repo.get_by_id(str(task_id))
                if task:
                    task_status = str(getattr(task, "status", "") or "") or None
            except Exception:
                pass

        svc = get_approval_request_service()
        svc.expire_old_requests()
        approvals = svc.list_requests(status="pending", task_id=task_id, goal_id=goal_id)
        pending_approvals = [_pending_approval_view(a) for a in approvals]

        active_instr = self._source.get_active_instruction(
            task_id=task_id, goal_id=goal_id, principal=principal
        )
        active_instruction = active_instr.as_dict() if active_instr else None

        branches = [
            b.as_dict()
            for b in self._source.list_branches(task_id=task_id, goal_id=goal_id, principal=principal)
        ]

        def matches_resource(cmd: RunCommand) -> bool:
            return bool(
                (task_id and cmd.task_id == task_id)
                or (goal_id and cmd.goal_id == goal_id)
                or (run_id and cmd.run_id == run_id)
            )

        recent_commands = sorted(
            [cmd.as_dict() for cmd in self._commands.values()
             if matches_resource(cmd) and _owned_by(cmd, principal)],
            key=lambda c: c["requested_at"],
            reverse=True,
        )[:20]

        run_status = compute_run_status(
            task_status=task_status,
            pending_approvals=pending_approvals,
            branches=branches,
            active_instruction=active_instruction,
        )

        return {
            "task_id": task_id,
            "goal_id": goal_id,
            "run_id": run_id,
            "task_status": task_status,
            "run_status": run_status,
            "pending_commands": [
                cmd.as_dict() for cmd in self._commands.values()
                if cmd.status == "pending_safe_point"
                and matches_resource(cmd)
                and _owned_by(cmd, principal)
            ],
            "active_instruction": active_instruction,
            "pending_approvals": pending_approvals,
            "branches": branches,
            "last_events": recent_commands,
            "computed_at": time.time(),
        }

    def get_all_active_control_states(
        self,
        limit: int = 50,
        *,
        principal: RunControlPrincipal | None = None,
    ) -> list[dict[str, Any]]:
        """Snapshot for Dashboard/Control-Center: all tasks needing human attention."""
        from agent.services.approval_request_service import get_approval_request_service
        from agent.services.repository_registry import get_repository_registry

        svc = get_approval_request_service()
        svc.expire_old_requests()

        pending = svc.list_requests(status="pending")
        task_ids: set[str] = {str(a.task_id or "") for a in pending if a.task_id}
        task_ids |= {
            str(cmd.task_id or "")
            for cmd in self._commands.values()
            if cmd.task_id and _owned_by(cmd, principal)
        }
        task_ids |= {
            str(i.task_id or "") for i in self._instructions.values()
            if i.status == "active" and i.task_id and _owned_by(i, principal)
        }
        try:
            active_statuses = {
                "in_progress", "assigned", "delegated", "proposing",
                "paused", "blocked_by_dependency",
            }
            for t in get_repository_registry().task_repo.get_all():
                if str(getattr(t, "status", "") or "") in active_statuses:
                    task_ids.add(str(t.id))
        except Exception:
            pass

        task_ids.discard("")
        result = []
        for tid in list(task_ids)[:max(1, min(int(limit), 200))]:
            try:
                result.append(self.get_control_state(task_id=tid, principal=principal))
            except RunControlAuthorizationError:
                continue
        return result

    def list_commands(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        limit: int = 50,
        principal: RunControlPrincipal | None = None,
    ) -> list[dict[str, Any]]:
        if principal is not None and (task_id or goal_id) and not self._source.authorize_resources(
            principal=principal,
            task_id=task_id,
            goal_id=goal_id,
        ):
            raise RunControlAuthorizationError(RunControlAuthorizationError.reason_code)
        cmds = sorted(
            [cmd.as_dict() for cmd in self._commands.values()
             if (not task_id or cmd.task_id == task_id)
             and (not goal_id or cmd.goal_id == goal_id)
             and _owned_by(cmd, principal)],
            key=lambda c: c["requested_at"],
            reverse=True,
        )
        return cmds[:max(1, min(int(limit), 500))]
