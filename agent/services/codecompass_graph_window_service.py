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
    represented_group_count: int
    total_group_count: int


class CodeCompassGraphWindowSelector(Protocol):
    def select(
        self,
        *,
        nodes: Sequence[object],
        edges: Sequence[object],
        node_limit: int,
        edge_limit: int,
        node_groups: Sequence[Sequence[str]] = (),
    ) -> CodeCompassGraphWindow: ...


class CodeCompassGraphWindowService:
    """Select a group-balanced topology window without changing graph facts."""

    def select(
        self,
        *,
        nodes: Sequence[object],
        edges: Sequence[object],
        node_limit: int,
        edge_limit: int,
        node_groups: Sequence[Sequence[str]] = (),
    ) -> CodeCompassGraphWindow:
        canonical_nodes = self._canonical_nodes(nodes)
        node_by_id = {_node_id(node): node for node in canonical_nodes}
        canonical_edges = tuple(
            edge
            for edge in edges
            if isinstance(edge, Mapping)
            and self._edge_is_bound(edge, node_by_id)
        )
        canonical_groups = self._canonical_groups(
            node_ids=tuple(node_by_id),
            node_groups=node_groups,
        )
        ordered_ids = (
            self._stratified_topology_order(
                groups=canonical_groups,
                edges=canonical_edges,
            )
            if canonical_groups
            else self._topology_order(
                tuple(node_by_id),
                canonical_edges,
            )
        )
        selected_ids = ordered_ids[: max(1, int(node_limit))]
        selected = set(selected_ids)
        window_edges = self._ranked_topology_edges(
            edges=canonical_edges,
            ranked_node_ids=ordered_ids,
            active_node_count=len(selected_ids),
        )
        bounded_edge_limit = max(1, int(edge_limit))
        visible_edges = window_edges[:bounded_edge_limit]
        return CodeCompassGraphWindow(
            nodes=tuple(node_by_id[node_id] for node_id in selected_ids),
            edges=visible_edges,
            total_node_count=len(canonical_nodes),
            total_edge_count=len(canonical_edges),
            source_edge_count=len(edges),
            unresolved_edge_count=len(edges) - len(canonical_edges),
            internal_edge_count=len(window_edges),
            edge_capped=len(visible_edges) < len(window_edges),
            represented_group_count=sum(
                1 for group in canonical_groups if selected.intersection(group)
            ),
            total_group_count=len(canonical_groups),
        )

    @staticmethod
    def _canonical_groups(
        *,
        node_ids: tuple[str, ...],
        node_groups: Sequence[Sequence[str]],
    ) -> tuple[tuple[str, ...], ...]:
        """Normalize caller-owned partitions without interpreting them.

        Group classification is deliberately delegated to the domain catalog.
        The selector only removes unknown/duplicate memberships and keeps any
        ungrouped canonical nodes visible in a final fallback partition.
        """

        if not node_groups:
            return ()
        known = set(node_ids)
        claimed: set[str] = set()
        groups: list[tuple[str, ...]] = []
        for raw_group in node_groups:
            group_values: list[str] = []
            group_seen: set[str] = set()
            for value in raw_group:
                node_id = str(value).strip()
                if (
                    node_id in known
                    and node_id not in claimed
                    and node_id not in group_seen
                ):
                    group_values.append(node_id)
                    group_seen.add(node_id)
            group = tuple(group_values)
            if not group:
                continue
            claimed.update(group)
            groups.append(group)
        fallback = tuple(node_id for node_id in node_ids if node_id not in claimed)
        if fallback:
            groups.append(fallback)
        return tuple(groups)

    @classmethod
    def _stratified_topology_order(
        cls,
        *,
        groups: tuple[tuple[str, ...], ...],
        edges: tuple[Mapping[str, object], ...],
    ) -> tuple[str, ...]:
        """Round-robin stable per-group topology orders.

        The resulting sequence is independent of the requested window size.
        Consequently every larger window is a strict prefix extension of a
        smaller one, while each non-empty group receives one representative
        before any group receives a second node.
        """

        all_node_ids = tuple(node_id for group in groups for node_id in group)
        group_by_node = {
            node_id: group_index
            for group_index, group in enumerate(groups)
            for node_id in group
        }
        adjacency = {node_id: set() for node_id in all_node_ids}
        degree = {node_id: 0 for node_id in all_node_ids}
        for edge in edges:
            source, target = _edge_endpoints(edge)
            if source in degree:
                degree[source] += 1
            if target in degree:
                degree[target] += 1
            if (
                source != target
                and source in group_by_node
                and target in group_by_node
                and group_by_node[source] == group_by_node[target]
            ):
                adjacency[source].add(target)
                adjacency[target].add(source)

        group_orders = tuple(
            cls._topology_order_from_facts(
                node_ids=group,
                adjacency=adjacency,
                degree=degree,
            )
            for group in groups
        )
        ordered: list[str] = []
        maximum_group_size = max((len(group) for group in group_orders), default=0)
        for position in range(maximum_group_size):
            for group in group_orders:
                if position < len(group):
                    ordered.append(group[position])
        return tuple(ordered)

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
    def _topology_order(
        node_ids: tuple[str, ...],
        edges: tuple[Mapping[str, object], ...],
    ) -> tuple[str, ...]:
        adjacency = {node_id: set() for node_id in node_ids}
        degree = {node_id: 0 for node_id in node_ids}
        for edge in edges:
            source, target = _edge_endpoints(edge)
            if source in degree:
                degree[source] += 1
            if target in degree:
                degree[target] += 1
            if source != target and source in adjacency and target in adjacency:
                adjacency[source].add(target)
                adjacency[target].add(source)

        return CodeCompassGraphWindowService._topology_order_from_facts(
            node_ids=node_ids,
            adjacency=adjacency,
            degree=degree,
        )

    @staticmethod
    def _topology_order_from_facts(
        *,
        node_ids: tuple[str, ...],
        adjacency: Mapping[str, set[str]],
        degree: Mapping[str, int],
    ) -> tuple[str, ...]:
        original_position = {
            node_id: position for position, node_id in enumerate(node_ids)
        }

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
    def _ranked_topology_edges(
        *,
        edges: tuple[Mapping[str, object], ...],
        ranked_node_ids: tuple[str, ...],
        active_node_count: int,
    ) -> tuple[Mapping[str, object], ...]:
        """Order edges monotonically for every prefix of the node ranking.

        An edge becomes eligible when the later-ranked endpoint enters the
        node window.  Activation rank is therefore the primary order key: an
        edge introduced by a larger window can never displace an edge that was
        eligible in a smaller window.  Within one activation rank, a global
        incremental spanning forest is emitted before cycle/parallel edges.
        """

        node_rank = {
            node_id: rank for rank, node_id in enumerate(ranked_node_ids)
        }
        edges_by_activation: dict[int, list[Mapping[str, object]]] = {}
        for edge in edges:
            source, target = _edge_endpoints(edge)
            activation_rank = max(node_rank[source], node_rank[target])
            if activation_rank < active_node_count:
                edges_by_activation.setdefault(activation_rank, []).append(edge)

        parent = {
            node_id: node_id for node_id in ranked_node_ids[:active_node_count]
        }

        def find(node_id: str) -> str:
            root = node_id
            while parent[root] != root:
                root = parent[root]
            while parent[node_id] != node_id:
                current = parent[node_id]
                parent[node_id] = root
                node_id = current
            return root

        ordered: list[Mapping[str, object]] = []
        for activation_rank in range(active_node_count):
            backbone: list[Mapping[str, object]] = []
            remainder: list[Mapping[str, object]] = []
            for edge in edges_by_activation.get(activation_rank, ()):
                source, target = _edge_endpoints(edge)
                source_root = find(source)
                target_root = find(target)
                if source_root != target_root:
                    parent[target_root] = source_root
                    backbone.append(edge)
                else:
                    remainder.append(edge)
            ordered.extend(backbone)
            ordered.extend(remainder)
        return tuple(ordered)


codecompass_graph_window_service = CodeCompassGraphWindowService()


def get_codecompass_graph_window_service() -> CodeCompassGraphWindowService:
    return codecompass_graph_window_service
