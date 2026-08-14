from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.workflow_runtime import (
    WorkflowCommandNonceDB,
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowControlDispatchIntentDB,
)
from agent.services.workflow_backend import (
    WORKFLOW_STATUS_SCHEMA,
    WorkflowRequest,
    WorkflowStepRequest,
)
from agent.services.workflow_backend_durable_run_adapter import (
    DURABLE_RUN_SIGNAL_SCHEMA,
    DURABLE_RUN_START_SCHEMA,
    WorkflowBackendDurableRunAdapter,
)
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_command_receipt_persistence import (
    InMemoryWorkflowControlCommandReceiptStore,
    SQLAlchemyWorkflowControlCommandReceiptStore,
)
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandReceiptError,
    WorkflowControlCommandRejectedError,
)
from agent.services.workflow_control_command_verification import (
    HubSignedWorkflowCommandVerifier,
)
from agent.services.workflow_control_dispatch_intents import (
    DISPATCH_STATE_COMPLETED,
    DISPATCH_STATE_OBSERVATION_PENDING,
    WorkflowControlDispatchIntentError,
)
from agent.services.workflow_control_dispatch_persistence import (
    InMemoryWorkflowControlDispatchIntentStore,
    SQLAlchemyWorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_dispatch_service import (
    COMMAND_OBSERVATION_PENDING,
    START_OBSERVATION_PENDING,
    WorkflowControlDispatchService,
)
from agent.services.workflow_control_persistence import (
    SQLAlchemyWorkflowCommandReplayNonceStore,
    SQLAlchemyWorkflowControlBindingStore,
)
from agent.services.workflow_runtime.commands import (
    SignedWorkflowCommand,
    WorkflowCommandIssuer,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.security import HmacKeyRing, InMemoryReplayNonceStore
from agent.services.workflow_runtime_status_projection import (
    authoritative_runtime_status,
)
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA


class _IdempotentTemporalBackend:
    backend_id = "temporal"

    def __init__(self, request: WorkflowRequest) -> None:
        self.request = request
        self.status = "running"
        self.revision = 0
        self.started = False
        self.start_calls = 0
        self.start_mutations = 0
        self.update_calls = 0
        self.update_ids: list[str] = []
        self.update_commands: list[dict[str, Any]] = []
        self._update_results: dict[str, dict[str, Any]] = {}
        self.raise_after_start_once = False
        self.raise_after_update_once = False
        self.fail_describe_count = 0
        self.malformed_ack = False
        self.projection_bind_failures = 0
        self.reject_update = False

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        self.start_calls += 1
        if not self.started:
            self.started = True
            self.start_mutations += 1
        if self.raise_after_start_once:
            self.raise_after_start_once = False
            raise RuntimeError("temporal_start_ack_lost")
        if self.projection_bind_failures:
            self.projection_bind_failures -= 1
            return {
                "schema": WORKFLOW_STATUS_SCHEMA,
                "backend": "temporal",
                "workflow_id": request.workflow_id,
                "status": "degraded",
                "reason": "temporal_projection_bind_failed:RuntimeError",
                "events": [],
            }
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "temporal",
            "workflow_id": request.workflow_id,
            "status": "running",
            "events": [],
        }

    def update_workflow(
        self,
        workflow_id: str,
        command: dict[str, Any],
        *,
        update_id: str = "",
    ) -> dict[str, Any]:
        assert workflow_id == self.request.workflow_id
        self.update_calls += 1
        self.update_ids.append(update_id)
        self.update_commands.append(dict(command))
        result = self._update_results.get(update_id)
        if result is None:
            if self.reject_update:
                result = {
                    "schema": "ananta.temporal-workflow-command-result.v2",
                    "command_id": command["command_id"],
                    "accepted": False,
                    "revision": self.revision,
                    "status": self.status,
                    "reason_code": "workflow_not_pausable",
                }
                self._update_results[update_id] = dict(result)
                return dict(result)
            self.revision += 1
            self.status = {
                "pause": "paused",
                "resume": "running",
                "cancel": "cancelled",
                "retry": "running",
            }[str(command["command_type"])]
            result = {
                "schema": "ananta.temporal-workflow-command-result.v2",
                "command_id": command["command_id"],
                "accepted": True,
                "revision": self.revision,
                "status": self.status,
                "reason_code": "",
            }
            if self.malformed_ack:
                result["status"] = "not-a-runtime-status"
            self._update_results[update_id] = dict(result)
        if self.raise_after_update_once:
            self.raise_after_update_once = False
            raise RuntimeError("temporal_update_ack_lost")
        return dict(result)

    def query_workflow(self, workflow_id: str, query_name: str) -> dict[str, Any]:
        assert workflow_id == self.request.workflow_id
        assert query_name == "status"
        if self.fail_describe_count:
            self.fail_describe_count -= 1
            raise RuntimeError("temporal_describe_unavailable")
        if not self.started and not self._update_results:
            raise RuntimeError("temporal_workflow_not_found")
        terminal = self.status in {"completed", "failed", "cancelled"}
        step_id = self.request.steps[0].step_id
        return {
            "schema": TEMPORAL_STATUS_SCHEMA,
            "workflow_id": workflow_id,
            "run_id": str(self.request.metadata["run_id"]),
            "status": self.status,
            "revision": self.revision,
            "current_step_id": "" if terminal else step_id,
            "completed_step_ids": [step_id] if self.status == "completed" else [],
            "retry_budget_remaining": 1,
            "checkpoint_ref": f"temporal:{workflow_id}:{self.revision}",
            "open_gates": [],
            "reason_code": "",
            "plan_hash": str(self.request.metadata["plan_hash"]),
            "plan_revision": 1,
            "active_step_ids": [] if terminal else [step_id],
            "failed_step_ids": [step_id] if self.status == "failed" else [],
        }

    def list_workflow_events(self, _workflow_id: str) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def intent_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _request(workflow_id: str = "workflow-intent") -> WorkflowRequest:
    return WorkflowRequest(
        workflow_id=workflow_id,
        requested_by="owner-a",
        metadata={
            "tenant_id": "tenant-a",
            "run_id": f"run:{workflow_id}",
            "plan_hash": "f" * 64,
            "policy_version": "policy-v1",
        },
        policy_scope={"policy_version": "policy-v1"},
        steps=(
            WorkflowStepRequest(
                step_id="step-a",
                task_kind="coding",
                policy_scope={"policy_version": "policy-v1"},
            ),
        ),
    )


def _binding(request: WorkflowRequest) -> WorkflowControlRunBinding:
    return WorkflowControlRunBinding(
        tenant_id="tenant-a",
        subject_id="owner-a",
        workflow_id=request.workflow_id,
        run_id=str(request.metadata["run_id"]),
        runtime_id="temporal",
        plan_hash=str(request.metadata["plan_hash"]),
        policy_version="policy-v1",
        checkpoint_id=f"temporal:{request.workflow_id}:0",
        request=request,
    )


def _temporal_status(binding: WorkflowControlRunBinding) -> dict[str, Any]:
    step_id = binding.request.steps[0].step_id
    return authoritative_runtime_status(
        {
            "schema": TEMPORAL_STATUS_SCHEMA,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
            "status": "running",
            "revision": 0,
            "current_step_id": step_id,
            "completed_step_ids": [],
            "retry_budget_remaining": 1,
            "checkpoint_ref": binding.checkpoint_id,
            "open_gates": [],
            "reason_code": "",
            "plan_hash": binding.plan_hash,
            "plan_revision": 1,
            "active_step_ids": [step_id],
            "failed_step_ids": [],
        },
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=1.0,
    )


def _start_pending(binding: WorkflowControlRunBinding) -> dict[str, Any]:
    return authoritative_runtime_status(
        {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "temporal",
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
            "plan_hash": binding.plan_hash,
            "status": "pending",
        },
        binding=binding,
        previous=None,
        runtime_id="temporal",
        allow_initial_ack=True,
        observed_at=1.0,
    )


def _command(
    binding: WorkflowControlRunBinding,
    key_ring: HmacKeyRing,
    *,
    command_id: str = "command-a",
    command_type: str = "pause",
    expected_revision: int = 0,
    checkpoint_id: str = "",
):
    return WorkflowCommandIssuer(key_ring, clock=time.time).issue(
        command_id=command_id,
        command_type=command_type,
        tenant_id=binding.tenant_id,
        workflow_id=binding.workflow_id,
        run_id=binding.run_id,
        step_id=binding.request.steps[0].step_id,
        checkpoint_id=checkpoint_id or binding.checkpoint_id,
        expected_revision=expected_revision,
        plan_hash=binding.plan_hash,
        policy_version=binding.policy_version,
        actor_id=binding.subject_id,
        actor_roles=("operator",),
        payload={},
    )


def _command_with_nonce(
    binding: WorkflowControlRunBinding,
    key_ring: HmacKeyRing,
    *,
    command_id: str,
    nonce: str,
    now: float,
) -> SignedWorkflowCommand:
    return SignedWorkflowCommand.issue(
        key_ring=key_ring,
        command_id=command_id,
        command_type="pause",
        tenant_id=binding.tenant_id,
        workflow_id=binding.workflow_id,
        run_id=binding.run_id,
        step_id=binding.request.steps[0].step_id,
        checkpoint_id=binding.checkpoint_id,
        expected_revision=0,
        plan_hash=binding.plan_hash,
        policy_version=binding.policy_version,
        actor_id=binding.subject_id,
        actor_roles=("operator",),
        payload={},
        nonce=nonce,
        now=now,
        ttl_seconds=300,
    )


def _dispatcher(
    *,
    engine,
    clock: list[float],
    backend: _IdempotentTemporalBackend,
    key_ring: HmacKeyRing,
    owner_id: str,
    intents: SQLAlchemyWorkflowControlDispatchIntentStore | None = None,
) -> WorkflowControlDispatchService:
    bindings = SQLAlchemyWorkflowControlBindingStore(engine, clock=lambda: clock[0])
    verifier = HubSignedWorkflowCommandVerifier(
        WorkflowCommandVerifier(
            key_ring,
            SQLAlchemyWorkflowCommandReplayNonceStore(
                engine,
                clock=lambda: clock[0],
            ),
        )
    )
    return WorkflowControlDispatchService(
        runtime_id="temporal",
        bindings=bindings,
        intents=intents
        or SQLAlchemyWorkflowControlDispatchIntentStore(
            engine,
            clock=lambda: clock[0],
        ),
        durable_runs=WorkflowBackendDurableRunAdapter(
            backend,
            commands=verifier,
            command_issuer=WorkflowCommandIssuer(key_ring),
        ),
        commands=verifier,
        project=lambda _binding, _status: None,
        clock=lambda: clock[0],
        owner_id=owner_id,
        lease_seconds=10.0,
        retry_seconds=0.1,
    )


def _prepared_command_run(engine, clock: list[float]):
    request = _request()
    binding = _binding(request)
    bindings = SQLAlchemyWorkflowControlBindingStore(engine, clock=lambda: clock[0])
    bindings.put(binding)
    bindings.record_status(binding.workflow_id, _temporal_status(binding))
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    backend = _IdempotentTemporalBackend(request)
    return binding, bindings, key_ring, backend


def _control_rows_snapshot(engine, workflow_id: str, intent_id: str) -> tuple[dict[str, Any], Any]:
    with Session(engine) as session:
        binding = session.get(WorkflowControlBindingDB, workflow_id)
        intent = session.get(WorkflowControlDispatchIntentDB, intent_id)
        assert binding is not None
        binding_values = {
            column.name: getattr(binding, column.name) for column in binding.__table__.columns
        }
        intent_values = (
            {column.name: getattr(intent, column.name) for column in intent.__table__.columns}
            if intent is not None
            else None
        )
        return binding_values, intent_values


@pytest.mark.parametrize("dispatch_kind", ["command", "start"])
def test_sql_active_transition_fences_legacy_dispatch_staging(
    intent_engine,
    dispatch_kind: str,
) -> None:
    clock = [100.0]
    binding, _bindings, key_ring, _backend = _prepared_command_run(intent_engine, clock)
    store = SQLAlchemyWorkflowControlDispatchIntentStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    command = _command(binding, key_ring)
    intent_id = command.command_id if dispatch_kind == "command" else f"start:{binding.workflow_id}"
    with Session(intent_engine) as session, session.begin():
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        assert row is not None
        row.active_transition_id = "transition-active"
    before = _control_rows_snapshot(intent_engine, binding.workflow_id, intent_id)

    with pytest.raises(
        WorkflowControlDispatchIntentError,
        match="workflow_control_dispatch_stage_cas_conflict",
    ):
        if dispatch_kind == "command":
            store.stage_command(binding=binding, command=command)
        else:
            store.stage_start(
                binding=binding,
                start_command={
                    "schema": DURABLE_RUN_START_SCHEMA,
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "run_id": binding.run_id,
                    "workflow_request": binding.request.to_dict(),
                },
                request_id="start-request-a",
                pending_status=_start_pending(binding),
            )

    assert _control_rows_snapshot(intent_engine, binding.workflow_id, intent_id) == before
    with Session(intent_engine) as session:
        assert session.exec(select(WorkflowControlDispatchIntentDB)).all() == []


@pytest.mark.parametrize(
    ("operation_name", "expected_error"),
    [
        ("claim", ""),
        ("claim_due", ""),
        ("acknowledge", "workflow_control_dispatch_lease_conflict"),
        ("release", "workflow_control_dispatch_lease_conflict"),
        ("complete", "workflow_control_dispatch_completion_conflict"),
        ("reject", "workflow_control_dispatch_completion_conflict"),
    ],
)
def test_sql_active_transition_fences_every_legacy_dispatch_mutation_family(
    intent_engine,
    operation_name: str,
    expected_error: str,
) -> None:
    clock = [100.0]
    binding, _bindings, key_ring, _backend = _prepared_command_run(intent_engine, clock)
    store = SQLAlchemyWorkflowControlDispatchIntentStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    command = _command(binding, key_ring)
    store.stage_command(binding=binding, command=command)
    if operation_name not in {"claim", "claim_due"}:
        claimed = store.claim(
            command.command_id,
            owner_id="legacy-owner",
            lease_seconds=30.0,
        )
        assert claimed is not None
    with Session(intent_engine) as session, session.begin():
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        assert row is not None
        row.active_transition_id = "transition-active"

    operations = {
        "claim": lambda: store.claim(
            command.command_id,
            owner_id="legacy-owner",
            lease_seconds=30.0,
        ),
        "claim_due": lambda: store.claim_due(
            owner_id="legacy-owner",
            lease_seconds=30.0,
            limit=10,
        ),
        "acknowledge": lambda: store.acknowledge(
            command.command_id,
            owner_id="legacy-owner",
            acknowledgement_revision=1,
            acknowledgement_status="paused",
        ),
        "release": lambda: store.release(
            command.command_id,
            owner_id="legacy-owner",
            reason_code="retryable_failure",
            retry_at=101.0,
        ),
        "complete": lambda: store.complete(
            command.command_id,
            owner_id="legacy-owner",
            status=_temporal_status(binding),
        ),
        "reject": lambda: store.reject(
            command.command_id,
            owner_id="legacy-owner",
            reason_code="command_rejected",
        ),
    }
    before = _control_rows_snapshot(intent_engine, binding.workflow_id, command.command_id)

    if expected_error:
        with pytest.raises(WorkflowControlDispatchIntentError, match=expected_error):
            operations[operation_name]()
    else:
        result = operations[operation_name]()
        assert result == (() if operation_name == "claim_due" else None)

    assert _control_rows_snapshot(intent_engine, binding.workflow_id, command.command_id) == before


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_legacy_receipt_heartbeat_atomically_upgrades_generation_and_fences_gen_zero(
    intent_engine,
    kind: str,
) -> None:
    clock = [100.0]
    request = _request(f"workflow-legacy-heartbeat-{kind}")
    binding = _binding(request)
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    command = _command(binding, key_ring, command_id=f"legacy-heartbeat-{kind}")
    if kind == "memory":
        bindings = InMemoryWorkflowControlBindingStore()
        bindings.put(binding)
        bindings.record_status(binding.workflow_id, _temporal_status(binding))
        receipts: Any = InMemoryWorkflowControlCommandReceiptStore(
            bindings,
            clock=lambda: clock[0],
        )
    else:
        bindings = SQLAlchemyWorkflowControlBindingStore(
            intent_engine,
            clock=lambda: clock[0],
        )
        bindings.put(binding)
        bindings.record_status(binding.workflow_id, _temporal_status(binding))
        receipts = SQLAlchemyWorkflowControlCommandReceiptStore(
            intent_engine,
            clock=lambda: clock[0],
        )
    staged = receipts.stage(
        binding=binding,
        command_id=command.command_id,
        actor_id=binding.subject_id,
        command_type=command.command_type,
        request_payload={
            "actor_roles": ["operator"],
            "admitted_command": command.to_dict(),
            "payload": {},
            "step_id": command.step_id,
        },
        expected_revision=0,
        checkpoint_ref=binding.checkpoint_id,
    )
    if kind == "memory":
        receipts._rows[staged.command_id] = replace(  # noqa: SLF001 - migration fixture
            staged,
            state="dispatching",
            dispatch_owner="legacy-owner",
            dispatch_lease_expires_at=130.0,
        )
    else:
        with Session(intent_engine) as session, session.begin():
            row = session.get(WorkflowControlCommandReceiptDB, staged.command_id)
            assert row is not None
            row.state = "dispatching"
            row.dispatch_owner = "legacy-owner"
            row.dispatch_lease_expires_at = 130.0
            row.dispatch_generation = 0
            row.last_heartbeat_at = 0.0

    heartbeat = receipts.heartbeat(
        staged.command_id,
        owner_id="legacy-owner",
        dispatch_generation=0,
        lease_seconds=30.0,
    )

    assert heartbeat.dispatch_generation == 1
    assert heartbeat.last_heartbeat_at == 100.0
    assert heartbeat.dispatch_lease_expires_at == 130.0
    assert receipts.get(staged.command_id) == heartbeat
    with pytest.raises(WorkflowControlCommandReceiptError, match="lease_conflict"):
        receipts.reject(
            staged.command_id,
            reason_code="stale_legacy_generation",
            owner_id="legacy-owner",
            dispatch_generation=0,
        )
    assert receipts.get(staged.command_id) == heartbeat


def test_command_intent_completes_from_real_v1_status_with_temporal_update_id(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, bindings, key_ring, backend = _prepared_command_run(intent_engine, clock)
    command = _command(binding, key_ring)

    status = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-a",
    ).stage_command(binding=binding, command=command)

    assert status["status"] == "paused"
    assert backend.update_ids == [command.command_id]
    renewed = SignedWorkflowCommand.from_mapping(backend.update_commands[0])
    assert renewed.command_id == command.command_id
    assert renewed.nonce != command.nonce
    assert renewed.signature != command.signature
    assert renewed.payload == command.payload
    assert bindings.last_status(binding.workflow_id)["source_observation"] == {
        "schema": TEMPORAL_STATUS_SCHEMA,
        "status": "paused",
        "revision": 1,
    }
    with Session(intent_engine) as session:
        intent = session.get(WorkflowControlDispatchIntentDB, command.command_id)
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        assert intent is not None and intent.state == DISPATCH_STATE_COMPLETED
        assert row is not None and row.dispatch_intent_id == ""
        assert row.command_claim == ""
        assert len(session.exec(select(WorkflowCommandNonceDB)).all()) == 1


def test_command_ack_loss_restarts_with_same_update_id_without_second_mutation(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, bindings, key_ring, backend = _prepared_command_run(intent_engine, clock)
    command = _command(binding, key_ring)
    backend.raise_after_update_once = True

    with pytest.raises(RuntimeError, match=COMMAND_OBSERVATION_PENDING):
        _dispatcher(
            engine=intent_engine,
            clock=clock,
            backend=backend,
            key_ring=key_ring,
            owner_id="hub-before-crash",
        ).stage_command(binding=binding, command=command)

    clock[0] += 1.0
    recovered = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-after-restart",
    ).drain()

    assert recovered["processed"] == 1
    assert backend.update_calls == 2
    assert backend.update_ids == [command.command_id, command.command_id]
    assert backend.revision == 1
    assert bindings.last_status(binding.workflow_id)["status"] == "paused"


def test_malformed_bound_ack_recovers_by_authoritative_describe(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, bindings, key_ring, backend = _prepared_command_run(intent_engine, clock)
    command = _command(binding, key_ring)
    backend.malformed_ack = True

    with pytest.raises(RuntimeError, match=COMMAND_OBSERVATION_PENDING):
        _dispatcher(
            engine=intent_engine,
            clock=clock,
            backend=backend,
            key_ring=key_ring,
            owner_id="hub-a",
        ).stage_command(binding=binding, command=command)
    intent = SQLAlchemyWorkflowControlDispatchIntentStore(intent_engine).get(command.command_id)
    assert intent is not None
    assert intent.state == DISPATCH_STATE_OBSERVATION_PENDING
    assert intent.acknowledgement_revision == 1
    assert intent.acknowledgement_status == ""

    clock[0] += 1.0
    recovered = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-b",
    ).drain()

    assert recovered["processed"] == 1
    assert backend.update_calls == 1
    assert backend.update_ids == [command.command_id]
    assert backend.revision == 1
    assert bindings.last_status(binding.workflow_id)["status"] == "paused"


def test_exact_rejected_command_ack_is_terminal_and_frees_followup_command(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, bindings, key_ring, backend = _prepared_command_run(intent_engine, clock)
    rejected = _command(binding, key_ring, command_id="command-rejected")
    backend.reject_update = True

    with pytest.raises(WorkflowControlCommandRejectedError):
        _dispatcher(
            engine=intent_engine,
            clock=clock,
            backend=backend,
            key_ring=key_ring,
            owner_id="hub-a",
        ).stage_command(binding=binding, command=rejected)

    assert backend.update_calls == 1
    assert bindings.last_status(binding.workflow_id)["revision"] == 0
    with Session(intent_engine) as session:
        intent = session.get(WorkflowControlDispatchIntentDB, rejected.command_id)
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        assert intent is not None and intent.state == "rejected"
        assert row is not None and row.dispatch_intent_id == ""
        assert row.command_claim == ""

    backend.reject_update = False
    accepted = _command(binding, key_ring, command_id="command-after-rejection")
    status = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-b",
    ).stage_command(binding=binding, command=accepted)

    assert status["status"] == "paused"
    assert backend.update_calls == 2


def test_stale_rejected_ack_materializes_newer_observation_before_freeing_intent(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, bindings, key_ring, backend = _prepared_command_run(intent_engine, clock)
    backend.revision = 1
    backend.status = "paused"
    backend.reject_update = True
    rejected = _command(binding, key_ring, command_id="command-stale")

    with pytest.raises(WorkflowControlCommandRejectedError):
        _dispatcher(
            engine=intent_engine,
            clock=clock,
            backend=backend,
            key_ring=key_ring,
            owner_id="hub-a",
        ).stage_command(binding=binding, command=rejected)

    observed = bindings.last_status(binding.workflow_id)
    assert observed is not None
    assert observed["revision"] == 1
    assert observed["status"] == "paused"
    with Session(intent_engine) as session:
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        assert row is not None
        assert row.dispatch_intent_id == ""
        assert row.command_claim == ""

    backend.reject_update = False
    resumed = _command(
        binding,
        key_ring,
        command_id="command-after-stale",
        command_type="resume",
        expected_revision=1,
        checkpoint_id=f"temporal:{binding.workflow_id}:1",
    )
    status = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-b",
    ).stage_command(binding=binding, command=resumed)

    assert status["revision"] == 2
    assert status["status"] == "running"


def test_persisted_command_is_reissued_with_fresh_signature_after_admission_ttl(
    intent_engine,
) -> None:
    request = _request("workflow-expired-admission")
    binding = _binding(request)
    key_ring = HmacKeyRing({"command-key": "x" * 32}, active_key_id="command-key")
    original = WorkflowCommandIssuer(
        key_ring,
        ttl_seconds=1.0,
        clock=lambda: 10.0,
    ).issue(
        command_id="command-expired-admission",
        command_type="pause",
        tenant_id=binding.tenant_id,
        workflow_id=binding.workflow_id,
        run_id=binding.run_id,
        step_id=binding.request.steps[0].step_id,
        checkpoint_id=binding.checkpoint_id,
        expected_revision=0,
        plan_hash=binding.plan_hash,
        policy_version=binding.policy_version,
        actor_id=binding.subject_id,
        actor_roles=("operator",),
        payload={},
    )
    verifier = HubSignedWorkflowCommandVerifier(
        WorkflowCommandVerifier(
            key_ring,
            SQLAlchemyWorkflowCommandReplayNonceStore(
                intent_engine,
                clock=lambda: 1_000.0,
            ),
        )
    )
    backend = _IdempotentTemporalBackend(request)
    adapter = WorkflowBackendDurableRunAdapter(
        backend,
        commands=verifier,
        command_issuer=WorkflowCommandIssuer(
            key_ring,
            ttl_seconds=60.0,
            clock=lambda: 1_000.0,
        ),
    )

    adapter.signal_persisted(
        tenant_id=binding.tenant_id,
        run_id=binding.workflow_id,
        command={
            "schema": DURABLE_RUN_SIGNAL_SCHEMA,
            "command": original.to_dict(),
        },
    )

    renewed = SignedWorkflowCommand.from_mapping(backend.update_commands[0])
    assert renewed.command_id == original.command_id
    assert renewed.payload == original.payload
    assert renewed.issued_at == 1_000.0
    assert renewed.expires_at == 1_060.0
    renewed.verify(
        key_ring=key_ring,
        tenant_id=binding.tenant_id,
        workflow_id=binding.workflow_id,
        run_id=binding.run_id,
        step_id=renewed.step_id,
        checkpoint_id=binding.checkpoint_id,
        expected_revision=0,
        plan_hash=binding.plan_hash,
        policy_version=binding.policy_version,
        now=1_001.0,
    )


def test_nonce_and_intent_stage_roll_back_together_and_exact_retry_succeeds(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, _bindings, key_ring, backend = _prepared_command_run(intent_engine, clock)
    command = _command(binding, key_ring)
    failures = [True]

    def fail_once(_stage: str) -> None:
        if failures and failures.pop():
            raise RuntimeError("injected_stage_failure")

    intents = SQLAlchemyWorkflowControlDispatchIntentStore(
        intent_engine,
        clock=lambda: clock[0],
        fault_injector=fail_once,
    )
    dispatcher = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-a",
        intents=intents,
    )
    with pytest.raises(RuntimeError, match="injected_stage_failure"):
        dispatcher.stage_command(binding=binding, command=command)
    with Session(intent_engine) as session:
        assert session.get(WorkflowControlDispatchIntentDB, command.command_id) is None
        assert session.exec(select(WorkflowCommandNonceDB)).all() == []
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        assert row is not None and row.dispatch_intent_id == ""

    status = dispatcher.stage_command(binding=binding, command=command)
    assert status["status"] == "paused"
    assert backend.update_calls == 1


def test_unexpired_lease_cannot_be_reclaimed_even_by_same_owner(intent_engine) -> None:
    clock = [100.0]
    binding, _bindings, key_ring, _backend = _prepared_command_run(intent_engine, clock)
    command = _command(binding, key_ring)
    verifier = HubSignedWorkflowCommandVerifier(
        WorkflowCommandVerifier(
            key_ring,
            SQLAlchemyWorkflowCommandReplayNonceStore(intent_engine),
        )
    )
    verifier.verify_for_staging(
        tenant_id=binding.tenant_id,
        run_id=binding.workflow_id,
        command={
            "schema": "ananta.durable_run_signal.v1",
            "command": command.to_dict(),
        },
    )
    intents = SQLAlchemyWorkflowControlDispatchIntentStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    intents.stage_command(binding=binding, command=command)

    assert intents.claim(command.command_id, owner_id="hub-a", lease_seconds=10) is not None
    assert intents.claim(command.command_id, owner_id="hub-a", lease_seconds=10) is None
    assert intents.claim(command.command_id, owner_id="hub-b", lease_seconds=10) is None
    clock[0] = 111.0
    assert intents.claim(command.command_id, owner_id="hub-b", lease_seconds=10) is not None


def test_start_ack_loss_keeps_pending_snapshot_and_restart_adopts_same_workflow(
    intent_engine,
) -> None:
    clock = [100.0]
    request = _request("workflow-start-intent")
    binding = _binding(request)
    bindings = SQLAlchemyWorkflowControlBindingStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    bindings.put(binding)
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    backend = _IdempotentTemporalBackend(request)
    backend.raise_after_start_once = True
    start_command = {
        "schema": DURABLE_RUN_START_SCHEMA,
        "tenant_id": binding.tenant_id,
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
        "workflow_request": request.to_dict(),
    }

    with pytest.raises(RuntimeError, match=START_OBSERVATION_PENDING):
        _dispatcher(
            engine=intent_engine,
            clock=clock,
            backend=backend,
            key_ring=key_ring,
            owner_id="hub-before-crash",
        ).stage_start(
            binding=binding,
            start_command=start_command,
            request_id="start-request-a",
            pending_status=_start_pending(binding),
        )
    pending = bindings.last_status(binding.workflow_id)
    assert pending is not None
    assert pending["status"] == "pending"
    assert pending["steps"] == [{"step_id": "step-a", "status": "pending"}]

    clock[0] += 1.0
    recovered = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-after-restart",
    ).drain()

    assert recovered["processed"] == 1
    assert backend.start_calls == 2
    assert backend.start_mutations == 1
    assert bindings.last_status(binding.workflow_id)["status"] == "running"


