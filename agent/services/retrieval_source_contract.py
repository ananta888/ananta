from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

SOURCE_TYPES: tuple[str, ...] = ("repo", "artifact", "task_memory", "wiki")
_SOURCE_TYPE_SET = set(SOURCE_TYPES)
_NORMALIZE_SPACES = re.compile(r"\s+")


@runtime_checkable
class RetrievalSourceAdapter(Protocol):
    """Narrow source adapter contract for retrieval providers."""

    source_type: str

    def search(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Return retrieval chunks for a query."""


@dataclass(frozen=True)
class SourceSelectionPolicy:
    enabled_source_types: frozenset[str]
    requested_source_types: tuple[str, ...]
    effective_source_types: frozenset[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": sorted(self.enabled_source_types),
            "requested": list(self.requested_source_types),
            "effective": sorted(self.effective_source_types),
        }


def normalize_requested_source_types(source_types: list[str] | None) -> tuple[str, ...]:
    if source_types is None:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in source_types:
        value = str(item or "").strip().lower()
        if not value:
            continue
        if value not in _SOURCE_TYPE_SET:
            raise ValueError(f"invalid_source_type:{value}")
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return tuple(normalized)


def enabled_source_types_from_settings(settings) -> frozenset[str]:
    enabled: set[str] = set()
    if bool(getattr(settings, "rag_source_repo_enabled", True)):
        enabled.add("repo")
    if bool(getattr(settings, "rag_source_artifact_enabled", True)):
        enabled.add("artifact")
    if bool(getattr(settings, "rag_source_task_memory_enabled", True)):
        enabled.add("task_memory")
    if bool(getattr(settings, "rag_source_wiki_enabled", False)):
        enabled.add("wiki")
    return frozenset(enabled)


def resolve_source_selection_policy(*, settings, requested_source_types: list[str] | None) -> SourceSelectionPolicy:
    enabled = enabled_source_types_from_settings(settings)
    requested = normalize_requested_source_types(requested_source_types)
    effective = frozenset(enabled.intersection(requested)) if requested else enabled
    if not effective:
        raise ValueError("no_retrieval_source_enabled")
    return SourceSelectionPolicy(
        enabled_source_types=enabled,
        requested_source_types=requested,
        effective_source_types=effective,
    )


def infer_source_type(*, engine: str, metadata: Mapping[str, Any] | None) -> str:
    payload = dict(metadata or {})
    explicit = str(payload.get("source_type") or "").strip().lower()
    if explicit in _SOURCE_TYPE_SET:
        return explicit

    source_scope = str(payload.get("source_scope") or "").strip().lower()
    if source_scope == "wiki":
        return "wiki"
    if source_scope in {"artifact", "knowledge"}:
        return "artifact"

    normalized_engine = str(engine or "").strip().lower()
    if normalized_engine == "result_memory":
        return "task_memory"
    if normalized_engine == "knowledge_index":
        if any(
            str(payload.get(key) or "").strip()
            for key in ("wiki_article_id", "article_title", "wiki_article_title", "section_title")
        ):
            return "wiki"
        return "artifact"
    return "repo"


def infer_source_id(*, source_type: str, source: str, metadata: Mapping[str, Any] | None) -> str:
    payload = dict(metadata or {})
    fallback = str(source or "").strip() or "unknown"
    if source_type == "artifact":
        return (
            str(payload.get("artifact_id") or "").strip()
            or str(payload.get("knowledge_index_id") or "").strip()
            or fallback
        )
    if source_type == "wiki":
        return (
            str(payload.get("wiki_article_id") or "").strip()
            or str(payload.get("article_title") or "").strip()
            or str(payload.get("wiki_article_title") or "").strip()
            or fallback
        )
    if source_type == "task_memory":
        return (
            str(payload.get("source_task_id") or "").strip()
            or str(payload.get("memory_entry_id") or "").strip()
            or fallback
        )
    return fallback


def source_scopes_for_types(source_types: set[str] | frozenset[str]) -> set[str]:
    scopes: set[str] = set()
    if "artifact" in source_types:
        scopes.add("artifact")
    if "wiki" in source_types:
        scopes.add("wiki")
    return scopes


def build_citation(
    *,
    source_type: str,
    source_id: str,
    source: str,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    citation: dict[str, Any] = {"source_type": source_type, "source_id": source_id}
    if source_type == "repo":
        citation["path"] = source
    elif source_type == "artifact":
        citation["artifact_id"] = payload.get("artifact_id")
        citation["knowledge_index_id"] = payload.get("knowledge_index_id")
        citation["record_kind"] = payload.get("record_kind")
    elif source_type == "task_memory":
        citation["task_id"] = payload.get("source_task_id")
        citation["memory_entry_id"] = payload.get("memory_entry_id")
    elif source_type == "wiki":
        citation["article_title"] = payload.get("article_title") or payload.get("wiki_article_title")
        citation["section_title"] = payload.get("section_title")
        citation["language"] = payload.get("language")
    return citation


def _build_chunk_id(*, source_type: str, source_id: str, engine: str, source: str, content: str, metadata: Mapping[str, Any]) -> str:
    explicit = str(metadata.get("chunk_id") or "").strip()
    if explicit:
        return explicit
    record_id = str(metadata.get("record_id") or metadata.get("id") or "").strip()
    if record_id:
        return f"{source_type}:{record_id}"
    content_sig = _NORMALIZE_SPACES.sub(" ", str(content or "").strip().lower())[:600]
    digest = hashlib.sha1(f"{engine}|{source_id}|{source}|{content_sig}".encode("utf-8")).hexdigest()[:16]
    return f"{source_type}:{digest}"


def normalize_chunk_metadata(
    *,
    engine: str,
    source: str,
    content: str,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    source_type = infer_source_type(engine=engine, metadata=payload)
    source_id = infer_source_id(source_type=source_type, source=source, metadata=payload)
    chunk_id = _build_chunk_id(
        source_type=source_type,
        source_id=source_id,
        engine=str(engine or ""),
        source=str(source or ""),
        content=str(content or ""),
        metadata=payload,
    )
    payload["source_type"] = source_type
    payload["source_id"] = source_id
    payload["chunk_id"] = chunk_id
    payload["citation"] = build_citation(source_type=source_type, source_id=source_id, source=source, metadata=payload)
    payload["provenance"] = {
        "engine": str(engine or ""),
        "source": str(source or ""),
        "source_scope": str(payload.get("source_scope") or "").strip() or None,
        "record_kind": str(payload.get("record_kind") or "").strip() or None,
    }
    return payload
