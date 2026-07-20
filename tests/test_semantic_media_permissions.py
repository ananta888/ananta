from __future__ import annotations

import pytest
from sqlmodel import SQLModel, create_engine

from agent.db_models import (
    SemanticMediaAuditEventDB,
    SemanticMediaAuditOutboxDB,
    SemanticMediaCapabilityGrantDB,
)
from agent.repositories.semantic_media_capability_grant_repository import (
    InMemorySemanticMediaCapabilityGrantRepository,
    SqlSemanticMediaCapabilityGrantRepository,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_permission_service import (
    SemanticMediaPermissionError,
    SemanticMediaPermissionService,
)
from ananta_contracts.semantic_media_permissions import SEMANTIC_CAPABILITIES


def _issue(service: SemanticMediaPermissionService, capability: str = "compute"):
    return service.issue(
        authorised_capabilities=SEMANTIC_CAPABILITIES,
        owner_id="owner-1",
        tenant_id="tenant-1",
        subject_id="worker-1",
        subject_role="compute_executor",
        capability=capability,
        scope_kind="session",
        scope_id="session-1",
        direction="ingress",
        data_type="audio/pcm",
        purpose="transcription",
        epoch=4,
        expires_at=1500,
    )


def _memory_service(*, clock) -> SemanticMediaPermissionService:
    return SemanticMediaPermissionService(
        b"k" * 32,
        repository=InMemorySemanticMediaCapabilityGrantRepository(),
        clock=clock,
    )


@pytest.mark.parametrize("capability", SEMANTIC_CAPABILITIES)
def test_each_capability_grants_only_with_exact_scope(capability: str) -> None:
    service = _memory_service(clock=lambda: 1000)
    grant = _issue(service, capability)
    assert service.evaluate(
        grant,
        tenant_id="tenant-1",
        subject_id="worker-1",
        scope_kind="session",
        scope_id="session-1",
        direction="ingress",
        data_type="audio/pcm",
        purpose="transcription",
        epoch=4,
    ) == (True, "ok")
    assert service.evaluate(
        grant,
        tenant_id="tenant-2",
        subject_id="worker-1",
        scope_kind="session",
        scope_id="session-1",
        direction="ingress",
        data_type="audio/pcm",
        purpose="transcription",
        epoch=4,
    ) == (False, "tenant_mismatch")


@pytest.mark.parametrize("capability", SEMANTIC_CAPABILITIES)
def test_each_capability_has_full_fail_closed_lifecycle_matrix(capability: str) -> None:
    now = [1000.0]
    service = _memory_service(clock=lambda: now[0])
    grant = _issue(service, capability)
    context = {
        "capability": capability,
        "tenant_id": "tenant-1",
        "subject_id": "worker-1",
        "scope_kind": "session",
        "scope_id": "session-1",
        "direction": "ingress",
        "data_type": "audio/pcm",
        "purpose": "transcription",
        "epoch": 4,
    }
    assert service.evaluate(grant, **context) == (True, "ok")
    mismatches = (
        ({"capability": next(item for item in SEMANTIC_CAPABILITIES if item != capability)}, "capability_mismatch"),
        ({"tenant_id": "tenant-2"}, "tenant_mismatch"),
        ({"subject_id": "worker-2"}, "subject_mismatch"),
        ({"scope_kind": "room"}, "scope_mismatch"),
        ({"scope_id": "session-2"}, "scope_mismatch"),
        ({"direction": "egress"}, "direction_mismatch"),
        ({"data_type": "text/plain"}, "data_type_mismatch"),
        ({"purpose": "training"}, "purpose_mismatch"),
        ({"epoch": 5}, "epoch_mismatch"),
    )
    for changes, reason in mismatches:
        assert service.evaluate(grant, **{**context, **changes}) == (False, reason)

    service.revoke(grant.grant_id, tenant_id="tenant-1", actor_id="owner-1")
    assert service.evaluate(grant, **context) == (False, "capability_revoked")
    now[0] = 1501.0
    assert service.evaluate(grant, **context) == (False, "capability_expired")


@pytest.mark.parametrize("capability", SEMANTIC_CAPABILITIES)
def test_each_capability_cannot_expand_authorised_rights(capability: str) -> None:
    service = _memory_service(clock=lambda: 1000)
    with pytest.raises(SemanticMediaPermissionError, match="capability_escalation_denied"):
        service.issue(
            authorised_capabilities=set(SEMANTIC_CAPABILITIES) - {capability},
            owner_id="owner-1",
            tenant_id="tenant-1",
            subject_id="worker-1",
            subject_role="compute_executor",
            capability=capability,
            scope_kind="session",
            scope_id="session-1",
            direction="ingress",
            data_type="audio/pcm",
            purpose="transcription",
            epoch=4,
            expires_at=1500,
        )


def test_client_cannot_escalate_or_use_ambiguous_master_role() -> None:
    service = _memory_service(clock=lambda: 1000)
    with pytest.raises(SemanticMediaPermissionError, match="capability_escalation_denied"):
        service.issue(
            authorised_capabilities={"capture"},
            owner_id="o",
            tenant_id="t",
            subject_id="w",
            subject_role="compute_executor",
            capability="compute",
            scope_kind="session",
            scope_id="s",
            direction="none",
            data_type="text",
            purpose="validate",
            epoch=1,
            expires_at=1100,
        )
    with pytest.raises(SemanticMediaPermissionError, match="subject_role_invalid"):
        service.issue(
            authorised_capabilities={"compute"},
            owner_id="o",
            tenant_id="t",
            subject_id="w",
            subject_role="master",
            capability="compute",
            scope_kind="session",
            scope_id="s",
            direction="none",
            data_type="text",
            purpose="validate",
            epoch=1,
            expires_at=1100,
        )


def test_expiry_revoke_and_scope_mismatch_are_stable() -> None:
    now = [1000.0]
    service = _memory_service(clock=lambda: now[0])
    grant = _issue(service)
    service.revoke(grant.grant_id)
    result = service.evaluate(
        grant,
        tenant_id="tenant-1",
        subject_id="worker-1",
        scope_kind="room",
        scope_id="session-1",
        direction="ingress",
        data_type="audio/pcm",
        purpose="transcription",
        epoch=4,
    )
    assert result == (False, "capability_revoked")
    now[0] = 1501
    assert service.evaluate(
        grant,
        tenant_id="tenant-1",
        subject_id="worker-1",
        scope_kind="session",
        scope_id="session-1",
        direction="ingress",
        data_type="audio/pcm",
        purpose="transcription",
        epoch=4,
    ) == (False, "capability_expired")


def test_sql_revocation_survives_service_and_repository_restart() -> None:
    db_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        db_engine,
        tables=[
            SemanticMediaCapabilityGrantDB.__table__,
            SemanticMediaAuditEventDB.__table__,
            SemanticMediaAuditOutboxDB.__table__,
        ],
    )
    key = b"p" * 32
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(InMemorySemanticMediaAuditRepository(), clock_ms=lambda: 1_000_000),
        secret=b"capability-persistent-audit-key" * 2,
    )
    first = SemanticMediaPermissionService(
        key,
        repository=SqlSemanticMediaCapabilityGrantRepository(db_engine=db_engine),
        audit=audit,
        clock=lambda: 1000,
    )
    grant = _issue(first, "validate")
    context = {
        "capability": "validate",
        "tenant_id": "tenant-1",
        "subject_id": "worker-1",
        "scope_kind": "session",
        "scope_id": "session-1",
        "direction": "ingress",
        "data_type": "audio/pcm",
        "purpose": "transcription",
        "epoch": 4,
    }
    restarted = SemanticMediaPermissionService(
        key,
        repository=SqlSemanticMediaCapabilityGrantRepository(db_engine=db_engine),
        audit=audit,
        clock=lambda: 1001,
    )
    assert restarted.evaluate_grant_id(grant.grant_id, **context) == (True, "ok")
    with pytest.raises(SemanticMediaPermissionError, match="capability_not_found"):
        restarted.revoke(grant.grant_id, tenant_id="tenant-2", actor_id="owner-1")
    restarted.revoke(grant.grant_id, tenant_id="tenant-1", actor_id="owner-1")

    second_restart = SemanticMediaPermissionService(
        key,
        repository=SqlSemanticMediaCapabilityGrantRepository(db_engine=db_engine),
        audit=audit,
        clock=lambda: 1002,
    )
    assert second_restart.evaluate_grant_id(grant.grant_id, **context) == (
        False,
        "capability_revoked",
    )