def test_start_projection_binding_failure_replays_start_before_describe_completion(
    intent_engine,
) -> None:
    clock = [100.0]
    request = _request("workflow-start-projection-binding")
    binding = _binding(request)
    bindings = SQLAlchemyWorkflowControlBindingStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    bindings.put(binding)
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    backend = _IdempotentTemporalBackend(request)
    backend.projection_bind_failures = 1
    start_command = {
        "schema": DURABLE_RUN_START_SCHEMA,
        "tenant_id": binding.tenant_id,
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
        "workflow_request": request.to_dict(),
    }

    with pytest.raises(RuntimeError, match=START_OBSERVATION_PENDING):
        _dispatcher(
            engine=intent_engine,
            clock=clock,
            backend=backend,
            key_ring=key_ring,
            owner_id="hub-before-restart",
        ).stage_start(
            binding=binding,
            start_command=start_command,
            request_id="projection-binding-request",
            pending_status=_start_pending(binding),
        )

    assert backend.started is True
    assert backend.start_mutations == 1
    assert bindings.last_status(binding.workflow_id)["status"] == "pending"

    clock[0] += 1.0
    recovered = _dispatcher(
        engine=intent_engine,
        clock=clock,
        backend=backend,
        key_ring=key_ring,
        owner_id="hub-after-restart",
    ).drain()

    assert recovered["processed"] == 1
    assert backend.start_calls == 2
    assert backend.start_mutations == 1
    assert bindings.last_status(binding.workflow_id)["status"] == "running"


