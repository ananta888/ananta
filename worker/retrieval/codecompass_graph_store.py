from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CodeCompassGraphStore:
    def __init__(self, *, index_path: str | Path):
        self._index_path = Path(index_path)
        self._cached_payload: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if self._cached_payload is not None:
            return self._cached_payload
        if not self._index_path.exists():
            self._cached_payload = {
                "state": {},
                "nodes": [],
                "edges": [],
                "semantic_nodes": [],
                "semantic_edges": [],
                "equivalence_rules": [],
                "translation_contracts": [],
                "transform_artifacts": [],
                "node_index": {},
                "semantic_index": {},
                "outgoing_index": {},
                "incoming_index": {},
                "diagnostics": {"status": "degraded", "reason": "graph_index_missing"},
            }
            return self._cached_payload
        payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        self._cached_payload = {
            "state": dict(payload.get("state") or {}),
            "nodes": [item for item in list(payload.get("nodes") or []) if isinstance(item, dict)],
            "edges": [item for item in list(payload.get("edges") or []) if isinstance(item, dict)],
            "semantic_nodes": [item for item in list(payload.get("semantic_nodes") or []) if isinstance(item, dict)],
            "semantic_edges": [item for item in list(payload.get("semantic_edges") or []) if isinstance(item, dict)],
            "equivalence_rules": [item for item in list(payload.get("equivalence_rules") or []) if isinstance(item, dict)],
            "translation_contracts": [item for item in list(payload.get("translation_contracts") or []) if isinstance(item, dict)],
            "transform_artifacts": [item for item in list(payload.get("transform_artifacts") or []) if isinstance(item, dict)],
            "node_index": dict(payload.get("node_index") or {}),
            "semantic_index": dict(payload.get("semantic_index") or {}),
            "outgoing_index": dict(payload.get("outgoing_index") or {}),
            "incoming_index": dict(payload.get("incoming_index") or {}),
            "diagnostics": dict(payload.get("diagnostics") or {}),
        }
        return self._cached_payload

    def save(self, payload: dict[str, Any]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._cached_payload = None

    def rebuild_from_output_records(
        self,
        *,
        records: list[dict[str, Any]],
        manifest_hash: str,
    ) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        semantic_nodes: list[dict[str, Any]] = []
        semantic_edges: list[dict[str, Any]] = []
        equivalence_rules: list[dict[str, Any]] = []
        translation_contracts: list[dict[str, Any]] = []
        transform_artifacts: list[dict[str, Any]] = []
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
                edge: dict[str, Any] = {
                    "source_id": source_id,
                    "target_id": target_id,
                    "edge_type": edge_type,
                    "confidence": float(record.get("confidence") or 1.0),
                    "provenance": {
                        "manifest_hash": str(manifest_hash or ""),
                        "output_kind": output_kind,
                    },
                }
                for attribute_key in ("field", "operation", "heuristic"):
                    if record.get(attribute_key) is not None:
                        edge[attribute_key] = record[attribute_key]
                edges.append(edge)
            elif output_kind == "semantic_nodes":
                semantic_nodes.append(self._normalize_semantic_node(record, manifest_hash, index))
            elif output_kind == "semantic_edges":
                edge = self._normalize_semantic_edge(record, manifest_hash)
                if edge:
                    semantic_edges.append(edge)
            elif output_kind == "equivalence_rules":
                equivalence_rules.append(dict(record))
            elif output_kind == "translation_contracts":
                translation_contracts.append(dict(record))
            elif output_kind == "transform_artifacts":
                transform_artifacts.append(dict(record))

        node_index = self._build_node_index(nodes)
        semantic_index = self._build_semantic_index(semantic_nodes, semantic_edges, equivalence_rules)
        outgoing_index, incoming_index = self._build_edge_indexes([*edges, *semantic_edges])
        diagnostics = {"status": "ready", "reason": "graph_loaded", "node_count": len(nodes), "edge_count": len(edges)}
        if not has_nodes or not has_edges:
            diagnostics = {
                "status": "degraded",
                "reason": "missing_graph_outputs",
                "node_count": len(nodes),
                "edge_count": len(edges),
            }
        if semantic_nodes or semantic_edges or equivalence_rules or transform_artifacts:
            diagnostics["semantic_translation"] = {
                "schema": "codecompass_semantic_translation_graph.v1",
                "semantic_node_count": len(semantic_nodes),
                "semantic_edge_count": len(semantic_edges),
                "equivalence_rule_count": len(equivalence_rules),
                "translation_contract_count": len(translation_contracts),
                "transform_artifact_count": len(transform_artifacts),
                "status": "ready",
            }
        else:
            diagnostics["semantic_translation"] = {"status": "degraded", "reason": "semantic_translation_index_unavailable"}

        payload = {
            "state": {"schema": "codecompass_graph_index.v1", "manifest_hash": str(manifest_hash or "")},
            "nodes": nodes,
            "edges": edges,
            "semantic_nodes": semantic_nodes,
            "semantic_edges": semantic_edges,
            "equivalence_rules": equivalence_rules,
            "translation_contracts": translation_contracts,
            "transform_artifacts": transform_artifacts,
            "node_index": node_index,
            "semantic_index": semantic_index,
            "outgoing_index": outgoing_index,
            "incoming_index": incoming_index,
            "diagnostics": diagnostics,
        }
        self.save(payload)
        return diagnostics

    @staticmethod
    def _normalize_semantic_node(record: dict[str, Any], manifest_hash: str, index: int) -> dict[str, Any]:
        provenance = dict(record.get("provenance") or {})
        node_id = str(record.get("id") or record.get("node_id") or f"semantic-node:{index}").strip()
        return {
            "id": node_id,
            "file": str(provenance.get("file") or record.get("file") or record.get("path") or "").strip(),
            "kind": str(record.get("kind") or "semantic_node").strip().lower() or "semantic_node",
            "semantic_kind": str(record.get("semantic_kind") or "").strip().lower(),
            "language": str(record.get("language") or provenance.get("language") or "").strip().lower(),
            "symbol": str(record.get("symbol") or provenance.get("symbol") or record.get("name") or "").strip(),
            "rule_id": str(record.get("rule_id") or "").strip(),
            "record_id": str(record.get("record_id") or node_id).strip(),
            "attributes": dict(record.get("attributes") or {}),
            "provenance": {**provenance, "manifest_hash": str(manifest_hash or "")},
            "source_record": dict(record),
        }

    @staticmethod
    def _normalize_semantic_edge(record: dict[str, Any], manifest_hash: str) -> dict[str, Any] | None:
        source_id = str(record.get("source") or record.get("source_id") or "").strip()
        target_id = str(record.get("target") or record.get("target_id") or "").strip()
        if not source_id or not target_id:
            return None
        edge_type = str(record.get("edge_type") or record.get("type") or "related").strip().lower() or "related"
        return {
            "source_id": source_id,
            "target_id": target_id,
            "edge_type": edge_type,
            "rule_id": str(record.get("rule_id") or "").strip(),
            "confidence": float(record.get("confidence") or (record.get("provenance") or {}).get("confidence") or 1.0),
            "attributes": dict(record.get("attributes") or {}),
            "provenance": {**dict(record.get("provenance") or {}), "manifest_hash": str(manifest_hash or ""), "output_kind": "semantic_edges"},
            "source_record": dict(record),
        }

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
    def _build_semantic_index(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        by_id: dict[str, dict[str, Any]] = {}
        by_file: dict[str, list[str]] = {}
        by_kind: dict[str, list[str]] = {}
        by_language: dict[str, list[str]] = {}
        by_symbol: dict[str, list[str]] = {}
        by_semantic_kind: dict[str, list[str]] = {}
        by_rule_id: dict[str, list[str]] = {}
        for node in nodes:
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue
            by_id[node_id] = dict(node)
            for key, bucket, transform in [
                ("file", by_file, str),
                ("kind", by_kind, lambda value: str(value).lower()),
                ("language", by_language, lambda value: str(value).lower()),
                ("symbol", by_symbol, str),
                ("semantic_kind", by_semantic_kind, lambda value: str(value).lower()),
                ("rule_id", by_rule_id, str),
            ]:
                value = transform(node.get(key) or "").strip()
                if value:
                    bucket.setdefault(value, []).append(node_id)
        for edge in edges:
            rule_id = str(edge.get("rule_id") or "").strip()
            if rule_id:
                by_rule_id.setdefault(rule_id, []).append(f"{edge.get('source_id')}->{edge.get('target_id')}")
        for rule in rules:
            rule_id = str(rule.get("rule_id") or "").strip()
            if rule_id:
                by_rule_id.setdefault(rule_id, []).append(rule_id)
        return {
            "by_id": by_id,
            "by_file": {key: sorted(set(value)) for key, value in by_file.items()},
            "by_kind": {key: sorted(set(value)) for key, value in by_kind.items()},
            "by_language": {key: sorted(set(value)) for key, value in by_language.items()},
            "by_symbol": {key: sorted(set(value)) for key, value in by_symbol.items()},
            "by_semantic_kind": {key: sorted(set(value)) for key, value in by_semantic_kind.items()},
            "by_rule_id": {key: sorted(set(value)) for key, value in by_rule_id.items()},
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

    def get_node(self, *, node_id: str) -> dict[str, Any] | None:
        payload = self.load()
        by_id = dict((payload.get("node_index") or {}).get("by_id") or {})
        node = by_id.get(str(node_id or "").strip())
        return dict(node) if isinstance(node, dict) else None

    def find_nodes_by_name(self, *, name: str) -> list[dict[str, Any]]:
        query = str(name or "").strip()
        if not query:
            return []
        payload = self.load()
        node_index = dict(payload.get("node_index") or {})
        by_id = dict(node_index.get("by_id") or {})
        by_name = dict(node_index.get("by_name") or {})
        node_ids = list(by_name.get(query) or [])
        if not node_ids:
            lowered = query.lower()
            for known_name in sorted(by_name):
                if str(known_name).lower() == lowered:
                    node_ids.extend(by_name.get(known_name) or [])
        return [dict(by_id[node_id]) for node_id in sorted(set(node_ids)) if node_id in by_id]

    def find_nodes_by_file(self, *, file: str) -> list[dict[str, Any]]:
        query = str(file or "").strip()
        if not query:
            return []
        payload = self.load()
        node_index = dict(payload.get("node_index") or {})
        by_id = dict(node_index.get("by_id") or {})
        by_file = dict(node_index.get("by_file") or {})
        node_ids = list(by_file.get(query) or [])
        if not node_ids:
            lowered = query.lower()
            for known_file in sorted(by_file):
                if lowered in str(known_file).lower():
                    node_ids.extend(by_file.get(known_file) or [])
        return [dict(by_id[node_id]) for node_id in sorted(set(node_ids)) if node_id in by_id]

    def find_semantic_nodes(
        self,
        *,
        symbol: str | None = None,
        file: str | None = None,
        language: str | None = None,
        semantic_kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        payload = self.load()
        semantic_index = dict(payload.get("semantic_index") or {})
        by_id = dict(semantic_index.get("by_id") or {})
        candidate_sets: list[set[str]] = []
        for key, value in [
            ("by_symbol", symbol),
            ("by_file", file),
            ("by_language", language),
            ("by_semantic_kind", semantic_kind),
        ]:
            query = str(value or "").strip()
            if not query:
                continue
            bucket = dict(semantic_index.get(key) or {})
            exact = set(bucket.get(query) or bucket.get(query.lower()) or [])
            if not exact:
                lowered = query.lower()
                for known, ids in bucket.items():
                    if lowered in str(known).lower():
                        exact.update(ids or [])
            candidate_sets.append(exact)
        if not candidate_sets:
            ids = set(by_id.keys())
        else:
            ids = set.intersection(*candidate_sets) if candidate_sets else set()
        return [dict(by_id[node_id]) for node_id in sorted(ids)[: max(1, int(limit))] if node_id in by_id]

    @staticmethod
    def _edges_from_index(
        index: dict[str, Any],
        node_id: str,
        allowed_edge_types: set[str] | None,
    ) -> list[dict[str, Any]]:
        bucket = dict(index or {}).get(str(node_id), {})
        rows: list[dict[str, Any]] = []
        allow = {str(item).strip().lower() for item in set(allowed_edge_types or set()) if str(item).strip()}
        for edge_type in sorted(bucket):
            if allow and edge_type not in allow:
                continue
            rows.extend(dict(item) for item in list(bucket.get(edge_type) or []) if isinstance(item, dict))
        return rows

    def outgoing_edges(self, *, node_id: str, allowed_edge_types: set[str] | None = None) -> list[dict[str, Any]]:
        payload = self.load()
        return self._edges_from_index(payload.get("outgoing_index") or {}, node_id, allowed_edge_types)

    def incoming_edges(self, *, node_id: str, allowed_edge_types: set[str] | None = None) -> list[dict[str, Any]]:
        payload = self.load()
        return self._edges_from_index(payload.get("incoming_index") or {}, node_id, allowed_edge_types)

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

    def _neighbor_steps(
        self,
        payload: dict[str, Any],
        node_id: str,
        direction: str,
        allowed_edge_types: set[str] | None,
    ) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        if direction in {"outgoing", "both"}:
            for edge in self._edges_from_index(payload.get("outgoing_index") or {}, node_id, allowed_edge_types):
                other = str(edge.get("target_id") or "").strip()
                if other:
                    steps.append({**edge, "direction_used": "outgoing", "_other_id": other})
        if direction in {"incoming", "both"}:
            for edge in self._edges_from_index(payload.get("incoming_index") or {}, node_id, allowed_edge_types):
                other = str(edge.get("source_id") or "").strip()
                if other:
                    steps.append({**edge, "direction_used": "incoming", "_other_id": other})
        steps.sort(key=lambda item: (
            str(item.get("edge_type") or ""),
            str(item.get("_other_id") or ""),
            str(item.get("direction_used") or ""),
        ))
        return steps

    def traverse_paths(
        self,
        *,
        seed_ids: list[str],
        max_depth: int,
        max_nodes: int,
        allowed_edge_types: set[str] | None = None,
        direction: str = "outgoing",
        max_paths_per_node: int = 3,
    ) -> dict[str, Any]:
        direction_name = str(direction or "outgoing").strip().lower()
        if direction_name not in {"outgoing", "incoming", "both"}:
            direction_name = "outgoing"
        payload = self.load()
        by_id = dict((payload.get("node_index") or {}).get("by_id") or {})
        seeds = sorted({str(item).strip() for item in list(seed_ids or []) if str(item).strip() and str(item).strip() in by_id})
        depth_cap = max(0, int(max_depth))
        node_cap = max(1, int(max_nodes))
        path_cap = max(1, int(max_paths_per_node))
        expansion_cap = node_cap * max(4, path_cap * 2)

        paths_by_node: dict[str, list[dict[str, Any]]] = {}
        discovery_order: list[str] = []
        cycle_count = 0
        expansions = 0
        truncated = False
        queue: list[tuple[str, int, tuple[dict[str, Any], ...], frozenset[str]]] = [
            (seed, 0, (), frozenset({seed})) for seed in seeds
        ]
        while queue:
            node_id, depth, path, on_path = queue.pop(0)
            if depth >= depth_cap:
                continue
            for step in self._neighbor_steps(payload, node_id, direction_name, allowed_edge_types):
                other_id = str(step.pop("_other_id"))
                if other_id in on_path:
                    cycle_count += 1
                    continue
                if other_id not in by_id:
                    continue
                if expansions >= expansion_cap:
                    truncated = True
                    queue.clear()
                    break
                new_path = (*path, dict(step))
                if other_id not in seeds:
                    if other_id not in paths_by_node:
                        if len(discovery_order) >= node_cap:
                            truncated = True
                            continue
                        paths_by_node[other_id] = []
                        discovery_order.append(other_id)
                    bucket = paths_by_node[other_id]
                    if len(bucket) < path_cap:
                        bucket.append({"depth": depth + 1, "edges": [dict(edge) for edge in new_path]})
                expansions += 1
                queue.append((other_id, depth + 1, new_path, on_path | {other_id}))

        result_paths = [
            {
                "node_id": node_id,
                "depth": min(entry["depth"] for entry in paths_by_node[node_id]),
                "evidence_paths": list(paths_by_node[node_id]),
            }
            for node_id in discovery_order
            if paths_by_node.get(node_id)
        ]
        return {
            "seed_ids": seeds,
            "direction": direction_name,
            "nodes": [dict(by_id[node_id]) for node_id in [*seeds, *discovery_order] if node_id in by_id],
            "paths": result_paths,
            "cycle_guarded": True,
            "cycle_count": cycle_count,
            "bounded": True,
            "truncated": truncated,
            "expansions": expansions,
        }
