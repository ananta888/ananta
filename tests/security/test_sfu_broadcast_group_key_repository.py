from __future__ import annotations

import hashlib
from dataclasses import replace

from agent.repositories.sfu_broadcast_group_key_repository import (
    InMemorySfuBroadcastGroupKeyRepository,
    InMemorySfuBroadcastGroupKeyStore,
)
from agent.services.sfu_broadcast_group_key_repository_port import (
    SfuGroupKeyEpochState,
    SfuGroupKeyPackageWrite,
    SfuGroupKeyReceipt,
)
from agent.services.sfu_hub_secret_envelope import derive_sfu_hub_envelope
from agent.services.webrtc_group_key_authorization_service import GroupKeyEpochAuthorization


def _authorization() -> GroupKeyEpochAuthorization:
    return GroupKeyEpochAuthorization(
        version=1,
        authorization_id="authorization-a",
        tenant_id="tenant-a",
        room_id="room-a",
        publication_id="publication-a",
        epoch=4,
        previous_epoch=3,
        member_set_digest="a" * 64,
        member_ids=("alice", "bob"),
        key_package_refs={"alice": "package-alice", "bob": "package-bob"},
        valid_from_ms=1_000_000,
        expires_at_ms=1_120_000,
        rekey_deadline_ms=1_010_000,
        reason="refresh",
        hub_key_id="hub-key-a",
        membership_epoch=7,
        signature_b64="signature",
    )


def _receipt(operation: str, request_digest: str, result: dict) -> SfuGroupKeyReceipt:
    return SfuGroupKeyReceipt(
        tenant_id="tenant-a",
        actor_digest="actor-digest",
        operation=operation,  # type: ignore[arg-type]
        idempotency_key_digest=f"idempotency-{operation}",
        request_digest=request_digest,
        result=result,
        expires_at_ms=1_120_000,
    )


def test_delivery_ack_and_replay_survive_repository_restart_without_plaintext_state() -> None:
    envelope = derive_sfu_hub_envelope("test-master-secret-with-at-least-32-bytes", key_id="test-v1")
    store = InMemorySfuBroadcastGroupKeyStore()
    first = InMemorySfuBroadcastGroupKeyRepository(envelope, store=store)
    publisher_digest = envelope.blind(
        purpose="sfu-group-key-subject", scope="tenant-a:session-a", value="alice"
    )
    state = SfuGroupKeyEpochState(
        _authorization(), "session-a", publisher_digest, fencing_token=4
    )
    created = first.create_epoch(state, _receipt("prepare", "a" * 64, {"ok": True}), now_ms=1_000_000)
    assert created.committed

    opaque = b"client-wrapped-group-package" * 3
    package = SfuGroupKeyPackageWrite(
        recipient_id="bob",
        recipient_digest=envelope.blind(
            purpose="sfu-group-key-subject", scope="tenant-a:session-a", value="bob"
        ),
        package_ref="package-bob",
        opaque_package=opaque,
        package_digest=hashlib.sha256(opaque).hexdigest(),
        expires_at_ms=1_110_000,
    )
    delivered = first.deliver(
        tenant_id="tenant-a",
        authorization_id="authorization-a",
        expected_version=1,
        expected_fencing_token=4,
        packages=(package,),
        receipt=_receipt("deliver", "b" * 64, {"ok": True}),
        now_ms=1_001_000,
    )
    assert delivered.committed
    assert "client-wrapped" not in repr(store.packages["package-bob"])

    restarted = InMemorySfuBroadcastGroupKeyRepository(envelope, store=store)
    page = restarted.read_for_recipient(
        tenant_id="tenant-a", session_id="session-a",
        recipient_digest=package.recipient_digest, membership_epoch=7,
        cursor="", limit=2, now_ms=1_002_000,
    )
    assert page.items[0].opaque_package == opaque
    acknowledged = restarted.acknowledge(
        tenant_id="tenant-a", authorization_id="authorization-a",
        package_ref="package-bob", recipient_digest=package.recipient_digest,
        membership_epoch=7, now_ms=1_003_000,
    )
    replay = restarted.acknowledge(
        tenant_id="tenant-a", authorization_id="authorization-a",
        package_ref="package-bob", recipient_digest=package.recipient_digest,
        membership_epoch=7, now_ms=1_004_000,
    )
    assert acknowledged.committed and replay.committed and replay.replayed


def test_new_epoch_atomically_revokes_previous_delivery_rights() -> None:
    envelope = derive_sfu_hub_envelope("test-master-secret-with-at-least-32-bytes", key_id="test-v1")
    repository = InMemorySfuBroadcastGroupKeyRepository(envelope)
    publisher = envelope.blind(
        purpose="sfu-group-key-subject", scope="tenant-a:session-a", value="alice"
    )
    first = SfuGroupKeyEpochState(_authorization(), "session-a", publisher, fencing_token=4)
    assert repository.create_epoch(first, _receipt("prepare", "a" * 64, {"epoch": 4}), now_ms=1_000_000).committed
    next_authorization = _authorization()
    next_authorization = replace(
        next_authorization,
        authorization_id="authorization-b",
        epoch=5,
        previous_epoch=4,
        reason="leave",
    )
    second = SfuGroupKeyEpochState(next_authorization, "session-a", publisher, fencing_token=5)
    assert repository.create_epoch(
        second,
        SfuGroupKeyReceipt("tenant-a", "actor-digest", "prepare", "idempotency-next", "c" * 64, {"epoch": 5}, 1_120_000),
        now_ms=1_005_000,
    ).committed
    assert repository.get(tenant_id="tenant-a", authorization_id="authorization-a").status == "revoked"
