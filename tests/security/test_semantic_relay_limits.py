from __future__ import annotations

import json

import pytest

from agent.repositories.semantic_relay_repository import (
    InMemorySemanticRelayRepository,
    SemanticRelayEnvelope,
    SemanticRelayRepositoryError,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_relay_limits import SemanticRelayLimits
from agent.services.semantic_relay_rate_limiter import SemanticRelayPollLimiter


def _envelope(
    message_id: str,
    *,
    session: str = "session-a",
    audience: str = "peer-a",
    size: int = 4,
    traffic_class: str = "control",
):
    return SemanticRelayEnvelope(
        message_id=message_id,
        tenant_id="tenant-a",
        session_id=session,
        epoch=1,
        sender_id="sender",
        audience_id=audience,
        traffic_class=traffic_class,
        payload_bytes=size,
        payload_digest=f"{int(message_id.split('-')[-1]):064x}",
        ciphertext="opaque",
        expires_at=100.0,
    )


def test_atomic_count_byte_peer_session_and_global_limits() -> None:
    limits = SemanticRelayLimits(
        max_session_messages=2,
        max_session_bytes=8,
        max_peer_messages=2,
        max_peer_bytes=8,
        max_global_messages=3,
        max_global_bytes=12,
        max_sessions=2,
        max_peers_per_session=2,
    )
    repository = InMemorySemanticRelayRepository(limits)
    repository.append(_envelope("message-1"), now=0)
    repository.append(_envelope("message-2"), now=0)
    with pytest.raises(SemanticRelayRepositoryError, match="relay_session_message_quota"):
        repository.append(_envelope("message-3"), now=0)
    repository.append(_envelope("message-3", session="session-b"), now=0)
    with pytest.raises(SemanticRelayRepositoryError, match="relay_global_message_quota"):
        repository.append(_envelope("message-4", session="session-b", audience="peer-b"), now=0)


def test_expire_revoke_and_ack_are_repeatable_and_content_free() -> None:
    repository = InMemorySemanticRelayRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: 1_000,
        ),
        secret=b"semantic-relay-test-audit-key-32b",
    )

    def event(transition: str, reason_code: str, job_ref: str):
        return audit.prepare_transition(
            idempotency_key=f"relay-{transition}-{job_ref}",
            tenant_id="tenant-a",
            scope="semantic-media-session:session-a",
            event_type="semantic_relay",
            transition=transition,
            reason_code=reason_code,
            epoch=1,
            job_ref=job_ref,
        )

    queued = event("queued", "accepted", "message-1")
    repository.append(_envelope("message-1"), now=0, audit_event=queued)
    assert repository.expire(now=101, limit=10) == 1
    assert repository.expire(now=101, limit=10) == 0
    repository.append(_envelope("message-2"), now=0)
    revoked = event("revoked", "hub_revoked", "message-2")
    assert repository.revoke(
        tenant_id="tenant-a",
        session_id="session-a",
        audit_event=revoked,
    ) == 1
    assert repository.revoke(
        tenant_id="tenant-a",
        session_id="session-a",
        audit_event=revoked,
    ) == 0
    repository.append(_envelope("message-3"), now=0)
    acknowledged = event("acknowledged", "audience_confirmed", "peer-a:control:3")
    assert repository.acknowledge(
        tenant_id="tenant-a",
        session_id="session-a",
        audience_id="peer-a",
        cursor=3,
        traffic_class="control",
        now=1,
        audit_event=acknowledged,
    ) == 3
    assert repository.acknowledge(
        tenant_id="tenant-a",
        session_id="session-a",
        audience_id="peer-a",
        cursor=3,
        traffic_class="control",
        now=1,
        audit_event=acknowledged,
    ) == 3
    assert repository.snapshot() == {
        "messages": 0,
        "bytes": 0,
        "cursors": 1,
        "ack_cursors": 1,
        "audit_events": 3,
    }
    audit_events = repository.audit_events()
    assert [
        (item.event_type, item.transition, item.reason_code)
        for item in audit_events
    ] == [
        ("semantic_relay", "queued", "accepted"),
        ("semantic_relay", "revoked", "hub_revoked"),
        ("semantic_relay", "acknowledged", "audience_confirmed"),
    ]
    rendered_audit = json.dumps([item.public() for item in audit_events], sort_keys=True)
    assert all(
        forbidden not in rendered_audit
        for forbidden in ("tenant-a", "session-a", "peer-a", "sender", "opaque")
    )


def test_saturating_one_session_does_not_consume_another_sessions_reserved_slot() -> None:
    limits = SemanticRelayLimits(max_session_messages=2, max_global_messages=2)
    repository = InMemorySemanticRelayRepository(limits)
    repository.append(_envelope("message-1", session="bulk-session", traffic_class="evidence_bulk"), now=0)
    with pytest.raises(SemanticRelayRepositoryError, match="relay_global_message_quota"):
        repository.append(_envelope("message-2", session="bulk-session", traffic_class="evidence_bulk"), now=0)
    control = repository.append(_envelope("message-3", session="control-session"), now=0)
    assert control.cursor == 1


def test_poll_limiter_is_bounded_monotone_and_has_no_timers() -> None:
    limiter = SemanticRelayPollLimiter(max_per_minute=2, max_scopes=2)
    assert limiter.allow(tenant_id="t", session_id="s1", audience_id="a", now=0)
    assert limiter.allow(tenant_id="t", session_id="s1", audience_id="a", now=1)
    assert not limiter.allow(tenant_id="t", session_id="s1", audience_id="a", now=2)
    assert limiter.allow(tenant_id="t", session_id="s2", audience_id="a", now=2)
    assert limiter.allow(tenant_id="t", session_id="s3", audience_id="a", now=3)
    assert limiter.snapshot()["scopes"] == 2
    assert limiter.snapshot()["timers"] == 0
    assert limiter.allow(tenant_id="t", session_id="s1", audience_id="a", now=61)


def test_ack_cursor_is_isolated_per_traffic_class() -> None:
    repository = InMemorySemanticRelayRepository()
    control = repository.append(_envelope("message-1"), now=0)
    transcript = repository.append(
        _envelope("message-2", traffic_class="transcript"),
        now=0,
    )
    assert control.cursor == transcript.cursor == 1
    assert (
        repository.acknowledge(
            tenant_id="tenant-a",
            session_id="session-a",
            audience_id="peer-a",
            cursor=1,
            now=1,
            traffic_class="control",
        )
        == 1
    )
    remaining = repository.read_after(
        tenant_id="tenant-a",
        session_id="session-a",
        audience_id="peer-a",
        cursor=0,
        limit=10,
        now=1,
        traffic_class="transcript",
    )
    assert [row.message_id for row in remaining] == ["message-2"]
