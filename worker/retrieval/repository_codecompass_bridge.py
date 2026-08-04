"""Deterministic CodeCompass graph outputs for governed repository records."""

from __future__ import annotations

import hashlib
import heapq
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from agent.codecompass.semantic_translation.config import (
    load_semantic_translation_config,
)
from agent.codecompass.semantic_translation.registry import (
    SemanticAdapterRegistry,
    SemanticGraphExecutionPort,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class RepositoryGraphExecutionDeadlinePort(Protocol):
    def checkpoint(self) -> None: ...


def _checkpoint(
    execution_deadline: RepositoryGraphExecutionDeadlinePort | None,
) -> None:
    if execution_deadline is not None:
        execution_deadline.checkpoint()


class _BoundedSemanticGraphCollector:
    """Collect endpoint-closed semantic partitions within explicit budgets."""

    def __init__(
        self,
        *,
        max_records_per_partition: int,
        max_bytes_per_partition: int,
    ) -> None:
        self._limit = max(1, int(max_records_per_partition))
        self._max_bytes = max(1, int(max_bytes_per_partition))
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.node_bytes = 0
        self.edge_bytes = 0
        self.truncated_node_count = 0
        self.truncated_edge_count = 0
        self.unresolved_edge_count = 0

    def add_node(self, node: dict[str, Any]) -> str | None:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            return None
        if node_id in self.nodes:
            return node_id
        record_bytes = self._record_bytes(node)
        if (
            len(self.nodes) >= self._limit
            or self.node_bytes + record_bytes > self._max_bytes
        ):
            self.truncated_node_count += 1
            return None
        self.nodes[node_id] = node
        self.node_bytes += record_bytes
        return node_id

    def add_edge(
        self,
        edge: dict[str, Any],
        *,
        additional_node_ids: set[str],
    ) -> None:
        source = str(edge.get("source") or edge.get("source_id") or "").strip()
        target = str(edge.get("target") or edge.get("target_id") or "").strip()
        if not source or not target:
            self.unresolved_edge_count += 1
            return
        identity = _canonical_json(edge)
        if identity in self.edges:
            return
        source_available = source in self.nodes or source in additional_node_ids
        target_available = target in self.nodes or target in additional_node_ids
        if not source_available or not target_available:
            self.unresolved_edge_count += 1
            return
        record_bytes = self._record_bytes(edge)
        if (
            len(self.edges) >= self._limit
            or self.edge_bytes + record_bytes > self._max_bytes
        ):
            self.truncated_edge_count += 1
            return
        self.edges[identity] = edge
        self.edge_bytes += record_bytes

    @staticmethod
    def _record_bytes(record: Mapping[str, Any]) -> int:
        return len((_canonical_json(record) + "\n").encode("utf-8"))

    @property
    def truncated(self) -> bool:
        return bool(self.truncated_node_count or self.truncated_edge_count)


class _BoundedSemanticEdgeSpool:
    """Keep a deterministic, order-independent reservoir of deferred edges."""

    def __init__(self, *, max_records: int, max_bytes: int) -> None:
        self.max_records = max(1, int(max_records))
        self.max_bytes = max(1, int(max_bytes))
        # Fixed slots make retention depend only on canonical hash priority,
        # never on arrival order or on a previously evicted variable-size row.
        self.max_record_bytes = max(1, self.max_bytes // self.max_records)
        self._seen_candidate_hashes: set[bytes] = set()
        self._candidate_identity_tracking_saturated = False
        self._records: dict[str, tuple[int, int]] = {}
        self._worst_first: list[tuple[int, str]] = []
        self._byte_count = 0

    def append(self, edge: Mapping[str, Any]) -> None:
        serialized = _canonical_json(edge)
        if serialized in self._records:
            return
        digest = hashlib.sha256(serialized.encode("utf-8")).digest()
        if digest in self._seen_candidate_hashes:
            return
        if len(self._seen_candidate_hashes) < MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES:
            self._seen_candidate_hashes.add(digest)
        else:
            # Keep duplicate tracking bounded. Once saturated, the reported
            # truncation count remains a deterministic lower bound.
            self._candidate_identity_tracking_saturated = True
        serialized_bytes = len((serialized + "\n").encode("utf-8"))
        if serialized_bytes > self.max_record_bytes:
            return

        priority = int.from_bytes(digest, byteorder="big")
        self._records[serialized] = (priority, serialized_bytes)
        self._byte_count += serialized_bytes
        heapq.heappush(self._worst_first, (-priority, serialized))
        while len(self._records) > self.max_records:
            _negated_priority, evicted = heapq.heappop(self._worst_first)
            _evicted_priority, evicted_bytes = self._records.pop(evicted)
            self._byte_count -= evicted_bytes

    def records(self) -> Iterator[dict[str, Any]]:
        ordered = sorted(
            self._records,
            key=lambda serialized: (self._records[serialized][0], serialized),
        )
        for serialized in ordered:
            parsed = json.loads(serialized)
            if isinstance(parsed, dict):
                yield parsed

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def byte_count(self) -> int:
        return self._byte_count

    @property
    def truncated_edge_count(self) -> int:
        """Count distinct candidates lost to the bounded reservoir."""

        distinct_candidate_count = len(self._seen_candidate_hashes)
        if self._candidate_identity_tracking_saturated:
            distinct_candidate_count += 1
        return max(0, distinct_candidate_count - len(self._records))

    def __enter__(self) -> _BoundedSemanticEdgeSpool:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class RepositoryCodeCompassBridge:
    """Project authorized file records into generic and semantic graph JSONL."""

    def __init__(
        self,
        semantic_graph: SemanticGraphExecutionPort | None = None,
        *,
        max_semantic_records_per_partition: int | None = None,
        max_semantic_edge_candidates: int = (
            MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES
        ),
        max_semantic_edge_candidate_bytes: int = (
            MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES
        ),
    ) -> None:
        self._semantic_graph = semantic_graph or SemanticAdapterRegistry()
        configured_default = load_semantic_translation_config().max_graph_records
        configured_limit = int(
            configured_default
            if max_semantic_records_per_partition is None
            else max_semantic_records_per_partition
        )
        if configured_limit <= 0:
            raise ValueError("semantic_graph_record_limit_invalid")
        self._configured_semantic_records_per_partition = configured_limit
        self._max_semantic_records_per_partition = min(
            configured_limit,
            MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
        )
        self._max_semantic_edge_candidates = self._contract_limit(
            max_semantic_edge_candidates,
            maximum=MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
            error="semantic_graph_edge_candidate_limit_invalid",
        )
        self._max_semantic_edge_candidate_bytes = self._contract_limit(
            max_semantic_edge_candidate_bytes,
            maximum=MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
            error="semantic_graph_edge_candidate_byte_limit_invalid",
        )

    def build_outputs(
        self,
        *,
        source_id: str,
        records: Sequence[Mapping[str, Any]],
        output_dir: Path,
        execution_deadline: RepositoryGraphExecutionDeadlinePort | None = None,
    ) -> dict[str, Any]:
        _checkpoint(execution_deadline)
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
        semantic = _BoundedSemanticGraphCollector(
            max_records_per_partition=self._max_semantic_records_per_partition,
            max_bytes_per_partition=MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
        )
        diagnostic_count = 0
        semantic_file_count = 0

        # Materialize the source-grounded repository tree first. Semantic file
        # endpoints can then be bound during the one adapter pass below.
        for path, record in normalized:
            _checkpoint(execution_deadline)
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

        graph_node_ids = set(graph_nodes)
        with _BoundedSemanticEdgeSpool(
            max_records=self._max_semantic_edge_candidates,
            max_bytes=self._max_semantic_edge_candidate_bytes,
        ) as edge_spool:
            for path, record in normalized:
                _checkpoint(execution_deadline)
                content = record.get("content")
                if not isinstance(content, str):
                    continue
                emitted = self._semantic_graph.emit_graph_records(path, content)
                _checkpoint(execution_deadline)
                raw_nodes = (
                    emitted.get("nodes") if isinstance(emitted, Mapping) else []
                )
                raw_edges = (
                    emitted.get("edges") if isinstance(emitted, Mapping) else []
                )
                raw_diagnostics = (
                    emitted.get("diagnostics")
                    if isinstance(emitted, Mapping)
                    else []
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
                    accepted_id = semantic.add_node(node)
                    if accepted_id:
                        emitted_node_ids.append(accepted_id)
                file_id = self._node_id("file", path)
                if emitted_node_ids:
                    semantic_file_count += 1
                    for node_id in sorted(set(emitted_node_ids)):
                        self._add_graph_edge(
                            graph_edges,
                            source=file_id,
                            target=node_id,
                            edge_type="declares",
                        )
                for raw_edge in list(raw_edges or []):
                    if not isinstance(raw_edge, Mapping):
                        continue
                    edge = self._normalize_current_file_endpoint(
                        self._stable_semantic_record(raw_edge),
                        path=path,
                        file_id=file_id,
                    )
                    if self._semantic_edge_is_closed(
                        edge,
                        semantic_node_ids=semantic.nodes,
                        graph_node_ids=graph_node_ids,
                    ):
                        semantic.add_edge(
                            edge,
                            additional_node_ids=graph_node_ids,
                        )
                    else:
                        edge_spool.append(edge)

            # Resolve only the bounded spool after the complete semantic node
            # set is known. Adapters are never invoked a second time.
            for edge in edge_spool.records():
                _checkpoint(execution_deadline)
                semantic.add_edge(edge, additional_node_ids=graph_node_ids)
            semantic.truncated_edge_count += edge_spool.truncated_edge_count
            edge_spool_record_count = edge_spool.record_count
            edge_spool_byte_count = edge_spool.byte_count
            truncated_candidate_edge_count = edge_spool.truncated_edge_count

        _checkpoint(execution_deadline)
        self._write_jsonl(output_dir / "graph_nodes.jsonl", graph_nodes.values())
        self._write_jsonl(output_dir / "graph_edges.jsonl", graph_edges.values())
        self._write_jsonl(
            output_dir / "semantic_nodes.jsonl", semantic.nodes.values()
        )
        self._write_jsonl(
            output_dir / "semantic_edges.jsonl", semantic.edges.values()
        )
        _checkpoint(execution_deadline)
        return {
            "schema": "ananta.repository-codecompass-bridge.v1",
            "file_count": len(normalized),
            "graph_node_count": len(graph_nodes),
            "graph_edge_count": len(graph_edges),
            "semantic_node_count": len(semantic.nodes),
            "semantic_edge_count": len(semantic.edges),
            "semantic_file_count": semantic_file_count,
            "diagnostic_count": diagnostic_count,
            "semantic_budget": {
                "configured_max_records_per_partition": (
                    self._configured_semantic_records_per_partition
                ),
                "max_records_per_partition": self._max_semantic_records_per_partition,
                "max_bytes_per_partition": (
                    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION
                ),
                "configuration_clamped": (
                    self._configured_semantic_records_per_partition
                    != self._max_semantic_records_per_partition
                ),
                "truncated": semantic.truncated,
                "truncated_node_count": semantic.truncated_node_count,
                "truncated_edge_count": semantic.truncated_edge_count,
                "unresolved_edge_count": semantic.unresolved_edge_count,
                "semantic_node_bytes": semantic.node_bytes,
                "semantic_edge_bytes": semantic.edge_bytes,
                "candidate_edge_record_limit": (
                    self._max_semantic_edge_candidates
                ),
                "candidate_edge_byte_limit": (
                    self._max_semantic_edge_candidate_bytes
                ),
                "candidate_edge_count": edge_spool_record_count,
                "candidate_edge_bytes": edge_spool_byte_count,
                "truncated_candidate_edge_count": (
                    truncated_candidate_edge_count
                ),
            },
            "partitioned_outputs": {
                "graph_nodes": ["graph_nodes.jsonl"],
                "graph_edges": ["graph_edges.jsonl"],
                "semantic_nodes": ["semantic_nodes.jsonl"],
                "semantic_edges": ["semantic_edges.jsonl"],
            },
        }

    @staticmethod
    def _contract_limit(value: int, *, maximum: int, error: str) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > maximum
        ):
            raise ValueError(error)
        return value

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
    def _normalize_current_file_endpoint(
        edge: dict[str, Any],
        *,
        path: str,
        file_id: str,
    ) -> dict[str, Any]:
        """Bind adapter-declared semantic file endpoints to the source file node."""

        normalized_path = path.replace("\\", "/").removeprefix("./")
        result = dict(edge)
        attributes = dict(result.get("attributes") or {})
        for endpoint in ("source", "source_id", "target", "target_id"):
            value = str(result.get(endpoint) or "").strip()
            prefix, separator, endpoint_path = value.partition(":file:")
            normalized_endpoint_path = endpoint_path.replace("\\", "/").removeprefix("./")
            if (
                separator
                and prefix.startswith("semantic:")
                and normalized_endpoint_path == normalized_path
            ):
                result[endpoint] = file_id
                attributes[f"{endpoint}_endpoint_original"] = value
                attributes[f"{endpoint}_endpoint_binding"] = "repository_source_file"
        if attributes:
            result["attributes"] = attributes
        return result

    @staticmethod
    def _semantic_edge_is_closed(
        edge: Mapping[str, Any],
        *,
        semantic_node_ids: Mapping[str, object],
        graph_node_ids: set[str],
    ) -> bool:
        source = str(edge.get("source") or edge.get("source_id") or "").strip()
        target = str(edge.get("target") or edge.get("target_id") or "").strip()
        return bool(
            source
            and target
            and (source in semantic_node_ids or source in graph_node_ids)
            and (target in semantic_node_ids or target in graph_node_ids)
        )

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
