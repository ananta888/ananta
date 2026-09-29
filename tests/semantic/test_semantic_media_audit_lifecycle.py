from __future__ import annotations

import hashlib

import pytest
from flask import Flask

from agent.routes.semantic_media_debug import semantic_media_debug_bp
from agent.routes.semantic_media_privacy import semantic_media_privacy_bp
from agent.services.semantic_media_audit_lifecycle_service import (
    SemanticMediaAuditLifecyclePrincipal,
    SemanticMediaAuditLifecycleService,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditError,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_debug_read_model import SemanticMediaDebugReadModel
from agent.services.user_session_tokens import issue_user_access_token, local_user_tenant_id

NOW = 2_000_000


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _seed(repository: InMemorySemanticMediaAuditRepository, scope: str, index: int) -> None:
    SemanticMediaAuditService(repository, clock_ms=lambda: NOW + index).record_transition(
        idempotency_key=f"lifecycle-event-{scope}-{index}",
        tenant_digest=_digest("tenant"),
        scope_digest=_digest(scope),
        event_type="semantic_contract",
        transition="activated",
        reason_code="hub_confirmed",
        epoch=1,
        contract_ref=_digest(f"contract-{index}"),
        retention_ms=3_600_000,
    )


def _principal(role: str) -> SemanticMediaAuditLifecyclePrincipal:
    return SemanticMediaAuditLifecyclePrincipal(
        tenant_digest=_digest("tenant"),
        subject_digest=_digest("subject"),
        roles=frozenset({role}),
    )


def test_export_is_role_scoped_bounded_content_free_and_digest_bound() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    _seed(repository, "scope-a", 1)
    _seed(repository, "scope-a", 2)
    _seed(repository, "scope-b", 3)
    lifecycle = SemanticMediaAuditLifecycleService(repository, clock_ms=lambda: NOW + 100)

    exported = lifecycle.export_scope(_principal("semantic_media_auditor"), scope_digest=_digest("scope-a"))

    assert exported["schema"] == "ananta.semantic-media-audit-export.v1"
    assert exported["event_count"] == 2
    assert len(str(exported["export_digest"])) == 64
    rendered = str(exported).casefold()
    assert "scope-a" not in rendered and "contract-1" not in rendered
    with pytest.raises(SemanticMediaAuditError, match="semantic_audit_export_forbidden"):
        lifecycle.export_scope(_principal("viewer"), scope_digest=_digest("scope-a"))


def test_scope_and_tenant_erasure_require_privacy_role_and_delete_idempotently() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    _seed(repository, "scope-a", 1)
    _seed(repository, "scope-b", 2)
    lifecycle = SemanticMediaAuditLifecycleService(repository, clock_ms=lambda: NOW + 100)

    with pytest.raises(SemanticMediaAuditError, match="semantic_audit_erasure_forbidden"):
        lifecycle.erase_scope(_principal("semantic_media_auditor"), scope_digest=_digest("scope-a"))
    privacy = _principal("semantic_media_privacy_officer")
    assert lifecycle.erase_scope(privacy, scope_digest=_digest("scope-a")) == 1
    assert lifecycle.erase_scope(privacy, scope_digest=_digest("scope-a")) == 0
    assert lifecycle.erase_tenant(privacy) == 1
    assert lifecycle.erase_tenant(privacy) == 0


def test_http_export_and_erasure_are_separate_role_scoped_workflows() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    audit_service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    recorder = SemanticMediaAuditRecorder(audit_service, secret=b"audit-lifecycle-http-secret" * 2)
    tenant = local_user_tenant_id("privacy-user")
    recorder.record_transition(
        idempotency_key="privacy-http-event",
        tenant_id=tenant,
        scope="semantic-media-session:privacy-test",
        event_type="semantic_consent",
        transition="revoked",
        reason_code="user_requested",
        epoch=1,
        contract_ref="privacy-contract",
        retention_ms=3_600_000,
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(semantic_media_debug_bp)
    app.register_blueprint(semantic_media_privacy_bp)
    app.extensions["semantic_media_audit_recorder"] = recorder
    app.extensions["semantic_media_debug_read_model"] = SemanticMediaDebugReadModel(
        repository, clock_ms=lambda: NOW
    )
    app.extensions["semantic_media_audit_lifecycle_service"] = SemanticMediaAuditLifecycleService(
        repository, clock_ms=lambda: NOW
    )
    client = app.test_client()
    scope = "semantic-media-session:privacy-test"
    auditor = issue_user_access_token(username="privacy-user", role="semantic_media_auditor")
    privacy = issue_user_access_token(username="privacy-user", role="semantic_media_privacy_officer")

    exported = client.get(
        f"/v1/semantic-media/privacy/audit-export?scope={scope}",
        headers={"Authorization": f"Bearer {auditor}"},
    )
    assert exported.status_code == 200
    assert exported.json["data"]["event_count"] == 1
    assert client.delete(
        "/v1/semantic-media/privacy/audit-scope",
        json={"scope": scope},
        headers={"Authorization": f"Bearer {auditor}"},
    ).status_code == 403
    erased = client.delete(
        "/v1/semantic-media/privacy/audit-scope",
        json={"scope": scope},
        headers={"Authorization": f"Bearer {privacy}"},
    )
    assert erased.status_code == 200 and erased.json["data"]["deleted_event_count"] == 1
    assert client.post(
        "/v1/semantic-media/debug/events",
        headers={"Authorization": f"Bearer {auditor}"},
    ).status_code == 405
