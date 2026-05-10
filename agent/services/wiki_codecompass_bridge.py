from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WikiCodeCompassBridge:
    """Stream-like JSONL bridge for wiki records without per-article markdown files."""

    def build_outputs(
        self,
        *,
        records: list[dict[str, Any]],
        output_dir: Path,
        include_graph: bool = True,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path = output_dir / "index.jsonl"
        details_path = output_dir / "details.jsonl"
        graph_nodes_path = output_dir / "graph_nodes.jsonl"
        graph_edges_path = output_dir / "graph_edges.jsonl"

        index_rows: list[str] = []
        detail_rows: list[str] = []
        node_rows: list[str] = []
        edge_rows: list[str] = []
        seen_nodes: set[str] = set()

        for record in records:
            kind = str(record.get("kind") or "")
            if kind != "wiki_section_chunk":
                continue
            payload = dict(record)
            index_rows.append(json.dumps(payload, ensure_ascii=True, sort_keys=True))
            detail_rows.append(
                json.dumps(
                    {
                        "kind": "wiki_detail",
                        "wiki_article_id": payload.get("wiki_article_id"),
                        "article_title": payload.get("article_title"),
                        "section_title": payload.get("section_title"),
                        "chunk_id": payload.get("chunk_id"),
                        "content": payload.get("content"),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            if not include_graph:
                continue
            article_node = f"article:{payload.get('wiki_article_id') or payload.get('article_title')}"
            section_node = f"section:{payload.get('wiki_article_id')}:{payload.get('section_title')}"
            chunk_node = f"chunk:{payload.get('chunk_id')}"
            for node_id, node_kind, title in (
                (article_node, "wiki_article", payload.get("article_title")),
                (section_node, "wiki_section", payload.get("section_title")),
                (chunk_node, "wiki_chunk", payload.get("chunk_id")),
            ):
                if node_id in seen_nodes:
                    continue
                seen_nodes.add(node_id)
                node_rows.append(
                    json.dumps(
                        {"node_id": node_id, "kind": node_kind, "title": title},
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            edge_rows.append(
                json.dumps({"from": article_node, "to": section_node, "relation": "contains_section"}, ensure_ascii=True, sort_keys=True)
            )
            edge_rows.append(
                json.dumps({"from": section_node, "to": chunk_node, "relation": "contains_chunk"}, ensure_ascii=True, sort_keys=True)
            )

        index_path.write_text("\n".join(index_rows) + ("\n" if index_rows else ""), encoding="utf-8")
        details_path.write_text("\n".join(detail_rows) + ("\n" if detail_rows else ""), encoding="utf-8")
        if include_graph:
            graph_nodes_path.write_text("\n".join(node_rows) + ("\n" if node_rows else ""), encoding="utf-8")
            graph_edges_path.write_text("\n".join(edge_rows) + ("\n" if edge_rows else ""), encoding="utf-8")

        return {
            "source_scope": "wiki",
            "index_record_count": len(index_rows),
            "detail_record_count": len(detail_rows),
            "relation_record_count": len(edge_rows),
            "file_count": 1,
            "partitioned_outputs": {
                "index": str(index_path),
                "details": str(details_path),
                **(
                    {
                        "graph_nodes": str(graph_nodes_path),
                        "graph_edges": str(graph_edges_path),
                    }
                    if include_graph
                    else {}
                ),
            },
            "chunking": {"strategy": "wiki_streaming_codecompass_prerender"},
        }
