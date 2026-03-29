from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agent.hybrid_orchestrator import ContextChunk
from agent.repository import knowledge_index_repo, knowledge_link_repo


class KnowledgeIndexRetrievalService:
    """Reads completed rag-helper outputs as an additive retrieval source."""

    OUTPUT_FILENAMES = ("index.jsonl", "details.jsonl", "relations.jsonl")
    FIELD_EXCLUDE_KEYS = {"id", "parent_id", "node_id", "edge_id", "hash", "sha1", "sha256"}

    def __init__(self, knowledge_index_repository=None, knowledge_link_repository=None) -> None:
        self._knowledge_index_repository = knowledge_index_repository or knowledge_index_repo
        self._knowledge_link_repository = knowledge_link_repository or knowledge_link_repo

    def _collection_metadata(self, artifact_id: str) -> tuple[list[str], list[str]]:
        if not artifact_id:
            return [], []
        links = self._knowledge_link_repository.get_by_artifact(artifact_id)
        collection_ids: list[str] = []
        collection_names: list[str] = []
        for link in links:
            collection_id = str(getattr(link, "collection_id", "") or "").strip()
            collection_name = str(((getattr(link, "link_metadata", None) or {}).get("collection_name")) or "").strip()
            if collection_id and collection_id not in collection_ids:
                collection_ids.append(collection_id)
            if collection_name and collection_name not in collection_names:
                collection_names.append(collection_name)
        return collection_ids, collection_names

    def _iter_completed_indices(self):
        return self._knowledge_index_repository.list_completed()

    def _iter_output_records(self, output_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
        for filename in self.OUTPUT_FILENAMES:
            path = output_dir / filename
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield filename, payload
            except OSError:
                continue

    def _flatten_scalars(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int, float, bool)):
            return [str(value)]
        if isinstance(value, dict):
            parts: list[str] = []
            for key, nested in value.items():
                if key in self.FIELD_EXCLUDE_KEYS:
                    continue
                parts.extend(self._flatten_scalars(nested))
            return parts
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                parts.extend(self._flatten_scalars(item))
            return parts
        return []

    def _record_text(self, record: dict[str, Any]) -> str:
        preferred_parts: list[str] = []
        for key in ("title", "name", "content", "text", "path", "tag", "relation", "file", "kind"):
            if key in record:
                preferred_parts.extend(self._flatten_scalars(record.get(key)))
        if "summary" in record:
            preferred_parts.extend(self._flatten_scalars(record.get("summary")))
        if "symbols" in record:
            preferred_parts.extend(self._flatten_scalars(record.get("symbols")))
        all_parts = preferred_parts + self._flatten_scalars(record)
        compact = " ".join(part.strip() for part in all_parts if str(part).strip())
        return re.sub(r"\s+", " ", compact).strip()[:2000]

    def _score_record(self, query: str, record_text: str, source_hint: str) -> float:
        query_tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query) if len(token) > 2]
        if not query_tokens:
            return 0.0
        haystack = f"{source_hint} {record_text}".lower()
        score = 0.0
        for token in query_tokens:
            occurrences = haystack.count(token)
            if occurrences:
                score += 1.0 + (occurrences - 1) * 0.15
        return score

    def search(self, query: str, *, top_k: int = 4, artifact_ids: set[str] | None = None) -> list[ContextChunk]:
        candidates: list[ContextChunk] = []
        for knowledge_index in self._iter_completed_indices():
            artifact_id = str(getattr(knowledge_index, "artifact_id", "") or "")
            if artifact_ids is not None and artifact_id not in artifact_ids:
                continue
            collection_ids, collection_names = self._collection_metadata(artifact_id)
            output_dir_raw = getattr(knowledge_index, "output_dir", None)
            if not output_dir_raw:
                continue
            output_dir = Path(output_dir_raw)
            if not output_dir.exists():
                continue
            for filename, record in self._iter_output_records(output_dir):
                source = str(record.get("file") or record.get("path") or getattr(knowledge_index, "artifact_id", "knowledge-index"))
                record_text = self._record_text(record)
                if not record_text:
                    continue
                score = self._score_record(query, record_text, source)
                if score <= 0:
                    continue
                candidates.append(
                    ContextChunk(
                        engine="knowledge_index",
                        source=source,
                        content=record_text,
                        score=score,
                        metadata={
                            "knowledge_index_id": str(getattr(knowledge_index, "id", "")),
                            "artifact_id": artifact_id,
                            "record_kind": str(record.get("kind", "")),
                            "record_file": filename,
                            "source_scope": str(getattr(knowledge_index, "source_scope", "artifact")),
                            "profile_name": str(getattr(knowledge_index, "profile_name", "default")),
                            "collection_ids": collection_ids,
                            "collection_names": collection_names,
                        },
                    )
                )
        ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
        return ranked[:top_k]


knowledge_index_retrieval_service = KnowledgeIndexRetrievalService()


def get_knowledge_index_retrieval_service() -> KnowledgeIndexRetrievalService:
    return knowledge_index_retrieval_service
