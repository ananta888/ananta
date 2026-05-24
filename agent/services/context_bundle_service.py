"""ContextBundler — FA-T012 rich LLM prompts for strategies."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from worker.core.propose_orchestrator import ProposeContext


_CONTEXT_BUNDLE_DEFAULTS: dict = {
    "mode": "standard",
    "window_profile": "standard_32k",
    "compact_max_chunks": 5,
    "standard_max_chunks": 12,
    "compact_budget_tokens": 4096,
    "standard_budget_tokens": 32000,
    "full_budget_tokens": 32768,
    "include_context_text": True,
}


def _normalize_positive_int(value, *, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = int(default)
    return max(1, normalized)


def normalize_context_bundle_policy_config(cfg: dict | None) -> dict:
    """Merge caller-provided overrides with defaults and return a clean policy dict."""
    raw = dict(cfg) if isinstance(cfg, dict) else {}
    result = dict(_CONTEXT_BUNDLE_DEFAULTS)
    for key in _CONTEXT_BUNDLE_DEFAULTS:
        if key in raw and raw[key] is not None:
            result[key] = raw[key]
    result["compact_max_chunks"] = _normalize_positive_int(result.get("compact_max_chunks"), default=_CONTEXT_BUNDLE_DEFAULTS["compact_max_chunks"])
    result["standard_max_chunks"] = _normalize_positive_int(result.get("standard_max_chunks"), default=_CONTEXT_BUNDLE_DEFAULTS["standard_max_chunks"])
    return result


def resolve_context_bundle_policy(cfg: dict | None) -> dict:
    """Resolve a normalized context bundle policy to a runtime-ready effective config."""
    policy = normalize_context_bundle_policy_config(cfg)
    mode = str(policy.get("mode") or "standard")
    if mode == "compact":
        max_chunks = policy["compact_max_chunks"]
        total_budget_tokens = policy["compact_budget_tokens"]
    elif mode == "full":
        max_chunks = None
        total_budget_tokens = policy["full_budget_tokens"]
    else:
        max_chunks = policy["standard_max_chunks"]
        total_budget_tokens = policy["standard_budget_tokens"]
    return {
        "mode": mode,
        "window_profile": policy["window_profile"],
        "max_chunks": max_chunks,
        "total_budget_tokens": total_budget_tokens,
        "include_context_text": bool(policy.get("include_context_text", True)),
        "budget_tokens_by_mode": {
            "compact": policy["compact_budget_tokens"],
            "standard": policy["standard_budget_tokens"],
            "full": policy["full_budget_tokens"],
        },
    }


class ContextBundler:
    """Bundles rich context for LLM strategies."""

    @staticmethod
    def bundle(context: ProposeContext, strategy_id: str) -> str:
        policy = dict(strategy_order=["deterministic_handler", "worker_strategy"], allow_legacy_sgpt=False)
        tools = context.tool_definitions_resolver() or []
        schema_str = """
ExecutableProposal schema:
{
  "proposal_id": "string",
  "command": "string or null",
  "tool_calls": array of {"name": "string", "args": object}
}
"""
        examples = ContextBundler._get_examples(strategy_id)
        goal_prompt = context.base_prompt

        prompt = f"""Task: {context.task}

Policy: {policy}

Schema: {schema_str}

Tools: {tools}

Examples:
{examples}

Propose for goal: {goal_prompt}

