"""Scoped lexical search over the active materialized CodeCompass graph."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any


class CodeCompassScopedGraphSearchService:
    """Adapt the persisted graph artifact to the agentic graph-search port."""

    def search(
        self,
        query: str,
        *,
        limit: int,
        scope: Mapping[str, Any],
        depth: int = 1,
    ) -> list[dict[str, Any]]:
        from agent.services.codecompass_graph_store_resolution_service import (
            resolve_codecompass_graph_store,
        )

        allowed_index_ids = {
            str(item)
            for item in list(scope.get("allowed_index_ids") or [])
            if str(item).strip()
        } or None
        store, _index_id, _diagnostics = resolve_codecompass_graph_store(
            {}, allowed_index_ids=allowed_index_ids
        )
        if store is None:
            raise RuntimeError("graph_index_unavailable")
        payload = store.load()
        nodes = [
            dict(item)
            for item in list(payload.get("nodes") or [])
            if isinstance(item, Mapping)
        ]
        edges = [
            dict(item)
            for item in list(payload.get("edges") or [])
            if isinstance(item, Mapping)
        ]
        return self._rank_and_expand(
            query=query,
            nodes=nodes,
            edges=edges,
            limit=max(1, int(limit)),
            depth=max(0, min(int(depth), 4)),
        )

    @staticmethod
    def _rank_and_expand(
        *,
        query: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        limit: int,
        depth: int,
    ) -> list[dict[str, Any]]:
        tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}", query)
        }
        by_id = {str(node.get("id") or ""): node for node in nodes}
        scored: list[tuple[float, str]] = []
        for node_id, node in by_id.items():
            path = str(node.get("file") or node.get("path") or "").replace("\\", "/")
            haystack = " ".join(
                str(node.get(key) or "")
                for key in ("name", "kind", "file", "path")
            ).lower()
            overlap = sum(1 for token in tokens if token in haystack)
            if overlap and path:
                scored.append((float(overlap), node_id))
        scored.sort(key=lambda item: (-item[0], str(by_id[item[1]].get("file") or ""), item[1]))

        adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for edge in edges:
            source = str(edge.get("source_id") or edge.get("source") or "")
            target = str(edge.get("target_id") or edge.get("target") or "")
            relation = str(edge.get("edge_type") or edge.get("type") or "related")
            if source and target:
                adjacency[source].append((target, relation))
                adjacency[target].append((source, relation))

        distance: dict[str, int] = {}
        queue: deque[str] = deque()
        for _score, node_id in scored[:limit]:
            distance[node_id] = 0
            queue.append(node_id)
        while queue:
            node_id = queue.popleft()
            if distance[node_id] >= depth:
                continue
            for neighbor, _relation in adjacency.get(node_id, []):
                if neighbor in by_id and neighbor not in distance:
                    distance[neighbor] = distance[node_id] + 1
                    queue.append(neighbor)

        base_score = {node_id: score for score, node_id in scored}
        selected = sorted(
            distance,
            key=lambda node_id: (
                distance[node_id],
                -base_score.get(node_id, 0.0),
                str(by_id[node_id].get("file") or ""),
                node_id,
            ),
        )[:limit]
        rows: list[dict[str, Any]] = []
        for node_id in selected:
            node = by_id[node_id]
            path = str(node.get("file") or node.get("path") or "").replace("\\", "/")
            relations = sorted(
                {
                    relation
                    for _neighbor, relation in adjacency.get(node_id, [])
                    if relation
                }
            )
            rows.append(
                {
                    "id": node_id,
                    "path": path,
                    "content": (
                        f"{node.get('kind') or 'node'} {node.get('name') or path}; "
                        f"graph relations: {', '.join(relations) or 'none'}"
                    ),
                    "score": base_score.get(node_id, 0.0) + 1.0 / (distance[node_id] + 1),
                    "kind": str(node.get("kind") or "graph_node"),
                    "source": "codecompass_graph",
                }
            )
        return rows


_SERVICE = CodeCompassScopedGraphSearchService()


def get_codecompass_scoped_graph_search_service() -> CodeCompassScopedGraphSearchService:
    return _SERVICE
