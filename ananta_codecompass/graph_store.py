from __future__ import annotations

import hashlib  # noqa: F401 - re-exported through the worker star-import facade
import json
import math  # noqa: F401 - re-exported through the worker star-import facade
import os  # noqa: F401 - re-exported through the worker star-import facade
import tempfile  # noqa: F401 - re-exported through the worker star-import facade
from collections import Counter  # noqa: F401 - re-exported through the worker star-import facade
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any

from ananta_codecompass.graph_artifact_io import BoundedUtf8Writer, atomic_write_json
from ananta_codecompass.graph_compact_storage import (
    COMPACT_STORAGE_ENCODING,
    compact_storage_payload,
    hydrate_stored_edges,
)
from ananta_codecompass.graph_edge_identity import (
    derived_edge_id,
    finite_non_negative,
    stable_edge_id,
)
from ananta_codecompass.graph_indexes import (
    GraphIndexBuilders,
    build_edge_id_indexes,
    build_node_index,
    build_semantic_index,
    edges_from_index,
    hydrate_edge_index,
)
from ananta_codecompass.graph_output_record_assembler import (
    GraphOutputRecordAssembler,
    normalize_semantic_edge,
    normalize_semantic_node,
)
from ananta_codecompass.graph_traversal import (
    neighbor_steps,
    traverse_breadth_first,
    traverse_evidence_paths,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)

# Compatibility aliases for the historic private helpers of this module.
_COMPACT_STORAGE_ENCODING = COMPACT_STORAGE_ENCODING
_BoundedUtf8Writer = BoundedUtf8Writer
_finite_non_negative = finite_non_negative
_stable_edge_id = stable_edge_id


def _empty_x86_index() -> dict[str, Any]:
    return {
        "schema": "codecompass_x86_graph.v1",
        "nodes": [], "edges": [], "nodes_by_id": {},
        "node_count": 0, "edge_count": 0,
    }


def _empty_rig_index() -> dict[str, Any]:
    return {
        "schema": "codecompass_repository_intelligence.v1",
        "nodes": [], "edges": [], "nodes_by_id": {},
        "node_count": 0, "edge_count": 0,
    }


def _missing_index_payload() -> dict[str, Any]:
    return {
        "state": {},
        "nodes": [],
        "edges": [],
        "semantic_nodes": [],
        "semantic_edges": [],
        "equivalence_rules": [],
        "translation_contracts": [],
        "transform_artifacts": [],
        # X86CC-020: x86_extension slot is always present so consumers can
        # safely access it even when the index is missing.
        "x86_nodes": [],
        "x86_edges": [],
        "x86_index": _empty_x86_index(),
        # RIG-002: repository_intelligence slot is always present so consumers
        # can safely access rig_nodes / rig_edges even when the index is missing.
        "rig_nodes": [],
        "rig_edges": [],
        "rig_index": _empty_rig_index(),
        "node_index": {},
        "semantic_index": {},
        "outgoing_index": {},
        "incoming_index": {},
        "diagnostics": {
            "status": "degraded",
            "reason": "graph_index_missing",
            # RIG-002: rig slot is always present with a stable shape
            # so consumers can safely access it on a missing index.
            "repository_intelligence": {
                "schema": "codecompass_repository_intelligence.v1",
                "status": "degraded",
                "reason": "no_rig_records",
                "node_count": 0,
                "edge_count": 0,
            },
        },
    }


