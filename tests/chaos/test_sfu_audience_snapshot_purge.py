from __future__ import annotations

import pytest

from agent.repositories.sfu_broadcast_repository import (
    InMemorySfuAudienceSnapshotRetentionRepository,
    InMemorySfuBroadcastRepositoryStore,
    SfuBroadcastRepositoryError,
)
from agent.services.sfu_broadcast_repository_ports import SfuAudienceRetentionFence


def test_stale_hub_fence_cannot_resume_purge_after_failover() -> None:
    repository = InMemorySfuAudienceSnapshotRetentionRepository(
        store=InMemorySfuBroadcastRepositoryStore()
    )
    repository.purge_due(
        fence=SfuAudienceRetentionFence("hub-new", 2, 2_000.0),
        now=1_000.0, page_size=10,
    )
    with pytest.raises(SfuBroadcastRepositoryError, match="audience_retention_fence_stale"):
        repository.purge_due(
            fence=SfuAudienceRetentionFence("hub-old", 1, 2_000.0),
            now=1_001.0, page_size=10,
        )
