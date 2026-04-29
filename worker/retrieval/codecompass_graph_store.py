from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CodeCompassGraphStore:
    def __init__(self, *, index_path: str | Path):
        self._index_path = Path(index_path)

    def load(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {
                "state": {},
                "nodes": [],
                "edges": [],
                "node_index": {},
                "outgoing_index": {},
                "incoming_index": {},
                "diagnostics": {"status": "degraded", "reason": "graph_index_missing"},
            }
        payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        return {
            "state": dict(payload.get("state") or {}),
            "nodes": [item for item in list(payload.get("nodes") or []) if isinstance(item, dict)],
            "edges": [item for item in list(payload.get("edges") or []) if isinstance(item, dict)],
            "node_index": dict(payload.get("node_index") or {}),
            "outgoing_index": dict(payload.get("outgoing_index") or {}),
            "incoming_index": dict(payload.get("incoming_index") or {}),
            "diagnostics": dict(payload.get("diagnostics") or {}),
        }

    def save(self, payload: dict[str, Any]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def rebuild_from_output_records(
        self,
        *,
        records: list[dict[str, Any]],
        manifest_hash: str,
    ) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        has_nodes = False
        has_edges = False
        for index, record in enumerate(list(records or []), start=1):
            if not isinstance(record, dict):
                continue
            provenance = dict(record.get("_provenance") or {})
            output_kind = str(provenance.get("output_kind") or "").strip().lower()
            if output_kind == "graph_nodes":
                has_nodes = True
                node_id = str(record.get("id") or record.get("node_id") or f"node:{index}").strip()
                nodes.append(
                    {
                        "id": node_id,
                        "file": str(record.get("file") or record.get("path") or "").strip(),
                        "kind": str(record.get("kind") or record.get("type") or "unknown").strip().lower() or "unknown",
                        "name": str(record.get("name") or record.get("symbol") or "").strip(),
                        "record_id": str(record.get("record_id") or node_id).strip(),
                        "content": str(record.get("content") or record.get("summary") or "").strip(),
                        "source_record": record,
                    }
                )
            elif output_kind == "graph_edges":
                has_edges = True
                source_id = str(record.get("source") or record.get("source_id") or "").strip()
                target_id = str(record.get("target") or record.get("target_id") or "").strip()
                if not source_id or not target_id:
                    continue
                edge_type = str(record.get("type") or record.get("edge_type") or "related").strip().lower() or "related"
                edges.append(
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "edge_type": edge_type,
                        "confidence": float(record.get("confidence") or 1.0),
                        "provenance": {
                            "manifest_hash": str(manifest_hash or ""),
                            "output_kind": output_kind,
                        },
                    }
                )

        node_index = self._build_node_index(nodes)
        outgoing_index, incoming_index = self._build_edge_indexes(edges)
        diagnostics = {"status": "ready", "reason": "graph_loaded", "node_count": len(nodes), "edge_count": len(edges)}
        if not has_nodes or not has_edges:
            diagnostics = {
                "status": "degraded",
                "reason": "missing_graph_outputs",
                "node_count": len(nodes),
                "edge_count": len(edges),
            }

        payload = {
            "state": {"schema": "codecompass_graph_index.v1", "manifest_hash": str(manifest_hash or "")},
            "nodes": nodes,
            "edges": edges,
            "node_index": node_index,
            "outgoing_index": outgoing_index,
            "incoming_index": incoming_index,
            "diagnostics": diagnostics,
        }
        self.save(payload)
        return diagnostics

    @staticmethod
    def _build_node_index(nodes: list[dict[str, Any]]) -> dict[str, Any]:
        by_id = {}
        by_file: dict[str, list[str]] = {}
        by_kind: dict[str, list[str]] = {}
        by_name: dict[str, list[str]] = {}
        by_record_id: dict[str, list[str]] = {}
        for node in nodes:
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue
            by_id[node_id] = dict(node)
            file = str(node.get("file") or "").strip()
            kind = str(node.get("kind") or "").strip().lower()
            name = str(node.get("name") or "").strip()
            record_id = str(node.get("record_id") or "").strip()
            if file:
                by_file.setdefault(file, []).append(node_id)
            if kind:
                by_kind.setdefault(kind, []).append(node_id)
            if name:
                by_name.setdefault(name, []).append(node_id)
            if record_id:
                by_record_id.setdefault(record_id, []).append(node_id)
        return {
            "by_id": by_id,
            "by_file": {key: sorted(value) for key, value in by_file.items()},
            "by_kind": {key: sorted(value) for key, value in by_kind.items()},
            "by_name": {key: sorted(value) for key, value in by_name.items()},
            "by_record_id": {key: sorted(value) for key, value in by_record_id.items()},
        }

    @staticmethod
    def _build_edge_indexes(edges: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        outgoing: dict[str, dict[str, list[dict[str, Any]]]] = {}
        incoming: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for edge in edges:
            source_id = str(edge.get("source_id") or "").strip()
            target_id = str(edge.get("target_id") or "").strip()
            edge_type = str(edge.get("edge_type") or "related").strip().lower() or "related"
            if not source_id or not target_id:
                continue
            outgoing.setdefault(source_id, {}).setdefault(edge_type, []).append(dict(edge))
            incoming.setdefault(target_id, {}).setdefault(edge_type, []).append(dict(edge))
        return outgoing, incoming

    def outgoing_edges(self, *, node_id: str, allowed_edge_types: set[str] | None = None) -> list[dict[str, Any]]:
        payload = self.load()
        outgoing = dict(payload.get("outgoing_index") or {}).get(str(node_id), {})
        rows: list[dict[str, Any]] = []
        allow = {str(item).strip().lower() for item in set(allowed_edge_types or set()) if str(item).strip()}
        for edge_type in sorted(outgoing):
            if allow and edge_type not in allow:
                continue
            rows.extend(dict(item) for item in list(outgoing.get(edge_type) or []) if isinstance(item, dict))
        return rows

    def traverse(
        self,
        *,
        seed_ids: list[str],
        max_depth: int,
        max_nodes: int,
        allowed_edge_types: set[str] | None = None,
    ) -> dict[str, Any]:
        payload = self.load()
        by_id = dict((payload.get("node_index") or {}).get("by_id") or {})
        visited: set[str] = set()
        queue: list[tuple[str, int, list[dict[str, Any]]]] = []
        for seed in sorted({str(item).strip() for item in list(seed_ids or []) if str(item).strip()}):
            if seed in by_id:
                queue.append((seed, 0, []))
        selected_nodes: list[dict[str, Any]] = []
        selected_paths: list[dict[str, Any]] = []
        while queue and len(selected_nodes) < max(1, int(max_nodes)):
            node_id, depth, path = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            node = dict(by_id.get(node_id) or {})
            if not node:
                continue
            selected_nodes.append(node)
            if path:
                selected_paths.append({"node_id": node_id, "path": path})
            if depth >= max(0, int(max_depth)):
                continue
            for edge in self.outgoing_edges(node_id=node_id, allowed_edge_types=allowed_edge_types):
                target = str(edge.get("target_id") or "").strip()
                if not target or target in visited:
                    continue
                queue.append((target, depth + 1, [*path, dict(edge)]))
        return {
            "nodes": selected_nodes,
            "paths": selected_paths,
            "cycle_guarded": True,
            "bounded": True,
        }

