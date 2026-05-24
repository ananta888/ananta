"""OHA-009: MemoryTreeIngestionService.

Converts completed KnowledgeIndex records (index.jsonl / details.jsonl /
relations.jsonl) and CodeCompass graph data into MemoryTree leaves.

Design rules:
- Ingest is idempotent: identical content yields identical chunk_id → no duplicates.
- Source type tags: code | doc | relation | config | graph_node | graph_edge | unknown.
- Sensitivity defaults from the record's own sensitivity field; falls back to "internal".
- Does not depend on LLM — pure deterministic transformation.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from agent.services.memory_tree_store_service import (
    MemoryTreeStoreService,
    get_memory_tree_store_service,
)

logger = logging.getLogger(__name__)

_OUTPUT_FILENAMES = ("index.jsonl", "details.jsonl", "relations.jsonl")

_FILE_TO_SOURCE_TYPE: dict[str, str] = {
    "index.jsonl": "code",
    "details.jsonl": "doc",
    "relations.jsonl": "relation",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class IngestionStats:
    source_id: str
    created: int = 0
    skipped_duplicate: int = 0
    skipped_policy: int = 0
    errors: int = 0
    source_types: dict[str, int] = field(default_factory=dict)

    @property
    def total_processed(self) -> int:
        return self.created + self.skipped_duplicate + self.skipped_policy + self.errors

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "created": self.created,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_policy": self.skipped_policy,
            "errors": self.errors,
            "total_processed": self.total_processed,
            "source_types": self.source_types,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sensitivity_from_record(record: dict[str, Any]) -> str:
    raw = str(record.get("sensitivity") or record.get("sensitivity_label") or "internal").strip().lower()
    if raw in {"public", "internal", "internal_high", "secret", "credential", "security_sensitive"}:
        return raw
    return "internal"


def _label_from_record(record: dict[str, Any]) -> str:
    for key in ("name", "title", "path", "file", "id", "tag", "relation"):
        v = record.get(key)
        if v and isinstance(v, str):
            return v[:256]
    return str(record.get("record_id") or "")[:64]


def _content_from_record(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("content", "text", "summary", "description", "body"):
        v = record.get(key)
        if v and isinstance(v, str) and v.strip():
            parts.append(v.strip())
    if not parts:
        # Fallback: serialize non-empty scalar fields
        for k, v in record.items():
            if k in {"sensitivity", "sensitivity_label", "record_id", "id"}:
                continue
            if isinstance(v, (str, int, float)) and str(v).strip():
                parts.append(f"{k}: {v}")
    return "\n".join(parts)[:4000]


def _provenance_from_record(record: dict[str, Any], source_type: str) -> str:
    file_path = record.get("file") or record.get("path") or ""
    record_id = record.get("record_id") or record.get("id") or ""
    parts = []
    if file_path:
        parts.append(f"file:{file_path}")
    if record_id:
        parts.append(f"record:{record_id}")
    if source_type:
        parts.append(f"type:{source_type}")
    return "|".join(parts)


def _iter_jsonl_records(output_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    for filename in _OUTPUT_FILENAMES:
        path = output_dir / filename
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield filename, payload
        except OSError:
            continue


# ---------------------------------------------------------------------------
# MemoryTreeIngestionService
# ---------------------------------------------------------------------------

class MemoryTreeIngestionService:
    """Ingest KnowledgeIndex and CodeCompass records into the Memory Tree."""

    def __init__(self, store: MemoryTreeStoreService | None = None) -> None:
        self._store = store or get_memory_tree_store_service()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_knowledge_index(
        self,
        *,
        knowledge_index_id: str,
        output_dir: str | Path,
        created_by: str | None = None,
        sensitivity_ceiling: str = "internal_high",
        enabled: bool = True,
    ) -> IngestionStats:
        """
        Ingest all records from a completed KnowledgeIndex output directory.

        Args:
            knowledge_index_id: ID of the KnowledgeIndex (used as source_id).
            output_dir: Path to the directory containing index.jsonl etc.
            created_by: Task or goal ID that triggered the ingest.
            sensitivity_ceiling: Records with higher sensitivity are skipped.
            enabled: If False, returns empty stats without touching DB.
        """
        stats = IngestionStats(source_id=knowledge_index_id)
        if not enabled:
            return stats

        output_path = Path(output_dir)
        if not output_path.exists():
            logger.warning("MemoryTreeIngestionService: output_dir %s not found", output_path)
            return stats

        _ceiling_order = ["public", "internal", "internal_high", "secret", "credential", "security_sensitive"]
        ceiling_idx = _ceiling_order.index(sensitivity_ceiling) if sensitivity_ceiling in _ceiling_order else 2

        for filename, record in _iter_jsonl_records(output_path):
            source_type = _FILE_TO_SOURCE_TYPE.get(filename, "unknown")
            sensitivity = _sensitivity_from_record(record)
            sens_idx = _ceiling_order.index(sensitivity) if sensitivity in _ceiling_order else 1

            if sens_idx > ceiling_idx:
                stats.skipped_policy += 1
                continue

            label = _label_from_record(record)
            content = _content_from_record(record)
            if not content.strip():
                continue

            try:
                _, created = self._store.ingest_chunk(
                    source_id=knowledge_index_id,
                    source_type=source_type,
                    label=label,
                    content=content,
                    scope="source",
                    kind="leaf",
                    sensitivity=sensitivity,
                    provenance_ref=_provenance_from_record(record, source_type),
                    created_by=created_by,
                    metadata={
                        "filename": filename,
                        "record_id": str(record.get("record_id") or record.get("id") or ""),
                    },
                )
                if created:
                    stats.created += 1
                    stats.source_types[source_type] = stats.source_types.get(source_type, 0) + 1
                else:
                    stats.skipped_duplicate += 1
            except Exception as exc:
                logger.warning("MemoryTreeIngestionService: ingest failed for record — %s", exc)
                stats.errors += 1

        logger.info(
            "MemoryTreeIngestionService: ingest complete for %s — %s",
            knowledge_index_id,
            stats.as_dict(),
        )

        # Enqueue seal job if threshold reached
        chunk_count = self._store.count_chunks(knowledge_index_id, lifecycle="admitted")
        if chunk_count > 0:
            self._store.enqueue_job(
                kind="append_buffer",
                payload={"source_id": knowledge_index_id, "chunk_count": chunk_count},
                dedupe_key=f"append_buffer:{knowledge_index_id}",
            )

        return stats

    def ingest_codecompass_graph(
        self,
        *,
        knowledge_index_id: str,
        graph_artifact: dict,
        created_by: str | None = None,
        enabled: bool = True,
    ) -> IngestionStats:
        """
        Ingest a domain_graph_artifact.v1 payload into the Memory Tree.
        Nodes become graph_node leaves; edges become graph_edge leaves.
        """
        stats = IngestionStats(source_id=knowledge_index_id)
        if not enabled:
            return stats

        nodes = graph_artifact.get("nodes") or []
        edges = graph_artifact.get("edges") or []

        for node in nodes:
            if not isinstance(node, dict):
                continue
            attrs = node.get("attributes") or {}
            label = str(attrs.get("name") or node.get("node_id") or "")[:256]
            content_parts = []
            if attrs.get("content"):
                content_parts.append(str(attrs["content"]))
            if attrs.get("file"):
                content_parts.append(f"file: {attrs['file']}")
            content = "\n".join(content_parts) or label
            try:
                _, created = self._store.ingest_chunk(
                    source_id=knowledge_index_id,
                    source_type="graph_node",
                    label=label,
                    content=content,
                    scope="source",
                    kind="leaf",
                    sensitivity="internal",
                    provenance_ref=f"node:{node.get('node_id')}|type:{node.get('node_type')}",
                    created_by=created_by,
                    metadata={"node_id": node.get("node_id"), "node_type": node.get("node_type")},
                )
                if created:
                    stats.created += 1
                    stats.source_types["graph_node"] = stats.source_types.get("graph_node", 0) + 1
                else:
                    stats.skipped_duplicate += 1
            except Exception as exc:
                logger.warning("MemoryTreeIngestionService: graph node ingest failed — %s", exc)
                stats.errors += 1

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            label = f"{edge.get('source_id')} --[{edge.get('relation')}]--> {edge.get('target_id')}"
            attrs = edge.get("attributes") or {}
            content = f"relation: {edge.get('relation')}\nconfidence: {attrs.get('confidence', 1.0)}"
            try:
                _, created = self._store.ingest_chunk(
                    source_id=knowledge_index_id,
                    source_type="graph_edge",
                    label=label[:256],
                    content=content,
                    scope="source",
                    kind="leaf",
                    sensitivity="internal",
                    provenance_ref=f"edge:{edge.get('source_id')}:{edge.get('target_id')}",
                    created_by=created_by,
                    metadata={
                        "source_id": edge.get("source_id"),
                        "target_id": edge.get("target_id"),
                        "relation": edge.get("relation"),
                    },
                )
                if created:
                    stats.created += 1
                    stats.source_types["graph_edge"] = stats.source_types.get("graph_edge", 0) + 1
                else:
                    stats.skipped_duplicate += 1
            except Exception as exc:
                logger.warning("MemoryTreeIngestionService: graph edge ingest failed — %s", exc)
                stats.errors += 1

        return stats


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_memory_tree_ingestion_service: MemoryTreeIngestionService | None = None


def get_memory_tree_ingestion_service() -> MemoryTreeIngestionService:
    global _memory_tree_ingestion_service
    if _memory_tree_ingestion_service is None:
        _memory_tree_ingestion_service = MemoryTreeIngestionService()
    return _memory_tree_ingestion_service
