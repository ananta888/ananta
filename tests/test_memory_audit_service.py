"""Real SQL audit and synthetic Hub policy; no production release evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import delete, event, update
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models.memory_audit import MemoryAuditEventDB, MemoryAuditHeadDB
from agent.repositories.memory_audit import SQLMemoryAuditRepository
from agent.services.ephemeral_scratchpad_service import EphemeralScratchpadService
from agent.services.memory_audit_service import MemoryAuditService
from agent.services.memory_channel_registry import MemoryChannelRegistry
from agent.services.memory_policy_gateway import MemoryAccessError, MemoryPolicyGateway
from ananta_contracts.memory_access import MemoryScope, MemorySecurityMetadata, MemoryTarget
from tests.security.memory_fixtures import ACTOR, NAMESPACE, PolicyFixture, policy


@pytest.fixture
def audit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.sqlite3'}")
    SQLModel.metadata.create_all(engine, tables=[MemoryAuditHeadDB.__table__, MemoryAuditEventDB.__table__])
    repository = SQLMemoryAuditRepository(engine)
    service = MemoryAuditService(repository=repository, retention_seconds=3600, clock=lambda: 100)
    policies = PolicyFixture(policy(trace_id="synthetic-trace"))
    gate = MemoryPolicyGateway(policies=policies, audit=service,
                               channels=MemoryChannelRegistry(frozenset({"run-scratchpad"})), clock=lambda: 100)
    yield engine, repository, service, gate, policies
    engine.dispose()


def authorize(gate):
    return gate.authorize_read(actor=ACTOR, run_id=NAMESPACE.run_id, target=MemoryTarget(
        "run-scratchpad", NAMESPACE, MemorySecurityMetadata(scope=MemoryScope.run),
    ))


def test_positive_and_negative_decisions_persist_without_memory_content(audit):
    _engine, repository, _service, gate, policies = audit
    authorize(gate)
    policies.value = replace(policies.value, capabilities=())
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        authorize(gate)
    rows = repository.list_events(tenant_id=ACTOR.tenant_id, task_id=NAMESPACE.task_id, trace_id="synthetic-trace")
    assert [row["sequence"] for row in rows] == [1, 2]
    assert [row["payload"]["allowed"] for row in rows] == [True, False]
    assert all(row["payload"]["event_kind"] == "policy_decision" for row in rows)
    assert rows[1]["previous_hash"] == rows[0]["event_hash"]
    assert all(row["retention_until"] == 3700 for row in rows)
    assert repository.verify(tenant_id=ACTOR.tenant_id) == {
        "verified": True, "reason_code": "memory_audit_chain_verified", "checked": 2,
        "production_release_evidence": False,
    }
    assert repository.list_events(tenant_id="another-tenant") == []
    assert repository.list_events(tenant_id=ACTOR.tenant_id, trace_id="another-trace") == []


@pytest.mark.parametrize("field,value", [
    ("event_hash", "a" * 64), ("previous_hash", "a" * 64), ("created_at", 200),
    ("retention_until", 5000), ("task_id", "foreign-task"), ("trace_id", "foreign-trace"),
    ("payload", {"allowed": True}),
])
def test_mutation_of_audit_or_query_bindings_is_detected(audit, field, value):
    engine, repository, _service, gate, _policies = audit
    authorize(gate)
    with Session(engine) as session:
        session.exec(update(MemoryAuditEventDB).values(**{field: value}))
        session.commit()
    assert not repository.verify(tenant_id=ACTOR.tenant_id)["verified"]
    with pytest.raises(MemoryAccessError, match="audit_unavailable"):
        authorize(gate)
    assert len(repository.list_events(tenant_id=ACTOR.tenant_id)) == 1


def test_missing_event_is_detected_even_when_remaining_rows_are_unchanged(audit):
    engine, repository, _service, gate, _policies = audit
    for _ in range(3):
        authorize(gate)
    with Session(engine) as session:
        session.exec(delete(MemoryAuditEventDB).where(MemoryAuditEventDB.sequence == 2))
        session.commit()
    assert repository.verify(tenant_id=ACTOR.tenant_id)["reason_code"] == "memory_audit_chain_invalid"


def test_head_mutation_is_detected(audit):
    engine, repository, _service, gate, _policies = audit
    authorize(gate)
    with Session(engine) as session:
        session.exec(update(MemoryAuditHeadDB).values(last_hash="f" * 64))
        session.commit()
    assert not repository.verify(tenant_id=ACTOR.tenant_id)["verified"]
    with pytest.raises(MemoryAccessError, match="audit_unavailable"):
        authorize(gate)


def test_query_results_are_detached_copies(audit):
    _engine, repository, _service, gate, _policies = audit
    authorize(gate)
    returned = repository.list_events(tenant_id=ACTOR.tenant_id)
    returned[0]["payload"]["actor"]["subject"] = "forged"
    assert repository.list_events(tenant_id=ACTOR.tenant_id)[0]["payload"]["actor"]["subject"] == ACTOR.subject
    assert repository.verify(tenant_id=ACTOR.tenant_id)["verified"]


def test_two_repository_instances_append_one_contiguous_chain(audit):
    engine, repository, service, gate, _policies = audit
    decision = authorize(gate)
    other = MemoryAuditService(repository=SQLMemoryAuditRepository(engine), retention_seconds=3600, clock=lambda: 100)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit((service if index % 2 else other).record, decision) for index in range(16)]
        for future in futures:
            future.result(timeout=10)
    assert [row["sequence"] for row in repository.list_events(tenant_id=ACTOR.tenant_id)] == list(range(1, 18))
    assert repository.verify(tenant_id=ACTOR.tenant_id)["verified"]


def test_separate_sqlite_engines_serialize_through_database_transaction(audit):
    engine, repository, _service, gate, _policies = audit
    other_engine = create_engine(engine.url)
    decision = authorize(gate)
    services = [MemoryAuditService(repository=SQLMemoryAuditRepository(bind), retention_seconds=3600, clock=lambda: 100)
                for bind in (engine, other_engine)]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(services[index % 2].record, decision) for index in range(12)]
            for future in futures:
                future.result(timeout=10)
        assert repository.verify(tenant_id=ACTOR.tenant_id)["checked"] == 13
        assert repository.verify(tenant_id=ACTOR.tenant_id)["verified"]
    finally:
        other_engine.dispose()


def test_verification_never_claims_success_for_a_truncated_window(audit):
    _engine, repository, _service, gate, _policies = audit
    authorize(gate)
    authorize(gate)
    result = repository.verify(tenant_id=ACTOR.tenant_id, limit=1)
    assert not result["verified"] and result["reason_code"] == "memory_audit_verification_window_exceeded"


def test_failed_audit_transaction_rolls_back_head_and_prevents_store_access(audit):
    engine, repository, _service, gate, _policies = audit
    authorize(gate)
    before = repository.list_events(tenant_id=ACTOR.tenant_id)
    def failure(_mapper, _connection, _target):
        raise RuntimeError("synthetic persistence failure")

    class ForbiddenStorage:
        def __getattr__(self, name):
            pytest.fail(f"Storage must not be accessed after audit failure: {name}")

    scratchpad = EphemeralScratchpadService(gateway=gate, store=ForbiddenStorage(), clock=lambda: 100)
    event.listen(MemoryAuditEventDB, "before_insert", failure)
    try:
        with pytest.raises(MemoryAccessError, match="audit_unavailable"):
            scratchpad.append(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch", value="private memory value")
    finally:
        event.remove(MemoryAuditEventDB, "before_insert", failure)
    assert repository.list_events(tenant_id=ACTOR.tenant_id) == before
    assert repository.verify(tenant_id=ACTOR.tenant_id)["verified"]
    with Session(engine) as session:
        assert session.get(MemoryAuditHeadDB, ACTOR.tenant_id).last_sequence == 1


def test_audit_retention_does_not_depend_on_run_expiry(audit):
    _engine, repository, service, gate, _policies = audit
    decision = authorize(gate)
    service.record(replace(decision, expires_at=101))
    assert repository.list_events(tenant_id=ACTOR.tenant_id)[-1]["retention_until"] == 3700


@pytest.mark.parametrize("retention", [True, 0, float("inf"), 60, 366 * 86400])
def test_invalid_audit_retention_is_rejected(retention):
    with pytest.raises(ValueError):
        MemoryAuditService(repository=None, retention_seconds=retention)
