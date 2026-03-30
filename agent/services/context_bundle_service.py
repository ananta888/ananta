from __future__ import annotations

CONTEXT_BUNDLE_POLICY_MODES = {"compact", "standard", "full"}


def normalize_context_bundle_policy_config(value: dict | None) -> dict[str, object]:
    payload = dict(value or {})
    mode = str(payload.get("mode") or "full").strip().lower() or "full"
    if mode not in CONTEXT_BUNDLE_POLICY_MODES:
        mode = "full"

    compact_max_chunks = payload.get("compact_max_chunks")
    standard_max_chunks = payload.get("standard_max_chunks")

    def _normalize_limit(raw_value, default: int) -> int:
        try:
            value = int(raw_value) if raw_value is not None else default
        except (TypeError, ValueError):
            value = default
        return max(1, min(50, value))

    return {
        "mode": mode,
        "compact_max_chunks": _normalize_limit(compact_max_chunks, 3),
        "standard_max_chunks": _normalize_limit(standard_max_chunks, 8),
    }


def resolve_context_bundle_policy(value: dict | None) -> dict[str, object]:
    config = normalize_context_bundle_policy_config(value)
    mode = str(config["mode"])
    max_chunks = None
    include_context_text = True
    if mode == "compact":
        include_context_text = False
        max_chunks = int(config["compact_max_chunks"])
    elif mode == "standard":
        include_context_text = True
        max_chunks = int(config["standard_max_chunks"])
    return {
        **config,
        "include_context_text": include_context_text,
        "max_chunks": max_chunks,
    }


class ContextBundleService:
    """Builds worker-facing context bundles from retrieval output."""

    def normalize_context_bundle_policy_config(self, value: dict | None) -> dict[str, object]:
        return normalize_context_bundle_policy_config(value)

    def resolve_context_bundle_policy(self, value: dict | None) -> dict[str, object]:
        return resolve_context_bundle_policy(value)

    def _build_explainability(self, chunks: list[dict]) -> dict[str, object]:
        engines: list[str] = []
        artifact_ids: list[str] = []
        knowledge_index_ids: list[str] = []
        chunk_types: list[str] = []
        collection_ids: list[str] = []
        collection_names: list[str] = []
        sources: list[dict[str, object]] = []

        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            engine = str(chunk.get("engine") or "").strip()
            source = str(chunk.get("source") or "").strip()
            metadata = dict(chunk.get("metadata") or {})
            if engine and engine not in engines:
                engines.append(engine)
            artifact_id = str(metadata.get("artifact_id") or "").strip()
            if artifact_id and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            knowledge_index_id = str(metadata.get("knowledge_index_id") or "").strip()
            if knowledge_index_id and knowledge_index_id not in knowledge_index_ids:
                knowledge_index_ids.append(knowledge_index_id)
            chunk_type = str(metadata.get("record_kind") or "").strip()
            if chunk_type and chunk_type not in chunk_types:
                chunk_types.append(chunk_type)
            for collection_id in metadata.get("collection_ids") or []:
                value = str(collection_id or "").strip()
                if value and value not in collection_ids:
                    collection_ids.append(value)
            for collection_name in metadata.get("collection_names") or []:
                value = str(collection_name or "").strip()
                if value and value not in collection_names:
                    collection_names.append(value)
            if source:
                sources.append(
                    {
                        "engine": engine,
                        "source": source,
                        "score": chunk.get("score"),
                        "record_kind": chunk_type,
                        "artifact_id": artifact_id,
                        "knowledge_index_id": knowledge_index_id,
                        "collection_names": metadata.get("collection_names") or [],
                    }
                )

        return {
            "engines": engines,
            "artifact_ids": artifact_ids,
            "knowledge_index_ids": knowledge_index_ids,
            "chunk_types": chunk_types,
            "collection_ids": collection_ids,
            "collection_names": collection_names,
            "source_count": len(sources),
            "sources": sources,
        }

    def build_bundle(
        self,
        *,
        query: str,
        context_payload: dict[str, object],
        include_context_text: bool = True,
        max_chunks: int | None = None,
        policy_mode: str = "full",
    ) -> dict[str, object]:
        payload = dict(context_payload or {})
        chunks = list(payload.get("chunks") or [])
        if max_chunks is not None:
            chunks = chunks[: max(1, int(max_chunks))]
        payload["chunks"] = chunks
        if not include_context_text:
            payload.pop("context_text", None)
        payload.setdefault("query", query)
        payload.setdefault("policy_version", "v1")
        payload.setdefault("strategy", {})
        payload.setdefault("token_estimate", 0)
        payload["chunk_count"] = len(payload.get("chunks") or [])
        payload["explainability"] = self._build_explainability(list(payload.get("chunks") or []))
        payload["bundle_type"] = "retrieval_context"
        payload["context_policy"] = {
            "mode": str(policy_mode or "full"),
            "include_context_text": bool(include_context_text),
            "max_chunks": max_chunks,
        }
        return payload

    def build_grounded_prompt(self, *, prompt: str, context_text: str) -> str:
        return (
            "Nutze den folgenden selektiven Kontext und beantworte die Frage praezise.\n\n"
            f"Frage:\n{prompt}\n\n"
            f"Kontext:\n{context_text}"
        )


context_bundle_service = ContextBundleService()


def get_context_bundle_service() -> ContextBundleService:
    return context_bundle_service
