from __future__ import annotations

import json

from agent.services.wiki_codecompass_bridge import WikiCodeCompassBridge


def test_wiki_codecompass_bridge_optionally_exports_graph(tmp_path):
    bridge = WikiCodeCompassBridge()
    records = [
        {
            "kind": "wiki_section_chunk",
            "wiki_article_id": "de-ananta",
            "article_title": "Ananta",
            "section_title": "Links",
            "chunk_id": "wiki:c2",
            "content": "Linked content.",
        }
    ]
    manifest = bridge.build_outputs(records=records, output_dir=tmp_path, include_graph=True)

    node_lines = (tmp_path / "graph_nodes.jsonl").read_text(encoding="utf-8").strip().splitlines()
    edge_lines = (tmp_path / "graph_edges.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(node_lines) >= 3
    assert len(edge_lines) >= 2
    first = json.loads(node_lines[0])
    assert "node_id" in first
    assert "graph_nodes" in manifest["partitioned_outputs"]
