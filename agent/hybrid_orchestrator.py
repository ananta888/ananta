from __future__ import annotations

import re
from pathlib import Path

from agent.config import settings
from agent.cli_backends.sgpt import run_llm_cli_command
from agent.hybrid_context_orchestration import collect_context_chunks, serialize_context_result
from agent.hybrid_context_support import redact_sensitive_text
from agent.rag_query_normalizer import normalize_query_from_settings

# Re-exports für Rückwärtskompatibilität
from agent.repository_map_engine import ContextChunk, RepositoryMapEngine
from agent.agentic_search_engine import AgenticSearchEngine, SearchSkill
from agent import semantic_search_engine as _semantic_search
from agent.semantic_search_engine import SemanticSearchEngine
from agent.context_manager import ContextManager

# Compatibility aliases for callers that replace optional semantic-search
# dependencies through the historical facade.
StorageContext = _semantic_search.StorageContext
VectorStoreIndex = _semantic_search.VectorStoreIndex
load_index_from_storage = _semantic_search.load_index_from_storage
SimpleDirectoryReader = _semantic_search.SimpleDirectoryReader

__all__ = [
    "ContextChunk",
    "RepositoryMapEngine",
    "AgenticSearchEngine",
    "SearchSkill",
    "SemanticSearchEngine",
    "ContextManager",
    "HybridOrchestrator",
]


