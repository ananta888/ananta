from __future__ import annotations

from typing import Any

from agent.config import settings
from agent.services.context_file_selector import provider_to_llm_scope


class SourceChatService:
    """Coordinates governed source context and the configured LLM provider."""

    def __init__(self, *, context_service=None, llm_call=None) -> None:
        if context_service is None:
            from agent.services.source_chat_context_service import get_source_chat_context_service

            context_service = get_source_chat_context_service()
        self._context_service = context_service
        self._llm_call = llm_call

    def answer(
        self,
        *,
        prompt: str,
        source_ref: str,
        include_insights: bool = True,
        include_notes: bool = False,
        max_chunks: int | None = None,
        provenance_visibility: str | None = None,
        requested_llm_scope: str | None = None,
    ) -> dict[str, Any]:
        provider = str(settings.default_provider or "").strip().lower()
        base_url = str(getattr(settings, f"{provider}_url", "") or "")
        effective_scope = provider_to_llm_scope(provider, base_url)
        requested = str(requested_llm_scope or "").strip().lower()
        if requested and requested != effective_scope:
            raise ValueError("llm_scope_mismatch")

        context = self._context_service.build_context(
            prompt=prompt,
            source_ref=source_ref,
            include_insights=include_insights,
            include_notes=include_notes,
            max_chunks=max_chunks,
            provenance_visibility=provenance_visibility,
            llm_scope=effective_scope,
        )
        if not context["selected_sources"] or not context["source_references"]:
            raise ValueError("source_context_unavailable_for_llm_scope")

        if self._llm_call is None:
            from agent.services.chat_partial_summary_service import call_llm_text

            answer = call_llm_text(context["grounded_prompt"]) or ""
        else:
            answer = self._llm_call(context["grounded_prompt"]) or ""
        bundle = dict(context.get("context_bundle") or {})
        return {
            "source_id": source_ref,
            "answer": answer,
            "source_references": context["source_references"],
            "context_hash": context["context_hash"],
            "explainability": dict(bundle.get("explainability") or {}),
            "selected_sources": context["selected_sources"],
            "budget": context["budget"],
            "llm_scope": effective_scope,
        }


def get_source_chat_service() -> SourceChatService:
    return SourceChatService()
