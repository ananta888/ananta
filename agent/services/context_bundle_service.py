from __future__ import annotations


class ContextBundleService:
    """Builds worker-facing context bundles from retrieval output."""

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
    ) -> dict[str, object]:
        payload = dict(context_payload or {})
        if not include_context_text:
            payload.pop("context_text", None)
        payload.setdefault("query", query)
        payload.setdefault("policy_version", "v1")
        payload.setdefault("chunks", [])
        payload.setdefault("strategy", {})
        payload.setdefault("token_estimate", 0)
        payload["chunk_count"] = len(payload.get("chunks") or [])
        payload["explainability"] = self._build_explainability(list(payload.get("chunks") or []))
        payload["bundle_type"] = "retrieval_context"
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
