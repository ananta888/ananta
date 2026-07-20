from __future__ import annotations

import hashlib

import pytest
from flask import Flask

from agent.routes.semantic_media_debug import semantic_media_debug_bp
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditError,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_debug_read_model import (
    SemanticMediaDebugPrincipal,
    SemanticMediaDebugReadModel,
)
from agent.services.user_session_tokens import issue_user_access_token

NOW = 2_000_000


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _record(service: SemanticMediaAuditService, key: str = "idempotency-test"):
    return service.record_transition(
        idempotency_key=key,
        tenant_digest=_digest("tenant"),
        scope_digest=_digest("scope"),
        event_type="semantic_contract",
        transition="activated",
        reason_code="hub_confirmed",
        epoch=7,
        contract_ref=_digest("contract"),
        retention_ms=3_600_000,
    )


def test_authoritative_transition_is_exactly_once_content_free_and_paginated() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    first, created = _record(service)
    replay, replay_created = _record(service)
    assert created and not replay_created and replay == first
    assert "idempotency" not in first.public()
    assert not any(field in str(first.public()).casefold() for field in ("transcript", "audio", "secret"))

    second, _ = _record(service, "idempotency-second")
    read = SemanticMediaDebugReadModel(repository, clock_ms=lambda: NOW)
    principal = SemanticMediaDebugPrincipal(
        _digest("tenant"), _digest("subject"), frozenset({"semantic_media_auditor"})
    )
    page = read.page(principal, scope_digest=_digest("scope"), limit=1)
    assert page["read_only"] is True and len(page["items"]) == 1 and page["next_cursor"] == first.event_id
    last = read.page(principal, scope_digest=_digest("scope"), cursor=first.event_id, limit=1)
    assert last["items"][0]["event_id"] == second.event_id and last["next_cursor"] is None


def test_debug_is_role_scoped_and_retention_cleanup_is_bounded() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    _record(service)
    read = SemanticMediaDebugReadModel(repository, clock_ms=lambda: NOW)
    denied = SemanticMediaDebugPrincipal(_digest("tenant"), _digest("subject"), frozenset({"viewer"}))
    with pytest.raises(SemanticMediaAuditError, match="semantic_debug_forbidden"):
        read.page(denied, scope_digest=_digest("scope"))
    expired = SemanticMediaAuditService(repository, clock_ms=lambda: NOW + 3_600_001)
    assert expired.delete_expired(limit=1) == 1


def test_idempotency_conflict_and_content_fields_fail_closed() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    _record(service)
    with pytest.raises(SemanticMediaAuditError, match="audit_idempotency_conflict"):
        service.record_transition(
            idempotency_key="idempotency-test",
            tenant_digest=_digest("tenant"),
            scope_digest=_digest("scope"),
            event_type="semantic_contract",
            transition="revoked",
            reason_code="hub_confirmed",
            epoch=7,
            contract_ref=_digest("contract"),
            retention_ms=3_600_000,
        )
    with pytest.raises(SemanticMediaAuditError, match="audit_transition_invalid"):
        service.record_transition(
            idempotency_key="another-key",
            tenant_digest=_digest("tenant"),
            scope_digest=_digest("scope"),
            event_type="transcript_content",
            transition="activated",
            reason_code="hub_confirmed",
            epoch=7,
            contract_ref=_digest("contract"),
            retention_ms=3_600_000,
        )


def test_debug_http_api_digests_logical_scope_enforces_role_and_has_no_mutation() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    recorder = SemanticMediaAuditRecorder(service, secret=b"semantic-debug-http-test-secret" * 2)
    recorder.record_transition(
        idempotency_key="semantic-debug-http-event",
        tenant_id="debug-auditor",
        scope="semantic-media-session:session-debug",
        event_type="semantic_relay",
        transition="queued",
        reason_code="accepted",
        epoch=2,
        job_ref="relay-message-debug",
        retention_ms=3_600_000,
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(semantic_media_debug_bp)
    app.extensions["semantic_media_audit_recorder"] = recorder
    app.extensions["semantic_media_debug_read_model"] = SemanticMediaDebugReadModel(
        repository,
        clock_ms=lambda: NOW,
    )
    client = app.test_client()
    auditor = issue_user_access_token(username="debug-auditor", role="semantic_media_auditor")
    response = client.get(
        "/v1/semantic-media/debug/events?scope=semantic-media-session:session-debug&limit=1",
        headers={"Authorization": f"Bearer {auditor}"},
    )
    assert response.status_code == 200
    assert response.json["data"]["read_only"] is True
    assert response.json["data"]["items"][0]["event_type"] == "semantic_relay"
    assert "debug-auditor" not in repr(response.json)
    assert client.post(
        "/v1/semantic-media/debug/events?scope=semantic-media-session:session-debug",
        headers={"Authorization": f"Bearer {auditor}"},
    ).status_code == 405

    viewer = issue_user_access_token(username="debug-auditor", role="viewer")
    denied = client.get(
        "/v1/semantic-media/debug/events?scope=semantic-media-session:session-debug",
        headers={"Authorization": f"Bearer {viewer}"},
    )
    assert denied.status_code == 403


def test_audit_scope_cardinality_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr("agent.services.semantic_media_audit_service.MAX_SCOPE_EVENTS", 1)
    repository = InMemorySemanticMediaAuditRepository()
    service = SemanticMediaAuditService(repository, clock_ms=lambda: NOW)
    _record(service, "cardinality-first")
    with pytest.raises(SemanticMediaAuditError, match="audit_scope_cardinality_exceeded") as error:
        _record(service, "cardinality-second")
    assert error.value.status_code == 429


def test_recorder_bounds_long_domain_idempotency_without_losing_replay() -> None:
    repository = InMemorySemanticMediaAuditRepository()
    recorder = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(repository, clock_ms=lambda: NOW),
        secret=b"semantic-audit-long-command-key" * 2,
    )
    values = {
        "idempotency_key": "domain-command:" + "x" * 1000,
        "tenant_id": "tenant-long-command",
        "scope": "semantic-media-session:session-long-command",
        "event_type": "semantic_job",
        "transition": "queued",
        "reason_code": "hub_confirmed",
        "epoch": 1,
        "job_ref": "job-long-command",
    }
    first, created = recorder.record_transition(**values)
    replay, replay_created = recorder.record_transition(**values)
    assert created and not replay_created and replay == first
