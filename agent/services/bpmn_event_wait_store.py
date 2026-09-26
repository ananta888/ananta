"""Signed wait-aggregate adapter over the existing Hub checkpoint CAS port."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from agent.services.bpmn_event_wait_contracts import EventWaitConflict, WaitRunBinding
from agent.services.workflow_runtime.errors import FencingTokenError
from agent.services.workflow_runtime.persistence import CheckpointStore
from agent.services.workflow_runtime.security import (
    SignatureSigningKeyRingPort,
    SignedCheckpoint,
    WorkflowState,
)

WAIT_STATE_SCHEMA = "ananta.bpmn_event_wait.v1"
WAIT_CHECKPOINT_TASK = "bpmn-event-waits:v1"
WAIT_RUNTIME = "hub-bpmn-event-waits"


@dataclass(frozen=True)
class WaitSnapshot:
    revision: int
    fencing_token: int
    state: dict[str, Any]


class EventWaitStore(Protocol):
    """Read returns detached state; commit atomically checks revision and fence."""

    def load(self, run: WaitRunBinding) -> WaitSnapshot: ...

    def for_lease(self, lease: Any) -> EventWaitStore:
        """Bind a fresh adapter to one real control operation's recipient fence."""
        ...

    def commit(
        self,
        run: WaitRunBinding,
        *,
        previous: WaitSnapshot,
        state: dict[str, Any],
        fencing_token: int,
        now: float,
    ) -> None: ...


class CheckpointEventWaitStore:
    """No tables or alternate task queue; one independent checkpoint stream/run.

    Durability is that of the supplied CheckpointStore. InMemoryCheckpointStore
    is test-only; use a durable implementation in runtime composition.
    """

    def __init__(self, checkpoints: CheckpointStore, key_ring: SignatureSigningKeyRingPort, *, lease=None):
        self._checkpoints = checkpoints
        self._key_ring = key_ring
        self._lease = lease

    def for_lease(self, lease) -> CheckpointEventWaitStore:
        if lease is None or not callable(getattr(self._checkpoints, "save_fenced", None)):
            raise FencingTokenError("bpmn_wait_fenced_store_required")
        return CheckpointEventWaitStore(self._checkpoints, self._key_ring, lease=lease)

    def load(self, run: WaitRunBinding) -> WaitSnapshot:
        checkpoint = self._checkpoints.get_latest(
            tenant_id=run.tenant_id,
            run_id=run.run_id,
            task_id=WAIT_CHECKPOINT_TASK,
        )
        if checkpoint is None:
            return WaitSnapshot(
                0,
                0,
                {
                    "schema": WAIT_STATE_SCHEMA,
                    "binding": asdict(run),
                    "observed_at": 0.0,
                    "closed": False,
                    "waits": {},
                    "messages": {},
                },
            )
        checkpoint.verify(
            key_ring=self._key_ring,
            tenant_id=run.tenant_id,
            workflow_id=run.workflow_id,
            run_id=run.run_id,
            task_id=WAIT_CHECKPOINT_TASK,
            plan_hash=run.plan_hash,
            policy_version=run.policy_version,
        )
        state = checkpoint.state.business_data
        if (
            checkpoint.runtime_id != WAIT_RUNTIME
            or checkpoint.runtime_version != "1"
            or state.get("schema") != WAIT_STATE_SCHEMA
            or state.get("binding") != asdict(run)
        ):
            raise EventWaitConflict("bpmn_wait_checkpoint_binding_mismatch")
        return WaitSnapshot(checkpoint.revision, checkpoint.fencing_token, state)

    def commit(
        self,
        run: WaitRunBinding,
        *,
        previous: WaitSnapshot,
        state: dict[str, Any],
        fencing_token: int,
        now: float,
    ) -> None:
        if fencing_token < previous.fencing_token:
            raise FencingTokenError("bpmn_wait_fencing_token_stale")
        if self._lease is not None and fencing_token != self._lease.fencing_token:
            raise FencingTokenError("bpmn_wait_lease_fence_mismatch")
        checkpoint = SignedCheckpoint.issue(
            key_ring=self._key_ring,
            tenant_id=run.tenant_id,
            workflow_id=run.workflow_id,
            run_id=run.run_id,
            task_id=WAIT_CHECKPOINT_TASK,
            plan_hash=run.plan_hash,
            policy_version=run.policy_version,
            runtime_id=WAIT_RUNTIME,
            runtime_version="1",
            state=WorkflowState(business_data=state),
            revision=previous.revision + 1,
            fencing_token=fencing_token,
            now=now,
        )
        if self._lease is not None:
            self._checkpoints.save_fenced(checkpoint, expected_revision=previous.revision, lease=self._lease)
        else:
            # Component-only compatibility. Native always binds for_lease().
            self._checkpoints.save(checkpoint, expected_revision=previous.revision)
