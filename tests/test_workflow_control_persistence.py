from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.auth import generate_token
from agent.config import settings
from agent.routes.workflow_runtime_operations import workflow_runtime_operations_bp
from agent.services.local_workflow_backend import LocalWorkflowBackend
from agent.services.workflow_backend import WorkflowRequest, WorkflowSignal, WorkflowStepRequest
from agent.services.workflow_control_composition import (
    WorkflowControlBindingOwnerResolver,
    WorkflowControlRunBinding,
    build_workflow_backend_control_facade,
)
from agent.services.workflow_control_persistence import (
    SQLAlchemyWorkflowCommandReplayNonceStore,
    SQLAlchemyWorkflowControlBindingStore,
    WorkflowControlBindingPersistenceError,
)
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRouteAuthorizationService,
    WorkflowRoutePrincipal,
)
from agent.services.workflow_runtime import SQLAlchemyEventStore
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_runtime_command_service import (
    WorkflowAwareRuntimeCommandGateway,
    WorkflowRuntimeGatewayError,
)
from agent.services.workflow_runtime_read_model_persistence import (
    SQLAlchemyWorkflowRuntimeReadModelRepository,
)
from agent.services.workflow_runtime_read_model_service import WorkflowRuntimeReadModelService


@pytest.fixture
def control_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _request() -> WorkflowRequest:
    return WorkflowRequest(
        workflow_id="workflow-restart",
        metadata={"run_id": "run-restart"},
        policy_scope={"policy_version": "policy-v1"},
        steps=(
            WorkflowStepRequest(
                step_id="step-a",
                task_kind="coding",
                policy_scope={"policy_version": "policy-v1"},
            ),
        ),
    )


def _binding() -> WorkflowControlRunBinding:
    return WorkflowControlRunBinding(
        tenant_id="tenant-a",
        subject_id="owner-a",
        workflow_id="workflow-restart",
        run_id="run-restart",
        runtime_id="local",
        plan_hash="f" * 64,
        policy_version="policy-v1",
        checkpoint_id="checkpoint-0",
        request=_request(),
    )


def test_sql_control_binding_restores_owner_request_status_and_global_uniqueness(
    control_engine,
) -> None:
    first = SQLAlchemyWorkflowControlBindingStore(control_engine, clock=lambda: 10.0)
    first.put(_binding())
    first.record_status(
        "workflow-restart",
        {
            "workflow_id": "workflow-restart",
            "status": "running",
            "revision": 1,
            "checkpoint_ref": "local:workflow-restart:1",
        },
    )

    restarted = SQLAlchemyWorkflowControlBindingStore(control_engine, clock=lambda: 20.0)
    restored = restarted.get_by_run_id("run-restart")
    assert restored is not None
    assert restored.tenant_id == "tenant-a"
    assert restored.subject_id == "owner-a"
    assert restored.runtime_id == "local"
    assert restored.request.workflow_id == _request().workflow_id
    assert restored.request.metadata == _request().metadata
    assert restored.request.steps[0].step_id == "step-a"
    assert restarted.last_status("workflow-restart")["revision"] == 1

    ownership = WorkflowRouteAuthorizationService(
        WorkflowControlBindingOwnerResolver(restarted)
    )
    assert ownership.is_authorized(
        "workflow-restart",
        WorkflowRoutePrincipal("tenant-a", "owner-a"),
    )
    assert not ownership.is_authorized(
        "workflow-restart",
        WorkflowRoutePrincipal("tenant-b", "owner-a"),
    )

    duplicate = SimpleNamespace(
        **{
            **_binding().__dict__,
            "tenant_id": "tenant-b",
            "subject_id": "owner-b",
        }
    )
    with pytest.raises(
        WorkflowControlBindingPersistenceError,
        match="already_exists",
    ):
        restarted.put(duplicate)


def test_sql_control_command_cas_and_nonce_replay_survive_restart(control_engine) -> None:
    store = SQLAlchemyWorkflowControlBindingStore(control_engine)
    store.put(_binding())
    store.record_status(
        "workflow-restart",
        {
            "status": "running",
            "revision": 1,
            "checkpoint_ref": "checkpoint-1",
        },
    )
    store.claim_command(
        "workflow-restart",
        expected_revision=1,
        checkpoint_id="checkpoint-1",
        command_id="command-1",
    )
    with pytest.raises(WorkflowControlBindingPersistenceError, match="cas_conflict"):
        store.claim_command(
            "workflow-restart",
            expected_revision=1,
            checkpoint_id="checkpoint-1",
            command_id="command-race",
        )
    store.finish_command(
        "workflow-restart",
        command_id="command-1",
        status={
            "status": "paused",
            "revision": 2,
            "checkpoint_ref": "checkpoint-2",
        },
    )
    with pytest.raises(WorkflowControlBindingPersistenceError, match="cas_conflict"):
        store.claim_command(
            "workflow-restart",
            expected_revision=1,
            checkpoint_id="checkpoint-1",
            command_id="stale-command",
        )

    first_nonces = SQLAlchemyWorkflowCommandReplayNonceStore(
        control_engine,
        clock=lambda: 100.0,
    )
    assert first_nonces.consume(
        tenant_id="tenant-a",
        nonce="nonce-a",
        expires_at=200.0,
    )
    restarted_nonces = SQLAlchemyWorkflowCommandReplayNonceStore(
        control_engine,
        clock=lambda: 110.0,
    )
    assert not restarted_nonces.consume(
        tenant_id="tenant-a",
        nonce="nonce-a",
        expires_at=200.0,
    )
    assert restarted_nonces.consume(
        tenant_id="tenant-b",
        nonce="nonce-a",
        expires_at=200.0,
    )


