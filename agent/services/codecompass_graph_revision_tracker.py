"""Content-addressed graph revision identities with a bounded identity cache."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from threading import RLock

from agent.services.codecompass_graph_read_models import (
    GraphPayloadRevision,
    GraphRevisionIdentity,
)


class CodeCompassGraphRevisionTracker:
    """Derive content and evidence revisions once per loaded graph payload."""

    def __init__(self, *, maximum_cached_revisions: int) -> None:
        self._maximum_cached_revisions = int(maximum_cached_revisions)
        self._revision_cache: OrderedDict[int, GraphPayloadRevision] = OrderedDict()
        self._revision_lock = RLock()

    def clear(self) -> None:
        with self._revision_lock:
            self._revision_cache.clear()

    def identity(
        self,
        *,
        raw: Mapping[str, object],
        nodes: Sequence[Mapping[str, object]],
        edges: Sequence[Mapping[str, object]],
    ) -> GraphRevisionIdentity:
        cache_key = id(raw)
        with self._revision_lock:
            cached = self._revision_cache.pop(cache_key, None)
            if cached is not None and cached.payload is raw:
                self._revision_cache[cache_key] = cached
                return cached.identity
            content_revision = self.compute_content_revision(
                nodes=nodes,
                edges=edges,
            )
            state = raw.get("state")
            explicit = str(state.get("manifest_hash") or "").strip() if isinstance(state, Mapping) else ""
            identity = GraphRevisionIdentity(
                content_revision=content_revision,
                evidence_revision=explicit or content_revision,
            )
            self._revision_cache[cache_key] = GraphPayloadRevision(
                payload=raw,
                identity=identity,
            )
            while len(self._revision_cache) > self._maximum_cached_revisions:
                self._revision_cache.popitem(last=False)
            return identity

    def compute_content_revision(
        self,
        *,
        nodes: Sequence[Mapping[str, object]],
        edges: Sequence[Mapping[str, object]],
    ) -> str:
        digest = hashlib.sha256()
        encoder = json.JSONEncoder(
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        payload = {
            "schema": "codecompass_graph_content_revision.v1",
            "nodes": nodes,
            "edges": edges,
        }
        for chunk in encoder.iterencode(payload):
            digest.update(chunk.encode("utf-8"))
        return f"sha256:{digest.hexdigest()}"
