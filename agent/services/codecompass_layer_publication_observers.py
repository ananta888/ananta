"""What else happens after the Hub published a layer (the publisher stays unaware).

* ``KnowledgeIndexPointerObserver`` keeps one completed ``KnowledgeIndexDB``
  row per layer profile. Retrieval and the Meet scope binding find the layer
  view through it (``source_id`` = ``<profile>:chunks``).
* ``SearchIndexSyncObserver`` applies the new layer to the FTS projection
  right away, so the first query after a commit does not pay for it.

An observer failure is logged and never undoes a publication: the head is
authoritative, and both projections catch up on the next query or publish.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.codecompass_layer_record_source import POINTER_KEY, POINTER_SCHEMA, LayerRecordSource

POINTER_NAMESPACE = uuid.UUID("3f6c2c9e-5d0b-4d8e-9a57-6c0b1d2e4f10")


class LayerPublicationObserverPort(Protocol):
    def published(self, publication: Mapping[str, Any]) -> None: ...


def pointer_index_id(profile_id: str) -> str:
    return str(uuid.uuid5(POINTER_NAMESPACE, f"codecompass-layer-pointer:{profile_id}"))


def pointer_source_id(profile_id: str) -> str:
    return f"{profile_id}:chunks"


class KnowledgeIndexPointerObserver:
    def __init__(self, repository: Any, model: Any) -> None:
        self._repository = repository
        self._model = model

    def published(self, publication: Mapping[str, Any]) -> None:
        profile_id = str(publication.get("profile_id") or "")
        index_id = pointer_index_id(profile_id)
        row = self._repository.get_by_id(index_id)
        metadata = {
            "source_id": pointer_source_id(profile_id),
            "source_scope": "repo_path",
            POINTER_KEY: {"schema": POINTER_SCHEMA, "profile_id": profile_id},
            "head_generation": int(publication.get("generation") or 0),
            "effective_source_revision": str(publication.get("snapshot_revision") or ""),
            "layer_id": str(publication.get("layer_id") or ""),
        }
        if row is None:
            row = self._model(id=index_id, source_scope="repo_path", profile_name="codecompass_layers",
                              status="completed", output_dir=None, created_by="codecompass-layers")
        row.index_metadata = metadata
        row.status = "completed"
        row.updated_at = time.time()
        self._repository.save(row)


class SearchIndexSyncObserver:
    def __init__(self, *, root: Any, layers: Any, heads: Any) -> None:
        self._root = root
        self._layers = layers
        self._heads = heads

    def published(self, publication: Mapping[str, Any]) -> None:
        LayerRecordSource(root=self._root, profile_id=str(publication.get("profile_id") or ""),
                          layers=self._layers, heads=self._heads).sync()


def notify(observers: list[Any], publication: Mapping[str, Any]) -> None:
    for observer in observers:
        try:
            observer.published(publication)
        except Exception:  # noqa: BLE001 -- projections catch up; the head stays authoritative
            logging.exception("codecompass layer publication observer failed: %s", type(observer).__name__)
