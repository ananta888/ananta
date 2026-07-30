"""Model-specific graph construction and bounded traversal.

This bounded context is intentionally independent from the CodeCompass source
graph.  It consumes versioned analysis artifacts rather than worker classes or
container-local model paths.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import time
from types import MappingProxyType
from typing import Mapping, Sequence


class ModelGraphError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _stable_id(model_id: str, kind: str, key: str) -> str:
    return hashlib.sha256(
        f"{model_id}\0{kind}\0{key}".encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ModelGraphNode:
    node_id: str
    kind: str
    label: str
    attributes: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(dict(self.attributes)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class ModelGraphEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {
            "edge_id": self.edge_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class ModelGraphArtifact:
    schema_version: str
    model_id: str
    nodes: tuple[ModelGraphNode, ...]
    edges: tuple[ModelGraphEdge, ...]

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
        }
        body["content_digest"] = hashlib.sha256(
            _canonical_json(body).encode("utf-8")
        ).hexdigest()
        return body


@dataclass(frozen=True)
class ModelGraphBuildPolicy:
    max_nodes: int = 100_000
    max_edges: int = 250_000

    def __post_init__(self) -> None:
        if self.max_nodes <= 0 or self.max_edges <= 0:
            raise ValueError("model graph limits must be positive")


class ModelGraphBuilder:
    def __init__(self, policy: ModelGraphBuildPolicy | None = None) -> None:
        self._policy = policy or ModelGraphBuildPolicy()

    def from_static_analysis(
        self,
        *,
        model_id: str,
        analysis: Mapping[str, object],
    ) -> ModelGraphArtifact:
        if not model_id:
            raise ModelGraphError(
                "model_graph_model_id_missing",
                "A canonical model ID is required.",
            )
        if analysis.get("schema_version") != "static_analysis.v1":
            raise ModelGraphError(
                "model_graph_analysis_schema_unsupported",
                "Static-analysis schema is unsupported.",
            )
        raw_tensors = analysis.get("tensors")
        if not isinstance(raw_tensors, list):
            raise ModelGraphError(
                "model_graph_tensors_invalid",
                "Static-analysis tensors must be an array.",
            )

        nodes: dict[str, ModelGraphNode] = {}
        edges: dict[str, ModelGraphEdge] = {}
        model_node_id = _stable_id(model_id, "model", model_id)
        nodes[model_node_id] = ModelGraphNode(
            node_id=model_node_id,
            kind="model",
            label=model_id,
            attributes={
                "parameter_count": analysis.get("parameter_count"),
                "tensor_count": analysis.get("tensor_count"),
                "total_tensor_bytes": analysis.get("total_tensor_bytes"),
            },
        )
        for raw in sorted(
            raw_tensors,
            key=lambda value: (
                str(value.get("name") or "")
                if isinstance(value, Mapping)
                else ""
            ),
        ):
            if not isinstance(raw, Mapping):
                raise ModelGraphError(
                    "model_graph_tensor_invalid",
                    "Every tensor entry must be an object.",
                )
            tensor_name = str(raw.get("name") or "")
            module_name = str(raw.get("module") or "")
            if not tensor_name or not module_name:
                raise ModelGraphError(
                    "model_graph_tensor_identity_missing",
                    "Tensor and module names are required.",
                )
            module_node_id = _stable_id(
                model_id,
                "module",
                module_name,
            )
            if module_node_id not in nodes:
                nodes[module_node_id] = ModelGraphNode(
                    node_id=module_node_id,
                    kind="module",
                    label=module_name,
                    attributes={},
                )
                self._add_edge(
                    edges,
                    model_id=model_id,
                    source=model_node_id,
                    target=module_node_id,
                    kind="contains_module",
                )

            parent_node_id = module_node_id
            layer_index = raw.get("layer_index")
            if isinstance(layer_index, int) and not isinstance(
                layer_index,
                bool,
            ):
                layer_key = f"{module_name}:{layer_index}"
                layer_node_id = _stable_id(model_id, "layer", layer_key)
                if layer_node_id not in nodes:
                    nodes[layer_node_id] = ModelGraphNode(
                        node_id=layer_node_id,
                        kind="layer",
                        label=f"layer {layer_index}",
                        attributes={"layer_index": layer_index},
                    )
                    self._add_edge(
                        edges,
                        model_id=model_id,
                        source=module_node_id,
                        target=layer_node_id,
                        kind="contains_layer",
                    )
                parent_node_id = layer_node_id

            tensor_node_id = _stable_id(
                model_id,
                "tensor",
                tensor_name,
            )
            if tensor_node_id in nodes:
                raise ModelGraphError(
                    "model_graph_tensor_id_collision",
                    "Tensor IDs must be unique within a model.",
                )
            nodes[tensor_node_id] = ModelGraphNode(
                node_id=tensor_node_id,
                kind="tensor",
                label=tensor_name,
                attributes={
                    "dtype": raw.get("dtype"),
                    "shape": raw.get("shape"),
                    "parameter_count": raw.get("parameter_count"),
                    "size_bytes": raw.get("size_bytes"),
                    "relative_path": raw.get("relative_path"),
                },
            )
            self._add_edge(
                edges,
                model_id=model_id,
                source=parent_node_id,
                target=tensor_node_id,
                kind="contains_tensor",
            )
            if (
                len(nodes) > self._policy.max_nodes
                or len(edges) > self._policy.max_edges
            ):
                raise ModelGraphError(
                    "model_graph_build_limit_exceeded",
                    "Model graph exceeds its configured build limits.",
                )
        return ModelGraphArtifact(
            schema_version="model_graph.v1",
            model_id=model_id,
            nodes=tuple(sorted(nodes.values(), key=lambda item: item.node_id)),
            edges=tuple(sorted(edges.values(), key=lambda item: item.edge_id)),
        )

    @staticmethod
    def _add_edge(
        edges: dict[str, ModelGraphEdge],
        *,
        model_id: str,
        source: str,
        target: str,
        kind: str,
    ) -> None:
        edge_id = _stable_id(
            model_id,
            "edge",
            f"{source}:{kind}:{target}",
        )
        edges[edge_id] = ModelGraphEdge(
            edge_id=edge_id,
            source_node_id=source,
            target_node_id=target,
            kind=kind,
        )


@dataclass(frozen=True)
class ModelGraphQueryPolicy:
    max_depth: int = 8
    max_nodes: int = 5000
    max_page_size: int = 100
    max_timeout_ms: int = 1000

    def __post_init__(self) -> None:
        if min(
            self.max_depth,
            self.max_nodes,
            self.max_page_size,
            self.max_timeout_ms,
        ) <= 0:
            raise ValueError("model graph query limits must be positive")


@dataclass(frozen=True)
class ModelGraphQueryResult:
    nodes: tuple[ModelGraphNode, ...]
    edges: tuple[ModelGraphEdge, ...]
    next_cursor: str | None
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            "next_cursor": self.next_cursor,
            "truncated": self.truncated,
        }


class ModelGraphQueryService:
    def __init__(
        self,
        artifact: ModelGraphArtifact,
        policy: ModelGraphQueryPolicy | None = None,
    ) -> None:
        self._artifact = artifact
        self._policy = policy or ModelGraphQueryPolicy()
        self._nodes = {item.node_id: item for item in artifact.nodes}
        adjacency: dict[str, list[tuple[str, ModelGraphEdge]]] = {}
        for edge in artifact.edges:
            adjacency.setdefault(edge.source_node_id, []).append(
                (edge.target_node_id, edge)
            )
            adjacency.setdefault(edge.target_node_id, []).append(
                (edge.source_node_id, edge)
            )
        self._adjacency = {
            key: tuple(
                sorted(value, key=lambda item: (item[0], item[1].edge_id))
            )
            for key, value in adjacency.items()
        }

    def traverse(
        self,
        *,
        start_node_id: str,
        max_depth: int = 1,
        max_nodes: int = 100,
        page_size: int = 50,
        cursor: str | None = None,
        timeout_ms: int = 200,
    ) -> ModelGraphQueryResult:
        if start_node_id not in self._nodes:
            raise ModelGraphError(
                "model_graph_start_node_missing",
                "Start node does not exist.",
            )
        if not 0 <= max_depth <= self._policy.max_depth:
            raise ModelGraphError(
                "model_graph_depth_invalid",
                "Traversal depth exceeds the server limit.",
            )
        if not 1 <= max_nodes <= self._policy.max_nodes:
            raise ModelGraphError(
                "model_graph_node_limit_invalid",
                "Traversal node limit exceeds the server limit.",
            )
        if not 1 <= page_size <= self._policy.max_page_size:
            raise ModelGraphError(
                "model_graph_page_size_invalid",
                "Page size exceeds the server limit.",
            )
        if not 1 <= timeout_ms <= self._policy.max_timeout_ms:
            raise ModelGraphError(
                "model_graph_timeout_invalid",
                "Traversal timeout exceeds the server limit.",
            )
        offset = self._decode_cursor(cursor)
        deadline = time.monotonic() + timeout_ms / 1000
        visited: set[str] = {start_node_id}
        ordered: list[str] = []
        used_edges: dict[str, ModelGraphEdge] = {}
        queue: deque[tuple[str, int]] = deque([(start_node_id, 0)])
        while queue and len(ordered) < max_nodes:
            if time.monotonic() > deadline:
                raise ModelGraphError(
                    "model_graph_query_timeout",
                    "Traversal exceeded its execution-time budget.",
                )
            node_id, depth = queue.popleft()
            ordered.append(node_id)
            if depth >= max_depth:
                continue
            for neighbour, edge in self._adjacency.get(node_id, ()):
                used_edges[edge.edge_id] = edge
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append((neighbour, depth + 1))

        page_node_ids = ordered[offset : offset + page_size]
        page_node_set = set(page_node_ids)
        page_edges = tuple(
            edge
            for edge in sorted(
                used_edges.values(),
                key=lambda item: item.edge_id,
            )
            if edge.source_node_id in page_node_set
            and edge.target_node_id in page_node_set
        )
        next_offset = offset + len(page_node_ids)
        has_more = next_offset < len(ordered)
        return ModelGraphQueryResult(
            nodes=tuple(self._nodes[node_id] for node_id in page_node_ids),
            edges=page_edges,
            next_cursor=str(next_offset) if has_more else None,
            truncated=len(ordered) >= max_nodes or has_more,
        )

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        if not cursor.isdigit():
            raise ModelGraphError(
                "model_graph_cursor_invalid",
                "Cursor is invalid.",
            )
        return int(cursor)
