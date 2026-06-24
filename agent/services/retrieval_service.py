from __future__ import annotations

from pathlib import Path

from agent.config import settings
from agent.hybrid_orchestrator import ContextChunk, HybridOrchestrator
from agent.metrics import KNOWLEDGE_RETRIEVAL_CHUNKS, RAG_RETRIEVAL_TASK_KIND_TOTAL
from agent.repository import memory_entry_repo as default_memory_entry_repo
from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service
from agent.services.retrieval_query_builder import (
    build_retrieval_trace,
    dedupe_candidates,
    diversity_cut,
    engine_contributions,
    expand_candidates,
    final_merge_trace,
    knowledge_index_plan,
    memory_candidates,
    normalize_chunks,
    normalize_task_kind,
    rerank_candidates,
    selection_stage_trace,
    serialize_context,
    source_priority_rules,
    source_selection_policy,
    source_type_contributions,
    task_profile_for_fusion,
)
from agent.services.retrieval_source_adapters import (
    ArtifactKnowledgeSourceAdapter,
    RepoRetrievalSourceAdapter,
    TaskMemorySourceAdapter,
    WikiKnowledgeSourceAdapter,
)
from agent.services.retrieval_source_contract import normalize_chunk_metadata


class RetrievalService:
    """Owns retrieval-engine lifecycle and exposes a stable retrieval seam."""

    def __init__(self, knowledge_index_retrieval_service=None, memory_entry_repository=None) -> None:
        self._orchestrator: HybridOrchestrator | None = None
        self._signature: tuple | None = None
        self._knowledge_index_retrieval_service = knowledge_index_retrieval_service or get_knowledge_index_retrieval_service()
        self._memory_entry_repository = memory_entry_repository or default_memory_entry_repo
        self._source_adapters = self._build_source_adapters()

    def _build_source_adapters(self) -> dict[str, object]:
        return {
            "repo": RepoRetrievalSourceAdapter(
                orchestrator_provider=self.get_orchestrator,
                chunk_deserializer=self._deserialize_chunk,
            ),
            "artifact": ArtifactKnowledgeSourceAdapter(self._knowledge_index_retrieval_service),
            "wiki": WikiKnowledgeSourceAdapter(self._knowledge_index_retrieval_service),
            "task_memory": TaskMemorySourceAdapter(
                memory_search=lambda *, query, task_id, goal_id, neighbor_task_ids, top_k: memory_candidates(
                    query=query,
                    task_id=task_id,
                    goal_id=goal_id,
                    neighbor_task_ids=neighbor_task_ids,
                    top_k=top_k,
                    memory_entry_repository=self._memory_entry_repository,
                ),
            ),
        }

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
            settings.rag_source_repo_enabled,
            settings.rag_source_artifact_enabled,
            settings.rag_source_task_memory_enabled,
            settings.rag_source_wiki_enabled,
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

    def get_source_preflight(self) -> dict[str, object]:
        repo_root = Path(settings.rag_repo_root).resolve()
        data_roots = [repo_root / p.strip() for p in settings.rag_data_roots.split(",") if p.strip()]
        semantic_dir = repo_root / settings.rag_semantic_persist_dir
        try:
            source_policy = source_selection_policy(None)
        except ValueError:
            source_policy = {
                "enabled": [],
                "requested": [],
                "effective": [],
            }
        effective = set(source_policy.get("effective") or [])

        repo_status = "ok"
        repo_issues: list[str] = []
        if not repo_root.exists():
            repo_status = "error"
            repo_issues.append("repo_root_missing")
        elif not repo_root.is_dir():
            repo_status = "error"
            repo_issues.append("repo_root_not_directory")
        elif not any(root.exists() and root.is_dir() for root in data_roots):
            repo_status = "degraded"
            repo_issues.append("rag_data_roots_missing")

        knowledge_preflight = {}
        if hasattr(self._knowledge_index_retrieval_service, "get_source_preflight"):
            knowledge_preflight = dict(self._knowledge_index_retrieval_service.get_source_preflight() or {})
        artifact_status = str((knowledge_preflight.get("artifact") or {}).get("status") or "unknown")
        wiki_status = str((knowledge_preflight.get("wiki") or {}).get("status") or "unknown")

        task_memory_status = "ok"
        task_memory_issues: list[str] = []
        if not hasattr(self._memory_entry_repository, "get_by_task"):
            task_memory_status = "error"
            task_memory_issues.append("memory_repo_missing_get_by_task")

        sources = {
            "repo": {
                "enabled": "repo" in effective,
                "status": repo_status,
                "issues": repo_issues,
                "repo_root": str(repo_root),
                "data_roots": [str(item) for item in data_roots],
                "semantic_persist_dir": str(semantic_dir),
                "semantic_index_present": semantic_dir.exists(),
            },
            "artifact": {
                "enabled": "artifact" in effective,
                "status": artifact_status,
                "issues": list((knowledge_preflight.get("artifact") or {}).get("issues") or []),
                "completed_indices": int((knowledge_preflight.get("artifact") or {}).get("completed_indices") or 0),
            },
            "wiki": {
                "enabled": "wiki" in effective,
                "status": wiki_status,
                "issues": list((knowledge_preflight.get("wiki") or {}).get("issues") or []),
                "completed_indices": int((knowledge_preflight.get("wiki") or {}).get("completed_indices") or 0),
            },
            "task_memory": {
                "enabled": "task_memory" in effective,
                "status": task_memory_status,
                "issues": task_memory_issues,
                "notes": ["task_memory availability is contextual and depends on task/goal neighborhood"],
            },
        }
        source_statuses = [str((item or {}).get("status") or "unknown") for item in sources.values() if bool((item or {}).get("enabled"))]
        global_status = "ok"
        if any(status == "error" for status in source_statuses):
            global_status = "error"
        elif any(status in {"degraded", "unknown"} for status in source_statuses):
            global_status = "degraded"
        return {
            "status": global_status,
            "source_policy": source_policy,
            "sources": sources,
        }

    def get_orchestrator(self) -> HybridOrchestrator:
        signature = self._config_signature()
        if self._orchestrator is None or self._signature != signature:
            self._orchestrator = self._build_orchestrator()
            self._signature = signature
        return self._orchestrator

    def _deserialize_chunk(self, payload: dict[str, object]) -> ContextChunk:
        engine = str(payload.get("engine") or "")
        source = str(payload.get("source") or "")
        content = str(payload.get("content") or "")
        metadata = normalize_chunk_metadata(
            engine=engine,
            source=source,
            content=content,
            metadata=dict(payload.get("metadata") or {}),
        )
        return ContextChunk(
            engine=engine,
            source=source,
            content=content,
            score=float(payload.get("score") or 0.0),
            metadata=metadata,
        )

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
        retrieval_profile: dict | None = None,
        domain_scope: object | None = None,
    ) -> dict[str, object]:
        # CRPS-008: extract profile source_types if not explicitly provided
        effective_source_types_override: list[str] | None = source_types
        if retrieval_profile and isinstance(retrieval_profile, dict) and source_types is None:
            profile_st = list(retrieval_profile.get("source_types") or [])
            if profile_st:
                effective_source_types_override = profile_st
        if effective_source_types_override is None:
            effective_source_types_override = ["repo", "artifact", "task_memory"]

        orchestrator = self.get_orchestrator()
        source_policy = source_selection_policy(effective_source_types_override)
        effective_source_types = set(source_policy.get("effective") or [])
        context_payload: dict[str, object] = {
            "query": query,
            "strategy": {},
            "policy_version": orchestrator.context_manager.policy_version,
            "chunks": [],
        }
        knowledge_top_k, knowledge_reason = knowledge_index_plan(query, task_kind=task_kind, retrieval_intent=retrieval_intent)
        fusion_profile = task_profile_for_fusion(task_kind, retrieval_intent)

        # CRPS-008: merge profile source_type_weights into fusion_profile (profile wins for explicitly set keys)
        if retrieval_profile and isinstance(retrieval_profile, dict):
            profile_weights = dict(retrieval_profile.get("source_type_weights") or {})
            if profile_weights:
                merged_weights = dict(fusion_profile.get("source_type_weights") or {})
                merged_weights.update(profile_weights)
                fusion_profile = dict(fusion_profile)
                fusion_profile["source_type_weights"] = merged_weights
                fusion_profile["profile_id"] = retrieval_profile.get("profile_id")
                fusion_profile["profile_domain"] = retrieval_profile.get("domain")
                fusion_profile["profile_intent"] = retrieval_profile.get("intent")

        knowledge_chunks: list[ContextChunk] = []
        artifact_adapter = self._source_adapters.get("artifact")
        wiki_adapter = self._source_adapters.get("wiki")
        if "artifact" in effective_source_types and isinstance(artifact_adapter, ArtifactKnowledgeSourceAdapter):
            knowledge_chunks.extend(
                artifact_adapter.search(
                    query,
                    top_k=knowledge_top_k,
                    task_kind=task_kind,
                    retrieval_intent=retrieval_intent,
                )
            )
        if "wiki" in effective_source_types and isinstance(wiki_adapter, WikiKnowledgeSourceAdapter):
            knowledge_chunks.extend(
                wiki_adapter.search(
                    query,
                    top_k=knowledge_top_k,
                    task_kind=task_kind,
                    retrieval_intent=retrieval_intent,
                )
            )
        memory_chunks: list[ContextChunk] = []
        memory_meta: dict[str, object] = {"reason": "disabled_by_source_policy", "entries_considered": 0, "matches": 0}
        memory_adapter = self._source_adapters.get("task_memory")
        if "task_memory" in effective_source_types and isinstance(memory_adapter, TaskMemorySourceAdapter):
            memory_chunks, memory_meta = memory_adapter.search_with_meta(
                query,
                top_k=max(2, knowledge_top_k),
                task_kind=task_kind,
                retrieval_intent=retrieval_intent,
                task_id=task_id,
                goal_id=goal_id,
                neighbor_task_ids=neighbor_task_ids,
            )
        KNOWLEDGE_RETRIEVAL_CHUNKS.observe(len(knowledge_chunks))

        orchestrator_chunks: list[ContextChunk] = []
        context_payload: dict[str, object] = {}
        repo_adapter = self._source_adapters.get("repo")
        if "repo" in effective_source_types and isinstance(repo_adapter, RepoRetrievalSourceAdapter):
            context_payload = repo_adapter.load_context(query, domain_scope=domain_scope)
            orchestrator_chunks = repo_adapter.search(
                query,
                top_k=max(settings.rag_max_chunks * 2, 8),
                task_kind=task_kind,
                retrieval_intent=retrieval_intent,
                context_payload=context_payload,
            )
        orchestrator_chunks = normalize_chunks(orchestrator_chunks)
        knowledge_chunks = normalize_chunks(knowledge_chunks)
        memory_chunks = normalize_chunks(memory_chunks)
        all_candidates = [*orchestrator_chunks, *knowledge_chunks, *memory_chunks]
        deduped_candidates, dedupe_meta = dedupe_candidates(all_candidates)

        # CRPS-009: apply negative source pattern filter after dedup
        profile_constraints: dict = {"removed": 0, "patterns": [], "insufficient_positive_sources": False}
        if retrieval_profile and isinstance(retrieval_profile, dict):
            neg_patterns = [str(p).lower() for p in list(retrieval_profile.get("negative_source_patterns") or []) if str(p).strip()]
            if neg_patterns:
                filtered: list = []
                removed_count = 0
                for chunk in deduped_candidates:
                    chunk_source = str(getattr(chunk, "source", "") or "").lower()
                    chunk_metadata = dict(getattr(chunk, "metadata", {}) or {})
                    source_id = str(chunk_metadata.get("source_id") or "").lower()
                    record_kind = str(chunk_metadata.get("record_kind") or "").lower()
                    collection = str(chunk_metadata.get("collection_name") or "").lower()
                    haystack = f"{chunk_source} {source_id} {record_kind} {collection}"
                    if any(pat in haystack for pat in neg_patterns):
                        removed_count += 1
                    else:
                        filtered.append(chunk)
                insufficient = len(filtered) == 0 and removed_count > 0
                profile_constraints = {
                    "removed": removed_count,
                    "patterns": neg_patterns,
                    "insufficient_positive_sources": insufficient,
                }
                if not insufficient:
                    deduped_candidates = filtered
        expanded_candidates, expansion_meta = expand_candidates(
            deduped_candidates,
            max_candidates=max(len(deduped_candidates), max(settings.rag_max_chunks * 4, 12)),
        )
        reranked_candidates, rerank_meta = rerank_candidates(
            chunks=expanded_candidates,
            query=query,
            profile=fusion_profile,
        )
        diversified_candidates, diversity_meta = diversity_cut(
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
        strategy["source_policy"] = source_policy
        strategy["fusion"] = {
            "mode": "deterministic_v2",
            "deterministic_order_key": "score_desc_engine_source_content_prefix",
            "task_kind": normalize_task_kind(task_kind) or None,
            "retrieval_intent": str(retrieval_intent or "").strip() or None,
            "source_policy": source_policy,
            "profile_id": fusion_profile.get("profile_id") if isinstance(fusion_profile, dict) else None,
            "profile_domain": fusion_profile.get("profile_domain") if isinstance(fusion_profile, dict) else None,
            "profile_intent": fusion_profile.get("profile_intent") if isinstance(fusion_profile, dict) else None,
            "profile_constraints": profile_constraints,
            "engine_contributions_before": engine_contributions(all_candidates),
            "engine_contributions_after_dedupe": engine_contributions(deduped_candidates),
            "engine_contributions_final": engine_contributions(merged),
            "source_type_contributions_before": source_type_contributions(all_candidates),
            "source_type_contributions_after_dedupe": source_type_contributions(deduped_candidates),
            "source_type_contributions_final": source_type_contributions(merged),
            "dedupe": dedupe_meta,
            "expansion": expansion_meta,
            "rerank": rerank_meta,
            "diversity": diversity_meta,
            "source_priority_rules": source_priority_rules(
                task_kind=task_kind,
                retrieval_intent=retrieval_intent,
                source_type_weights=dict(rerank_meta.get("source_type_weights") or {}),
            ),
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
            "selection_stages": [
                selection_stage_trace("all_candidates", all_candidates),
                selection_stage_trace("deduped", deduped_candidates),
                selection_stage_trace("expanded", expanded_candidates),
                selection_stage_trace("reranked", reranked_candidates),
                selection_stage_trace("diversified", diversified_candidates),
                selection_stage_trace("final", merged),
            ],
            "final_ranked_sources": final_merge_trace(merged),
        }
        strategy["retrieval_trace"] = build_retrieval_trace(
            query=query,
            strategy=strategy,
            chunks=merged,
        )
        metric_task_kind = normalize_task_kind(task_kind) or "unknown"
        metric_bundle_mode = "standard_32k"
        outcome = "with_knowledge" if knowledge_chunks else "without_knowledge"
        RAG_RETRIEVAL_TASK_KIND_TOTAL.labels(metric_task_kind, metric_bundle_mode, outcome).inc()
        result = serialize_context(
            orchestrator=orchestrator,
            query=query,
            strategy=strategy,
            chunks=merged,
        )
        # CCRDS-014: preserve domain_scope from orchestrator through the chain
        if isinstance(context_payload, dict) and "domain_scope" in context_payload:
            result["domain_scope"] = dict(context_payload["domain_scope"])
        return result


retrieval_service = RetrievalService()


def get_retrieval_service() -> RetrievalService:
    return retrieval_service
