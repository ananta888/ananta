from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WikiCodeCompassBridge:
    """Stream-writes CodeCompass JSONL outputs from wiki records.

    Produces four files:
      index.jsonl       — compact chunk records for RAG retrieval
      details.jsonl     — full content per chunk (article, section, text)
      graph_nodes.jsonl — article / section / chunk nodes
      graph_edges.jsonl — structural edges (contains_*) + inter-article wiki-links
    """

    def _iter_records(
        self,
        records: list[dict[str, Any]] | None,
        records_path: Path | None,
    ):
        if records_path and records_path.exists():
            with records_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass
        else:
            yield from (records or [])

    def build_outputs(
        self,
        *,
        records: list[dict[str, Any]] | None = None,
        records_path: Path | None = None,
        output_dir: Path,
        include_graph: bool = True,
        links_path: Path | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path       = output_dir / "index.jsonl"
        details_path     = output_dir / "details.jsonl"
        graph_nodes_path = output_dir / "graph_nodes.jsonl"
        graph_edges_path = output_dir / "graph_edges.jsonl"

        index_count = detail_count = node_count = edge_count = 0
        seen_nodes: set[str] = set()

        with (
            index_path.open("w", encoding="utf-8")       as idx_fh,
            details_path.open("w", encoding="utf-8")     as det_fh,
            graph_nodes_path.open("w", encoding="utf-8") as node_fh,
            graph_edges_path.open("w", encoding="utf-8") as edge_fh,
        ):
            def _write_node(node_id: str, kind: str, title: str, extra: dict | None = None) -> None:
                nonlocal node_count
                if node_id in seen_nodes:
                    return
                seen_nodes.add(node_id)
                node_fh.write(json.dumps(
                    {"node_id": node_id, "kind": kind, "title": title, **(extra or {})},
                    ensure_ascii=False,
                ) + "\n")
                node_count += 1

            def _write_edge(from_id: str, to_id: str, relation: str) -> None:
                nonlocal edge_count
                edge_fh.write(json.dumps(
                    {"from": from_id, "to": to_id, "relation": relation},
                    ensure_ascii=False,
                ) + "\n")
                edge_count += 1

            # ── Records → index + details + structural nodes/edges ──────────
            for record in self._iter_records(records, records_path):
                if str(record.get("kind") or "") != "wiki_section_chunk":
                    continue

                article_title   = str(record.get("article_title") or "")
                section_title   = str(record.get("section_title") or "")
                chunk_id        = str(record.get("chunk_id") or "")
                wiki_article_id = str(record.get("wiki_article_id") or article_title)

                # index record (all fields except heavy ones already stripped upstream)
                idx_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                index_count += 1

                # detail record (content for retrieval display)
                det_fh.write(json.dumps({
                    "kind":            "wiki_detail",
                    "wiki_article_id": wiki_article_id,
                    "article_title":   article_title,
                    "section_title":   section_title,
                    "chunk_id":        chunk_id,
                    "content":         record.get("content"),
                    "language":        record.get("language"),
                }, ensure_ascii=False) + "\n")
                detail_count += 1

                if not include_graph:
                    continue

                article_node = f"article:{wiki_article_id}"
                section_node = f"section:{wiki_article_id}:{section_title}"
                chunk_node   = f"chunk:{chunk_id}"

                _write_node(article_node, "wiki_article", article_title)
                _write_node(section_node, "wiki_section", section_title,
                            {"article_node": article_node})
                _write_node(chunk_node,   "wiki_chunk",   chunk_id,
                            {"section_node": section_node})

                _write_edge(article_node, section_node, "contains_section")
                _write_edge(section_node, chunk_node,   "contains_chunk")

            # ── Inter-article link edges from links file ─────────────────────
            link_edge_count = 0
            if include_graph and links_path and links_path.exists():
                logger.info("wiki_codecompass_bridge: reading link edges from %s", links_path.name)
                with links_path.open("r", encoding="utf-8") as lf:
                    for line in lf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ldata = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        from_title = str(ldata.get("from") or "")
                        if not from_title:
                            continue
                        from_node = f"article:{from_title}"
                        if from_node not in seen_nodes:
                            continue
                        to_list = ldata.get("to") or []
                        if isinstance(to_list, str):
                            to_list = [to_list]
                        for to_title in to_list:
                            to_title = str(to_title or "").strip()
                            if not to_title:
                                continue
                            to_node = f"article:{to_title}"
                            if to_node in seen_nodes:
                                _write_edge(from_node, to_node, "wiki_link")
                                link_edge_count += 1

                logger.info("wiki_codecompass_bridge: %d wiki_link edges written", link_edge_count)
                edge_count += link_edge_count

        logger.info(
            "wiki_codecompass_bridge: index=%d detail=%d nodes=%d edges=%d (structural=%d link=%d)",
            index_count, detail_count, node_count, edge_count,
            edge_count - link_edge_count, link_edge_count,
        )

        return {
            "source_scope": "wiki",
            "index_record_count":    index_count,
            "detail_record_count":   detail_count,
            "node_count":            node_count,
            "relation_record_count": edge_count,
            "link_edge_count":       link_edge_count,
            "file_count": 4,
            "partitioned_outputs": {
                "index":       str(index_path),
                "details":     str(details_path),
                "graph_nodes": str(graph_nodes_path),
                "graph_edges": str(graph_edges_path),
            },
            "chunking": {"strategy": "wiki_inline_compact_codecompass"},
        }
