"""RC-010/030/040/050: Hub-owned Run-Control domain.

Covers:
  - RunCommand dispatch: pause/resume/cancel/retry/inject_instruction/select_branch/approve_gate/deny_gate
  - OperatorInstruction persistence with safe-point semantics
  - BranchCandidate management for multi-LLM and planner variants
  - Control-state read model aggregating task status, approvals, instructions, branches

Design:
  - TaskAdminService handles all task state transitions (never duplicated here)
  - ApprovalRequestService handles all approval lifecycle (never duplicated here)
  - RunCommand is the audit trail; all mutations create one
  - Idempotency keys prevent duplicate execution
  - No raw prompts/secrets in audit events or control-state
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.common.audit import log_audit

COMMAND_TYPES = frozenset({
    "pause_run", "resume_run", "cancel_run", "retry_run_or_task",
    "inject_instruction", "select_branch", "approve_gate", "deny_gate",
})

INSTRUCTION_MODES = frozenset({
    "next_iteration_instruction", "pause_then_apply", "context_note_only",
})

INSTRUCTION_CLASSES = frozenset({
    "correction", "constraint", "preference", "branch_hint", "stop_condition",
})

BRANCH_TYPES = frozenset({
    "llm_comparison_variant", "planner_variant", "implementation_strategy",
    "repair_strategy", "security_hardened_variant",
})


@dataclass
class RunCommand:
    command_id: str
    type: str
    requested_by: str
    requested_at: float
    status: str  # accepted|rejected_by_policy|pending_safe_point|applied|superseded|failed
    task_id: str | None = None
    goal_id: str | None = None
    run_id: str | None = None
    payload: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    effective_at: float | None = None
    idempotency_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "type": self.type,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "effective_at": self.effective_at,
            "status": self.status,
            "result": self.result,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class OperatorInstruction:
    instruction_id: str
    text: str
    actor: str
    created_at: float
    mode: str = "next_iteration_instruction"
    instruction_class: str = "constraint"
    status: str = "active"  # active|superseded|applied|resolved
    task_id: str | None = None
    goal_id: str | None = None
    run_id: str | None = None
    applied_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "instruction_id": self.instruction_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "text": self.text,
            "instruction_class": self.instruction_class,
            "actor": self.actor,
            "created_at": self.created_at,
            "status": self.status,
            "applied_at": self.applied_at,
        }


@dataclass
class BranchCandidate:
    branch_id: str
    label: str
    branch_type: str = "llm_comparison_variant"
    status: str = "proposed"  # proposed|active|selected|paused|rejected|superseded|completed
    task_id: str | None = None
    goal_id: str | None = None
    description: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    selected_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "branch_type": self.branch_type,
            "label": self.label,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "selected_at": self.selected_at,
            "metadata": self.metadata,
        }


class RunControlService:
    """Hub-owned run-control mutations and read models.

    All state-changing commands wrap existing services:
      pause/resume/cancel/retry  → TaskAdminService.intervene_task()
      approve/deny               → ApprovalRequestService.decide_request()

    This service adds: RunCommand audit trail, OperatorInstruction persistence,
    BranchCandidate management, and the aggregated control-state read model.
    """

    def __init__(self) -> None:
        self._commands: dict[str, RunCommand] = {}
        self._instructions: dict[str, OperatorInstruction] = {}
        self._branches: dict[str, BranchCandidate] = {}
        self._idempotency_index: dict[str, str] = {}  # key -> command_id

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _actor() -> str:
        try:
            from flask import g
            user = getattr(g, "user", {}) or {}
            return str(user.get("sub") or user.get("username") or "operator")
        except Exception:
            return "system"

    def _check_idempotency(self, key: str | None) -> RunCommand | None:
        if not key:
            return None
        cid = self._idempotency_index.get(str(key))
        return self._commands.get(cid) if cid else None

    def _register_idempotency(self, key: str | None, command_id: str) -> None:
        if key:
            self._idempotency_index[str(key)] = command_id

    # ── Command dispatch ───────────────────────────────────────────────────────

    def send_command(
        self,
        *,
        command_type: str,
        task_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        payload: dict | None = None,
        requested_by: str | None = None,
        idempotency_key: str | None = None,
    ) -> RunCommand:
        """Dispatch a run-control command and return the result."""
        actor = requested_by or self._actor()

        if command_type not in COMMAND_TYPES:
            return RunCommand(
                command_id=str(uuid.uuid4()),
                type=command_type,
                task_id=task_id,
                goal_id=goal_id,
                run_id=run_id,
                payload=dict(payload or {}),
                requested_by=actor,
                requested_at=time.time(),
                status="rejected_by_policy",
                result={"error": "unknown_command_type", "allowed": sorted(COMMAND_TYPES)},
            )

        existing = self._check_idempotency(idempotency_key)
        if existing is not None:
            return existing

        cmd = RunCommand(
            command_id=str(uuid.uuid4()),
            type=command_type,
            task_id=task_id,
            goal_id=goal_id,
            run_id=run_id,
            payload=dict(payload or {}),
            requested_by=actor,
            requested_at=time.time(),
            status="accepted",
            idempotency_key=idempotency_key,
        )
        self._register_idempotency(idempotency_key, cmd.command_id)

        try:
            dispatch = {
                "pause_run": self._do_pause,
                "resume_run": self._do_resume,
                "cancel_run": self._do_cancel,
                "retry_run_or_task": self._do_retry,
                "inject_instruction": self._do_inject_instruction,
                "select_branch": self._do_select_branch,
                "approve_gate": self._do_approve_gate,
                "deny_gate": self._do_deny_gate,
            }
            dispatch[command_type](cmd)
        except Exception as exc:
            cmd.status = "failed"
            cmd.result = {"error": str(exc)[:300]}

        self._commands[cmd.command_id] = cmd
        self._emit_audit(cmd)
        return cmd

    # ── Task intervention shims ────────────────────────────────────────────────

    def _task_intervene(self, cmd: RunCommand, action: str) -> None:
        tid = str(cmd.task_id or "").strip()
        if not tid:
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": "task_id_required"}
            return
        from agent.services.service_registry import get_core_services
        ok, msg, data = get_core_services().task_admin_service.intervene_task(
            task_id=tid, action=action, actor=cmd.requested_by
        )
        if ok:
            cmd.status = "applied"
            cmd.result.update(data)
            cmd.effective_at = time.time()
        else:
            cmd.status = "rejected_by_policy" if msg == "invalid_transition" else "failed"
            cmd.result.update({"error": msg, **{k: v for k, v in data.items() if k != "error"}})

    def _do_pause(self, cmd: RunCommand) -> None:
        self._task_intervene(cmd, "pause")

    def _do_cancel(self, cmd: RunCommand) -> None:
        self._task_intervene(cmd, "cancel")

    def _do_retry(self, cmd: RunCommand) -> None:
        self._task_intervene(cmd, "retry")

    def _do_resume(self, cmd: RunCommand) -> None:
        instruction_text = str(cmd.payload.get("instruction") or "").strip()
        if instruction_text:
            instr = self._build_instruction(cmd, text=instruction_text)
            self._store_instruction(instr)
            cmd.result["instruction_id"] = instr.instruction_id
        self._task_intervene(cmd, "resume")

    # ── Instruction injection ──────────────────────────────────────────────────

    def _do_inject_instruction(self, cmd: RunCommand) -> None:
        text = str(cmd.payload.get("text") or "").strip()
        if not text:
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": "instruction_text_required"}
            return
        if len(text) > 4000:
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": "instruction_text_too_long", "max_length": 4000, "got": len(text)}
            return
        instr = self._build_instruction(cmd, text=text)
        self._store_instruction(instr)
        cmd.status = "applied"
        cmd.result = {
            "instruction_id": instr.instruction_id,
            "mode": instr.mode,
            "instruction_class": instr.instruction_class,
            "status": instr.status,
        }
        cmd.effective_at = time.time()

    def _build_instruction(self, cmd: RunCommand, *, text: str) -> OperatorInstruction:
        raw_mode = str(cmd.payload.get("mode") or "next_iteration_instruction")
        mode = raw_mode if raw_mode in INSTRUCTION_MODES else "next_iteration_instruction"
        raw_class = str(cmd.payload.get("instruction_class") or "constraint")
        instr_class = raw_class if raw_class in INSTRUCTION_CLASSES else "constraint"
        return OperatorInstruction(
            instruction_id=str(uuid.uuid4()),
            task_id=cmd.task_id,
            goal_id=cmd.goal_id,
            run_id=cmd.run_id,
            text=text,
            mode=mode,
            instruction_class=instr_class,
            actor=cmd.requested_by,
            created_at=time.time(),
        )

    def _store_instruction(self, instr: OperatorInstruction) -> None:
        for existing in list(self._instructions.values()):
            if existing.status != "active":
                continue
            if instr.mode == "context_note_only":
                continue
            same = (instr.task_id and existing.task_id == instr.task_id) or \
                   (instr.goal_id and existing.goal_id == instr.goal_id)
            if same:
                existing.status = "superseded"
        self._instructions[instr.instruction_id] = instr
        try:
            log_audit("operator_instruction_created", {
                "instruction_id": instr.instruction_id,
                "task_id": instr.task_id,
                "goal_id": instr.goal_id,
                "mode": instr.mode,
                "instruction_class": instr.instruction_class,
                "actor": instr.actor,
            })
        except Exception:
            pass

    def get_active_instruction(self, task_id: str | None = None, goal_id: str | None = None) -> OperatorInstruction | None:
        for instr in reversed(list(self._instructions.values())):
            if instr.status != "active":
                continue
            if task_id and instr.task_id == task_id:
                return instr
            if goal_id and instr.goal_id == goal_id and not instr.task_id:
                return instr
        return None

    def list_instructions(self, task_id: str | None = None, goal_id: str | None = None) -> list[OperatorInstruction]:
        result = [
            i for i in self._instructions.values()
            if (task_id and i.task_id == task_id) or (goal_id and i.goal_id == goal_id)
        ]
        return sorted(result, key=lambda i: i.created_at, reverse=True)

    def mark_instruction_applied(self, instruction_id: str) -> bool:
        instr = self._instructions.get(instruction_id)
        if instr and instr.status == "active":
            instr.status = "applied"
            instr.applied_at = time.time()
            return True
        return False

    # ── Branch management ──────────────────────────────────────────────────────

    def _do_select_branch(self, cmd: RunCommand) -> None:
        branch_id = str(cmd.payload.get("branch_id") or "").strip()
        if not branch_id:
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": "branch_id_required"}
            return
        branch = self._branches.get(branch_id)
        if branch is None:
            cmd.status = "failed"
            cmd.result = {"error": "branch_not_found", "branch_id": branch_id}
            return
        if branch.status in ("selected", "rejected", "superseded", "completed"):
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": f"branch_already_{branch.status}", "branch_id": branch_id}
            return
        for b in list(self._branches.values()):
            match_task = cmd.task_id and b.task_id == cmd.task_id
            match_goal = cmd.goal_id and b.goal_id == cmd.goal_id
            if (match_task or match_goal) and b.branch_id != branch_id:
                if b.status in ("proposed", "active"):
                    b.status = "paused"
        branch.status = "selected"
        branch.selected_at = time.time()
        cmd.status = "applied"
        cmd.result = {"branch_id": branch_id, "new_status": "selected"}
        cmd.effective_at = time.time()
        try:
            log_audit("branch_selected", {
                "branch_id": branch_id,
                "task_id": cmd.task_id,
                "goal_id": cmd.goal_id,
                "actor": cmd.requested_by,
            })
        except Exception:
            pass

    def create_branch(
        self,
        *,
        branch_id: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        branch_type: str = "llm_comparison_variant",
        label: str,
        description: str = "",
        metadata: dict | None = None,
        status: str = "proposed",
    ) -> BranchCandidate:
        bid = branch_id or str(uuid.uuid4())
        branch = BranchCandidate(
            branch_id=bid,
            task_id=task_id,
            goal_id=goal_id,
            branch_type=branch_type,
            label=label,
            description=description,
            status=status,
            metadata=dict(metadata or {}),
            created_at=time.time(),
        )
        self._branches[bid] = branch
        return branch

    def list_branches(self, task_id: str | None = None, goal_id: str | None = None) -> list[BranchCandidate]:
        result = [
            b for b in self._branches.values()
            if (task_id and b.task_id == task_id) or (goal_id and b.goal_id == goal_id)
        ]
        return sorted(result, key=lambda b: b.created_at, reverse=True)

    # ── Approval gate shims ────────────────────────────────────────────────────

    def _approval_decide(self, cmd: RunCommand, decision: str) -> None:
        approval_id = str(cmd.payload.get("approval_id") or "").strip()
        if not approval_id:
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": "approval_id_required"}
            return
        reason = str(cmd.payload.get("reason") or "").strip() or None
        from agent.services.approval_request_service import get_approval_request_service, ApprovalDecisionError
        try:
            row = get_approval_request_service().decide_request(
                approval_id,
                decision=decision,
                decided_by=cmd.requested_by,
                reason=reason,
            )
            cmd.status = "applied"
            cmd.result = {"approval_id": approval_id, "decision": decision, "status": row.status}
            cmd.effective_at = time.time()
        except ApprovalDecisionError as exc:
            cmd.status = "failed"
            cmd.result = {"error": exc.code, "approval_id": approval_id}

    def _do_approve_gate(self, cmd: RunCommand) -> None:
        self._approval_decide(cmd, "granted")

    def _do_deny_gate(self, cmd: RunCommand) -> None:
        self._approval_decide(cmd, "denied")

    # ── Control-state read model ───────────────────────────────────────────────

    def get_control_state(self, task_id: str | None = None, goal_id: str | None = None) -> dict[str, Any]:
        """Aggregate read model: task status + pending approvals + instruction + branches + command history."""
        from agent.services.approval_request_service import get_approval_request_service

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
        pending_approvals = [
            {
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
            for a in approvals
        ]

        active_instr = self.get_active_instruction(task_id=task_id, goal_id=goal_id)
        active_instruction = active_instr.as_dict() if active_instr else None

        branches = [b.as_dict() for b in self.list_branches(task_id=task_id, goal_id=goal_id)]

        recent_commands = sorted(
            [cmd.as_dict() for cmd in self._commands.values()
             if (task_id and cmd.task_id == task_id) or (goal_id and cmd.goal_id == goal_id)],
            key=lambda c: c["requested_at"],
            reverse=True,
        )[:20]

        run_status = self._compute_run_status(
            task_status=task_status,
            pending_approvals=pending_approvals,
            branches=branches,
            active_instruction=active_instruction,
        )

        return {
            "task_id": task_id,
            "goal_id": goal_id,
            "task_status": task_status,
            "run_status": run_status,
            "pending_commands": [
                cmd.as_dict() for cmd in self._commands.values()
                if cmd.status == "pending_safe_point"
                and ((task_id and cmd.task_id == task_id) or (goal_id and cmd.goal_id == goal_id))
            ],
            "active_instruction": active_instruction,
            "pending_approvals": pending_approvals,
            "branches": branches,
            "last_events": recent_commands,
            "computed_at": time.time(),
        }

    @staticmethod
    def _compute_run_status(
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

    def get_all_active_control_states(self, limit: int = 50) -> list[dict[str, Any]]:
        """Snapshot for Dashboard/Control-Center: all tasks needing human attention."""
        from agent.services.approval_request_service import get_approval_request_service
        from agent.services.repository_registry import get_repository_registry

        svc = get_approval_request_service()
        svc.expire_old_requests()

        pending = svc.list_requests(status="pending")
        task_ids: set[str] = {str(a.task_id or "") for a in pending if a.task_id}
        task_ids |= {str(cmd.task_id or "") for cmd in self._commands.values() if cmd.task_id}
        task_ids |= {
            str(i.task_id or "") for i in self._instructions.values()
            if i.status == "active" and i.task_id
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
            result.append(self.get_control_state(task_id=tid))
        return result

    def list_commands(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cmds = sorted(
            [cmd.as_dict() for cmd in self._commands.values()
             if (not task_id or cmd.task_id == task_id)
             and (not goal_id or cmd.goal_id == goal_id)],
            key=lambda c: c["requested_at"],
            reverse=True,
        )
        return cmds[:max(1, min(int(limit), 500))]

    # ── Audit ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _emit_audit(cmd: RunCommand) -> None:
        event = "run_command_applied" if cmd.status == "applied" else "run_command_created"
        if cmd.status == "rejected_by_policy":
            event = "run_command_rejected"
        try:
            log_audit(event, {
                "command_id": cmd.command_id,
                "type": cmd.type,
                "task_id": cmd.task_id,
                "goal_id": cmd.goal_id,
                "requested_by": cmd.requested_by,
                "status": cmd.status,
                "idempotency_key": cmd.idempotency_key,
            })
        except Exception:
            pass


_run_control_service: RunControlService | None = None


def get_run_control_service() -> RunControlService:
    global _run_control_service
    if _run_control_service is None:
        _run_control_service = RunControlService()
    return _run_control_service