class HybridOrchestrator:
    """Central orchestrator for repository-map, semantic retrieval and agentic search."""

    SECRET_PATTERNS = [
        re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        re.compile(
            r"\b([A-Za-z0-9_]*(?:token|password|secret|apikey|api_key)[A-Za-z0-9_]*\s*[:=]\s*['\"]?[^'\"\s]{6,})", re.I
        ),
        re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"),
    ]

    def __init__(
        self,
        repo_root: str | Path,
        data_roots: list[str | Path] | None = None,
        max_context_chars: int = 12000,
        max_context_tokens: int = 3000,
        max_chunks: int = 12,
        agentic_max_commands: int = 3,
        agentic_timeout_seconds: int = 8,
        semantic_persist_dir: str | Path | None = None,
        redact_sensitive: bool = True,
        codecompass_vector_enabled: bool | None = None,
        codecompass_vector_service: object | None = None,
        global_config: dict | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.data_roots = data_roots or [self.repo_root / "docs", self.repo_root / "data"]
        self.max_context_chars = max_context_chars
        self.max_context_tokens = max_context_tokens
        self.max_chunks = max_chunks
        self.redact_sensitive = redact_sensitive

        persist_dir = Path(semantic_persist_dir) if semantic_persist_dir else (self.repo_root / ".rag" / "llamaindex")
        self.repository_engine = RepositoryMapEngine(self.repo_root)
        self.agentic_engine = AgenticSearchEngine(
            self.repo_root,
            max_commands=agentic_max_commands,
            command_timeout_seconds=agentic_timeout_seconds,
        )
        _semantic_search.StorageContext = StorageContext
        _semantic_search.VectorStoreIndex = VectorStoreIndex
        _semantic_search.load_index_from_storage = load_index_from_storage
        _semantic_search.SimpleDirectoryReader = SimpleDirectoryReader
        self.semantic_engine = SemanticSearchEngine(self.data_roots, persist_dir=persist_dir)
        self.codecompass_vector_service = codecompass_vector_service
        self._global_config: dict = dict(global_config or {})
        vector_enabled = (
            bool(settings.codecompass_vector_enabled)
            if codecompass_vector_enabled is None
            else bool(codecompass_vector_enabled)
        )
        if self.codecompass_vector_service is None and vector_enabled:
            from agent.services.codecompass_vector_retrieval_service import (
                CodeCompassVectorRetrievalService,
            )
            from agent.services.codecompass_ranking_config_service import (
                CodeCompassRankingConfigService,
            )

            ranking_cfg = CodeCompassRankingConfigService(
                global_config=self._global_config,
            ).resolve()
            strategy_cfg = ranking_cfg.to_strategy_config()

            # Wire the restricted inference service when a transformer strategy is configured.
            restricted_inference = None
            if strategy_cfg.wants_prefilter():
                try:
                    from agent.services.restricted_model_inference_service import (
                        RestrictedModelInferenceService,
                    )
                    restricted_inference = RestrictedModelInferenceService()
                except Exception:
                    pass  # degrade gracefully — prefilter skipped if unavailable

            vector_encoding_config = {
                "mode": getattr(settings, "codecompass_vector_encoding_mode", "off"),
                "target_bits": getattr(settings, "codecompass_vector_encoding_target_bits", 32.0),
                "seed": getattr(settings, "codecompass_vector_encoding_seed", 888),
                "block_size": getattr(settings, "codecompass_vector_encoding_block_size", 0),
                "store_original": getattr(settings, "codecompass_vector_encoding_store_original", False),
            }
            self.codecompass_vector_service = CodeCompassVectorRetrievalService(
                repo_root=self.repo_root,
                embedding_records_path=settings.codecompass_vector_embedding_records_path,
                manifest_path=settings.codecompass_vector_manifest_path,
                index_path=settings.codecompass_vector_index_path,
                provider_config={"provider": "local_hash", "model_version": "hash-v1", "dimensions": 12},
                embedding_text_profile=settings.codecompass_vector_embedding_text_profile,
                fail_mode=settings.codecompass_vector_fail_mode,
                restricted_inference_service=restricted_inference,
                strategy_config=strategy_cfg,
                vector_encoding_config=vector_encoding_config,
                vector_encoding_fallback_policy=getattr(
                    settings, "codecompass_vector_encoding_fallback_policy", "fallback_float32"
                ),
            )
        self.context_manager = ContextManager(policy_version="v1")

        # HCCA-009: optional context compression adapter
        self._compression_adapter = None
        compression_cfg = dict(getattr(settings, "global_config", None) or {}).get("context_compression", {})
        if compression_cfg.get("enabled"):
            try:
                from agent.services.context_compression import build_compression_adapter
                self._compression_adapter = build_compression_adapter(compression_cfg)
            except Exception:
                pass  # compression is always optional, degrade gracefully

    def _compress_context_text(
        self, content: str, content_type: str = "rag_results", task_intent: str = ""
    ) -> str:
        """HCCA-010/011: Apply optional context compression to assembled context text."""
        if self._compression_adapter is None or not self._compression_adapter.is_enabled():
            return content
        try:
            from agent.services.context_compression import CompressionRequest
            req = CompressionRequest(
                content=content, content_type=content_type, task_intent=task_intent
            )
            result = self._compression_adapter.compress(req)
            return result.content
        except Exception:
            return content  # always safe passthrough on any error

    def _redact(self, text: str) -> str:
        if not self.redact_sensitive:
            return text
        return redact_sensitive_text(text, self.SECRET_PATTERNS)

    def _resolve_domain_scope(self, domain_scope: object) -> object | None:
        """CCRDS-007: accept a DomainScope or pre-resolved scope, or None."""
        if domain_scope is None:
            return None
        from agent.codecompass.domain_scope import DomainScope, ResolvedDomainScope
        if isinstance(domain_scope, ResolvedDomainScope):
            return domain_scope
        if isinstance(domain_scope, DomainScope):
            from agent.codecompass.domain_scope_resolver import DomainScopeResolver
            resolver = DomainScopeResolver(
                repo_root=self.repo_root,
                artifact_path=str(getattr(settings, "codecompass_domain_artifact_path", "") or "") or None,
                descriptor_root=str(getattr(settings, "codecompass_domain_descriptor_root", "") or "") or None,
            )
            return resolver.resolve(domain_scope)
        raise TypeError(f"unsupported domain_scope type: {type(domain_scope)!r}")

    def get_relevant_context(self, query: str, domain_scope: object | None = None) -> dict[str, object]:
        resolved_scope = self._resolve_domain_scope(domain_scope)
        scope_active = resolved_scope is not None and resolved_scope.active

        if scope_active and not resolved_scope.ok:
            # CCRDS-DD-003: strict resolution failure fails closed — no
            # global fallback, no context, explicit error for the caller.
            from agent.codecompass.domain_scope_filter import build_no_match_guidance
            return {
                "query": query,
                "error": "domain_scope_violation",
                "strategy": {},
                "policy_version": self.context_manager.policy_version,
                "chunks": [],
                "context_text": "",
                "token_estimate": 0,
                "domain_scope": {
                    **resolved_scope.as_dict(),
                    "guidance": build_no_match_guidance(resolved_scope),
                },
            }

        allowed_paths = list(resolved_scope.allowed_read_paths) if scope_active else None
        query_variants = normalize_query_from_settings(query)
        quotas = self.context_manager.route(query)

        # Collect chunks for original query plus any normalized variants.
        # Results are merged and deduplicated; original query keeps routing priority.
        all_chunks: list[ContextChunk] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for variant in query_variants:
            variant_chunks = collect_context_chunks(
                query=variant,
                quotas=quotas,
                repository_engine=self.repository_engine,
                semantic_engine=self.semantic_engine,
                agentic_engine=self.agentic_engine,
                codecompass_vector_service=self.codecompass_vector_service,
                allowed_paths=allowed_paths,
            )
            for chunk in variant_chunks:
                key = (chunk.engine, chunk.source, chunk.content[:120])
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_chunks.append(chunk)

        filter_stats = None
        if scope_active:
            from agent.codecompass.domain_scope_filter import filter_chunks
            all_chunks, filter_stats = filter_chunks(
                all_chunks, resolved_scope, repo_root=self.repo_root
            )

        # Re-score alias anchor chunks using the global max score (across all engines).
        # Alias anchors are injected during repo_engine.search() using only the repo-local
        # max score, which is far below semantic search scores. After collecting all chunks
        # we know the true global max and can give alias anchors a competitive score so
        # they survive the context budget selection.
        global_max_score = max((float(c.score or 0.0) for c in all_chunks), default=1.0)
        try:
            alias_boost = float(getattr(settings, "rag_path_focus_alias_anchor_boost", None) or 0.85)
        except Exception:
            alias_boost = 0.85
        for chunk in all_chunks:
            if dict(getattr(chunk, "metadata", {}) or {}).get("alias_anchor") == "true":
                chunk.score = global_max_score * alias_boost

        best = self.context_manager.rerank(
            chunks=all_chunks,
            query=query,
            max_chunks=self.max_chunks,
            max_chars=self.max_context_chars,
            max_tokens=self.max_context_tokens,
        )

        result = serialize_context_result(
            query=query,
            quotas=quotas,
            policy_version=self.context_manager.policy_version,
            chunks=best,
            redact=self._redact,
            estimate_tokens=self.context_manager.estimate_tokens,
            retrieval_diagnostics=self._retrieval_diagnostics(),
        )
        if scope_active:
            from agent.codecompass.domain_scope_filter import (
                build_no_match_guidance,
                build_scope_banner,
            )
            result["domain_scope"] = {
                **resolved_scope.as_dict(),
                "active_domain_ids": list(resolved_scope.selected_domain_ids),
                "filter_stats": filter_stats.as_dict() if filter_stats else None,
            }
            if not best:
                # CCRDS-014: empty in-scope result — explain instead of
                # silently widening the search.
                result["domain_scope"]["guidance"] = build_no_match_guidance(
                    resolved_scope, filter_stats
                )
            banner = build_scope_banner(resolved_scope, filter_stats)
            result["context_text"] = (
                f"{banner}\n\n{result['context_text']}" if result["context_text"] else banner
            )
        # HCCA-011: apply optional compression to the final assembled context text
        result["context_text"] = self._compress_context_text(
            result["context_text"], content_type="rag_results", task_intent=""
        )
        return result

    def _retrieval_diagnostics(self) -> dict[str, object]:
        diagnostics: dict[str, object] = {}
        if self.codecompass_vector_service is not None and hasattr(self.codecompass_vector_service, "last_diagnostic"):
            diagnostics["codecompass_vector"] = self.codecompass_vector_service.last_diagnostic()
        elif bool(getattr(settings, "codecompass_vector_enabled", False)):
            diagnostics["codecompass_vector"] = {"status": "degraded", "reason": "not_configured"}
        else:
            diagnostics["codecompass_vector"] = {"status": "disabled", "reason": "disabled"}
        return diagnostics

    def run_with_sgpt(
        self,
        query: str,
        options: list[str] | None = None,
        domain_scope: object | None = None,
    ) -> dict[str, object]:
        context = self.get_relevant_context(query, domain_scope=domain_scope)
        if context.get("error"):
            # Strict scope failure: no prompt is built, no LLM is called.
            return {
                "returncode": 1,
                "output": "",
                "errors": str(context["error"]),
                "backend": None,
                "context": context,
            }
        prompt = (
            "Nutze den folgenden selektiven Kontext und beantworte die Frage praezise.\n\n"
            f"Frage:\n{query}\n\n"
            f"Kontext:\n{context['context_text']}"
        )
        rc, output, errors, backend_used = run_llm_cli_command(
            prompt=prompt,
            options=options or ["--no-interaction"],
            backend="auto",
        )
        return {
            "returncode": rc,
            "output": output,
            "errors": errors,
            "backend": backend_used,
            "context": context,
        }
