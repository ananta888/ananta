from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.adapters.sfu_member_digest_key_contract_memory import (
    InMemoryDigestKeyCryptoAdapter,
    InMemoryDigestKeyMetadataRepository,
)
from agent.services.sfu_hub_secret_envelope import derive_sfu_hub_envelope
from agent.services.sfu_member_digest_key_contract import (
    DigestKeyLifecycleState,
    DigestKeyMetadata,
    SfuMemberDigestKeyContractService,
    SfuMemberDigestScope,
)
from agent.services.turn_pool_contract import TurnPoolObservationDocument


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _ToggleDestroyCrypto(InMemoryDigestKeyCryptoAdapter):
    fail_destroy = True

    def destroy(self, key_id: str) -> None:
        if self.fail_destroy:
            raise RuntimeError("kms unavailable")
        super().destroy(key_id)


def _staged(key_id: str, generation: int, scope: SfuMemberDigestScope) -> DigestKeyMetadata:
    return DigestKeyMetadata(
        key_id=key_id,
        algorithm="HMAC-SHA256",
        generation=generation,
        version=1,
        scope_fingerprint=scope.fingerprint(),
        state=DigestKeyLifecycleState.STAGED,
        valid_from=NOW - timedelta(minutes=1),
        valid_until=NOW + timedelta(days=1),
        state_changed_at=NOW - timedelta(minutes=1),
    )


def test_blind_index_is_stable_when_only_wrapping_key_id_rotates() -> None:
    master = "m" * 32
    first = derive_sfu_hub_envelope(master, key_id="wrap-v1")
    second = derive_sfu_hub_envelope(master, key_id="wrap-v2")

    left = first.blind_index(purpose="membership", scope="tenant:room", value="actor")
    right = second.blind_index(purpose="membership", scope="tenant:room", value="actor")

    assert left == right
    assert left in second.blind_candidates(
        purpose="membership", scope="tenant:room", value="actor"
    )


def test_turn_observation_contract_normalizes_fail_closed_capacity() -> None:
    document = TurnPoolObservationDocument.from_mapping(
        {
            "node_id": "pool-a:instance-a",
            "config_digest": "a" * 64,
            "health_status": "healthy",
            "relay_status": "ready",
            "capacity_status": "ready",
            "observation_fencing_token": 4,
            "observation_version": 9,
            "observed_at": 100.0,
            "fresh_until": 110.0,
        }
    )

    assert document.capacity_status == "accept"
    assert document.repository_mapping()["contract_version"] == 1


def test_compromised_kms_rotation_commits_pending_and_cleanup_is_idempotent() -> None:
    scope = SfuMemberDigestScope("tenant-a", "room-a", "publication-a", 3)
    repository = InMemoryDigestKeyMetadataRepository()
    crypto = _ToggleDestroyCrypto()
    current = repository.stage(_staged("key-a", 1, scope))
    crypto.provision("key-a", b"a" * 32)
    current = repository.activate(
        current.key_id, expected_version=current.version, transitioned_at=NOW
    )
    successor = repository.stage(_staged("key-b", 2, scope))
    crypto.provision("key-b", b"b" * 32)
    service = SfuMemberDigestKeyContractService(
        reader=repository, writer=repository, crypto=crypto, clock=_Clock()
    )

    previous, active = service.rotate(
        scope=scope,
        current_key_id=current.key_id,
        expected_current_version=current.version,
        successor=successor,
        overlap=timedelta(minutes=1),
        retention=timedelta(days=1),
        compromised=True,
    )

    assert previous.state is DigestKeyLifecycleState.DESTRUCTION_PENDING
    assert active.state is DigestKeyLifecycleState.ACTIVE
    crypto.fail_destroy = False
    destroyed = service.complete_pending_destruction(
        scope=scope, key_id=previous.key_id, expected_version=previous.version
    )
    assert destroyed.state is DigestKeyLifecycleState.DESTROYED
    assert service.complete_pending_destruction(
        scope=scope, key_id=destroyed.key_id, expected_version=destroyed.version
    ) == destroyed
