"""Hub-scoped CodeCompass graph store resolution for tool implementations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

GraphStoreResolver = Callable[..., Any]
"""Callable ``(arguments, *, allowed_index_ids=None)`` returning a store result."""


def resolve_graph_store(
    arguments: dict[str, Any],
    *,
    allowed_index_ids: set[str] | None = None,
):
    """Open a consumable graph store within a Hub-derived index scope."""
    store, index_id, _diagnostics = resolve_graph_store_diagnostics(
        arguments,
        allowed_index_ids=allowed_index_ids,
    )
    return store, index_id


def resolve_graph_store_diagnostics(
    arguments: dict[str, Any],
    *,
    allowed_index_ids: set[str] | None = None,
):
    """Compatibility facade for the service-layer graph resolver."""
    from agent.services.codecompass_graph_store_resolution_service import (
        resolve_codecompass_graph_store,
    )

    return resolve_codecompass_graph_store(
        arguments, allowed_index_ids=allowed_index_ids
    )
