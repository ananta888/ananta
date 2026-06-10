from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag_helper.domain_discovery.inputs import AnalysisInputs, load_analysis_inputs


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


class TestLoadAnalysisInputs(unittest.TestCase):
    def test_missing_out_dir_warns_instead_of_raising(self) -> None:
        inputs = load_analysis_inputs(Path("/nonexistent/dd-out"))
        self.assertEqual(inputs.index_records, [])
        self.assertTrue(any("out_dir not found" in w for w in inputs.warnings))

    def test_empty_out_dir_reports_all_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inputs = load_analysis_inputs(Path(tmp))
        missing = [w for w in inputs.warnings if "not found in out_dir" in w]
        self.assertEqual(len(missing), 6)  # 5 jsonl files + manifest.json

    def test_partial_outputs_load_without_abort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            _write_jsonl(out_dir / "index.jsonl", [{"id": "a", "kind": "java_type", "file": "x/A.java"}])
            _write_jsonl(out_dir / "graph_nodes.jsonl", [{"id": "a", "kind": "java_type", "file": "x/A.java"}])
            inputs = load_analysis_inputs(out_dir)
        self.assertEqual(len(inputs.index_records), 1)
        self.assertEqual(len(inputs.graph_nodes), 1)
        self.assertTrue(any("graph_edges.jsonl not found" in w for w in inputs.warnings))
        self.assertTrue(any("manifest.json not found" in w for w in inputs.warnings))

    def test_full_outputs_load_and_validate_minimal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            _write_jsonl(out_dir / "index.jsonl", [{"id": "a", "kind": "java_type", "file": "x/A.java"}])
            _write_jsonl(out_dir / "details.jsonl", [{"id": "a.m", "kind": "java_method", "file": "x/A.java"}])
            _write_jsonl(out_dir / "relations.jsonl", [{"from": "a", "to": "b", "type": "uses_type"}])
            _write_jsonl(
                out_dir / "graph_nodes.jsonl",
                [
                    {"id": "a", "kind": "java_type", "file": "x/A.java"},
                    {"id": None, "kind": "java_type", "file": "broken.java"},
                ],
            )
            _write_jsonl(
                out_dir / "graph_edges.jsonl",
                [
                    {"source": "a", "target": "b", "type": "uses_type", "kind": "relation"},
                    {"source": "a", "target": None, "type": "uses_type"},
                ],
            )
            (out_dir / "manifest.json").write_text(json.dumps({"package_type_index": {}}), encoding="utf-8")
            inputs = load_analysis_inputs(out_dir)

        self.assertEqual(len(inputs.graph_nodes), 1)
        self.assertEqual(len(inputs.graph_edges), 1)
        self.assertTrue(any("skipped 1 node(s)" in w for w in inputs.warnings))
        self.assertTrue(any("skipped 1 edge(s)" in w for w in inputs.warnings))
        self.assertEqual(inputs.loaded_files["manifest.json"], 1)

    def test_from_memory_validates_nodes_and_edges(self) -> None:
        inputs = AnalysisInputs.from_memory(
            index_records=[{"id": "a", "kind": "java_type", "file": "x/A.java"}],
            detail_records=[],
            relation_records=[],
            graph_nodes=[{"id": "a", "kind": "java_type", "file": "x/A.java"}, {"kind": "broken"}],
            graph_edges=[{"source": "a", "target": "b", "type": "uses_type"}],
            manifest={},
        )
        self.assertEqual(len(inputs.graph_nodes), 1)
        self.assertEqual(len(inputs.graph_edges), 1)


if __name__ == "__main__":
    unittest.main()
