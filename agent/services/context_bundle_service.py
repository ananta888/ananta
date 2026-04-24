from __future__ import annotations

import re

from agent.common.redaction import redact
from agent.config import settings
from agent.metrics import RAG_BUNDLE_BUDGET_UTILIZATION, RAG_BUNDLE_DUPLICATE_RATE, RAG_BUNDLE_NOISE_RATE

CONTEXT_BUNDLE_POLICY_MODES = {"compact", "standard", "full"}
CONTEXT_WINDOW_PROFILES = {"compact_12k", "standard_32k", "full_64k"}
DEFAULT_BUNDLE_BUDGET_BY_MODE = {
    "compact": 12000,
    "standard": 32000,
    "full": 64000,
}
DEBUG_REDACTION_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\b([A-Za-z0-9_]*(?:token|password|secret|apikey|api_key)[A-Za-z0-9_]*\s*[:=]\s*['\"]?[^'\"\s]{6,})", re.I),
]


def normalize_context_bundle_policy_config(value: dict | None) -> dict[str, object]:
    payload = dict(value or {})
    mode = str(payload.get("mode") or "full").strip().lower() or "full"
    if mode not in CONTEXT_BUNDLE_POLICY_MODES:
        mode = "full"

    compact_max_chunks = payload.get("compact_max_chunks")
    standard_max_chunks = payload.get("standard_max_chunks")
    standard_budget_tokens = payload.get("standard_budget_tokens")
    default_window_profile = str(getattr(settings, "rag_default_window_profile", "standard_32k") or "standard_32k").strip().lower()
    window_profile = str(payload.get("window_profile") or default_window_profile).strip().lower() or default_window_profile
    if window_profile not in CONTEXT_WINDOW_PROFILES:
        window_profile = "standard_32k"

    def _normalize_limit(raw_value, default: int) -> int:
        try:
            value = int(raw_value) if raw_value is not None else default
        except (TypeError, ValueError):
            value = default
        return max(1, min(50, value))

    def _normalize_budget(raw_value, default: int) -> int:
        try:
            value = int(raw_value) if raw_value is not None else default
        except (TypeError, ValueError):
            value = default
        return max(4096, min(131072, value))

    compact_budget_default = int(getattr(settings, "rag_compact_budget_tokens", 12000) or 12000)
    standard_budget_default = int(getattr(settings, "rag_standard_budget_tokens", 32000) or 32000)
    full_budget_default = int(getattr(settings, "rag_full_budget_tokens", 64000) or 64000)
    standard_budget = _normalize_budget(standard_budget_tokens, standard_budget_default)
    budget_tokens_by_mode = {
        "compact": _normalize_budget(payload.get("compact_budget_tokens"), compact_budget_default),
        "standard": standard_budget,
        "full": _normalize_budget(payload.get("full_budget_tokens"), full_budget_default),
    }

    return {
        "mode": mode,
        "window_profile": window_profile,
        "compact_max_chunks": _normalize_limit(compact_max_chunks, 3),
        "standard_max_chunks": _normalize_limit(standard_max_chunks, 8),
        "compact_budget_tokens": int(budget_tokens_by_mode["compact"]),
        "standard_budget_tokens": standard_budget,
        "full_budget_tokens": int(budget_tokens_by_mode["full"]),
        "budget_tokens_by_mode": budget_tokens_by_mode,
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
    budget_tokens_by_mode = dict(config.get("budget_tokens_by_mode") or {})
    total_budget_tokens = int(budget_tokens_by_mode.get(mode) or DEFAULT_BUNDLE_BUDGET_BY_MODE.get(mode, 32000))
    return {
        **config,
        "include_context_text": include_context_text,
        "max_chunks": max_chunks,
        "total_budget_tokens": total_budget_tokens,
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
        reservation_weights = self._priority_reservations_for_task(task_kind)
        priority_order = ["critical", "high", "medium", "low"]
        priority_tokens: dict[str, int] = {}
        assigned_priority = 0
        for index, priority in enumerate(priority_order):
            if index == len(priority_order) - 1:
                priority_tokens[priority] = max(0, retrieval_alloc - assigned_priority)
                continue
            amount = int(round(retrieval_alloc * float(reservation_weights.get(priority, 0.0))))
            priority_tokens[priority] = amount
            assigned_priority += amount
        return {
            "model": "sectional_v2",
            "mode": mode,
            "task_kind": str(task_kind or "").strip() or None,
            "total_tokens": total_tokens,
            "sections": sections,
            "retrieval_utilization": round(utilization, 4),
            "priority_reservations": {
                "version": "source-priority-reservation-v1",
                "weights": reservation_weights,
                "tokens_by_priority": priority_tokens,
            },
        }

    def _priority_reservations_for_task(self, task_kind: str | None) -> dict[str, float]:
        normalized = str(task_kind or "").strip().lower()
        if normalized in {"bugfix", "testing", "test"}:
            return {"critical": 0.40, "high": 0.30, "medium": 0.20, "low": 0.10}
        if normalized in {"refactor", "coding", "implement"}:
            return {"critical": 0.35, "high": 0.30, "medium": 0.22, "low": 0.13}
        if normalized in {"architecture", "analysis", "doc", "research"}:
            return {"critical": 0.28, "high": 0.28, "medium": 0.28, "low": 0.16}
        return {"critical": 0.33, "high": 0.30, "medium": 0.22, "low": 0.15}

    def _chunk_priority(self, chunk: dict[str, object], *, task_kind: str | None) -> str:
        metadata = dict(chunk.get("metadata") or {})
        record_kind = str(metadata.get("record_kind") or "").strip().lower()
        source_type = str(metadata.get("source_type") or "").strip().lower()
        relation = str(metadata.get("task_relation") or "").strip().lower()
        if record_kind in {"policy", "constraint", "security_note", "approval", "contract"}:
            return "critical"
        if relation in {"same_task", "direct_parent", "direct_child"}:
            return "high"
        if source_type in {"task_memory", "artifact"}:
            return "high"
        if source_type in {"goal_memory", "result_memory", "wiki", "kb"}:
            return "medium"
        normalized_task = str(task_kind or "").strip().lower()
        if normalized_task in {"bugfix", "testing", "test"} and source_type in {"logs", "telemetry", "trace"}:
            return "high"
        return "low"

    @staticmethod
    def _estimate_chunk_tokens(chunk: dict[str, object]) -> int:
        text = str(chunk.get("content") or "")
        return max(1, int(len(text) / 4))

    def _compact_chunk_entry(self, chunk: dict[str, object], *, reason: str, priority: str, tokens: int) -> dict[str, object]:
        metadata = dict(chunk.get("metadata") or {})
        return {
            "engine": str(chunk.get("engine") or "").strip() or None,
            "source": self._redact_debug_value(str(chunk.get("source") or "").strip() or None),
            "score": chunk.get("score"),
            "record_kind": str(metadata.get("record_kind") or "").strip() or None,
            "source_type": str(metadata.get("source_type") or "").strip() or None,
            "chunk_id": str(metadata.get("chunk_id") or "").strip() or None,
            "estimated_tokens": int(tokens),
            "priority": priority,
            "reason": reason,
        }

    def _enforce_budget_with_compaction(
        self,
        *,
        chunks: list[dict[str, object]],
        task_kind: str | None,
        retrieval_budget_tokens: int,
        priority_tokens_by_level: dict[str, int],
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        indexed: list[tuple[int, dict[str, object], str, int]] = []
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            priority = self._chunk_priority(chunk, task_kind=task_kind)
            indexed.append((index, chunk, priority, self._estimate_chunk_tokens(chunk)))
        ordered = sorted(
            indexed,
            key=lambda item: (
                -int(rank.get(item[2], 0)),
                -float(item[1].get("score") or 0.0),
                int(item[0]),
            ),
        )

        selected: list[dict[str, object]] = []
        selected_entries: list[tuple[int, dict[str, object], str, int]] = []
        deferred: list[tuple[int, dict[str, object], str, int, str]] = []
        used_tokens_total = 0
        used_by_priority = {key: 0 for key in ("critical", "high", "medium", "low")}
        dropped_by_reason: dict[str, int] = {}
        dropped_by_priority = {key: 0 for key in ("critical", "high", "medium", "low")}
        kept_by_priority = {key: 0 for key in ("critical", "high", "medium", "low")}

        def _drop(item: tuple[int, dict[str, object], str, int], reason: str) -> None:
            deferred.append((item[0], item[1], item[2], item[3], reason))
            dropped_by_reason[reason] = int(dropped_by_reason.get(reason) or 0) + 1
            dropped_by_priority[item[2]] = int(dropped_by_priority.get(item[2]) or 0) + 1

        for item in ordered:
            _, _, priority, tokens = item
            if used_tokens_total + tokens > retrieval_budget_tokens:
                _drop(item, "retrieval_budget_exhausted")
                continue
            reserved_limit = max(1, int(priority_tokens_by_level.get(priority) or 0))
            if used_by_priority[priority] + tokens > reserved_limit:
                _drop(item, "priority_reservation_exhausted")
                continue
            used_tokens_total += tokens
            used_by_priority[priority] += tokens
            kept_by_priority[priority] = int(kept_by_priority.get(priority) or 0) + 1
            selected_entries.append(item)
            selected.append(item[1])

        if used_tokens_total < retrieval_budget_tokens:
            remaining = sorted(
                [item for item in deferred if item[4] == "priority_reservation_exhausted"],
                key=lambda item: (-float(item[1].get("score") or 0.0), int(item[0])),
            )
            refill_kept: list[tuple[int, dict[str, object], str, int, str]] = []
            for item in remaining:
                index, chunk, priority, tokens, _ = item
                if used_tokens_total + tokens > retrieval_budget_tokens:
                    continue
                used_tokens_total += tokens
                used_by_priority[priority] += tokens
                kept_by_priority[priority] = int(kept_by_priority.get(priority) or 0) + 1
                selected_entries.append((index, chunk, priority, tokens))
                selected.append(chunk)
                refill_kept.append(item)
            if refill_kept:
                for item in refill_kept:
                    deferred.remove(item)
                    dropped_by_reason["priority_reservation_exhausted"] = max(
                        0,
                        int(dropped_by_reason.get("priority_reservation_exhausted") or 0) - 1,
                    )
                    dropped_by_priority[item[2]] = max(0, int(dropped_by_priority.get(item[2]) or 0) - 1)

        selected_entries = sorted(selected_entries, key=lambda item: int(item[0]))
        selected = [item[1] for item in selected_entries]
        dropped_chunks = [
            self._compact_chunk_entry(chunk, reason=reason, priority=priority, tokens=tokens)
            for _, chunk, priority, tokens, reason in deferred
        ]
        compaction = {
            "enabled": True,
            "version": "priority-budget-compaction-v1",
            "retrieval_budget_tokens": int(retrieval_budget_tokens),
            "selected_tokens": int(used_tokens_total),
            "selected_chunk_count": len(selected),
            "dropped_chunk_count": len(dropped_chunks),
            "kept_by_priority": kept_by_priority,
            "dropped_by_priority": dropped_by_priority,
            "used_tokens_by_priority": used_by_priority,
            "dropped_reasons": dropped_by_reason,
            "dropped_chunks": self._redact_debug_value(dropped_chunks),
            "provenance_preserved": True,
        }
        return selected, compaction

    def _build_explainability(self, chunks: list[dict]) -> dict[str, object]:
        engines: list[str] = []
        artifact_ids: list[str] = []
        knowledge_index_ids: list[str] = []
        chunk_types: list[str] = []
        source_types: list[str] = []
        source_type_counts: dict[str, int] = {}
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
            source_type = str(metadata.get("source_type") or "").strip()
            if source_type:
                if source_type not in source_types:
                    source_types.append(source_type)
                source_type_counts[source_type] = int(source_type_counts.get(source_type) or 0) + 1
            source_id = str(metadata.get("source_id") or "").strip() or None
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
                        "source": self._redact_debug_value(source),
                        "score": chunk.get("score"),
                        "record_kind": chunk_type,
                        "source_type": source_type or None,
                        "source_id": source_id,
                        "chunk_id": str(metadata.get("chunk_id") or "").strip() or None,
                        "artifact_id": artifact_id,
                        "knowledge_index_id": knowledge_index_id,
                        "collection_names": self._redact_debug_value(metadata.get("collection_names") or []),
                    }
                )

        return {
            "engines": engines,
            "artifact_ids": artifact_ids,
            "knowledge_index_ids": knowledge_index_ids,
            "chunk_types": chunk_types,
            "source_types": source_types,
            "source_type_counts": source_type_counts,
            "collection_ids": collection_ids,
            "collection_names": collection_names,
            "source_count": len(sources),
            "sources": sources,
        }

    def _redact_debug_value(self, value):
        if not bool(getattr(settings, "rag_redact_sensitive", True)):
            return value
        return redact(value)

    def _mode_profile(self, mode: str) -> dict[str, object]:
        normalized = str(mode or "full").strip().lower()
        if normalized == "compact":
            return {
                "bundle_strategy": "minimal",
                "explainability_level": "minimal",
                "chunk_text_style": "compressed_snippets",
                "source_limit": 2,
                "top_source_limit": 2,
                "chunk_content_limit": 320,
            }
        if normalized == "standard":
            return {
                "bundle_strategy": "balanced",
                "explainability_level": "balanced",
                "chunk_text_style": "balanced_snippets",
                "source_limit": 6,
                "top_source_limit": 4,
                "chunk_content_limit": 1200,
            }
        return {
            "bundle_strategy": "deep",
            "explainability_level": "detailed",
            "chunk_text_style": "detailed_context",
            "source_limit": 12,
            "top_source_limit": 8,
            "chunk_content_limit": 2400,
        }

    def _compact_chunks_for_mode(self, chunks: list[dict], *, mode: str) -> list[dict]:
        profile = self._mode_profile(mode)
        content_limit = max(120, int(profile.get("chunk_content_limit") or 1200))
        normalized_chunks: list[dict] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            content = str(chunk.get("content") or "")
            metadata = dict(chunk.get("metadata") or {})
            trimmed = content[:content_limit]
            if content and len(content) > len(trimmed):
                metadata["content_trimmed_for_mode"] = mode
            normalized_chunks.append({**chunk, "content": trimmed, "metadata": metadata})
        return normalized_chunks

    def _mode_adjusted_explainability(self, explainability: dict[str, object], *, mode: str) -> dict[str, object]:
        profile = self._mode_profile(mode)
        source_limit = max(1, int(profile.get("source_limit") or 5))
        sources = list(explainability.get("sources") or [])[:source_limit]
        payload = {**dict(explainability or {}), "sources": sources, "source_count": len(sources)}
        if mode == "compact":
            payload.pop("collection_ids", None)
            payload.pop("knowledge_index_ids", None)
        return dict(self._redact_debug_value(payload) or {})

    def _normalize_provenance_visibility(self, value: str | None) -> str:
        normalized = str(value or "standard").strip().lower() or "standard"
        if normalized in {"admin", "operator"}:
            return "admin"
        return "standard"

    def _apply_provenance_visibility(self, explainability: dict[str, object], *, visibility_level: str) -> dict[str, object]:
        if visibility_level == "admin":
            return explainability
        filtered_sources: list[dict[str, object]] = []
        for item in list(explainability.get("sources") or []):
            if not isinstance(item, dict):
                continue
            filtered_sources.append(
                {
                    "engine": item.get("engine"),
                    "source": item.get("source"),
                    "score": item.get("score"),
                    "record_kind": item.get("record_kind"),
                    "source_type": item.get("source_type"),
                    "collection_names": item.get("collection_names"),
                }
            )
        return {**dict(explainability or {}), "sources": filtered_sources, "source_count": len(filtered_sources)}

    def _build_why_this_context(
        self,
        *,
        chunks: list[dict],
        strategy: dict[str, object],
        task_kind: str | None,
        retrieval_intent: str | None,
        required_context_scope: str | None,
        mode: str,
        compaction: dict[str, object] | None = None,
    ) -> dict[str, object]:
        profile = self._mode_profile(mode)
        top_source_limit = max(1, int(profile.get("top_source_limit") or 5))
        top_sources: list[dict[str, object]] = []
        for chunk in chunks[:top_source_limit]:
            if not isinstance(chunk, dict):
                continue
            metadata = dict(chunk.get("metadata") or {})
            fusion = dict(metadata.get("fusion") or {})
            relevance_dimensions = [
                key
                for key in ("query_overlap", "relation_bonus")
                if float(fusion.get(key) or 0.0) > 0
            ]
            top_sources.append(
                {
                    "engine": str(chunk.get("engine") or ""),
                    "source": str(chunk.get("source") or ""),
                    "score": chunk.get("score"),
                    "record_kind": str(metadata.get("record_kind") or ""),
                    "source_type": str(metadata.get("source_type") or "").strip() or None,
                    "relevance_dimensions": relevance_dimensions,
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
        if isinstance(compaction, dict):
            summary_parts.append(
                "compaction="
                f"{int(compaction.get('selected_chunk_count') or 0)}/"
                f"{int((compaction.get('selected_chunk_count') or 0) + (compaction.get('dropped_chunk_count') or 0))}"
            )
        summary_parts.append(f"mode={mode}")
        return {
            "summary": self._redact_debug_value(" | ".join(summary_parts)),
            "task_kind": str(task_kind or "").strip() or None,
            "retrieval_intent": str(retrieval_intent or "").strip() or None,
            "required_context_scope": str(required_context_scope or "").strip() or None,
            "mode": mode,
            "top_sources": self._redact_debug_value(top_sources),
            "compaction_summary": {
                "selected_chunk_count": int((compaction or {}).get("selected_chunk_count") or 0),
                "dropped_chunk_count": int((compaction or {}).get("dropped_chunk_count") or 0),
                "dropped_reasons": dict((compaction or {}).get("dropped_reasons") or {}),
                "provenance_preserved": bool((compaction or {}).get("provenance_preserved")),
            },
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
        budget_tokens_by_mode: dict[str, int] | None = None,
        window_profile: str | None = None,
        provenance_visibility: str | None = None,
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
        mode_profile = self._mode_profile(effective_mode)
        if effective_mode == "compact":
            include_context_text = False
            payload.pop("context_text", None)
        payload["chunks"] = self._compact_chunks_for_mode(list(payload.get("chunks") or []), mode=effective_mode)
        mode_budget_map = dict(budget_tokens_by_mode or {})
        effective_total_budget_tokens = total_budget_tokens
        if mode_budget_map:
            mapped = mode_budget_map.get(effective_mode)
            if mapped is not None:
                try:
                    effective_total_budget_tokens = int(mapped)
                except (TypeError, ValueError):
                    effective_total_budget_tokens = total_budget_tokens
        budget_model = self._build_budget_model(
            policy_mode=effective_mode,
            task_kind=task_kind,
            token_estimate=int(payload.get("token_estimate") or 0),
            explicit_total_tokens=effective_total_budget_tokens,
        )
        selected_chunks, compaction = self._enforce_budget_with_compaction(
            chunks=list(payload.get("chunks") or []),
            task_kind=task_kind,
            retrieval_budget_tokens=max(1, int((budget_model.get("sections") or {}).get("retrieval_context") or 1)),
            priority_tokens_by_level=dict(
                ((budget_model.get("priority_reservations") or {}).get("tokens_by_priority") or {})
            ),
        )
        payload["chunks"] = selected_chunks
        payload["chunk_count"] = len(selected_chunks)
        payload["compaction"] = compaction
        selected_tokens = int(compaction.get("selected_tokens") or 0)
        retrieval_alloc = max(1, int((budget_model.get("sections") or {}).get("retrieval_context") or 1))
        budget_model["retrieval_selected_tokens"] = selected_tokens
        budget_model["retrieval_utilization"] = round(min(1.0, float(selected_tokens) / float(retrieval_alloc)), 4)
        strategy = dict(payload.get("strategy") or {})
        base_explainability = self._build_explainability(list(payload.get("chunks") or []))
        visibility_level = self._normalize_provenance_visibility(provenance_visibility)
        mode_explainability = self._mode_adjusted_explainability(base_explainability, mode=effective_mode)
        payload["explainability"] = self._apply_provenance_visibility(mode_explainability, visibility_level=visibility_level)
        payload["why_this_context"] = self._build_why_this_context(
            chunks=list(payload.get("chunks") or []),
            strategy=strategy,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            required_context_scope=required_context_scope,
            mode=effective_mode,
            compaction=compaction,
        )
        payload["budget"] = budget_model
        fusion = dict(strategy.get("fusion") or {})
        dedupe = dict(fusion.get("dedupe") or {})
        candidate_counts = dict(fusion.get("candidate_counts") or {})
        all_candidates = max(1, int(candidate_counts.get("all") or 0))
        final_candidates = max(0, int(candidate_counts.get("final") or 0))
        duplicate_rate = min(1.0, float((dedupe.get("identity_duplicates") or 0) + (dedupe.get("content_duplicates") or 0)) / float(all_candidates))
        noise_rate = min(1.0, max(0.0, float(all_candidates - final_candidates) / float(all_candidates)))
        payload["bundle_type"] = "retrieval_context"
        payload["selection_trace"] = {
            "fusion": dict(strategy.get("fusion") or {}),
            "knowledge_index_reason": strategy.get("knowledge_index_reason"),
            "result_memory_reason": strategy.get("result_memory_reason"),
            "compaction": {
                "version": compaction.get("version"),
                "dropped_reasons": dict(compaction.get("dropped_reasons") or {}),
            },
        }
        payload["selection_trace"] = self._redact_debug_value(payload["selection_trace"])
        payload["context_policy"] = {
            "mode": effective_mode,
            "include_context_text": bool(include_context_text),
            "max_chunks": max_chunks,
            "total_budget_tokens": int(payload["budget"].get("total_tokens") or 0),
            "window_profile": str(window_profile or "standard_32k").strip().lower() or "standard_32k",
            "bundle_strategy": str(mode_profile.get("bundle_strategy") or "balanced"),
            "explainability_level": str(mode_profile.get("explainability_level") or "balanced"),
            "chunk_text_style": str(mode_profile.get("chunk_text_style") or "balanced_snippets"),
            "source_prioritization_rules": [
                {
                    "priority": "critical",
                    "match": ["record_kind in {policy,constraint,security_note,approval,contract}"],
                    "reason": "Governance and safety constraints are preserved first",
                },
                {
                    "priority": "high",
                    "match": ["task_relation in {same_task,direct_parent,direct_child}", "source_type in {task_memory,artifact}"],
                    "reason": "Direct execution context and task-local artifacts are favored",
                },
                {
                    "priority": "medium",
                    "match": ["source_type in {goal_memory,result_memory,wiki,kb}"],
                    "reason": "Supporting context is retained when budget allows",
                },
                {
                    "priority": "low",
                    "match": ["fallback for non-matching chunks"],
                    "reason": "Residual context is included only after higher priority reservations",
                },
            ],
        }
        payload["provenance_policy"] = {
            "policy_version": "multi-source-provenance-v1",
            "visibility_level": visibility_level,
            "source_blending": "forbidden",
            "rules": [
                "source_type and citation metadata stay attached per chunk",
                "non-admin views hide high-cardinality provenance identifiers",
                "selection traces remain observable after redaction",
            ],
        }
        metric_task_kind = str(task_kind or "unknown").strip() or "unknown"
        metric_bundle_mode = str(effective_mode or "unknown").strip() or "unknown"
        RAG_BUNDLE_BUDGET_UTILIZATION.labels(metric_task_kind, metric_bundle_mode).observe(
            float(payload["budget"].get("retrieval_utilization") or 0.0)
        )
        RAG_BUNDLE_DUPLICATE_RATE.labels(metric_task_kind, metric_bundle_mode).observe(duplicate_rate)
        RAG_BUNDLE_NOISE_RATE.labels(metric_task_kind, metric_bundle_mode).observe(noise_rate)
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
