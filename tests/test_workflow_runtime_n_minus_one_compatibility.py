from __future__ import annotations

import pytest

from agent.services.workflow_runtime.compatibility import (
    RUNTIME_CONTRACT_TARGETS,
    RuntimeContractMigrationService,
    build_default_runtime_upcaster_registry,
)
from agent.services.workflow_runtime.errors import UnsupportedSchemaVersion
from agent.services.workflow_runtime.events import (
    CanonicalWorkflowEvent,
    InMemoryEventStore,
    WorkflowRunProjection,
)
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.schema_evolution import QuarantinedContract
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
    SignatureValidationError,
    SignedCheckpoint,
    WorkflowState,
)
from tests.workflow_runtime_contract_fixtures import (
    n_minus_one_runtime_contract_fixture,
)


def _keys() -> HmacKeyRing:
    return HmacKeyRing({"key-1": "0123456789abcdef0123456789abcdef"}, active_key_id="key-1")


def test_all_runtime_contract_families_publish_one_step_n_minus_one_paths() -> None:
    registry = build_default_runtime_upcaster_registry()

    assert {
        contract_type: registry.migration_path(
            contract_type=contract_type,
            source_schema=target.replace(".v1", ".v0"),
            target_schema=target,
        )
        for contract_type, target in RUNTIME_CONTRACT_TARGETS.items()
    } == {
        contract_type: (target.replace(".v1", ".v0"), target)
        for contract_type, target in RUNTIME_CONTRACT_TARGETS.items()
    }


def test_n_minus_one_plan_and_state_are_migrated_then_validated() -> None:
    service = RuntimeContractMigrationService()
    fixture = n_minus_one_runtime_contract_fixture()
    plan_v0 = fixture["plan"]
    migrated_plan = service.migrate(
        plan_v0,
        contract_type="plan",
        validator=ExecutionPlan.from_mapping,
    )
    migrated_state = service.migrate(
        fixture["state"],
        contract_type="state",
        validator=WorkflowState.from_mapping,
    )

    assert isinstance(migrated_plan, dict)
    assert ExecutionPlan.from_mapping(migrated_plan).schema == "ananta.execution_plan.v1"
    assert isinstance(migrated_state, dict)
    assert WorkflowState.from_mapping(migrated_state).artifact_refs == ()


def test_public_contract_loaders_apply_n_minus_one_upcasters_automatically() -> None:
    plan_v0 = n_minus_one_runtime_contract_fixture()["plan"]

    loaded = ExecutionPlan.from_mapping(plan_v0)

    assert loaded.schema == "ananta.execution_plan.v1"
    assert loaded.plan_hash


def test_shared_n_minus_one_fixture_is_deterministic_and_isolated() -> None:
    first = n_minus_one_runtime_contract_fixture()
    second = n_minus_one_runtime_contract_fixture()

    assert first == second
    first["plan"]["nodes"][0]["id"] = "mutated"
    assert second["plan"]["nodes"][0]["id"] == "step-1"


@pytest.mark.parametrize(
    ("loader", "payload"),
    [
        (
            ExecutionPlan.from_mapping,
            {"schema": "ananta.execution_plan.v99", "tenant_id": "tenant-a"},
        ),
        (
            WorkflowState.from_mapping,
            {"schema": "ananta.workflow_state.v99"},
        ),
    ],
)
def test_public_contract_loaders_quarantine_explicit_unknown_versions(
    loader, payload
) -> None:
    with pytest.raises(
        UnsupportedSchemaVersion,
        match="runtime_contract_quarantined",
    ):
        loader(payload)


def test_n_minus_one_event_can_be_reprojected_without_inventing_identifiers() -> None:
    event_id = "event-provided-by-fixture"
    migrated = RuntimeContractMigrationService().migrate(
        {
            "schema": "ananta.workflow_event.v0",
            "event_id": event_id,
            "tenant_id": "tenant-1",
            "workflow_id": "workflow-1",
            "run_id": "run-1",
            "event_type": "workflow.run.started",
            "correlation_id": "correlation-provided",
            "causation_id": "causation-provided",
            "sequence": 1,
            "occurred_at": 100,
        },
        contract_type="event",
        validator=CanonicalWorkflowEvent.from_mapping,
    )

    assert isinstance(migrated, dict)
    event = CanonicalWorkflowEvent.from_mapping(migrated)
    assert event.event_id == event_id
    assert event.dedupe_key == event_id
    store = InMemoryEventStore()
    unsequenced = CanonicalWorkflowEvent.from_mapping({**event.to_dict(), "sequence": 0}, validate=False)
    store.append(unsequenced, expected_sequence=0)
    projection = WorkflowRunProjection.rebuild(
        tenant_id="tenant-1",
        run_id="run-1",
        events=store.list_events(tenant_id="tenant-1", run_id="run-1"),
    )
    assert projection.status == "running"


def test_migrated_signed_contracts_still_require_hub_resigning() -> None:
    keys = _keys()
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=keys,
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="a" * 64,
        policy_version="policy-1",
        now=100,
        ttl_seconds=60,
    )
    checkpoint = SignedCheckpoint.issue(
        key_ring=keys,
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        task_id="task-1",
        plan_hash="a" * 64,
        policy_version="policy-1",
        runtime_id="native",
        runtime_version="0",
        state=WorkflowState(),
        revision=1,
        fencing_token=1,
        now=100,
    )
    service = RuntimeContractMigrationService()
    migrated_envelope = service.migrate(
        {
            **envelope.to_dict(),
            "schema": "ananta.runtime_authorization.v0",
            "signature": "signature-issued-over-v0-schema",
        },
        contract_type="authorization",
        validator=RuntimeAuthorizationEnvelope.from_mapping,
    )
    migrated_checkpoint = service.migrate(
        {
            **checkpoint.to_dict(),
            "schema": "ananta.workflow_checkpoint.v0",
            "signature": "signature-issued-over-v0-schema",
            "state": {**checkpoint.state.to_dict(), "schema": "ananta.workflow_state.v0"},
        },
        contract_type="checkpoint",
        validator=SignedCheckpoint.from_mapping,
    )

    assert isinstance(migrated_envelope, dict)
    assert isinstance(migrated_checkpoint, dict)
    with pytest.raises(SignatureValidationError, match="signature_invalid"):
        RuntimeAuthorizationEnvelope.from_mapping(migrated_envelope).verify(
            key_ring=keys,
            tenant_id="tenant-1",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            plan_hash="a" * 64,
            policy_version="policy-1",
            now=120,
        )
    with pytest.raises(SignatureValidationError, match="signature_invalid"):
        SignedCheckpoint.from_mapping(migrated_checkpoint).verify(
            key_ring=keys,
            tenant_id="tenant-1",
            workflow_id="workflow-1",
            run_id="run-1",
            task_id="task-1",
            plan_hash="a" * 64,
            policy_version="policy-1",
        )


def test_unknown_or_invalid_n_minus_one_data_is_quarantined_and_redacted() -> None:
    service = RuntimeContractMigrationService()

    unknown = service.migrate(
        {"schema": "ananta.workflow_state.v99", "api_key": "not-visible"},
        contract_type="state",
        validator=WorkflowState.from_mapping,
    )
    invalid = service.migrate(
        {"schema": "ananta.workflow_state.v0", "business_data": {"password": "not-visible"}},
        contract_type="state",
        validator=WorkflowState.from_mapping,
    )

    assert isinstance(unknown, QuarantinedContract)
    assert unknown.payload["api_key"] == "[REDACTED]"
    assert isinstance(invalid, QuarantinedContract)
    assert invalid.payload["business_data"]["password"] == "[REDACTED]"
