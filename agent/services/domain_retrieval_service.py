from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from agent.services.rag_source_profile_loader import RagSourceProfileLoader

_UNBOUNDED_QUERY_PATTERN = re.compile(
    r"\b(full|entire|complete|all)\s+(repo|repository|codebase|docs|documentation)\b",
    flags=re.IGNORECASE,
)


@runtime_checkable
class DomainRetrievalBackend(Protocol):
    def retrieve_context(
        self,
        query: str,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        neighbor_task_ids: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> dict[str, object]:
        """Return retrieval chunks in the shared retrieval payload format."""


class DomainRetrievalService:
    """Domain-scoped retrieval facade with bounded output and guardrails."""

    def __init__(
        self,
        *,
        rag_profile_loader: RagSourceProfileLoader,
        retrieval_backend: DomainRetrievalBackend | None = None,
        max_results_default: int = 5,
        max_results_limit: int = 12,
    ) -> None:
        self.rag_profile_loader = rag_profile_loader
        self.retrieval_backend = retrieval_backend or self._build_default_backend()
        self.max_results_default = max(1, int(max_results_default))
        self.max_results_limit = max(self.max_results_default, int(max_results_limit))

    def retrieve(
        self,
        *,
        domain_id: str,
        retrieval_intent: str,
        query: str,
        context_summary: dict[str, Any] | None = None,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        normalized_domain = str(domain_id).strip()
        normalized_intent = str(retrieval_intent).strip()
        normalized_query = str(query).strip()
        if not normalized_domain or not normalized_intent or not normalized_query:
            return {
                "status": "degraded",
                "reason": "invalid_retrieval_input",
                "domain_id": normalized_domain,
                "retrieval_intent": normalized_intent,
                "chunks": [],
            }
        if self._is_unbounded_query(normalized_query):
            return {
                "status": "rejected",
                "reason": "query_scope_unbounded",
                "domain_id": normalized_domain,
                "retrieval_intent": normalized_intent,
                "chunks": [],
                "usage_limits": self._usage_limits(max_results=max_results),
            }

        profiles = self.rag_profile_loader.profiles_for_retrieval(
            normalized_domain,
            retrieval_intent=normalized_intent,
            max_profiles=max(3, self.max_results_limit),
        )
        if not profiles:
            return {
                "status": "degraded",
                "reason": "no_rag_profiles",
                "domain_id": normalized_domain,
                "retrieval_intent": normalized_intent,
                "chunks": [],
                "usage_limits": self._usage_limits(max_results=max_results),
            }

        limits = self._usage_limits(max_results=max_results)
        source_types = self._resolve_source_types(profiles)
        payload = self.retrieval_backend.retrieve_context(
            normalized_query,
            task_kind="analysis",
            retrieval_intent=normalized_intent,
            source_types=source_types,
        )
        raw_chunks = [item for item in list(payload.get("chunks") or []) if isinstance(item, dict)]
        normalized_chunks = [self._normalize_chunk(item) for item in raw_chunks[: limits["max_results"]]]
        return {
            "status": "ok",
            "reason": "retrieval_completed",
            "domain_id": normalized_domain,
            "retrieval_intent": normalized_intent,
            "context_summary": dict(context_summary or {}),
            "chunks": normalized_chunks,
            "usage_limits": limits,
            "applied_profiles": [str(profile.get("source_id") or "") for profile in profiles],
            "source_types": source_types,
        }

    def _usage_limits(self, *, max_results: int | None) -> dict[str, int]:
        requested = self.max_results_default if max_results is None else int(max_results)
        bounded = max(1, min(requested, self.max_results_limit))
        return {
            "max_results": bounded,
            "max_results_default": self.max_results_default,
            "max_results_limit": self.max_results_limit,
        }

    @staticmethod
    def _is_unbounded_query(query: str) -> bool:
        normalized = str(query or "").strip().lower()
        if normalized in {"*", "all", "everything"}:
            return True
        return bool(_UNBOUNDED_QUERY_PATTERN.search(normalized))

    @staticmethod
    def _normalize_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(chunk.get("metadata") or {})
        citation = dict(metadata.get("citation") or {})
        fusion = dict(metadata.get("fusion") or {})
        score = float(chunk.get("score") or 0.0)
        source = str(chunk.get("source") or "")
        source_id = str(metadata.get("source_id") or source)
        ref = str(metadata.get("ref") or metadata.get("revision") or metadata.get("import_revision") or "")
        path = str(citation.get("path") or metadata.get("path") or source)
        symbol_or_section = str(
            metadata.get("symbol") or metadata.get("section_title") or metadata.get("record_kind") or ""
        )
        reason = str(metadata.get("reason") or "retrieval_match")
        if "query_overlap" in fusion:
            reason = f"fusion_overlap:{fusion.get('query_overlap')}"
        return {
            "source_id": source_id,
            "ref": ref,
            "path": path,
            "symbol_or_section": symbol_or_section,
            "score": round(score, 6),
            "reason": reason,
        }

    @staticmethod
    def _resolve_source_types(profiles: list[dict[str, Any]]) -> list[str]:
        source_types: list[str] = []
        seen: set[str] = set()
        for profile in profiles:
            explicit = [
                str(item).strip()
                for item in list(profile.get("retrieval_source_types") or [])
                if str(item).strip()
            ]
            candidates = explicit if explicit else DomainRetrievalService._source_types_from_profile(profile)
            for candidate in candidates:
                if candidate in {"repo", "artifact", "wiki", "task_memory"} and candidate not in seen:
                    seen.add(candidate)
                    source_types.append(candidate)
        return source_types or ["repo", "artifact"]

    @staticmethod
    def _source_types_from_profile(profile: dict[str, Any]) -> list[str]:
        source_type = str(profile.get("source_type") or "").strip().lower()
        if source_type in {"source_code", "examples"}:
            return ["repo", "artifact"]
        if source_type in {"api_docs", "internal_docs", "project_reference"}:
            return ["artifact"]
        return ["repo"]

    @staticmethod
    def _build_default_backend() -> DomainRetrievalBackend:
        from agent.services.retrieval_service import get_retrieval_service

        backend = get_retrieval_service()
        if not isinstance(backend, DomainRetrievalBackend):
            raise TypeError("retrieval backend does not implement DomainRetrievalBackend")
        return backend
