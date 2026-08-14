from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.services.native_graph_production_composition import (
    build_native_graph_workflow_control_bridge,
)
from agent.services.workflow_authorization_grant_service import (
    SQLAlchemyWorkflowAuthorizationGrantService,
)
from agent.services.workflow_backend import (
    WorkflowRequest,
    WorkflowStepRequest,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_persistence import (
    SQLAlchemyWorkflowCommandReplayNonceStore,
    SQLAlchemyWorkflowControlBindingStore,
)
from agent.services.workflow_control_service import (
    RuntimeSelection,
    WorkflowPrincipal,
)
from agent.services.workflow_provider_selection_service import (
    WorkflowProviderDecision,
)
from agent.services.workflow_runtime.commands import WorkflowCommandIssuer
from agent.services.workflow_runtime.execution_plan import (
    WorkflowRequestExecutionPlanAdapter,
)
from agent.services.workflow_runtime.native_graph_contracts import HubTaskReceipt
from agent.services.workflow_runtime.security import HmacKeyRing


class RecordingHubTaskQueue:
    def __init__(self) -> None:
        self.commands = []
        self.cancelled: list[str] = []

    def submit(self, command):
        self.commands.append(command)
        return HubTaskReceipt(
            hub_task_id=f"native-task-{len(self.commands)}",
            command_id=command.command_id,
            accepted=True,
        )

    def poll(self, **_values):
        return ()

    def cancel(self, *, hub_task_ids, **_values):
        self.cancelled.extend(hub_task_ids)


class NoProviderRequired:
    def decide(self, _requirement):
        return WorkflowProviderDecision(
            status="not_required",
            binding=None,
            reason_code="provider_not_required",
        )


class FailFirstCommandFinishStore(SQLAlchemyWorkflowControlBindingStore):
    def __init__(self, engine) -> None:
        super().__init__(engine)
        self.fail_command_finish = True

    def finish_command(self, workflow_id, *, command_id, status):
        if self.fail_command_finish:
            self.fail_command_finish = False
            raise RuntimeError("injected_binding_finish_failure")
        return super().finish_command(
            workflow_id,
            command_id=command_id,
            status=status,
        )


def _request() -> WorkflowRequest:
    return WorkflowRequest(
        workflow_id="native-production",
        plan_id="native-production-plan",
        policy_scope={"policy_version": "policy-v1"},
        metadata={"run_id": "native-production-run"},
        steps=(
            WorkflowStepRequest(
                step_id="code",
                title="code",
                task_kind="coding",
                policy_scope={"policy_version": "policy-v1"},
            ),
        ),
    )


def test_native_production_bridge_uses_sql_state_and_hub_queue_after_restart() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    keys = HmacKeyRing({"native-production": "n" * 32}, active_key_id="native-production")
    bindings = SQLAlchemyWorkflowControlBindingStore(engine)
    replay = SQLAlchemyWorkflowCommandReplayNonceStore(engine)
    grants = SQLAlchemyWorkflowAuthorizationGrantService(engine)
    queue = RecordingHubTaskQueue()
    request = _request()
    plan = WorkflowRequestExecutionPlanAdapter.adapt(
        request,
        tenant_id="tenant-a",
        policy_version="policy-v1",
    )
    binding = WorkflowControlRunBinding(
        tenant_id="tenant-a",
        subject_id="owner-a",
        workflow_id=request.workflow_id,
        run_id="native-production-run",
        runtime_id="local",
        plan_hash=plan.plan_hash,
        policy_version=plan.policy_version,
        checkpoint_id=f"legacy-current:{plan.plan_hash[:24]}",
        request=request,
    )
    bindings.put(binding)
    principal = WorkflowPrincipal("tenant-a", "owner-a")
    bridge = build_native_graph_workflow_control_bridge(
        engine=engine,
        bindings=bindings,
        key_ring=keys,
        replay_store=replay,
        authorization_grants=grants,
        provider_decisions=NoProviderRequired(),
        queue=queue,
    )
    assert bridge._orchestrator._components is not None  # noqa: SLF001 - composition probe

    handle = bridge.start(
        principal=principal,
        plan=plan,
        run_id=binding.run_id,
        selection=RuntimeSelection(
            runtime_id="ananta-native",
            capabilities=frozenset(),
            mode="live",
            reason_code="selected",
        ),
        authorization_envelope={
            "schema": "ananta.workflow_route_control.v1",
            "tenant_id": "tenant-a",
            "subject_id": "owner-a",
            "workflow_id": request.workflow_id,
            "run_id": binding.run_id,
        },
    )
    first_status = bindings.last_status(request.workflow_id)

    assert handle.runtime_id == "ananta-native"
    assert first_status is not None
    assert first_status["backend"] == "local"
    assert first_status["runtime_id"] == "ananta-native"
    assert first_status["status"] == "running"
    assert all("gate" not in step for step in first_status["steps"])
    assert len(queue.commands) == 1
    first_checkpoint = bridge._orchestrator.checkpoint(  # noqa: SLF001 - contract probe
        bridge._request(binding)  # noqa: SLF001 - contract probe
    )
    running_state = dict(first_checkpoint.state.runtime_metadata["running"])
    assert running_state["code"]["grant_ref"] == (queue.commands[0].authorization.envelope_id)
    assert "authorization_envelope_id" not in running_state["code"]
    assert grants.revalidate(queue.commands[0].authorization) is True

    restarted = build_native_graph_workflow_control_bridge(
        engine=engine,
        bindings=SQLAlchemyWorkflowControlBindingStore(engine),
        key_ring=keys,
        replay_store=SQLAlchemyWorkflowCommandReplayNonceStore(engine),
        authorization_grants=SQLAlchemyWorkflowAuthorizationGrantService(engine),
        provider_decisions=NoProviderRequired(),
        queue=queue,
    )
    history_before_query = restarted.history(
        principal=principal,
        run_id=binding.run_id,
    )
    first_query = restarted.query(principal=principal, run_id=binding.run_id)
    second_query = restarted.query(principal=principal, run_id=binding.run_id)

    assert first_query == second_query
    assert first_query["revision"] == first_status["revision"]
    assert first_query["checkpoint_ref"] == first_status["checkpoint_ref"]
    assert (
        restarted.history(
            principal=principal,
            run_id=binding.run_id,
        )
        == history_before_query
    )
    assert len(queue.commands) == 1

    reconciliation = restarted.reconcile_active()
    resumed_status = restarted.query(principal=principal, run_id=binding.run_id)

    assert reconciliation == {
        "runtime_id": "ananta-native",
        "processed": 1,
        "failed": [],
    }
    assert resumed_status["runtime_id"] == "ananta-native"
    assert resumed_status["revision"] > first_status["revision"]
    assert resumed_status["checkpoint_ref"] != first_status["checkpoint_ref"]

    issuer = WorkflowCommandIssuer(keys)
    cancel = issuer.issue(
        command_id="cancel-native-production",
        command_type="cancel",
        tenant_id="tenant-a",
        workflow_id=request.workflow_id,
        run_id=binding.run_id,
        step_id="__workflow__",
        checkpoint_id=resumed_status["checkpoint_ref"],
        expected_revision=resumed_status["revision"],
        plan_hash=plan.plan_hash,
        policy_version=plan.policy_version,
        actor_id="owner-a",
        actor_roles=(),
        payload={"reason": "operator"},
    )
    cancelled = restarted.cancel(principal=principal, command=cancel)

    assert cancelled["status"] == "cancelled"
    assert queue.cancelled == ["native-task-1"]
    assert grants.revalidate(queue.commands[0].authorization) is False
    assert any(
        event["event_type"] == "workflow.run.cancelled"
        for event in restarted.history(principal=principal, run_id=binding.run_id)
    )


def test_native_command_checkpoint_receipt_recovers_binding_finish_loss() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    keys = HmacKeyRing({"native-production": "n" * 32}, active_key_id="native-production")
    bindings = FailFirstCommandFinishStore(engine)
    queue = RecordingHubTaskQueue()
    request = _request()
    plan = WorkflowRequestExecutionPlanAdapter.adapt(
        request,
        tenant_id="tenant-a",
        policy_version="policy-v1",
    )
    binding = WorkflowControlRunBinding(
        tenant_id="tenant-a",
        subject_id="owner-a",
        workflow_id=request.workflow_id,
        run_id="native-production-run",
        runtime_id="local",
        plan_hash=plan.plan_hash,
        policy_version=plan.policy_version,
        checkpoint_id=f"legacy-current:{plan.plan_hash[:24]}",
        request=request,
    )
    bindings.put(binding)
    principal = WorkflowPrincipal("tenant-a", "owner-a")
    bridge = build_native_graph_workflow_control_bridge(
        engine=engine,
        bindings=bindings,
        key_ring=keys,
        replay_store=SQLAlchemyWorkflowCommandReplayNonceStore(engine),
        authorization_grants=SQLAlchemyWorkflowAuthorizationGrantService(engine),
        provider_decisions=NoProviderRequired(),
        queue=queue,
    )
    bridge.start(
        principal=principal,
        plan=plan,
        run_id=binding.run_id,
        selection=RuntimeSelection(
            runtime_id="ananta-native",
            capabilities=frozenset(),
            mode="live",
            reason_code="selected",
        ),
        authorization_envelope={
            "schema": "ananta.workflow_route_control.v1",
            "tenant_id": "tenant-a",
            "subject_id": "owner-a",
            "workflow_id": request.workflow_id,
            "run_id": binding.run_id,
        },
    )
    before = bindings.last_status(request.workflow_id)
    assert before is not None
    issuer = WorkflowCommandIssuer(keys)

    def command():
        return issuer.issue(
            command_id="cancel-native-finish-loss",
            command_type="cancel",
            tenant_id="tenant-a",
            workflow_id=request.workflow_id,
            run_id=binding.run_id,
            step_id="__workflow__",
            checkpoint_id=before["checkpoint_ref"],
            expected_revision=before["revision"],
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            actor_id="owner-a",
            actor_roles=(),
            payload={"reason": "operator"},
        )

    with pytest.raises(RuntimeError, match="injected_binding_finish_failure"):
        bridge.cancel(principal=principal, command=command())

    persisted_checkpoint = bridge._orchestrator.checkpoint(  # noqa: SLF001
        bridge._request(binding)  # noqa: SLF001
    )
    assert persisted_checkpoint.revision == before["revision"] + 1
    assert bindings.last_status(request.workflow_id) == before
    assert queue.cancelled == ["native-task-1"]

    restarted_bindings = SQLAlchemyWorkflowControlBindingStore(engine)
    restarted = build_native_graph_workflow_control_bridge(
        engine=engine,
        bindings=restarted_bindings,
        key_ring=keys,
        replay_store=SQLAlchemyWorkflowCommandReplayNonceStore(engine),
        authorization_grants=SQLAlchemyWorkflowAuthorizationGrantService(engine),
        provider_decisions=NoProviderRequired(),
        queue=queue,
    )
    recovered = restarted.recover_command(principal=principal, command=command())

    assert recovered["status"] == "cancelled"
    assert recovered["revision"] == before["revision"] + 1
    assert queue.cancelled == ["native-task-1"]
    assert restarted_bindings.last_status(request.workflow_id) == recovered
