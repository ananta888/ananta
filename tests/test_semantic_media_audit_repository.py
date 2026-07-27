from __future__ import annotations

import hashlib

import pytest
from flask import Flask
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.bootstrap.semantic_media_services import initialize_semantic_media_services
from agent.repositories.semantic_media_audit_repository import (
    SqlSemanticMediaAuditRepository,
)
from agent.services.media_topology_policy import MediaTopologyPolicy
from agent.services.semantic_fanout_coordination_service import (
    SemanticFanoutCoordinationService,
)
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditError,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_debug_read_model import SemanticMediaDebugReadModel

NOW = 2_000_000


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def repository() -> SqlSemanticMediaAuditRepository:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return SqlSemanticMediaAuditRepository(db_engine=engine)


def _record(
    service: SemanticMediaAuditService,
    key: str,
    *,
    scope: str = "scope-a",
    transition: str = "activated",
):
    return service.record_transition(
        idempotency_key=key,
        tenant_digest=_digest("tenant-a"),
        scope_digest=_digest(scope),
        event_type="semantic_contract",
        transition=transition,
        reason_code="hub_confirmed",
        epoch=3,
        contract_ref=_digest("contract-a"),
        retention_ms=3_600_000,
    )


def test_sql_audit_is_durable_idempotent_scoped_and_paginated(repository) -> None:
    clock = {"now": NOW}
    service = SemanticMediaAuditService(repository, clock_ms=lambda: clock["now"])
    first, created = _record(service, "audit-key-first")
    clock["now"] += 500
    replay, replay_created = _record(service, "audit-key-first")
    _record(service, "audit-key-second")
    _record(service, "audit-key-other", scope="scope-b")

    assert created is True
    assert replay_created is False
    assert replay == first

    page, cursor = repository.page(
        tenant_digest=_digest("tenant-a"),
        scope_digest=_digest("scope-a"),
        after_event_id=None,
        limit=1,
        now_ms=NOW,
    )
    assert len(page) == 1 and cursor == page[0].event_id
    remainder, next_cursor = repository.page(
        tenant_digest=_digest("tenant-a"),
        scope_digest=_digest("scope-a"),
        after_event_id=cursor,
        limit=10,
        now_ms=NOW,
    )
    assert len(remainder) == 1 and next_cursor is None
    other, _ = repository.page(
        tenant_digest=_digest("tenant-a"),
        scope_digest=_digest("scope-b"),
        after_event_id=None,
        limit=10,
        now_ms=NOW,
    )
    assert len(other) == 1


def test_sql_audit_conflict_cursor_and_bounded_retention(repository) -> None:
    service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    _record(service, "audit-key-conflict")
    with pytest.raises(SemanticMediaAuditError, match="audit_idempotency_conflict"):
        _record(service, "audit-key-conflict", transition="revoked")
    with pytest.raises(SemanticMediaAuditError, match="audit_cursor_invalid"):
        repository.page(
            tenant_digest=_digest("tenant-a"),
            scope_digest=_digest("scope-a"),
            after_event_id="audit-does-not-exist",
            limit=10,
            now_ms=NOW,
        )
    assert repository.delete_expired(now_ms=NOW + 3_600_001, limit=1) == 1
    assert repository.delete_expired(now_ms=NOW + 3_600_001, limit=1) == 0


def test_sql_audit_supports_bounded_scope_and_tenant_erasure(repository) -> None:
    service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    _record(service, "scope-delete-a", scope="scope-a")
    _record(service, "scope-delete-b", scope="scope-b")
    assert repository.delete_scope(
        tenant_digest=_digest("tenant-a"),
        scope_digest=_digest("scope-a"),
        limit=1,
    ) == 1
    assert repository.delete_scope(
        tenant_digest=_digest("tenant-a"),
        scope_digest=_digest("scope-a"),
        limit=1,
    ) == 0
    assert repository.delete_tenant(tenant_digest=_digest("tenant-a"), limit=1) == 1


def test_hub_composition_wires_persistent_read_only_audit(monkeypatch) -> None:
    repository = object()
    monkeypatch.setattr(
        "agent.bootstrap.semantic_media_services.SqlSemanticMediaAuditRepository",
        lambda: repository,
    )
    app = Flask(__name__)
    app.secret_key = "test-only-semantic-media-audit-key-32-bytes"
    initialize_semantic_media_services(app)
    assert app.extensions["semantic_media_audit_repository"] is repository
    assert isinstance(
        app.extensions["semantic_media_debug_read_model"],
        SemanticMediaDebugReadModel,
    )
    assert isinstance(app.extensions["semantic_media_topology_policy"], MediaTopologyPolicy)
    assert isinstance(
        app.extensions["semantic_media_fanout_coordination"],
        SemanticFanoutCoordinationService,
    )
    assert app.extensions["semantic_media_feature_flags"] == {
        "ordinary_media_publication": False,
        "semantic_visual_capture": False,
        "semantic_speech_runtime": False,
        "semantic_media_sfu": False,
        "semantic_media_background_operations": False,
        "peer_evidence_sync": False,
        "speech_reconciliation": False,
        "speech_adaptation_training": False,
        "speech_adapter_routing": False,
        "semantic_media_broadcast": False,
        "semantic_media_receiver_groups": False,
        "semantic_media_fleet_admission": False,
        "semantic_media_turn_cost_controls": False,
    }
