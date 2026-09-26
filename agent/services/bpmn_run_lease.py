"""Signed, bounded Hub control-operation leases on the existing checkpoint port.

The lease is per invocation, never an assumed worker fence or process singleton.
It serializes BPMN control mutations across Hub instances. Mutation recipients
still enforce their own CAS/fences; this is not a cross-store transaction.
"""

from __future__ import annotations

import math
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace

from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.security import SignedCheckpoint, WorkflowState

_TASK = "bpmn-control-lease:v1"
_RUNTIME = "hub-bpmn-control-lease"


def is_bpmn_plan(plan) -> bool:
    return bool(plan.metadata.get("bpmn_definition_hash") or plan.metadata.get("bpmn_activation_expansion"))


@dataclass(frozen=True)
class BpmnRunLease:
    checkpoint: SignedCheckpoint
    store: object
    keys: object
    clock: object

    @property
    def fencing_token(self) -> int:
        return self.checkpoint.fencing_token

    def ensure_valid(self) -> None:
        current = self.store.get_latest(
            tenant_id=self.checkpoint.tenant_id, run_id=self.checkpoint.run_id, task_id=_TASK
        )
        self.assert_current(
            current,
            tenant_id=self.checkpoint.tenant_id,
            workflow_id=self.checkpoint.workflow_id,
            run_id=self.checkpoint.run_id,
        )

    def assert_current(self, current, *, tenant_id: str, workflow_id: str, run_id: str) -> None:
        """Validate the authoritative row locked by a mutation recipient.

        The recipient supplies its same-transaction snapshot, never a separate
        pre-write read through ``store``. This method performs no persistence.
        """
        if (tenant_id, workflow_id, run_id) != (
            self.checkpoint.tenant_id,
            self.checkpoint.workflow_id,
            self.checkpoint.run_id,
        ):
            raise OptimisticConcurrencyError("bpmn_recipient_lease_binding_mismatch")
        if current is None:
            raise OptimisticConcurrencyError("bpmn_control_lease_missing")
        _verify(current, self.checkpoint, self.keys)
        if (
            current.checkpoint_id != self.checkpoint.checkpoint_id
            or _now(self.clock) >= current.state.business_data["expires_at"]
        ):
            raise OptimisticConcurrencyError("bpmn_control_lease_stale")


class BpmnRunLeaseService:
    def __init__(self, *, checkpoints, keys, clock, lease_seconds=30.0):
        if type(lease_seconds) not in {int, float} or not math.isfinite(lease_seconds) or not 0 < lease_seconds <= 300:
            raise ValueError("bpmn_control_lease_duration_invalid")
        self._store, self._keys, self._clock = checkpoints, keys, clock
        self._duration = float(lease_seconds)

    @contextmanager
    def acquire(self, request):
        if not is_bpmn_plan(request.plan):
            yield None
            return
        current = self._store.get_latest(tenant_id=request.plan.tenant_id, run_id=request.run_id, task_id=_TASK)
        now = _now(self._clock)
        if current is not None:
            current.verify(
                key_ring=self._keys,
                tenant_id=request.plan.tenant_id,
                workflow_id=request.plan.workflow_id,
                run_id=request.run_id,
                task_id=_TASK,
                plan_hash=request.plan.plan_hash,
                policy_version=request.plan.policy_version,
            )
            _structure(current)
            if current.state.business_data["control_task_id"] != request.control_task_id:
                raise OptimisticConcurrencyError("bpmn_control_task_binding_mismatch")
            if current.state.business_data["expires_at"] > now:
                raise OptimisticConcurrencyError("bpmn_control_lease_held")
        deadline = now + self._duration
        if not math.isfinite(deadline):
            raise ValueError("bpmn_control_lease_deadline_invalid")
        lease_checkpoint = SignedCheckpoint.issue(
            key_ring=self._keys,
            tenant_id=request.plan.tenant_id,
            workflow_id=request.plan.workflow_id,
            run_id=request.run_id,
            task_id=_TASK,
            plan_hash=request.plan.plan_hash,
            policy_version=request.plan.policy_version,
            runtime_id=_RUNTIME,
            runtime_version="1",
            state=WorkflowState(
                business_data={
                    "owner": uuid.uuid4().hex,
                    "expires_at": deadline,
                    "control_task_id": request.control_task_id,
                }
            ),
            revision=current.revision + 1 if current else 1,
            fencing_token=current.fencing_token + 1 if current else 1,
            now=now,
        )
        saved = self._store.save(lease_checkpoint, expected_revision=current.revision if current else 0)
        lease = BpmnRunLease(saved, self._store, self._keys, self._clock)
        try:
            yield lease
        finally:
            # Never release another invocation's lease, including after expiry.
            latest = self._store.get_latest(tenant_id=saved.tenant_id, run_id=saved.run_id, task_id=_TASK)
            if latest is not None and latest.checkpoint_id == saved.checkpoint_id:
                released = SignedCheckpoint.issue(
                    key_ring=self._keys,
                    tenant_id=saved.tenant_id,
                    workflow_id=saved.workflow_id,
                    run_id=saved.run_id,
                    task_id=_TASK,
                    plan_hash=saved.plan_hash,
                    policy_version=saved.policy_version,
                    runtime_id=_RUNTIME,
                    runtime_version="1",
                    revision=saved.revision + 1,
                    fencing_token=saved.fencing_token,
                    now=_now(self._clock),
                    state=replace(saved.state, business_data={**saved.state.business_data, "expires_at": 0.0}),
                )
                try:
                    self._store.save(released, expected_revision=saved.revision)
                except OptimisticConcurrencyError:
                    pass  # A concurrent successor won; it owns the new fence.


def _now(clock):
    value = float(clock())
    if not math.isfinite(value) or value < 0:
        raise ValueError("bpmn_control_clock_invalid")
    return value


def _structure(checkpoint):
    data = checkpoint.state.business_data
    if (
        checkpoint.runtime_id != _RUNTIME
        or checkpoint.runtime_version != "1"
        or set(data) != {"owner", "expires_at", "control_task_id"}
        or not isinstance(data["owner"], str)
        or not data["owner"]
        or type(data["expires_at"]) not in {int, float}
        or not math.isfinite(data["expires_at"])
        or data["expires_at"] < 0
    ):
        raise OptimisticConcurrencyError("bpmn_control_lease_invalid")


def _verify(current, binding, keys):
    current.verify(
        key_ring=keys,
        tenant_id=binding.tenant_id,
        workflow_id=binding.workflow_id,
        run_id=binding.run_id,
        task_id=_TASK,
        plan_hash=binding.plan_hash,
        policy_version=binding.policy_version,
    )
    _structure(current)
