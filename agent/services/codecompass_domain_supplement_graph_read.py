"""Lazy request-scoped CodeCompass domain supplement reads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import RLock

from agent.services.codecompass_domain_supplement import (
    CodeCompassDomainSupplementCatalog,
    CodeCompassDomainSupplementPort,
    CodeCompassDomainSupplementRecords,
)
from agent.services.codecompass_domain_supplement_overlay import (
    CodeCompassDomainSupplementOverlayStore,
)
from agent.services.codecompass_graph_artifact_resolver import (
    ResolvedCodeCompassDomainSupplement,
)
from agent.services.codecompass_graph_domain_catalog_service import (
    CodeCompassGraphDomainCatalogPort,
)
from agent.services.codecompass_graph_read_service import (
    CodeCompassGraphReadPort,
    CodeCompassGraphReadStorePort,
)
from ananta_contracts.codecompass_semantic_partitions import (
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)


@dataclass(frozen=True)
class _ScopeOverlayCacheKey:
    index_id: str
    artifact_sha256: str
    logical_content_hash: str
    graph_revision: str
    domain_scope: str
    include_subdomains: bool


@dataclass(frozen=True)
class _ScopeOverlayCacheValue:
    key: _ScopeOverlayCacheKey
    store: CodeCompassGraphReadStorePort
    records: CodeCompassDomainSupplementRecords
    status: str


class CodeCompassDomainSupplementGraphReadCoordinator:
    """Coordinate catalog-only inventory and scoped payload overlays.

    Authorization and active-revision checks remain adapter-owned. This class
    owns only deterministic request-local selection and projection.
    """

    def __init__(
        self,
        *,
        graph_read: CodeCompassGraphReadPort,
        graph_domains: CodeCompassGraphDomainCatalogPort,
        domain_supplements: CodeCompassDomainSupplementPort,
    ) -> None:
        self._graph_read = graph_read
        self._graph_domains = graph_domains
        self._domain_supplements = domain_supplements
        self._scope_cache: _ScopeOverlayCacheValue | None = None
        self._scope_cache_lock = RLock()

    def read(
        self,
        *,
        index_id: str,
        base_store: CodeCompassGraphReadStorePort,
        parameters: Mapping[str, object],
        artifact_status: Mapping[str, object],
        resolved: ResolvedCodeCompassDomainSupplement | None,
        fallback_source_revision_id: str = "",
        fallback_source_revision_digest: str = "",
    ) -> Mapping[str, object]:
        values = dict(parameters)
        domain_scope = str(values.get("domain_scope") or "").strip()
        view = str(values.get("view") or "default").strip().lower()
        if view == "inventory":
            result = self._base_read(
                index_id=index_id,
                store=base_store,
                parameters=values,
                artifact_status=artifact_status,
            )
            if resolved is None:
                return result
            catalog = self._domain_supplements.catalog(
                path=resolved.path,
                binding=resolved.binding,
            )
            base_payload = base_store.load()
            structural_catalog = self._graph_domains.catalog(
                nodes=self._structural_graph_nodes(base_payload)
            )
            return self._augment_domain_inventory(
                result=result,
                catalog=catalog,
                structural_node_counts={
                    facet.key: facet.subtree_node_count
                    for facet in structural_catalog.facets
                },
            )
        if not domain_scope:
            return self._base_read(
                index_id=index_id,
                store=base_store,
                parameters=values,
                artifact_status=artifact_status,
            )
        cache_key = (
            self._scope_cache_key(
                index_id=index_id,
                resolved=resolved,
                domain_scope=domain_scope,
                include_subdomains=bool(
                    values.get("include_subdomains", True)
                ),
            )
            if resolved is not None
            else None
        )
        cached = self._cached_scope_overlay(cache_key)
        if cached is not None and resolved is not None:
            result = self._base_read(
                index_id=index_id,
                store=cached.store,
                parameters=values,
                artifact_status=artifact_status,
            )
            return self._augment_semantic_scope(
                result=result,
                domain_scope=domain_scope,
                status=cached.status,
                records=cached.records,
                source_revision_id=resolved.binding.source_revision_id,
                source_revision_digest=(
                    resolved.binding.source_revision_digest
                ),
                graph_revision=resolved.binding.graph_revision,
                evidence_graph_revision=resolved.binding.graph_revision,
            )

        base_payload = base_store.load()
        domain_index = self._graph_domains.prepare(
            nodes=self._graph_nodes(base_payload)
        )
        include_subdomains = bool(values.get("include_subdomains", True))
        selection = domain_index.select(
            scope_key=domain_scope,
            include_descendants=include_subdomains,
        )
        supplement_domain_keys = self._supplement_domain_keys(selection.nodes)
        store = base_store
        records = self._empty_supplement_records()
        semantic_status = "unavailable"
        evidence_revision = self._base_evidence_revision(base_payload)
        source_revision_id = fallback_source_revision_id
        source_revision_digest = fallback_source_revision_digest
        semantic_graph_revision = evidence_revision
        if resolved is not None:
            source_revision_id = resolved.binding.source_revision_id
            source_revision_digest = resolved.binding.source_revision_digest
            semantic_graph_revision = resolved.binding.graph_revision
            # Worker graph_revision already covers supplement logical content.
            evidence_revision = resolved.binding.graph_revision
            if supplement_domain_keys:
                facet = selection.facet
                store, records, semantic_status = self._scope_overlay(
                    index_id=index_id,
                    domain_scope=domain_scope,
                    include_subdomains=include_subdomains,
                    domain_keys=supplement_domain_keys,
                    selected_base_nodes=selection.nodes,
                    scope_source=(facet.source if facet is not None else None),
                    scope_path=(facet.path if facet is not None else None),
                    base_store=base_store,
                    base_payload=base_payload,
                    resolved=resolved,
                    cache_key=cache_key,
                )
        result = self._base_read(
            index_id=index_id,
            store=store,
            parameters=values,
            artifact_status=artifact_status,
        )
        return self._augment_semantic_scope(
            result=result,
            domain_scope=domain_scope,
            status=semantic_status,
            records=records,
            source_revision_id=source_revision_id,
            source_revision_digest=source_revision_digest,
            graph_revision=semantic_graph_revision,
            evidence_graph_revision=evidence_revision,
        )

    def _scope_overlay(
        self,
        *,
        index_id: str,
        domain_scope: str,
        include_subdomains: bool,
        domain_keys: tuple[str, ...],
        selected_base_nodes: Sequence[Mapping[str, object]],
        scope_source: str | None,
        scope_path: str | None,
        base_store: CodeCompassGraphReadStorePort,
        base_payload: Mapping[str, object],
        resolved: ResolvedCodeCompassDomainSupplement,
        cache_key: _ScopeOverlayCacheKey | None,
    ) -> tuple[
        CodeCompassGraphReadStorePort,
        CodeCompassDomainSupplementRecords,
        str,
    ]:
        binding = resolved.binding
        key = cache_key or self._scope_cache_key(
            index_id=index_id,
            resolved=resolved,
            domain_scope=domain_scope,
            include_subdomains=include_subdomains,
        )
        # Capacity one is intentional: staged pages for the active scope reuse
        # the exact overlay payload identity (and therefore GraphRead snapshots),
        # while a scope or revision change deterministically evicts it.
        with self._scope_cache_lock:
            cached = self._scope_cache
            if cached is not None and cached.key == key:
                return cached.store, cached.records, cached.status
            self._scope_cache = None

            partition_records = self._domain_supplements.load_domains(
                path=resolved.path,
                domain_keys=domain_keys,
                binding=binding,
            )
            loaded_keys = frozenset(partition_records.domain_keys)
            requested_keys = frozenset(domain_keys)
            status = (
                "complete"
                if loaded_keys == requested_keys
                else "partial"
                if loaded_keys
                else "unavailable"
            )
            if not loaded_keys:
                return base_store, self._empty_supplement_records(), status
            records, scope_proven = self._filter_supplement_records(
                records=partition_records,
                selected_base_nodes=selected_base_nodes,
                scope_source=scope_source,
                scope_path=scope_path,
                include_subdomains=include_subdomains,
            )
            if not scope_proven:
                return (
                    base_store,
                    self._empty_supplement_records(),
                    "unavailable",
                )
            store = CodeCompassDomainSupplementOverlayStore(
                base_store=base_store,
                base_payload=base_payload,
                supplement=records,
                evidence_graph_revision=binding.graph_revision,
                scope_source=scope_source,
                scope_path=scope_path,
            )
            cached = _ScopeOverlayCacheValue(
                key=key,
                store=store,
                records=records,
                status=status,
            )
            self._scope_cache = cached
            return cached.store, cached.records, cached.status

    @staticmethod
    def _scope_cache_key(
        *,
        index_id: str,
        resolved: ResolvedCodeCompassDomainSupplement,
        domain_scope: str,
        include_subdomains: bool,
    ) -> _ScopeOverlayCacheKey:
        binding = resolved.binding
        return _ScopeOverlayCacheKey(
            index_id=index_id,
            artifact_sha256=binding.artifact_sha256,
            logical_content_hash=binding.logical_content_hash,
            graph_revision=binding.graph_revision,
            domain_scope=domain_scope,
            include_subdomains=include_subdomains,
        )

    def _cached_scope_overlay(
        self,
        key: _ScopeOverlayCacheKey | None,
    ) -> _ScopeOverlayCacheValue | None:
        if key is None:
            return None
        with self._scope_cache_lock:
            cached = self._scope_cache
            return cached if cached is not None and cached.key == key else None

    def _base_read(
        self,
        *,
        index_id: str,
        store: CodeCompassGraphReadStorePort,
        parameters: Mapping[str, object],
        artifact_status: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._graph_read.read(
            index_id=index_id,
            store=store,
            parameters=parameters,
            artifact_status=artifact_status,
        )

    @staticmethod
    def _graph_nodes(
        payload: Mapping[str, object],
    ) -> tuple[Mapping[str, object], ...]:
        nodes: list[Mapping[str, object]] = []
        for field in ("nodes", "semantic_nodes"):
            values = payload.get(field)
            if isinstance(values, Sequence) and not isinstance(
                values,
                (str, bytes),
            ):
                nodes.extend(
                    item for item in values if isinstance(item, Mapping)
                )
        return tuple(nodes)

    @staticmethod
    def _structural_graph_nodes(
        payload: Mapping[str, object],
    ) -> tuple[Mapping[str, object], ...]:
        values = payload.get("nodes")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return ()
        return tuple(item for item in values if isinstance(item, Mapping))

    @classmethod
    def _supplement_domain_keys(
        cls,
        nodes: Sequence[Mapping[str, object]],
    ) -> tuple[str, ...]:
        keys: set[str] = set()
        for node in nodes:
            path = cls._repository_relative_node_path(node)
            if path is None:
                continue
            parts = tuple(
                part
                for part in path.replace("\\", "/").split("/")
                if part not in {"", "."}
            )
            if not parts or ".." in parts:
                continue
            kind = cls._node_kind(node)
            if len(parts) == 1 and kind not in {
                "directory",
                "repository",
            }:
                keys.add(
                    codecompass_semantic_repository_root_domain_key()
                )
            else:
                keys.add(codecompass_semantic_domain_key(parts[0]))
        return tuple(sorted(keys))

    @staticmethod
    def _repository_relative_node_path(
        node: Mapping[str, object],
    ) -> str | None:
        records: list[Mapping[str, object]] = [node]
        for field in ("attributes", "attrs", "source_record", "provenance"):
            value = node.get(field)
            if isinstance(value, Mapping):
                records.append(value)
        for field in ("file", "path", "relative_path"):
            for record in records:
                raw = record.get(field)
                if not isinstance(raw, str):
                    continue
                path = raw.strip().replace("\\", "/")
                if (
                    not path
                    or len(path) > 4_096
                    or path.startswith("/")
                    or (len(path) > 1 and path[1] == ":")
                ):
                    continue
                return path
        return None

    @classmethod
    def _filter_supplement_records(
        cls,
        *,
        records: CodeCompassDomainSupplementRecords,
        selected_base_nodes: Sequence[Mapping[str, object]],
        scope_source: str | None,
        scope_path: str | None,
        include_subdomains: bool,
    ) -> tuple[CodeCompassDomainSupplementRecords, bool]:
        if scope_source not in {
            "path",
            "unassigned",
            "domain_id",
            "domain_path",
        }:
            return cls._empty_supplement_records(), False

        accepted_nodes: list[Mapping[str, object]] = []
        if scope_source == "path" and scope_path:
            prefix = scope_path.rstrip("/") + "/"
            accepted_nodes = [
                node
                for node in records.nodes
                if (
                    (domain_path := cls._node_directory_domain(node))
                    == scope_path
                    or (
                        include_subdomains
                        and domain_path.startswith(prefix)
                    )
                )
            ]
            scope_proven = True
        elif scope_source == "unassigned":
            selected_paths = [
                cls._repository_relative_node_path(node)
                for node in selected_base_nodes
            ]
            scope_proven = bool(selected_paths) and all(
                path is not None
                and cls._node_directory_domain(node) == ""
                for node, path in zip(selected_base_nodes, selected_paths)
            )
            accepted_nodes = (
                [
                    node
                    for node in records.nodes
                    if cls._repository_relative_node_path(node) is not None
                    and cls._node_directory_domain(node) == ""
                ]
                if scope_proven
                else []
            )
        elif scope_path:
            exact_paths: set[str] = set()
            directory_prefixes: set[str] = set()
            for node in selected_base_nodes:
                path = cls._repository_relative_node_path(node)
                if path is None:
                    continue
                normalized = path.strip("/")
                if cls._node_kind(node) in {"directory", "repository"}:
                    directory_prefixes.add(normalized.rstrip("/") + "/")
                else:
                    exact_paths.add(normalized)
            scope_proven = bool(exact_paths or directory_prefixes)
            accepted_nodes = (
                [
                    node
                    for node in records.nodes
                    if (
                        (path := cls._repository_relative_node_path(node))
                        is not None
                        and (
                            path.strip("/") in exact_paths
                            or any(
                                path.strip("/").startswith(prefix)
                                for prefix in directory_prefixes
                            )
                        )
                    )
                ]
                if scope_proven
                else []
            )
        else:
            return cls._empty_supplement_records(), False

        accepted_ids = {
            str(node.get("id") or node.get("node_id") or "").strip()
            for node in accepted_nodes
        }
        accepted_ids.discard("")
        selected_endpoint_ids = accepted_ids | {
            str(node.get("id") or node.get("node_id") or "").strip()
            for node in selected_base_nodes
        }
        selected_endpoint_ids.discard("")
        semantic_edges = tuple(
            edge
            for edge in records.semantic_edges
            if cls._edge_touches(edge, selected_endpoint_ids)
        )
        declaration_edges = tuple(
            edge
            for edge in records.declaration_edges
            if cls._edge_touches(edge, selected_endpoint_ids)
        )
        return (
            CodeCompassDomainSupplementRecords(
                graph_revision=records.graph_revision,
                logical_content_hash=records.logical_content_hash,
                domain_keys=records.domain_keys,
                nodes=tuple(accepted_nodes),
                semantic_edges=semantic_edges,
                declaration_edges=declaration_edges,
                semantic_node_count=len(accepted_nodes),
                semantic_edge_count=len(semantic_edges),
                declaration_edge_count=len(declaration_edges),
            ),
            scope_proven,
        )

    @classmethod
    def _node_directory_domain(
        cls,
        node: Mapping[str, object],
    ) -> str:
        path = cls._repository_relative_node_path(node)
        if path is None:
            return ""
        normalized = path.strip("/")
        parts = normalized.split("/") if normalized else []
        if cls._node_kind(node) not in {"directory", "repository"}:
            parts = parts[:-1]
        return "/".join(parts)

    @staticmethod
    def _node_kind(node: Mapping[str, object]) -> str:
        records: list[Mapping[str, object]] = [node]
        for field in ("attributes", "attrs", "source_record"):
            value = node.get(field)
            if isinstance(value, Mapping):
                records.append(value)
        for field in ("raw_node_type", "node_type", "kind", "type"):
            for record in records:
                value = record.get(field)
                if value is not None:
                    return str(value).strip().lower()
        return ""

    @staticmethod
    def _edge_touches(
        edge: Mapping[str, object],
        node_ids: set[str],
    ) -> bool:
        source = str(
            edge.get("source")
            or edge.get("source_id")
            or edge.get("from")
            or ""
        ).strip()
        target = str(
            edge.get("target")
            or edge.get("target_id")
            or edge.get("to")
            or ""
        ).strip()
        return source in node_ids or target in node_ids

    @staticmethod
    def _base_evidence_revision(payload: Mapping[str, object]) -> str:
        state = payload.get("state")
        if isinstance(state, Mapping):
            revision = str(state.get("manifest_hash") or "")
            if revision:
                return revision
        return ""

    @staticmethod
    def _empty_supplement_records() -> CodeCompassDomainSupplementRecords:
        return CodeCompassDomainSupplementRecords(
            graph_revision="",
            logical_content_hash="",
            domain_keys=(),
            nodes=(),
            semantic_edges=(),
            declaration_edges=(),
            semantic_node_count=0,
            semantic_edge_count=0,
            declaration_edge_count=0,
        )

    @staticmethod
    def _augment_semantic_scope(
        *,
        result: Mapping[str, object],
        domain_scope: str,
        status: str,
        records: CodeCompassDomainSupplementRecords,
        source_revision_id: str,
        source_revision_digest: str,
        graph_revision: str,
        evidence_graph_revision: str,
    ) -> Mapping[str, object]:
        projected = dict(result)
        raw_metadata = projected.get("metadata")
        metadata = (
            dict(raw_metadata)
            if isinstance(raw_metadata, Mapping)
            else {}
        )
        metadata.update(
            {
                "semantic_scope_status": status,
                "semantic_scope_complete": status == "complete",
                "semantic_scope_key": domain_scope,
                "semantic_scope_supplement_domain_keys": list(
                    records.domain_keys
                ),
                "semantic_scope_source_revision_id": source_revision_id,
                "semantic_scope_source_revision_digest": (
                    source_revision_digest
                ),
                "semantic_scope_graph_revision": graph_revision,
                "semantic_scope_supplement_node_count": (
                    records.semantic_node_count
                ),
                "semantic_scope_supplement_edge_count": (
                    records.semantic_edge_count
                ),
                "semantic_scope_supplement_declaration_count": (
                    records.declaration_edge_count
                ),
                "evidence_graph_revision": evidence_graph_revision,
            }
        )
        projected["metadata"] = metadata
        return projected

    @staticmethod
    def _augment_domain_inventory(
        *,
        result: Mapping[str, object],
        catalog: CodeCompassDomainSupplementCatalog,
        structural_node_counts: Mapping[str, int],
    ) -> Mapping[str, object]:
        summaries = {domain.domain_key: domain for domain in catalog.domains}
        projected = dict(result)
        raw_facets = projected.get("facets")
        if not isinstance(raw_facets, Mapping):
            return projected
        facets = dict(raw_facets)
        raw_domains = facets.get("domains")
        if not isinstance(raw_domains, Mapping):
            return projected
        domains = dict(raw_domains)
        raw_items = domains.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(
            raw_items,
            (str, bytes),
        ):
            return projected
        items: list[object] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                items.append(raw_item)
                continue
            item = dict(raw_item)
            domain_key: str | None = None
            if item.get("depth") == 0 and item.get("source") == "path":
                path = str(item.get("path") or "")
                if path and "/" not in path and "\\" not in path:
                    domain_key = codecompass_semantic_domain_key(path)
            summary = summaries.get(domain_key or "")
            if summary is not None:
                base_count = int(
                    structural_node_counts.get(str(item.get("key") or ""), 0)
                )
                item.update(
                    {
                        "base_node_count": base_count,
                        "semantic_node_count": summary.semantic_node_count,
                        "complete_node_count": (
                            base_count + summary.semantic_node_count
                        ),
                        "semantic_edge_count": summary.semantic_edge_count,
                        "declaration_edge_count": (
                            summary.declaration_edge_count
                        ),
                        "semantic_scope_status": "available",
                    }
                )
            items.append(item)
        domains["items"] = items
        facets["domains"] = domains
        projected["facets"] = facets
        return projected


__all__ = ["CodeCompassDomainSupplementGraphReadCoordinator"]
