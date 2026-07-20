from __future__ import annotations

import base64
import hashlib
import random
from dataclasses import replace

import pytest

from agent.repositories.semantic_relay_repository import (
    InMemorySemanticRelayRepository,
    SemanticRelayEnvelope,
    SemanticRelayRepositoryError,
)
from agent.services.semantic_relay_limits import SemanticRelayLimits
from ananta_contracts.webrtc_datachannel import (
    CHUNK_VERSION,
    DataChannelContractError,
    bound_chunk_id,
    validate_chunk,
)


def _chunk(payload: bytes = b"bounded") -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "version": CHUNK_VERSION,
        "chunk_id": bound_chunk_id(
            session_id="session-fuzz",
            epoch=3,
            sender_id="sender-fuzz",
            payload_digest=digest,
        ),
        "message_id": "message-fuzz",
        "session_id": "session-fuzz",
        "epoch": 3,
        "sender_id": "sender-fuzz",
        "traffic_class": "transcript",
        "index": 0,
        "total": 1,
        "chunk_bytes": len(payload),
        "total_bytes": len(payload),
        "expires_at_ms": 10_000,
        "payload_digest": digest,
        "data": base64.b64encode(payload).decode(),
    }


def _envelope(index: int, *, traffic_class: str = "evidence_bulk") -> SemanticRelayEnvelope:
    return SemanticRelayEnvelope(
        message_id=f"message-{index}",
        tenant_id="tenant-fuzz",
        session_id="session-fuzz",
        epoch=3,
        sender_id="sender-fuzz",
        audience_id="audience-fuzz",
        traffic_class=traffic_class,
        payload_bytes=1,
        payload_digest=f"{index:064x}",
        ciphertext="opaque",
        expires_at=100,
    )


def test_seeded_hostile_chunk_values_are_rejected_without_proportional_allocation() -> None:
    random_source = random.Random(87_431)
    hostile_values = [-(10**18), -1, 1.5, float("inf"), float("nan"), 10**18, "256", None, True]
    fields = ["total", "index", "chunk_bytes", "total_bytes", "epoch", "expires_at_ms"]
    rejected = 0
    for _ in range(2_000):
        candidate = _chunk()
        candidate[random_source.choice(fields)] = random_source.choice(hostile_values)
        try:
            validate_chunk(candidate)
        except DataChannelContractError:
            rejected += 1
    assert rejected == 2_000


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"data": "A"}, "invalid_base64"),
        ({"payload_digest": "0" * 64}, "chunk_context_mismatch"),
        ({"index": 1}, "chunk_index_out_of_range"),
        ({"total": 257}, "invalid_integer"),
        ({"total_bytes": 100_000, "traffic_class": "diagnostic"}, "invalid_integer"),
    ],
)
def test_truncation_gap_digest_oversize_and_context_transplant_fail_closed(mutation: dict, reason: str) -> None:
    candidate = _chunk()
    candidate.update(mutation)
    with pytest.raises(DataChannelContractError, match=reason):
        validate_chunk(candidate)


def test_reorder_duplicate_and_queue_flood_keep_cursor_and_resources_bounded() -> None:
    limits = SemanticRelayLimits(
        max_global_messages=20,
        max_global_bytes=100,
        priority_reserve_messages=2,
        priority_reserve_bytes=10,
        max_session_messages=20,
        max_session_bytes=100,
        max_peer_messages=20,
        max_peer_bytes=100,
    )
    repository = InMemorySemanticRelayRepository(limits)
    order = list(range(18))
    random.Random(17).shuffle(order)
    stored = [repository.append(_envelope(index), now=0) for index in order]
    assert [row.cursor for row in stored] == list(range(1, 19))
    for row, index in zip(stored, order, strict=True):
        assert repository.append(_envelope(index), now=1).cursor == row.cursor
    with pytest.raises(SemanticRelayRepositoryError, match="relay_global_message_quota"):
        repository.append(_envelope(99), now=1)
    control = repository.append(_envelope(100, traffic_class="control"), now=1)
    transcript = repository.append(_envelope(101, traffic_class="transcript"), now=1)
    assert (control.cursor, transcript.cursor) == (1, 1)
    assert [
        row.cursor
        for row in repository.read_after(
            tenant_id="tenant-fuzz",
            session_id="session-fuzz",
            audience_id="audience-fuzz",
            cursor=0,
            limit=20,
            now=1,
            traffic_class="evidence_bulk",
        )
    ] == list(range(1, 19))
    page = repository.read_after(
        tenant_id="tenant-fuzz",
        session_id="session-fuzz",
        audience_id="audience-fuzz",
        cursor=0,
        limit=100,
        now=2,
    )
    assert len(page) == 20
    assert {(row.traffic_class, row.cursor) for row in page} >= {
        ("control", 1),
        ("transcript", 1),
        ("evidence_bulk", 18),
    }
    assert repository.snapshot()["messages"] == 20


def test_conflicting_duplicate_and_expiry_are_idempotently_contained() -> None:
    repository = InMemorySemanticRelayRepository()
    repository.append(_envelope(1), now=0)
    conflicting = replace(_envelope(1), payload_digest="f" * 64)
    with pytest.raises(SemanticRelayRepositoryError, match="relay_message_id_conflict"):
        repository.append(conflicting, now=0)
    assert repository.expire(now=101, limit=100) == 1
    assert repository.expire(now=101, limit=100) == 0
    assert repository.snapshot()["messages"] == 0
