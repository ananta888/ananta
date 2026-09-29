from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.services.codecompass_domain_supplement import (
    CodeCompassDomainSupplementBinding,
    CodeCompassDomainSupplementCatalog,
    CodeCompassDomainSupplementRecords,
    CodeCompassDomainSupplementSummary,
    SqliteCodeCompassDomainSupplementReader,
)
from agent.services.codecompass_domain_supplement_graph_read import (
    CodeCompassDomainSupplementGraphReadCoordinator,
)
from agent.services.codecompass_domain_supplement_overlay import (
    CodeCompassDomainSupplementOverlayStore,
)
from agent.services.codecompass_graph_artifact_resolver import (
    ResolvedCodeCompassDomainSupplement,
)
from agent.services.codecompass_graph_domain_catalog_service import (
    CodeCompassGraphDomainCatalogService,
)
from agent.services.codecompass_graph_read_service import (
    CodeCompassGraphReadService,
)
from agent.services.codecompass_graph_window_service import (
    CodeCompassGraphWindowService,
)
from agent.services.knowledge_index_worker_artifact_service import (
    KnowledgeIndexWorkerArtifactService,
)
from agent.services.source_control_production_adapters import (
    HubSourceControlOperationsAdapter,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
    DOMAIN_SUPPLEMENT_SCHEMA,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    codecompass_semantic_domain_key,
)
from worker.retrieval.codecompass_domain_supplement import (
    CodeCompassDomainSupplementSourceWriter,
    SemanticDomainIdentity,
    WorkerCodeCompassDomainSupplementMaterializer,
)

_GRAPH_REVISION = "sha256:" + "a" * 64
_SOURCE_REVISION_ID = "srev_" + "b" * 64
_SOURCE_REVISION_DIGEST = "c" * 64
_SOURCE_ID = f"bound-source:{_SOURCE_REVISION_ID}"


