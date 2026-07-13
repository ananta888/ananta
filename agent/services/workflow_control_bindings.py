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

    def bind_runtime(
        self, workflow_id: str, *, plan_hash: str, runtime_id: str
    ) -> WorkflowControlRunBinding: ...

    def list_reconcilable(
        self, *, runtime_id: str, limit: int = 100
    ) -> tuple[WorkflowControlRunBinding, ...]: ...

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

    def release_command(self, workflow_id: str, *, command_id: str) -> None: ...


class InMemoryWorkflowControlBindingStore:
    """Thread-safe, explicit containment store for unit/dev composition."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._bindings: dict[str, WorkflowControlRunBinding] = {}
        self._run_ids: dict[str, str] = {}
        self._statuses: dict[str, dict[str, Any]] = {}
        self._runtime_revisions: dict[str, int] = {}
        self._checkpoint_refs: dict[str, str] = {}
        self._command_claims: dict[str, str] = {}
        self._command_claim_expiry: dict[str, float] = {}
        self._scheduler_claims: dict[str, tuple[str, float]] = {}
        self._clock = clock
        self._lock = threading.RLock()

    def put(self, binding: WorkflowControlRunBinding) -> None:
        with self._lock:
            previous = self._bindings.get(binding.workflow_id)
            if previous is not None or binding.run_id in self._run_ids:
                raise RuntimeError("workflow_control_binding_already_exists")
            self._bindings[binding.workflow_id] = deepcopy(binding)
            self._run_ids[binding.run_id] = binding.workflow_id
            self._statuses.pop(binding.workflow_id, None)
            self._runtime_revisions[binding.workflow_id] = 0
            self._checkpoint_refs[binding.workflow_id] = binding.checkpoint_id
            self._command_claims[binding.workflow_id] = ""
            self._command_claim_expiry[binding.workflow_id] = 0.0
            self._scheduler_claims[binding.workflow_id] = ("", 0.0)

    def bind_runtime(
        self, workflow_id: str, *, plan_hash: str, runtime_id: str
    ) -> WorkflowControlRunBinding:
        normalized = str(workflow_id or "").strip()
        selected = str(runtime_id or "").strip()
        with self._lock:
            binding = self._bindings.get(normalized)
            if binding is None or binding.plan_hash != str(plan_hash):
                raise RuntimeError("workflow_control_binding_not_found")
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

    def list_reconcilable(
        self, *, runtime_id: str, limit: int = 100
    ) -> tuple[WorkflowControlRunBinding, ...]:
        bounded = max(1, min(int(limit), 1000))
        now = float(self._clock())
        with self._lock:
            values = []
            for workflow_id in sorted(self._bindings):
                binding = self._bindings[workflow_id]
                status = self._statuses.get(workflow_id) or {}
                if (
                    binding.runtime_id == str(runtime_id)
                    and (
                        not self._command_claims.get(workflow_id)
                        or self._command_claim_expiry.get(workflow_id, 0.0) <= now
                    )
                    and str(status.get("status") or "").lower()
                    not in {"completed", "failed", "cancelled"}
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
                if current_owner and current_owner != owner_id and expires > now:
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
            if (
                owner != str(owner_id)
                or self._runtime_revisions.get(normalized, 0) != int(expected_revision)
                or self._checkpoint_refs.get(normalized, "") != str(expected_checkpoint_ref)
            ):
                raise RuntimeError("workflow_control_reconciliation_cas_conflict")
            self._scheduler_claims[normalized] = ("", 0.0)
            self._record_status(normalized, status)

    def release_reconciliation(self, workflow_id: str, *, owner_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            owner, _expires = self._scheduler_claims.get(normalized, ("", 0.0))
            if owner == str(owner_id):
                self._scheduler_claims[normalized] = ("", 0.0)

    def discard(self, workflow_id: str, *, plan_hash: str = "") -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            binding = self._bindings.get(normalized)
            if binding is None or (plan_hash and binding.plan_hash != str(plan_hash)):
                return
            self._bindings.pop(normalized, None)
            self._run_ids.pop(binding.run_id, None)
            self._statuses.pop(normalized, None)
            self._runtime_revisions.pop(normalized, None)
            self._checkpoint_refs.pop(normalized, None)
            self._command_claims.pop(normalized, None)
            self._command_claim_expiry.pop(normalized, None)
            self._scheduler_claims.pop(normalized, None)

    def record_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if self._command_claims.get(normalized):
                raise RuntimeError("workflow_control_binding_revision_conflict")
            self._record_status(normalized, status)

    def last_status(self, workflow_id: str) -> dict[str, Any] | None:
        with self._lock:
            status = self._statuses.get(str(workflow_id or "").strip())
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
            if (
                normalized not in self._bindings
                or (
                    self._command_claims.get(normalized)
                    and self._command_claim_expiry.get(normalized, 0.0)
                    > float(self._clock())
                )
                or self._runtime_revisions.get(normalized, 0) != int(expected_revision)
                or self._checkpoint_refs.get(normalized) != str(checkpoint_id)
            ):
                raise RuntimeError("workflow_control_command_cas_conflict")
            self._command_claims[normalized] = str(command_id)
            self._command_claim_expiry[normalized] = float(self._clock()) + 300.0

    def finish_command(
        self,
        workflow_id: str,
        *,
        command_id: str,
        status: dict[str, Any],
    ) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if self._command_claims.get(normalized) != str(command_id):
                raise RuntimeError("workflow_control_command_finish_conflict")
            self._record_status(normalized, status)
            self._command_claims[normalized] = ""
            self._command_claim_expiry[normalized] = 0.0

    def release_command(self, workflow_id: str, *, command_id: str) -> None:
        normalized = str(workflow_id or "").strip()
        with self._lock:
            if self._command_claims.get(normalized) == str(command_id):
                self._command_claims[normalized] = ""
                self._command_claim_expiry[normalized] = 0.0

    def _record_status(self, workflow_id: str, status: dict[str, Any]) -> None:
        try:
            revision = int(status.get("revision", 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("workflow_control_runtime_revision_invalid") from exc
        if revision < 0:
            raise RuntimeError("workflow_control_runtime_revision_invalid")
        self._statuses[workflow_id] = deepcopy(status)
        self._runtime_revisions[workflow_id] = revision
        self._checkpoint_refs[workflow_id] = str(
            status.get("checkpoint_ref")
            or self._checkpoint_refs.get(workflow_id)
            or ""
        )


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
    "WorkflowRouteControlAuthorization",
]
