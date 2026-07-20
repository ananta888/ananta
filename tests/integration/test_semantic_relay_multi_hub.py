from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from agent.db_models.semantic_relay import SemanticRelayCursorDB, SemanticRelayEnvelopeDB
from agent.repositories.semantic_relay_repository import SemanticRelayEnvelope
from agent.repositories.semantic_relay_shared_store import SharedSemanticRelayRepository
from agent.services.semantic_relay_limits import SemanticRelayLimits


def _envelope(index: int) -> SemanticRelayEnvelope:
    return SemanticRelayEnvelope(
        message_id=f"multi-hub-{index}",
        tenant_id="tenant-multi",
        session_id="session-multi",
        epoch=1,
        sender_id="sender-a",
        audience_id="receiver-b",
        traffic_class="control",
        payload_bytes=8,
        payload_digest=f"{index:064x}",
        ciphertext="opaque",
        expires_at=10_000,
    )


def test_two_hub_repositories_keep_atomic_monotone_restart_stable_cursor(db_session) -> None:
    # Explicit cleanup keeps this integration test independent of prior rows.
    db_session.query(SemanticRelayEnvelopeDB).delete()
    db_session.query(SemanticRelayCursorDB).delete()
    db_session.commit()
    limits = SemanticRelayLimits(max_global_messages=100, max_session_messages=100, max_peer_messages=100)
    hub_a = SharedSemanticRelayRepository(limits)
    hub_b = SharedSemanticRelayRepository(limits)
    with ThreadPoolExecutor(max_workers=8) as executor:
        rows = list(executor.map(lambda item: (hub_a if item % 2 else hub_b).append(_envelope(item), now=1), range(40)))
    assert sorted(row.cursor for row in rows) == list(range(1, 41))
    after_restart = SharedSemanticRelayRepository(limits)
    delivered = after_restart.read_after(
        tenant_id="tenant-multi",
        session_id="session-multi",
        audience_id="receiver-b",
        cursor=0,
        limit=100,
        now=2,
    )
    assert [row.cursor for row in delivered] == list(range(1, 41))
    assert (
        after_restart.acknowledge(
            tenant_id="tenant-multi",
            session_id="session-multi",
            audience_id="receiver-b",
            cursor=40,
            now=3,
        )
        == 40
    )
    assert (
        hub_a.read_after(
            tenant_id="tenant-multi",
            session_id="session-multi",
            audience_id="receiver-b",
            cursor=0,
            limit=100,
            now=4,
        )
        == []
    )
