from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest

from agent.adapters.sfu_member_digest_secret_store import (
    SfuMemberDigestSecretStoreAdapter,
)
from agent.services.sfu_member_digest_key_provider import (
    DigestKeyState,
    SfuMemberDigest,
    SfuMemberDigestKeyPolicy,
    SfuMemberDigestKeyProvider,
    SfuMemberDigestKeyRecord,
    SfuMemberDigestPolicyError,
    SfuMemberDigestUnavailable,
)


@dataclass
class FixedClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


def _provider(
    store: SfuMemberDigestSecretStoreAdapter,
    clock: FixedClock,
    policy: SfuMemberDigestKeyPolicy | None = None,
) -> SfuMemberDigestKeyProvider:
    return SfuMemberDigestKeyProvider(
        secret_store=store,
        clock=clock,
        policy=policy or SfuMemberDigestKeyPolicy(),
    )


def _stage_active(
    store: SfuMemberDigestSecretStoreAdapter,
    now: datetime,
    *,
    key_id: str = "member-digest-7",
    generation: int = 7,
    scope: str = "tenant-a/room-9/member-id",
    secret: bytes = bytes(range(32)),
) -> None:
    store.stage_generation(
        key_id=key_id,
        generation=generation,
        algorithm="HMAC-SHA256",
        scope=scope,
        secret=secret,
    )
    store.activate(
        key_id=key_id,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
    )


