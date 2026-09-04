from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_workspace_store import (
    CollaborationStoreConflict,
    CollaborationWorkspaceStore,
)


def test_durable_admission_quota_is_persistent_tenant_and_actor_scoped(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    store = CollaborationWorkspaceStore(database)
    first = store.consume_quota(
        "tenant-a",
        "workspace-a",
        "actor-a",
        category="durable_event",
        now=100.0,
        window_seconds=60,
        maximum=2,
    )
    second_store = CollaborationWorkspaceStore(database)
    second = second_store.consume_quota(
        "tenant-a",
        "workspace-a",
        "actor-a",
        category="durable_event",
        now=101.0,
        window_seconds=60,
        maximum=2,
    )
    assert (first["count"], second["count"]) == (1, 2)
    with pytest.raises(CollaborationStoreConflict, match="admission_rate_limited"):
        store.consume_quota(
            "tenant-a",
            "workspace-a",
            "actor-a",
            category="durable_event",
            now=102.0,
            window_seconds=60,
            maximum=2,
        )
    isolated = store.consume_quota(
        "tenant-b",
        "workspace-a",
        "actor-a",
        category="durable_event",
        now=102.0,
        window_seconds=60,
        maximum=2,
    )
    assert isolated["count"] == 1
