"""Deterministic CodeCompass graph outputs for governed repository records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.codecompass.semantic_translation.registry import (
    SemanticAdapterRegistry,
    SemanticGraphExecutionPort,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class RepositoryCodeCompassBridge:
    """Project authorized file records into generic and semantic graph JSONL."""

    def __init__(
        self,
        semantic_graph: SemanticGraphExecutionPort | None = None,
    ) -> None:
        self._semantic_graph = semantic_graph or SemanticAdapterRegistry()

    def build_outputs(
        self,
        *,
        source_id: str,
        records: Sequence[Mapping[str, Any]],
        output_dir: Path,
    ) -> dict[str, Any]:
        normalized = self._normalized_records(records)
        if not normalized:
            raise ValueError("repository_graph_source_records_empty")

        output_dir.mkdir(parents=True, exist_ok=True)
        root_id = self._node_id("repository", str(source_id))
        graph_nodes: dict[str, dict[str, Any]] = {
            root_id: {
                "id": root_id,
                "kind": "repository",
                "name": str(source_id),
            }
        }
        graph_edges: dict[str, dict[str, Any]] = {}
        semantic_nodes: dict[str, dict[str, Any]] = {}
        semantic_edges: dict[str, dict[str, Any]] = {}
        diagnostic_count = 0
        semantic_file_count = 0

        for path, record in normalized:
            file_id = self._node_id("file", path)
            graph_nodes[file_id] = {
                "id": file_id,
                "kind": "source_file",
                "name": Path(path).name,
                "file": path,
                "path": path,
                "line_count": self._line_count(record.get("content")),
            }
            parent_id = self._materialize_directories(
                path=path,
                root_id=root_id,
                nodes=graph_nodes,
                edges=graph_edges,
            )
            self._add_graph_edge(
                graph_edges,
                source=parent_id,
                target=file_id,
                edge_type="contains_file",
            )

            content = record.get("content")
            if not isinstance(content, str):
                continue
            emitted = self._semantic_graph.emit_graph_records(path, content)
            raw_nodes = emitted.get("nodes") if isinstance(emitted, Mapping) else []
            raw_edges = emitted.get("edges") if isinstance(emitted, Mapping) else []
            raw_diagnostics = (
                emitted.get("diagnostics") if isinstance(emitted, Mapping) else []
            )
            diagnostic_count += len(raw_diagnostics or [])
            emitted_node_ids: list[str] = []
            for raw_node in list(raw_nodes or []):
                if not isinstance(raw_node, Mapping):
                    continue
                node = self._stable_semantic_record(raw_node)
                node_id = str(node.get("id") or "").strip()
                if not node_id:
                    continue
                semantic_nodes.setdefault(node_id, node)
                emitted_node_ids.append(node_id)
            for raw_edge in list(raw_edges or []):
                if not isinstance(raw_edge, Mapping):
                    continue
                edge = self._stable_semantic_record(raw_edge)
                source = str(edge.get("source") or edge.get("source_id") or "").strip()
                target = str(edge.get("target") or edge.get("target_id") or "").strip()
                if not source or not target:
                    continue
                semantic_edges.setdefault(_canonical_json(edge), edge)
            if emitted_node_ids:
                semantic_file_count += 1
                for node_id in sorted(set(emitted_node_ids)):
                    self._add_graph_edge(
                        graph_edges,
                        source=file_id,
                        target=node_id,
                        edge_type="declares",
                    )

        self._write_jsonl(output_dir / "graph_nodes.jsonl", graph_nodes.values())
        self._write_jsonl(output_dir / "graph_edges.jsonl", graph_edges.values())
        self._write_jsonl(
            output_dir / "semantic_nodes.jsonl", semantic_nodes.values()
        )
        self._write_jsonl(
            output_dir / "semantic_edges.jsonl", semantic_edges.values()
        )
        return {
            "schema": "ananta.repository-codecompass-bridge.v1",
            "file_count": len(normalized),
            "graph_node_count": len(graph_nodes),
            "graph_edge_count": len(graph_edges),
            "semantic_node_count": len(semantic_nodes),
            "semantic_edge_count": len(semantic_edges),
            "semantic_file_count": semantic_file_count,
            "diagnostic_count": diagnostic_count,
            "partitioned_outputs": {
                "graph_nodes": ["graph_nodes.jsonl"],
                "graph_edges": ["graph_edges.jsonl"],
                "semantic_nodes": ["semantic_nodes.jsonl"],
                "semantic_edges": ["semantic_edges.jsonl"],
            },
        }

    @classmethod
    def _normalized_records(
        cls,
        records: Sequence[Mapping[str, Any]],
    ) -> list[tuple[str, dict[str, Any]]]:
        by_path: dict[str, dict[str, Any]] = {}
        for raw in records:
            record = dict(raw)
            path = cls._relative_path(record)
            if not path:
                continue
            current = by_path.get(path)
            if current is None or _canonical_json(record) < _canonical_json(current):
                by_path[path] = record
        return [(path, by_path[path]) for path in sorted(by_path)]

    @staticmethod
    def _relative_path(record: Mapping[str, Any]) -> str:
        metadata = record.get("metadata")
        values = metadata if isinstance(metadata, Mapping) else {}
        raw = str(
            values.get("relative_path")
            or record.get("file")
            or record.get("path")
            or record.get("id")
            or ""
        ).strip().replace("\\", "/")
        parts = raw.split("/")
        if (
            not raw
            or raw.startswith("/")
            or "\x00" in raw
            or any(part in {"", ".", ".."} for part in parts)
        ):
            return ""
        return "/".join(parts)

    @classmethod
    def _materialize_directories(
        cls,
        *,
        path: str,
        root_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: dict[str, dict[str, Any]],
    ) -> str:
        parts = path.split("/")[:-1]
        parent_id = root_id
        for depth in range(1, len(parts) + 1):
            directory = "/".join(parts[:depth])
            directory_id = cls._node_id("directory", directory)
            nodes.setdefault(
                directory_id,
                {
                    "id": directory_id,
                    "kind": "directory",
                    "name": parts[depth - 1],
                    "path": directory,
                },
            )
            cls._add_graph_edge(
                edges,
                source=parent_id,
                target=directory_id,
                edge_type="contains_directory",
            )
            parent_id = directory_id
        return parent_id

    @staticmethod
    def _stable_semantic_record(raw: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(raw)
        attributes = record.get("attributes")
        if isinstance(attributes, Mapping):
            stable_attributes = dict(attributes)
            # Semantic adapters also emit every method as a function_signature
            # node connected through a declares edge. Keeping the full nested
            # method snapshots here duplicates large parameter/type payloads
            # without adding graph information.
            stable_attributes.pop("methods", None)
            record["attributes"] = stable_attributes
        provenance = record.get("provenance")
        if isinstance(provenance, Mapping):
            stable_provenance = dict(provenance)
            stable_provenance.pop("created_at", None)
            record["provenance"] = stable_provenance
        return record

    @staticmethod
    def _node_id(kind: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
        return f"source:{kind}:{digest}"

    @staticmethod
    def _line_count(content: object) -> int:
        if not isinstance(content, str) or not content:
            return 0
        return content.count("\n") + 1

    @staticmethod
    def _add_graph_edge(
        edges: dict[str, dict[str, Any]],
        *,
        source: str,
        target: str,
        edge_type: str,
    ) -> None:
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
            "directed": True,
        }
        edges.setdefault(_canonical_json(edge), edge)

    @staticmethod
    def _write_jsonl(path: Path, records: object) -> None:
        rows = sorted(_canonical_json(dict(item)) for item in records)
        path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


__all__ = ["RepositoryCodeCompassBridge"]
