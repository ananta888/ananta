from __future__ import annotations

import unittest

from rag_helper.domain_discovery.boundaries import (
    WARNING_HETEROGENEOUS_ROOT,
    WARNING_LAYER_SPANS_DOMAINS,
    WARNING_MUTUAL_COUPLING,
    compute_boundary_metrics,
)
from rag_helper.domain_discovery.clustering import cluster_domains
from rag_helper.domain_discovery.graph_model import DomainGraph
from rag_helper.domain_discovery.inputs import AnalysisInputs


def _build_inputs(
    *, nodes: list[dict], edges: list[dict] | None = None
) -> AnalysisInputs:
    inputs = AnalysisInputs(
        out_dir=None,
        graph_nodes=[
            n
            for n in nodes
            if n.get("id") and n.get("kind") is not None and n.get("file")
        ],
        graph_edges=[
            e
            for e in (edges or [])
            if e.get("source") and e.get("target") and e.get("type")
        ],
    )
    return inputs


def _record(rid: str, file: str, **extra) -> dict:
    base = {"id": rid, "kind": "py_module", "file": file}
    base.update(extra)
    return base


class TestMutualCoupling(unittest.TestCase):
    def test_two_domains_with_bidirectional_edges_warn(self) -> None:
        nodes = (
            [_record(f"a{i}", f"alpha/f{i}.py") for i in range(4)]
            + [_record(f"b{i}", f"beta/f{i}.py") for i in range(4)]
        )
        edges = []
        for i in range(3):
            edges.append({"source": f"a{i}", "target": f"b{i}", "type": "field_type_uses"})
            edges.append({"source": f"b{i}", "target": f"a{i}", "type": "injects_dependency"})
        graph = DomainGraph.build(_build_inputs(nodes=nodes, edges=edges))
        clustering = cluster_domains(graph)
        result = compute_boundary_metrics(clustering, graph, mutual_threshold=3)

        warning_types = {w["warning_type"] for w in result.boundary_warnings}
        self.assertIn(WARNING_MUTUAL_COUPLING, warning_types)
        mutual = [
            w
            for w in result.boundary_warnings
            if w["warning_type"] == WARNING_MUTUAL_COUPLING
        ]
        self.assertEqual(len(mutual), 1)
        domains = {mutual[0]["source_domain"], mutual[0]["target_domain"]}
        self.assertEqual(domains, {"alpha", "beta"})
        self.assertEqual(mutual[0]["evidence"]["a_to_b_edges"], 3)
        self.assertEqual(mutual[0]["evidence"]["b_to_a_edges"], 3)

    def test_below_threshold_no_warning(self) -> None:
        nodes = (
            [_record(f"a{i}", f"alpha/f{i}.py") for i in range(4)]
            + [_record(f"b{i}", f"beta/f{i}.py") for i in range(4)]
        )
        edges = [
            {"source": "a0", "target": "b0", "type": "field_type_uses"},
            {"source": "a1", "target": "b1", "type": "field_type_uses"},
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes, edges=edges))
        clustering = cluster_domains(graph)
        result = compute_boundary_metrics(clustering, graph, mutual_threshold=3)
        self.assertEqual(
            [w for w in result.boundary_warnings if w["warning_type"] == WARNING_MUTUAL_COUPLING],
            [],
        )


class TestExternalDomainRefs(unittest.TestCase):
    def test_external_domain_refs_attached_to_metrics(self) -> None:
        nodes = (
            [_record(f"a{i}", f"alpha/f{i}.py") for i in range(4)]
            + [_record(f"b{i}", f"beta/f{i}.py") for i in range(4)]
        )
        edges = [
            {"source": "a0", "target": "b0", "type": "field_type_uses"},
            {"source": "a0", "target": "b1", "type": "field_type_uses"},
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes, edges=edges))
        clustering = cluster_domains(graph)
        result = compute_boundary_metrics(clustering, graph)
        by_id = {c.domain_id: c for c in result.candidates}
        # 2 outbound edges (a -> b) and 0 inbound (no b -> a in this fixture).
        self.assertEqual(by_id["alpha"].metrics["external_domain_refs"], {"beta": 2})
        self.assertEqual(by_id["beta"].metrics["external_domain_refs"], {"alpha": 0})


class TestLayerSpansDomains(unittest.TestCase):
    def test_layer_shared_by_three_domains_warns(self) -> None:
        # Build three clusters whose members all share a role label
        # (controller -> 'api' via gem_partitions._classify_domain).
        nodes = []
        records = []
        for d in ("alpha", "beta", "gamma"):
            for i in range(4):
                rec = _record(
                    f"{d}{i}", f"{d}/f{i}.py", role_labels=["controller"]
                )
                nodes.append(rec)
                records.append(rec)
        graph = DomainGraph.build(_build_inputs(nodes=nodes))
        clustering = cluster_domains(graph, records=records)
        result = compute_boundary_metrics(clustering, graph, layer_min_domains=3)
        layer_warnings = [
            w
            for w in result.boundary_warnings
            if w["warning_type"] == WARNING_LAYER_SPANS_DOMAINS
        ]
        self.assertTrue(layer_warnings)
        layers = {w["source_domain"] for w in layer_warnings}
        self.assertIn("api", layers)
        # Each warning carries the list of spanning domains.
        api_warning = next(w for w in layer_warnings if w["source_domain"] == "api")
        self.assertEqual(
            sorted(api_warning["evidence"]["domains"]),
            ["alpha", "beta", "gamma"],
        )


class TestHeterogeneousRoot(unittest.TestCase):
    def test_cluster_with_few_internal_edges_warns(self) -> None:
        nodes = [_record(f"x{i}", f"alpha/f{i}.py") for i in range(4)]
        graph = DomainGraph.build(_build_inputs(nodes=nodes))
        clustering = cluster_domains(graph)
        result = compute_boundary_metrics(
            clustering, graph, heterogeneous_min_records=3
        )
        heterogeneous = [
            w
            for w in result.boundary_warnings
            if w["warning_type"] == WARNING_HETEROGENEOUS_ROOT
        ]
        self.assertEqual(len(heterogeneous), 1)
        self.assertEqual(heterogeneous[0]["source_domain"], "alpha")


class TestStability(unittest.TestCase):
    def test_warning_output_is_byte_stable(self) -> None:
        nodes = (
            [_record(f"a{i}", f"alpha/f{i}.py") for i in range(4)]
            + [_record(f"b{i}", f"beta/f{i}.py") for i in range(4)]
        )
        edges = [
            {"source": "a0", "target": "b0", "type": "field_type_uses"},
            {"source": "b0", "target": "a0", "type": "field_type_uses"},
            {"source": "a0", "target": "b1", "type": "field_type_uses"},
            {"source": "b1", "target": "a0", "type": "field_type_uses"},
        ]
        graph_a = DomainGraph.build(_build_inputs(nodes=list(nodes), edges=list(edges)))
        graph_b = DomainGraph.build(_build_inputs(nodes=list(reversed(nodes)), edges=list(reversed(edges))))
        clustering_a = cluster_domains(graph_a)
        clustering_b = cluster_domains(graph_b)
        result_a = compute_boundary_metrics(clustering_a, graph_a)
        result_b = compute_boundary_metrics(clustering_b, graph_b)
        self.assertEqual(result_a.boundary_warnings, result_b.boundary_warnings)
        self.assertEqual(result_a.coupling_pairs, result_b.coupling_pairs)


if __name__ == "__main__":
    unittest.main()
