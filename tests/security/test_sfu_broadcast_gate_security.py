from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from agent.services.sfu_member_digest_key_contract import (
    DigestKeyLifecycleState,
    DigestKeyMetadata,
    SfuMemberDigestKeyContractService,
    SfuMemberDigestReason,
    SfuMemberDigestScope,
)
from scripts.sfu_broadcast_gate_common import scan_content_free_document

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


class _Clock:
    def now(self):
        return NOW


class _Reader:
    def __init__(self, record):
        self.record = record

    def get(self, key_id):
        return self.record if self.record.key_id == key_id else None

    def list_for_scope(self, scope_fingerprint):
        return (self.record,) if self.record.scope_fingerprint == scope_fingerprint else ()


class _Writer:
    def rotate(self, request):
        raise AssertionError("security verification does not rotate")


class _Crypto:
    def mac_sha256(self, key_id, message):
        return hmac.new(b"s" * 32, message, hashlib.sha256).digest()

    def destroy(self, key_id):
        return None


def _service(state=DigestKeyLifecycleState.ACTIVE):
    scope = SfuMemberDigestScope("tenant-a", "room-a", "publication-a", 4)
    record = DigestKeyMetadata(
        "key-a", "HMAC-SHA256", 1, 1, scope.fingerprint(), state,
        NOW - timedelta(seconds=1), NOW + timedelta(hours=1), NOW,
    )
    reader = _Reader(record)
    return SfuMemberDigestKeyContractService(
        reader=reader, writer=_Writer(), crypto=_Crypto(), clock=_Clock()
    ), scope


def test_cross_tenant_room_publication_and_epoch_digest_replay_fails_closed() -> None:
    service, scope = _service()
    digest = service.create_digest(b"member-set", scope)
    wrong_scopes = (
        SfuMemberDigestScope("tenant-b", "room-a", "publication-a", 4),
        SfuMemberDigestScope("tenant-a", "room-b", "publication-a", 4),
        SfuMemberDigestScope("tenant-a", "room-a", "publication-b", 4),
        SfuMemberDigestScope("tenant-a", "room-a", "publication-a", 3),
    )
    for wrong_scope in wrong_scopes:
        result = service.verify_digest(b"member-set", wrong_scope, digest)
        assert result.valid is False
        assert result.reason_code is SfuMemberDigestReason.KEY_SCOPE_MISMATCH


def test_destroyed_or_compromised_key_cannot_verify_prior_authority() -> None:
    active_service, scope = _service()
    digest = active_service.create_digest(b"member-set", scope)
    destroyed_service, _ = _service(DigestKeyLifecycleState.DESTROYED)
    result = destroyed_service.verify_digest(b"member-set", scope, digest)
    assert result.valid is False
    assert result.reason_code is SfuMemberDigestReason.KEY_STATE_INVALID


def test_gate_report_scanner_detects_secret_pii_and_unverified_identifiers() -> None:
    assert "gate_pii_pattern_detected" in scan_content_free_document(
        {"diagnostic": "person@example.test"}
    )
    assert "gate_credential_pattern_detected" in scan_content_free_document(
        {"diagnostic": "Bearer opaque-test-value"}
    )
    assert "gate_private_network_identifier_detected" in scan_content_free_document(
        {"diagnostic": "192.168.10.20"}
    )
    assert "gate_unverified_source_run_identifier_detected" in scan_content_free_document(
        {"diagnostic": "RUN_NOT_VERIFIED"}
    )

