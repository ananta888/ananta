"""Obsidian Canvas Extractor for CodeCompass/ANANTA (OBS-006).

Parses .canvas files (Obsidian JSON format) into index/detail/relation records.
"""
from __future__ import annotations

import json

from rag_helper.utils.ids import sha1_text


class CanvasExtractor:
    """Extracts records from Obsidian Canvas JSON files."""

    def __init__(
        self,
        vault_name: str = "default",
        max_nodes: int | None = None,
    ) -> None:
        self.vault_name = vault_name
        self.max_nodes = max_nodes

    def parse(
        self,
        rel_path: str,
        text: str,
    ) -> tuple[list[dict], list[dict], list[dict], dict]:
        """Parse a .canvas JSON file.

        Returns (index_records, detail_records, relation_records, stats)
        """
        vault_name = self.vault_name

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return [], [], [], {"error": "invalid_json", "node_count": 0, "edge_count": 0}

        raw_nodes: list[dict] = data.get("nodes") or []
        raw_edges: list[dict] = data.get("edges") or []

        if self.max_nodes is not None:
            raw_nodes = raw_nodes[: self.max_nodes]

        canvas_id = f"obs_canvas:{sha1_text(f'{vault_name}:{rel_path}')}"

        # ── Canvas index record ──────────────────────────────────────────
        title = rel_path.rsplit("/", 1)[-1]
        if title.endswith(".canvas"):
            title = title[:-7]

        node_texts = [
            n.get("text", "")[:100]
            for n in raw_nodes
            if n.get("type") == "text" and n.get("text")
        ]
        node_files = [
            n.get("file", "")
            for n in raw_nodes
            if n.get("type") == "file" and n.get("file")
        ]

        embedding_text = (
            f"Canvas: {title} | "
            f"nodes: {len(raw_nodes)} | "
            f"texts: {' '.join(node_texts[:5])} | "
            f"files: {', '.join(node_files[:5])}"
        )[:400]

        index_records = [
            {
                "id": canvas_id,
                "kind": "obsidian_canvas",
                "file": rel_path,
                "vault": vault_name,
                "title": title,
                "node_count": len(raw_nodes),
                "edge_count": len(raw_edges),
                "embedding_text": embedding_text,
                "source_type": "obsidian_vault",
                "importance_score": 0.6,
            }
        ]

        # ── Node index records ───────────────────────────────────────────
        node_records: list[dict] = []
        for node in raw_nodes:
            nid = node.get("id", "")
            node_record_id = f"obs_canvas_node:{sha1_text(f'{vault_name}:{rel_path}:{nid}')}"
            node_type = node.get("type", "unknown")

            node_embedding = ""
            if node_type == "text":
                node_embedding = f"{title} canvas node | {node.get('text', '')[:200]}"
            elif node_type == "file":
                node_embedding = f"{title} canvas file node | {node.get('file', '')}"
            elif node_type == "link":
                node_embedding = f"{title} canvas link node | {node.get('url', '')}"
            else:
                node_embedding = f"{title} canvas {node_type} node"

            node_records.append({
                "id": node_record_id,
                "kind": "obsidian_canvas_node",
                "canvas_id": canvas_id,
                "canvas_file": rel_path,
                "vault": vault_name,
                "node_id": nid,
                "node_type": node_type,
                "text": node.get("text", "")[:500] if node_type == "text" else "",
                "file": node.get("file", "") if node_type == "file" else "",
                "url": node.get("url", "") if node_type == "link" else "",
                "x": node.get("x", 0),
                "y": node.get("y", 0),
                "width": node.get("width", 0),
                "height": node.get("height", 0),
                "embedding_text": node_embedding,
                "parent_id": canvas_id,
                "source_type": "obsidian_vault",
                "importance_score": 0.5,
            })

        index_records.extend(node_records)

        # ── Detail record ────────────────────────────────────────────────
        detail_records = [
            {
                "id": canvas_id,
                "kind": "obsidian_canvas_detail",
                "file": rel_path,
                "vault": vault_name,
                "title": title,
                "nodes": raw_nodes,
                "edges": raw_edges,
                "source_type": "obsidian_vault",
            }
        ]

        # ── Relations ────────────────────────────────────────────────────
        relation_records: list[dict] = []

        # Node-to-node relations from edges
        node_id_map = {}
        for n in raw_nodes:
            nid_key = n.get("id")
            if nid_key:
                node_id_map[nid_key] = f"obs_canvas_node:{sha1_text(vault_name + ':' + rel_path + ':' + nid_key)}"

        for edge in raw_edges:
            from_node = edge.get("fromNode", "")
            to_node = edge.get("toNode", "")
            if from_node in node_id_map and to_node in node_id_map:
                relation_records.append({
                    "from": node_id_map[from_node],
                    "to": node_id_map[to_node],
                    "type": "obs_canvas_edge",
                    "label": edge.get("label", ""),
                    "edge_id": edge.get("id", ""),
                })

        # File-node relations to actual notes
        for node in raw_nodes:
            if node.get("type") == "file" and node.get("file"):
                nid = node.get("id", "")
                file_path = node["file"]
                # Normalize the path
                target_note_id = f"obs_note:{sha1_text(f'{vault_name}:{file_path}')}"
                node_record_id = f"obs_canvas_node:{sha1_text(f'{vault_name}:{rel_path}:{nid}')}"
                relation_records.append({
                    "from": node_record_id,
                    "to": target_note_id,
                    "type": "obs_canvas_references_note",
                    "resolved": True,
                    "confidence": 0.9,
                })

        stats = {
            "node_count": len(raw_nodes),
            "edge_count": len(raw_edges),
            "graph_nodes": [
                {
                    "id": canvas_id,
                    "kind": "obsidian_canvas",
                    "file": rel_path,
                    "vault": vault_name,
                    "title": title,
                }
            ],
            "graph_edges": [
                {
                    "from": node_id_map.get(e.get("fromNode", ""), ""),
                    "to": node_id_map.get(e.get("toNode", ""), ""),
                    "type": "obs_canvas_edge",
                }
                for e in raw_edges
                if e.get("fromNode") in node_id_map and e.get("toNode") in node_id_map
            ],
        }

        return index_records, detail_records, relation_records, stats
