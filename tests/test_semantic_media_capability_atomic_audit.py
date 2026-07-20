from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import (
    SemanticMediaAuditEventDB,
    SemanticMediaAuditOutboxDB,
    SemanticMediaCapabilityGrantDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.semantic_media_capability_grant_repository import (
    SemanticMediaCapabilityGrantRepositoryError,
    SqlSemanticMediaCapabilityGrantRepository,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_permission_service import SemanticMediaPermissionService


def _stack():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SemanticMediaCapabilityGrantDB.__table__,
            SemanticMediaAuditEventDB.__table__,
            SemanticMediaAuditOutboxDB.__table__,
        ],
    )
    recorder = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: 1_000_000,
        ),
        secret=b"semantic-capability-atomic-audit-key" * 2,
    )
    repository = SqlSemanticMediaCapabilityGrantRepository(db_engine=engine)
    service = SemanticMediaPermissionService(
        b"s" * 32,
        repository=repository,
        audit=recorder,
        clock=lambda: 1000,
    )
    return engine, recorder, repository, service


def _issue(service: SemanticMediaPermissionService):
    return service.issue(
        authorised_capabilities={"compute"},
        owner_id="owner-a",
        tenant_id="tenant-a",
        subject_id="worker-a",
        subject_role="compute_executor",
        capability="compute",
        scope_kind="session",
        scope_id="session-a",
        direction="bidirectional",
        data_type="application/vnd.ananta.semantic-media-control+json",
        purpose="semantic_media_control",
        epoch=1,
        expires_at=1300,
    )


def _rows(engine, model):
    with Session(engine) as db:
        return list(db.exec(select(model)))


def test_issue_revoke_and_replays_share_one_domain_and_audit_transaction() -> None:
    engine, recorder, repository, service = _stack()
    grant = _issue(service)
    assert len(_rows(engine, SemanticMediaCapabilityGrantDB)) == 1
    assert [row.transition for row in _rows(engine, SemanticMediaAuditOutboxDB)] == [
        "capability_granted"
    ]

    replay_event = recorder.prepare_transition(
        idempotency_key=f"semantic-capability:{grant.grant_id}:granted",
        tenant_id=grant.tenant_id,
        scope="session:session-a",
        event_type="semantic_admission",
        transition="capability_granted",
        reason_code="capability_granted",
        epoch=1,
        contract_ref=grant.grant_id,
    )
    assert repository.create(grant, audit_event=replay_event).grant == grant
    assert len(_rows(engine, SemanticMediaCapabilityGrantDB)) == 1
    assert len(_rows(engine, SemanticMediaAuditOutboxDB)) == 1

    first = service.revoke(grant.grant_id, tenant_id="tenant-a", actor_id="owner-a")
    replay = service.revoke(grant.grant_id, tenant_id="tenant-a", actor_id="owner-a")
    assert first.revocation_version == replay.revocation_version == 1
    assert len(_rows(engine, SemanticMediaCapabilityGrantDB)) == 1
    assert sorted(row.transition for row in _rows(engine, SemanticMediaAuditOutboxDB)) == [
        "capability_granted",
        "capability_revoked",
    ]


def test_issue_rolls_back_when_audit_enqueue_fails(monkeypatch) -> None:
    engine, _recorder, _repository, service = _stack()

    def reject_audit(*_args, **_kwargs):
        raise RuntimeError("audit_enqueue_failed")

    monkeypatch.setattr(SqlSemanticMediaAuditOutbox, "enqueue_in_session", reject_audit)
    with pytest.raises(RuntimeError, match="audit_enqueue_failed"):
        _issue(service)
    assert _rows(engine, SemanticMediaCapabilityGrantDB) == []
    assert _rows(engine, SemanticMediaAuditOutboxDB) == []


def test_revoke_rolls_back_when_audit_enqueue_fails(monkeypatch) -> None:
    engine, _recorder, _repository, service = _stack()
    grant = _issue(service)

    def reject_audit(*_args, **_kwargs):
        raise RuntimeError("audit_enqueue_failed")

    monkeypatch.setattr(SqlSemanticMediaAuditOutbox, "enqueue_in_session", reject_audit)
    with pytest.raises(RuntimeError, match="audit_enqueue_failed"):
        service.revoke(grant.grant_id, tenant_id="tenant-a", actor_id="owner-a")
    rows = _rows(engine, SemanticMediaCapabilityGrantDB)
    assert len(rows) == 1
    assert rows[0].revoked_at is None
    assert rows[0].revocation_version == 0
    assert len(_rows(engine, SemanticMediaAuditOutboxDB)) == 1


def test_sql_authority_rejects_unaudited_mutation() -> None:
    _engine, _recorder, repository, service = _stack()
    grant = _issue(service)
    with pytest.raises(
        SemanticMediaCapabilityGrantRepositoryError,
        match="capability_audit_required",
    ):
        repository.revoke(
            grant.grant_id,
            tenant_id="tenant-a",
            revoked_by="owner-a",
            revoked_at=1001,
        )
