"""Composition root helpers for local runtime capability projections."""

from __future__ import annotations

import os
from pathlib import Path

from agent.services.local_runtime_capability_cache import LocalRuntimeCapabilityCache
from agent.services.local_runtime_capability_inventory_adapter import LocalRuntimeCapabilityInventoryAdapter
from agent.services.local_runtime_capability_projection import LocalRuntimeCapabilityProjection


def local_runtime_capability_cache() -> LocalRuntimeCapabilityCache:
    path = Path(os.environ.get("ANANTA_LOCAL_RUNTIME_CAPABILITY_CACHE", "data/local-runtime-capabilities.json"))
    return LocalRuntimeCapabilityCache(path)


def local_runtime_capability_projection() -> LocalRuntimeCapabilityProjection:
    return LocalRuntimeCapabilityProjection(local_runtime_capability_cache())


def local_runtime_capability_inventory_adapter() -> LocalRuntimeCapabilityInventoryAdapter:
    return LocalRuntimeCapabilityInventoryAdapter(local_runtime_capability_cache())


__all__ = [
    "local_runtime_capability_cache",
    "local_runtime_capability_inventory_adapter",
    "local_runtime_capability_projection",
]
