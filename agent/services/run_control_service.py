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

import json
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from threading import RLock
from typing import Any, Mapping

from agent.common.audit import log_audit
from agent.config import settings
from agent.services.identity_validation import require_canonical_identity


@dataclass(frozen=True)
class RunControlPrincipal:
    tenant_id: str
    subject_id: str

    @classmethod
    def from_values(cls, tenant_id: Any, subject_id: Any) -> "RunControlPrincipal":
        return cls(
            tenant_id=require_canonical_identity(tenant_id, field_name="tenant_id"),
            subject_id=require_canonical_identity(subject_id, field_name="subject_id"),
        )

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
    tenant_id: str = ""
    subject_id: str = ""

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
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
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
    tenant_id: str = ""
    subject_id: str = ""

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
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
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
    tenant_id: str = ""
    subject_id: str = ""

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
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
        }


class RunCommandIdempotencyConflictError(RuntimeError):
    """Raised when an idempotency key is reused for a different request."""

    reason_code = "run_command_idempotency_conflict"

    def __init__(
        self,
        *,
        idempotency_key_ref: str,
        existing_command_id: str,
        mismatched_fields: tuple[str, ...],
    ) -> None:
        super().__init__(self.reason_code)
        self.idempotency_key_ref = idempotency_key_ref
        self.existing_command_id = existing_command_id
        self.mismatched_fields = mismatched_fields