def test_start_intent_requires_exact_client_request_id_on_adoption(intent_engine) -> None:
    clock = [100.0]
    request = _request("workflow-start-request-id")
    binding = _binding(request)
    bindings = SQLAlchemyWorkflowControlBindingStore(intent_engine, clock=lambda: clock[0])
    bindings.put(binding)
    intents = SQLAlchemyWorkflowControlDispatchIntentStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    start_command = {
        "schema": DURABLE_RUN_START_SCHEMA,
        "tenant_id": binding.tenant_id,
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
        "workflow_request": request.to_dict(),
    }

    first = intents.stage_start(
        binding=binding,
        start_command=start_command,
        request_id="client-start-a",
        pending_status=_start_pending(binding),
    )
    repeated = intents.stage_start(
        binding=binding,
        start_command=start_command,
        request_id="client-start-a",
        pending_status=_start_pending(binding),
    )

    assert repeated == first
    with pytest.raises(
        WorkflowControlDispatchIntentError,
        match="workflow_control_dispatch_stage_conflict",
    ):
        intents.stage_start(
            binding=binding,
            start_command=start_command,
            request_id="client-start-b",
            pending_status=_start_pending(binding),
        )


def test_sql_command_receipt_recovers_binding_commit_after_process_restart(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, bindings, key_ring, _backend = _prepared_command_run(
        intent_engine,
        clock,
    )
    receipts = SQLAlchemyWorkflowControlCommandReceiptStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    admitted = _command(
        binding,
        key_ring,
        command_id="native-command-a",
    )
    receipt = receipts.stage(
        binding=binding,
        command_id="native-command-a",
        actor_id=binding.subject_id,
        command_type="pause",
        request_payload={
            "actor_roles": ["operator"],
            "admitted_command": admitted.to_dict(),
            "payload": {},
            "step_id": "step-a",
        },
        expected_revision=0,
        checkpoint_ref=binding.checkpoint_id,
    )
    bindings.claim_command(
        binding.workflow_id,
        expected_revision=0,
        checkpoint_id=binding.checkpoint_id,
        command_id=receipt.command_id,
    )
    status = {
        **_temporal_status(binding),
        "status": "paused",
        "revision": 1,
        "checkpoint_ref": f"temporal:{binding.workflow_id}:1",
        "source_observation": {
            "schema": TEMPORAL_STATUS_SCHEMA,
            "status": "paused",
            "revision": 1,
        },
        "steps": [{"step_id": "step-a", "status": "paused"}],
    }
    bindings.finish_command(
        binding.workflow_id,
        command_id=receipt.command_id,
        status=status,
    )

    restarted = SQLAlchemyWorkflowControlCommandReceiptStore(intent_engine)
    claimed = restarted.claim(receipt.command_id, owner_id="receipt-test-owner")
    assert claimed is not None
    completed = restarted.complete(
        receipt.command_id,
        status=status,
        owner_id="receipt-test-owner",
        dispatch_generation=claimed.dispatch_generation,
    )

    assert completed.state == "completed"
    assert completed.result_status == status
    with Session(intent_engine) as session:
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        persisted = session.get(WorkflowControlCommandReceiptDB, receipt.command_id)
        assert row is not None and row.command_receipt_id == ""
        assert persisted is not None and persisted.state == "completed"


def test_sql_command_receipt_lease_blocks_parallel_and_same_owner_reclaim(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, _bindings, key_ring, _backend = _prepared_command_run(
        intent_engine,
        clock,
    )
    receipts = SQLAlchemyWorkflowControlCommandReceiptStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    admitted = _command(
        binding,
        key_ring,
        command_id="native-command-leased",
    )
    receipt = receipts.stage(
        binding=binding,
        command_id="native-command-leased",
        actor_id=binding.subject_id,
        command_type="pause",
        request_payload={
            "actor_roles": ["operator"],
            "admitted_command": admitted.to_dict(),
            "payload": {},
            "step_id": "step-a",
        },
        expected_revision=0,
        checkpoint_ref=binding.checkpoint_id,
    )

    claimed = receipts.claim(
        receipt.command_id,
        owner_id="receipt-owner-a",
        lease_seconds=30,
    )

    assert claimed is not None and claimed.state == "dispatching"
    assert receipts.claim(receipt.command_id, owner_id="receipt-owner-a") is None
    assert receipts.claim(receipt.command_id, owner_id="receipt-owner-b") is None
    assert receipts.list_pending() == ()
    clock[0] = 131.0
    assert receipts.list_pending()[0].command_id == receipt.command_id
    reclaimed = receipts.claim(receipt.command_id, owner_id="receipt-owner-b")
    assert reclaimed is not None and reclaimed.dispatch_owner == "receipt-owner-b"
    released = receipts.release(
        receipt.command_id,
        owner_id="receipt-owner-b",
        dispatch_generation=reclaimed.dispatch_generation,
    )
    assert released.state == "pending"


def test_sql_command_receipt_atomically_consumes_nonce_and_rolls_back_fault(
    intent_engine,
) -> None:
    clock = [100.0]
    binding, bindings, key_ring, _backend = _prepared_command_run(
        intent_engine,
        clock,
    )
    command = _command_with_nonce(
        binding,
        key_ring,
        command_id="receipt-command-after-fault",
        nonce="receipt-shared-nonce",
        now=clock[0],
    )
    request_payload = {
        "actor_roles": ["operator"],
        "admitted_command": command.to_dict(),
        "payload": {},
        "step_id": "step-a",
    }

    def fail_after_nonce(stage: str) -> None:
        if stage == "receipt_staged_before_binding_cas":
            raise RuntimeError("receipt_stage_fault")

    failing = SQLAlchemyWorkflowControlCommandReceiptStore(
        intent_engine,
        clock=lambda: clock[0],
        fault_injector=fail_after_nonce,
    )
    with pytest.raises(RuntimeError, match="receipt_stage_fault"):
        failing.stage(
            binding=binding,
            command_id=command.command_id,
            actor_id=binding.subject_id,
            command_type=command.command_type,
            request_payload=request_payload,
            expected_revision=0,
            checkpoint_ref=binding.checkpoint_id,
        )
    with Session(intent_engine) as session:
        persisted_binding = session.get(WorkflowControlBindingDB, binding.workflow_id)
        assert persisted_binding is not None and persisted_binding.command_receipt_id == ""
        assert session.get(WorkflowControlCommandReceiptDB, command.command_id) is None
        assert session.exec(select(WorkflowCommandNonceDB)).all() == []

    restarted = SQLAlchemyWorkflowControlCommandReceiptStore(
        intent_engine,
        clock=lambda: clock[0],
    )
    staged = restarted.stage(
        binding=binding,
        command_id=command.command_id,
        actor_id=binding.subject_id,
        command_type=command.command_type,
        request_payload=request_payload,
        expected_revision=0,
        checkpoint_ref=binding.checkpoint_id,
    )
    assert (
        restarted.stage(
            binding=binding,
            command_id=command.command_id,
            actor_id=binding.subject_id,
            command_type=command.command_type,
            request_payload=request_payload,
            expected_revision=0,
            checkpoint_ref=binding.checkpoint_id,
        )
        == staged
    )

    second_request = _request("workflow-receipt-nonce-collision")
    second_binding = _binding(second_request)
    bindings.put(second_binding)
    bindings.record_status(second_binding.workflow_id, _temporal_status(second_binding))
    collision = _command_with_nonce(
        second_binding,
        key_ring,
        command_id="receipt-command-nonce-collision",
        nonce="receipt-shared-nonce",
        now=clock[0],
    )
    with pytest.raises(
        WorkflowControlCommandReceiptError,
        match="workflow_control_command_receipt_stage_conflict",
    ):
        restarted.stage(
            binding=second_binding,
            command_id=collision.command_id,
            actor_id=second_binding.subject_id,
            command_type=collision.command_type,
            request_payload={
                "actor_roles": ["operator"],
                "admitted_command": collision.to_dict(),
                "payload": {},
                "step_id": "step-a",
            },
            expected_revision=0,
            checkpoint_ref=second_binding.checkpoint_id,
        )
    assert bindings.get(second_binding.workflow_id) is not None
    with Session(intent_engine) as session:
        persisted_second = session.get(
            WorkflowControlBindingDB,
            second_binding.workflow_id,
        )
        assert persisted_second is not None and persisted_second.command_receipt_id == ""
        assert session.get(WorkflowControlCommandReceiptDB, collision.command_id) is None
        assert len(session.exec(select(WorkflowCommandNonceDB)).all()) == 1


def test_in_memory_command_receipt_consumes_shared_nonce_and_clears_failed_marker() -> None:
    key_ring = HmacKeyRing({"control": "x" * 32}, active_key_id="control")
    replay = InMemoryReplayNonceStore(clock=lambda: 100.0)
    bindings = InMemoryWorkflowControlBindingStore(clock=lambda: 100.0)
    first_binding = _binding(_request("workflow-receipt-memory-a"))
    second_binding = _binding(_request("workflow-receipt-memory-b"))
    bindings.put(first_binding)
    bindings.put(second_binding)
    receipts = InMemoryWorkflowControlCommandReceiptStore(
        bindings,
        clock=lambda: 100.0,
        replay_store=replay,
    )

    first = _command_with_nonce(
        first_binding,
        key_ring,
        command_id="receipt-memory-a",
        nonce="receipt-memory-shared",
        now=100.0,
    )
    collision = _command_with_nonce(
        second_binding,
        key_ring,
        command_id="receipt-memory-b",
        nonce="receipt-memory-shared",
        now=100.0,
    )
    replacement = _command_with_nonce(
        second_binding,
        key_ring,
        command_id="receipt-memory-c",
        nonce="receipt-memory-fresh",
        now=100.0,
    )

    def stage(binding: WorkflowControlRunBinding, command: SignedWorkflowCommand):
        return receipts.stage(
            binding=binding,
            command_id=command.command_id,
            actor_id=binding.subject_id,
            command_type=command.command_type,
            request_payload={
                "actor_roles": ["operator"],
                "admitted_command": command.to_dict(),
                "payload": {},
                "step_id": binding.request.steps[0].step_id,
            },
            expected_revision=0,
            checkpoint_ref=binding.checkpoint_id,
        )

    assert stage(first_binding, first).command_id == first.command_id
    with pytest.raises(
        WorkflowControlCommandReceiptError,
        match="workflow_control_command_receipt_replay_detected",
    ):
        stage(second_binding, collision)
    assert stage(second_binding, replacement).command_id == replacement.command_id

    third_binding = _binding(_request("workflow-receipt-memory-c"))
    bindings.put(third_binding)
    cross_runtime_collision = _command_with_nonce(
        third_binding,
        key_ring,
        command_id="dispatch-memory-shared-nonce",
        nonce="receipt-memory-shared",
        now=100.0,
    )
    dispatches = InMemoryWorkflowControlDispatchIntentStore(
        bindings,
        clock=lambda: 100.0,
        replay_store=replay,
    )
    with pytest.raises(
        WorkflowControlDispatchIntentError,
        match="workflow_control_dispatch_command_replay_detected",
    ):
        dispatches.stage_command(
            binding=third_binding,
            command=cross_runtime_collision,
        )
