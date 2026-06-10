from __future__ import annotations

import unittest

from rag_helper.domain_discovery.graph_model import DomainGraph
from rag_helper.domain_discovery.inputs import AnalysisInputs


def _inputs(**overrides) -> AnalysisInputs:
    base = {
        "index_records": [
            {"id": "a", "kind": "java_type", "file": "x/A.java", "package": "com.example.x"},
            {"id": "b", "kind": "java_type", "file": "y/B.java"},
        ],
        "detail_records": [
            {"id": "a.m", "kind": "java_method", "file": "x/A.java", "parent_id": "a"},
        ],
        "relation_records": [
            {"from": "a", "to": "b", "type": "uses_type"},
        ],
        "graph_nodes": [
            {"id": "a", "kind": "java_type", "file": "x/A.java"},
            {"id": "b", "kind": "java_type", "file": "y/B.java"},
            {"id": "a.m", "kind": "java_method", "file": "x/A.java", "parent_id": "a"},
        ],
        "graph_edges": [
            {"source": "a", "target": "a.m", "type": "parent_child", "kind": "parent_child"},
            {"source": "a", "target": "b", "type": "uses_type", "kind": "relation", "confidence": 0.9},
        ],
        "manifest": {},
    }
    base.update(overrides)
    return AnalysisInputs.from_memory(**base)


class TestDomainGraph(unittest.TestCase):
    def test_nodes_carry_file_kind_role_labels_parent_and_package(self) -> None:
        graph = DomainGraph.build(_inputs())
        node = graph.nodes["a"]
        self.assertEqual(node.file, "x/A.java")
        self.assertEqual(node.kind, "java_type")
        self.assertEqual(node.package, "com.example.x")
        self.assertEqual(graph.nodes["a.m"].parent_id, "a")

    def test_relation_edges_are_deduplicated_against_graph_edges(self) -> None:
        # relations.jsonl repeats the a->b uses_type edge that graph_edges
        # already contains; it must be counted exactly once.
        graph = DomainGraph.build(_inputs())
        uses_edges = [e for e in graph.relation_edges if e.type == "uses_type"]
        self.assertEqual(len(uses_edges), 1)
        self.assertEqual(uses_edges[0].confidence, 0.9)

    def test_parent_child_is_structural_and_not_a_relation(self) -> None:
        graph = DomainGraph.build(_inputs())
        self.assertEqual([e.type for e in graph.structural_edges], ["parent_child"])
        self.assertNotIn("parent_child", {e.type for e in graph.relation_edges})
        # Coupling metrics use relation_degree, which must ignore structure:
        # a has one relation edge (uses_type), not two.
        self.assertEqual(graph.relation_degree("a"), 1)

    def test_missing_graph_nodes_falls_back_to_records(self) -> None:
        graph = DomainGraph.build(_inputs(graph_nodes=[]))
        self.assertIn("a", graph.nodes)
        self.assertTrue(any("derived nodes" in w for w in graph.warnings))


if __name__ == "__main__":
    unittest.main()
