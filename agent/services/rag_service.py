from __future__ import annotations

import re

from agent.config import settings
from agent.services.context_bundle_service import get_context_bundle_service
from agent.services.retrieval_service import get_retrieval_service


class RagService:
    """Central RAG facade for routes that need retrieval and grounded prompts."""

    def __init__(self, retrieval_service=None, context_bundle_service=None) -> None:
        self._retrieval_service = retrieval_service or get_retrieval_service()
        self._context_bundle_service = context_bundle_service or get_context_bundle_service()

    @staticmethod
    def _redact_sensitive(value):
        if isinstance(value, dict):
            return {str(key): RagService._redact_sensitive(item) for key, item in value.items()}
        if isinstance(value, list):
            return [RagService._redact_sensitive(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        return value

    def retrieve_context_bundle(
        self,
        query: str,
        *,
        include_context_text: bool = True,
        max_chunks: int | None = None,
        policy_mode: str = "full",
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        required_context_scope: str | None = None,
        preferred_bundle_mode: str | None = None,
        total_budget_tokens: int | None = None,
        budget_tokens_by_mode: dict[str, int] | None = None,
        window_profile: str | None = None,
        provenance_visibility: str | None = None,
        llm_scope: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        neighbor_task_ids: list[str] | None = None,
        source_types: list[str] | None = None,
        retrieval_profile: dict | None = None,
    ) -> dict[str, object]:
        context_payload = self._retrieval_service.retrieve_context(
            query,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            task_id=task_id,
            goal_id=goal_id,
            neighbor_task_ids=neighbor_task_ids,
            source_types=source_types,
            retrieval_profile=retrieval_profile,
        )
        bundle = self._context_bundle_service.build_bundle(
            query=query,
            context_payload=context_payload,
            include_context_text=include_context_text,
            max_chunks=max_chunks,
            policy_mode=policy_mode,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            required_context_scope=required_context_scope,
            preferred_bundle_mode=preferred_bundle_mode,
            total_budget_tokens=total_budget_tokens,
            budget_tokens_by_mode=budget_tokens_by_mode,
            window_profile=window_profile,
            provenance_visibility=provenance_visibility,
            llm_scope=llm_scope or "local_only",
            retrieval_profile=retrieval_profile,
        )
        if not list(bundle.get("chunks") or []):
            fallback_chunks = list(context_payload.get("chunks") or [])
            if max_chunks is not None:
                fallback_chunks = fallback_chunks[: max(1, int(max_chunks))]
            bundle["chunks"] = fallback_chunks
            bundle["chunk_count"] = len(fallback_chunks)
        chunks = list(bundle.get("chunks") or [])
        explainability = dict(bundle.get("explainability") or {})
        engines = sorted({str(chunk.get("engine") or "unknown") for chunk in chunks})
        explainability["engines"] = engines
        artifact_ids: set[str] = set()
        collection_names: set[str] = set()
        chunk_types: set[str] = set()
        source_types: list[str] = []
        source_type_counts: dict[str, int] = {}
        sources: list[dict[str, object]] = []
        visibility = str(provenance_visibility or "standard")
        for chunk in chunks:
            metadata = dict(chunk.get("metadata") or {})
            source_type = str(metadata.get("source_type") or "")
            if source_type:
                source_types.append(source_type)
                source_type_counts[source_type] = int(source_type_counts.get(source_type, 0)) + 1
            artifact_id = str(metadata.get("artifact_id") or "")
            if artifact_id:
                artifact_ids.add(artifact_id)
            for name in list(metadata.get("collection_names") or []):
                if str(name).strip():
                    collection_names.add(str(name))
            chunk_kind = str(metadata.get("record_kind") or "")
            if chunk_kind:
                chunk_types.add(chunk_kind)
            source_row: dict[str, object] = {
                "source": chunk.get("source"),
                "engine": chunk.get("engine"),
                "source_type": source_type,
            }
            if visibility == "admin":
                if metadata.get("source_id"):
                    source_row["source_id"] = metadata.get("source_id")
                if metadata.get("chunk_id"):
                    source_row["chunk_id"] = metadata.get("chunk_id")
            sources.append(source_row)
        explainability["artifact_ids"] = sorted(artifact_ids)
        explainability["collection_names"] = sorted(collection_names)
        explainability["chunk_types"] = sorted(chunk_types)
        explainability["source_types"] = sorted({item for item in source_types if item})
        explainability["source_type_counts"] = source_type_counts
        explainability["sources"] = sources[:10]
        bundle["explainability"] = explainability
        bundle["provenance_policy"] = {"visibility_level": visibility}
        # CRPS-006: attach retrieval_profile summary to bundle
        if retrieval_profile and isinstance(retrieval_profile, dict):
            bundle["retrieval_profile"] = {
                "profile_id": retrieval_profile.get("profile_id"),
                "domain": retrieval_profile.get("domain"),
                "intent": retrieval_profile.get("intent"),
                "feature_flag": retrieval_profile.get("feature_flag"),
                "selected_by": retrieval_profile.get("selected_by"),
                "warnings": list(retrieval_profile.get("warnings") or []),
                "analysis_mode": retrieval_profile.get("analysis_mode"),
                "output_intent": retrieval_profile.get("output_intent"),
                "coverage_policy": retrieval_profile.get("coverage_policy"),
                "summary_policy": retrieval_profile.get("summary_policy"),
            }
        if bool(getattr(settings, "rag_redact_sensitive", False)):
            bundle["explainability"] = self._redact_sensitive(bundle.get("explainability") or {})
            bundle["why_this_context"] = self._redact_sensitive(bundle.get("why_this_context") or {})
            bundle["selection_trace"] = self._redact_sensitive(bundle.get("selection_trace") or {})
        if include_context_text is False:
            bundle.pop("context_text", None)
        return bundle

    def build_execution_context(
        self,
        prompt: str,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_types: list[str] | None = None,
        max_chunks: int | None = None,
        retrieval_profile: dict | None = None,
    ) -> tuple[dict[str, object], str]:
        # CRPS-006: if retrieval_profile provided, derive source_types and retrieval_intent from it
        effective_source_types = source_types
        effective_intent = retrieval_intent
        if retrieval_profile and isinstance(retrieval_profile, dict):
            if effective_source_types is None:
                profile_st = list(retrieval_profile.get("source_types") or [])
                if profile_st:
                    effective_source_types = profile_st
            if not effective_intent:
                effective_intent = str(retrieval_profile.get("retrieval_intent") or "").strip() or None

        bundle = self.retrieve_context_bundle(
            prompt,
            include_context_text=True,
            max_chunks=max_chunks,
            task_kind=task_kind,
            retrieval_intent=effective_intent,
            source_types=effective_source_types,
            retrieval_profile=retrieval_profile,
        )
        grounded_prompt = self._context_bundle_service.build_grounded_prompt(
            prompt=prompt,
            context_text=str(bundle.get("context_text") or ""),
            chunks=list(bundle.get("chunks") or []),
            retrieval_profile=retrieval_profile,
        )
        return bundle, grounded_prompt


rag_service = RagService()


def get_rag_service() -> RagService:
    return rag_service
