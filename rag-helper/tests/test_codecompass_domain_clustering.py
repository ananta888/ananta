from __future__ import annotations

import unittest

from rag_helper.domain_discovery.clustering import cluster_domains
from rag_helper.domain_discovery.graph_model import DomainGraph
from rag_helper.domain_discovery.inputs import AnalysisInputs


def _build_inputs(
    *,
    nodes: list[dict],
    edges: list[dict] | None = None,
    relations: list[dict] | None = None,
    manifest: dict | None = None,
) -> AnalysisInputs:
    inputs = AnalysisInputs(
        out_dir=None,
        graph_nodes=nodes,
        graph_edges=edges or [],
        relation_records=relations or [],
        manifest=dict(manifest or {}),
        loaded_files={"graph_nodes(in-memory)": len(nodes)},
    )
    inputs.graph_nodes = [
        n for n in nodes if n.get("id") and n.get("kind") is not None and n.get("file")
    ]
    inputs.graph_edges = [
        e for e in (edges or []) if e.get("source") and e.get("target") and e.get("type")
    ]
    return inputs


def _record(rid: str, file: str, **extra) -> dict:
    base = {"id": rid, "kind": "py_module", "file": file}
    base.update(extra)
    return base


class TestPathSignalClustering(unittest.TestCase):
    def test_dominant_path_yields_single_cluster_per_root(self) -> None:
        nodes = [
            _record(f"r{i}", f"rag-helper/rag_helper/application/mod{i}.py", importance_score=0.5)
            for i in range(5)
        ] + [
            _record("cli", "rag-helper/rag_helper/cli.py", importance_score=0.9),
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes))
        result = cluster_domains(graph)

        ids = [c.domain_id for c in result.candidates]
        self.assertEqual(ids, ["rag-helper-rag_helper"])
        cluster = result.candidates[0]
        self.assertEqual(cluster.root_paths, ["rag-helper/rag_helper"])
        self.assertEqual(cluster.record_count, 6)
        self.assertEqual(cluster.core_records[0], "cli")  # highest importance
        self.assertEqual(result.unassigned_records, [])

    def test_heterogeneous_parent_splits_into_sub_roots(self) -> None:
        nodes = (
            [_record(f"s{i}", f"agent/services/service{i}.py") for i in range(5)]
            + [_record(f"r{i}", f"agent/routes/route{i}.py") for i in range(4)]
        )
        graph = DomainGraph.build(_build_inputs(nodes=nodes))
        result = cluster_domains(graph)

        ids = {c.domain_id for c in result.candidates}
        self.assertIn("agent-services", ids)
        self.assertIn("agent-routes", ids)
        self.assertNotIn("agent", ids)

    def test_output_is_byte_stable_sorted(self) -> None:
        nodes = (
            [_record(f"a{i}", f"alpha/file{i}.py") for i in range(4)]
            + [_record(f"b{i}", f"beta/file{i}.py") for i in range(4)]
        )
        graph_a = DomainGraph.build(_build_inputs(nodes=list(nodes)))
        graph_b = DomainGraph.build(_build_inputs(nodes=list(reversed(nodes))))
        result_a = cluster_domains(graph_a)
        result_b = cluster_domains(graph_b)
        ids_a = [c.domain_id for c in result_a.candidates]
        ids_b = [c.domain_id for c in result_b.candidates]
        self.assertEqual(ids_a, ids_b)
        self.assertEqual(ids_a, sorted(ids_a))


class TestGraphSignalClustering(unittest.TestCase):
    def test_isolated_record_joins_single_coupled_cluster(self) -> None:
        # Two well-populated clusters and one isolated record whose only
        # relation edges point into the rag-helper cluster.
        nodes = [
            _record(f"r{i}", f"rag-helper/rag_helper/m{i}.py") for i in range(4)
        ] + [
            _record("orphan", "tools/lonely.py"),
        ]
        edges = [
            {"source": "orphan", "target": "r0", "type": "field_type_uses"},
            {"source": "orphan", "target": "r1", "type": "field_type_uses"},
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes, edges=edges))
        result = cluster_domains(graph)

        ids = {c.domain_id: c for c in result.candidates}
        self.assertIn("rag-helper-rag_helper", ids)
        # 'tools' has only one file; below the min_files threshold, so no
        # cluster is created for it. The orphan is absorbed into the
        # rag-helper cluster via graph-signal (2 coupled edges to r0/r1).
        self.assertEqual(len(ids), 1)
        rag_cluster = ids["rag-helper-rag_helper"]
        self.assertIn("orphan", rag_cluster.member_record_ids)
        self.assertNotIn("orphan", result.unassigned_records)
        self.assertIn("r0", rag_cluster.member_record_ids)

    def test_isolated_record_ambiguous_coupling_stays_unassigned(self) -> None:
        nodes = [
            _record(f"a{i}", f"alpha/f{i}.py") for i in range(4)
        ] + [
            _record(f"b{i}", f"beta/f{i}.py") for i in range(4)
        ] + [_record("orphan", "tools/lonely.py")]
        edges = [
            {"source": "orphan", "target": "a0", "type": "field_type_uses"},
            {"source": "orphan", "target": "b0", "type": "field_type_uses"},
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes, edges=edges))
        result = cluster_domains(graph)
        self.assertIn("orphan", result.unassigned_records)
        ids = {c.domain_id for c in result.candidates}
        self.assertIn("alpha", ids)
        self.assertIn("beta", ids)


