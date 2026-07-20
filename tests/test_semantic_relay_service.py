from __future__ import annotations

import base64
import hashlib
import json

import pytest

from agent.repositories.semantic_relay_repository import InMemorySemanticRelayRepository
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_relay_authorization import RelayMember, SemanticRelayAuthorization
from agent.services.semantic_relay_limits import SemanticRelayLimits
from agent.services.semantic_relay_service import SemanticRelayService, SemanticRelayServiceError
from ananta_contracts.webrtc_datachannel import CONTRACT_VERSION


class Memberships:
    def __init__(self) -> None:
        permissions = frozenset({"semantic_control"})
        self.rows = {
            member_id: RelayMember(
                tenant_id="tenant-a",
                session_id="session-a",
                member_id=member_id,
                epoch=1,
                active=True,
                permissions=permissions,
                send_audiences=frozenset({"peer-b"}) if member_id == "peer-a" else frozenset({"peer-a"}),
            )
            for member_id in ("peer-a", "peer-b")
        }

    def member(self, *, tenant_id: str, session_id: str, member_id: str):
        row = self.rows.get(member_id)
        return row if row and row.tenant_id == tenant_id and row.session_id == session_id else None


class Replay:
    @staticmethod
    def decide(**_values):
        return True, "accepted"


class TrafficPolicy:
    @staticmethod
    def enabled(_traffic_class):
        return True


class KeyConfirmation:
    @staticmethod
    def confirmed(**_values):
        return True


def _raw(message_id: str, payload: bytes = b"cipher") -> bytes:
    return json.dumps(
        {
            "version": CONTRACT_VERSION,
            "traffic_class": "control",
            "message_id": message_id,
            "session_id": "session-a",
            "epoch": 1,
            "sender_id": "peer-a",
            "audience_id": "peer-b",
            "sequence": 1,
            "expires_at_ms": 1_200_000,
            "compression": "none",
            "security": {"algorithm": "AES-GCM-256", "key_id": "key-a"},
            "payload_bytes": len(payload),
            "payload_digest": hashlib.sha256(payload).hexdigest(),
            "ciphertext": base64.b64encode(payload).decode(),
        },
        separators=(",", ":"),
    ).encode()


def _service(limits: SemanticRelayLimits | None = None, *, audit=None):
    effective = limits or SemanticRelayLimits()
    repository = InMemorySemanticRelayRepository(effective)
    service = SemanticRelayService(
        repository=repository,
        authorization=SemanticRelayAuthorization(Memberships()),
        limits=effective,
        replay=Replay(),
        traffic_policy=TrafficPolicy(),
        key_confirmation=KeyConfirmation(),
        audit=audit,
        clock=lambda: 1_000.0,
    )
    return service, repository


def test_append_read_ack_is_idempotent_and_cursor_monotone() -> None:
    service, repository = _service()
    first = service.append_raw(tenant_id="tenant-a", authenticated_sender_id="peer-a", raw=_raw("message-1"))
    replay = service.append_raw(tenant_id="tenant-a", authenticated_sender_id="peer-a", raw=_raw("message-1"))
    assert first["cursor"] == replay["cursor"] == 1
    page = service.read_after(
        tenant_id="tenant-a",
        audience_id="peer-b",
        session_id="session-a",
        epoch=1,
        traffic_class="control",
        cursor=0,
    )
    assert [row["message_id"] for row in page["messages"]] == ["message-1"]
    assert (
        service.acknowledge(
            tenant_id="tenant-a",
            audience_id="peer-b",
            session_id="session-a",
            epoch=1,
            traffic_class="control",
            cursor=1,
        )
        == 1
    )
    assert repository.snapshot()["messages"] == 0
    assert (
        service.acknowledge(
            tenant_id="tenant-a",
            audience_id="peer-b",
            session_id="session-a",
            epoch=1,
            traffic_class="control",
            cursor=0,
        )
        == 1
    )


def test_authoritative_relay_transitions_are_content_free_and_exactly_once() -> None:
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: 1_000_000),
        secret=b"semantic-relay-audit-test-key" * 2,
    )
    service, repository = _service(audit=audit)
    service.append_raw(tenant_id="tenant-a", authenticated_sender_id="peer-a", raw=_raw("audit-message"))
    service.append_raw(tenant_id="tenant-a", authenticated_sender_id="peer-a", raw=_raw("audit-message"))
    service.acknowledge(
        tenant_id="tenant-a",
        audience_id="peer-b",
        session_id="session-a",
        epoch=1,
        traffic_class="control",
        cursor=1,
    )
    service.acknowledge(
        tenant_id="tenant-a",
        audience_id="peer-b",
        session_id="session-a",
        epoch=1,
        traffic_class="control",
        cursor=0,
    )
    rows = repository.audit_events()
    assert [(row.transition, row.reason_code) for row in rows] == [
        ("queued", "accepted"),
        ("acknowledged", "audience_confirmed"),
    ]
    assert "cipher" not in repr([row.public() for row in rows])


def test_sender_spoof_and_request_oversize_fail_before_persistence() -> None:
    service, repository = _service()
    with pytest.raises(SemanticRelayServiceError, match="relay_sender_mismatch"):
        service.append_raw(tenant_id="tenant-a", authenticated_sender_id="peer-b", raw=_raw("m"))
    limits = SemanticRelayLimits(max_request_bytes=100)
    service, repository = _service(limits)
    with pytest.raises(SemanticRelayServiceError, match="relay_request_too_large"):
        service.append_raw(tenant_id="tenant-a", authenticated_sender_id="peer-a", raw=b"{" * 101)
    assert repository.snapshot()["messages"] == 0


def test_poll_rate_limit_is_explicit_and_does_not_change_cursor() -> None:
    limits = SemanticRelayLimits(max_poll_per_minute=1)
    service, repository = _service(limits)
    service.append_raw(tenant_id="tenant-a", authenticated_sender_id="peer-a", raw=_raw("message-1"))
    arguments = {
        "tenant_id": "tenant-a",
        "audience_id": "peer-b",
        "session_id": "session-a",
        "epoch": 1,
        "traffic_class": "control",
        "cursor": 0,
    }
    assert service.read_after(**arguments)["cursor"] == 1
    with pytest.raises(SemanticRelayServiceError, match="relay_poll_rate_limited") as error:
        service.read_after(**arguments)
    assert error.value.status_code == 429
    assert repository.snapshot()["messages"] == 1