def _reference_digest(
    *, secret: bytes, key_id: str, generation: int, scope: str, member: str
) -> str:
    def frame(value: bytes) -> bytes:
        return len(value).to_bytes(4, "big") + value

    domain = b"ananta:sfu-member-digest:v1"
    salt = hashlib.sha256(
        domain
        + frame(b"HMAC-SHA256")
        + frame(key_id.encode("ascii"))
        + frame(str(generation).encode("ascii"))
    ).digest()
    pseudorandom_key = hmac.new(salt, secret, hashlib.sha256).digest()
    info = domain + frame(scope.encode("ascii"))
    derived = hmac.new(
        pseudorandom_key, info + b"\x01", hashlib.sha256
    ).digest()
    message = domain + frame(scope.encode("ascii")) + frame(member.encode("utf-8"))
    result = hmac.new(derived, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(result).rstrip(b"=").decode("ascii")


def test_digest_is_deterministic_scoped_and_carries_algorithm_and_key_id() -> None:
    now = datetime(2032, 4, 5, 12, tzinfo=timezone.utc)
    scope = "tenant-a/room-9/member-id"
    secret = bytes(range(32))
    store = SfuMemberDigestSecretStoreAdapter()
    _stage_active(store, now, scope=scope, secret=secret)
    provider = _provider(store, FixedClock(now))

    first = provider.create_digest(member_identifier="member-42", scope=scope)
    second = provider.create_digest(member_identifier="member-42", scope=scope)

    assert first == second
    assert first == SfuMemberDigest(
        algorithm="HMAC-SHA256",
        key_id="member-digest-7",
        scope=scope,
        digest=_reference_digest(
            secret=secret,
            key_id="member-digest-7",
            generation=7,
            scope=scope,
            member="member-42",
        ),
    )
    assert provider.verify_digest(
        member_identifier="member-42", expected_scope=scope, candidate=first
    )
    assert secret.hex() not in repr(first)


def test_hkdf_separates_scope_key_id_and_generation() -> None:
    now = datetime(2032, 4, 5, 12, tzinfo=timezone.utc)
    secret = b"d" * 32
    store_a = SfuMemberDigestSecretStoreAdapter()
    store_b = SfuMemberDigestSecretStoreAdapter()
    _stage_active(store_a, now, key_id="key-a", generation=1, scope="t/r/a", secret=secret)
    _stage_active(store_b, now, key_id="key-b", generation=2, scope="t/r/b", secret=secret)

    digest_a = _provider(store_a, FixedClock(now)).create_digest(
        member_identifier="same-member", scope="t/r/a"
    )
    digest_b = _provider(store_b, FixedClock(now)).create_digest(
        member_identifier="same-member", scope="t/r/b"
    )

    assert digest_a.digest != digest_b.digest
    assert not _provider(store_a, FixedClock(now)).verify_digest(
        member_identifier="same-member", expected_scope="t/r/b", candidate=digest_a
    )


def test_generation_and_expired_active_windows_fail_closed() -> None:
    now = datetime(2032, 4, 5, 12, tzinfo=timezone.utc)
    clock = FixedClock(now)
    store = SfuMemberDigestSecretStoreAdapter()
    store.stage_generation(
        key_id="key-1",
        generation=1,
        algorithm="HMAC-SHA256",
        scope="t/r/member",
        secret=b"g" * 32,
    )
    provider = _provider(store, clock)

    with pytest.raises(SfuMemberDigestUnavailable):
        provider.create_digest(member_identifier="m", scope="t/r/member")

    store.activate(
        key_id="key-1",
        valid_from=now,
        valid_until=now + timedelta(minutes=5),
    )
    candidate = provider.create_digest(member_identifier="m", scope="t/r/member")
    clock.current = now + timedelta(minutes=5)

    with pytest.raises(SfuMemberDigestUnavailable):
        provider.create_digest(member_identifier="m", scope="t/r/member")
    assert not provider.verify_digest(
        member_identifier="m", expected_scope="t/r/member", candidate=candidate
    )


def test_rotation_is_active_then_bounded_dual_read_then_retired_and_destroyed() -> None:
    now = datetime(2032, 4, 5, 12, tzinfo=timezone.utc)
    scope = "tenant/room/member"
    clock = FixedClock(now)
    store = SfuMemberDigestSecretStoreAdapter()
    _stage_active(store, now, key_id="old", generation=1, scope=scope, secret=b"o" * 32)
    provider = _provider(store, clock)
    old_digest = provider.create_digest(member_identifier="member", scope=scope)

    store.begin_dual_read(
        key_id="old",
        valid_from=now,
        valid_until=now + timedelta(minutes=10),
    )
    with pytest.raises(SfuMemberDigestUnavailable):
        provider.create_digest(member_identifier="member", scope=scope)
    assert provider.verify_digest(
        member_identifier="member", expected_scope=scope, candidate=old_digest
    )

    _stage_active(store, now, key_id="new", generation=2, scope=scope, secret=b"n" * 32)
    new_digest = provider.create_digest(member_identifier="member", scope=scope)
    assert new_digest.key_id == "new"
    assert provider.verify_digest(
        member_identifier="member", expected_scope=scope, candidate=old_digest
    )

    store.retire(key_id="old")
    assert not provider.verify_digest(
        member_identifier="member", expected_scope=scope, candidate=old_digest
    )
    store.destroy(key_id="old")
    tombstone = store.get("old")
    assert tombstone is not None
    assert tombstone.state is DigestKeyState.DESTROYED
    assert tombstone.secret is None
    assert not provider.verify_digest(
        member_identifier="member", expected_scope=scope, candidate=old_digest
    )


def test_tampering_unknown_keys_and_metadata_mismatch_deny() -> None:
    now = datetime(2032, 4, 5, 12, tzinfo=timezone.utc)
    scope = "tenant/room/member"
    store = SfuMemberDigestSecretStoreAdapter()
    _stage_active(store, now, scope=scope)
    provider = _provider(store, FixedClock(now))
    candidate = provider.create_digest(member_identifier="member", scope=scope)

    variants = (
        replace(candidate, digest=("A" if candidate.digest[0] != "A" else "B") + candidate.digest[1:]),
        replace(candidate, algorithm="HMAC-SHA1"),
        replace(candidate, key_id="unknown"),
        replace(candidate, scope="tenant/other/member"),
        replace(candidate, digest=candidate.digest + "="),
    )
    for variant in variants:
        assert not provider.verify_digest(
            member_identifier="member", expected_scope=scope, candidate=variant
        )
    assert not provider.verify_digest(
        member_identifier="other-member", expected_scope=scope, candidate=candidate
    )


def test_ambiguous_active_keys_unsupported_algorithm_and_weak_secret_fail_closed() -> None:
    now = datetime(2032, 4, 5, 12, tzinfo=timezone.utc)
    scope = "tenant/room/member"
    store = SfuMemberDigestSecretStoreAdapter()
    _stage_active(store, now, key_id="a", generation=1, scope=scope, secret=b"a" * 32)
    _stage_active(store, now, key_id="b", generation=2, scope=scope, secret=b"b" * 32)
    provider = _provider(store, FixedClock(now))

    with pytest.raises(SfuMemberDigestUnavailable):
        provider.create_digest(member_identifier="member", scope=scope)

    corrupt_stores = (
        SfuMemberDigestSecretStoreAdapter(
            (
                SfuMemberDigestKeyRecord(
                    key_id="bad-algorithm",
                    generation=1,
                    algorithm="HMAC-SHA1",
                    scope=scope,
                    state=DigestKeyState.ACTIVE,
                    secret=b"x" * 32,
                    valid_from=now,
                    valid_until=now + timedelta(minutes=1),
                ),
            )
        ),
        SfuMemberDigestSecretStoreAdapter(
            (
                SfuMemberDigestKeyRecord(
                    key_id="weak",
                    generation=1,
                    algorithm="HMAC-SHA256",
                    scope=scope,
                    state=DigestKeyState.ACTIVE,
                    secret=b"weak",
                    valid_from=now,
                    valid_until=now + timedelta(minutes=1),
                ),
            )
        ),
    )
    for corrupt_store in corrupt_stores:
        with pytest.raises(SfuMemberDigestUnavailable):
            _provider(corrupt_store, FixedClock(now)).create_digest(
                member_identifier="member", scope=scope
            )


def test_policy_rejects_downgrades_unbounded_windows_and_media_sized_input() -> None:
    with pytest.raises(SfuMemberDigestPolicyError):
        SfuMemberDigestKeyPolicy(algorithm="HMAC-SHA1")
    with pytest.raises(SfuMemberDigestPolicyError):
        SfuMemberDigestKeyPolicy(kdf="HKDF-SHA1")

    now = datetime(2032, 4, 5, 12, tzinfo=timezone.utc)
    scope = "tenant/room/member"
    store = SfuMemberDigestSecretStoreAdapter()
    store.stage_generation(
        key_id="too-long",
        generation=1,
        algorithm="HMAC-SHA256",
        scope=scope,
        secret=b"x" * 32,
    )
    store.activate(
        key_id="too-long",
        valid_from=now,
        valid_until=now + timedelta(days=8),
    )
    provider = _provider(store, FixedClock(now))
    with pytest.raises(SfuMemberDigestUnavailable):
        provider.create_digest(member_identifier="member", scope=scope)

    bounded_store = SfuMemberDigestSecretStoreAdapter()
    _stage_active(bounded_store, now, scope=scope)
    bounded_provider = _provider(bounded_store, FixedClock(now))
    with pytest.raises(SfuMemberDigestUnavailable):
        bounded_provider.create_digest(member_identifier="x" * 1025, scope=scope)