class TestLayerOnlyFiltered(unittest.TestCase):
    def test_cluster_without_path_signal_is_dropped(self) -> None:
        # All records share the same layer-only classification; no path
        # signal lands because the only root candidate has no records.
        nodes = [
            _record(f"x{i}", f"shared/api/{i}.py", role_labels=["controller"])
            for i in range(3)
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes))
        result = cluster_domains(graph)
        # 'shared' should remain as a cluster only if it qualifies; the
        # critical guard is that no cluster is created solely because the
        # records share role_labels=controller.
        for cluster in result.candidates:
            self.assertTrue(cluster.root_paths, f"cluster {cluster.domain_id} has no root_path")
        # 'shared/api' should appear because the file count is at min_files
        # and a path-signal lands; verify the layer_only filter does not
        # produce ghost clusters.
        self.assertTrue(all(c.root_paths for c in result.candidates))


class TestPackageEnrichment(unittest.TestCase):
    def test_package_prefix_attaches_to_matching_cluster(self) -> None:
        nodes = [_record(f"r{i}", f"rag-helper/rag_helper/m{i}.py") for i in range(4)]
        records = [
            {"id": "r0", "package": "com.example.billing"},
            {"id": "r1", "package": "com.example.billing.api"},
            {"id": "r2", "package": "com.example.identity"},
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes))
        result = cluster_domains(graph, records=records, manifest={})
        cluster = result.candidates[0]
        # The first segment of the package prefix appears in the root path
        # via "rag-helper", not "com.example"; the conservative filter does
        # not attach the prefix in this case.
        self.assertEqual(cluster.package_prefixes, [])

    def test_package_prefix_from_manifest_attaches_when_segment_matches(self) -> None:
        nodes = [_record(f"r{i}", f"rag-helper/rag_helper/m{i}.py") for i in range(4)]
        manifest = {
            "package_type_index": {
                "rag_helper.invoice": ["Invoice"],
                "rag_helper.payment": ["Payment"],
            }
        }
        graph = DomainGraph.build(_build_inputs(nodes=nodes, manifest=manifest))
        result = cluster_domains(graph, records=[], manifest=manifest)
        cluster = result.candidates[0]
        # The full package prefixes land verbatim; the conservative
        # segment-match in clustering.py attaches the prefix when the
        # first segment of the package prefix appears in the root path.
        self.assertIn("rag_helper.invoice", cluster.package_prefixes)
        self.assertIn("rag_helper.payment", cluster.package_prefixes)


class TestRelationMetrics(unittest.TestCase):
    def test_internal_and_external_edges_counted(self) -> None:
        nodes = (
            [_record(f"a{i}", f"alpha/f{i}.py") for i in range(4)]
            + [_record(f"b{i}", f"beta/f{i}.py") for i in range(4)]
        )
        edges = [
            {"source": "a0", "target": "a1", "type": "field_type_uses"},
            {"source": "a0", "target": "b0", "type": "injects_dependency"},
        ]
        graph = DomainGraph.build(_build_inputs(nodes=nodes, edges=edges))
        result = cluster_domains(graph)
        by_id = {c.domain_id: c for c in result.candidates}
        alpha = by_id["alpha"]
        beta = by_id["beta"]
        self.assertEqual(alpha.metrics["internal_edge_count"], 1)
        self.assertEqual(alpha.metrics["outbound_edge_count"], 1)
        self.assertEqual(beta.metrics["inbound_edge_count"], 1)
        # edge_type_counts aggregate internal + external edges that touch
        # the cluster; outbound edges count for the source cluster.
        self.assertEqual(
            alpha.metrics["edge_type_counts"],
            {"field_type_uses": 1, "injects_dependency": 1},
        )
        self.assertEqual(beta.metrics["edge_type_counts"], {"injects_dependency": 1})


if __name__ == "__main__":
    unittest.main()
