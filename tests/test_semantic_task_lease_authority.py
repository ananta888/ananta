from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.semantic_task_lease_authority import (
    HubSemanticTaskLeaseAuthority,
    SemanticTaskLeaseAuthorityError,
)


def _lease():
    return SimpleNamespace(
        id="lease-a",
        contract_id="contract-a",
        contract_digest="a" * 64,
        session_id="session-a",
        epoch=1,
        task_type="visual_extract",
        role="primary",
        executor_id="worker-a",
        audience="viewer-a",
        sequence_start=0,
        sequence_end=9,
        fencing_token=7,
        resource_budget={
            "cpu_ms": 100,
            "memory_bytes": 1_048_576,
            "artifact_bytes": 1_024,
        },
        issued_at=1_000.0,
        expires_at=1_010.0,
        deadline_at=1_015.0,
    )


def test_hub_signed_task_lease_verifies_without_exposing_secret_material() -> None:
    secret = b"lease-authority-test-secret-material" * 2
    authority = HubSemanticTaskLeaseAuthority(secret, clock_ms=lambda: 1_001_000)
    lease = _lease()
    signed = authority.issue(lease, room_id="room-a")

    verified = authority.verify(
        signed,
        lease=lease,
        expected_executor_id="worker-a",
        expected_audience="viewer-a",
    )
    assert verified["room_id"] == "room-a"
    assert verified["signature"]["algorithm"] == "hmac-sha256"
    assert secret.decode() not in repr(signed)
    assert not ({"secret", "private_key", "signing_key"} & set(signed))


def test_task_lease_tamper_stale_wrong_audience_and_executor_fail_closed() -> None:
    now_ms = [1_001_000]
    authority = HubSemanticTaskLeaseAuthority(
        b"lease-authority-test-secret-material" * 2,
        clock_ms=lambda: now_ms[0],
    )
    lease = _lease()
    signed = authority.issue(lease)

    tampered = {**signed, "sequence_end": 8}
    with pytest.raises(SemanticTaskLeaseAuthorityError, match="signature_invalid"):
        authority.verify(
            tampered,
            lease=lease,
            expected_executor_id="worker-a",
            expected_audience="viewer-a",
        )
    with pytest.raises(SemanticTaskLeaseAuthorityError, match="wrong_audience"):
        authority.verify(
            signed,
            lease=lease,
            expected_executor_id="worker-a",
            expected_audience="viewer-b",
        )
    with pytest.raises(SemanticTaskLeaseAuthorityError, match="binding_mismatch"):
        authority.verify(
            signed,
            lease=lease,
            expected_executor_id="worker-b",
            expected_audience="viewer-a",
        )
    now_ms[0] = 1_010_000
    with pytest.raises(SemanticTaskLeaseAuthorityError, match="lease_expired"):
        authority.verify(
            signed,
            lease=lease,
            expected_executor_id="worker-a",
            expected_audience="viewer-a",
        )