Output ONLY valid JSON matching schema."""

        return prompt

    @staticmethod
    def build_bundle(
        *,
        query: str,
        context_payload: dict,
        policy_mode: str = "standard",
        llm_scope: str = "local_only",
        include_context_text: bool | None = None,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        total_budget_tokens: int | None = None,
        budget_tokens_by_mode: dict | None = None,
        # OHA-014: optional MemoryTree view to include alongside standard chunks
        memory_tree_retrieval_result: "Any | None" = None,
        **_kwargs: Any,
    ) -> dict:
        """FA-T012: Build a governed context bundle with scope-aware filtering."""
        chunks = list((context_payload or {}).get("chunks") or [])
        filtered = []
        denied = 0
        cloud_deny_sensitivities = {"internal_high", "secret", "credential", "security_sensitive"}
        for chunk in chunks:
            meta = dict(chunk.get("metadata") or {})
            sensitivity = str(meta.get("sensitivity") or "public").lower()
            # external cloud scope: deny internal_high/secret-like content by default
            if llm_scope == "external_cloud_allowed" and sensitivity in cloud_deny_sensitivities:
                denied += 1
                continue
            filtered.append(chunk)

        # Compatibility ordering: architecture-like intents prioritize wiki over repo.
        effective_intent = str(retrieval_intent or "").lower()
        if effective_intent and "architecture" in effective_intent:
            filtered.sort(
                key=lambda c: (
                    0 if str((c.get("metadata") or {}).get("source_type") or "").lower() == "wiki" else 1,
                    -float(c.get("score") or 0.0),
                )
            )
        else:
            filtered.sort(
                key=lambda c: (
                    0 if str((c.get("metadata") or {}).get("source_type") or "").lower() == "repo" else 1,
                    -float(c.get("score") or 0.0),
                )
            )

        # OHA-014: build memory_tree_view from MemoryRetrievalResult
        memory_tree_view: dict | None = None
        mt_denied = 0
        if memory_tree_retrieval_result is not None:
            mt_chunks_allowed = []
            for mc in getattr(memory_tree_retrieval_result, "chunks", []):
                if llm_scope == "external_cloud_allowed" and mc.sensitivity in cloud_deny_sensitivities:
                    mt_denied += 1
                    continue
                mt_chunks_allowed.append({
                    "chunk_id": mc.chunk_id,
                    "source_id": mc.source_id,
                    "label": mc.label,
                    "content": mc.content,
                    "sensitivity": mc.sensitivity,
                    "score": mc.score,
                })
            summary_node = getattr(memory_tree_retrieval_result, "summary_node", None)
            memory_tree_view = {
                "scope": getattr(memory_tree_retrieval_result, "scope", "any"),
                "query": getattr(memory_tree_retrieval_result, "query", query),
                "chunk_count": len(mt_chunks_allowed),
                "chunks": mt_chunks_allowed,
                "denied_count": mt_denied + getattr(memory_tree_retrieval_result, "filtered_by_policy", 0),
                "drilldown_refs": list(getattr(memory_tree_retrieval_result, "drilldown_refs", [])),
                "summary_node": {
                    "node_id": summary_node.node_id,
                    "node_type": summary_node.node_type,
                    "label": summary_node.label,
                    "summary": summary_node.summary,
                    "leaf_count": summary_node.leaf_count,
                } if summary_node else None,
            }

        retrieval_trace = dict((context_payload or {}).get("retrieval_trace") or {})
        if not retrieval_trace:
            by_channel: dict[str, int] = {}
            manifest_hash = None
            for chunk in filtered:
                channel = str(chunk.get("engine") or "unknown")
                by_channel[channel] = by_channel.get(channel, 0) + 1
                meta = dict(chunk.get("metadata") or {})
                if not manifest_hash and meta.get("source_manifest_hash"):
                    manifest_hash = str(meta.get("source_manifest_hash"))
            context_hash = hashlib.sha256(
                "\n".join(str(c.get("content") or "") for c in filtered).encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            retrieval_trace = {
                "trace_id": f"retrieval-{int(time.time())}",
                "manifest_hash": manifest_hash,
                "final_chunk_count": len(filtered),
                "selected_chunk_counts_by_channel": by_channel,
                "context_hash": context_hash,
            }

        channel_contrib: dict[str, int] = {}
        sources: list[dict[str, Any]] = []
        for chunk in filtered:
            channel = str(chunk.get("engine") or "unknown")
            channel_contrib[channel] = channel_contrib.get(channel, 0) + 1
            meta = dict(chunk.get("metadata") or {})
            sources.append(
                {
                    "source": chunk.get("source"),
                    "engine": channel,
                    "expanded_from": meta.get("expanded_from"),
                    "relation_path": meta.get("relation_path"),
                    "source_manifest_hash": meta.get("source_manifest_hash"),
                }
            )

        bundle: dict = {
            "schema": "worker_context_bundle.v1",
            "query": query,
            "policy_mode": policy_mode,
            "task_kind": task_kind,
            "retrieval_intent": retrieval_intent,
            "llm_scope": llm_scope,
            "chunk_count": len(filtered),
            "chunks": filtered,
            "context_text": (context_payload or {}).get("context_text") if include_context_text is not False else None,
            "token_estimate": int((context_payload or {}).get("token_estimate") or 0),
            "context_policy": {
                "default_deny": llm_scope == "external_cloud_allowed",
                "llm_scope": llm_scope,
            },
            "retrieval_trace": retrieval_trace,
            "selection_trace": {
                "retrieval_trace_id": retrieval_trace.get("trace_id"),
                "context_hash": retrieval_trace.get("context_hash"),
                "manifest_hash": retrieval_trace.get("manifest_hash"),
            },
            "explainability": {
                "channel_contributions": channel_contrib,
                "sources": sources,
            },
            "budget": {
                "total_budget_tokens": int(total_budget_tokens or 0),
                "budget_tokens_by_mode": dict(budget_tokens_by_mode or {}),
            },
            "policy_filter": {
                "input_count": len(chunks),
                "allowed_count": len(filtered),
                "denied_count": denied,
                # OHA-014: separate memory_tree counts
                "memory_tree_allowed_count": len(mt_chunks_allowed) if memory_tree_view else 0,
                "memory_tree_denied_count": mt_denied,
            },
        }
        if memory_tree_view is not None:
            bundle["memory_tree_view"] = memory_tree_view
        return bundle

    @staticmethod
    def _get_examples(strategy_id: str) -> str:
        examples = {
            "tool_calling_llm": """
tool_calling_llm examples:
Example valid:
{{"tool_calls": [{{"name": "write_file", "args": {{"path": "main.py", "content": "code"}}}]}}
""",
            "json_schema_llm": """
json_schema_llm examples:
Example valid:
{{"command": "mkdir src"}}
""",
            "flexible_llm_normalization": """
Example fenced JSON:
```json
{{"tool_calls": [...]}}
```
Example shell:
```bash
pip install fastapi
```
""",
        }
        return examples.get(strategy_id, "No examples.")

    @staticmethod
    def resolve_context_bundle_policy(cfg: dict | None) -> dict:
        return resolve_context_bundle_policy(cfg)


_context_bundle_service = ContextBundler()


def get_context_bundle_service() -> ContextBundler:
    return _context_bundle_service


# Backward-compatible alias for older tests/callers.
ContextBundleService = ContextBundler