def _dict_items(payload: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    return [item for item in list(payload.get(key) or []) if isinstance(item, dict)]


class CodeCompassGraphStore:
    """JSON-backed CodeCompass graph store.

    The store owns loading, caching, persistence and the query surface. Record
    normalization, compact encoding, index construction and traversal are
    delegated to focused collaborators; the protected ``_build_*`` hooks stay
    overridable because the SQLite store customizes the edge index contract.
    """

    def __init__(
        self,
        *,
        index_path: str | Path,
        max_artifact_bytes: int | None = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
        visual_metrics_path: str | Path | None = None,
        record_assembler: GraphOutputRecordAssembler | None = None,
    ):
        self._index_path = Path(index_path)
        self._visual_metrics_path = (
            Path(visual_metrics_path) if visual_metrics_path is not None else None
        )
        self._record_assembler = record_assembler or GraphOutputRecordAssembler()
        self._cached_payload: dict[str, Any] | None = None
        self._visual_metrics_loaded = False
        self._cached_visual_metrics: dict[str, Any] | None = None
        self._read_lock = RLock()
        if max_artifact_bytes is None:
            self._max_artifact_bytes = None
        else:
            normalized_limit = int(max_artifact_bytes)
            if normalized_limit <= 0:
                raise ValueError("codecompass_graph_artifact_limit_invalid")
            self._max_artifact_bytes = normalized_limit

    def load(self) -> dict[str, Any]:
        if self._cached_payload is not None:
            return self._cached_payload
        with self._read_lock:
            if self._cached_payload is not None:
                return self._cached_payload
            return self._load_uncached()

    def _load_uncached(self) -> dict[str, Any]:
        if not self._index_path.exists():
            self._cached_payload = _missing_index_payload()
            return self._cached_payload
        if self._index_path.is_symlink() or not self._index_path.is_file():
            raise RuntimeError("codecompass_graph_artifact_invalid")
        if (
            self._max_artifact_bytes is not None
            and self._index_path.stat().st_size > self._max_artifact_bytes
        ):
            raise RuntimeError("codecompass_graph_artifact_too_large")
        payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        state = dict(payload.get("state") or {})
        storage_encoding = str(state.get("storage_encoding") or "")
        nodes = _dict_items(payload, "nodes")
        semantic_nodes = _dict_items(payload, "semantic_nodes")
        if storage_encoding == COMPACT_STORAGE_ENCODING:
            edges = self._hydrate_stored_edges(
                payload.get("edges"),
                state=state,
                role="graph_edges",
            )
            semantic_edges = self._hydrate_stored_edges(
                payload.get("semantic_edges"),
                state=state,
                role="semantic_edges",
            )
        else:
            edges = _dict_items(payload, "edges")
            semantic_edges = _dict_items(payload, "semantic_edges")
        equivalence_rules = _dict_items(payload, "equivalence_rules")
        edge_lookup = {
            str(item.get("edge_id") or ""): item
            for item in [*edges, *semantic_edges]
            if str(item.get("edge_id") or "")
        }
        raw_outgoing_index = payload.get("outgoing_index")
        raw_incoming_index = payload.get("incoming_index")
        if not isinstance(raw_outgoing_index, dict) or not isinstance(raw_incoming_index, dict):
            raw_outgoing_index, raw_incoming_index = self._build_edge_indexes(
                [*edges, *semantic_edges]
            )
        self._cached_payload = {
            "state": state,
            "nodes": nodes,
            "edges": edges,
            "semantic_nodes": semantic_nodes,
            "semantic_edges": semantic_edges,
            "equivalence_rules": equivalence_rules,
            "translation_contracts": _dict_items(payload, "translation_contracts"),
            "transform_artifacts": _dict_items(payload, "transform_artifacts"),
            # X86CC-020: x86 fields are optional in the on-disk payload; default to empty.
            "x86_nodes": _dict_items(payload, "x86_nodes"),
            "x86_edges": _dict_items(payload, "x86_edges"),
            "x86_index": dict(payload.get("x86_index") or _empty_x86_index()),
            # RIG-002: rig fields are optional in the on-disk payload; default to empty.
            "rig_nodes": _dict_items(payload, "rig_nodes"),
            "rig_edges": _dict_items(payload, "rig_edges"),
            "rig_index": dict(payload.get("rig_index") or _empty_rig_index()),
            "node_index": (
                dict(payload.get("node_index") or {})
                if isinstance(payload.get("node_index"), dict)
                else self._build_node_index(nodes)
            ),
            "semantic_index": (
                dict(payload.get("semantic_index") or {})
                if isinstance(payload.get("semantic_index"), dict)
                else self._build_semantic_index(
                    semantic_nodes,
                    semantic_edges,
                    equivalence_rules,
                )
            ),
            "outgoing_index": self._hydrate_edge_index(
                raw_outgoing_index, edge_lookup
            ),
            "incoming_index": self._hydrate_edge_index(
                raw_incoming_index, edge_lookup
            ),
            "diagnostics": dict(payload.get("diagnostics") or {}),
        }
        return self._cached_payload

    @property
    def visual_metrics_path(self) -> Path:
        explicit_path = getattr(self, "_visual_metrics_path", None)
        if explicit_path is not None:
            return Path(explicit_path)
        storage_path = getattr(self, "_index_path", None) or getattr(self, "_db_path", None)
        if storage_path is None:
            raise RuntimeError("graph_store_path_unavailable")
        path = Path(storage_path)
        return path.with_name(f"{path.stem}.visual_metrics.json")

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        atomic_write_json(path, payload, max_artifact_bytes=self._max_artifact_bytes)

    def save(self, payload: dict[str, Any]) -> None:
        with self._read_lock:
            self._atomic_write_json(
                self._index_path,
                self._compact_storage_payload(payload),
            )
            self._cached_payload = None

    @classmethod
    def _compact_storage_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        return compact_storage_payload(payload)

    @staticmethod
    def _derived_edge_id(
        edge: dict[str, Any],
        *,
        strategy: str = "normalized",
    ) -> str:
        return derived_edge_id(edge, strategy=strategy)

    @classmethod
    def _hydrate_stored_edges(
        cls,
        raw_edges: Any,
        *,
        state: dict[str, Any],
        role: str,
    ) -> list[dict[str, Any]]:
        return hydrate_stored_edges(raw_edges, state=state, role=role)

    def load_visual_metrics(self) -> dict[str, Any] | None:
        """Read a worker-produced sidecar without deriving missing metrics."""
        if self._visual_metrics_loaded:
            return self._cached_visual_metrics
        with self._read_lock:
            if self._visual_metrics_loaded:
                return self._cached_visual_metrics
            return self._load_visual_metrics_uncached()

    def _load_visual_metrics_uncached(self) -> dict[str, Any] | None:
        path = self.visual_metrics_path
        try:
            if path.is_symlink() or not path.is_file():
                self._visual_metrics_loaded = True
                return None
            if (
                self._max_artifact_bytes is not None
                and path.stat().st_size > self._max_artifact_bytes
            ):
                self._visual_metrics_loaded = True
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._visual_metrics_loaded = True
            return None
        self._cached_visual_metrics = (
            dict(payload) if isinstance(payload, dict) else None
        )
        self._visual_metrics_loaded = True
        return self._cached_visual_metrics

    def publish_visual_metrics(self, artifact: dict[str, Any]) -> None:
        """Atomically publish an already computed visual-metrics artifact."""
        with self._read_lock:
            self._atomic_write_json(self.visual_metrics_path, artifact)
            self._cached_visual_metrics = dict(artifact)
            self._visual_metrics_loaded = True

    def _index_builders(self) -> GraphIndexBuilders:
        return GraphIndexBuilders(
            node_index=self._build_node_index,
            semantic_index=self._build_semantic_index,
            edge_indexes=self._build_edge_indexes,
        )

    def rebuild_from_output_records(
        self,
        *,
        records: list[dict[str, Any]],
        manifest_hash: str,
        semantic_budget: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload, diagnostics = self._record_assembler.assemble(
            records=records,
            manifest_hash=manifest_hash,
            semantic_budget=semantic_budget,
            index_builders=self._index_builders(),
        )
        self.save(payload)
        # Metrics are worker-owned and published as a revision-bound sidecar.
        # Advanced algorithms remain opt-in so ordinary indexing stays bounded.
        from worker.retrieval.codecompass_graph_visual_metrics import materialize_graph_visual_metrics

        materialize_graph_visual_metrics(graph_store=self)
        return diagnostics

    @staticmethod
    def _normalize_semantic_node(record: dict[str, Any], manifest_hash: str, index: int) -> dict[str, Any]:
        return normalize_semantic_node(record, manifest_hash, index)

    @staticmethod
    def _normalize_semantic_edge(
        record: dict[str, Any],
        manifest_hash: str,
        edge_identity_occurrences: dict[str, int],
    ) -> dict[str, Any] | None:
        return normalize_semantic_edge(record, manifest_hash, edge_identity_occurrences)

    @staticmethod
    def _build_node_index(nodes: list[dict[str, Any]]) -> dict[str, Any]:
        return build_node_index(nodes)

    @staticmethod
    def _build_semantic_index(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return build_semantic_index(nodes, edges, rules)

    @staticmethod
    def _build_edge_indexes(edges: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        return build_edge_id_indexes(edges)

    @staticmethod
    def _hydrate_edge_index(
        raw_index: Any,
        edge_lookup: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        return hydrate_edge_index(raw_index, edge_lookup)

    def get_node(self, *, node_id: str) -> dict[str, Any] | None:
        payload = self.load()
        by_id = dict((payload.get("node_index") or {}).get("by_id") or {})
        node = by_id.get(str(node_id or "").strip())
        return dict(node) if isinstance(node, dict) else None

    def find_nodes_by_name(self, *, name: str) -> list[dict[str, Any]]:
        query = str(name or "").strip()
        if not query:
            return []
        payload = self.load()
        node_index = dict(payload.get("node_index") or {})
        by_id = dict(node_index.get("by_id") or {})
        by_name = dict(node_index.get("by_name") or {})
        node_ids = list(by_name.get(query) or [])
        if not node_ids:
            lowered = query.lower()
            for known_name in sorted(by_name):
                if str(known_name).lower() == lowered:
                    node_ids.extend(by_name.get(known_name) or [])
        return [dict(by_id[node_id]) for node_id in sorted(set(node_ids)) if node_id in by_id]

    def find_nodes_by_file(self, *, file: str) -> list[dict[str, Any]]:
        query = str(file or "").strip()
        if not query:
            return []
        payload = self.load()
        node_index = dict(payload.get("node_index") or {})
        by_id = dict(node_index.get("by_id") or {})
        by_file = dict(node_index.get("by_file") or {})
        node_ids = list(by_file.get(query) or [])
        if not node_ids:
            lowered = query.lower()
            for known_file in sorted(by_file):
                if lowered in str(known_file).lower():
                    node_ids.extend(by_file.get(known_file) or [])
        return [dict(by_id[node_id]) for node_id in sorted(set(node_ids)) if node_id in by_id]

    def find_semantic_nodes(
        self,
        *,
        symbol: str | None = None,
        file: str | None = None,
        language: str | None = None,
        semantic_kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        payload = self.load()
        semantic_index = dict(payload.get("semantic_index") or {})
        by_id = dict(semantic_index.get("by_id") or {})
        candidate_sets: list[set[str]] = []
        for key, value in [
            ("by_symbol", symbol),
            ("by_file", file),
            ("by_language", language),
            ("by_semantic_kind", semantic_kind),
        ]:
            query = str(value or "").strip()
            if not query:
                continue
            bucket = dict(semantic_index.get(key) or {})
            exact = set(bucket.get(query) or bucket.get(query.lower()) or [])
            if not exact:
                lowered = query.lower()
                for known, ids in bucket.items():
                    if lowered in str(known).lower():
                        exact.update(ids or [])
            candidate_sets.append(exact)
        if not candidate_sets:
            ids = set(by_id.keys())
        else:
            ids = set.intersection(*candidate_sets) if candidate_sets else set()
        return [dict(by_id[node_id]) for node_id in sorted(ids)[: max(1, int(limit))] if node_id in by_id]

    @staticmethod
    def _edges_from_index(
        index: dict[str, Any],
        node_id: str,
        allowed_edge_types: set[str] | None,
    ) -> list[dict[str, Any]]:
        return edges_from_index(index, node_id, allowed_edge_types)

    def outgoing_edges(self, *, node_id: str, allowed_edge_types: set[str] | None = None) -> list[dict[str, Any]]:
        payload = self.load()
        return self._edges_from_index(payload.get("outgoing_index") or {}, node_id, allowed_edge_types)

    def incoming_edges(self, *, node_id: str, allowed_edge_types: set[str] | None = None) -> list[dict[str, Any]]:
        payload = self.load()
        return self._edges_from_index(payload.get("incoming_index") or {}, node_id, allowed_edge_types)

    def traverse(
        self,
        *,
        seed_ids: list[str],
        max_depth: int,
        max_nodes: int,
        allowed_edge_types: set[str] | None = None,
    ) -> dict[str, Any]:
        return traverse_breadth_first(
            self.load(),
            seed_ids=seed_ids,
            max_depth=max_depth,
            max_nodes=max_nodes,
            allowed_edge_types=allowed_edge_types,
            outgoing_edges=lambda node_id, edge_types: self.outgoing_edges(
                node_id=node_id,
                allowed_edge_types=edge_types,
            ),
        )

    def _neighbor_steps(
        self,
        payload: dict[str, Any],
        node_id: str,
        direction: str,
        allowed_edge_types: set[str] | None,
    ) -> list[dict[str, Any]]:
        return neighbor_steps(payload, node_id, direction, allowed_edge_types)

    def traverse_paths(
        self,
        *,
        seed_ids: list[str],
        max_depth: int,
        max_nodes: int,
        allowed_edge_types: set[str] | None = None,
        direction: str = "outgoing",
        max_paths_per_node: int = 3,
    ) -> dict[str, Any]:
        return traverse_evidence_paths(
            self.load(),
            seed_ids=seed_ids,
            max_depth=max_depth,
            max_nodes=max_nodes,
            allowed_edge_types=allowed_edge_types,
            direction=direction,
            max_paths_per_node=max_paths_per_node,
        )
