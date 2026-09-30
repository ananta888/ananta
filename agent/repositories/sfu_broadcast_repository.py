"""Substitutable SQL and in-memory adapters for SFU broadcast projections.

This module stays the public entry point. The adapters live in focused
modules split by responsibility:

- ``sfu_broadcast_projection_rules``: validation, mutation and identity rules
- ``sfu_broadcast_projection_paging``: page filters, ordering keys, cursors
- ``sfu_broadcast_memory_repository``: in-memory adapters and shared store
- ``sfu_broadcast_sql_repository``: SQL projection adapters
- ``sfu_broadcast_sql_retention_repository``: SQL Audience snapshot retention
"""

from __future__ import annotations

from agent.repositories.sfu_broadcast_memory_repository import (
    InMemorySfuAtomicGroupProjectionRepository,
    InMemorySfuAudienceSnapshotRetentionRepository,
    InMemorySfuBroadcastAudienceRepository,
    InMemorySfuBroadcastRepositoryStore,
    InMemorySfuFanoutRouteRepository,
    InMemorySfuReceiverGroupRepository,
)
from agent.repositories.sfu_broadcast_projection_rules import SfuBroadcastRepositoryError
from agent.repositories.sfu_broadcast_sql_repository import (
    SqlSfuAtomicGroupProjectionRepository,
    SqlSfuBroadcastAudienceRepository,
    SqlSfuFanoutRouteRepository,
    SqlSfuReceiverGroupRepository,
)
from agent.repositories.sfu_broadcast_sql_retention_repository import (
    SqlSfuAudienceSnapshotRetentionRepository,
)

__all__ = [
    "InMemorySfuAudienceSnapshotRetentionRepository",
    "InMemorySfuAtomicGroupProjectionRepository",
    "InMemorySfuBroadcastAudienceRepository",
    "InMemorySfuBroadcastRepositoryStore",
    "InMemorySfuFanoutRouteRepository",
    "InMemorySfuReceiverGroupRepository",
    "SfuBroadcastRepositoryError",
    "SqlSfuBroadcastAudienceRepository",
    "SqlSfuAudienceSnapshotRetentionRepository",
    "SqlSfuAtomicGroupProjectionRepository",
    "SqlSfuFanoutRouteRepository",
    "SqlSfuReceiverGroupRepository",
]
