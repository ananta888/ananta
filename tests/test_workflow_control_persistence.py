from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib import import_module
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import Flask
from sqlalchemy import event, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from agent.auth import generate_token
from agent.config import settings
from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowControlDispatchIntentDB,
)
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

    ownership = WorkflowRouteAuthorizationService(WorkflowControlBindingOwnerResolver(restarted))
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


@pytest.mark.parametrize("runtime_id", ["local", "langgraph"])
def test_sql_sync_runtime_status_rejects_revision_regression_and_same_revision_mutation(
    control_engine,
    runtime_id: str,
) -> None:
    store = SQLAlchemyWorkflowControlBindingStore(control_engine)
    store.put(replace(_binding(), runtime_id=runtime_id))
    store.record_status(
        "workflow-restart",
        {"status": "running", "revision": 2, "checkpoint_ref": "checkpoint-2"},
    )

    with pytest.raises(
        WorkflowControlBindingPersistenceError,
        match="runtime_revision_regressed",
    ):
        store.record_status(
            "workflow-restart",
            {"status": "running", "revision": 1, "checkpoint_ref": "checkpoint-1"},
        )
    with pytest.raises(
        WorkflowControlBindingPersistenceError,
        match="runtime_revision_conflict",
    ):
        store.record_status(
            "workflow-restart",
            {"status": "paused", "revision": 2, "checkpoint_ref": "checkpoint-2"},
        )


def _runtime_status(
    *,
    status: str,
    revision: int,
    checkpoint_ref: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "revision": revision,
        "checkpoint_ref": checkpoint_ref,
        "source_observation": {
            "schema": "ananta.temporal-workflow-status.v1",
            "status": status,
            "revision": revision,
        },
    }


def test_sql_command_observation_pending_is_restart_safe_ready_and_fenced(
    control_engine,
) -> None:
    now = [100.0]
    store = SQLAlchemyWorkflowControlBindingStore(
        control_engine,
        clock=lambda: now[0],
    )
    store.put(_binding())
    store.record_status(
        "workflow-restart",
        _runtime_status(
            status="running",
            revision=1,
            checkpoint_ref="checkpoint-1",
        ),
    )
    store.claim_command(
        "workflow-restart",
        expected_revision=1,
        checkpoint_id="checkpoint-1",
        command_id="command-pending",
    )
    store.mark_command_observation_pending(
        "workflow-restart",
        command_id="command-pending",
        minimum_revision=2,
        reconciliation_ready=False,
    )

    restarted = SQLAlchemyWorkflowControlBindingStore(
        control_engine,
        clock=lambda: now[0],
    )
    assert restarted.list_reconcilable(runtime_id="local") == ()
    with pytest.raises(WorkflowControlBindingPersistenceError, match="cas_conflict"):
        restarted.claim_command(
            "workflow-restart",
            expected_revision=1,
            checkpoint_id="checkpoint-1",
            command_id="command-concurrent",
        )

    restarted.mark_command_observation_pending(
        "workflow-restart",
        command_id="command-pending",
        minimum_revision=3,
        expected_status="paused",
        reconciliation_ready=True,
    )
    restarted.release_command(
        "workflow-restart",
        command_id="command-pending",
    )
    assert [value.workflow_id for value in restarted.list_reconcilable(runtime_id="local")] == ["workflow-restart"]

    claimed = restarted.claim_reconcilable(
        runtime_id="local",
        owner_id="scheduler-a",
        lease_seconds=30.0,
    )
    assert [value.workflow_id for value in claimed] == ["workflow-restart"]
    for stale in (
        _runtime_status(
            status="paused",
            revision=2,
            checkpoint_ref="checkpoint-2",
        ),
        _runtime_status(
            status="running",
            revision=3,
            checkpoint_ref="checkpoint-3",
        ),
    ):
        with pytest.raises(
            WorkflowControlBindingPersistenceError,
            match="observation_fence_conflict",
        ):
            restarted.finish_reconciliation(
                "workflow-restart",
                owner_id="scheduler-a",
                expected_revision=1,
                expected_checkpoint_ref="checkpoint-1",
                status=stale,
            )

    restarted.finish_reconciliation(
        "workflow-restart",
        owner_id="scheduler-a",
        expected_revision=1,
        expected_checkpoint_ref="checkpoint-1",
        status=_runtime_status(
            status="paused",
            revision=3,
            checkpoint_ref="checkpoint-3",
        ),
    )
    assert restarted.last_status("workflow-restart")["revision"] == 3
    with Session(control_engine) as session:
        row = session.get(WorkflowControlBindingDB, "workflow-restart")
        assert row is not None
        assert row.command_observation_pending is False
        assert row.command_claim == ""
    restarted.claim_command(
        "workflow-restart",
        expected_revision=3,
        checkpoint_id="checkpoint-3",
        command_id="command-after-recovery",
    )


