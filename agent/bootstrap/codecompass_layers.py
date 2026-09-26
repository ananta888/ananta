"""Composition root for Hub-owned incremental CodeCompass layers.

Off by default. ``ANANTA_CODECOMPASS_LAYERS_ENABLED=1`` (Hub role only)
replaces the "unavailable" layer backend with the Hub store under
``<data_dir>/codecompass_layers``: heads, diffs and plans become readable.
Writes (``ANANTA_CODECOMPASS_LAYER_WRITES=1``) queue one Hub task per layer
build, reserve a ``RUN_*`` in the Hub Evidence Registry (tenant/project from
``ANANTA_CODECOMPASS_LAYER_TENANT_ID`` / ``_PROJECT_ID``) and publish the
Worker's verified layer by advancing the head.
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
TENANT_ENV = "ANANTA_CODECOMPASS_LAYER_TENANT_ID"
PROJECT_ENV = "ANANTA_CODECOMPASS_LAYER_PROJECT_ID"
ROOT_DIRNAME = "codecompass_layers"


@dataclass(frozen=True)
class CodeCompassLayerWiringStatus:
    enabled: bool
    reason: str
    root: str = ""


def _flag(name: str, environ: Mapping[str, str]) -> bool:
    return str(environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def layer_root(data_dir: str | Path) -> Path:
    return Path(data_dir) / ROOT_DIRNAME


def initialize_codecompass_layers(
    app: Flask,
    *,
    environ: Mapping[str, str] | None = None,
    data_dir: str | Path | None = None,
    task_queue: Any = None,
    evidence: Any = None,
    pointer_repository: Any = None,
) -> CodeCompassLayerWiringStatus:
    environ = os.environ if environ is None else environ
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        return CodeCompassLayerWiringStatus(False, "codecompass_layers_hub_role_required")
    if not _flag(ENABLED_ENV, environ):
        return CodeCompassLayerWiringStatus(False, "codecompass_layers_disabled")

    from agent.config import settings
    from agent.services.codecompass_layer_dispatch_adapters import (
        HubCodeCompassLayerPublisher,
        HubTaskQueueLayerDispatcher,
    )
    from agent.services.codecompass_layer_hub_store import (
        ContentBlobStore,
        FileLayerDispatchRepository,
        SnapshotManifestStore,
    )
    from agent.services.codecompass_layer_job_gateway import CodeCompassLayerJobGateway
    from agent.services.codecompass_layer_query_backend import CodeCompassLayerQueryBackend
    from agent.services.codecompass_layer_service import CodeCompassLayerDispatchBackend, CodeCompassLayerService
    from worker.incremental_index.head_registry import LayerHeadRegistry
    from worker.incremental_index.layer_store import ArtifactLayerStore

    root = layer_root(settings.data_dir if data_dir is None else data_dir)
    root.mkdir(parents=True, exist_ok=True)
    layers, heads = ArtifactLayerStore(root), LayerHeadRegistry(root)
    evidence = evidence or _Lazy(lambda: _hub_evidence(environ))
    backend = CodeCompassLayerDispatchBackend(
        query_backend=CodeCompassLayerQueryBackend(layers=layers, heads=heads, snapshots=SnapshotManifestStore(root)),
        task_queue=HubTaskQueueLayerDispatcher(queue=task_queue or _Lazy(_hub_task_queue), evidence=evidence),
        dispatch_repository=FileLayerDispatchRepository(root),
        publisher=HubCodeCompassLayerPublisher(
            layers=layers, heads=heads, evidence=evidence,
            observers=_publication_observers(root, layers, heads, pointer_repository),
        ),
        writes_enabled=lambda: _flag(WRITES_ENV, environ),
    )
    service = CodeCompassLayerService(backend=backend)
    app.extensions["codecompass_layer_service"] = service
    app.extensions["codecompass_layer_root"] = str(root)
    app.extensions["codecompass_layer_job_gateway"] = CodeCompassLayerJobGateway(
        job_lookup=service.job, contents=ContentBlobStore(root), layers=layers
    )
    return CodeCompassLayerWiringStatus(True, "codecompass_layers_enabled", str(root))


def _publication_observers(root: Path, layers: Any, heads: Any, pointer_repository: Any) -> list[Any]:
    from agent.db_models.knowledge import KnowledgeIndexDB
    from agent.services.codecompass_layer_publication_observers import (
        KnowledgeIndexPointerObserver,
        SearchIndexSyncObserver,
    )

    repository = pointer_repository or _Lazy(_knowledge_index_repository)
    return [KnowledgeIndexPointerObserver(repository, KnowledgeIndexDB),
            SearchIndexSyncObserver(root=root, layers=layers, heads=heads)]


def _knowledge_index_repository() -> Any:
    from agent.repository import knowledge_index_repo

    return knowledge_index_repo


class _Lazy:
    """Create a Hub service on first use, so a read-only start never touches it."""

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self._target: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._target is None:
            self._target = self._factory()
        return getattr(self._target, name)


def _hub_task_queue() -> Any:
    from agent.services.task_queue_service import get_task_queue_service

    return get_task_queue_service()


def _hub_evidence(environ: Mapping[str, str]) -> Any:
    from agent.services.codecompass_layer_dispatch_adapters import HubEvidenceLayerRuns
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service

    return HubEvidenceLayerRuns(
        get_hub_evidence_registry_service(),
        tenant_id=str(environ.get(TENANT_ENV) or "default"),
        project_id=str(environ.get(PROJECT_ENV) or "ananta"),
    )


__all__ = ["CodeCompassLayerWiringStatus", "initialize_codecompass_layers", "layer_root"]
