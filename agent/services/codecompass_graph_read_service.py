"""Bounded, project-scoped read orchestration for CodeCompass graph artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol

from agent.services.codecompass_graph_domain_catalog_service import (
    CodeCompassGraphDomainCatalogPort,
    CodeCompassGraphDomainIndex,
    get_codecompass_graph_domain_catalog_service,
)
from agent.services.codecompass_graph_projection_service import (
    CodeCompassPreparedEdgePopulation,
    get_codecompass_graph_projection_service,
)
from agent.services.codecompass_graph_window_service import (
    CodeCompassGraphWindowSelector,
    get_codecompass_graph_window_service,
)

_MAX_NODE_PAGE_SIZE = 500
_MAX_EDGE_PAGE_SIZE = 2_000
_UNPREPARED_EDGE_POPULATION = object()


class CodeCompassGraphReadError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class CodeCompassGraphReadStorePort(Protocol):
    def load(self) -> Mapping[str, object]: ...

    def load_visual_metrics(self) -> Mapping[str, object] | None: ...


class CodeCompassGraphProjectionPort(Protocol):
    def project(self, **kwargs: Any) -> dict[str, Any]: ...


class CodeCompassGraphReadPort(Protocol):
    def read(
        self,
        *,
        index_id: str,
        store: CodeCompassGraphReadStorePort,
        parameters: Mapping[str, object],
        artifact_status: Mapping[str, object],
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class _RelationFacet:
    raw_type: str
    edge_count: int
    bound_edge_count: int
    unresolved_edge_count: int

    def to_wire(self) -> dict[str, object]:
        return {
            "raw_type": self.raw_type,
            "edge_count": self.edge_count,
            "bound_edge_count": self.bound_edge_count,
            "unresolved_edge_count": self.unresolved_edge_count,
        }


@dataclass
class _GraphDerivedSnapshot:
    domain_index: CodeCompassGraphDomainIndex
    nodes: tuple[Mapping[str, object], ...]
    edges: tuple[Mapping[str, object], ...]
    node_ids: frozenset[str]
    bound_edges: tuple[Mapping[str, object], ...]
    unresolved_edges: tuple[Mapping[str, object], ...]
    edge_indices_by_endpoint: Mapping[str, tuple[int, ...]]
    relation_facets: tuple[_RelationFacet, ...]
    prepared_edge_population: object = field(
        default=_UNPREPARED_EDGE_POPULATION,
        repr=False,
    )
    prepared_edge_population_lock: RLock = field(
        default_factory=RLock,
        repr=False,
    )


@dataclass(frozen=True)
class _GraphRevisionIdentity:
    content_revision: str
    evidence_revision: str


@dataclass(frozen=True)
class _GraphPayloadRevision:
    payload: Mapping[str, object]
    identity: _GraphRevisionIdentity


@dataclass(frozen=True)
class _InventoryCursor:
    facet: str | None
    offset: int


class CodeCompassGraphReadService:
    """Create bounded graph read models without owning index authorization."""

    def __init__(
        self,
        *,
        projection: CodeCompassGraphProjectionPort,
        window: CodeCompassGraphWindowSelector,
        domains: CodeCompassGraphDomainCatalogPort,
        maximum_cached_revisions: int = 2,
    ) -> None:
        if maximum_cached_revisions < 1:
            raise ValueError("graph_read_cache_size_invalid")
        self._projection = projection
        self._window = window
        self._domains = domains
        self._maximum_cached_revisions = int(maximum_cached_revisions)
        self._snapshot_cache: OrderedDict[
            tuple[str, str],
            _GraphDerivedSnapshot,
        ] = OrderedDict()
        self._snapshot_lock = RLock()
        self._revision_cache: OrderedDict[int, _GraphPayloadRevision] = OrderedDict()
        self._revision_lock = RLock()

    def read(
        self,
        *,
        index_id: str,
        store: CodeCompassGraphReadStorePort,
        parameters: Mapping[str, object],
        artifact_status: Mapping[str, object],
    ) -> Mapping[str, object]:
        values = parameters
        limit = min(max(int(values.get("limit", 100)), 1), _MAX_NODE_PAGE_SIZE)
        edge_limit = min(
            max(int(values.get("max_edges") or limit * 4), 1),
            _MAX_EDGE_PAGE_SIZE,
        )
        requested_view = str(values.get("view") or "default").strip().lower()
        view = requested_view if requested_view in {"inventory", "staged", "topology"} else "default"
        if view == "topology" and values.get("cursor"):
            raise CodeCompassGraphReadError("graph_topology_cursor_unsupported")

        raw = store.load()
        diagnostics = self._diagnostics(raw)
        semantic_translation = self._semantic_translation(diagnostics)
        semantic_budget = self._semantic_budget(semantic_translation)
        base_nodes = tuple(self._mappings(raw.get("nodes")))
        base_edges = tuple(self._mappings(raw.get("edges")))
        semantic_nodes = tuple(self._mappings(raw.get("semantic_nodes")))
        semantic_edges = tuple(self._mappings(raw.get("semantic_edges")))
        all_nodes = base_nodes + semantic_nodes
        all_edges = base_edges + semantic_edges
        revision_identity = self._graph_revision_identity(
            raw=raw,
            nodes=all_nodes,
            edges=all_edges,
        )
        graph_revision = revision_identity.content_revision
        warnings = self._graph_warnings(
            semantic_budget=semantic_budget,
            semantic_translation=semantic_translation,
        )

        inventory_cursor: _InventoryCursor | None = None
        staged_offset: int | None = None
        stage = str(values.get("stage") or "nodes").strip().lower()
        if view == "inventory":
            inventory_cursor = self._decode_inventory_cursor(
                values.get("cursor"),
                graph_revision=graph_revision,
                index_id=index_id,
            )
        elif view == "staged":
            if stage not in {"nodes", "edges"}:
                raise CodeCompassGraphReadError("graph_stage_invalid")
            staged_offset = self._decode_graph_cursor(
                values.get("cursor"),
                graph_revision=graph_revision,
                scope_digest=self._staged_scope_digest(
                    index_id=index_id,
                    stage=stage,
                    domain_scope=str(values.get("domain_scope") or "").strip() or None,
                    include_subdomains=bool(values.get("include_subdomains", True)),
                ),
            )

        if view == "default":
            return self._default_page(
                index_id=index_id,
                store=store,
                nodes=base_nodes,
                edges=base_edges,
                values=values,
                limit=limit,
                edge_limit=edge_limit,
                graph_revision=graph_revision,
                evidence_revision=revision_identity.evidence_revision,
                semantic_budget=semantic_budget,
                diagnostics=diagnostics,
                warnings=warnings,
                artifact_status=artifact_status,
            )

        snapshot = self._snapshot(
            index_id=index_id,
            graph_revision=graph_revision,
            nodes=all_nodes,
            edges=all_edges,
        )
        if view == "inventory":
            return self._inventory_page(
                index_id=index_id,
                snapshot=snapshot,
                cursor=inventory_cursor or _InventoryCursor(None, 0),
                limit=limit,
                graph_revision=graph_revision,
                semantic_budget=semantic_budget,
                diagnostics=diagnostics,
                warnings=warnings,
                artifact_status=artifact_status,
            )

        scope = self._scope(
            snapshot=snapshot,
            scope_key=str(values.get("domain_scope") or "").strip() or None,
            include_descendants=bool(values.get("include_subdomains", True)),
        )
        if scope["global_unresolved_edge_count"]:
            warnings.insert(
                0,
                self._unresolved_graph_warning(int(scope["global_unresolved_edge_count"])),
            )
        if scope["boundary_edge_count"]:
            boundary_count = int(scope["boundary_edge_count"])
            warnings.append(
                f"{boundary_count} graph relation"
                f"{'s cross' if boundary_count != 1 else ' crosses'} the selected "
                "domain boundary and remain available in the full indexed graph."
            )

        if view == "staged":
            return self._staged_page(
                index_id=index_id,
                store=store,
                snapshot=snapshot,
                scope=scope,
                stage=stage,
                offset=staged_offset or 0,
                limit=limit,
                edge_limit=edge_limit,
                graph_revision=graph_revision,
                evidence_revision=revision_identity.evidence_revision,
                semantic_budget=semantic_budget,
                diagnostics=diagnostics,
                warnings=warnings,
                artifact_status=artifact_status,
            )
        return self._topology_page(
            index_id=index_id,
            store=store,
            scope=scope,
            values=values,
            limit=limit,
            edge_limit=edge_limit,
            graph_revision=graph_revision,
            evidence_revision=revision_identity.evidence_revision,
            semantic_budget=semantic_budget,
            diagnostics=diagnostics,
            warnings=warnings,
            artifact_status=artifact_status,
        )

    def clear_cache(self) -> None:
        with self._snapshot_lock:
            self._snapshot_cache.clear()
        with self._revision_lock:
            self._revision_cache.clear()

    def _snapshot(
        self,
        *,
        index_id: str,
        graph_revision: str,
        nodes: Sequence[object],
        edges: Sequence[object],
    ) -> _GraphDerivedSnapshot:
        key = (str(index_id), graph_revision)
        with self._snapshot_lock:
            cached = self._snapshot_cache.pop(key, None)
            if cached is not None:
                self._snapshot_cache[key] = cached
                return cached
            snapshot = self._build_snapshot(nodes=nodes, edges=edges)
            self._snapshot_cache[key] = snapshot
            while len(self._snapshot_cache) > self._maximum_cached_revisions:
                self._snapshot_cache.popitem(last=False)
            return snapshot

    def _build_snapshot(
        self,
        *,
        nodes: Sequence[object],
        edges: Sequence[object],
    ) -> _GraphDerivedSnapshot:
        domain_index = self._domains.prepare(nodes=nodes)
        canonical_nodes = domain_index.select(
            scope_key=None,
            include_descendants=True,
        ).nodes
        node_ids = frozenset(identifier for node in canonical_nodes if (identifier := self._node_id(node)))
        canonical_edges = tuple(self._mappings(edges))
        bound_edges: list[Mapping[str, object]] = []
        unresolved_edges: list[Mapping[str, object]] = []
        endpoint_indices: dict[str, list[int]] = {}
        relation_counts: dict[str, list[int]] = {}
        for edge_index, edge in enumerate(canonical_edges):
            source, target = self._edge_endpoints(edge)
            for endpoint in {source, target} - {""}:
                endpoint_indices.setdefault(endpoint, []).append(edge_index)
            bound = source in node_ids and target in node_ids
            if bound:
                bound_edges.append(edge)
            else:
                unresolved_edges.append(edge)
            relation = self._edge_relation(edge)
            counts = relation_counts.setdefault(relation, [0, 0, 0])
            counts[0] += 1
            counts[1 if bound else 2] += 1
        relation_facets = tuple(
            _RelationFacet(
                raw_type=relation,
                edge_count=counts[0],
                bound_edge_count=counts[1],
                unresolved_edge_count=counts[2],
            )
            for relation, counts in sorted(relation_counts.items())
        )
        return _GraphDerivedSnapshot(
            domain_index=domain_index,
            nodes=canonical_nodes,
            edges=canonical_edges,
            node_ids=node_ids,
            bound_edges=tuple(bound_edges),
            unresolved_edges=tuple(unresolved_edges),
            edge_indices_by_endpoint={endpoint: tuple(indices) for endpoint, indices in endpoint_indices.items()},
            relation_facets=relation_facets,
        )

    def _prepared_edge_population(
        self,
        snapshot: _GraphDerivedSnapshot,
    ) -> CodeCompassPreparedEdgePopulation | None:
        with snapshot.prepared_edge_population_lock:
            prepared = snapshot.prepared_edge_population
            if prepared is _UNPREPARED_EDGE_POPULATION:
                prepare = getattr(self._projection, "prepare_edge_population", None)
                prepared = prepare(snapshot.edges) if callable(prepare) else None
                snapshot.prepared_edge_population = prepared
            return prepared if isinstance(prepared, CodeCompassPreparedEdgePopulation) else None

    def _default_page(
        self,
        *,
        index_id: str,
        store: CodeCompassGraphReadStorePort,
        nodes: Sequence[Mapping[str, object]],
        edges: Sequence[Mapping[str, object]],
        values: Mapping[str, object],
        limit: int,
        edge_limit: int,
        graph_revision: str,
        evidence_revision: str,
        semantic_budget: Mapping[str, object],
        diagnostics: Mapping[str, object],
        warnings: list[str],
        artifact_status: Mapping[str, object],
    ) -> Mapping[str, object]:
        offset = self._decode_offset(values.get("cursor"))
        visible = list(nodes[offset : offset + limit])
        node_ids = {self._node_id(item) for item in visible}
        node_ids.discard("")
        internal_edges = [
            edge for edge in edges if all(endpoint in node_ids for endpoint in self._edge_endpoints(edge))
        ]
        visible_edges = internal_edges[:edge_limit]
        next_cursor = self._encode_offset(offset + limit) if offset + limit < len(nodes) else None
        projected = self._projection.project(
            nodes=visible,
            edges=visible_edges,
            source_kind="codecompass_graph",
            source_ref=index_id,
            graph_revision=evidence_revision,
            visual_metrics=store.load_visual_metrics(),
            diagnostics=diagnostics,
            warnings=warnings,
            metadata={
                "knowledge_index_id": index_id,
                "view": "default",
                "content_graph_revision": graph_revision,
                "next_cursor": next_cursor,
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "source_edge_count": len(edges),
                "unresolved_edge_count": 0,
                "internal_edge_count": len(internal_edges),
                "edge_capped": len(visible_edges) < len(internal_edges),
                "max_edges": edge_limit,
                "semantic_budget": dict(semantic_budget),
            },
        )
        projected["text_alternative"] = f"Graph with {len(visible)} nodes and {len(visible_edges)} edges."
        projected["artifact_status"] = dict(artifact_status)
        return projected

    def _inventory_page(
        self,
        *,
        index_id: str,
        snapshot: _GraphDerivedSnapshot,
        cursor: _InventoryCursor,
        limit: int,
        graph_revision: str,
        semantic_budget: Mapping[str, object],
        diagnostics: Mapping[str, object],
        warnings: list[str],
        artifact_status: Mapping[str, object],
    ) -> Mapping[str, object]:
        catalog = snapshot.domain_index.domain_catalog
        domain_offset = cursor.offset if cursor.facet in {None, "domains"} else 0
        relation_offset = cursor.offset if cursor.facet == "relations" else 0
        domain_page = catalog.facets[domain_offset : domain_offset + limit]
        relation_page = snapshot.relation_facets[relation_offset : relation_offset + limit]
        domain_next = self._inventory_next_cursor(
            offset=domain_offset + len(domain_page),
            total=len(catalog.facets),
            graph_revision=graph_revision,
            index_id=index_id,
            facet="domains",
        )
        relation_next = self._inventory_next_cursor(
            offset=relation_offset + len(relation_page),
            total=len(snapshot.relation_facets),
            graph_revision=graph_revision,
            index_id=index_id,
            facet="relations",
        )
        if snapshot.unresolved_edges:
            warnings.insert(
                0,
                self._unresolved_graph_warning(len(snapshot.unresolved_edges)),
            )
        return {
            "schema": "codecompass_graph_inventory.v1",
            "source_kind": "codecompass_graph",
            "source_ref": index_id,
            "graph_revision": graph_revision,
            "facets": {
                "domains": {
                    "items": [facet.to_wire() for facet in domain_page],
                    "returned": len(domain_page),
                    "next_cursor": domain_next,
                    "total_count": len(catalog.facets),
                    "complete": domain_next is None,
                },
                "relations": {
                    "items": [facet.to_wire() for facet in relation_page],
                    "returned": len(relation_page),
                    "next_cursor": relation_next,
                    "total_count": len(snapshot.relation_facets),
                    "complete": relation_next is None,
                },
            },
            "coverage": {
                "graph": {
                    "nodes": catalog.total_node_count,
                    "bound_edges": len(snapshot.bound_edges),
                    "source_edges": len(snapshot.edges),
                    "unresolved_edges": len(snapshot.unresolved_edges),
                },
                "domains": {
                    "assigned_nodes": catalog.assigned_node_count,
                    "unassigned_nodes": catalog.unassigned_node_count,
                    "returned": len(domain_page),
                    "total": len(catalog.facets),
                    "complete": domain_next is None,
                },
                "relations": {
                    "returned": len(relation_page),
                    "total_count": len(snapshot.relation_facets),
                    "complete": relation_next is None,
                    "edge_count": len(snapshot.edges),
                    "bound_edge_count": len(snapshot.bound_edges),
                    "unresolved_edge_count": len(snapshot.unresolved_edges),
                },
                "materialization": {
                    "semantic_budget": dict(semantic_budget),
                },
            },
            "diagnostics": dict(diagnostics),
            "warnings": list(warnings),
            "metadata": {
                "knowledge_index_id": index_id,
                "view": "inventory",
                "content_graph_revision": graph_revision,
                "next_cursor": domain_next,
                "relations_next_cursor": relation_next,
                "total_nodes": catalog.total_node_count,
                "total_edges": len(snapshot.bound_edges),
                "total_domains": len(catalog.facets),
                "total_relation_types": len(snapshot.relation_facets),
            },
            "text_alternative": (
                f"Domain inventory page with {len(domain_page)} of "
                f"{len(catalog.facets)} domains and {len(relation_page)} of "
                f"{len(snapshot.relation_facets)} relation types for "
                f"{catalog.total_node_count} nodes."
            ),
            "artifact_status": dict(artifact_status),
        }

    def _scope(
        self,
        *,
        snapshot: _GraphDerivedSnapshot,
        scope_key: str | None,
        include_descendants: bool,
    ) -> dict[str, object]:
        try:
            selection = snapshot.domain_index.select(
                scope_key=scope_key,
                include_descendants=include_descendants,
            )
        except ValueError as exc:
            if str(exc) == "graph_domain_scope_unknown":
                raise CodeCompassGraphReadError("graph_domain_scope_unknown") from exc
            raise
        if scope_key is None:
            return {
                "nodes": selection.nodes,
                "edges": snapshot.edges,
                "bound_edges": snapshot.bound_edges,
                "staged_edge_indices": None,
                "scope_key": None,
                "scope_label": None,
                "include_subdomains": include_descendants,
                "global_total_node_count": len(snapshot.nodes),
                "global_source_edge_count": len(snapshot.edges),
                "global_bound_edge_count": len(snapshot.bound_edges),
                "global_unresolved_edge_count": len(snapshot.unresolved_edges),
                "scope_unresolved_edge_count": len(snapshot.unresolved_edges),
                "boundary_edge_count": 0,
                "domain_node_groups": tuple(
                    group.node_ids for group in selection.groups
                ),
            }

        selected_ids = frozenset(identifier for node in selection.nodes if (identifier := self._node_id(node)))
        candidate_indices: set[int] = set()
        for node_id in selected_ids:
            candidate_indices.update(snapshot.edge_indices_by_endpoint.get(node_id, ()))
        selected_bound: list[Mapping[str, object]] = []
        selected_staged_indices: list[int] = []
        boundary_edges = 0
        selected_unresolved = 0
        for edge_index in sorted(candidate_indices):
            edge = snapshot.edges[edge_index]
            source, target = self._edge_endpoints(edge)
            source_known = source in snapshot.node_ids
            target_known = target in snapshot.node_ids
            source_selected = source in selected_ids
            target_selected = target in selected_ids
            if source_known and target_known:
                if source_selected and target_selected:
                    selected_bound.append(edge)
                    selected_staged_indices.append(edge_index)
                elif source_selected != target_selected:
                    boundary_edges += 1
            elif source_selected or target_selected:
                selected_staged_indices.append(edge_index)
                selected_unresolved += 1
        return {
            "nodes": selection.nodes,
            "edges": tuple(selected_bound),
            "bound_edges": tuple(selected_bound),
            "staged_edge_indices": tuple(selected_staged_indices),
            "scope_key": scope_key,
            "scope_label": selection.facet.path if selection.facet else None,
            "include_subdomains": include_descendants,
            "global_total_node_count": len(snapshot.nodes),
            "global_source_edge_count": len(snapshot.edges),
            "global_bound_edge_count": len(snapshot.bound_edges),
            "global_unresolved_edge_count": len(snapshot.unresolved_edges),
            "scope_unresolved_edge_count": selected_unresolved,
            "boundary_edge_count": boundary_edges,
            "domain_node_groups": tuple(
                group.node_ids for group in selection.groups
            ),
        }

    def _topology_page(
        self,
        *,
        index_id: str,
        store: CodeCompassGraphReadStorePort,
        scope: Mapping[str, object],
        values: Mapping[str, object],
        limit: int,
        edge_limit: int,
        graph_revision: str,
        evidence_revision: str,
        semantic_budget: Mapping[str, object],
        diagnostics: Mapping[str, object],
        warnings: list[str],
        artifact_status: Mapping[str, object],
    ) -> Mapping[str, object]:
        window = self._window.select(
            nodes=scope["nodes"],
            edges=scope["edges"],
            node_limit=limit,
            edge_limit=edge_limit,
            node_groups=scope["domain_node_groups"],
        )
        visible = list(window.nodes)
        edges = list(window.edges)
        domain_scope = str(values.get("domain_scope") or "").strip() or None
        total_nodes = window.total_node_count
        projected = self._projection.project(
            nodes=visible,
            edges=edges,
            source_kind="codecompass_graph",
            source_ref=index_id,
            graph_revision=evidence_revision,
            visual_metrics=store.load_visual_metrics(),
            derive_projection_revision=True,
            diagnostics=diagnostics,
            warnings=warnings,
            metadata={
                "knowledge_index_id": index_id,
                "view": "topology",
                "content_graph_revision": graph_revision,
                "next_cursor": None,
                "total_nodes": total_nodes,
                "total_edges": window.total_edge_count,
                "source_edge_count": scope["global_source_edge_count"],
                "unresolved_edge_count": scope["global_unresolved_edge_count"],
                "internal_edge_count": window.internal_edge_count,
                "edge_capped": window.edge_capped,
                "max_edges": edge_limit,
                "semantic_budget": dict(semantic_budget),
                "domain_scope": domain_scope,
                "domain_scope_label": scope["scope_label"],
                "include_subdomains": bool(values.get("include_subdomains", True)),
                "global_total_nodes": scope["global_total_node_count"],
                "global_total_edges": scope["global_bound_edge_count"],
                "global_source_edge_count": scope["global_source_edge_count"],
                "global_unresolved_edge_count": scope["global_unresolved_edge_count"],
                "scope_total_nodes": total_nodes,
                "scope_boundary_edge_count": scope["boundary_edge_count"],
                "scope_unresolved_edge_count": scope["scope_unresolved_edge_count"],
                "remaining_nodes": max(0, total_nodes - len(visible)),
                "window_node_limit": limit,
                "window_domain_group_count": window.represented_group_count,
                "scope_domain_group_count": window.total_group_count,
                "delivery_complete": (len(visible) == total_nodes and not window.edge_capped),
            },
        )
        projected["text_alternative"] = (
            f"Topology graph window with {len(visible)} nodes and "
            f"{len(edges)} edges out of {total_nodes}"
            f"{' selected' if domain_scope else ''} nodes."
        )
        projected["artifact_status"] = dict(artifact_status)
        return projected

    def _staged_page(
        self,
        *,
        index_id: str,
        store: CodeCompassGraphReadStorePort,
        snapshot: _GraphDerivedSnapshot,
        scope: Mapping[str, object],
        stage: str,
        offset: int,
        limit: int,
        edge_limit: int,
        graph_revision: str,
        evidence_revision: str,
        semantic_budget: Mapping[str, object],
        diagnostics: Mapping[str, object],
        warnings: list[str],
        artifact_status: Mapping[str, object],
    ) -> Mapping[str, object]:
        raw_scoped_nodes = scope.get("nodes")
        scoped_nodes = (
            raw_scoped_nodes
            if isinstance(raw_scoped_nodes, Sequence) and not isinstance(raw_scoped_nodes, (str, bytes))
            else ()
        )
        raw_scoped_edge_indices = scope.get("staged_edge_indices")
        scoped_edge_indices = (
            raw_scoped_edge_indices
            if isinstance(raw_scoped_edge_indices, Sequence) and not isinstance(raw_scoped_edge_indices, (str, bytes))
            else None
        )
        total_edges = len(snapshot.edges) if scoped_edge_indices is None else len(scoped_edge_indices)
        page_edge_indices: Sequence[int] = ()
        if stage == "nodes":
            page_size = limit
            page_nodes = list(scoped_nodes[offset : offset + page_size])
            page_edges: list[Mapping[str, object]] = []
            total_items = len(scoped_nodes)
        else:
            page_size = edge_limit
            page_nodes = []
            if scoped_edge_indices is None:
                page_edge_indices = range(
                    offset,
                    min(offset + page_size, len(snapshot.edges)),
                )
            else:
                page_edge_indices = scoped_edge_indices[offset : offset + page_size]
            page_edges = [snapshot.edges[index] for index in page_edge_indices]
            total_items = total_edges
        returned = len(page_nodes) if stage == "nodes" else len(page_edges)
        next_cursor = (
            self._encode_graph_cursor(
                offset + returned,
                graph_revision=graph_revision,
                scope_digest=self._staged_scope_digest(
                    index_id=index_id,
                    stage=stage,
                    domain_scope=scope.get("scope_key"),
                    include_subdomains=bool(scope.get("include_subdomains", True)),
                ),
            )
            if offset + returned < total_items
            else None
        )
        projection_options: dict[str, object] = {}
        if stage == "edges":
            prepared = self._prepared_edge_population(snapshot)
            if prepared is not None:
                projection_options = {
                    "prepared_edge_population": prepared,
                    "edge_population_indices": page_edge_indices,
                }
        projected = self._projection.project(
            nodes=page_nodes,
            edges=page_edges,
            source_kind="codecompass_graph",
            source_ref=index_id,
            graph_revision=evidence_revision,
            visual_metrics=store.load_visual_metrics(),
            diagnostics=diagnostics,
            warnings=warnings,
            metadata={
                "knowledge_index_id": index_id,
                "view": "staged",
                "content_graph_revision": graph_revision,
                "stage": stage,
                "next_cursor": next_cursor,
                "total_nodes": len(scoped_nodes),
                "total_edges": total_edges,
                "source_edge_count": scope["global_source_edge_count"],
                "unresolved_edge_count": scope["global_unresolved_edge_count"],
                "internal_edge_count": len(scope.get("bound_edges") or []),
                "edge_capped": False,
                "max_edges": edge_limit,
                "semantic_budget": dict(semantic_budget),
                "domain_scope": scope.get("scope_key"),
                "domain_scope_label": scope.get("scope_label"),
                "include_subdomains": bool(scope.get("include_subdomains", True)),
                "global_total_nodes": scope["global_total_node_count"],
                "global_total_edges": scope["global_bound_edge_count"],
                "global_source_edge_count": scope["global_source_edge_count"],
                "scope_boundary_edge_count": scope["boundary_edge_count"],
                "scope_unresolved_edge_count": scope["scope_unresolved_edge_count"],
                "delivery_complete": next_cursor is None,
                "delivery_returned": returned,
                "delivery_total": total_items,
            },
            **projection_options,
        )
        projected["text_alternative"] = (
            f"Lossless staged {stage} page with {returned} of {total_items} selected records."
        )
        projected["artifact_status"] = dict(artifact_status)
        return projected

    @staticmethod
    def _diagnostics(raw: Mapping[str, object]) -> dict[str, object]:
        value = raw.get("diagnostics")
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _semantic_translation(
        diagnostics: Mapping[str, object],
    ) -> Mapping[str, object]:
        value = diagnostics.get("semantic_translation")
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _semantic_budget(
        semantic_translation: Mapping[str, object],
    ) -> dict[str, object]:
        value = semantic_translation.get("semantic_budget")
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _mappings(values: object) -> tuple[Mapping[str, object], ...]:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return ()
        return tuple(value for value in values if isinstance(value, Mapping))

    @staticmethod
    def _node_id(node: Mapping[str, object]) -> str:
        return str(node.get("id") or node.get("node_id") or "").strip()

    @staticmethod
    def _edge_endpoints(edge: Mapping[str, object]) -> tuple[str, str]:
        return (
            str(edge.get("source_id") or edge.get("source") or edge.get("from") or "").strip(),
            str(edge.get("target_id") or edge.get("target") or edge.get("to") or "").strip(),
        )

    @staticmethod
    def _edge_relation(edge: Mapping[str, object]) -> str:
        attributes = edge.get("attributes")
        nested = attributes if isinstance(attributes, Mapping) else {}
        return str(
            edge.get("raw_edge_type")
            or nested.get("raw_edge_type")
            or edge.get("edge_type")
            or edge.get("relation")
            or edge.get("type")
            or "related"
        )

    def _graph_revision_identity(
        self,
        *,
        raw: Mapping[str, object],
        nodes: Sequence[Mapping[str, object]],
        edges: Sequence[Mapping[str, object]],
    ) -> _GraphRevisionIdentity:
        cache_key = id(raw)
        with self._revision_lock:
            cached = self._revision_cache.pop(cache_key, None)
            if cached is not None and cached.payload is raw:
                self._revision_cache[cache_key] = cached
                return cached.identity
            content_revision = self._compute_content_graph_revision(
                nodes=nodes,
                edges=edges,
            )
            state = raw.get("state")
            explicit = str(state.get("manifest_hash") or "").strip() if isinstance(state, Mapping) else ""
            identity = _GraphRevisionIdentity(
                content_revision=content_revision,
                evidence_revision=explicit or content_revision,
            )
            self._revision_cache[cache_key] = _GraphPayloadRevision(
                payload=raw,
                identity=identity,
            )
            while len(self._revision_cache) > self._maximum_cached_revisions:
                self._revision_cache.popitem(last=False)
            return identity

    def _compute_content_graph_revision(
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

    @staticmethod
    def _graph_warnings(
        *,
        semantic_budget: Mapping[str, object],
        semantic_translation: Mapping[str, object],
    ) -> list[str]:
        warnings: list[str] = []
        if bool(semantic_budget.get("truncated")):
            warnings.append(
                "The semantic graph reached its configured record budget; the topology is a documented partial view."
            )
        semantic_unresolved = int(semantic_budget.get("unresolved_edge_count") or 0)
        if semantic_unresolved:
            warnings.append(
                f"{semantic_unresolved} semantic graph relation"
                f"{'s were' if semantic_unresolved != 1 else ' was'} not materialized "
                "because no source-grounded endpoint was available."
            )
        if str(semantic_translation.get("status") or "").lower() == "degraded" and not warnings:
            warnings.append("The semantic graph reports degraded materialization.")
        return warnings

    @staticmethod
    def _unresolved_graph_warning(count: int) -> str:
        return (
            f"{count} graph relation"
            f"{'s have' if count != 1 else ' has'} an unavailable source or target "
            "node. The staged edge stream retains these relations; reindex the "
            "source to materialize current endpoints."
        )

    @staticmethod
    def _staged_scope_digest(
        *,
        index_id: str,
        stage: str,
        domain_scope: object,
        include_subdomains: bool,
    ) -> str:
        return CodeCompassGraphReadService._cursor_scope_digest(
            {
                "view": "staged",
                "stage": stage,
                "domain_scope": domain_scope,
                "include_subdomains": include_subdomains,
                "index_id": index_id,
            }
        )

    @staticmethod
    def _inventory_scope_digest(*, index_id: str, facet: str) -> str:
        return CodeCompassGraphReadService._cursor_scope_digest(
            {"view": "inventory", "facet": facet, "index_id": index_id}
        )

    @staticmethod
    def _cursor_scope_digest(scope: Mapping[str, object]) -> str:
        payload = json.dumps(
            dict(scope),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_graph_cursor(
        offset: int,
        *,
        graph_revision: str,
        scope_digest: str,
    ) -> str:
        payload = json.dumps(
            {
                "version": 1,
                "graph_revision": graph_revision,
                "scope_digest": scope_digest,
                "offset": int(offset),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def _decode_graph_cursor(
        cls,
        value: object,
        *,
        graph_revision: str,
        scope_digest: str,
    ) -> int:
        if value in (None, ""):
            return 0
        payload = cls._decode_cursor_payload(
            value,
            graph_revision=graph_revision,
        )
        if payload.get("scope_digest") != scope_digest:
            raise CodeCompassGraphReadError("graph_cursor_scope_mismatch")
        return int(payload["offset"])

    @classmethod
    def _decode_inventory_cursor(
        cls,
        value: object,
        *,
        graph_revision: str,
        index_id: str,
    ) -> _InventoryCursor:
        if value in (None, ""):
            return _InventoryCursor(None, 0)
        payload = cls._decode_cursor_payload(
            value,
            graph_revision=graph_revision,
        )
        scope_digest = payload.get("scope_digest")
        for facet in ("domains", "relations"):
            if scope_digest == cls._inventory_scope_digest(
                index_id=index_id,
                facet=facet,
            ):
                return _InventoryCursor(facet, int(payload["offset"]))
        raise CodeCompassGraphReadError("graph_cursor_scope_mismatch")

    @staticmethod
    def _decode_cursor_payload(
        value: object,
        *,
        graph_revision: str,
    ) -> Mapping[str, object]:
        try:
            encoded = str(value)
            encoded += "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
            if not isinstance(payload, Mapping) or payload.get("version") != 1:
                raise ValueError
            if payload.get("graph_revision") != graph_revision:
                raise CodeCompassGraphReadError(
                    "graph_cursor_stale",
                    status_code=409,
                )
            offset = int(payload["offset"])
            if offset < 0:
                raise ValueError
            return payload
        except CodeCompassGraphReadError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise CodeCompassGraphReadError("graph_cursor_invalid") from exc

    @classmethod
    def _inventory_next_cursor(
        cls,
        *,
        offset: int,
        total: int,
        graph_revision: str,
        index_id: str,
        facet: str,
    ) -> str | None:
        if offset >= total:
            return None
        return cls._encode_graph_cursor(
            offset,
            graph_revision=graph_revision,
            scope_digest=cls._inventory_scope_digest(
                index_id=index_id,
                facet=facet,
            ),
        )

    @staticmethod
    def _encode_offset(value: int) -> str:
        return base64.urlsafe_b64encode(str(value).encode("ascii")).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_offset(value: object) -> int:
        if value in (None, ""):
            return 0
        try:
            encoded = str(value)
            encoded += "=" * (-len(encoded) % 4)
            parsed = int(base64.urlsafe_b64decode(encoded).decode("ascii"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CodeCompassGraphReadError("graph_cursor_invalid") from exc
        if parsed < 0:
            raise CodeCompassGraphReadError("graph_cursor_invalid")
        return parsed


codecompass_graph_read_service = CodeCompassGraphReadService(
    projection=get_codecompass_graph_projection_service(),
    window=get_codecompass_graph_window_service(),
    domains=get_codecompass_graph_domain_catalog_service(),
)


def get_codecompass_graph_read_service() -> CodeCompassGraphReadService:
    return codecompass_graph_read_service


__all__ = [
    "CodeCompassGraphReadError",
    "CodeCompassGraphReadPort",
    "CodeCompassGraphReadService",
    "CodeCompassGraphReadStorePort",
    "get_codecompass_graph_read_service",
]
