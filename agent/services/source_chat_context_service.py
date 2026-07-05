from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from agent.sources.open_notebook_source_reference import build_source_references_for_chunks

DEFAULT_MAX_CHUNKS = 6
DEFAULT_MAX_CONTEXT_CHARS = 6000


class SourceChatContextService:
    """Builds budgeted, source-focused chat context for a single imported
    OpenNotebook source, snapshot or collection.

    The heavy lifting stays in RagService.retrieve_context_bundle with
    source_types=['open_notebook']; this service filters to the requested
    source, applies include_insights/include_notes, enforces chunk and char
    budgets and produces source references plus a grounded prompt.
    """

    def __init__(
        self,
        *,
        rag_service=None,
        source_registry=None,
        grounded_prompt_builder: Callable[..., str] | None = None,
    ) -> None:
        if rag_service is None:
            from agent.services.rag_service import get_rag_service

            rag_service = get_rag_service()
        if source_registry is None:
            from agent.sources.source_registry import SourceRegistry

            source_registry = SourceRegistry()
        if grounded_prompt_builder is None:
            from agent.services.context_bundle_service import ContextBundleService

            grounded_prompt_builder = ContextBundleService.build_grounded_prompt
        self._rag_service = rag_service
        self._source_registry = source_registry
        self._grounded_prompt_builder = grounded_prompt_builder

    def build_context(
        self,
        *,
        prompt: str,
        source_ref: str | None = None,
        artifact_id: str | None = None,
        snapshot_id: str | None = None,
        include_insights: bool = True,
        include_notes: bool = False,
        max_chunks: int | None = None,
        max_context_chars: int | None = None,
        provenance_visibility: str | None = None,
        llm_scope: str | None = None,
    ) -> dict[str, Any]:
        query = str(prompt or "").strip()
        if not query:
            raise ValueError("prompt_required")
        normalized_source_ref = str(source_ref or "").strip() or None
        normalized_artifact_id = str(artifact_id or "").strip() or None
        normalized_snapshot_id = str(snapshot_id or "").strip() or None
        if not any((normalized_source_ref, normalized_artifact_id, normalized_snapshot_id)):
            raise ValueError("source_ref_required")

        if normalized_source_ref is not None:
            descriptor = self._source_registry.get_source(normalized_source_ref)
            if descriptor is None:
                raise ValueError("source_not_found")
            if not bool(descriptor.get("enabled", True)):
                raise ValueError("source_disabled")

        effective_max_chunks = max(1, int(max_chunks or DEFAULT_MAX_CHUNKS))
        effective_max_chars = max(500, int(max_context_chars or DEFAULT_MAX_CONTEXT_CHARS))

        bundle = self._rag_service.retrieve_context_bundle(
            query,
            source_types=["open_notebook"],
            max_chunks=max(effective_max_chunks * 3, effective_max_chunks),
            retrieval_intent="source_focused_chat",
            task_kind="research",
            provenance_visibility=provenance_visibility,
            llm_scope=llm_scope,
            source_constraints={
                key: value
                for key, value in {
                    "source_ref": normalized_source_ref,
                    "artifact_id": normalized_artifact_id,
                    "snapshot_id": normalized_snapshot_id,
                }.items()
                if value is not None
            },
        )

        selected: list[dict[str, Any]] = []
        used_chars = 0
        budget_cut = False
        for chunk in list(bundle.get("chunks") or []):
            metadata = dict((chunk or {}).get("metadata") or {})
            if not self._matches_source(
                metadata,
                source_ref=normalized_source_ref,
                artifact_id=normalized_artifact_id,
                snapshot_id=normalized_snapshot_id,
            ):
                continue
            record_kind = str(metadata.get("record_kind") or "primary_source")
            if record_kind == "source_insight" and not include_insights:
                continue
            if record_kind == "note" and not include_notes:
                continue
            content_length = len(str(chunk.get("content") or ""))
            if len(selected) >= effective_max_chunks or used_chars + content_length > effective_max_chars:
                budget_cut = True
                break
            selected.append(dict(chunk))
            used_chars += content_length

        source_references = build_source_references_for_chunks(selected)
        context_text = "\n\n".join(str(chunk.get("content") or "") for chunk in selected)
        grounded_prompt = self._grounded_prompt_builder(
            prompt=query,
            context_text=context_text,
            chunks=selected,
        )
        context_hash = self._context_hash(query=query, chunks=selected)

        return {
            "context_bundle": bundle,
            "selected_sources": [
                {
                    "source": chunk.get("source"),
                    "record_kind": str(dict(chunk.get("metadata") or {}).get("record_kind") or ""),
                    "score": chunk.get("score"),
                }
                for chunk in selected
            ],
            "source_references": source_references,
            "grounded_prompt": grounded_prompt,
            "context_hash": context_hash,
            "budget": {
                "max_chunks": effective_max_chunks,
                "max_context_chars": effective_max_chars,
                "used_chunks": len(selected),
                "used_chars": used_chars,
                "budget_cut": budget_cut,
            },
            "filters": {
                "source_ref": normalized_source_ref,
                "artifact_id": normalized_artifact_id,
                "snapshot_id": normalized_snapshot_id,
                "include_insights": include_insights,
                "include_notes": include_notes,
            },
        }

    @staticmethod
    def _matches_source(
        metadata: dict[str, Any],
        *,
        source_ref: str | None,
        artifact_id: str | None,
        snapshot_id: str | None,
    ) -> bool:
        if str(metadata.get("source_type") or "") != "open_notebook":
            return False
        if source_ref is not None:
            candidates = {
                str(metadata.get("source_id") or ""),
                str(metadata.get("registry_source_id") or ""),
                str(metadata.get("open_notebook_source_id") or ""),
            }
            if source_ref not in candidates:
                return False
        if artifact_id is not None and str(metadata.get("artifact_id") or "") != artifact_id:
            return False
        if snapshot_id is not None:
            snapshot_candidates = {
                str(metadata.get("snapshot_id") or ""),
                str(metadata.get("parent_source_snapshot_id") or ""),
            }
            if snapshot_id not in snapshot_candidates:
                return False
        return True

    @staticmethod
    def _context_hash(*, query: str, chunks: list[dict[str, Any]]) -> str:
        records = []
        for chunk in chunks:
            metadata = dict((chunk or {}).get("metadata") or {})
            records.append(
                {
                    "source_id": str(metadata.get("source_id") or ""),
                    "snapshot_id": str(metadata.get("snapshot_id") or ""),
                    "content_hash": str(metadata.get("content_hash") or ""),
                    "chunk_id": str(metadata.get("chunk_id") or ""),
                }
            )
        payload = json.dumps({"query": query, "records": records}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


source_chat_context_service: SourceChatContextService | None = None


def get_source_chat_context_service() -> SourceChatContextService:
    global source_chat_context_service
    if source_chat_context_service is None:
        source_chat_context_service = SourceChatContextService()
    return source_chat_context_service
