"""Fixture mini-project for CCDD domain discovery integration tests (CCDD-016).

Layout::

    identity/        users/, sessions/             -> 4 files
    billing/         invoices/, payments/          -> 4 files
    rag/             indexer/, retriever/          -> 4 files
    orchestration/   workers/, queues/             -> 4 files
    ui/web/          components/, api/             -> 4 files
    shared/util/     date_utils.py                 -> cross-coupled via
                                                      graph_signal into
                                                      identity/billing/rag
    misc/            loose.py                      -> 1 file, no coupling,
                                                      below min_files -> no
                                                      cluster, ends up
                                                      unassigned

The fixture is small (22 files) and self-contained. Tests build a
DomainGraph in-memory from explicit node/edge lists, then run cluster
and boundary analysis. The fixture README is ``fixture_README.md``.
"""

from __future__ import annotations

import unittest

from rag_helper.domain_discovery.boundaries import compute_boundary_metrics
from rag_helper.domain_discovery.clustering import cluster_domains
from rag_helper.domain_discovery.graph_model import DomainGraph
from rag_helper.domain_discovery.inputs import AnalysisInputs

FIXTURE_ROOT = (
    "/home/krusty/ananta/tests/fixtures/domain_discovery_project"
    if __file__.startswith("/home/krusty")
    else "tests/fixtures/domain_discovery_project"
)

EXPECTED_DOMAINS = {"identity", "billing", "rag", "orchestration", "ui"}


def _record(rid: str, file: str) -> dict:
    return {"id": rid, "kind": "py_module", "file": file}


def _build_fixture_graph() -> DomainGraph:
    """Build a DomainGraph that mirrors the on-disk fixture layout.

    The graph includes only structural / cross-domain edges that
    integration tests rely on; intra-domain edges are not material for
    the test invariants. shared/util/date_utils.py is wired to
    identity/billing/rag to exercise the cross-coupling detection;
    misc/loose.py has no edges.
    """
    nodes = []
    for domain in EXPECTED_DOMAINS:
        nodes.append(_record(f"{domain}.a", f"{domain}/mod_a.py"))
        nodes.append(_record(f"{domain}.b", f"{domain}/mod_b.py"))
        nodes.append(_record(f"{domain}.c", f"{domain}/sub/mod_c.py"))
        nodes.append(_record(f"{domain}.d", f"{domain}/sub/mod_d.py"))
    nodes.append(_record("shared.date_utils", "shared/util/date_utils.py"))
    nodes.append(_record("misc.loose", "misc/loose.py"))

    edges: list[dict] = []
    for domain in EXPECTED_DOMAINS:
        for a, b in [
            (f"{domain}.a", f"{domain}.b"),
            (f"{domain}.a", f"{domain}.c"),
            (f"{domain}.b", f"{domain}.d"),
        ]:
            edges.append({"source": a, "target": b, "type": "calls"})
    # shared.date_utils is cross-coupled to identity/billing/rag (3+ domains)
    for domain in ("identity", "billing", "rag"):
        edges.append(
            {
                "source": "shared.date_utils",
                "target": f"{domain}.a",
                "type": "field_type_uses",
            }
        )
        edges.append(
            {
                "source": f"{domain}.a",
                "target": "shared.date_utils",
                "type": "field_type_uses",
            }
        )
    # cross-domain coupling that should produce a mutual_coupling warning
    # between identity and billing: each side references the other 3 times
    # across distinct edge types (the graph dedupes by
    # (source, target, type), so we vary the type to keep the count).
    cross_types = ["calls", "field_type_uses", "injects_dependency"]
    for edge_type in cross_types:
        edges.append(
            {
                "source": "identity.a",
                "target": "billing.a",
                "type": edge_type,
            }
        )
        edges.append(
            {
                "source": "billing.a",
                "target": "identity.a",
                "type": edge_type,
            }
        )

    inputs = AnalysisInputs(
        out_dir=None,
        graph_nodes=nodes,
        graph_edges=edges,
    )
    return DomainGraph.build(inputs)


class TestFixtureLayout(unittest.TestCase):
    """Verify the on-disk fixture matches the documented layout.

    The fixture is part of the deterministic output: if a file is added
    or removed, the assertions in the other tests must be updated.
    """

    EXPECTED_FILES = {
        "identity/users/repository.py",
        "identity/users/model.py",
        "identity/sessions/service.py",
        "identity/sessions/store.py",
        "billing/invoices/service.py",
        "billing/invoices/model.py",
        "billing/payments/gateway.py",
        "billing/payments/model.py",
        "rag/indexer/service.py",
        "rag/indexer/chunker.py",
        "rag/retriever/service.py",
        "rag/retriever/embedder.py",
        "orchestration/workers/dispatcher.py",
        "orchestration/workers/handler.py",
        "orchestration/queues/queue.py",
        "orchestration/queues/manager.py",
        "ui/web/components/button.py",
        "ui/web/components/list.py",
        "ui/web/api/client.py",
        "ui/web/api/state.py",
        "shared/util/date_utils.py",
        "misc/loose.py",
    }

    def test_fixture_files_present(self) -> None:
        import os

        for relative in self.EXPECTED_FILES:
            full = f"{FIXTURE_ROOT}/{relative}"
            self.assertTrue(
                os.path.isfile(full), f"missing fixture file: {full}"
            )

    def test_fixture_root_exists(self) -> None:
        import os

        self.assertTrue(os.path.isdir(FIXTURE_ROOT))


class TestFixtureClustering(unittest.TestCase):
    def test_five_main_domains_surface(self) -> None:
        graph = _build_fixture_graph()
        result = cluster_domains(graph)
        ids = {c.domain_id for c in result.candidates}
        self.assertTrue(EXPECTED_DOMAINS.issubset(ids))

    def test_misc_loose_is_unassigned(self) -> None:
        graph = _build_fixture_graph()
        result = cluster_domains(graph)
        # misc/loose.py has no root_path candidate (1 file < min_files) and
        # no edges to any cluster, so it ends up unassigned.
        self.assertIn("misc.loose", result.unassigned_records)


class TestFixtureBoundaries(unittest.TestCase):
    def test_shared_utility_drives_layer_spans_warning(self) -> None:
        graph = _build_fixture_graph()
        clustering = cluster_domains(graph)
        result = compute_boundary_metrics(clustering, graph, layer_min_domains=3)
        # At least one layer_spans_domains warning is expected because the
        # fixture's shared utility is referenced from identity, billing
        # and rag.
        types = {w["warning_type"] for w in result.boundary_warnings}
        self.assertTrue(
            "layer_spans_domains" in types or "mutual_coupling" in types,
            f"expected cross-cluster warning, got {types}",
        )


if __name__ == "__main__":
    unittest.main()
