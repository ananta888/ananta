"""Hub-owned idempotency receipts for synchronous workflow controls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_transition_outbox import (
    workflow_admitted_command_digest,
    workflow_transition_request_fingerprint,
)

COMMAND_RECEIPT_PENDING = "pending"
COMMAND_RECEIPT_DISPATCHING = "dispatching"
COMMAND_RECEIPT_COMPLETED = "completed"
COMMAND_RECEIPT_REJECTED = "rejected"

_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_COMMAND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_REQUEST_BYTES = 80_000
_MAX_RESULT_BYTES = 524_288
_PUBLIC_STATUS_KEYS = frozenset(
    {
        "active_step_ids",
        "backend",
        "checkpoint_ref",
        "completed_step_ids",
        "correlation_id",
        "created_at",
        "current_step_id",
        "error",
        "event_cursor",
        "events",
        "failed_step_ids",
        "finished_at",
        "gate",
        "open_gates",
        "plan_hash",
        "plan_revision",
        "process_id",
        "process_version",
        "reason",
        "reason_code",
        "retry_budget_remaining",
        "revision",
        "run_id",
        "runtime_id",
        "schema",
        "snapshot_hash",
        "source_observation",
        "started_at",
        "status",
        "steps",
        "temporal",
        "tenant_id",
        "updated_at",
        "workflow_id",
    }
)


class WorkflowControlCommandReceiptError(RuntimeError):
    """Stable fail-closed command-receipt contract error."""


class WorkflowControlCommandRejectedError(PermissionError):
    """A strictly identified command was rejected before durable mutation."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = _reason_code(reason_code)
        super().__init__("workflow_control_command_rejected")


@dataclass(frozen=True)
class WorkflowControlCommandReceipt:
    command_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    actor_id: str
    command_type: str
    request_payload: dict[str, Any]
    expected_revision: int
    checkpoint_ref: str
    state: str = COMMAND_RECEIPT_PENDING
    result_status: dict[str, Any] | None = None
    rejection_reason: str = ""
    dispatch_owner: str = ""
    dispatch_lease_expires_at: float = 0.0
    request_fingerprint: str = ""
    transition_id: str = ""
    effect_fingerprint: str = ""
    outcome_fingerprint: str = ""
    dispatch_generation: int = 0
    last_heartbeat_at: float = 0.0
    revision: int = 1

    def __post_init__(self) -> None:
        for name in ("command_id", "tenant_id", "workflow_id", "run_id", "actor_id"):
            _identity(getattr(self, name), name)
        if not _COMMAND_RE.fullmatch(self.command_type):
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_type_invalid")
        if (
            isinstance(self.expected_revision, bool)
            or not isinstance(self.expected_revision, int)
            or self.expected_revision < 0
        ):
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_revision_invalid")
        _bounded_text(self.checkpoint_ref, 512, "checkpoint_ref")
        if self.state not in {
            COMMAND_RECEIPT_PENDING,
            COMMAND_RECEIPT_DISPATCHING,
            COMMAND_RECEIPT_COMPLETED,
            COMMAND_RECEIPT_REJECTED,
        }:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_state_invalid")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_record_revision_invalid")
        if (
            isinstance(self.dispatch_generation, bool)
            or not isinstance(self.dispatch_generation, int)
            or self.dispatch_generation < 0
        ):
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_generation_invalid")
        if self.last_heartbeat_at < 0:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_heartbeat_invalid")
        for value, name in (
            (self.request_fingerprint, "request_fingerprint"),
            (self.effect_fingerprint, "effect_fingerprint"),
            (self.outcome_fingerprint, "outcome_fingerprint"),
        ):
            if value and _SHA256_RE.fullmatch(value) is None:
                raise WorkflowControlCommandReceiptError(f"workflow_control_command_receipt_{name}_invalid")
        if self.transition_id:
            _identity(self.transition_id, "transition_id")
            if not self.request_fingerprint or not self.effect_fingerprint:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_attribution_invalid")
        elif self.effect_fingerprint or self.outcome_fingerprint:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_attribution_invalid")
        if self.outcome_fingerprint and self.state != COMMAND_RECEIPT_COMPLETED:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_outcome_invalid")
        if (
            self.transition_id
            and self.state == COMMAND_RECEIPT_COMPLETED
            and not self.outcome_fingerprint
        ):
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_outcome_invalid")
        _bounded_mapping(
            self.request_payload,
            maximum=_MAX_REQUEST_BYTES,
            reason="request",
        )
        result = self.result_status
        if self.state == COMMAND_RECEIPT_COMPLETED:
            _bounded_mapping(
                result,
                maximum=_MAX_RESULT_BYTES,
                reason="result",
                empty=False,
            )
        elif result not in (None, {}):
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_result_invalid")
        if self.state == COMMAND_RECEIPT_REJECTED:
            if not self.rejection_reason or _reason_code(self.rejection_reason) != self.rejection_reason:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_rejection_invalid")
        elif self.rejection_reason:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_rejection_invalid")
        if self.state == COMMAND_RECEIPT_DISPATCHING:
            _identity(self.dispatch_owner, "dispatch_owner")
            legacy_pre_generation_lease = (
                not self.transition_id and self.dispatch_generation == 0 and self.last_heartbeat_at == 0
            )
            generation_fenced_lease = self.dispatch_generation >= 1 and self.last_heartbeat_at > 0
            if self.dispatch_lease_expires_at <= 0 or not (
                legacy_pre_generation_lease or generation_fenced_lease
            ):
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_lease_invalid")
        elif self.dispatch_owner or self.dispatch_lease_expires_at != 0:
            raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_lease_invalid")


