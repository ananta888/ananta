"""Request-local overlay of complete semantic domain evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from agent.services.codecompass_domain_supplement import (
    CodeCompassDomainSupplementRecords,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)


class _GraphStorePort(Protocol):
    def load(self) -> Mapping[str, object]: ...

    def load_visual_metrics(self) -> Mapping[str, object] | None: ...


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _logical_record(record: Mapping[str, object]) -> dict[str, object]:
    return {str(key): value for key, value in record.items() if str(key) != CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD}


class CodeCompassDomainSupplementOverlayStore:
    """Expose one immutable base payload with selected supplement records.

    The wrapper is request-local. It neither mutates the cached graph store nor
    broadens Worker authority. Duplicate semantic rows are collapsed while a
    conflicting node identity fails closed.
    """

    def __init__(
        self,
        *,
        base_store: _GraphStorePort,
        base_payload: Mapping[str, object],
        supplement: CodeCompassDomainSupplementRecords,
        evidence_graph_revision: str,
        scope_source: str | None = None,
        scope_path: str | None = None,
    ) -> None:
        self._base_store = base_store
        self._payload = self._merge(
            base_payload=base_payload,
            supplement=supplement,
            evidence_graph_revision=evidence_graph_revision,
            scope_source=scope_source,
            scope_path=scope_path,
        )

    def load(self) -> Mapping[str, object]:
        return self._payload

    def load_visual_metrics(self) -> Mapping[str, object] | None:
        return self._base_store.load_visual_metrics()

    @classmethod
    def _merge(
        cls,
        *,
        base_payload: Mapping[str, object],
        supplement: CodeCompassDomainSupplementRecords,
        evidence_graph_revision: str,
        scope_source: str | None,
        scope_path: str | None,
    ) -> dict[str, object]:
        payload = dict(base_payload)
        semantic_nodes = cls._records(payload.get("semantic_nodes"))
        scoped_nodes = tuple(
            cls._assign_scope(
                node,
                scope_source=scope_source,
                scope_path=scope_path,
            )
            for node in supplement.nodes
        )
        payload["semantic_nodes"] = cls._merge_nodes(
            semantic_nodes,
            scoped_nodes,
            assigned_scope_source=(scope_source if scope_source in {"domain_id", "domain_path"} else None),
        )
        payload["semantic_edges"] = cls._merge_edges(
            cls._records(payload.get("semantic_edges")),
            supplement.semantic_edges,
        )
        payload["edges"] = cls._merge_edges(
            cls._records(payload.get("edges")),
            supplement.declaration_edges,
        )
        raw_state = payload.get("state")
        state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
        state["manifest_hash"] = evidence_graph_revision
        payload["state"] = state
        return payload

    @staticmethod
    def _assign_scope(
        node: Mapping[str, object],
        *,
        scope_source: str | None,
        scope_path: str | None,
    ) -> Mapping[str, object]:
        if scope_source not in {"domain_id", "domain_path"} or not scope_path:
            return node
        scoped = dict(node)
        scoped[scope_source] = scope_path
        return scoped

    @staticmethod
    def _records(value: object) -> tuple[Mapping[str, object], ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()
        return tuple(item for item in value if isinstance(item, Mapping))

    @staticmethod
    def _node_id(node: Mapping[str, object]) -> str:
        return str(node.get("id") or node.get("node_id") or "").strip()

    @classmethod
    def _merge_nodes(
        cls,
        base: Sequence[Mapping[str, object]],
        supplement: Sequence[Mapping[str, object]],
        assigned_scope_source: str | None,
    ) -> list[Mapping[str, object]]:
        merged = list(base)
        positions = {cls._node_id(node): position for position, node in enumerate(base) if cls._node_id(node)}
        identities = {
            identifier: _canonical(
                cls._comparable_node(
                    merged[position],
                    assigned_scope_source=assigned_scope_source,
                )
            )
            for identifier, position in positions.items()
        }
        for node in supplement:
            identifier = cls._node_id(node)
            if not identifier:
                raise ValueError("domain_supplement_overlay_node_invalid")
            logical = _canonical(
                cls._comparable_node(
                    node,
                    assigned_scope_source=assigned_scope_source,
                )
            )
            existing = identities.get(identifier)
            if existing is not None:
                if existing != logical:
                    raise ValueError("domain_supplement_overlay_node_conflict")
                if assigned_scope_source is not None:
                    position = positions[identifier]
                    scoped_existing = dict(merged[position])
                    scoped_existing[assigned_scope_source] = node[assigned_scope_source]
                    merged[position] = scoped_existing
                continue
            identities[identifier] = logical
            positions[identifier] = len(merged)
            merged.append(node)
        return merged

    @staticmethod
    def _comparable_node(
        node: Mapping[str, object],
        *,
        assigned_scope_source: str | None,
    ) -> dict[str, object]:
        comparable = _logical_record(node)
        if assigned_scope_source is not None:
            comparable.pop(assigned_scope_source, None)
        return comparable

    @staticmethod
    def _merge_edges(
        base: Sequence[Mapping[str, object]],
        supplement: Sequence[Mapping[str, object]],
    ) -> list[Mapping[str, object]]:
        merged = list(base)
        identities = {_canonical(_logical_record(edge)) for edge in base}
        for edge in supplement:
            logical = _canonical(_logical_record(edge))
            if logical in identities:
                continue
            identities.add(logical)
            merged.append(edge)
        return merged


__all__ = [
    "CodeCompassDomainSupplementOverlayStore",
]