def test_sql_finish_reconciliation_revision_cas_retains_strengthened_pending(
    control_engine,
) -> None:
    store = SQLAlchemyWorkflowControlBindingStore(control_engine, clock=lambda: 100.0)
    store.put(_binding())
    store.record_status(
        "workflow-restart",
        _runtime_status(
            status="running",
            revision=1,
            checkpoint_ref="checkpoint-1",
        ),
    )
    store.claim_command(
        "workflow-restart",
        expected_revision=1,
        checkpoint_id="checkpoint-1",
        command_id="command-race",
    )
    store.mark_command_observation_pending(
        "workflow-restart",
        command_id="command-race",
        minimum_revision=2,
        expected_status="paused",
        reconciliation_ready=True,
    )
    assert store.claim_reconcilable(
        runtime_id="local",
        owner_id="scheduler-race",
        lease_seconds=30.0,
    )

    injected = False

    def strengthen_before_finish(connection, _cursor, statement, _parameters, _context, _many):
        nonlocal injected
        if injected or not statement.startswith("UPDATE workflow_control_bindings SET last_status="):
            return
        injected = True
        connection.exec_driver_sql(
            "UPDATE workflow_control_bindings "
            "SET command_observation_min_revision = 3, "
            "command_observation_expected_status = 'failed', revision = revision + 1 "
            "WHERE id = 'workflow-restart'"
        )
        # Commit the simulated concurrent writer before the stale UPDATE runs;
        # the store's subsequent rollback must not erase the strengthened row.
        connection.connection.commit()

    event.listen(control_engine, "before_cursor_execute", strengthen_before_finish)
    try:
        with pytest.raises(
            WorkflowControlBindingPersistenceError,
            match="reconciliation_cas_conflict",
        ):
            store.finish_reconciliation(
                "workflow-restart",
                owner_id="scheduler-race",
                expected_revision=1,
                expected_checkpoint_ref="checkpoint-1",
                status=_runtime_status(
                    status="paused",
                    revision=2,
                    checkpoint_ref="checkpoint-2",
                ),
            )
    finally:
        event.remove(control_engine, "before_cursor_execute", strengthen_before_finish)

    with Session(control_engine) as session:
        row = session.get(WorkflowControlBindingDB, "workflow-restart")
        assert row is not None
        assert row.command_observation_pending is True
        assert row.command_observation_min_revision == 3
        assert row.command_observation_expected_status == "failed"
        assert row.command_claim == "command-race"


def test_sql_reconciler_claim_rechecks_command_eligibility_in_update(control_engine) -> None:
    store = SQLAlchemyWorkflowControlBindingStore(control_engine, clock=lambda: 100.0)
    store.put(_binding())
    store.record_status(
        "workflow-restart",
        _runtime_status(
            status="running",
            revision=1,
            checkpoint_ref="checkpoint-1",
        ),
    )
    injected = False

    def claim_command_before_scheduler(connection, _cursor, statement, _parameters, _context, _many):
        nonlocal injected
        if injected or "SET scheduler_owner=" not in statement:
            return
        injected = True
        # Keep the row revision unchanged to prove the eligibility predicate is
        # independently reasserted by the scheduler UPDATE.
        connection.exec_driver_sql(
            "UPDATE workflow_control_bindings "
            "SET command_claim = 'command-concurrent', command_claim_expires_at = 400 "
            "WHERE id = 'workflow-restart'"
        )

    event.listen(control_engine, "before_cursor_execute", claim_command_before_scheduler)
    try:
        assert (
            store.claim_reconcilable(
                runtime_id="local",
                owner_id="scheduler-race",
                lease_seconds=30.0,
            )
            == ()
        )
    finally:
        event.remove(control_engine, "before_cursor_execute", claim_command_before_scheduler)

    with Session(control_engine) as session:
        row = session.get(WorkflowControlBindingDB, "workflow-restart")
        assert row is not None
        assert row.command_claim == "command-concurrent"
        assert row.scheduler_owner == ""


