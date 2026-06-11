from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from agent.config import settings
from agent.hybrid_orchestrator import ContextChunk, HybridOrchestrator
from agent.repository import memory_entry_repo as default_memory_entry_repo
from agent.services.retrieval_source_contract import normalize_chunk_metadata, resolve_source_selection_policy
from agent.services.task_neighborhood_service import get_task_neighborhood_service


def normalize_task_kind(task_kind: str | None) -> str:
    return str(task_kind or "").strip().lower()


def source_selection_policy(source_types: list[str] | None) -> dict[str, object]:
    return resolve_source_selection_policy(
        settings=settings,
        requested_source_types=source_types,
    ).as_dict()


def knowledge_index_plan(query: str, *, task_kind: str | None, retrieval_intent: str | None) -> tuple[int, str]:
    normalized = str(query or "").lower()
    normalized_kind = normalize_task_kind(task_kind)
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


def task_profile_for_fusion(task_kind: str | None, retrieval_intent: str | None) -> dict[str, object]:
    normalized_kind = normalize_task_kind(task_kind)
    normalized_intent = str(retrieval_intent or "").strip().lower()
    profile: dict[str, object] = {
        "engine_weights": {
            "repository_map": 1.0,
            "semantic_search": 1.0,
            "agentic_search": 0.95,
            "knowledge_index": 1.0,
            "result_memory": 1.05,
        },
        "source_type_weights": {
            "repo": 1.0,
            "artifact": 1.08,
            "task_memory": 1.1,
            "wiki": 1.06,
        },
        "max_per_source": 2,
        "max_per_source_type": max(1, settings.rag_max_chunks // 2),
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
        profile["source_type_weights"] = {"repo": 1.1, "artifact": 1.15, "task_memory": 1.2, "wiki": 1.0}
    elif normalized_kind in {"refactor", "implement", "coding"}:
        profile["engine_weights"] = {
            "repository_map": 1.25,
            "semantic_search": 1.0,
            "agentic_search": 0.9,
            "knowledge_index": 1.1,
            "result_memory": 1.15,
        }
        profile["source_type_weights"] = {"repo": 1.15, "artifact": 1.1, "task_memory": 1.15, "wiki": 1.0}
    elif normalized_kind in {"architecture", "analysis", "doc", "research"}:
        profile["engine_weights"] = {
            "repository_map": 0.85,
            "semantic_search": 1.1,
            "agentic_search": 1.0,
            "knowledge_index": 1.3,
            "result_memory": 1.1,
        }
        profile["source_type_weights"] = {"repo": 0.9, "artifact": 1.2, "task_memory": 1.0, "wiki": 1.25}
        profile["max_per_source"] = 1
    elif normalized_kind in {"config", "xml", "ops"}:
        profile["engine_weights"] = {
            "repository_map": 1.05,
            "semantic_search": 0.95,
            "agentic_search": 1.0,
            "knowledge_index": 1.2,
            "result_memory": 1.15,
        }
        profile["source_type_weights"] = {"repo": 1.05, "artifact": 1.15, "task_memory": 1.1, "wiki": 1.0}

    if "architecture" in normalized_intent:
        engine_weights = dict(profile["engine_weights"] or {})
        engine_weights["knowledge_index"] = max(1.35, float(engine_weights.get("knowledge_index", 1.0)))
        profile["engine_weights"] = engine_weights
        source_weights = dict(profile.get("source_type_weights") or {})
        source_weights["wiki"] = max(1.3, float(source_weights.get("wiki", 1.0)))
        source_weights["artifact"] = max(1.2, float(source_weights.get("artifact", 1.0)))
        profile["source_type_weights"] = source_weights
    if "bug" in normalized_intent or "error" in normalized_intent:
        engine_weights = dict(profile["engine_weights"] or {})
        engine_weights["repository_map"] = max(1.2, float(engine_weights.get("repository_map", 1.0)))
        engine_weights["knowledge_index"] = max(1.25, float(engine_weights.get("knowledge_index", 1.0)))
        engine_weights["result_memory"] = max(1.25, float(engine_weights.get("result_memory", 1.0)))
        profile["engine_weights"] = engine_weights
        source_weights = dict(profile.get("source_type_weights") or {})
        source_weights["repo"] = max(1.15, float(source_weights.get("repo", 1.0)))
        source_weights["artifact"] = max(1.15, float(source_weights.get("artifact", 1.0)))
        source_weights["task_memory"] = max(1.2, float(source_weights.get("task_memory", 1.0)))
        profile["source_type_weights"] = source_weights
    return profile


def source_priority_rules(
    *,
    task_kind: str | None,
    retrieval_intent: str | None,
    source_type_weights: dict[str, object],
) -> dict[str, object]:
    normalized_kind = normalize_task_kind(task_kind) or "generic"
    normalized_intent = str(retrieval_intent or "").strip().lower() or None
    weighted = sorted(
        [
            (str(source_type or "unknown"), float(weight or 0.0))
            for source_type, weight in dict(source_type_weights or {}).items()
        ],
        key=lambda item: (-item[1], item[0]),
    )
    rules = []
    for rank, (source_type, weight) in enumerate(weighted, start=1):
        reason = "base_profile_weight"
        if source_type == "task_memory" and normalized_kind in {"bugfix", "testing", "test", "refactor", "implement", "coding"}:
            reason = "task_execution_history_relevance"
        elif source_type in {"artifact", "wiki"} and normalized_kind in {"architecture", "analysis", "doc", "research"}:
            reason = "architecture_and_documentation_coverage"
        elif source_type == "repo":
            reason = "repository_locality"
        rules.append(
            {
                "rank": rank,
                "source_type": source_type,
                "weight": round(weight, 4),
                "reason": reason,
            }
        )
    return {
        "version": "source-priority-rules-v1",
        "task_kind": normalized_kind,
        "retrieval_intent": normalized_intent,
        "rules": rules,
    }


def score_memory_entry(query_tokens: list[str], title: str, summary: str, content: str, tags: list[str]) -> float:
    haystack = " ".join([title, summary, content, " ".join(tags or [])]).lower()
    if not haystack.strip():
        return 0.0
    score = 0.0
    for token in query_tokens:
        count = haystack.count(token)
        if count > 0:
            score += 0.9 + (count - 1) * 0.12
    return score


def redact_nested(value, *, orchestrator: HybridOrchestrator):
    if isinstance(value, dict):
        return {str(key): redact_nested(item, orchestrator=orchestrator) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_nested(item, orchestrator=orchestrator) for item in value]
    if isinstance(value, str):
        return orchestrator._redact(value)
    return value


def final_merge_trace(chunks: list[ContextChunk]) -> list[dict[str, object]]:
    ranked = sorted(chunks, key=lambda chunk: (-float(chunk.score or 0.0), chunk.engine, chunk.source, chunk.content[:80]))
    trace: list[dict[str, object]] = []
    for index, chunk in enumerate(ranked, start=1):
        trace.append(
            {
                "rank": index,
                "engine": str(chunk.engine or ""),
                "source": str(chunk.source or ""),
                "score": round(float(chunk.score or 0.0), 4),
            }
        )
    return trace


def trace_context_hash(
    *,
    query: str,
    chunks: list[ContextChunk],
    manifest_hash: str,
) -> str:
    selected_records: list[str] = []
    for chunk in chunks:
        metadata = dict(chunk.metadata or {})
        selected_records.append(
            str(metadata.get("record_id") or metadata.get("chunk_id") or chunk.source or "").strip()
        )
    payload = {
        "query": str(query or ""),
        "selected_records": selected_records,
        "manifest_hash": str(manifest_hash or ""),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_retrieval_trace(
    *,
    query: str,
    strategy: dict[str, object],
    chunks: list[ContextChunk],
) -> dict[str, object]:
    selected_chunk_counts_by_channel = engine_contributions(chunks)
    enabled_channels = sorted(str(key) for key in selected_chunk_counts_by_channel.keys())
    source_policy = dict(strategy.get("source_policy") or {})
    effective = [str(item).strip() for item in list(source_policy.get("effective") or []) if str(item).strip()]
    source_channel_map = {
        "repo": "repository_map",
        "artifact": "knowledge_index",
        "wiki": "knowledge_index",
        "task_memory": "result_memory",
    }
    degraded_channels = sorted(
        {
            source_channel_map[source_type]
            for source_type in effective
            if source_channel_map.get(source_type) and source_channel_map[source_type] not in selected_chunk_counts_by_channel
        }
    )
    manifest_hash = ""
    for chunk in chunks:
        metadata = dict(chunk.metadata or {})
        manifest_hash = str(metadata.get("source_manifest_hash") or metadata.get("manifest_hash") or "").strip()
        if manifest_hash:
            break
    fusion = dict(strategy.get("fusion") or {})
    expansion = dict(fusion.get("expansion") or {})
    context_hash = trace_context_hash(query=query, chunks=chunks, manifest_hash=manifest_hash)
    trace_id = f"retrieval-{context_hash[:16]}"
    return {
        "trace_id": trace_id,
        "enabled_channels": enabled_channels,
        "degraded_channels": degraded_channels,
        "seed_counts": {"graph_seed_count": int(expansion.get("seed_count") or 0)},
        "graph_expansion_counts": {"expanded_nodes": int(expansion.get("expanded_count") or 0)},
        "final_chunk_count": len(chunks),
        "context_hash": context_hash,
        "manifest_hash": manifest_hash,
        "selected_chunk_counts_by_channel": selected_chunk_counts_by_channel,
        "channel_latency_ms": {},
    }


def memory_candidates(
    *,
    query: str,
    task_id: str | None,
    goal_id: str | None,
    neighbor_task_ids: list[str] | None,
    top_k: int,
    memory_entry_repository=None,
) -> tuple[list[ContextChunk], dict[str, object]]:
    repo = memory_entry_repository or default_memory_entry_repo
    query_tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query or "") if len(token) > 2]
    if not query_tokens:
        return [], {"reason": "no_query_tokens", "entries_considered": 0}

    neighbors = [str(item).strip() for item in (neighbor_task_ids or []) if str(item).strip()]
    if task_id and not neighbors:
        neighborhood = get_task_neighborhood_service().build_neighborhood(task_id)
        neighbors = list(neighborhood.get("neighbor_task_ids") or [])
    candidate_entries = []
    if task_id:
        candidate_entries.extend(repo.get_by_task(task_id))
    for neighbor_id in neighbors:
        if not task_id or neighbor_id != task_id:
            candidate_entries.extend(repo.get_by_task(neighbor_id))
    if goal_id:
        goal_entries = repo.get_by_goal(goal_id)
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
        memory_metadata = dict(getattr(entry, "memory_metadata", None) or {})
        retrieval_document = str(memory_metadata.get("retrieval_document") or "").strip()
        structured_summary = dict(memory_metadata.get("structured_summary") or {})
        security_metadata = dict(memory_metadata.get("security_metadata") or {})
        score = score_memory_entry(query_tokens, title, summary, content, tags)
        if score <= 0:
            continue
        relation = "goal_related"
        if task_id and entry_task_id == task_id:
            relation = "same_task"
            score += 0.45
        elif entry_task_id in neighbors:
            relation = "task_neighbor"
            score += 0.3
        compact = str(memory_metadata.get("compacted_summary") or "").strip()
        content_preview = (
            retrieval_document
            or "\n".join(part for part in [summary, compact] if part).strip()
            or content[:1200]
        )
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
                    "memory_format": str(memory_metadata.get("memory_format") or ""),
                    "focus_terms": list(structured_summary.get("focus_terms") or []),
                    "compacted_summary": compact or None,
                    "retrieval_document_present": bool(retrieval_document),
                    "security_metadata": security_metadata,
                    "classification": str(
                        security_metadata.get("classification")
                        or memory_metadata.get("classification")
                        or ""
                    ).strip()
                    or None,
                    "source_origin": str(
                        security_metadata.get("source_origin")
                        or memory_metadata.get("source_origin")
                        or "task_memory"
                    ).strip()
                    or "task_memory",
                    "sensitivity": str(
                        security_metadata.get("sensitivity")
                        or memory_metadata.get("sensitivity")
                        or ""
                    ).strip()
                    or None,
                    "tenancy": str(
                        security_metadata.get("tenancy")
                        or memory_metadata.get("tenancy")
                        or ""
                    ).strip()
                    or None,
                    "approval_class": str(
                        security_metadata.get("approval_class")
                        or memory_metadata.get("approval_class")
                        or ""
                    ).strip()
                    or None,
                    "chunk_security_tags": list(
                        security_metadata.get("chunk_security_tags")
                        or memory_metadata.get("chunk_security_tags")
                        or []
                    ),
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


def chunk_identity(chunk: ContextChunk) -> tuple[str, str, str]:
    normalized_content = re.sub(r"\s+", " ", str(chunk.content or "").strip().lower())
    return (str(chunk.engine or "").strip().lower(), str(chunk.source or "").strip().lower(), normalized_content[:800])


def chunk_content_signature(chunk: ContextChunk) -> str:
    return re.sub(r"\s+", " ", str(chunk.content or "").strip().lower())[:800]


def is_better_chunk(candidate: ContextChunk, existing: ContextChunk) -> bool:
    if candidate.score != existing.score:
        return candidate.score > existing.score
    candidate_key = (candidate.engine, candidate.source, candidate.content[:120])
    existing_key = (existing.engine, existing.source, existing.content[:120])
    return candidate_key < existing_key


def dedupe_candidates(chunks: list[ContextChunk]) -> tuple[list[ContextChunk], dict[str, int]]:
    by_identity: dict[tuple[str, str, str], ContextChunk] = {}
    duplicate_identity = 0
    for chunk in chunks:
        identity = chunk_identity(chunk)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = chunk
            continue
        duplicate_identity += 1
        if is_better_chunk(chunk, existing):
            by_identity[identity] = chunk

    by_content: dict[str, ContextChunk] = {}
    duplicate_content = 0
    for chunk in by_identity.values():
        content_sig = chunk_content_signature(chunk)
        existing = by_content.get(content_sig)
        if existing is None:
            by_content[content_sig] = chunk
            continue
        duplicate_content += 1
        if is_better_chunk(chunk, existing):
            by_content[content_sig] = chunk
    deduped = sorted(by_content.values(), key=lambda chunk: (-chunk.score, chunk.engine, chunk.source, chunk.content[:80]))
    return deduped, {"identity_duplicates": duplicate_identity, "content_duplicates": duplicate_content}


def expand_candidates(chunks: list[ContextChunk], *, max_candidates: int) -> tuple[list[ContextChunk], dict[str, int]]:
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
        key = chunk_identity(candidate)
        if key in seen:
            return
        seen.add(key)
        selected.append(candidate)

    for seed in seeds:
        _add(seed)
        for sibling in by_source.get(str(seed.source or ""), []):
            if len(selected) >= max_candidates:
                break
            if chunk_identity(sibling) == chunk_identity(seed):
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


def rerank_candidates(
    *,
    chunks: list[ContextChunk],
    query: str,
    profile: dict[str, object],
) -> tuple[list[ContextChunk], dict[str, object]]:
    query_tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query or "") if len(token) > 2]
    engine_weights = dict(profile.get("engine_weights") or {})
    source_type_weights = dict(profile.get("source_type_weights") or {})
    reranked: list[ContextChunk] = []
    for chunk in chunks:
        metadata = dict(chunk.metadata or {})
        engine_weight = float(engine_weights.get(chunk.engine, 1.0))
        source_type = str(metadata.get("source_type") or "repo").strip().lower() or "repo"
        source_type_weight = float(source_type_weights.get(source_type, 1.0))
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

        fused_score = (chunk.score * engine_weight * source_type_weight) + overlap + relation_bonus
        metadata["fusion"] = {
            "base_score": round(float(chunk.score), 4),
            "engine_weight": round(engine_weight, 4),
            "source_type": source_type,
            "source_type_weight": round(source_type_weight, 4),
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
    return reranked, {"engine_weights": engine_weights, "source_type_weights": source_type_weights}


def diversity_cut(
    *,
    chunks: list[ContextChunk],
    profile: dict[str, object],
    max_candidates: int,
) -> tuple[list[ContextChunk], dict[str, int]]:
    max_per_source = max(1, int(profile.get("max_per_source") or 2))
    max_per_source_type = max(1, int(profile.get("max_per_source_type") or max_per_source))
    max_per_engine = max(1, int(profile.get("max_per_engine") or max(2, settings.rag_max_chunks)))
    per_source: dict[str, int] = defaultdict(int)
    per_source_type: dict[str, int] = defaultdict(int)
    per_engine: dict[str, int] = defaultdict(int)
    selected: list[ContextChunk] = []
    skipped: list[ContextChunk] = []
    for chunk in chunks:
        source_key = str(chunk.source or "").lower()
        engine_key = str(chunk.engine or "").lower()
        source_type_key = str((dict(chunk.metadata or {})).get("source_type") or "repo").strip().lower() or "repo"
        if (
            per_source[source_key] >= max_per_source
            or per_engine[engine_key] >= max_per_engine
            or per_source_type[source_type_key] >= max_per_source_type
        ):
            skipped.append(chunk)
            continue
        selected.append(chunk)
        per_source[source_key] += 1
        per_engine[engine_key] += 1
        per_source_type[source_type_key] += 1
        if len(selected) >= max_candidates:
            break
    if len(selected) < max_candidates:
        for chunk in skipped:
            if len(selected) >= max_candidates:
                break
            selected.append(chunk)
    return selected[:max_candidates], {
        "max_per_source": max_per_source,
        "max_per_source_type": max_per_source_type,
        "max_per_engine": max_per_engine,
        "selected": len(selected[:max_candidates]),
    }


def engine_contributions(chunks: list[ContextChunk]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        counts[str(chunk.engine or "unknown")] += 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def source_type_contributions(chunks: list[ContextChunk]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        metadata = dict(chunk.metadata or {})
        source_type = str(metadata.get("source_type") or "unknown").strip().lower() or "unknown"
        counts[source_type] += 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def normalize_chunks(chunks: list[ContextChunk]) -> list[ContextChunk]:
    normalized: list[ContextChunk] = []
    for chunk in chunks:
        normalized.append(
            ContextChunk(
                engine=chunk.engine,
                source=chunk.source,
                content=chunk.content,
                score=float(chunk.score or 0.0),
                metadata=normalize_chunk_metadata(
                    engine=str(chunk.engine or ""),
                    source=str(chunk.source or ""),
                    content=str(chunk.content or ""),
                    metadata=dict(chunk.metadata or {}),
                ),
            )
        )
    return normalized


def selection_stage_trace(stage: str, chunks: list[ContextChunk], *, limit: int = 5) -> dict[str, object]:
    ranked = sorted(chunks, key=lambda chunk: (-chunk.score, chunk.engine, chunk.source, chunk.content[:80]))
    top: list[dict[str, object]] = []
    for index, chunk in enumerate(ranked[: max(1, limit)], start=1):
        metadata = dict(chunk.metadata or {})
        fusion = dict(metadata.get("fusion") or {})
        top.append(
            {
                "rank": index,
                "engine": str(chunk.engine or ""),
                "source": str(chunk.source or ""),
                "score": round(float(chunk.score or 0.0), 4),
                "record_kind": str(metadata.get("record_kind") or ""),
                "expansion_kind": metadata.get("expansion_kind"),
                "fused_score": fusion.get("fused_score"),
            }
        )
    return {"stage": stage, "count": len(chunks), "top": top}


def serialize_context(
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
        safe_source = orchestrator._redact(str(chunk.source or ""))
        safe_metadata = redact_nested(dict(chunk.metadata or {}), orchestrator=orchestrator)
        context_lines.append(f"[{chunk.engine}] {safe_source}\n{safe_content}")
        serialized_chunks.append(
            {
                "engine": chunk.engine,
                "source": safe_source,
                "score": round(chunk.score, 3),
                "content": safe_content,
                "metadata": safe_metadata,
            }
        )
    context_text = "\n\n".join(context_lines)
    safe_strategy = redact_nested(strategy, orchestrator=orchestrator)
    retrieval_trace = dict(safe_strategy.get("retrieval_trace") or {})
    return {
        "query": query,
        "strategy": safe_strategy,
        "retrieval_trace": retrieval_trace,
        "policy_version": orchestrator.context_manager.policy_version,
        "chunks": serialized_chunks,
        "context_text": context_text,
        "token_estimate": orchestrator.context_manager.estimate_tokens(context_text),
    }
