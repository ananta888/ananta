from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag_helper.application.output_formats import build_graph_edges, build_graph_nodes
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project


class _GraphJavaExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def pre_scan_types(self, rel_path: str, text: str) -> dict:
        return {"file": rel_path, "package": None, "imports": [], "type_names": []}

    def parse(self, rel_path: str, text: str, known_package_types: dict[str, set[str]]):
        return [{
            "kind": "java_file",
            "file": rel_path,
            "id": "java_file:Demo",
            "embedding_text": "demo",
            "parent_id": None,
        }], [{
            "kind": "java_method_detail",
            "file": rel_path,
            "id": "java_method_detail:Demo.run",
            "parent_id": "java_file:Demo",
        }], [{
            "type": "calls",
            "from": "java_method_detail:Demo.run",
            "to": "java_file:Demo",
            "confidence": 0.8,
            "heuristic": "same_class",
        }], {"kind": "java", "file": rel_path}


class _GraphNoopExtractor:
    def parse(self, rel_path: str, text: str):
        return [], [], [], {"kind": "noop", "file": rel_path}


class GraphExportTests(unittest.TestCase):
    def test_build_graph_formats_jsonl_nodes_and_edges(self) -> None:
        nodes = build_graph_nodes(
            index_records=[{"id": "file:1", "kind": "java_file", "file": "Demo.java"}],
            detail_records=[{"id": "detail:1", "kind": "java_method_detail", "file": "Demo.java", "parent_id": "file:1"}],
            mode="jsonl",
        )
        edges = build_graph_edges(
            index_records=[],
            detail_records=[{"id": "detail:1", "parent_id": "file:1"}],
            relation_records=[{"from": "detail:1", "to": "file:1", "type": "calls"}],
            mode="jsonl",
        )
        self.assertEqual(nodes[0]["kind"], "java_file")
        self.assertEqual(edges[0]["type"], "parent_child")
        self.assertEqual(edges[1]["type"], "calls")

    def test_build_graph_formats_neo4j_nodes_and_edges(self) -> None:
        nodes = build_graph_nodes(
            index_records=[{"id": "file:1", "kind": "java_file", "file": "Demo.java"}],
            detail_records=[],
            mode="neo4j",
        )
        edges = build_graph_edges(
            index_records=[],
            detail_records=[{"id": "detail:1", "parent_id": "file:1"}],
            relation_records=[{"from": "detail:1", "to": "file:1", "type": "calls", "confidence": 0.5}],
            mode="neo4j",
        )
        self.assertEqual(nodes[0]["labels"], ["java_file"])
        self.assertEqual(edges[0]["type"], "HAS_CHILD")
        self.assertEqual(edges[1]["type"], "CALLS")
        self.assertEqual(edges[1]["properties"]["confidence"], 0.5)

    def test_process_project_writes_graph_export_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "Demo.java").write_text("class Demo {}", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"java"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(graph_export_mode="neo4j"),
                java_extractor_cls=_GraphJavaExtractor,
                adoc_extractor_cls=_GraphNoopExtractor,
                xml_extractor_cls=_GraphNoopExtractor,
                xsd_extractor_cls=_GraphNoopExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            graph_nodes = [
                json.loads(line)
                for line in (out_dir / "graph_nodes.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            graph_edges = [
                json.loads(line)
                for line in (out_dir / "graph_edges.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(manifest["graph_node_count"], 3)
            self.assertEqual(manifest["graph_edge_count"], 2)
            self.assertEqual(manifest["options"]["graph_export_mode"], "neo4j")
            self.assertEqual(graph_nodes[0]["labels"], ["java_file"])
            self.assertEqual(graph_edges[0]["type"], "HAS_CHILD")