def _published_supplement(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    domain_key = codecompass_semantic_domain_key("src")
    source = tmp_path / "domain-source.sqlite3"
    with CodeCompassDomainSupplementSourceWriter(source) as writer:
        writer.add_domain(
            SemanticDomainIdentity(
                domain_key=domain_key,
                domain_kind="top_level_path",
                domain_label="src",
            ),
            source_file_count=1,
        )
        writer.add_node(
            domain_key=domain_key,
            record={
                "id": "semantic:src:work",
                "kind": "symbol",
                "file": "src/work.py",
            },
        )
        writer.add_semantic_edge(
            domain_key=domain_key,
            record={
                "source": "semantic:src:work",
                "target": "semantic:src:dependency",
                "type": "calls",
            },
        )
        writer.add_declaration_edge(
            domain_key=domain_key,
            record={
                "source": "source:src/work.py",
                "target": "semantic:src:work",
                "type": "declares",
            },
        )
        writer.finalize()
    destination = tmp_path / DOMAIN_SUPPLEMENT_FILENAME
    proof = WorkerCodeCompassDomainSupplementMaterializer().materialize(
        source_path=source,
        output_path=destination,
        graph_revision=_GRAPH_REVISION,
        source_scope="repo_path",
        knowledge_index_id="index-1",
        source_id=_SOURCE_ID,
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
    )
    return destination, proof


def _binding(path: Path, proof: dict[str, object]) -> CodeCompassDomainSupplementBinding:
    return CodeCompassDomainSupplementBinding(
        knowledge_index_id="index-1",
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
        graph_revision=_GRAPH_REVISION,
        artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        logical_content_hash=str(proof["graph_content_hash"]),
        source_scope="repo_path",
        source_id=_SOURCE_ID,
    )


def test_hub_domain_summary_allows_raw_stream_larger_than_artifact_envelope() -> None:
    summary = SqliteCodeCompassDomainSupplementReader._summary(
        {
            "domain_key": codecompass_semantic_domain_key("src"),
            "domain_kind": "top_level_path",
            "domain_label": "src",
            "source_file_count": 1,
            "semantic_node_count": 1,
            "semantic_edge_count": 0,
            "declaration_edge_count": 0,
            "semantic_node_bytes": (
                MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES + 1
            ),
            "semantic_edge_bytes": 0,
            "declaration_edge_bytes": 0,
            "complete": 1,
        }
    )

    assert (
        summary.semantic_node_bytes
        == MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES + 1
    )


def test_worker_sqlite_round_trips_through_strict_hub_reader(
    tmp_path: Path,
) -> None:
    path, proof = _published_supplement(tmp_path)
    binding = _binding(path, proof)
    reader = SqliteCodeCompassDomainSupplementReader()

    catalog = reader.validate_artifact(path=path, binding=binding)
    selected = reader.load_domains(
        path=path,
        domain_keys=[codecompass_semantic_domain_key("src")],
        binding=binding,
    )

    assert catalog.logical_content_hash == proof["graph_content_hash"]
    assert selected.domain_keys == (codecompass_semantic_domain_key("src"),)
    assert selected.semantic_node_count == 1
    assert selected.semantic_edge_count == 1
    assert selected.declaration_edge_count == 1


def test_hub_admission_binds_optional_seventh_graph_reference(
    tmp_path: Path,
) -> None:
    class _Deadline:
        calls = 0

        def require_remaining_seconds(self) -> float:
            self.calls += 1
            return 30.0

    supplement_path, proof = _published_supplement(tmp_path)
    graph_path = tmp_path / "cc_graph_index.json"
    graph_path.write_text(
        json.dumps(
            {
                "state": {
                    "schema": "codecompass_graph_index.v1",
                    "manifest_hash": _GRAPH_REVISION,
                },
                "nodes": [],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    metrics_path = tmp_path / "cc_graph_index.visual_metrics.json"
    unsigned_metrics = {
        "schema": "graph_visual_metrics.v1",
        "graph_revision": _GRAPH_REVISION,
    }
    metrics_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                unsigned_metrics,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    metrics_path.write_text(
        json.dumps({**unsigned_metrics, "content_hash": metrics_hash}),
        encoding="utf-8",
    )
    supplement_sha = hashlib.sha256(supplement_path.read_bytes()).hexdigest()
    by_role = {
        "graph_index": {
            "artifact_schema": "codecompass_graph_index.v1",
            "media_type": "application/vnd.ananta.codecompass-graph-index+json",
            "graph_revision": _GRAPH_REVISION,
            "graph_content_hash": "sha256:" + hashlib.sha256(graph_path.read_bytes()).hexdigest(),
        },
        "graph_visual_metrics": {
            "artifact_schema": "graph_visual_metrics.v1",
            "media_type": "application/vnd.ananta.codecompass-graph-visual-metrics+json",
            "graph_revision": _GRAPH_REVISION,
            "graph_content_hash": metrics_hash,
        },
        DOMAIN_SUPPLEMENT_OUTPUT_ROLE: {
            "artifact_schema": DOMAIN_SUPPLEMENT_SCHEMA,
            "media_type": DOMAIN_SUPPLEMENT_MEDIA_TYPE,
            "graph_revision": _GRAPH_REVISION,
            "graph_content_hash": proof["graph_content_hash"],
            "source_revision_id": _SOURCE_REVISION_ID,
            "source_revision_digest": _SOURCE_REVISION_DIGEST,
            "sha256": supplement_sha,
            "domain_count": 1,
            "semantic_node_count": 1,
            "semantic_edge_count": 1,
            "declaration_edge_count": 1,
        },
    }
    service = KnowledgeIndexWorkerArtifactService(
        downloader=object(),
        knowledge_index_repository=object(),
        knowledge_index_run_repository=object(),
        output_root=tmp_path,
    )
    deadline = _Deadline()

    admitted = service._validate_graph_artifacts(
        by_role=by_role,
        staged_paths={
            "graph_index": graph_path,
            "graph_visual_metrics": metrics_path,
            DOMAIN_SUPPLEMENT_OUTPUT_ROLE: supplement_path,
        },
        knowledge_index_id="index-1",
        source_scope="repo_path",
        source_id=_SOURCE_ID,
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
        transfer_deadline=deadline,
    )

    assert admitted["graph_revision"] == _GRAPH_REVISION
    assert admitted["domain_supplement"]["content_hash"] == proof["graph_content_hash"]
    assert deadline.calls > 0


def test_overlay_keeps_worker_evidence_revision_and_adds_complete_records(
    tmp_path: Path,
) -> None:
    path, proof = _published_supplement(tmp_path)
    reader = SqliteCodeCompassDomainSupplementReader()
    records = reader.load_domains(
        path=path,
        domain_keys=[codecompass_semantic_domain_key("src")],
        binding=_binding(path, proof),
    )

    class _BaseStore:
        def load_visual_metrics(self):
            return None

    store = CodeCompassDomainSupplementOverlayStore(
        base_store=_BaseStore(),
        base_payload={
            "state": {"manifest_hash": _GRAPH_REVISION},
            "nodes": [{"id": "source:src/work.py", "path": "src/work.py"}],
            "edges": [],
            "semantic_nodes": [],
            "semantic_edges": [],
        },
        supplement=records,
        evidence_graph_revision=_GRAPH_REVISION,
    )

    payload = store.load()
    assert payload["state"]["manifest_hash"] == _GRAPH_REVISION
    assert len(payload["semantic_nodes"]) == 1
    assert len(payload["semantic_edges"]) == 1
    assert len(payload["edges"]) == 1


def test_source_graph_loads_lazily_and_filters_partition_to_subdomain(
    tmp_path: Path,
) -> None:
    domain_key = codecompass_semantic_domain_key("src")
    binding = CodeCompassDomainSupplementBinding(
        knowledge_index_id="index-1",
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
        graph_revision=_GRAPH_REVISION,
        artifact_sha256="d" * 64,
        logical_content_hash="sha256:" + "e" * 64,
        source_scope="repo_path",
        source_id=_SOURCE_ID,
    )
    records = CodeCompassDomainSupplementRecords(
        graph_revision=_GRAPH_REVISION,
        logical_content_hash=binding.logical_content_hash,
        domain_keys=(domain_key,),
        nodes=(
            {
                "id": "semantic:sub",
                "kind": "symbol",
                "file": "src/sub/a.py",
            },
            {
                "id": "semantic:other",
                "kind": "symbol",
                "file": "src/other/b.py",
            },
        ),
        semantic_edges=(
            {
                "source": "source:sub",
                "target": "semantic:external",
                "type": "calls",
            },
        ),
        declaration_edges=(),
        semantic_node_count=2,
        semantic_edge_count=1,
        declaration_edge_count=0,
    )

    class _Reader:
        calls = 0

        def load_domains(self, **_kwargs):
            self.calls += 1
            return records

    class _Store:
        payload = {
            "state": {"manifest_hash": _GRAPH_REVISION},
            "nodes": [
                {
                    "id": "source:sub",
                    "kind": "file",
                    "path": "src/sub/a.py",
                },
                {
                    "id": "source:other",
                    "kind": "file",
                    "path": "src/other/b.py",
                },
            ],
            "edges": [],
            "semantic_nodes": [],
            "semantic_edges": [],
        }

        def load(self):
            return self.payload

        def load_visual_metrics(self):
            return None

    class _Projection:
        def project(self, **kwargs):
            return {
                "nodes": list(kwargs["nodes"]),
                "edges": list(kwargs["edges"]),
                "metadata": dict(kwargs["metadata"]),
                "graph_revision": kwargs["graph_revision"],
            }

    domains = CodeCompassGraphDomainCatalogService()
    scope = next(
        facet.key
        for facet in domains.catalog(nodes=_Store.payload["nodes"]).facets
        if facet.source == "path" and facet.path == "src/sub"
    )
    reader = _Reader()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._active_index = lambda **_kwargs: type(
        "Index",
        (),
        {
            "id": "index-1",
            "index_metadata": {
                "source_scope": "repo_path",
                "source_id": _SOURCE_ID,
                "source_revision_id": _SOURCE_REVISION_ID,
                "source_revision_digest": _SOURCE_REVISION_DIGEST,
            },
        },
    )()
    adapter._graph_store = lambda _index: _Store()
    adapter._artifact_projection = lambda _index: {"state": "available"}
    adapter._resolver = type(
        "Resolver",
        (),
        {
            "resolve_domain_supplement": lambda _self, _index: (
                ResolvedCodeCompassDomainSupplement(
                    path=tmp_path / DOMAIN_SUPPLEMENT_FILENAME,
                    binding=binding,
                )
            )
        },
    )()
    adapter._domain_supplements = reader
    adapter._graph_domains = domains
    adapter._validate_domain_supplement_binding = lambda **_kwargs: None
    adapter._graph_read = CodeCompassGraphReadService(
        projection=_Projection(),
        window=CodeCompassGraphWindowService(),
        domains=domains,
    )

    adapter.graph(parameters={"view": "default"})
    assert reader.calls == 0

    scoped = adapter.graph(
        parameters={
            "view": "topology",
            "domain_scope": scope,
            "include_subdomains": False,
            "limit": 100,
        }
    )

    assert reader.calls == 1
    assert scoped["metadata"]["semantic_scope_status"] == "complete"
    assert scoped["metadata"]["semantic_scope_supplement_node_count"] == 1
    assert scoped["metadata"]["semantic_scope_supplement_edge_count"] == 1
    assert scoped["metadata"]["scope_unresolved_edge_count"] == 1
    assert scoped["metadata"]["evidence_graph_revision"] == _GRAPH_REVISION
    assert {node["id"] for node in scoped["nodes"]} == {
        "source:sub",
        "semantic:sub",
    }

    staged_parameters = {
        "view": "staged",
        "stage": "nodes",
        "domain_scope": scope,
        "include_subdomains": False,
        "limit": 1,
    }
    first_page = adapter.graph(parameters=staged_parameters)
    second_page = adapter.graph(
        parameters={
            **staged_parameters,
            "cursor": first_page["metadata"]["next_cursor"],
        }
    )

    assert first_page["metadata"]["delivery_returned"] == 1
    assert second_page["metadata"]["delivery_returned"] == 1
    assert second_page["metadata"]["next_cursor"] is None
    assert reader.calls == 1



def test_domain_inventory_does_not_count_bounded_semantic_nodes_as_base_nodes(
    tmp_path: Path,
) -> None:
    domain_key = codecompass_semantic_domain_key("src")
    binding = CodeCompassDomainSupplementBinding(
        knowledge_index_id="index-1",
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
        graph_revision=_GRAPH_REVISION,
        artifact_sha256="d" * 64,
        logical_content_hash="sha256:" + "e" * 64,
        source_scope="repo_path",
        source_id=_SOURCE_ID,
    )

    class _Store:
        payload = {
            "state": {"manifest_hash": _GRAPH_REVISION},
            "nodes": [
                {"id": "source:src/work.py", "kind": "file", "path": "src/work.py"},
            ],
            # This is only the bounded semantic overview. It is present in the
            # base inventory but must not be counted again next to the complete
            # supplement count.
            "semantic_nodes": [
                {
                    "id": "semantic:bounded",
                    "kind": "symbol",
                    "file": "src/work.py",
                },
            ],
            "edges": [],
            "semantic_edges": [],
        }

        def load(self):
            return self.payload

        def load_visual_metrics(self):
            return None

    class _Reader:
        def catalog(self, **_kwargs):
            return CodeCompassDomainSupplementCatalog(
                graph_revision=_GRAPH_REVISION,
                logical_content_hash=binding.logical_content_hash,
                domains=(
                    CodeCompassDomainSupplementSummary(
                        domain_key=domain_key,
                        domain_kind="top_level_path",
                        domain_label="src",
                        source_file_count=1,
                        semantic_node_count=3,
                        semantic_edge_count=2,
                        declaration_edge_count=1,
                        semantic_node_bytes=0,
                        semantic_edge_bytes=0,
                        declaration_edge_bytes=0,
                        complete=True,
                    ),
                ),
            )

        def load_domains(self, **_kwargs):
            raise AssertionError("inventory_must_not_expand_domain_payloads")

    domains = CodeCompassGraphDomainCatalogService()
    coordinator = CodeCompassDomainSupplementGraphReadCoordinator(
        graph_read=CodeCompassGraphReadService(
            projection=object(),
            window=CodeCompassGraphWindowService(),
            domains=domains,
        ),
        graph_domains=domains,
        domain_supplements=_Reader(),
    )

    inventory = coordinator.read(
        index_id="index-1",
        base_store=_Store(),
        parameters={"view": "inventory", "limit": 100},
        artifact_status={"state": "available"},
        resolved=ResolvedCodeCompassDomainSupplement(
            path=tmp_path / DOMAIN_SUPPLEMENT_FILENAME,
            binding=binding,
        ),
    )
    src = next(
        item
        for item in inventory["facets"]["domains"]["items"]
        if item["depth"] == 0 and item["path"] == "src"
    )

    assert src["subtree_node_count"] == 2
    assert src["base_node_count"] == 1
    assert src["semantic_node_count"] == 3
    assert src["complete_node_count"] == 4
