from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from agent.adapters.sfu_member_digest_key_contract_memory import (
    InMemoryDigestCryptoState,
    InMemoryDigestKeyCryptoAdapter,
    InMemoryDigestKeyMetadataRepository,
    InMemoryDigestMetadataState,
)
from agent.services.sfu_member_digest_key_contract import (
    DigestKeyContractPolicy,
    DigestKeyLifecycleState,
    DigestKeyMetadata,
    SfuMemberDigestContractError,
    SfuMemberDigestKeyContractService,
    SfuMemberDigestReason,
    SfuMemberDigestScope,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def _scope(publication_id: str = "publication-1") -> SfuMemberDigestScope:
    return SfuMemberDigestScope(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id=publication_id,
        key_epoch=7,
    )


def _staged(
    key_id: str,
    generation: int,
    scope: SfuMemberDigestScope,
) -> DigestKeyMetadata:
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


def _service(
    repository: InMemoryDigestKeyMetadataRepository,
    crypto: InMemoryDigestKeyCryptoAdapter,
) -> SfuMemberDigestKeyContractService:
    return SfuMemberDigestKeyContractService(
        reader=repository,
        writer=repository,
        crypto=crypto,
        clock=FixedClock(NOW),
    )


def test_shared_repository_produces_identical_digest_across_hub_instances() -> None:
    metadata_state = InMemoryDigestMetadataState()
    crypto_state = InMemoryDigestCryptoState()
    first_repository = InMemoryDigestKeyMetadataRepository(metadata_state)
    second_repository = InMemoryDigestKeyMetadataRepository(metadata_state)
    first_crypto = InMemoryDigestKeyCryptoAdapter(crypto_state)
    second_crypto = InMemoryDigestKeyCryptoAdapter(crypto_state)
    scope = _scope()
    staged = first_repository.stage(_staged("digest-key-1", 1, scope))
    first_crypto.provision(staged.key_id, b"a" * 32)
    active = first_repository.activate(
        staged.key_id,
        expected_version=staged.version,
        transitioned_at=NOW,
    )

    first = _service(first_repository, first_crypto).create_digest(b"member-set", scope)
    second_service = _service(second_repository, second_crypto)
    second = second_service.create_digest(b"member-set", scope)

    assert first == second
    assert first.key_version == active.version
    assert second_service.verify_digest(b"member-set", scope, first).valid is True
    assert second_service.verify_digest(b"other", scope, first).valid is False
    assert "secret" not in repr(active).lower()
    assert repr(crypto_state) == "InMemoryDigestCryptoState()"


def test_scope_domain_separates_publications_and_key_epochs() -> None:
    assert _scope("publication-1").fingerprint() != _scope("publication-2").fingerprint()
    assert _scope().fingerprint() != SfuMemberDigestScope(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        key_epoch=8,
    ).fingerprint()


def test_rotation_is_cas_versioned_and_compromise_skips_dual_read() -> None:
    repository = InMemoryDigestKeyMetadataRepository()
    crypto = InMemoryDigestKeyCryptoAdapter()
    scope = _scope()
    old_staged = repository.stage(_staged("digest-key-old", 1, scope))
    crypto.provision(old_staged.key_id, b"o" * 32)
    current = repository.activate(
        old_staged.key_id,
        expected_version=old_staged.version,
        transitioned_at=NOW,
    )
    successor = repository.stage(_staged("digest-key-new", 2, scope))
    crypto.provision(successor.key_id, b"n" * 32)
    service = _service(repository, crypto)
    old_digest = service.create_digest(b"member-set", scope)

    previous, active = service.rotate(
        scope=scope,
        current_key_id=current.key_id,
        expected_current_version=current.version,
        successor=successor,
        overlap=timedelta(hours=12),
        retention=timedelta(days=7),
        compromised=True,
    )

    assert previous.state is DigestKeyLifecycleState.DESTROYED
    assert previous.dual_read_until is None
    assert active.state is DigestKeyLifecycleState.ACTIVE
    assert service.verify_digest(b"member-set", scope, old_digest).valid is False
    with pytest.raises(SfuMemberDigestContractError) as stale_write:
        repository.destroy(
            current.key_id,
            expected_version=current.version,
            transitioned_at=NOW,
            retain_until=NOW + timedelta(days=1),
        )
    assert stale_write.value.reason_code is SfuMemberDigestReason.KEY_VERSION_CONFLICT


def test_rotation_accepts_only_immediately_previous_version_during_dual_read() -> None:
    repository = InMemoryDigestKeyMetadataRepository()
    crypto = InMemoryDigestKeyCryptoAdapter()
    scope = _scope()
    old_staged = repository.stage(_staged("digest-key-old", 1, scope))
    crypto.provision(old_staged.key_id, b"o" * 32)
    current = repository.activate(
        old_staged.key_id,
        expected_version=old_staged.version,
        transitioned_at=NOW,
    )
    service = _service(repository, crypto)
    old_digest = service.create_digest(b"member-set", scope)
    successor = repository.stage(_staged("digest-key-new", 2, scope))
    crypto.provision(successor.key_id, b"n" * 32)

    previous, _ = service.rotate(
        scope=scope,
        current_key_id=current.key_id,
        expected_current_version=current.version,
        successor=successor,
        overlap=timedelta(hours=12),
        retention=timedelta(days=7),
    )

    assert previous.state is DigestKeyLifecycleState.DUAL_READ
    assert previous.version == old_digest.key_version + 1
    assert service.verify_digest(b"member-set", scope, old_digest).valid is True


def test_rotation_policy_rejects_unbounded_retention() -> None:
    repository = InMemoryDigestKeyMetadataRepository()
    crypto = InMemoryDigestKeyCryptoAdapter()
    scope = _scope()
    old_staged = repository.stage(_staged("digest-key-old", 1, scope))
    crypto.provision(old_staged.key_id, b"o" * 32)
    current = repository.activate(
        old_staged.key_id,
        expected_version=old_staged.version,
        transitioned_at=NOW,
    )
    successor = repository.stage(_staged("digest-key-new", 2, scope))
    crypto.provision(successor.key_id, b"n" * 32)

    with pytest.raises(SfuMemberDigestContractError) as captured:
        _service(repository, crypto).rotate(
            scope=scope,
            current_key_id=current.key_id,
            expected_current_version=current.version,
            successor=successor,
            overlap=timedelta(hours=1),
            retention=timedelta(days=31),
        )

    assert captured.value.reason_code is SfuMemberDigestReason.RETENTION_EXCEEDED


def test_policy_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        DigestKeyContractPolicy(maximum_payload_bytes=0)
