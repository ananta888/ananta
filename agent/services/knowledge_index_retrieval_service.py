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
    TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
    SYMBOL_SPLIT_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")
    CODE_EXTENSIONS = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
    }
    DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
    CONFIG_EXTENSIONS = {".xml", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".properties", ".env"}
    CODE_KIND_MARKERS = (
        "class",
        "function",
        "method",
        "symbol",
        "type",
        "enum",
        "interface",
        "impl",
        "code",
    )
    DOC_KIND_MARKERS = (
        "md_",
        "doc",
        "readme",
        "adr",
        "guide",
        "architecture",
        "overview",
        "policy",
    )
    RELATION_KIND_MARKERS = ("relation", "edge", "link", "reference", "dependency", "call", "import")

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

    def _tokenize(self, value: str) -> list[str]:
        return [token.lower() for token in self.TOKEN_PATTERN.findall(value or "") if len(token) > 1]

    def _query_features(self, query: str) -> dict[str, list[str]]:
        tokens = self._tokenize(query)
        symbols: list[str] = []
        for token in tokens:
            if "_" in token or any(char.isdigit() for char in token) or (len(token) >= 6 and any(char.isupper() for char in query)):
                symbols.append(token)
        for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query or ""):
            parts = [part.lower() for part in self.SYMBOL_SPLIT_PATTERN.split(raw) if part]
            if len(parts) > 1:
                symbols.extend(parts)
        unique_tokens = sorted(set(tokens))
        unique_symbols = sorted(set(symbols))
        return {"tokens": unique_tokens, "symbols": unique_symbols}

    def _task_profile(self, task_kind: str | None, retrieval_intent: str | None) -> dict[str, Any]:
        normalized_kind = str(task_kind or "").strip().lower()
        normalized_intent = str(retrieval_intent or "").strip().lower()
        profile = {
            "record_kind_weights": {"code": 1.0, "doc": 1.0, "relation": 1.0, "other": 1.0},
            "file_kind_weights": {"code": 1.0, "doc": 1.0, "config": 1.0},
            "symbol_multiplier": 1.0,
            "relation_bonus": 0.0,
        }
        if normalized_kind in {"bugfix", "implement", "coding", "refactor", "test", "testing"}:
            profile["record_kind_weights"]["code"] = 1.25
            profile["record_kind_weights"]["relation"] = 1.15
            profile["file_kind_weights"]["code"] = 1.2
            profile["symbol_multiplier"] = 1.15
            profile["relation_bonus"] = 0.35
        if normalized_kind in {"architecture", "analysis", "doc", "research"}:
            profile["record_kind_weights"]["doc"] = 1.3
            profile["record_kind_weights"]["relation"] = 1.1
            profile["file_kind_weights"]["doc"] = 1.25
        if normalized_kind in {"config", "xml", "ops"}:
            profile["file_kind_weights"]["config"] = 1.35
            profile["record_kind_weights"]["code"] = 1.1
            profile["relation_bonus"] = 0.2

        if "architecture" in normalized_intent or "overview" in normalized_intent:
            profile["record_kind_weights"]["doc"] = max(1.35, profile["record_kind_weights"]["doc"])
            profile["file_kind_weights"]["doc"] = max(1.3, profile["file_kind_weights"]["doc"])
        if "bug" in normalized_intent or "error" in normalized_intent or "fix" in normalized_intent:
            profile["record_kind_weights"]["code"] = max(1.35, profile["record_kind_weights"]["code"])
            profile["record_kind_weights"]["relation"] = max(1.2, profile["record_kind_weights"]["relation"])
            profile["symbol_multiplier"] = max(1.2, profile["symbol_multiplier"])

        return profile

    def _record_field_texts(self, record: dict[str, Any], source_hint: str) -> dict[str, str]:
        def _join(keys: tuple[str, ...]) -> str:
            values: list[str] = []
            for key in keys:
                if key in record:
                    values.extend(self._flatten_scalars(record.get(key)))
            return " ".join(str(item).strip() for item in values if str(item).strip())

        return {
            "symbol": _join(("symbol", "symbols", "name", "title")),
            "kind": _join(("kind", "type", "tag")),
            "path": _join(("path", "file")) or source_hint,
            "relations": _join(("relation", "relations", "parents", "children", "depends_on", "references")),
            "summary": _join(("summary",)),
            "content": _join(("content", "text", "description", "snippet")),
        }

    def _record_kind_bucket(self, record_kind: str) -> str:
        normalized = str(record_kind or "").strip().lower()
        if any(marker in normalized for marker in self.CODE_KIND_MARKERS):
            return "code"
        if any(marker in normalized for marker in self.DOC_KIND_MARKERS):
            return "doc"
        if any(marker in normalized for marker in self.RELATION_KIND_MARKERS):
            return "relation"
        return "other"

    def _file_kind_bucket(self, source_hint: str) -> str:
        suffix = Path(str(source_hint or "")).suffix.lower()
        if suffix in self.CODE_EXTENSIONS:
            return "code"
        if suffix in self.DOC_EXTENSIONS:
            return "doc"
        if suffix in self.CONFIG_EXTENSIONS:
            return "config"
        return "other"

    def _weighted_token_hits(self, tokens: list[str], text: str, weight: float) -> float:
        if not tokens or not text:
            return 0.0
        haystack = text.lower()
        score = 0.0
        for token in tokens:
            count = haystack.count(token)
            if count <= 0:
                continue
            score += weight * (1.0 + (count - 1) * 0.2)
        return score

    def _score_record(
        self,
        *,
        query: str,
        query_features: dict[str, list[str]],
        field_texts: dict[str, str],
        record_kind: str,
        source_hint: str,
        profile: dict[str, Any],
    ) -> tuple[float, dict[str, float]]:
        query_tokens = list(query_features.get("tokens") or [])
        symbol_tokens = list(query_features.get("symbols") or [])
        if not query_tokens:
            return 0.0, {}

        field_weights = {
            "symbol": 4.2,
            "kind": 2.6,
            "path": 2.1,
            "relations": 2.0,
            "summary": 1.5,
            "content": 1.0,
        }
        weighted_hits = {
            field: self._weighted_token_hits(query_tokens, field_texts.get(field, ""), weight)
            for field, weight in field_weights.items()
        }
        symbol_hit_score = self._weighted_token_hits(symbol_tokens, field_texts.get("symbol", ""), 2.5) * float(
            profile.get("symbol_multiplier", 1.0)
        )
        phrase_bonus = 0.0
        compact_haystack = " ".join(field_texts.values()).lower()
        normalized_query = re.sub(r"\s+", " ", str(query or "").strip().lower())
        if normalized_query and normalized_query in compact_haystack:
            phrase_bonus = 1.8

        relation_signal = 0.0
        if field_texts.get("relations"):
            relation_signal += 0.7 + float(profile.get("relation_bonus", 0.0))
        if str(record_kind or "").strip().lower().startswith("relation"):
            relation_signal += 0.4

        record_bucket = self._record_kind_bucket(record_kind)
        file_bucket = self._file_kind_bucket(source_hint)
        record_multiplier = float((profile.get("record_kind_weights") or {}).get(record_bucket, 1.0))
        file_multiplier = float((profile.get("file_kind_weights") or {}).get(file_bucket, 1.0))
        base_score = sum(weighted_hits.values()) + symbol_hit_score + phrase_bonus + relation_signal
        score = base_score * record_multiplier * file_multiplier
        return score, {
            "base_score": round(base_score, 4),
            "record_multiplier": round(record_multiplier, 4),
            "file_multiplier": round(file_multiplier, 4),
            "symbol_hit_score": round(symbol_hit_score, 4),
            "relation_signal": round(relation_signal, 4),
            "phrase_bonus": round(phrase_bonus, 4),
            "final_score": round(score, 4),
        }

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        artifact_ids: set[str] | None = None,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
    ) -> list[ContextChunk]:
        query_features = self._query_features(query)
        profile = self._task_profile(task_kind, retrieval_intent)
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
                record_kind = str(record.get("kind", ""))
                field_texts = self._record_field_texts(record, source)
                score, breakdown = self._score_record(
                    query=query,
                    query_features=query_features,
                    field_texts=field_texts,
                    record_kind=record_kind,
                    source_hint=source,
                    profile=profile,
                )
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
                            "record_kind": record_kind,
                            "record_file": filename,
                            "source_scope": str(getattr(knowledge_index, "source_scope", "artifact")),
                            "profile_name": str(getattr(knowledge_index, "profile_name", "default")),
                            "collection_ids": collection_ids,
                            "collection_names": collection_names,
                            "retrieval_score_breakdown": breakdown,
                            "task_kind": str(task_kind or "").strip() or None,
                            "retrieval_intent": str(retrieval_intent or "").strip() or None,
                        },
                    )
                )
        ranked = sorted(
            candidates,
            key=lambda item: (-item.score, item.engine, item.source, item.content[:80]),
        )
        return ranked[: max(1, int(top_k))]


knowledge_index_retrieval_service = KnowledgeIndexRetrievalService()


def get_knowledge_index_retrieval_service() -> KnowledgeIndexRetrievalService:
    return knowledge_index_retrieval_service