def test_local_control_and_operations_projection_resume_after_hub_restart(
    control_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    store = SQLAlchemyWorkflowControlBindingStore(control_engine)
    read_models = WorkflowRuntimeReadModelService(
        SQLAlchemyWorkflowRuntimeReadModelRepository(control_engine)
    )
    first_ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert first_ownership.reserve("workflow-restart", principal) == "reserved"
    first_facade = build_workflow_backend_control_facade(
        LocalWorkflowBackend(),
        ownership=first_ownership,
        bindings=store,
        command_key_ring=key_ring,
        command_replay_store=SQLAlchemyWorkflowCommandReplayNonceStore(control_engine),
        read_model_projector=WorkflowControlReadModelProjector(
            read_models,
            event_store=SQLAlchemyEventStore(control_engine),
        ),
    )
    first = first_facade.bind(principal)
    assert first.start_workflow(_request())["status"] == "running"
    assert first.signal_workflow(
        "workflow-restart",
        WorkflowSignal(name="pause"),
    )["status"] == "paused"

    # New backend, composition, authorization cache and repository instances
    # model a Hub process restart while preserving only SQL state and the key.
    restarted_store = SQLAlchemyWorkflowControlBindingStore(control_engine)
    restarted_reads = WorkflowRuntimeReadModelService(
        SQLAlchemyWorkflowRuntimeReadModelRepository(control_engine)
    )
    restarted_facade = build_workflow_backend_control_facade(
        LocalWorkflowBackend(),
        ownership=WorkflowRouteAuthorizationService(),
        bindings=restarted_store,
        command_key_ring=key_ring,
        command_replay_store=SQLAlchemyWorkflowCommandReplayNonceStore(control_engine),
        read_model_projector=WorkflowControlReadModelProjector(
            restarted_reads,
            event_store=SQLAlchemyEventStore(control_engine),
        ),
    )
    restarted = restarted_facade.bind(principal)
    assert restarted.get_workflow_status("workflow-restart")["status"] == "paused"
    assert restarted.signal_workflow(
        "workflow-restart",
        WorkflowSignal(name="resume"),
    )["status"] == "running"

    projected = restarted_reads.get_record(
        tenant_id="tenant-a",
        run_id="run-restart",
    )
    assert projected is not None
    assert projected.status == "running"
    assert projected.source_sequence > 0
    rebuilt = WorkflowControlReadModelProjector(
        restarted_reads,
        event_store=SQLAlchemyEventStore(control_engine),
    ).rebuild(tenant_id="tenant-a", run_id="run-restart")
    assert rebuilt.status == "running"
    assert rebuilt.source_sequence == projected.source_sequence
    assert restarted_reads.get_record(
        tenant_id="tenant-b",
        run_id="run-restart",
    ) is None

    monkeypatch.setattr(
        "agent.routes.workflow_runtime_operations.get_workflow_runtime_read_model_service",
        lambda: restarted_reads,
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(workflow_runtime_operations_bp)
    client = app.test_client()

    def headers(tenant_id: str) -> dict[str, str]:
        token = generate_token(
            {"sub": "owner-a", "tenant_id": tenant_id, "role": "user"},
            settings.secret_key,
        )
        return {"Authorization": f"Bearer {token}"}

    visible = client.get(
        "/api/workflow-runtime/operations/runs/run-restart",
        headers=headers("tenant-a"),
    )
    assert visible.status_code == 200
    assert visible.get_json()["run"]["status"] == "running"
    foreign_tenant = client.get(
        "/api/workflow-runtime/operations/runs/run-restart",
        headers=headers("tenant-b"),
    )
    assert foreign_tenant.status_code == 404

    foreign = restarted_facade.bind(WorkflowRoutePrincipal("tenant-b", "owner-a"))
    with pytest.raises(PermissionError, match="workflow_run_not_found"):
        foreign.get_workflow_status("workflow-restart")


class _ForbiddenTaskGateway:
    def send(self, **_: object):
        raise AssertionError("workflow commands must not use RunControl")


def test_workflow_aware_gateway_concurrent_replay_returns_accepted_snapshot_once() -> None:
    side_effect_entered = Event()
    release_side_effect = Event()
    side_effect_count = 0

    class _Bindings:
        @staticmethod
        def get_by_run_id(_run_id: str):
            return SimpleNamespace(
                tenant_id="tenant-a",
                subject_id="owner-a",
                runtime_id="local",
                workflow_id="workflow-a",
            )

    class _Controlled:
        def command_workflow(self, *_args, **_kwargs):
            nonlocal side_effect_count
            side_effect_count += 1
            side_effect_entered.set()
            assert release_side_effect.wait(timeout=2.0)
            return {"status": "paused", "revision": 2}

    class _Facade:
        backend_id = "local"
        registry = SimpleNamespace(runtime_ids=("local",))

        @staticmethod
        def bind(_principal):
            return _Controlled()

    gateway = WorkflowAwareRuntimeCommandGateway(
        bindings=_Bindings(),
        facade_provider=lambda: _Facade(),
        task_gateway=_ForbiddenTaskGateway(),
    )
    request = {
        "tenant_id": "tenant-a",
        "command_type": "pause_run",
        "task_id": "task-a",
        "run_id": "run-a",
        "requested_by": "owner-a",
        "idempotency_key": "workflow-concurrent-key",
        "governance_context": {"approval_id": "approval-a", "evidence_refs": ["ev-a"]},
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(gateway.send, **request)
        assert side_effect_entered.wait(timeout=2.0)
        replay = pool.submit(gateway.send, **request).result(timeout=2.0)
        assert replay == {
            "command_id": "workflow-concurrent-key",
            "type": "pause_run",
            "status": "accepted",
            "run_id": "run-a",
            "workflow_id": "workflow-a",
        }
        release_side_effect.set()
        completed = owner.result(timeout=2.0)

    assert side_effect_count == 1
    assert gateway.send(**request) == completed
    assert side_effect_count == 1


def test_operations_gateway_uses_workflow_control_for_persisted_workflow_runs(
    control_engine,
) -> None:
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    store = SQLAlchemyWorkflowControlBindingStore(control_engine)
    ownership = WorkflowRouteAuthorizationService()
    principal = WorkflowRoutePrincipal("tenant-a", "owner-a")
    assert ownership.reserve("workflow-restart", principal) == "reserved"
    facade = build_workflow_backend_control_facade(
        LocalWorkflowBackend(),
        ownership=ownership,
        bindings=store,
        command_key_ring=key_ring,
        command_replay_store=SQLAlchemyWorkflowCommandReplayNonceStore(control_engine),
        read_model_projector=WorkflowControlReadModelProjector(
            WorkflowRuntimeReadModelService(
                SQLAlchemyWorkflowRuntimeReadModelRepository(control_engine)
            )
        ),
    )
    facade.bind(principal).start_workflow(_request())
    gateway = WorkflowAwareRuntimeCommandGateway(
        bindings=store,
        facade_provider=lambda: facade,
        task_gateway=_ForbiddenTaskGateway(),
    )

    result = gateway.send(
        tenant_id="tenant-a",
        command_type="pause_run",
        task_id="run-restart",
        run_id="run-restart",
        requested_by="owner-a",
        idempotency_key="runtime-ops:tenant-a:run-restart:pause-1",
        governance_context={"approval_id": "approval-a", "evidence_refs": ["ev-a"]},
    )
    assert result["status"] == "accepted"
    assert result["workflow_status"]["status"] == "paused"
    status_after_first_effect = store.last_status("workflow-restart")

    replay = gateway.send(
        tenant_id="tenant-a",
        command_type="pause_run",
        task_id="run-restart",
        run_id="run-restart",
        requested_by="owner-a",
        idempotency_key="runtime-ops:tenant-a:run-restart:pause-1",
        governance_context={"approval_id": "approval-a", "evidence_refs": ["ev-a"]},
    )
    assert replay == result
    assert store.last_status("workflow-restart") == status_after_first_effect

    with pytest.raises(WorkflowRuntimeGatewayError) as conflict_info:
        gateway.send(
            tenant_id="tenant-a",
            command_type="resume_run",
            task_id="run-restart",
            run_id="run-restart",
            requested_by="owner-a",
            idempotency_key="runtime-ops:tenant-a:run-restart:pause-1",
            governance_context={"approval_id": "approval-a", "evidence_refs": ["ev-a"]},
        )
    assert conflict_info.value.reason_code == "runtime_command_idempotency_conflict"
    assert conflict_info.value.http_status == 409
    assert store.last_status("workflow-restart") == status_after_first_effect

    with pytest.raises(WorkflowRuntimeGatewayError) as governance_conflict_info:
        gateway.send(
            tenant_id="tenant-a",
            command_type="pause_run",
            task_id="run-restart",
            run_id="run-restart",
            requested_by="owner-a",
            idempotency_key="runtime-ops:tenant-a:run-restart:pause-1",
            governance_context={"approval_id": "approval-b", "evidence_refs": ["ev-b"]},
        )
    assert governance_conflict_info.value.reason_code == "runtime_command_idempotency_conflict"
    assert governance_conflict_info.value.http_status == 409
    assert store.last_status("workflow-restart") == status_after_first_effect

    with pytest.raises(WorkflowRuntimeGatewayError, match="owner_required"):
        gateway.send(
            tenant_id="tenant-a",
            command_type="resume_run",
            task_id="run-restart",
            run_id="run-restart",
            requested_by="operator-b",
            idempotency_key="runtime-ops:tenant-a:run-restart:resume-1",
            governance_context={"approval_id": "approval-b"},
        )
