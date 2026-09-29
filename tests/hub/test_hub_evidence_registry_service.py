from __future__ import annotations

import pytest
from sqlalchemy import update
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models.evidence_identity import (
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import (
    EvidenceIdentityPersistenceError,
    SqlEvidenceIdentityRepository,
)
from agent.services.hub_evidence_registry_service import (
    HubEvidenceRegistryError,
    HubEvidenceRegistryService,
)
from ananta_contracts.hub_evidence import (
    HubEvidenceAssignmentError,
    validate_hub_evidence_assignment,
)

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_REVISION = "1" * 40


def test_pinned_source_lookup_preserves_test_classification_and_does_not_issue(service):
    source = _source(service, scope="test", synthetic=True)
    verified = service.require_source_identity(
        tenant_id=source.tenant_id,
        project_id=source.project_id,
        source_id=source.source_id,
        expected_binding_digest=source.binding_digest,
    )
    assert verified == source and verified.synthetic and verified.evidence_scope == "test"
    with pytest.raises(HubEvidenceRegistryError, match="mutated"):
        service.require_source_identity(
            tenant_id=source.tenant_id,
            project_id=source.project_id,
            source_id=source.source_id,
            expected_binding_digest="f" * 64,
        )


def test_pinned_source_lookup_rejects_changed_persisted_content(service):
    source = _source(service)
    with Session(service._repository._database) as session:
        session.exec(update(HubSourceEvidenceIdentityDB).values(content_digest="f" * 64))
        session.commit()
    with pytest.raises(HubEvidenceRegistryError, match="mutated"):
        service.require_source_identity(
            tenant_id=source.tenant_id,
            project_id=source.project_id,
            source_id=source.source_id,
            expected_binding_digest=source.binding_digest,
        )


def test_pinned_source_lookup_rejects_unknown_or_cross_project_identity(service):
    source = _source(service)
    with pytest.raises(HubEvidenceRegistryError, match="unavailable"):
        service.require_source_identity(
            tenant_id=source.tenant_id,
            project_id="foreign",
            source_id=source.source_id,
            expected_binding_digest=source.binding_digest,
        )


@pytest.fixture()
def service() -> HubEvidenceRegistryService:
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        database,
        tables=[
            HubSourceEvidenceIdentityDB.__table__,
            HubRunEvidenceIdentityDB.__table__,
        ],
    )
    return HubEvidenceRegistryService(
        SqlEvidenceIdentityRepository(database),
        clock=iter([10.0, 20.0, 30.0, 40.0, 50.0]).__next__,
    )


def _source(
    service: HubEvidenceRegistryService,
    *,
    scope: str = "production",
    synthetic: bool = False,
    tenant_id: str = "tenant-1",
    supplied_source_id: str | None = None,
    issuer: str = "hub-evidence-registry",
):
    return service.register_source(
        tenant_id=tenant_id,
        project_id="project-1",
        origin_type="dataset",
        origin_digest=_A,
        content_digest=_B,
        policy_digest=_C,
        evidence_scope=scope,
        synthetic=synthetic,
        supplied_source_id=supplied_source_id,
        issuer=issuer,
    )


def _run(
    service: HubEvidenceRegistryService,
    *,
    source_id: str,
    scope: str = "production",
    synthetic: bool = False,
    tenant_id: str = "tenant-1",
    idempotency_key: str = "evidence-run-key-1",
):
    return service.reserve_run(
        tenant_id=tenant_id,
        project_id="project-1",
        task_id="task-1",
        assignment_id="assignment-1",
        dispatch_lease_id="lease-1",
        repository_revision=_REVISION,
        input_digest=_A,
        execution_profile_digest=_B,
        environment_digest=_C,
        source_ids=[source_id],
        evidence_scope=scope,
        idempotency_key=idempotency_key,
        synthetic=synthetic,
    )


def test_hub_automatically_issues_idempotent_source_identity(service) -> None:
    first = _source(service)
    second = _source(service)

    assert first == second
    assert first.source_id.startswith("SRC_")
    assert len(first.source_id) == 36
    assert first.issuer == "hub-evidence-registry"
    assert first.state == "admitted"


def test_externally_supplied_identity_requires_named_external_issuer(service) -> None:
    with pytest.raises(HubEvidenceRegistryError) as caught:
        _source(service, supplied_source_id="SRC_0001")
    assert caught.value.reason_code == "evidence_external_identifier_issuer_required"

    source = _source(
        service,
        supplied_source_id="SRC_0001",
        issuer="external:source-catalog",
    )
    assert source.source_id == "SRC_0001"
    assert source.issuer == "external:source-catalog"


