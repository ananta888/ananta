"""Hub-owned workflow-control binding contracts and in-memory adapter."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_control_service import (
    WorkflowPrincipal,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRouteAuthorizationService,
    WorkflowRoutePrincipal,
    WorkflowRunOwner,
)
from agent.services.workflow_runtime._serialization import canonical_json


@dataclass(frozen=True)
class WorkflowControlRunBinding:
    """Durable Hub owner/request/runtime binding for legacy route calls."""

    tenant_id: str
    subject_id: str
    workflow_id: str
    run_id: str
    runtime_id: str
    plan_hash: str
    policy_version: str
    checkpoint_id: str
    request: WorkflowRequest
    execution_plan: dict[str, Any] = field(default_factory=dict)


class WorkflowControlBindingStore(Protocol):
    def put(self, binding: WorkflowControlRunBinding) -> None: ...

    def get(self, workflow_id: str) -> WorkflowControlRunBinding | None: ...

    def get_by_run_id(self, run_id: str) -> WorkflowControlRunBinding | None: ...

    def bind_runtime(self, workflow_id: str, *, plan_hash: str, runtime_id: str) -> WorkflowControlRunBinding: ...

    def list_reconcilable(self, *, runtime_id: str, limit: int = 100) -> tuple[WorkflowControlRunBinding, ...]: ...

    def claim_reconcilable(
        self,
        *,
        runtime_id: str,
        owner_id: str,
        lease_seconds: float,
        limit: int = 100,
    ) -> tuple[WorkflowControlRunBinding, ...]: ...

    def finish_reconciliation(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        expected_revision: int,
        expected_checkpoint_ref: str,
        status: dict[str, Any],
    ) -> None: ...

    def release_reconciliation(self, workflow_id: str, *, owner_id: str) -> None: ...

    def discard(self, workflow_id: str, *, plan_hash: str = "") -> None: ...

    def record_status(self, workflow_id: str, status: dict[str, Any]) -> None: ...

    def last_status(self, workflow_id: str) -> dict[str, Any] | None: ...

    def record_public_status(self, workflow_id: str, status: dict[str, Any]) -> None: ...

    def last_public_status(self, workflow_id: str) -> dict[str, Any] | None: ...

    def claim_command(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        checkpoint_id: str,
        command_id: str,
    ) -> None: ...

    def finish_command(
        self,
        workflow_id: str,
        *,
        command_id: str,
        status: dict[str, Any],
    ) -> None: ...

    def mark_command_observation_pending(
        self,
        workflow_id: str,
        *,
        command_id: str,
        minimum_revision: int,
        expected_status: str = "",
        reconciliation_ready: bool = True,
    ) -> None: ...

    def release_command(self, workflow_id: str, *, command_id: str) -> None: ...

    def bind_command_receipt(
        self,
        workflow_id: str,
        *,
        receipt_id: str,
        expected_revision: int,
        checkpoint_ref: str,
    ) -> None: ...

    def clear_command_receipt(self, workflow_id: str, *, receipt_id: str) -> None: ...

    def finish_command_receipt(
        self,
        workflow_id: str,
        *,
        receipt_id: str,
        status: dict[str, Any],
    ) -> None: ...

    def reject_command_receipt(self, workflow_id: str, *, receipt_id: str) -> None: ...


class WorkflowControlTransitionFenceStore(Protocol):
    """Read-only marker port preventing legacy mutation during a transition.

    The transition aggregate is the only marker write authority.  Legacy
    binding adapters may observe that authority, but must never manufacture or
    clear a marker independently of the outbox unit of work.
    """

    def active_transition_id(self, workflow_id: str) -> str: ...


class InMemoryWorkflowControlBindingStore:
    """Thread-safe, explicit containment store for unit/dev composition.

    Transition admission is disabled in default composition.  Tests that own a
    complete in-memory transition aggregate may inject only its read-only fence
    view; this store never creates a second marker authority.
    """

    def __init__(
        self,
        *,
        clock: Any = time.time,
        transition_fences: WorkflowControlTransitionFenceStore | None = None,
    ) -> None:
        self._bindings: dict[str, WorkflowControlRunBinding] = {}
        self._run_ids: dict[str, str] = {}
        self._statuses: dict[str, dict[str, Any]] = {}
        self._public_statuses: dict[str, dict[str, Any]] = {}
        self._runtime_revisions: dict[str, int] = {}
        self._checkpoint_refs: dict[str, str] = {}
        self._command_claims: dict[str, str] = {}
        self._command_claim_expiry: dict[str, float] = {}
        self._command_observation_pending: dict[str, bool] = {}
        self._command_observation_min_revision: dict[str, int] = {}
        self._command_observation_expected_status: dict[str, str] = {}
        self._dispatch_intents: dict[str, str] = {}
        self._command_receipts: dict[str, str] = {}
        self._scheduler_claims: dict[str, tuple[str, float]] = {}
        self._clock = clock
        self._transition_fences = transition_fences
        self._lock = threading.RLock()

    def put(self, binding: WorkflowControlRunBinding) -> None:
        with self._lock:
            previous = self._bindings.get(binding.workflow_id)
            if previous is not None or binding.run_id in self._run_ids:
                raise RuntimeError("workflow_control_binding_already_exists")
            self._bindings[binding.workflow_id] = deepcopy(binding)
            self._run_ids[binding.run_id] = binding.workflow_id
            self._statuses.pop(binding.workflow_id, None)
            self._public_statuses.pop(binding.workflow_id, None)
            self._runtime_revisions[binding.workflow_id] = 0
            self._checkpoint_refs[binding.workflow_id] = binding.checkpoint_id
            self._command_claims[binding.workflow_id] = ""
            self._command_claim_expiry[binding.workflow_id] = 0.0
            self._command_observation_pending[binding.workflow_id] = False
            self._command_observation_min_revision[binding.workflow_id] = 0
            self._command_observation_expected_status[binding.workflow_id] = ""
            self._dispatch_intents[binding.workflow_id] = ""
            self._command_receipts[binding.workflow_id] = ""
            self._scheduler_claims[binding.workflow_id] = ("", 0.0)

    def bind_runtime(self, workflow_id: str, *, plan_hash: str, runtime_id: str) -> WorkflowControlRunBinding:
        normalized = str(workflow_id or "").strip()
        selected = str(runtime_id or "").strip()
        with self._lock:
            binding = self._bindings.get(normalized)
            if binding is None or binding.plan_hash != str(plan_hash):
                raise RuntimeError("workflow_control_binding_not_found")
            if self.active_transition_id(normalized):
                raise RuntimeError("workflow_control_runtime_binding_conflict")
            if binding.runtime_id not in {"pending", selected}:
                raise RuntimeError("workflow_control_runtime_binding_conflict")
            if binding.runtime_id == "pending":
                binding = replace(binding, runtime_id=selected)
                self._bindings[normalized] = binding
            return deepcopy(binding)

    def get(self, workflow_id: str) -> WorkflowControlRunBinding | None:
        with self._lock:
            binding = self._bindings.get(str(workflow_id or "").strip())
            return deepcopy(binding) if binding is not None else None

    def get_by_run_id(self, run_id: str) -> WorkflowControlRunBinding | None:
        with self._lock:
            workflow_id = self._run_ids.get(str(run_id or "").strip())
            binding = self._bindings.get(workflow_id) if workflow_id is not None else None
            return deepcopy(binding) if binding is not None else None

    def active_transition_id(self, workflow_id: str) -> str:
        normalized = str(workflow_id or "").strip()
        if not normalized or self._transition_fences is None:
            return ""
        return str(self._transition_fences.active_transition_id(normalized) or "")

    def list_reconcilable(self, *, runtime_id: str, limit: int = 100) -> tuple[WorkflowControlRunBinding, ...]:
        bounded = max(1, min(int(limit), 1000))
        now = float(self._clock())
        with self._lock:
            values = []
            for workflow_id in sorted(self._bindings):
                binding = self._bindings[workflow_id]
                status = self._statuses.get(workflow_id) or {}
                if (
                    binding.runtime_id == str(runtime_id)
                    and bool(status)
                    and not self._dispatch_intents.get(workflow_id)
                    and not self._command_receipts.get(workflow_id)
                    and not self.active_transition_id(workflow_id)
                    and (
                        not self._command_claims.get(workflow_id)
                        or self._command_claim_expiry.get(workflow_id, 0.0) <= now
                    )
                    and (
                        self._command_observation_pending.get(workflow_id, False)
                        or str(status.get("status") or "").lower() not in {"completed", "failed", "cancelled"}
                    )
                ):
                    values.append(deepcopy(binding))
                    if len(values) >= bounded:
                        break
            return tuple(values)

    def claim_reconcilable(
        self,
        *,
        runtime_id: str,
        owner_id: str,
        lease_seconds: float,
        limit: int = 100,
    ) -> tuple[WorkflowControlRunBinding, ...]:
        now = float(self._clock())
        claimed: list[WorkflowControlRunBinding] = []
        with self._lock:
            for binding in self.list_reconcilable(runtime_id=runtime_id, limit=limit * 4):
                workflow_id = binding.workflow_id
                command = self._command_claims.get(workflow_id, "")
                if command and self._command_claim_expiry.get(workflow_id, 0.0) > now:
                    continue
                current_owner, expires = self._scheduler_claims.get(workflow_id, ("", 0.0))
                if current_owner and expires > now:
                    continue
                self._scheduler_claims[workflow_id] = (
                    str(owner_id),
                    now + max(1.0, float(lease_seconds)),
                )
                claimed.append(binding)
                if len(claimed) >= max(1, int(limit)):
                    break
        return tuple(claimed)

    def finish_reconciliation(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        expected_revision: int,
        expected_checkpoint_ref: str,
        status: dict[str, Any],
    ) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            owner, _expires = self._scheduler_claims.get(normalized, ("", 0.0))
            command = self._command_claims.get(normalized, "")
            if (
                owner != str(owner_id)
                or self.active_transition_id(normalized)
                or self._runtime_revisions.get(normalized, 0) != int(expected_revision)
                or self._checkpoint_refs.get(normalized, "") != str(expected_checkpoint_ref)
                or self._command_receipts.get(normalized)
                or (command and not self._command_observation_pending.get(normalized, False))
            ):
                raise RuntimeError("workflow_control_reconciliation_cas_conflict")
            self._scheduler_claims[normalized] = ("", 0.0)
            self._assert_command_observation_fence(normalized, status)
            self._record_status(normalized, status)
            self._command_claims[normalized] = ""
            self._command_claim_expiry[normalized] = 0.0
            self._command_observation_pending[normalized] = False
            self._command_observation_min_revision[normalized] = 0
            self._command_observation_expected_status[normalized] = ""

    def release_reconciliation(self, workflow_id: str, *, owner_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            owner, _expires = self._scheduler_claims.get(normalized, ("", 0.0))
            if owner == str(owner_id) and not self.active_transition_id(normalized):
                self._scheduler_claims[normalized] = ("", 0.0)

    def discard(self, workflow_id: str, *, plan_hash: str = "") -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            binding = self._bindings.get(normalized)
            if binding is None or (plan_hash and binding.plan_hash != str(plan_hash)):
                return
            if self.active_transition_id(normalized):
                raise RuntimeError("workflow_control_binding_transition_active")
            self._bindings.pop(normalized, None)
            self._run_ids.pop(binding.run_id, None)
            self._statuses.pop(normalized, None)
            self._public_statuses.pop(normalized, None)
            self._runtime_revisions.pop(normalized, None)
            self._checkpoint_refs.pop(normalized, None)
            self._command_claims.pop(normalized, None)
            self._command_claim_expiry.pop(normalized, None)
            self._command_observation_pending.pop(normalized, None)
            self._command_observation_min_revision.pop(normalized, None)
            self._command_observation_expected_status.pop(normalized, None)
            self._dispatch_intents.pop(normalized, None)
            self._command_receipts.pop(normalized, None)
            self._scheduler_claims.pop(normalized, None)

    def record_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if (
                self._command_claims.get(normalized)
                or self._command_receipts.get(normalized)
                or self.active_transition_id(normalized)
            ):
                raise RuntimeError("workflow_control_binding_revision_conflict")
            self._record_status(normalized, status)

    def last_status(self, workflow_id: str) -> dict[str, Any] | None:
        with self._lock:
            status = self._statuses.get(str(workflow_id or "").strip())
            return deepcopy(status) if status is not None else None

    def record_public_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if normalized not in self._bindings:
                raise RuntimeError("workflow_control_binding_not_found")
            if self.active_transition_id(normalized):
                raise RuntimeError("workflow_control_public_status_cas_conflict")
            previous = self._public_statuses.get(normalized)
            assert_public_status_progression(previous, status)
            self._public_statuses[normalized] = deepcopy(status)

    def last_public_status(self, workflow_id: str) -> dict[str, Any] | None:
        with self._lock:
            status = self._public_statuses.get(str(workflow_id or "").strip())
            return deepcopy(status) if status is not None else None

    def claim_command(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        checkpoint_id: str,
        command_id: str,
    ) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            scheduler_owner, scheduler_expires = self._scheduler_claims.get(normalized, ("", 0.0))
            if (
                normalized not in self._bindings
                or self.active_transition_id(normalized)
                or self._command_observation_pending.get(normalized, False)
                or (scheduler_owner and scheduler_expires > float(self._clock()))
                or (
                    self._command_claims.get(normalized)
                    and self._command_claim_expiry.get(normalized, 0.0) > float(self._clock())
                )
                or self._command_receipts.get(normalized, "") not in {"", str(command_id)}
                or self._runtime_revisions.get(normalized, 0) != int(expected_revision)
                or self._checkpoint_refs.get(normalized) != str(checkpoint_id)
            ):
                raise RuntimeError("workflow_control_command_cas_conflict")
            self._command_claims[normalized] = str(command_id)
            self._command_claim_expiry[normalized] = float(self._clock()) + 300.0
            self._command_observation_pending[normalized] = False
            self._command_observation_min_revision[normalized] = 0
            self._command_observation_expected_status[normalized] = ""

    def bind_dispatch_intent(self, workflow_id: str, *, intent_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if (
                normalized not in self._bindings
                or self.active_transition_id(normalized)
                or self._dispatch_intents.get(normalized)
                or self._command_receipts.get(normalized)
            ):
                raise RuntimeError("workflow_control_dispatch_stage_cas_conflict")
            self._dispatch_intents[normalized] = str(intent_id)

    def clear_dispatch_intent(self, workflow_id: str, *, intent_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if self._dispatch_intents.get(normalized) != str(intent_id):
                raise RuntimeError("workflow_control_dispatch_completion_conflict")
            if self.active_transition_id(normalized):
                raise RuntimeError("workflow_control_dispatch_completion_conflict")
            self._dispatch_intents[normalized] = ""

    def bind_command_receipt(
        self,
        workflow_id: str,
        *,
        receipt_id: str,
        expected_revision: int,
        checkpoint_ref: str,
    ) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            scheduler_owner, scheduler_expires = self._scheduler_claims.get(
                normalized,
                ("", 0.0),
            )
            if (
                normalized not in self._bindings
                or self.active_transition_id(normalized)
                or self._dispatch_intents.get(normalized)
                or self._command_receipts.get(normalized)
                or self._command_claims.get(normalized)
                or (scheduler_owner and scheduler_expires > float(self._clock()))
                or self._runtime_revisions.get(normalized, 0) != int(expected_revision)
                or self._checkpoint_refs.get(normalized) != str(checkpoint_ref)
            ):
                raise RuntimeError("workflow_control_command_receipt_stage_conflict")
            self._command_receipts[normalized] = str(receipt_id)

    def clear_command_receipt(self, workflow_id: str, *, receipt_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if (
                self._command_receipts.get(normalized) != str(receipt_id)
                or self._command_claims.get(normalized)
                or self.active_transition_id(normalized)
            ):
                raise RuntimeError("workflow_control_command_receipt_completion_conflict")
            self._command_receipts[normalized] = ""

    def finish_command_receipt(
        self,
        workflow_id: str,
        *,
        receipt_id: str,
        status: dict[str, Any],
    ) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if (
                self._command_receipts.get(normalized) != str(receipt_id)
                or self._command_claims.get(normalized)
                or self.active_transition_id(normalized)
            ):
                raise RuntimeError("workflow_control_command_receipt_completion_conflict")
            del status
            self._command_receipts[normalized] = ""

    def reject_command_receipt(self, workflow_id: str, *, receipt_id: str) -> None:
        self.clear_command_receipt(workflow_id, receipt_id=receipt_id)

    def finish_command(
        self,
        workflow_id: str,
        *,
        command_id: str,
        status: dict[str, Any],
    ) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if (
                self._command_claims.get(normalized) != str(command_id)
                or self.active_transition_id(normalized)
            ):
                raise RuntimeError("workflow_control_command_finish_conflict")
            self._assert_command_observation_fence(normalized, status)
            self._record_status(normalized, status)
            self._command_claims[normalized] = ""
            self._command_claim_expiry[normalized] = 0.0
            self._command_observation_pending[normalized] = False
            self._command_observation_min_revision[normalized] = 0
            self._command_observation_expected_status[normalized] = ""

    def mark_command_observation_pending(
        self,
        workflow_id: str,
        *,
        command_id: str,
        minimum_revision: int,
        expected_status: str = "",
        reconciliation_ready: bool = True,
    ) -> None:
        normalized = str(workflow_id or "").strip()
        minimum = _command_observation_revision(minimum_revision)
        status = _command_observation_status(expected_status)
        ready = _command_observation_readiness(reconciliation_ready)
        with self._lock:
            if (
                self._command_claims.get(normalized) != str(command_id)
                or self.active_transition_id(normalized)
            ):
                raise RuntimeError("workflow_control_command_pending_conflict")
            current_minimum = self._command_observation_min_revision.get(normalized, 0)
            current_status = self._command_observation_expected_status.get(normalized, "")
            if minimum < current_minimum:
                if ready:
                    self._command_claim_expiry[normalized] = 0.0
                return
            if minimum == current_minimum and current_status and status and current_status != status:
                raise RuntimeError("workflow_control_command_pending_fence_conflict")
            self._command_observation_pending[normalized] = True
            if minimum > current_minimum:
                self._command_observation_min_revision[normalized] = minimum
                self._command_observation_expected_status[normalized] = status
            elif status:
                self._command_observation_expected_status[normalized] = status
            if ready:
                self._command_claim_expiry[normalized] = 0.0

    def release_command(self, workflow_id: str, *, command_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if (
                self._command_claims.get(normalized) == str(command_id)
                and not self._command_observation_pending.get(normalized, False)
                and not self.active_transition_id(normalized)
            ):
                self._command_claims[normalized] = ""
                self._command_claim_expiry[normalized] = 0.0
                self._command_observation_min_revision[normalized] = 0
                self._command_observation_expected_status[normalized] = ""

    def _assert_command_observation_fence(
        self,
        workflow_id: str,
        status: dict[str, Any],
    ) -> None:
        if not self._command_observation_pending.get(workflow_id, False):
            return
        _assert_command_observation_status(
            status,
            minimum_revision=self._command_observation_min_revision.get(workflow_id, 0),
            expected_status=self._command_observation_expected_status.get(workflow_id, ""),
        )

    def _record_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        if isinstance(status.get("revision"), bool):
            raise RuntimeError("workflow_control_runtime_revision_invalid")
        try:
            revision = int(status.get("revision", 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("workflow_control_runtime_revision_invalid") from exc
        if revision < 0:
            raise RuntimeError("workflow_control_runtime_revision_invalid")
        binding = self._bindings.get(workflow_id)
        if binding is None:
            raise RuntimeError("workflow_control_binding_not_found")
        if binding.runtime_id != "temporal":
            assert_runtime_status_progression(
                self._statuses.get(workflow_id),
                status,
            )
        self._statuses[workflow_id] = deepcopy(status)
        self._runtime_revisions[workflow_id] = revision
        self._checkpoint_refs[workflow_id] = str(
            status.get("checkpoint_ref") or self._checkpoint_refs.get(workflow_id) or ""
        )


def _command_observation_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError("workflow_control_command_pending_revision_invalid")
    return value


def assert_public_status_progression(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> None:
    revision = _public_revision(current)
    if previous is None:
        return
    old_revision = _public_revision(previous)
    if revision < old_revision:
        raise RuntimeError("workflow_control_public_status_revision_regressed")
    if (
        revision == old_revision
        and not _is_initial_public_pending(previous)
        and _public_signature(previous) != _public_signature(current)
    ):
        raise RuntimeError("workflow_control_public_status_revision_conflict")


def assert_runtime_status_progression(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> None:
    """Fence non-Temporal raw state before it reaches the public projector."""

    revision = _runtime_status_revision(current)
    if previous is None:
        return
    old_revision = _runtime_status_revision(previous)
    if revision < old_revision:
        raise RuntimeError("workflow_control_runtime_revision_regressed")
    if revision == old_revision and _public_signature(previous) != _public_signature(current):
        raise RuntimeError("workflow_control_runtime_revision_conflict")


def _runtime_status_revision(status: dict[str, Any]) -> int:
    value = status.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("workflow_control_runtime_revision_invalid")
    return value


def _is_initial_public_pending(status: dict[str, Any]) -> bool:
    source = status.get("source_observation")
    return (
        status.get("revision") == 0
        and status.get("status") == "pending"
        and isinstance(source, dict)
        and "revision" not in source
    )


def _public_revision(status: dict[str, Any]) -> int:
    value = status.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("workflow_control_public_status_revision_invalid")
    return value


def _public_signature(status: dict[str, Any]) -> str:
    return canonical_json(
        {key: value for key, value in status.items() if key not in {"events", "event_cursor", "updated_at"}}
    )


def _command_observation_status(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 64:
        raise RuntimeError("workflow_control_command_pending_status_invalid")
    if any(not character.isprintable() or character in {"\x00", "\x7f"} for character in value):
        raise RuntimeError("workflow_control_command_pending_status_invalid")
    return value


def _command_observation_readiness(value: Any) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError("workflow_control_command_pending_readiness_invalid")
    return value


def _assert_command_observation_status(
    status: dict[str, Any],
    *,
    minimum_revision: int,
    expected_status: str,
) -> None:
    source = status.get("source_observation")
    if not isinstance(source, dict):
        raise RuntimeError("workflow_control_command_observation_fence_conflict")
    revision = source.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < minimum_revision
        or (revision == minimum_revision and expected_status and source.get("status") != expected_status)
    ):
        raise RuntimeError("workflow_control_command_observation_fence_conflict")


class WorkflowRouteControlAuthorization:
    """Adapt the fail-closed route ownership boundary to the control port."""

    def __init__(self, ownership: WorkflowRouteAuthorizationService) -> None:
        self._ownership = ownership

    def authorize(
        self,
        *,
        principal: WorkflowPrincipal,
        action: str,
        workflow_id: str,
        run_id: str = "",
    ) -> str:
        del action, run_id
        route_principal = WorkflowRoutePrincipal(
            tenant_id=principal.tenant_id,
            subject=principal.subject_id,
        )
        if self._ownership.is_authorized(workflow_id, route_principal):
            return "allowed"
        return "workflow_run_not_found"


class WorkflowControlBindingOwnerResolver:
    """Reconstruct HTTP route ownership from the durable Hub binding."""

    def __init__(self, bindings: WorkflowControlBindingStore) -> None:
        self._bindings = bindings

    def resolve(self, workflow_id: str) -> WorkflowRunOwner | None:
        binding = self._bindings.get(workflow_id)
        if binding is None:
            return None
        return WorkflowRunOwner(
            workflow_id=binding.workflow_id,
            tenant_id=binding.tenant_id,
            subject=binding.subject_id,
        )


__all__ = [
    "InMemoryWorkflowControlBindingStore",
    "WorkflowControlBindingOwnerResolver",
    "WorkflowControlBindingStore",
    "WorkflowControlRunBinding",
    "WorkflowControlTransitionFenceStore",
    "WorkflowRouteControlAuthorization",
]
