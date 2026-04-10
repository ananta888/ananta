from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from agent.config import settings
from agent.hybrid_orchestrator import ContextChunk, HybridOrchestrator
from agent.metrics import KNOWLEDGE_RETRIEVAL_CHUNKS, RAG_RETRIEVAL_TASK_KIND_TOTAL
from agent.repository import memory_entry_repo as default_memory_entry_repo
from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service


class RetrievalService:
    """Owns retrieval-engine lifecycle and exposes a stable retrieval seam."""

    def __init__(self, knowledge_index_retrieval_service=None, memory_entry_repository=None) -> None:
        self._orchestrator: HybridOrchestrator | None = None
        self._signature: tuple | None = None
        self._knowledge_index_retrieval_service = knowledge_index_retrieval_service or get_knowledge_index_retrieval_service()
        self._memory_entry_repository = memory_entry_repository or default_memory_entry_repo

    def _config_signature(self) -> tuple:
        return (
            settings.rag_enabled,
            settings.rag_repo_root,
            settings.rag_data_roots,
            settings.rag_max_context_chars,
            settings.rag_max_context_tokens,
            settings.rag_max_chunks,
            settings.rag_agentic_max_commands,
            settings.rag_agentic_timeout_seconds,
            settings.rag_semantic_persist_dir,
            settings.rag_redact_sensitive,
        )

    def _build_orchestrator(self) -> HybridOrchestrator:
        repo_root = Path(settings.rag_repo_root).resolve()
        data_roots = [repo_root / p.strip() for p in settings.rag_data_roots.split(",") if p.strip()]
        persist_dir = repo_root / settings.rag_semantic_persist_dir
        return HybridOrchestrator(
            repo_root=repo_root,
            data_roots=data_roots,
            max_context_chars=settings.rag_max_context_chars,
            max_context_tokens=settings.rag_max_context_tokens,
            max_chunks=settings.rag_max_chunks,
            agentic_max_commands=settings.rag_agentic_max_commands,
            agentic_timeout_seconds=settings.rag_agentic_timeout_seconds,
            semantic_persist_dir=persist_dir,
            redact_sensitive=settings.rag_redact_sensitive,
        )

    def _normalize_task_kind(self, task_kind: str | None) -> str:
        return str(task_kind or "").strip().lower()

    def _knowledge_index_plan(self, query: str, *, task_kind: str | None, retrieval_intent: str | None) -> tuple[int, str]:
        normalized = str(query or "").lower()
        normalized_kind = self._normalize_task_kind(task_kind)
        normalized_intent = str(retrieval_intent or "").strip().lower()
        doc_markers = ("doc", "docs", "readme", "guide", "architecture", "policy", "adr", "concept", "overview")
        code_markers = ("bug", "error", "trace", "stack", "code", "function", "class", "module", "refactor")
        reasons: list[str] = []
        top_k = max(1, settings.rag_max_chunks // 3)

        if normalized_kind in {"architecture", "analysis", "doc", "research"}:
            top_k = settings.rag_max_chunks
            reasons.append("task_kind_doc_or_architecture")
        elif normalized_kind in {"bugfix", "testing", "test", "refactor", "implement", "coding"}:
            top_k = max(2, settings.rag_max_chunks // 2)
            reasons.append("task_kind_code_or_debug")
        elif normalized_kind in {"config", "xml", "ops"}:
            top_k = max(2, (settings.rag_max_chunks * 2) // 3)
            reasons.append("task_kind_config")

        if any(marker in normalized for marker in doc_markers):
            top_k = max(top_k, settings.rag_max_chunks)
            reasons.append("query_doc_or_architecture")
        if any(marker in normalized for marker in code_markers):
            top_k = max(top_k, max(2, settings.rag_max_chunks // 2))
            reasons.append("query_code_or_debug")
        if any(marker in normalized_intent for marker in ("architecture", "overview", "decision")):
            top_k = max(top_k, settings.rag_max_chunks)
            reasons.append("intent_architecture")
        if any(marker in normalized_intent for marker in ("bug", "fix", "error")):
            top_k = max(top_k, max(2, settings.rag_max_chunks // 2))
            reasons.append("intent_bugfix")

        if not reasons:
            reasons.append("default_balanced_query")
        return top_k, ";".join(reasons)

    def _task_profile_for_fusion(self, task_kind: str | None, retrieval_intent: str | None) -> dict[str, object]:
        normalized_kind = self._normalize_task_kind(task_kind)
        normalized_intent = str(retrieval_intent or "").strip().lower()
        profile: dict[str, object] = {
            "engine_weights": {
                "repository_map": 1.0,
                "semantic_search": 1.0,
                "agentic_search": 0.95,
                "knowledge_index": 1.0,
                "result_memory": 1.05,
            },
            "max_per_source": 2,
            "max_per_engine": max(2, settings.rag_max_chunks),
        }
        if normalized_kind in {"bugfix", "testing", "test"}:
            profile["engine_weights"] = {
                "repository_map": 1.2,
                "semantic_search": 1.0,
                "agentic_search": 0.9,
                "knowledge_index": 1.25,
                "result_memory": 1.2,
            }
        elif normalized_kind in {"refactor", "implement", "coding"}:
            profile["engine_weights"] = {
                "repository_map": 1.25,
                "semantic_search": 1.0,
                "agentic_search": 0.9,
                "knowledge_index": 1.1,
                "result_memory": 1.15,
            }
        elif normalized_kind in {"architecture", "analysis", "doc", "research"}:
            profile["engine_weights"] = {
                "repository_map": 0.85,
                "semantic_search": 1.1,
                "agentic_search": 1.0,
                "knowledge_index": 1.3,
                "result_memory": 1.1,
            }
            profile["max_per_source"] = 1
        elif normalized_kind in {"config", "xml", "ops"}:
            profile["engine_weights"] = {
                "repository_map": 1.05,
                "semantic_search": 0.95,
                "agentic_search": 1.0,
                "knowledge_index": 1.2,
                "result_memory": 1.15,
            }

        if "architecture" in normalized_intent:
            engine_weights = dict(profile["engine_weights"] or {})
            engine_weights["knowledge_index"] = max(1.35, float(engine_weights.get("knowledge_index", 1.0)))
            profile["engine_weights"] = engine_weights
        if "bug" in normalized_intent or "error" in normalized_intent:
            engine_weights = dict(profile["engine_weights"] or {})
            engine_weights["repository_map"] = max(1.2, float(engine_weights.get("repository_map", 1.0)))
            engine_weights["knowledge_index"] = max(1.25, float(engine_weights.get("knowledge_index", 1.0)))
            engine_weights["result_memory"] = max(1.25, float(engine_weights.get("result_memory", 1.0)))
            profile["engine_weights"] = engine_weights
        return profile

    def _score_memory_entry(self, query_tokens: list[str], title: str, summary: str, content: str, tags: list[str]) -> float:
        haystack = " ".join([title, summary, content, " ".join(tags or [])]).lower()
        if not haystack.strip():
            return 0.0
        score = 0.0
        for token in query_tokens:
            count = haystack.count(token)
            if count > 0:
                score += 0.9 + (count - 1) * 0.12
        return score

    def _memory_candidates(
        self,
        *,
        query: str,
        task_id: str | None,
        goal_id: str | None,
        neighbor_task_ids: list[str] | None,
        top_k: int,
    ) -> tuple[list[ContextChunk], dict[str, object]]:
        query_tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query or "") if len(token) > 2]
        if not query_tokens:
            return [], {"reason": "no_query_tokens", "entries_considered": 0}

        neighbors = [str(item).strip() for item in (neighbor_task_ids or []) if str(item).strip()]
        candidate_entries = []
        if task_id:
            candidate_entries.extend(self._memory_entry_repository.get_by_task(task_id))
        for neighbor_id in neighbors:
            if not task_id or neighbor_id != task_id:
                candidate_entries.extend(self._memory_entry_repository.get_by_task(neighbor_id))
        if goal_id:
            goal_entries = self._memory_entry_repository.get_by_goal(goal_id)
            existing_ids = {str(getattr(entry, "id", "")) for entry in candidate_entries}
            for entry in goal_entries:
                if str(getattr(entry, "id", "")) not in existing_ids:
                    candidate_entries.append(entry)

        chunks: list[ContextChunk] = []
        for entry in candidate_entries:
            entry_task_id = str(getattr(entry, "task_id", "") or "").strip()
            title = str(getattr(entry, "title", "") or "").strip()
            summary = str(getattr(entry, "summary", "") or "").strip()
            content = str(getattr(entry, "content", "") or "").strip()
            tags = [str(tag).strip() for tag in (getattr(entry, "retrieval_tags", None) or []) if str(tag).strip()]
            score = self._score_memory_entry(query_tokens, title, summary, content, tags)
            if score <= 0:
                continue
            relation = "goal_related"
            if task_id and entry_task_id == task_id:
                relation = "same_task"
                score += 0.45
            elif entry_task_id in neighbors:
                relation = "task_neighbor"
                score += 0.3
            compact = str(((getattr(entry, "memory_metadata", None) or {}).get("compacted_summary")) or "").strip()
            content_preview = "\n".join(part for part in [summary, compact] if part).strip() or content[:1200]
            chunks.append(
                ContextChunk(
                    engine="result_memory",
                    source=f"memory:{entry_task_id or getattr(entry, 'id', 'unknown')}",
                    content=content_preview[:1600],
                    score=score,
                    metadata={
                        "memory_entry_id": str(getattr(entry, "id", "")),
                        "entry_type": str(getattr(entry, "entry_type", "worker_result")),
                        "source_task_id": entry_task_id or None,
                        "relation": relation,
                        "retrieval_tags": tags,
                    },
                )
            )
        ranked = sorted(chunks, key=lambda item: (-item.score, item.source, item.content[:80]))
        return ranked[: max(1, int(top_k))], {
            "reason": "ok",
            "entries_considered": len(candidate_entries),
            "matches": len(ranked),
            "neighbor_task_ids": neighbors,
        }

    def get_orchestrator(self) -> HybridOrchestrator:
        signature = self._config_signature()
        if self._orchestrator is None or self._signature != signature:
            self._orchestrator = self._build_orchestrator()
            self._signature = signature
        return self._orchestrator

    def _deserialize_chunk(self, payload: dict[str, object]) -> ContextChunk:
        return ContextChunk(
            engine=str(payload.get("engine") or ""),
            source=str(payload.get("source") or ""),
            content=str(payload.get("content") or ""),
            score=float(payload.get("score") or 0.0),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _chunk_identity(self, chunk: ContextChunk) -> tuple[str, str, str]:
        normalized_content = re.sub(r"\s+", " ", str(chunk.content or "").strip().lower())
        return (str(chunk.engine or "").strip().lower(), str(chunk.source or "").strip().lower(), normalized_content[:800])

    def _chunk_content_signature(self, chunk: ContextChunk) -> str:
        return re.sub(r"\s+", " ", str(chunk.content or "").strip().lower())[:800]

    def _is_better_chunk(self, candidate: ContextChunk, existing: ContextChunk) -> bool:
        if candidate.score != existing.score:
            return candidate.score > existing.score
        candidate_key = (candidate.engine, candidate.source, candidate.content[:120])
        existing_key = (existing.engine, existing.source, existing.content[:120])
        return candidate_key < existing_key

    def _dedupe_candidates(self, chunks: list[ContextChunk]) -> tuple[list[ContextChunk], dict[str, int]]:
        by_identity: dict[tuple[str, str, str], ContextChunk] = {}
        duplicate_identity = 0
        for chunk in chunks:
            identity = self._chunk_identity(chunk)
            existing = by_identity.get(identity)
            if existing is None:
                by_identity[identity] = chunk
                continue
            duplicate_identity += 1
            if self._is_better_chunk(chunk, existing):
                by_identity[identity] = chunk

        by_content: dict[str, ContextChunk] = {}
        duplicate_content = 0
        for chunk in by_identity.values():
            content_sig = self._chunk_content_signature(chunk)
            existing = by_content.get(content_sig)
            if existing is None:
                by_content[content_sig] = chunk
                continue
            duplicate_content += 1
            if self._is_better_chunk(chunk, existing):
                by_content[content_sig] = chunk
        deduped = sorted(by_content.values(), key=lambda chunk: (-chunk.score, chunk.engine, chunk.source, chunk.content[:80]))
        return deduped, {"identity_duplicates": duplicate_identity, "content_duplicates": duplicate_content}

    def _expand_candidates(self, chunks: list[ContextChunk], *, max_candidates: int) -> tuple[list[ContextChunk], dict[str, int]]:
        if not chunks:
            return [], {"seed_count": 0, "expanded_count": 0}

        ranked = sorted(chunks, key=lambda chunk: (-chunk.score, chunk.engine, chunk.source, chunk.content[:80]))
        by_source: dict[str, list[ContextChunk]] = defaultdict(list)
        for chunk in ranked:
            by_source[str(chunk.source or "")].append(chunk)

        seeds = ranked[: min(len(ranked), max(1, settings.rag_max_chunks * 2))]
        selected: list[ContextChunk] = []
        seen: set[tuple[str, str, str]] = set()

        def _add(candidate: ContextChunk) -> None:
            key = self._chunk_identity(candidate)
            if key in seen:
                return
            seen.add(key)
            selected.append(candidate)

        for seed in seeds:
            _add(seed)
            for sibling in by_source.get(str(seed.source or ""), []):
                if len(selected) >= max_candidates:
                    break
                if self._chunk_identity(sibling) == self._chunk_identity(seed):
                    continue
                sibling_metadata = dict(sibling.metadata or {})
                sibling_metadata["expanded_from_source"] = seed.source
                sibling_metadata["expansion_kind"] = "source_neighbor"
                _add(
                    ContextChunk(
                        engine=sibling.engine,
                        source=sibling.source,
                        content=sibling.content,
                        score=sibling.score * 0.92,
                        metadata=sibling_metadata,
                    )
                )
            if len(selected) >= max_candidates:
                break

        if len(selected) < max_candidates:
            for candidate in ranked:
                if len(selected) >= max_candidates:
                    break
                _add(candidate)
        return selected[:max_candidates], {"seed_count": len(seeds), "expanded_count": max(0, len(selected) - len(seeds))}

    def _rerank_candidates(
        self,
        *,
        chunks: list[ContextChunk],
        query: str,
        profile: dict[str, object],
    ) -> tuple[list[ContextChunk], dict[str, object]]:
        query_tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query or "") if len(token) > 2]
        engine_weights = dict(profile.get("engine_weights") or {})
        reranked: list[ContextChunk] = []
        for chunk in chunks:
            metadata = dict(chunk.metadata or {})
            engine_weight = float(engine_weights.get(chunk.engine, 1.0))
            source_l = str(chunk.source or "").lower()
            content_l = str(chunk.content or "").lower()
            overlap = 0.0
            for token in query_tokens:
                if token in source_l:
                    overlap += 0.35
                if token in content_l:
                    overlap += 0.15
            relation_bonus = 0.0
            record_kind = str(metadata.get("record_kind") or "").lower()
            if "relation" in record_kind or "dependency" in record_kind or "reference" in record_kind:
                relation_bonus += 0.25
            if metadata.get("artifact_id"):
                relation_bonus += 0.05

            fused_score = (chunk.score * engine_weight) + overlap + relation_bonus
            metadata["fusion"] = {
                "base_score": round(float(chunk.score), 4),
                "engine_weight": round(engine_weight, 4),
                "query_overlap": round(overlap, 4),
                "relation_bonus": round(relation_bonus, 4),
                "fused_score": round(fused_score, 4),
            }
            reranked.append(
                ContextChunk(
                    engine=chunk.engine,
                    source=chunk.source,
                    content=chunk.content,
                    score=fused_score,
                    metadata=metadata,
                )
            )
        reranked.sort(key=lambda chunk: (-chunk.score, chunk.engine, chunk.source, chunk.content[:80]))
        return reranked, {"engine_weights": engine_weights}

    def _diversity_cut(
        self,
        *,
        chunks: list[ContextChunk],
        profile: dict[str, object],
        max_candidates: int,
    ) -> tuple[list[ContextChunk], dict[str, int]]:
        max_per_source = max(1, int(profile.get("max_per_source") or 2))
        max_per_engine = max(1, int(profile.get("max_per_engine") or max(2, settings.rag_max_chunks)))
        per_source: dict[str, int] = defaultdict(int)
        per_engine: dict[str, int] = defaultdict(int)
        selected: list[ContextChunk] = []
        skipped: list[ContextChunk] = []
        for chunk in chunks:
            source_key = str(chunk.source or "").lower()
            engine_key = str(chunk.engine or "").lower()
            if per_source[source_key] >= max_per_source or per_engine[engine_key] >= max_per_engine:
                skipped.append(chunk)
                continue
            selected.append(chunk)
            per_source[source_key] += 1
            per_engine[engine_key] += 1
            if len(selected) >= max_candidates:
                break
        if len(selected) < max_candidates:
            for chunk in skipped:
                if len(selected) >= max_candidates:
                    break
                selected.append(chunk)
        return selected[:max_candidates], {
            "max_per_source": max_per_source,
            "max_per_engine": max_per_engine,
            "selected": len(selected[:max_candidates]),
        }

    def _engine_contributions(self, chunks: list[ContextChunk]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for chunk in chunks:
            counts[str(chunk.engine or "unknown")] += 1
        return dict(sorted(counts.items(), key=lambda item: item[0]))

    def _serialize_context(
        self,
        *,
        orchestrator: HybridOrchestrator,
        query: str,
        strategy: dict[str, object],
        chunks: list[ContextChunk],
    ) -> dict[str, object]:
        serialized_chunks = []
        context_lines: list[str] = []
        for chunk in chunks:
            safe_content = orchestrator._redact(chunk.content)
            context_lines.append(f"[{chunk.engine}] {chunk.source}\n{safe_content}")
            serialized_chunks.append(
                {
                    "engine": chunk.engine,
                    "source": chunk.source,
                    "score": round(chunk.score, 3),
                    "content": safe_content,
                    "metadata": chunk.metadata,
                }
            )
        context_text = "\n\n".join(context_lines)
        return {
            "query": query,
            "strategy": strategy,
            "policy_version": orchestrator.context_manager.policy_version,
            "chunks": serialized_chunks,
            "context_text": context_text,
            "token_estimate": orchestrator.context_manager.estimate_tokens(context_text),
        }

    def retrieve_context(
        self,
        query: str,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        neighbor_task_ids: list[str] | None = None,
    ) -> dict[str, object]:
        orchestrator = self.get_orchestrator()
        context_payload = orchestrator.get_relevant_context(query)
        knowledge_top_k, knowledge_reason = self._knowledge_index_plan(query, task_kind=task_kind, retrieval_intent=retrieval_intent)
        fusion_profile = self._task_profile_for_fusion(task_kind, retrieval_intent)
        knowledge_chunks = self._knowledge_index_retrieval_service.search(
            query,
            top_k=knowledge_top_k,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
        )
        memory_chunks, memory_meta = self._memory_candidates(
            query=query,
            task_id=task_id,
            goal_id=goal_id,
            neighbor_task_ids=neighbor_task_ids,
            top_k=max(2, knowledge_top_k),
        )
        KNOWLEDGE_RETRIEVAL_CHUNKS.observe(len(knowledge_chunks))

        orchestrator_chunks = [
            self._deserialize_chunk(chunk_payload)
            for chunk_payload in context_payload.get("chunks", [])
            if isinstance(chunk_payload, dict)
        ]
        all_candidates = [*orchestrator_chunks, *knowledge_chunks, *memory_chunks]
        deduped_candidates, dedupe_meta = self._dedupe_candidates(all_candidates)
        expanded_candidates, expansion_meta = self._expand_candidates(
            deduped_candidates,
            max_candidates=max(len(deduped_candidates), max(settings.rag_max_chunks * 4, 12)),
        )
        reranked_candidates, rerank_meta = self._rerank_candidates(
            chunks=expanded_candidates,
            query=query,
            profile=fusion_profile,
        )
        diversified_candidates, diversity_meta = self._diversity_cut(
            chunks=reranked_candidates,
            profile=fusion_profile,
            max_candidates=max(settings.rag_max_chunks * 3, settings.rag_max_chunks),
        )
        merged = orchestrator.context_manager.rerank(
            chunks=diversified_candidates,
            query=query,
            max_chunks=settings.rag_max_chunks,
            max_chars=settings.rag_max_context_chars,
            max_tokens=settings.rag_max_context_tokens,
        )
        strategy = dict(context_payload.get("strategy") or {})
        strategy["knowledge_index"] = len(knowledge_chunks)
        strategy["knowledge_index_reason"] = knowledge_reason
        strategy["result_memory"] = len(memory_chunks)
        strategy["result_memory_reason"] = str(memory_meta.get("reason") or "unknown")
        strategy["result_memory_meta"] = memory_meta
        strategy["fusion"] = {
            "mode": "deterministic_v2",
            "task_kind": self._normalize_task_kind(task_kind) or None,
            "retrieval_intent": str(retrieval_intent or "").strip() or None,
            "engine_contributions_before": self._engine_contributions(all_candidates),
            "engine_contributions_after_dedupe": self._engine_contributions(deduped_candidates),
            "engine_contributions_final": self._engine_contributions(merged),
            "dedupe": dedupe_meta,
            "expansion": expansion_meta,
            "rerank": rerank_meta,
            "diversity": diversity_meta,
            "candidate_counts": {
                "orchestrator": len(orchestrator_chunks),
                "knowledge_index": len(knowledge_chunks),
                "result_memory": len(memory_chunks),
                "all": len(all_candidates),
                "deduped": len(deduped_candidates),
                "expanded": len(expanded_candidates),
                "reranked": len(reranked_candidates),
                "diversified": len(diversified_candidates),
                "final": len(merged),
            },
        }
        metric_task_kind = self._normalize_task_kind(task_kind) or "unknown"
        metric_bundle_mode = "standard_32k"
        outcome = "with_knowledge" if knowledge_chunks else "without_knowledge"
        RAG_RETRIEVAL_TASK_KIND_TOTAL.labels(metric_task_kind, metric_bundle_mode, outcome).inc()
        return self._serialize_context(
            orchestrator=orchestrator,
            query=query,
            strategy=strategy,
            chunks=merged,
        )


retrieval_service = RetrievalService()


def get_retrieval_service() -> RetrievalService:
    return retrieval_service
