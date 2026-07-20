from __future__ import annotations

from dataclasses import replace

from agent.repositories.semantic_relay_repository import (
    InMemorySemanticRelayRepository,
    SemanticRelayEnvelope,
)
from agent.services.semantic_relay_limits import SemanticRelayLimits


def _row(index: int, *, epoch: int = 4) -> SemanticRelayEnvelope:
    return SemanticRelayEnvelope(
        message_id=f"partition-{epoch}-{index}",
        tenant_id="tenant-chaos",
        session_id="session-chaos",
        epoch=epoch,
        sender_id="sender-chaos",
        audience_id="audience-chaos",
        traffic_class="transcript",
        payload_bytes=8,
        payload_digest=f"{epoch * 1000 + index:064x}",
        ciphertext="opaque",
        expires_at=1_000,
    )


def _read(repository: InMemorySemanticRelayRepository, cursor: int) -> list[SemanticRelayEnvelope]:
    return repository.read_after(
        tenant_id="tenant-chaos",
        session_id="session-chaos",
        audience_id="audience-chaos",
        cursor=cursor,
        limit=100,
        now=10,
    )


def test_hub_switch_partition_and_browser_reconnect_have_monotone_resume() -> None:
    # The repository is the Hub-owned shared-store port. Two Hub service
    # instances may be replaced while this state and its cursor remain durable.
    repository = InMemorySemanticRelayRepository(SemanticRelayLimits(max_batch_count=100))
    for index in range(5):
        repository.append(_row(index), now=0)
    first_connection = _read(repository, 0)[:2]
    persisted_cursor = first_connection[-1].cursor

    # Network partition: producer continues through a replacement Hub while
    # the browser retains only its last acknowledged cursor.
    for index in range(5, 10):
        repository.append(_row(index), now=1)
    resumed = _read(repository, persisted_cursor)
    assert [row.cursor for row in resumed] == list(range(3, 11))
    assert len({row.message_id for row in first_connection + resumed}) == 10
    assert (
        repository.acknowledge(
            tenant_id="tenant-chaos",
            session_id="session-chaos",
            audience_id="audience-chaos",
            cursor=10,
            now=11,
        )
        == 10
    )
    assert _read(repository, 10) == []


def test_epoch_change_never_dispatches_old_epoch_and_cleanup_is_repeatable() -> None:
    repository = InMemorySemanticRelayRepository()
    for index in range(3):
        repository.append(_row(index, epoch=4), now=0)
    for index in range(3):
        repository.append(_row(index, epoch=5), now=0)
    current_epoch = [row for row in _read(repository, 0) if row.epoch == 5]
    assert [row.epoch for row in current_epoch] == [5, 5, 5]
    assert repository.revoke(tenant_id="tenant-chaos", session_id="session-chaos") == 6
    assert repository.revoke(tenant_id="tenant-chaos", session_id="session-chaos") == 0
    assert repository.snapshot()["messages"] == 0


def test_replayed_message_id_is_idempotent_after_reconnect() -> None:
    repository = InMemorySemanticRelayRepository()
    first = repository.append(_row(1), now=0)
    replay = repository.append(replace(_row(1), created_at=999, cursor=999), now=2)
    assert replay == first
    assert repository.snapshot()["messages"] == 1