def test_same_compatibility_identifier_is_scoped_per_tenant(service) -> None:
    first = _source(
        service,
        tenant_id="tenant-1",
        supplied_source_id="SRC_0001",
        issuer="external:catalog-a",
    )
    second = _source(
        service,
        tenant_id="tenant-2",
        supplied_source_id="SRC_0001",
        issuer="external:catalog-a",
    )
    assert first.source_id == second.source_id
    assert first.binding_digest != second.binding_digest


def test_hub_reserves_run_before_execution_and_verifies_bound_result(service) -> None:
    source = _source(service)
    reserved = _run(service, source_id=source.source_id)

    assert reserved.run_id.startswith("RUN_")
    assert reserved.state == "reserved"
    assert reserved.result_digest is None

    projection = service.assignment_projection(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=reserved.run_id,
        task_id="task-1",
        assignment_id="assignment-1",
        dispatch_lease_id="lease-1",
    )
    assert projection["run_id"] == reserved.run_id
    assert projection["source_ids"] == [source.source_id]

    completed = service.record_result(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=reserved.run_id,
        assignment_id="assignment-1",
        dispatch_lease_id="lease-1",
        terminal_state="succeeded",
        result_digest=_D,
    )
    assert completed.state == "succeeded"

    verification = service.verify_release_binding(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=reserved.run_id,
        required_scope="production",
        task_id="task-1",
        repository_revision=_REVISION,
        source_ids=[source.source_id],
    )
    assert verification.verified is True
    assert verification.reason_code == "verified"


def test_worker_assignment_projection_rejects_mutation(service) -> None:
    source = _source(service)
    reserved = _run(service, source_id=source.source_id)
    projection = service.assignment_projection(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=reserved.run_id,
        task_id="task-1",
        assignment_id="assignment-1",
        dispatch_lease_id="lease-1",
    )
    projection["source_ids"] = ["SRC_unauthorized"]

    with pytest.raises(HubEvidenceAssignmentError) as caught:
        validate_hub_evidence_assignment(projection)
    assert str(caught.value) == "hub_evidence_assignment_digest_mismatch"


def test_worker_cannot_complete_a_run_under_another_lease(service) -> None:
    source = _source(service)
    reserved = _run(service, source_id=source.source_id)

    with pytest.raises(EvidenceIdentityPersistenceError) as caught:
        service.record_result(
            tenant_id="tenant-1",
            project_id="project-1",
            run_id=reserved.run_id,
            assignment_id="assignment-1",
            dispatch_lease_id="stale-lease",
            terminal_state="succeeded",
            result_digest=_D,
        )
    assert caught.value.reason_code == "evidence_run_assignment_binding_mismatch"


def test_synthetic_test_evidence_never_satisfies_production_release(service) -> None:
    source = _source(service, scope="test", synthetic=True)
    reserved = _run(
        service,
        source_id=source.source_id,
        scope="test",
        synthetic=True,
    )
    service.record_result(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=reserved.run_id,
        assignment_id="assignment-1",
        dispatch_lease_id="lease-1",
        terminal_state="succeeded",
        result_digest=_D,
    )

    verification = service.verify_release_binding(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=reserved.run_id,
        required_scope="production",
        task_id="task-1",
        repository_revision=_REVISION,
        source_ids=[source.source_id],
    )
    assert verification.verified is False
    assert verification.reason_code == "evidence_run_test_scope_forbidden"


def test_local_source_cannot_be_promoted_by_a_production_run(service) -> None:
    source = _source(service, scope="local")
    with pytest.raises(HubEvidenceRegistryError) as caught:
        _run(service, source_id=source.source_id, scope="production")
    assert caught.value.reason_code == "evidence_run_source_scope_forbidden"


def test_release_verifier_fails_closed_for_malformed_identity(service) -> None:
    verification = service.verify_release_binding(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id="RUN_missing",
        required_scope="production",
        task_id="task-1",
        repository_revision=_REVISION,
        source_ids=["not-a-source-id"],
    )
    assert verification.verified is False
    assert verification.reason_code == "evidence_source_ids_invalid"


def test_reservation_key_replay_cannot_change_bound_inputs(service) -> None:
    source = _source(service)
    _run(service, source_id=source.source_id)

    with pytest.raises(EvidenceIdentityPersistenceError) as caught:
        service.reserve_run(
            tenant_id="tenant-1",
            project_id="project-1",
            task_id="task-2",
            assignment_id="assignment-2",
            dispatch_lease_id="lease-2",
            repository_revision=_REVISION,
            input_digest=_A,
            execution_profile_digest=_B,
            environment_digest=_C,
            source_ids=[source.source_id],
            evidence_scope="production",
            idempotency_key="evidence-run-key-1",
        )
    assert caught.value.reason_code == "evidence_run_identity_immutable_conflict"
