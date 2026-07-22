from __future__ import annotations

from agent.repositories.sfu_broadcast_group_key_repository import (
    InMemorySfuBroadcastGroupKeyRepository,
    InMemorySfuBroadcastGroupKeyStore,
)
from agent.services.sfu_hub_secret_envelope import derive_sfu_hub_envelope


def test_two_hubs_share_no_process_local_authoritative_group_key_state() -> None:
    envelope = derive_sfu_hub_envelope("test-master-secret-with-at-least-32-bytes", key_id="test-v1")
    store = InMemorySfuBroadcastGroupKeyStore()
    first = InMemorySfuBroadcastGroupKeyRepository(envelope, store=store)
    restarted = InMemorySfuBroadcastGroupKeyRepository(envelope, store=store)
    assert first._store is restarted._store
