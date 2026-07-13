"""Checkpoint persistence and verification for the Native graph runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.services.native_graph_models import NativeGraphRequest, NativeRunState
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.persistence import CheckpointStore
from agent.services.workflow_runtime.security import HmacKeyRing, SignedCheckpoint


class NativeGraphCheckpointService:
    """Small persistence component injected into the Hub orchestrator."""

    def __init__(
        self,
        *,
        checkpoints: CheckpointStore,
        key_ring: HmacKeyRing,
        runtime_id: str,
        runtime_version: str,
        clock: Callable[[], float],
        compile_plan: Callable[[ExecutionPlan], ExecutionPlan],
    ) -> None:
        self._checkpoints = checkpoints
        self._key_ring = key_ring
        self._runtime_id = runtime_id
        self._runtime_version = runtime_version
        self._clock = clock
        self._compile_plan = compile_plan

    def save(
        self,
        *,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        state: NativeRunState,
        emit: Callable[..., Any],
    ) -> SignedCheckpoint:
        latest = self._checkpoints.get_latest(
            tenant_id=plan.tenant_id,
            run_id=request.run_id,
            task_id=request.control_task_id,
        )
        revision = (latest.revision + 1) if latest else 1
        fence = max(
            [
                latest.fencing_token if latest else 1,
                *[
                    int(value.get("fencing_token") or 0)
                    for value in state.running.values()
                ],
            ],
        )
        emit(
            state,
            plan=plan,
            request=request,
            event_type="workflow.checkpoint.created",
            dedupe_key=f"native:{request.run_id}:checkpoint:{revision}",
            payload={"revision": revision, "runtime_id": self._runtime_id},
        )
        checkpoint = SignedCheckpoint.issue(
            key_ring=self._key_ring,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            task_id=request.control_task_id,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            runtime_id=self._runtime_id,
            runtime_version=self._runtime_version,
            state=state.to_workflow_state(secret_refs=request.secret_refs),
            revision=revision,
            fencing_token=fence,
            now=float(self._clock()),
        )
        return self._checkpoints.save(checkpoint, expected_revision=revision - 1)

    def load_verified(
        self,
        *,
        requested_plan: ExecutionPlan,
        request: NativeGraphRequest,
    ) -> tuple[SignedCheckpoint, NativeRunState, ExecutionPlan]:
        checkpoint = self._checkpoints.get_latest(
            tenant_id=requested_plan.tenant_id,
            run_id=request.run_id,
            task_id=request.control_task_id,
        )
        if checkpoint is None:
            raise KeyError("native_graph_checkpoint_not_found")
        state = NativeRunState.from_workflow_state(checkpoint.state)
        plan = self.effective_plan(
            requested_plan=requested_plan,
            state=state,
            checkpoint=checkpoint,
        )
        self.verify(checkpoint=checkpoint, plan=plan, request=request)
        return checkpoint, state, plan

    def effective_plan(
        self,
        *,
        requested_plan: ExecutionPlan,
        state: NativeRunState,
        checkpoint: SignedCheckpoint,
    ) -> ExecutionPlan:
        base_plan_hash = state.base_plan_hash or checkpoint.plan_hash
        if requested_plan.plan_hash != base_plan_hash:
            raise ValueError("native_graph_base_plan_binding_mismatch")
        effective = (
            ExecutionPlan.from_mapping(dict(state.effective_plan))
            if state.effective_plan
            else requested_plan
        )
        if (
            effective.tenant_id != requested_plan.tenant_id
            or effective.workflow_id != requested_plan.workflow_id
            or effective.policy_version != requested_plan.policy_version
        ):
            raise ValueError("native_graph_effective_plan_binding_mismatch")
        return self._compile_plan(effective)

    def verify(
        self,
        *,
        checkpoint: SignedCheckpoint,
        plan: ExecutionPlan,
        request: NativeGraphRequest,
    ) -> None:
        checkpoint.verify(
            key_ring=self._key_ring,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=request.run_id,
            task_id=request.control_task_id,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
        )
        if checkpoint.runtime_id != self._runtime_id:
            raise ValueError("native_graph_cross_runtime_checkpoint_denied")
        if checkpoint.runtime_version != self._runtime_version:
            raise ValueError("native_graph_runtime_version_unsupported")


__all__ = ["NativeGraphCheckpointService"]
