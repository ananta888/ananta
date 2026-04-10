from __future__ import annotations

CONTEXT_BUNDLE_POLICY_MODES = {"compact", "standard", "full"}
DEFAULT_BUNDLE_BUDGET_BY_MODE = {
    "compact": 12000,
    "standard": 32000,
    "full": 64000,
}


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

    def _budget_weights_for_task(self, task_kind: str | None) -> dict[str, float]:
        normalized = str(task_kind or "").strip().lower()
        if normalized in {"bugfix", "testing", "test"}:
            return {
                "orchestration": 0.08,
                "retrieval_context": 0.62,
                "constraints": 0.08,
                "examples": 0.06,
                "output_reserve": 0.16,
            }
        if normalized in {"refactor", "coding", "implement"}:
            return {
                "orchestration": 0.08,
                "retrieval_context": 0.58,
                "constraints": 0.08,
                "examples": 0.08,
                "output_reserve": 0.18,
            }
        if normalized in {"architecture", "analysis", "doc", "research"}:
            return {
                "orchestration": 0.10,
                "retrieval_context": 0.50,
                "constraints": 0.12,
                "examples": 0.10,
                "output_reserve": 0.18,
            }
        if normalized in {"config", "xml", "ops"}:
            return {
                "orchestration": 0.08,
                "retrieval_context": 0.56,
                "constraints": 0.10,
                "examples": 0.08,
                "output_reserve": 0.18,
            }
        return {
            "orchestration": 0.08,
            "retrieval_context": 0.58,
            "constraints": 0.08,
            "examples": 0.08,
            "output_reserve": 0.18,
        }

    def _build_budget_model(
        self,
        *,
        policy_mode: str,
        task_kind: str | None,
        token_estimate: int,
        explicit_total_tokens: int | None = None,
    ) -> dict[str, object]:
        mode = str(policy_mode or "full").strip().lower()
        if mode not in CONTEXT_BUNDLE_POLICY_MODES:
            mode = "full"
        total_tokens = int(explicit_total_tokens or DEFAULT_BUNDLE_BUDGET_BY_MODE.get(mode, 32000))
        total_tokens = max(4096, min(total_tokens, 256000))
        weights = self._budget_weights_for_task(task_kind)

        sections: dict[str, int] = {}
        assigned = 0
        order = ["orchestration", "retrieval_context", "constraints", "examples", "output_reserve"]
        for index, section in enumerate(order):
            if index == len(order) - 1:
                sections[section] = max(0, total_tokens - assigned)
                continue
            amount = int(round(total_tokens * float(weights.get(section, 0.0))))
            sections[section] = amount
            assigned += amount

        retrieval_alloc = max(1, sections.get("retrieval_context", 0))
        utilization = min(1.0, float(token_estimate or 0) / float(retrieval_alloc))
        return {
            "model": "sectional_v1",
            "mode": mode,
            "task_kind": str(task_kind or "").strip() or None,
            "total_tokens": total_tokens,
            "sections": sections,
            "retrieval_utilization": round(utilization, 4),
        }

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

    def _build_why_this_context(
        self,
        *,
        chunks: list[dict],
        strategy: dict[str, object],
        task_kind: str | None,
        retrieval_intent: str | None,
        required_context_scope: str | None,
    ) -> dict[str, object]:
        top_sources: list[dict[str, object]] = []
        for chunk in chunks[:5]:
            if not isinstance(chunk, dict):
                continue
            metadata = dict(chunk.get("metadata") or {})
            top_sources.append(
                {
                    "engine": str(chunk.get("engine") or ""),
                    "source": str(chunk.get("source") or ""),
                    "score": chunk.get("score"),
                    "record_kind": str(metadata.get("record_kind") or ""),
                }
            )
        summary_parts = []
        if task_kind:
            summary_parts.append(f"task_kind={task_kind}")
        if retrieval_intent:
            summary_parts.append(f"retrieval_intent={retrieval_intent}")
        if required_context_scope:
            summary_parts.append(f"context_scope={required_context_scope}")
        if strategy:
            summary_parts.append(f"strategy_keys={','.join(sorted(str(key) for key in strategy.keys()))}")
        summary_parts.append(f"selected_chunks={len(chunks)}")
        return {
            "summary": " | ".join(summary_parts),
            "task_kind": str(task_kind or "").strip() or None,
            "retrieval_intent": str(retrieval_intent or "").strip() or None,
            "required_context_scope": str(required_context_scope or "").strip() or None,
            "top_sources": top_sources,
        }

    def build_bundle(
        self,
        *,
        query: str,
        context_payload: dict[str, object],
        include_context_text: bool = True,
        max_chunks: int | None = None,
        policy_mode: str = "full",
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        required_context_scope: str | None = None,
        preferred_bundle_mode: str | None = None,
        total_budget_tokens: int | None = None,
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
        effective_mode = str(preferred_bundle_mode or policy_mode or "full").strip().lower()
        if effective_mode not in CONTEXT_BUNDLE_POLICY_MODES:
            effective_mode = str(policy_mode or "full").strip().lower() or "full"
        strategy = dict(payload.get("strategy") or {})
        payload["explainability"] = self._build_explainability(list(payload.get("chunks") or []))
        payload["why_this_context"] = self._build_why_this_context(
            chunks=list(payload.get("chunks") or []),
            strategy=strategy,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            required_context_scope=required_context_scope,
        )
        payload["budget"] = self._build_budget_model(
            policy_mode=effective_mode,
            task_kind=task_kind,
            token_estimate=int(payload.get("token_estimate") or 0),
            explicit_total_tokens=total_budget_tokens,
        )
        payload["bundle_type"] = "retrieval_context"
        payload["context_policy"] = {
            "mode": effective_mode,
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
