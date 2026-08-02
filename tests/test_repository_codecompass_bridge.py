from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker.retrieval.repository_codecompass_bridge import RepositoryCodeCompassBridge


class _SemanticGraphStub:
    def emit_graph_records(self, path: str, content: str) -> dict[str, Any]:
        del path, content
        return {
            "nodes": [
                {
                    "id": "type:Example",
                    "kind": "data_record",
                    "attributes": {
                        "fields": [{"name": "value", "type": "str"}],
                        "methods": [
                            {
                                "name": "render",
                                "parameters": [{"name": "prefix", "type": "str"}],
                            }
                        ],
                    },
                },
                {
                    "id": "method:Example.render",
                    "kind": "function_signature",
                    "attributes": {
                        "name": "render",
                        "parameters": [{"name": "prefix", "type": "str"}],
                    },
                },
            ],
            "edges": [
                {
                    "source": "type:Example",
                    "target": "method:Example.render",
                    "edge_type": "declares",
                }
            ],
            "diagnostics": [],
        }


def test_bridge_omits_redundant_nested_method_snapshots(tmp_path: Path) -> None:
    RepositoryCodeCompassBridge(_SemanticGraphStub()).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "class Example: pass",
                "metadata": {"relative_path": "src/example.py"},
            }
        ],
        output_dir=tmp_path,
    )

    nodes = [
        json.loads(line)
        for line in (tmp_path / "semantic_nodes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    type_node = next(node for node in nodes if node["id"] == "type:Example")
    method_node = next(node for node in nodes if node["id"] == "method:Example.render")

    assert "methods" not in type_node["attributes"]
    assert type_node["attributes"]["fields"] == [{"name": "value", "type": "str"}]
    assert method_node["attributes"]["parameters"] == [
        {"name": "prefix", "type": "str"}
    ]
