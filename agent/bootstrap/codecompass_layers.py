"""Composition root for Hub-owned incremental CodeCompass layers.

Off by default. ``ANANTA_CODECOMPASS_LAYERS_ENABLED=1`` (Hub role only)
replaces the "unavailable" layer backend with the Hub store under
``<data_dir>/codecompass_layers``: heads, diffs and plans become readable.
Writes stay closed unless ``ANANTA_CODECOMPASS_LAYER_WRITES=1`` and a Worker
dispatch queue and publisher are wired; without them the dispatch backend
refuses every write with a coded error instead of guessing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask

ENABLED_ENV = "ANANTA_CODECOMPASS_LAYERS_ENABLED"
WRITES_ENV = "ANANTA_CODECOMPASS_LAYER_WRITES"
ROOT_DIRNAME = "codecompass_layers"


@dataclass(frozen=True)
class CodeCompassLayerWiringStatus:
    enabled: bool
    reason: str
    root: str = ""


def _flag(name: str, environ: Mapping[str, str]) -> bool:
    return str(environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


class _DispatchNotWired:
    """Queue and publisher placeholder until Worker layer jobs are wired."""

    def dispatch(self, *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("codecompass_layer_worker_dispatch_required")

    def publish(self, *, dispatch: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("codecompass_layer_worker_dispatch_required")


def layer_root(data_dir: str | Path) -> Path:
    return Path(data_dir) / ROOT_DIRNAME


def initialize_codecompass_layers(
    app: Flask, *, environ: Mapping[str, str] | None = None, data_dir: str | Path | None = None
) -> CodeCompassLayerWiringStatus:
    environ = os.environ if environ is None else environ
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        return CodeCompassLayerWiringStatus(False, "codecompass_layers_hub_role_required")
    if not _flag(ENABLED_ENV, environ):
        return CodeCompassLayerWiringStatus(False, "codecompass_layers_disabled")

    from agent.config import settings
    from agent.services.codecompass_layer_hub_store import FileLayerDispatchRepository, SnapshotManifestStore
    from agent.services.codecompass_layer_query_backend import CodeCompassLayerQueryBackend
    from agent.services.codecompass_layer_service import CodeCompassLayerDispatchBackend, CodeCompassLayerService
    from worker.incremental_index.head_registry import LayerHeadRegistry
    from worker.incremental_index.layer_store import ArtifactLayerStore

    root = layer_root(settings.data_dir if data_dir is None else data_dir)
    root.mkdir(parents=True, exist_ok=True)
    query = CodeCompassLayerQueryBackend(
        layers=ArtifactLayerStore(root),
        heads=LayerHeadRegistry(root),
        snapshots=SnapshotManifestStore(root),
    )
    not_wired = _DispatchNotWired()
    backend = CodeCompassLayerDispatchBackend(
        query_backend=query,
        task_queue=not_wired,
        dispatch_repository=FileLayerDispatchRepository(root),
        publisher=not_wired,
        writes_enabled=lambda: _flag(WRITES_ENV, environ),
    )
    app.extensions["codecompass_layer_service"] = CodeCompassLayerService(backend=backend)
    app.extensions["codecompass_layer_root"] = str(root)
    return CodeCompassLayerWiringStatus(True, "codecompass_layers_enabled", str(root))


__all__ = ["CodeCompassLayerWiringStatus", "initialize_codecompass_layers", "layer_root"]