class RunControlAuthorizationError(RuntimeError):
    reason_code = "run_control_resource_not_found"


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
        self._idempotency_index: dict[str, str] = {}  # scoped key hash -> command_id
        self._resource_owners: dict[tuple[str, str], RunControlPrincipal] = {}
        self._command_lock = RLock()

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _actor() -> str:
        try:
            from flask import g
            user = getattr(g, "user", {}) or {}
            return str(user.get("sub") or user.get("username") or "operator")
        except Exception:
            return "system"

    @staticmethod
    def _idempotency_scope_key(
        key: str | None,
        *,
        principal: RunControlPrincipal,
        task_id: str | None,
        goal_id: str | None,
        run_id: str | None,
    ) -> str:
        if not key:
            return ""
        canonical = json.dumps(
            {
                "client_key": key,
                "subject_id": principal.subject_id,
                "tenant_id": principal.tenant_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        # Operation, resources and payload remain in the command fingerprint
        # comparison. Reusing one client key for a different request inside the
        # same principal scope is an explicit conflict, while another tenant or
        # subject has an independent key space.
        del task_id, goal_id, run_id
        return sha256(canonical).hexdigest()

    def _check_idempotency(self, scoped_key: str) -> RunCommand | None:
        if not scoped_key:
            return None
        cid = self._idempotency_index.get(scoped_key)
        return self._commands.get(cid) if cid else None

    def _register_idempotency(self, scoped_key: str, command_id: str) -> None:
        if scoped_key:
            self._idempotency_index[scoped_key] = command_id

    @staticmethod
    def _resource_keys(
        *,
        task_id: str | None,
        goal_id: str | None,
        run_id: str | None,
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            (kind, str(value))
            for kind, value in (("task", task_id), ("goal", goal_id), ("run", run_id))
            if value
        )

    @staticmethod
    def _legacy_resource_principal(
        resource_key: tuple[str, str],
    ) -> RunControlPrincipal | None:
        """Resolve pre-tenancy rows without a caller-wins ownership claim.

        Historic Hub tasks/goals contain a subject but no organization.  The
        only deterministic compatible principal is therefore ``(subject,
        subject)``, matching local Hub accounts.  Externally tenanted callers
        must arrive through a trusted adapter which verifies and explicitly
        binds the resource first.
        """

        kind, resource_id = resource_key
        try:
            from agent.services.repository_registry import get_repository_registry

            repositories = get_repository_registry()
            record = (
                repositories.task_repo.get_by_id(resource_id)
                if kind == "task"
                else repositories.goal_repo.get_by_id(resource_id)
            )
        except Exception:
            return None
        if record is None:
            return None
        if kind == "task":
            ingest = next(
                (
                    event
                    for event in list(getattr(record, "history", None) or [])
                    if isinstance(event, dict) and event.get("event_type") == "task_ingested"
                ),
                None,
            )
            actor = str((ingest or {}).get("actor") or "").strip()
        else:
            actor = str(getattr(record, "requested_by", "") or "").strip()
        if actor and actor not in {"system", "hub", "unknown", "operator"}:
            subject = actor
        else:
            subject = str(settings.initial_admin_user or "").strip()
        try:
            return RunControlPrincipal.from_values(subject, subject)
        except ValueError:
            return None

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

        keys = self._resource_keys(task_id=task_id, goal_id=goal_id, run_id=run_id)
        if not keys:
            return False
        with self._command_lock:
            resolved: dict[tuple[str, str], RunControlPrincipal] = {}
            task_key = ("task", str(task_id)) if task_id else None
            for resource_key in keys:
                owner = self._resource_owners.get(resource_key)
                if owner is None and resource_key[0] == "run" and task_key is not None:
                    owner = self._resource_owners.get(task_key) or resolved.get(task_key)
                if owner is None and allow_legacy_binding:
                    if resource_key[0] in {"task", "goal"}:
                        owner = self._legacy_resource_principal(resource_key)
                    # A standalone historic run has no durable owner source.
                    # It may inherit an already verified task binding above,
                    # otherwise only a trusted adapter may bind it explicitly.
                if owner != principal:
                    return False
                resolved[resource_key] = owner
            for resource_key in keys:
                self._resource_owners.setdefault(resource_key, principal)
            return True

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

        keys = tuple((str(kind), str(resource_id)) for kind, resource_id in resources)
        if not keys or any(kind not in {"task", "goal", "run"} or not resource_id for kind, resource_id in keys):
            return False
        with self._command_lock:
            if any(
                existing is not None and existing != principal
                for existing in (self._resource_owners.get(key) for key in keys)
            ):
                return False
            for key in keys:
                self._resource_owners[key] = principal
            return True

    @staticmethod
    def _idempotency_key_ref(key: str | None) -> str:
        """Return a stable opaque audit reference, never the caller's raw key."""

        if not key:
            return ""
        digest = sha256(str(key).encode("utf-8")).hexdigest()
        return f"idempotency-sha256:{digest}"

    @staticmethod
    def _values_are_exact(left: Any, right: Any) -> bool:
        """Compare request payloads without Python's cross-type equality aliases."""

        if type(left) is not type(right):
            return False
        if isinstance(left, Mapping):
            if left.keys() != right.keys():
                return False
            return all(
                RunControlService._values_are_exact(left[key], right[key])
                for key in left
            )
        if isinstance(left, (list, tuple)):
            return len(left) == len(right) and all(
                RunControlService._values_are_exact(left_item, right_item)
                for left_item, right_item in zip(left, right)
            )
        return bool(left == right)

    @classmethod
    def _idempotency_mismatches(
        cls,
        existing: RunCommand,
        *,
        command_type: str,
        task_id: str | None,
        goal_id: str | None,
        run_id: str | None,
        payload: dict[str, Any],
        requested_by: str,
        principal: RunControlPrincipal,
    ) -> tuple[str, ...]:
        values = {
            "command_type": (existing.type, command_type),
            "task_id": (existing.task_id, task_id),
            "goal_id": (existing.goal_id, goal_id),
            "run_id": (existing.run_id, run_id),
            "payload": (existing.payload, payload),
            "requested_by": (existing.requested_by, requested_by),
            "tenant_id": (existing.tenant_id, principal.tenant_id),
            "subject_id": (existing.subject_id, principal.subject_id),
        }
        return tuple(
            name
            for name, (left, right) in values.items()
            if not cls._values_are_exact(left, right)
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

    # ── Control-state read model ───────────────────────────────────────────────

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

        if principal is not None and not self.authorize_resources(
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

        active_instr = self.get_active_instruction(task_id=task_id, goal_id=goal_id, principal=principal)
        active_instruction = active_instr.as_dict() if active_instr else None

        branches = [
            b.as_dict()
            for b in self.list_branches(task_id=task_id, goal_id=goal_id, principal=principal)
        ]

        recent_commands = sorted(
            [cmd.as_dict() for cmd in self._commands.values()
             if (
                 (task_id and cmd.task_id == task_id)
                 or (goal_id and cmd.goal_id == goal_id)
                 or (run_id and cmd.run_id == run_id)
             )
             and (
                 principal is None
                 or (cmd.tenant_id, cmd.subject_id) == (principal.tenant_id, principal.subject_id)
             )],
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
            "run_id": run_id,
            "task_status": task_status,
            "run_status": run_status,
            "pending_commands": [
                cmd.as_dict() for cmd in self._commands.values()
                if cmd.status == "pending_safe_point"
                and (
                    (task_id and cmd.task_id == task_id)
                    or (goal_id and cmd.goal_id == goal_id)
                    or (run_id and cmd.run_id == run_id)
                )
                and (
                    principal is None
                    or (cmd.tenant_id, cmd.subject_id) == (principal.tenant_id, principal.subject_id)
                )
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
            if cmd.task_id
            and (
                principal is None
                or (cmd.tenant_id, cmd.subject_id) == (principal.tenant_id, principal.subject_id)
            )
        }
        task_ids |= {
            str(i.task_id or "") for i in self._instructions.values()
            if i.status == "active" and i.task_id
            and (
                principal is None
                or (i.tenant_id, i.subject_id) == (principal.tenant_id, principal.subject_id)
            )
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
        if principal is not None and (task_id or goal_id) and not self.authorize_resources(
            principal=principal,
            task_id=task_id,
            goal_id=goal_id,
        ):
            raise RunControlAuthorizationError(RunControlAuthorizationError.reason_code)
        cmds = sorted(
            [cmd.as_dict() for cmd in self._commands.values()
             if (not task_id or cmd.task_id == task_id)
             and (not goal_id or cmd.goal_id == goal_id)
             and (
                 principal is None
                 or (cmd.tenant_id, cmd.subject_id) == (principal.tenant_id, principal.subject_id)
             )],
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
                "run_id": cmd.run_id,
                "tenant_id": cmd.tenant_id,
                "subject_id": cmd.subject_id,
                "requested_by": cmd.requested_by,
                "status": cmd.status,
                "idempotency_key_ref": RunControlService._idempotency_key_ref(
                    cmd.idempotency_key
                ),
            })
        except Exception:
            pass


_run_control_service: RunControlService | None = None


def get_run_control_service() -> RunControlService:
    global _run_control_service
    if _run_control_service is None:
        _run_control_service = RunControlService()
    return _run_control_service