class WorkflowControlCommandReceiptStore(Protocol):
    def stage(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command_id: str,
        actor_id: str,
        command_type: str,
        request_payload: dict[str, Any],
        expected_revision: int,
        checkpoint_ref: str,
    ) -> WorkflowControlCommandReceipt: ...

    def get(self, command_id: str) -> WorkflowControlCommandReceipt | None: ...

    def claim(
        self,
        command_id: str,
        *,
        owner_id: str,
        lease_seconds: float = 30.0,
    ) -> WorkflowControlCommandReceipt | None: ...

    def release(
        self,
        command_id: str,
        *,
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt: ...

    def heartbeat(
        self,
        command_id: str,
        *,
        owner_id: str,
        dispatch_generation: int,
        lease_seconds: float = 30.0,
    ) -> WorkflowControlCommandReceipt: ...

    def complete(
        self,
        command_id: str,
        *,
        status: dict[str, Any],
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt: ...

    def reject(
        self,
        command_id: str,
        *,
        reason_code: str,
        owner_id: str,
        dispatch_generation: int,
    ) -> WorkflowControlCommandReceipt: ...

    def list_pending(self, *, limit: int = 100) -> tuple[WorkflowControlCommandReceipt, ...]: ...


class WorkflowControlCommandReceiptReconciler:
    """Finish receipts whose canonical binding status already crossed its fence."""

    def __init__(
        self,
        *,
        receipts: WorkflowControlCommandReceiptStore,
        bindings: Any,
        project: Callable[[WorkflowControlRunBinding, dict[str, Any]], dict[str, Any]],
        recover: Callable[[WorkflowControlCommandReceipt, WorkflowControlRunBinding], dict[str, Any]],
        owner_id: str,
    ) -> None:
        self._receipts = receipts
        self._bindings = bindings
        self._project = project
        self._recover = recover
        self._owner_id = _identity(owner_id, "owner_id")

    def reconcile_workflow(self, workflow_id: str) -> bool:
        normalized = str(workflow_id or "").strip()
        for receipt in self._receipts.list_pending(limit=1000):
            if receipt.workflow_id != normalized:
                continue
            _assert_legacy_receipt(receipt)
            claimed = self._receipts.claim(
                receipt.command_id,
                owner_id=self._owner_id,
            )
            if claimed is None:
                continue
            receipt = claimed
            status = self._bindings.last_status(normalized)
            binding = self._bindings.get(normalized)
            if binding is None:
                raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_binding_missing")
            if status is None or status_revision(status) <= receipt.expected_revision:
                try:
                    status = self._recover(receipt, binding)
                except WorkflowControlCommandRejectedError as exc:
                    self._receipts.reject(
                        receipt.command_id,
                        reason_code=exc.reason_code,
                        owner_id=self._owner_id,
                        dispatch_generation=receipt.dispatch_generation,
                    )
                    raise
            receipt = self._receipts.heartbeat(
                receipt.command_id,
                owner_id=self._owner_id,
                dispatch_generation=receipt.dispatch_generation,
            )
            self._receipts.complete(
                receipt.command_id,
                status=self._project(binding, status),
                owner_id=self._owner_id,
                dispatch_generation=receipt.dispatch_generation,
            )
            return True
        return False

    def drain(self, *, limit: int = 100) -> dict[str, Any]:
        processed = 0
        failed: list[dict[str, str]] = []
        for receipt in self._receipts.list_pending(limit=limit):
            try:
                _assert_legacy_receipt(receipt)
                claimed = self._receipts.claim(
                    receipt.command_id,
                    owner_id=self._owner_id,
                )
                if claimed is None:
                    continue
                receipt = claimed
                status = self._bindings.last_status(receipt.workflow_id)
                binding = self._bindings.get(receipt.workflow_id)
                if binding is None:
                    raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_binding_missing")
                if status is None or status_revision(status) <= receipt.expected_revision:
                    try:
                        status = self._recover(receipt, binding)
                    except WorkflowControlCommandRejectedError as exc:
                        self._receipts.reject(
                            receipt.command_id,
                            reason_code=exc.reason_code,
                            owner_id=self._owner_id,
                            dispatch_generation=receipt.dispatch_generation,
                        )
                        processed += 1
                        continue
                receipt = self._receipts.heartbeat(
                    receipt.command_id,
                    owner_id=self._owner_id,
                    dispatch_generation=receipt.dispatch_generation,
                )
                self._receipts.complete(
                    receipt.command_id,
                    status=self._project(binding, status),
                    owner_id=self._owner_id,
                    dispatch_generation=receipt.dispatch_generation,
                )
                processed += 1
            except Exception as exc:
                failed.append(
                    {
                        "workflow_id": receipt.workflow_id,
                        "command_id": receipt.command_id,
                        "error_type": type(exc).__name__,
                    }
                )
        return {
            "processed": processed,
            "failed": failed,
        }


def assert_exact_receipt_request(
    receipt: WorkflowControlCommandReceipt,
    *,
    binding: WorkflowControlRunBinding,
    actor_id: str,
    command_type: str,
    request_payload: dict[str, Any],
) -> None:
    admitted = receipt.request_payload.get("admitted_command")
    requested_admitted = request_payload.get("admitted_command")
    if (
        receipt.tenant_id != binding.tenant_id
        or receipt.workflow_id != binding.workflow_id
        or receipt.run_id != binding.run_id
        or receipt.actor_id != actor_id
        or receipt.command_type != command_type
        or _semantic_request(receipt.request_payload) != _semantic_request(request_payload)
        or not isinstance(admitted, dict)
        or not isinstance(requested_admitted, dict)
        or workflow_admitted_command_digest(admitted)
        != workflow_admitted_command_digest(requested_admitted)
    ):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_conflict")
    if receipt.request_fingerprint and receipt.request_fingerprint != workflow_transition_request_fingerprint(
        receipt.request_payload
    ):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_conflict")


def admitted_receipt_command(
    receipt: WorkflowControlCommandReceipt,
) -> SignedWorkflowCommand:
    raw = receipt.request_payload.get("admitted_command")
    if not isinstance(raw, dict):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_admission_missing")
    try:
        command = SignedWorkflowCommand.from_mapping(raw)
    except (TypeError, ValueError) as exc:
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_admission_invalid") from exc
    if (
        command.command_id != receipt.command_id
        or command.tenant_id != receipt.tenant_id
        or command.workflow_id != receipt.workflow_id
        or command.run_id != receipt.run_id
        or command.actor_id != receipt.actor_id
        or command.command_type != receipt.command_type
        or command.expected_revision != receipt.expected_revision
        or command.checkpoint_id != receipt.checkpoint_ref
    ):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_admission_mismatch")
    return command


def assert_stable_receipt_retry(
    receipt: WorkflowControlCommandReceipt,
    *,
    binding: WorkflowControlRunBinding,
    actor_id: str,
    command_type: str,
    payload: dict[str, Any],
) -> None:
    """Validate caller-stable fields without renewing command authority."""

    command = admitted_receipt_command(receipt)
    stored_payload = receipt.request_payload.get("payload")
    stored_step_id = receipt.request_payload.get("step_id")
    stored_roles = receipt.request_payload.get("actor_roles")
    if (
        receipt.tenant_id != binding.tenant_id
        or receipt.workflow_id != binding.workflow_id
        or receipt.run_id != binding.run_id
        or receipt.actor_id != actor_id
        or receipt.command_type != command_type
        or command.plan_hash != binding.plan_hash
        or command.policy_version != binding.policy_version
        or not isinstance(stored_payload, dict)
        or stored_payload != payload
        or command.payload != payload
        or not isinstance(stored_step_id, str)
        or stored_step_id != command.step_id
        or not isinstance(stored_roles, list)
        or tuple(sorted(str(role) for role in stored_roles)) != tuple(sorted(command.actor_roles))
    ):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_conflict")


def _semantic_request(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"actor_roles", "admitted_command"}}


def _assert_legacy_receipt(receipt: WorkflowControlCommandReceipt) -> None:
    if receipt.transition_id:
        raise WorkflowControlCommandReceiptError("workflow_control_command_transition_pending")


def status_revision(status: dict[str, Any]) -> int:
    revision = status.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_status_revision_invalid")
    return revision


def validate_result_status(
    receipt: WorkflowControlCommandReceipt,
    status: dict[str, Any],
) -> None:
    _bounded_mapping(
        status,
        maximum=_MAX_RESULT_BYTES,
        reason="result",
        empty=False,
    )
    if status_revision(status) <= receipt.expected_revision:
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_observation_pending")


def validate_persisted_public_status(
    receipt: WorkflowControlCommandReceipt,
    binding: WorkflowControlRunBinding,
    status: dict[str, Any],
) -> None:
    """Validate an immutable receipt result without rewriting provenance."""

    validate_result_status(receipt, status)
    if (
        frozenset(status) - _PUBLIC_STATUS_KEYS
        or status.get("schema") != WORKFLOW_STATUS_SCHEMA
        or status.get("tenant_id") != binding.tenant_id
        or status.get("workflow_id") != binding.workflow_id
        or status.get("run_id") != binding.run_id
        or status.get("plan_hash") != binding.plan_hash
        or not isinstance(status.get("source_observation"), dict)
    ):
        raise WorkflowControlCommandReceiptError("workflow_control_command_receipt_public_status_invalid")


def _reason_code(value: Any) -> str:
    normalized = str(value or "").strip()
    if not _COMMAND_RE.fullmatch(normalized):
        return "workflow_control_command_rejected"
    return normalized


def _identity(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise WorkflowControlCommandReceiptError(f"workflow_control_command_receipt_{field_name}_invalid")
    return value


def _bounded_text(value: Any, maximum: int, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(not character.isprintable() or character in {"\x00", "\x7f"} for character in value)
    ):
        raise WorkflowControlCommandReceiptError(f"workflow_control_command_receipt_{field_name}_invalid")
    return value


def _bounded_mapping(
    value: Any,
    *,
    maximum: int,
    reason: str,
    empty: bool = True,
) -> None:
    if not isinstance(value, dict) or (not empty and not value):
        raise WorkflowControlCommandReceiptError(f"workflow_control_command_receipt_{reason}_invalid")
    try:
        size = len(canonical_json(value).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowControlCommandReceiptError(f"workflow_control_command_receipt_{reason}_invalid") from exc
    if size > maximum:
        raise WorkflowControlCommandReceiptError(f"workflow_control_command_receipt_{reason}_too_large")


__all__ = [
    "COMMAND_RECEIPT_COMPLETED",
    "COMMAND_RECEIPT_DISPATCHING",
    "COMMAND_RECEIPT_PENDING",
    "COMMAND_RECEIPT_REJECTED",
    "WorkflowControlCommandReceipt",
    "WorkflowControlCommandReceiptError",
    "WorkflowControlCommandReceiptReconciler",
    "WorkflowControlCommandRejectedError",
    "WorkflowControlCommandReceiptStore",
    "assert_exact_receipt_request",
    "admitted_receipt_command",
    "assert_stable_receipt_retry",
    "status_revision",
    "validate_persisted_public_status",
    "validate_result_status",
]
