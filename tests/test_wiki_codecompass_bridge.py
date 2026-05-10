from __future__ import annotations

import json

from agent.services.wiki_codecompass_bridge import WikiCodeCompassBridge


def test_wiki_codecompass_bridge_writes_streaming_outputs(tmp_path):
    bridge = WikiCodeCompassBridge()
    records = [
        {
            "kind": "wiki_section_chunk",
            "wiki_article_id": "payment-retries",
            "article_title": "Payment retries",
            "section_title": "Overview",
            "chunk_id": "wiki:c1",
            "content": "Retries are bounded.",
        }
    ]
    manifest = bridge.build_outputs(records=records, output_dir=tmp_path, include_graph=False)

    index_lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(index_lines) == 1
    payload = json.loads(index_lines[0])
    assert payload["kind"] == "wiki_section_chunk"
    assert manifest["chunking"]["strategy"] == "wiki_streaming_codecompass_prerender"
