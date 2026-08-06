from __future__ import annotations

from agent.services.codecompass_graph_domain_catalog_service import (
    CodeCompassGraphDomainCatalogService,
)
from agent.services.codecompass_graph_projection_service import (
    CodeCompassGraphProjectionService,
)
from agent.services.codecompass_graph_read_service import (
    CodeCompassGraphReadError,
    CodeCompassGraphReadService,
)
from agent.services.codecompass_graph_window_service import (
    CodeCompassGraphWindowService,
)


class _Store:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def load(self) -> dict:
        return self.payload

    def load_visual_metrics(self):
        return None


class _Projection:
    def project(self, **kwargs):
        return {
            "nodes": list(kwargs["nodes"]),
            "edges": list(kwargs["edges"]),
            "metadata": dict(kwargs["metadata"]),
            "warnings": list(kwargs.get("warnings") or []),
        }


class _CountingDomains(CodeCompassGraphDomainCatalogService):
    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare(self, *, nodes):
        self.prepare_calls += 1
        return super().prepare(nodes=nodes)


class _CountingRevisionReadService(CodeCompassGraphReadService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.content_revision_calls = 0

    def _compute_content_graph_revision(self, *, nodes, edges):
        self.content_revision_calls += 1
        return super()._compute_content_graph_revision(nodes=nodes, edges=edges)


class _CountingProjection(CodeCompassGraphProjectionService):
    def __init__(self) -> None:
        self.prepare_calls = 0
        self.page_contexts: list[dict] = []

    def prepare_edge_population(self, edges):
        self.prepare_calls += 1
        return super().prepare_edge_population(edges)

    def project(self, **kwargs):
        if kwargs.get("metadata", {}).get("stage") == "edges":
            self.page_contexts.append(
                {
                    "edge_count": len(kwargs["edges"]),
                    "edge_population": kwargs.get("edge_population"),
                    "prepared": kwargs.get("prepared_edge_population"),
                    "indices": tuple(kwargs.get("edge_population_indices") or ()),
                }
            )
        return super().project(**kwargs)


def _service(*, domains=None, projection=None) -> CodeCompassGraphReadService:
    return CodeCompassGraphReadService(
        projection=projection or _Projection(),
        window=CodeCompassGraphWindowService(),
        domains=domains or CodeCompassGraphDomainCatalogService(),
    )


def _read(
    service: CodeCompassGraphReadService,
    store: _Store,
    **parameters,
):
    return service.read(
        index_id="index-1",
        store=store,
        parameters=parameters,
        artifact_status={"state": "available"},
    )


def test_legacy_content_revision_rejects_cursor_before_unknown_scope() -> None:
    domains = _CountingDomains()
    service = _service(domains=domains)
    store = _Store(
        {
            "state": {},
            "nodes": [
                {"id": "one", "domain_id": "sales"},
                {"id": "two", "domain_id": "sales.orders"},
            ],
            "edges": [],
        }
    )
    first = _read(service, store, view="staged", stage="nodes", limit=1)
    cursor = first["metadata"]["next_cursor"]
    assert cursor
    assert domains.prepare_calls == 1

    store.payload = {
        **store.payload,
        "nodes": [
            *store.payload["nodes"],
            {"id": "three", "domain_id": "sales.orders"},
        ],
    }
    try:
        _read(
            service,
            store,
            view="staged",
            stage="nodes",
            limit=1,
            cursor=cursor,
            domain_scope="domain_id:" + "0" * 64,
        )
    except CodeCompassGraphReadError as exc:
        assert exc.reason_code == "graph_cursor_stale"
        assert exc.status_code == 409
    else:
        raise AssertionError("changed legacy graph accepted a stale cursor")
    assert domains.prepare_calls == 1


def test_content_revision_invalidates_same_manifest_without_rehashing_pages() -> None:
    service = _CountingRevisionReadService(
        projection=CodeCompassGraphProjectionService(),
        window=CodeCompassGraphWindowService(),
        domains=CodeCompassGraphDomainCatalogService(),
    )
    first_store = _Store(
        {
            "state": {"manifest_hash": "manifest-static"},
            "nodes": [
                {"id": "one", "domain_id": "sales"},
                {"id": "two", "domain_id": "support"},
            ],
            "edges": [],
        }
    )
    first = _read(service, first_store, view="staged", stage="nodes", limit=1)
    cursor = first["metadata"]["next_cursor"]
    assert cursor
    first_content_revision = first["metadata"]["content_graph_revision"]
    assert first["metadata"]["evidence_graph_revision"] == "manifest-static"

    _read(service, first_store, view="inventory", limit=1)
    assert service.content_revision_calls == 1

    changed_store = _Store(
        {
            "state": {"manifest_hash": "manifest-static"},
            "nodes": [
                {"id": "one", "domain_id": "sales"},
                {"id": "changed", "domain_id": "support"},
            ],
            "edges": [],
        }
    )
    try:
        _read(
            service,
            changed_store,
            view="staged",
            stage="nodes",
            limit=1,
            cursor=cursor,
        )
    except CodeCompassGraphReadError as exc:
        assert exc.reason_code == "graph_cursor_stale"
        assert exc.status_code == 409
    else:
        raise AssertionError("same manifest accepted changed graph content")
    changed_inventory = _read(service, changed_store, view="inventory", limit=1)
    assert changed_inventory["graph_revision"] != first_content_revision
    assert changed_inventory["metadata"]["content_graph_revision"] == changed_inventory["graph_revision"]
    assert service.content_revision_calls == 2


def test_prepared_inventory_is_reused_for_pages_and_scope_reads() -> None:
    domains = _CountingDomains()
    service = _service(domains=domains)
    store = _Store(
        {
            "state": {"manifest_hash": "sha256:revision"},
            "nodes": [
                {"id": "sales", "domain_id": "sales"},
                {"id": "orders", "domain_id": "sales.orders"},
                {"id": "support", "domain_id": "support"},
            ],
            "edges": [
                {
                    "source_id": "sales",
                    "target_id": "orders",
                    "edge_type": "contains",
                }
            ],
        }
    )
    first = _read(service, store, view="inventory", limit=1)
    scope_key = first["facets"]["domains"]["items"][0]["key"]
    _read(
        service,
        store,
        view="inventory",
        limit=1,
        cursor=first["facets"]["domains"]["next_cursor"],
    )
    scoped = _read(
        service,
        store,
        view="topology",
        limit=10,
        domain_scope=scope_key,
        include_subdomains=True,
    )

    assert domains.prepare_calls == 1
    assert scoped["metadata"]["scope_total_nodes"] == 2


def test_staged_edges_keep_unresolved_edges_and_global_page_metadata() -> None:
    projection = _CountingProjection()
    service = _service(projection=projection)
    store = _Store(
        {
            "state": {"manifest_hash": "sha256:revision"},
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [
                {"source_id": "a", "target_id": "b", "edge_type": "calls"},
                {"source_id": "a", "target_id": "b", "edge_type": "calls"},
                {"source_id": "a", "target_id": "b", "edge_type": "calls"},
                {
                    "source_id": "a",
                    "target_id": "missing",
                    "edge_type": "calls",
                },
            ],
        }
    )
    delivered: list[dict] = []
    cursor = None
    while True:
        page = _read(
            service,
            store,
            view="staged",
            stage="edges",
            limit=1,
            max_edges=1,
            cursor=cursor,
        )
        delivered.extend(page["edges"])
        cursor = page["metadata"]["next_cursor"]
        if cursor is None:
            break

    assert len(delivered) == 4
    assert len({edge["edge_id"] for edge in delivered}) == 4
    parallel = [edge for edge in delivered if edge["target_id"] == "b"]
    assert [edge["attributes"]["parallel_index"] for edge in parallel] == [
        0,
        1,
        2,
    ]
    assert {edge["attributes"]["parallel_count"] for edge in parallel} == {3}
    assert any(edge["target_id"] == "missing" for edge in delivered)
    assert projection.prepare_calls == 1
    assert len(projection.page_contexts) == 4
    assert all(context["edge_count"] == 1 for context in projection.page_contexts)
    assert all(context["edge_population"] is None for context in projection.page_contexts)
    assert len({id(context["prepared"]) for context in projection.page_contexts}) == 1
    assert [context["indices"] for context in projection.page_contexts] == [
        (0,),
        (1,),
        (2,),
        (3,),
    ]


def test_snapshot_cache_is_bounded_by_revision_count() -> None:
    domains = _CountingDomains()
    service = CodeCompassGraphReadService(
        projection=_Projection(),
        window=CodeCompassGraphWindowService(),
        domains=domains,
        maximum_cached_revisions=1,
    )
    store = _Store(
        {
            "state": {"manifest_hash": "sha256:one"},
            "nodes": [{"id": "one", "domain_id": "one"}],
            "edges": [],
        }
    )
    _read(service, store, view="inventory", limit=1)
    store.payload = {
        "state": {"manifest_hash": "sha256:two"},
        "nodes": [{"id": "two", "domain_id": "two"}],
        "edges": [],
    }
    _read(service, store, view="inventory", limit=1)
    store.payload = {
        "state": {"manifest_hash": "sha256:one"},
        "nodes": [{"id": "one", "domain_id": "one"}],
        "edges": [],
    }
    _read(service, store, view="inventory", limit=1)

    assert domains.prepare_calls == 3
