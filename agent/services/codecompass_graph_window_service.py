"""Deterministic, topology-preserving windows for large CodeCompass graphs."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


def _node_id(node: Mapping[str, object]) -> str:
    return str(node.get("id") or node.get("node_id") or "").strip()


def _edge_endpoints(edge: Mapping[str, object]) -> tuple[str, str]:
    source = str(
        edge.get("source_id")
        or edge.get("source")
        or edge.get("from")
        or ""
    ).strip()
    target = str(
        edge.get("target_id")
        or edge.get("target")
        or edge.get("to")
        or ""
    ).strip()
    return source, target


@dataclass(frozen=True)
class CodeCompassGraphWindow:
    nodes: tuple[Mapping[str, object], ...]
    edges: tuple[Mapping[str, object], ...]
    total_node_count: int
    total_edge_count: int
    source_edge_count: int
    unresolved_edge_count: int
    internal_edge_count: int
    edge_capped: bool


class CodeCompassGraphWindowSelector(Protocol):
    def select(
        self,
        *,
        nodes: Sequence[object],
        edges: Sequence[object],
        node_limit: int,
        edge_limit: int,
    ) -> CodeCompassGraphWindow: ...


class CodeCompassGraphWindowService:
    """Select a connected visualization window without changing graph facts."""

    def select(
        self,
        *,
        nodes: Sequence[object],
        edges: Sequence[object],
        node_limit: int,
        edge_limit: int,
    ) -> CodeCompassGraphWindow:
        canonical_nodes = self._canonical_nodes(nodes)
        node_by_id = {_node_id(node): node for node in canonical_nodes}
        canonical_edges = tuple(
            edge
            for edge in edges
            if isinstance(edge, Mapping)
            and self._edge_is_bound(edge, node_by_id)
        )
        ordered_ids = self._topology_order(
            tuple(node_by_id),
            canonical_edges,
        )
        selected_ids = ordered_ids[: max(1, int(node_limit))]
        selected = set(selected_ids)
        window_edges = tuple(
            edge
            for edge in canonical_edges
            if self._edge_is_internal(edge, selected)
        )
        bounded_edge_limit = max(1, int(edge_limit))
        visible_edges = (
            window_edges
            if len(window_edges) <= bounded_edge_limit
            else self._topology_bounded_edges(
                edges=window_edges,
                selected_ids=selected_ids,
                edge_limit=bounded_edge_limit,
            )
        )
        return CodeCompassGraphWindow(
            nodes=tuple(node_by_id[node_id] for node_id in selected_ids),
            edges=visible_edges,
            total_node_count=len(canonical_nodes),
            total_edge_count=len(canonical_edges),
            source_edge_count=len(edges),
            unresolved_edge_count=len(edges) - len(canonical_edges),
            internal_edge_count=len(window_edges),
            edge_capped=len(visible_edges) < len(window_edges),
        )

    @staticmethod
    def _canonical_nodes(
        nodes: Sequence[object],
    ) -> tuple[Mapping[str, object], ...]:
        result: list[Mapping[str, object]] = []
        seen: set[str] = set()
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            identifier = _node_id(node)
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            result.append(node)
        return tuple(result)

    @staticmethod
    def _edge_is_bound(
        edge: Mapping[str, object],
        node_by_id: Mapping[str, Mapping[str, object]],
    ) -> bool:
        source, target = _edge_endpoints(edge)
        return bool(source and target and source in node_by_id and target in node_by_id)

    @staticmethod
    def _edge_is_internal(
        edge: Mapping[str, object],
        selected: set[str],
    ) -> bool:
        source, target = _edge_endpoints(edge)
        return source in selected and target in selected

    @staticmethod
    def _topology_order(
        node_ids: tuple[str, ...],
        edges: tuple[Mapping[str, object], ...],
    ) -> tuple[str, ...]:
        original_position = {
            node_id: position for position, node_id in enumerate(node_ids)
        }
        adjacency = {node_id: set() for node_id in node_ids}
        degree = {node_id: 0 for node_id in node_ids}
        for edge in edges:
            source, target = _edge_endpoints(edge)
            degree[source] += 1
            degree[target] += 1
            if source != target:
                adjacency[source].add(target)
                adjacency[target].add(source)

        def priority(node_id: str) -> tuple[int, int, str]:
            return (
                -degree[node_id],
                original_position[node_id],
                node_id,
            )

        remaining = set(node_ids)
        ordered: list[str] = []
        for root in sorted(node_ids, key=priority):
            if root not in remaining:
                continue
            remaining.remove(root)
            frontier = deque([root])
            while frontier:
                current = frontier.popleft()
                ordered.append(current)
                neighbours = sorted(
                    adjacency[current].intersection(remaining),
                    key=priority,
                )
                for neighbour in neighbours:
                    remaining.remove(neighbour)
                    frontier.append(neighbour)
        return tuple(ordered)

    @staticmethod
    def _topology_bounded_edges(
        *,
        edges: tuple[Mapping[str, object], ...],
        selected_ids: tuple[str, ...],
        edge_limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        """Prefer a spanning forest before optional parallel/cycle edges."""

        parent = {node_id: node_id for node_id in selected_ids}

        def find(node_id: str) -> str:
            root = node_id
            while parent[root] != root:
                root = parent[root]
            while parent[node_id] != node_id:
                current = parent[node_id]
                parent[node_id] = root
                node_id = current
            return root

        backbone: list[Mapping[str, object]] = []
        remainder: list[Mapping[str, object]] = []
        for edge in edges:
            source, target = _edge_endpoints(edge)
            source_root = find(source)
            target_root = find(target)
            if source_root != target_root:
                parent[target_root] = source_root
                backbone.append(edge)
            else:
                remainder.append(edge)
        return tuple([*backbone, *remainder][:edge_limit])


codecompass_graph_window_service = CodeCompassGraphWindowService()


def get_codecompass_graph_window_service() -> CodeCompassGraphWindowService:
    return codecompass_graph_window_service
