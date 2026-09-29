from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.evidence_identity import (
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateError,
    HubEvidenceGateService,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_REVISION = "1" * 40


@pytest.fixture()
def gate_runtime():
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
    registry = HubEvidenceRegistryService(
        SqlEvidenceIdentityRepository(database),
        clock=iter([10.0, 20.0, 30.0, 40.0]).__next__,
    )
    return HubEvidenceGateService(registry), database


def _request(*, scope: str = "local", synthetic: bool = False) -> EvidenceGateRequest:
    return EvidenceGateRequest(
        tenant_id="tenant-1",
        project_id="project-1",
        task_id="gate-task",
        assignment_id="assignment-1",
        dispatch_lease_id="lease-1",
        repository_revision=_REVISION,
        input_digest=_A,
        execution_profile_digest=_B,
        environment_digest=_C,
        evidence_scope=scope,
        required_scope="local",
        idempotency_key="gate-execution-0001",
        sources=(
            EvidenceGateSourceAdmission(
                origin_type="repository_bundle",
                origin_digest=_A,
                content_digest=_B,
                policy_digest=_C,
                synthetic=synthetic,
            ),
        ),
        synthetic=synthetic,
    )


def test_gate_coordinates_closed_assignment_and_verified_result(gate_runtime) -> None:
    gate, _database = gate_runtime
    received: list[dict] = []

    outcome = gate.execute(
        _request(),
        lambda projection: received.append(dict(projection)) or {"passed": True, "tests": 7, "failures": 0},
    )

    assert outcome.passed is True
    assert outcome.verified is True
    assert outcome.reason_code == "verified"
    assert outcome.run_id.startswith("RUN_")
    assert outcome.source_ids[0].startswith("SRC_")
    assert received[0]["run_id"] == outcome.run_id
    assert received[0]["source_ids"] == list(outcome.source_ids)


def test_failed_gate_is_recorded_but_never_verified(gate_runtime) -> None:
    gate, database = gate_runtime

    outcome = gate.execute(
        _request(),
        lambda _projection: {"passed": False, "tests": 7, "failures": 1},
    )

    assert outcome.passed is False
    assert outcome.verified is False
    assert outcome.reason_code == "evidence_run_not_successful"
    with Session(database) as session:
        row = session.exec(select(HubRunEvidenceIdentityDB)).one()
    assert row.state == "failed"
    assert row.result_digest == outcome.result_digest


def test_executor_exception_terminalizes_reservation_without_leaking_message(gate_runtime) -> None:
    gate, database = gate_runtime

    def explode(_projection):
        raise RuntimeError("secret runtime detail")

    with pytest.raises(RuntimeError, match="secret runtime detail"):
        gate.execute(_request(), explode)

    with Session(database) as session:
        row = session.exec(select(HubRunEvidenceIdentityDB)).one()
    assert row.state == "failed"
    assert row.result_digest is not None


def test_executor_interrupt_terminalizes_reservation_as_cancelled(gate_runtime) -> None:
    gate, database = gate_runtime

    def interrupt(_projection):
        raise KeyboardInterrupt("operator interrupt detail")

    with pytest.raises(KeyboardInterrupt, match="operator interrupt detail"):
        gate.execute(_request(), interrupt)

    with Session(database) as session:
        row = session.exec(select(HubRunEvidenceIdentityDB)).one()
    assert row.state == "cancelled"
    assert row.result_digest is not None


def test_gate_rejects_non_boolean_or_non_json_execution_results(gate_runtime) -> None:
    gate, _database = gate_runtime

    with pytest.raises(HubEvidenceGateError, match="evidence_gate_passed_flag_required"):
        gate.execute(_request(), lambda _projection: {"passed": "yes"})


def test_synthetic_test_run_cannot_satisfy_local_release(gate_runtime) -> None:
    gate, _database = gate_runtime
    request = _request(scope="test", synthetic=True)

    outcome = gate.execute(request, lambda _projection: {"passed": True})

    assert outcome.passed is True
    assert outcome.verified is False
    assert outcome.reason_code == "evidence_run_test_scope_forbidden"