def test_command_observation_migration_upgrade_defaults_index_and_downgrade(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'observation-migration.db'}")
    metadata = sa.MetaData()
    sa.Table(
        "workflow_control_bindings",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
    )
    metadata.create_all(engine)
    migration = import_module("migrations.versions.c7e9a1b3d5f7_add_workflow_command_observation_pending")
    original_op = migration.op
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.execute(sa.text("INSERT INTO workflow_control_bindings (id) VALUES ('binding-a')"))
            stored = connection.execute(
                sa.text(
                    "SELECT public_status, command_observation_pending, "
                    "command_observation_min_revision, "
                    "command_observation_expected_status, dispatch_intent_id, "
                    "command_receipt_id "
                    "FROM workflow_control_bindings WHERE id = 'binding-a'"
                )
            ).one()
            assert tuple(stored) == ("{}", 0, 0, "", "", "")

        inspected = inspect(engine)
        columns = {value["name"]: value for value in inspected.get_columns("workflow_control_bindings")}
        assert isinstance(columns["public_status"]["type"], sa.JSON)
        assert isinstance(columns["command_observation_pending"]["type"], sa.Boolean)
        assert isinstance(columns["command_observation_min_revision"]["type"], sa.Integer)
        assert isinstance(columns["command_observation_expected_status"]["type"], sa.String)
        assert isinstance(columns["dispatch_intent_id"]["type"], sa.String)
        assert isinstance(columns["command_receipt_id"]["type"], sa.String)
        assert all(
            columns[name]["nullable"] is False
            for name in (
                "command_observation_pending",
                "public_status",
                "command_observation_min_revision",
                "command_observation_expected_status",
                "dispatch_intent_id",
                "command_receipt_id",
            )
        )
        assert "ix_workflow_control_bindings_command_observation_pending" in {
            value["name"] for value in inspected.get_indexes("workflow_control_bindings")
        }
        model_columns = WorkflowControlBindingDB.__table__.columns
        assert isinstance(model_columns.public_status.type, sa.JSON)
        assert isinstance(model_columns.command_observation_pending.type, sa.Boolean)
        assert isinstance(model_columns.command_observation_min_revision.type, sa.Integer)
        assert model_columns.command_observation_expected_status.type.python_type is str
        assert model_columns.command_observation_expected_status.type.length == 64
        assert model_columns.dispatch_intent_id.type.length == 256
        assert model_columns.command_receipt_id.type.length == 256
        assert "workflow_control_dispatch_intents" in inspected.get_table_names()
        intent_columns = {value["name"]: value for value in inspected.get_columns("workflow_control_dispatch_intents")}
        assert set(intent_columns) == {
            "id",
            "kind",
            "tenant_id",
            "workflow_id",
            "run_id",
            "payload",
            "state",
            "dispatch_from_state",
            "acknowledgement_revision",
            "acknowledgement_status",
            "attempt_count",
            "available_at",
            "lease_owner",
            "lease_expires_at",
            "last_error",
            "revision",
            "created_at",
            "updated_at",
        }
        assert intent_columns["id"]["type"].length == 256
        assert intent_columns["kind"]["type"].length == 32
        assert intent_columns["acknowledgement_status"]["type"].length == 64
        assert intent_columns["lease_owner"]["type"].length == 256
        model_intent = WorkflowControlDispatchIntentDB.__table__.columns
        assert model_intent.id.type.length == intent_columns["id"]["type"].length
        assert model_intent.kind.type.length == intent_columns["kind"]["type"].length
        assert (
            model_intent.acknowledgement_status.type.length == intent_columns["acknowledgement_status"]["type"].length
        )
        assert "workflow_control_command_receipts" in inspected.get_table_names()
        receipt_columns = {value["name"]: value for value in inspected.get_columns("workflow_control_command_receipts")}
        assert set(receipt_columns) == {
            "id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "actor_id",
            "command_type",
            "request_payload",
            "expected_revision",
            "checkpoint_ref",
            "state",
            "result_status",
            "rejection_reason",
            "dispatch_owner",
            "dispatch_lease_expires_at",
            "revision",
            "created_at",
            "updated_at",
        }
        model_receipt = WorkflowControlCommandReceiptDB.__table__.columns
        for name, length in (
            ("id", 256),
            ("tenant_id", 256),
            ("workflow_id", 256),
            ("run_id", 256),
            ("actor_id", 256),
            ("command_type", 64),
            ("checkpoint_ref", 512),
            ("state", 32),
            ("rejection_reason", 64),
            ("dispatch_owner", 256),
        ):
            assert receipt_columns[name]["type"].length == length
            assert model_receipt[name].type.length == length
        assert isinstance(receipt_columns["dispatch_lease_expires_at"]["type"], sa.Float)
        assert isinstance(model_receipt.dispatch_lease_expires_at.type, sa.Float)

        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.downgrade()
        remaining = {value["name"] for value in inspect(engine).get_columns("workflow_control_bindings")}
        assert remaining == {"id"}
        assert "workflow_control_dispatch_intents" not in inspect(engine).get_table_names()
        assert "workflow_control_command_receipts" not in inspect(engine).get_table_names()
    finally:
        migration.op = original_op


def test_local_control_and_operations_projection_resume_after_hub_restart(
    control_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    store = SQLAlchemyWorkflowControlBindingStore(control_engine)
    read_models = WorkflowRuntimeReadModelService(SQLAlchemyWorkflowRuntimeReadModelRepository(control_engine))
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
    assert (
        first.signal_workflow(
            "workflow-restart",
            WorkflowSignal(name="pause"),
        )["status"]
        == "paused"
    )

    # New backend, composition, authorization cache and repository instances
    # model a Hub process restart while preserving only SQL state and the key.
    restarted_store = SQLAlchemyWorkflowControlBindingStore(control_engine)
    restarted_reads = WorkflowRuntimeReadModelService(SQLAlchemyWorkflowRuntimeReadModelRepository(control_engine))
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
    assert (
        restarted.signal_workflow(
            "workflow-restart",
            WorkflowSignal(name="resume"),
        )["status"]
        == "running"
    )

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
    assert (
        restarted_reads.get_record(
            tenant_id="tenant-b",
            run_id="run-restart",
        )
        is None
    )

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
            WorkflowRuntimeReadModelService(SQLAlchemyWorkflowRuntimeReadModelRepository(control_engine))
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
