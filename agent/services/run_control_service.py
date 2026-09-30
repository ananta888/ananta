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

Collaborators (composed by ``RunControlService``):
  - ``run_control_models``: value types, vocabularies, domain errors
  - ``run_control_idempotency``: pure key scoping / replay comparison
  - ``run_control_resource_ownership``: principal ownership index
  - ``run_control_read_model``: control-state projections
"""
from __future__ import annotations

import time
import uuid
from copy import deepcopy
from threading import RLock
from typing import Any

from agent.common.audit import log_audit
from agent.config import settings  # noqa: F401  (compat re-export)
from agent.services.run_control_idempotency import (
    idempotency_key_ref,
    idempotency_mismatches,
    idempotency_scope_key,
    values_are_exact,
)
from agent.services.run_control_models import (  # noqa: F401  (compat re-exports)
    BRANCH_TYPES,
    COMMAND_TYPES,
    INSTRUCTION_CLASSES,
    INSTRUCTION_MODES,
    BranchCandidate,
    OperatorInstruction,
    RunCommand,
    RunCommandIdempotencyConflictError,
    RunControlAuthorizationError,
    RunControlPrincipal,
)
from agent.services.run_control_read_model import (
    RunControlReadModel,
    compute_run_status,
)
from agent.services.run_control_resource_ownership import (
    RunControlResourceOwnership,
    resolve_legacy_resource_principal,
    run_control_resource_keys,
)
from agent.services.run_control_task_intervention_mixin import (
    RunControlTaskInterventionMixin,
)


class RunControlService(RunControlTaskInterventionMixin):
    """Hub-owned run-control mutations and read models.

    All state-changing commands wrap existing services:
      pause/resume/cancel/retry  → TaskAdminService.intervene_task()
      approve/deny               → ApprovalRequestService.decide_request()

    This service adds: RunCommand audit trail, OperatorInstruction persistence,
    BranchCandidate management, and the aggregated control-state read model.
    """

    def __init__(
        self,
        *,
        resource_ownership: RunControlResourceOwnership | None = None,
    ) -> None:
        self._commands: dict[str, RunCommand] = {}
        self._instructions: dict[str, OperatorInstruction] = {}
        self._branches: dict[str, BranchCandidate] = {}
        self._idempotency_index: dict[str, str] = {}  # scoped key hash -> command_id
        self._command_lock = RLock()
        # Ownership shares the command lock through a provider so that a
        # replaced ``_command_lock`` keeps guarding both critical sections.
        self._resource_ownership = resource_ownership or RunControlResourceOwnership(
            lock_provider=lambda: self._command_lock,
        )
        self._resource_owners = self._resource_ownership.owners
        self._read_model = RunControlReadModel(
            source=self,
            commands=self._commands,
            instructions=self._instructions,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _actor() -> str:
        try:
            from flask import g
            user = getattr(g, "user", {}) or {}
            return str(user.get("sub") or user.get("username") or "operator")
        except Exception:
            return "system"

    _idempotency_scope_key = staticmethod(idempotency_scope_key)
    _idempotency_key_ref = staticmethod(idempotency_key_ref)
    _values_are_exact = staticmethod(values_are_exact)
    _idempotency_mismatches = staticmethod(idempotency_mismatches)
    _resource_keys = staticmethod(run_control_resource_keys)
    _legacy_resource_principal = staticmethod(resolve_legacy_resource_principal)

    def _check_idempotency(self, scoped_key: str) -> RunCommand | None:
        if not scoped_key:
            return None
        cid = self._idempotency_index.get(scoped_key)
        return self._commands.get(cid) if cid else None

    def _register_idempotency(self, scoped_key: str, command_id: str) -> None:
        if scoped_key:
            self._idempotency_index[scoped_key] = command_id

    # ── Resource ownership (delegated) ─────────────────────────────────────────

    def authorize_resources(
        self,
        *,
        principal: RunControlPrincipal,
        task_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        allow_legacy_binding: bool = True,
    ) -> bool:
        """Atomically authorize all exact resources and migrate legacy tasks."""

        return self._resource_ownership.authorize_resources(
            principal=principal,
            task_id=task_id,
            goal_id=goal_id,
            run_id=run_id,
            allow_legacy_binding=allow_legacy_binding,
        )

    def bind_resource_owner(
        self,
        *,
        kind: str,
        resource_id: str,
        principal: RunControlPrincipal,
    ) -> bool:
        """Trusted Hub adapter binding after its own verified read-model check."""

        return self.bind_resource_owners(
            principal=principal,
            resources=((str(kind), str(resource_id)),),
        )

    def bind_resource_owners(
        self,
        *,
        principal: RunControlPrincipal,
        resources: tuple[tuple[str, str], ...],
    ) -> bool:
        """Atomically bind resources verified by a trusted Hub adapter."""

        return self._resource_ownership.bind_resource_owners(
            principal=principal,
            resources=resources,
        )

    @staticmethod
    def _emit_idempotency_conflict(
        *,
        existing: RunCommand,
        command_type: str,
        task_id: str | None,
        goal_id: str | None,
        run_id: str | None,
        requested_by: str,
        idempotency_key_ref: str,
        mismatched_fields: tuple[str, ...],
    ) -> None:
        try:
            log_audit(
                "run_command_idempotency_conflict",
                {
                    "existing_command_id": existing.command_id,
                    "existing_command_type": existing.type,
                    "requested_command_type": command_type,
                    "task_id": task_id,
                    "goal_id": goal_id,
                    "run_id": run_id,
                    "tenant_id": existing.tenant_id,
                    "subject_id": existing.subject_id,
                    "requested_by": requested_by,
                    "idempotency_key_ref": idempotency_key_ref,
                    "mismatched_fields": list(mismatched_fields),
                },
            )
        except Exception:
            pass

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
        tenant_id: str | None = None,
        subject_id: str | None = None,
    ) -> RunCommand:
        """Dispatch a run-control command and return the result.

        An exact concurrent replay returns the same reserved command in its
        current state.  While the owner is still executing, that state is
        ``accepted``; completed sequential replays expose the final state.
        """
        actor = requested_by or self._actor()
        principal = RunControlPrincipal.from_values(tenant_id or "legacy", subject_id or actor)
        command_payload = deepcopy(dict(payload or {}))

        if tenant_id is not None or subject_id is not None:
            if not self.authorize_resources(
                principal=principal,
                task_id=task_id,
                goal_id=goal_id,
                run_id=run_id,
            ):
                raise RunControlAuthorizationError(RunControlAuthorizationError.reason_code)

        if command_type not in COMMAND_TYPES:
            return RunCommand(
                command_id=str(uuid.uuid4()),
                type=command_type,
                task_id=task_id,
                goal_id=goal_id,
                run_id=run_id,
                payload=command_payload,
                requested_by=actor,
                requested_at=time.time(),
                status="rejected_by_policy",
                result={"error": "unknown_command_type", "allowed": sorted(COMMAND_TYPES)},
                tenant_id=principal.tenant_id,
                subject_id=principal.subject_id,
            )

        normalized_key = str(idempotency_key) if idempotency_key else None
        scoped_key = self._idempotency_scope_key(
            normalized_key,
            principal=principal,
            task_id=task_id,
            goal_id=goal_id,
            run_id=run_id,
        )
        conflict: RunCommandIdempotencyConflictError | None = None
        conflict_command: RunCommand | None = None
        conflict_fields: tuple[str, ...] = ()
        cmd: RunCommand | None = None
        with self._command_lock:
            existing = self._check_idempotency(scoped_key)
            if existing is not None:
                mismatched_fields = self._idempotency_mismatches(
                    existing,
                    command_type=command_type,
                    task_id=task_id,
                    goal_id=goal_id,
                    run_id=run_id,
                    payload=command_payload,
                    requested_by=actor,
                    principal=principal,
                )
                if not mismatched_fields:
                    return existing
                conflict_fields = mismatched_fields
                conflict_command = existing
                conflict = RunCommandIdempotencyConflictError(
                    idempotency_key_ref=self._idempotency_key_ref(normalized_key),
                    existing_command_id=existing.command_id,
                    mismatched_fields=mismatched_fields,
                )
            else:
                cmd = RunCommand(
                    command_id=str(uuid.uuid4()),
                    type=command_type,
                    task_id=task_id,
                    goal_id=goal_id,
                    run_id=run_id,
                    payload=command_payload,
                    requested_by=actor,
                    requested_at=time.time(),
                    status="accepted",
                    idempotency_key=normalized_key,
                    tenant_id=principal.tenant_id,
                    subject_id=principal.subject_id,
                )
                self._commands[cmd.command_id] = cmd
                self._register_idempotency(scoped_key, cmd.command_id)

        if conflict is not None:
            assert conflict_command is not None
            self._emit_idempotency_conflict(
                existing=conflict_command,
                command_type=command_type,
                task_id=task_id,
                goal_id=goal_id,
                run_id=run_id,
                requested_by=actor,
                idempotency_key_ref=conflict.idempotency_key_ref,
                mismatched_fields=conflict_fields,
            )
            raise conflict

        assert cmd is not None

        # The lock protects only idempotency reservation.  Hub mutations may
        # call databases or external adapters and must not serialize unrelated
        # run-control commands behind one process-wide lock.
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

        self._emit_audit(cmd)
        return cmd

    # ── Task intervention shims ────────────────────────────────────────────────

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
            tenant_id=cmd.tenant_id,
            subject_id=cmd.subject_id,
        )

    def _store_instruction(self, instr: OperatorInstruction) -> None:
        for existing in list(self._instructions.values()):
            if existing.status != "active":
                continue
            if (existing.tenant_id, existing.subject_id) != (instr.tenant_id, instr.subject_id):
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
                "run_id": instr.run_id,
                "tenant_id": instr.tenant_id,
                "subject_id": instr.subject_id,
                "mode": instr.mode,
                "instruction_class": instr.instruction_class,
                "actor": instr.actor,
            })
        except Exception:
            pass

    def get_active_instruction(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        principal: RunControlPrincipal | None = None,
    ) -> OperatorInstruction | None:
        for instr in reversed(list(self._instructions.values())):
            if principal is not None and (instr.tenant_id, instr.subject_id) != (
                principal.tenant_id,
                principal.subject_id,
            ):
                continue
            if instr.status != "active":
                continue
            if task_id and instr.task_id == task_id:
                return instr
            if goal_id and instr.goal_id == goal_id and not instr.task_id:
                return instr
        return None

    def list_instructions(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        principal: RunControlPrincipal | None = None,
    ) -> list[OperatorInstruction]:
        result = [
            i for i in self._instructions.values()
            if ((task_id and i.task_id == task_id) or (goal_id and i.goal_id == goal_id))
            and (
                principal is None
                or (i.tenant_id, i.subject_id) == (principal.tenant_id, principal.subject_id)
            )
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
        branch_resource_mismatch = bool(
            branch is not None
            and (
                (cmd.task_id and branch.task_id != cmd.task_id)
                or (cmd.goal_id and branch.goal_id != cmd.goal_id)
            )
        )
        if (
            branch is None
            or (branch.tenant_id, branch.subject_id) != (cmd.tenant_id, cmd.subject_id)
            or branch_resource_mismatch
        ):
            cmd.status = "failed"
            cmd.result = {"error": "branch_not_found"}
            return
        if branch.status in ("selected", "rejected", "superseded", "completed"):
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": f"branch_already_{branch.status}", "branch_id": branch_id}
            return
        for b in list(self._branches.values()):
            if (b.tenant_id, b.subject_id) != (cmd.tenant_id, cmd.subject_id):
                continue
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
                "run_id": cmd.run_id,
                "tenant_id": cmd.tenant_id,
                "subject_id": cmd.subject_id,
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
        tenant_id: str | None = None,
        subject_id: str | None = None,
    ) -> BranchCandidate:
        principal = RunControlPrincipal.from_values(
            tenant_id or "legacy",
            subject_id or self._actor(),
        )
        if tenant_id is not None or subject_id is not None:
            if not self.authorize_resources(
                principal=principal,
                task_id=task_id,
                goal_id=goal_id,
            ):
                raise RunControlAuthorizationError(RunControlAuthorizationError.reason_code)
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
            tenant_id=principal.tenant_id,
            subject_id=principal.subject_id,
        )
        self._branches[bid] = branch
        return branch

    def list_branches(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        principal: RunControlPrincipal | None = None,
    ) -> list[BranchCandidate]:
        result = [
            b for b in self._branches.values()
            if ((task_id and b.task_id == task_id) or (goal_id and b.goal_id == goal_id))
            and (
                principal is None
                or (b.tenant_id, b.subject_id) == (principal.tenant_id, principal.subject_id)
            )
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
        from agent.services.approval_request_service import (
            ApprovalDecisionError,
            get_approval_request_service,
        )
        service = get_approval_request_service()
        request_row = service.get_request(approval_id)
        resource_mismatch = bool(
            request_row is None
            or (cmd.task_id and str(request_row.task_id or "") != cmd.task_id)
            or (cmd.goal_id and str(request_row.goal_id or "") != cmd.goal_id)
        )
        if resource_mismatch:
            cmd.status = "failed"
            cmd.result = {"error": "approval_not_found"}
            return
        from agent.services.task_recovery_planning_service import (
            RECOVERY_MATERIALIZE_TOOL,
        )

        if str(getattr(request_row, "tool_name", "") or "") == (
            RECOVERY_MATERIALIZE_TOOL
        ):
            # Run-Control principals carry ownership, but no trusted
            # administrator claim. Recovery materialization therefore remains
            # on the dedicated admin-gated approval endpoint.
            cmd.status = "rejected_by_policy"
            cmd.result = {
                "error": "recovery_approval_requires_admin_endpoint",
                "approval_id": approval_id,
            }
            return
        try:
            row = service.decide_request(
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

    # ── Control-state read model (delegated) ──────────────────────────────────

    _compute_run_status = staticmethod(compute_run_status)

    def get_control_state(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        *,
        principal: RunControlPrincipal | None = None,
    ) -> dict[str, Any]:
        """Aggregate read model: task status + pending approvals + instruction + branches + command history."""
        return self._read_model.get_control_state(
            task_id=task_id, goal_id=goal_id, run_id=run_id, principal=principal
        )

    def get_all_active_control_states(
        self,
        limit: int = 50,
        *,
        principal: RunControlPrincipal | None = None,
    ) -> list[dict[str, Any]]:
        """Snapshot for Dashboard/Control-Center: all tasks needing human attention."""
        return self._read_model.get_all_active_control_states(limit, principal=principal)

    def list_commands(
        self,
        task_id: str | None = None,
        goal_id: str | None = None,
        limit: int = 50,
        principal: RunControlPrincipal | None = None,
    ) -> list[dict[str, Any]]:
        return self._read_model.list_commands(
            task_id=task_id, goal_id=goal_id, limit=limit, principal=principal
        )

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
                "run_id": cmd.run_id,
                "tenant_id": cmd.tenant_id,
                "subject_id": cmd.subject_id,
                "requested_by": cmd.requested_by,
                "status": cmd.status,
                "idempotency_key_ref": idempotency_key_ref(cmd.idempotency_key),
            })
        except Exception:
            pass


_run_control_service: RunControlService | None = None


def get_run_control_service() -> RunControlService:
    global _run_control_service
    if _run_control_service is None:
        _run_control_service = RunControlService()
    return _run_control_service
