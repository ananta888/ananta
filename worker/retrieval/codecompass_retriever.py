"""Thin CodeCompass retriever facade for workflow adapters (LCG-009, LCG-010).

The only allowed retriever source; wraps the existing HybridRetrievalService.
Returns a simplified dict so adapters stay decoupled from retrieval internals.

LCG-010: optionally honours EmbeddingProviderConfigService so the workflow
layer shares the same embedding model selection as the rest of Ananta.
The wiring is opt-in: if no provider_config is passed, the retriever
falls back to the default HybridRetrievalService (the pre-LCG path).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ananta_contracts.retrieval import (
    RetrievalRequest,
    RetrievalResult,
    RetrievedSource,
    SourceRef,
)
from worker.retrieval.codecompass_channel_providers import (
    CodeCompassChannelProvider,
    providers_from_environment,
)
from worker.retrieval.vector_store_config import VectorStoreConfig
from worker.retrieval.vector_store_contract import CompatibilitySpec, VectorScope
from worker.retrieval.vector_store_endpoint_policy import SecretResolver
from worker.retrieval.vector_store_factory import VectorStoreFactory

if TYPE_CHECKING:
    from agent.services.embedding_provider_config_service import (
        EmbeddingProviderConfigService,
    )


class CodeCompassRetriever:
    """Query CodeCompass for context to inject into LangChain/LangGraph chains.

    Parameters
    ----------
    provider_config:
        Optional dict that will be wrapped in an
        ``EmbeddingProviderConfigService(global_config=...)`` and passed
        to ``resolve(scope='worker_retrieval')`` before the underlying
        retrieval is performed. When ``None`` (default), the retriever
        uses the pre-LCG path: ``HybridRetrievalService`` with default
        config. The latter is what every caller did before LCG-010.
    scope:
        Scope name passed to ``EmbeddingProviderConfigService.resolve``.
        Default is ``worker_retrieval`` which is the same scope the
        rest of Ananta uses for retrieval, so a single provider
        selection propagates everywhere.
    """

    def __init__(
        self,
        *,
        provider_config: dict[str, Any] | None = None,
        scope: str = "worker_retrieval",
        channel_providers: Mapping[str, CodeCompassChannelProvider] | None = None,
        graph_store: Any | None = None,
        vector_store_config: Mapping[str, Any] | VectorStoreConfig | None = None,
        vector_store_factory: VectorStoreFactory | None = None,
        trusted_vector_scope: VectorScope | None = None,
        vector_compatibility: CompatibilitySpec | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self._provider_config = provider_config
        self._scope = scope
        self._resolved_provider: dict[str, Any] | None = None
        self._resolved_provider_error: str | None = None
        if channel_providers is None:
            providers, environment_graph_store, diagnostics = providers_from_environment(
                provider_config=provider_config,
                vector_store_config=vector_store_config,
                vector_store_factory=vector_store_factory,
                trusted_vector_scope=trusted_vector_scope,
                vector_compatibility=vector_compatibility,
                secret_resolver=secret_resolver,
            )
            self._channel_providers = providers
            self._graph_store = graph_store or environment_graph_store
            self._provider_diagnostics = diagnostics
        else:
            self._channel_providers = {str(name): provider for name, provider in channel_providers.items()}
            self._graph_store = graph_store
            self._provider_diagnostics = {}

    # ── LCG-010 wiring ─────────────────────────────────────────────────

    @property
    def resolved_provider(self) -> dict[str, Any] | None:
        """The flat provider dict the retriever is currently using.

        None if no provider_config was injected, or if resolution
        failed (in which case ``resolved_provider_error`` is set).
        """
        if self._resolved_provider is None and self._provider_config is not None:
            self._resolve_provider()
        return self._resolved_provider

    @property
    def resolved_provider_error(self) -> str | None:
        return self._resolved_provider_error

    def _resolve_provider(self) -> None:
        """Resolve the embedding provider via EmbeddingProviderConfigService.

        Errors are captured, not raised. The retriever still falls back
        to HybridRetrievalService if the service is unavailable —
        LCG-010 adds wiring, not a hard dependency.
        """
        try:
            from agent.services.embedding_provider_config_service import (
                EmbeddingProviderConfigService,
            )

            svc: EmbeddingProviderConfigService = EmbeddingProviderConfigService(
                global_config=self._provider_config or {},
            )
            self._resolved_provider = svc.resolve_for_build(scope=self._scope)
        except Exception as exc:  # ImportError, ValidationError, etc.
            self._resolved_provider_error = f"{type(exc).__name__}: {exc}"
            self._resolved_provider = None

    # ── Query API ──────────────────────────────────────────────────────

    def query(self, query: str, *, max_results: int = 5) -> dict[str, Any]:
        """Backward-compatible query facade with fail-closed provenance.

        Callers that need grounded content must pass ``allowed_source_ids`` via
        :meth:`retrieve`. The legacy facade has no authoritative source catalog,
        therefore unknown candidates are rejected instead of receiving invented IDs.
        """
        return self.retrieve(
            RetrievalRequest(
                query=query,
                tenant_id="unbound",
                scope=self._scope,
                allowed_source_ids=frozenset(),
                max_results=max_results,
            )
        ).to_dict()

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if not request.tenant_id.strip():
            raise ValueError("retrieval_tenant_id_required")
        if not request.scope.strip():
            raise ValueError("retrieval_scope_required")
        if request.max_results < 1 or request.max_results > 100:
            raise ValueError("retrieval_max_results_invalid")

        try:
            from worker.retrieval.retrieval_service import HybridRetrievalService

            channel_results, channel_errors, latency, graph_expansion, queried_channels = self._query_channels(
                request.query,
                top_k=request.max_results,
                retrieval_profile=request.retrieval_profile,
            )
            contract_channels = list(channel_results)
            if graph_expansion is not None:
                contract_channels.append("codecompass_graph")
            if not contract_channels:
                return RetrievalResult(
                    query=request.query,
                    sources=(),
                    rejected_count=0,
                    rejection_reasons=("retrieval_provider_unconfigured",),
                    metadata={
                        "max_results": request.max_results,
                        "source": "codecompass",
                        "scope": request.scope,
                        "tenant_id": request.tenant_id,
                        "embedding_provider": self.resolved_provider,
                        "consistency_state": "degraded",
                        "channel_diagnostics": dict(self._provider_diagnostics),
                    },
                )
            svc = HybridRetrievalService()
            result = svc.retrieve(
                query=request.query,
                pipeline_contract={
                    "channels": contract_channels,
                    "fallback_order": contract_channels,
                },
                channel_results=channel_results,
                channel_errors=channel_errors,
                graph_expansion=graph_expansion,
                channel_latency_ms=latency,
                top_k=request.max_results,
            )
            sources, rejected = self._extract_sources(
                result,
                request,
                defer_content_scan=self._scope == "visual_process_assistant",
            )
            graph_chunks = list((graph_expansion or {}).get("chunks") or [])
            if not any(channel_results.values()) and not graph_chunks:
                rejected.append("production_channel_empty")
        except Exception as exc:  # Retrieval remains read-only and explicitly degraded.
            return RetrievalResult(
                query=request.query,
                sources=(),
                rejected_count=0,
                rejection_reasons=(f"retriever_unavailable:{type(exc).__name__}",),
                metadata={
                    "max_results": request.max_results,
                    "source": "codecompass",
                    "scope": request.scope,
                    "tenant_id": request.tenant_id,
                    "embedding_provider": self.resolved_provider,
                    "consistency_state": "degraded",
                },
            )

        diagnostics = dict(result.get("channel_diagnostics") or {})
        channel_degraded = any(
            str(row.get("status") or "").lower() in {"degraded", "failed"}
            for row in diagnostics.values()
            if isinstance(row, Mapping)
        )
        return RetrievalResult(
            query=request.query,
            sources=tuple(sources),
            rejected_count=len(rejected),
            rejection_reasons=tuple(sorted(set(rejected))),
            metadata={
                "max_results": request.max_results,
                "source": "codecompass",
                "scope": request.scope,
                "tenant_id": request.tenant_id,
                "embedding_provider": self.resolved_provider,
                "consistency_state": (
                    "current"
                    if queried_channels
                    and not channel_errors
                    and not self._provider_diagnostics
                    and not channel_degraded
                    and not rejected
                    else "degraded"
                ),
                "queried_channels": queried_channels,
                "channel_diagnostics": diagnostics,
            },
        )

    def _query_channels(
        self,
        query: str,
        *,
        top_k: int,
        retrieval_profile: Mapping[str, Any] | None = None,
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, str],
        dict[str, int],
        dict[str, Any] | None,
        list[str],
    ]:
        results: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        latency: dict[str, int] = {}
        queried: list[str] = []
        for channel, provider in sorted(self._channel_providers.items()):
            started = time.perf_counter()
            try:
                profiled_search = getattr(provider, "search_profiled", None)
                profile_name = str((retrieval_profile or {}).get("name") or "").strip().lower()
                if (
                    channel == "codecompass_fts"
                    and profile_name == "corpus_discriminative_lexical"
                    and callable(profiled_search)
                ):
                    rows = profiled_search(
                        query=query,
                        top_k=max(1, int(top_k) * 3),
                        retrieval_profile=dict(retrieval_profile or {}),
                        task_kind="bugfix",
                        retrieval_intent="fuzzy_semantic",
                    )
                else:
                    rows = provider.search(
                        query=query,
                        top_k=max(1, int(top_k) * 3),
                        task_kind="bugfix",
                        retrieval_intent="exact_symbol" if channel == "symbol" else "fuzzy_semantic",
                    )
                results[channel] = [
                    self._normalize_channel_candidate(item) for item in list(rows or []) if isinstance(item, dict)
                ]
                queried.append(channel)
            except Exception as exc:
                results[channel] = []
                errors[channel] = f"provider_failed:{type(exc).__name__}"
            latency[channel] = max(0, int((time.perf_counter() - started) * 1000))

        graph_expansion: dict[str, Any] | None = None
        if self._graph_store is not None:
            started = time.perf_counter()
            try:
                from worker.retrieval.codecompass_graph_expansion import expand_codecompass_graph

                seeds = sorted(
                    {
                        str(row.get("record_id") or "")
                        for rows in results.values()
                        for row in rows
                        if str(row.get("record_id") or "")
                    }
                )
                expanded = expand_codecompass_graph(
                    store=self._graph_store,
                    seed_node_ids=seeds,
                    profile="bugfix_local",
                )
                graph_expansion = {
                    "chunks": [self._graph_node_candidate(node) for node in list(expanded.get("nodes") or [])],
                    "diagnostics": {
                        "seed_count": len(seeds),
                        "expanded_count": len(list(expanded.get("nodes") or [])),
                    },
                }
                queried.append("codecompass_graph")
            except Exception as exc:
                errors["codecompass_graph"] = f"provider_failed:{type(exc).__name__}"
            latency["codecompass_graph"] = max(0, int((time.perf_counter() - started) * 1000))
        return results, errors, latency, graph_expansion, queried

    @staticmethod
    def _normalize_channel_candidate(item: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(item)
        metadata = dict(raw.get("metadata") or {})
        provenance = raw.get("provenance") or metadata.get("provenance") or {}
        return {
            **raw,
            "path": str(raw.get("path") or raw.get("source") or metadata.get("file") or ""),
            "record_id": str(raw.get("record_id") or metadata.get("record_id") or ""),
            "content_hash": str(
                raw.get("content_hash") or metadata.get("content_hash") or metadata.get("document_hash") or ""
            ),
            "content": str(raw.get("content") or raw.get("text") or ""),
            "score": float(raw.get("score") or raw.get("final_score") or 0.0),
            "source_id": str(raw.get("source_id") or metadata.get("source_id") or ""),
            "source_version": str(raw.get("source_version") or metadata.get("source_version") or ""),
            "tenant_id": str(raw.get("tenant_id") or metadata.get("tenant_id") or ""),
            "scope": str(raw.get("scope") or metadata.get("scope") or ""),
            "provenance": dict(provenance) if isinstance(provenance, Mapping) else {},
            "provenance_digest": str(raw.get("provenance_digest") or metadata.get("provenance_digest") or ""),
            "metadata": metadata,
        }

    @classmethod
    def _graph_node_candidate(cls, node: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(node)
        source_record = dict(raw.get("source_record") or {})
        merged = {**source_record, **raw}
        return cls._normalize_channel_candidate(
            {
                "path": merged.get("file") or merged.get("path"),
                "record_id": merged.get("record_id") or merged.get("id"),
                "content_hash": merged.get("content_hash"),
                "content": merged.get("summary") or merged.get("name") or "",
                "score": merged.get("score") or 0.15,
                "source_id": merged.get("source_id"),
                "source_version": merged.get("source_version"),
                "tenant_id": merged.get("tenant_id"),
                "scope": merged.get("scope") or merged.get("source_scope"),
                "provenance": merged.get("provenance") or {},
                "provenance_digest": merged.get("provenance_digest"),
                "metadata": {
                    "record_kind": merged.get("record_kind") or merged.get("kind"),
                    "line_start": merged.get("line_start") or merged.get("line"),
                    "line_end": merged.get("line_end") or merged.get("end_line"),
                },
            }
        )

    @staticmethod
    def _extract_sources(
        result: dict[str, Any],
        request: RetrievalRequest,
        *,
        defer_content_scan: bool = False,
    ) -> tuple[list[RetrievedSource], list[str]]:
        candidates = result.get("candidates") or result.get("chunks") or result.get("selected") or []
        out: list[RetrievedSource] = []
        rejected: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in candidates:
            if len(out) >= request.max_results:
                break
            metadata = dict(candidate.get("metadata") or {})
            source_id = str(
                candidate.get("source_id") or metadata.get("source_id") or metadata.get("registry_source_id") or ""
            ).strip()
            if not source_id:
                rejected.append("source_id_missing")
                continue
            if source_id not in request.allowed_source_ids:
                rejected.append("source_id_unverified")
                continue
            try:
                authoritative_ref = request.source_ref(source_id)
            except ValueError:
                rejected.append("source_ref_ambiguous")
                continue
            candidate_tenant = str(candidate.get("tenant_id") or metadata.get("tenant_id") or request.tenant_id)
            candidate_scope = str(candidate.get("scope") or metadata.get("scope") or request.scope)
            if candidate_tenant != request.tenant_id:
                rejected.append("source_tenant_mismatch")
                continue
            if candidate_scope != request.scope:
                rejected.append("source_scope_mismatch")
                continue
            source_version = str(
                candidate.get("source_version") or metadata.get("source_version") or metadata.get("snapshot_id") or ""
            ).strip()
            if not source_version:
                rejected.append("source_version_missing")
                continue
            if request.repository_revision and source_version != request.repository_revision:
                rejected.append("repository_revision_mismatch")
                continue
            candidate_manifest = str(
                candidate.get("manifest_hash")
                or candidate.get("source_manifest_hash")
                or metadata.get("manifest_hash")
                or metadata.get("source_manifest_hash")
                or ""
            ).strip()
            if request.manifest_hash:
                if not candidate_manifest:
                    rejected.append("source_manifest_missing")
                    continue
                if candidate_manifest != request.manifest_hash:
                    rejected.append("source_manifest_mismatch")
                    continue
            provenance = candidate.get("provenance") or metadata.get("provenance") or {}
            if not isinstance(provenance, dict):
                rejected.append("source_provenance_invalid")
                continue
            provenance_source_id = str(provenance.get("source_id") or "").strip()
            provenance_version = str(provenance.get("source_version") or "").strip()
            if (provenance_source_id and provenance_source_id != source_id) or (
                provenance_version and provenance_version != source_version
            ):
                rejected.append("source_provenance_mismatch")
                continue
            if authoritative_ref is not None:
                if source_version != authoritative_ref.source_version:
                    rejected.append("source_version_mismatch")
                    continue
                if candidate_tenant != authoritative_ref.tenant_id:
                    rejected.append("source_tenant_mismatch")
                    continue
                if candidate_scope != authoritative_ref.scope:
                    rejected.append("source_scope_mismatch")
                    continue
                candidate_digest = (
                    str(
                        candidate.get("provenance_digest")
                        or metadata.get("provenance_digest")
                        or provenance.get("provenance_digest")
                        or ""
                    )
                    .strip()
                    .lower()
                )
                if not candidate_digest and provenance:
                    candidate_digest = hashlib.sha256(
                        json.dumps(
                            provenance,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("utf-8")
                    ).hexdigest()
                if candidate_digest.startswith("sha256:"):
                    candidate_digest = candidate_digest.split(":", 1)[1]
                if candidate_digest != authoritative_ref.provenance_digest:
                    rejected.append("source_provenance_digest_mismatch")
                    continue
            dedupe_key = (source_id, source_version, candidate_scope)
            if dedupe_key in seen:
                rejected.append("source_duplicate")
                continue
            seen.add(dedupe_key)
            content = str(candidate.get("content") or "")[:500]
            if not defer_content_scan and _contains_prompt_injection(content):
                rejected.append("prompt_injection_detected")
                continue
            released_provenance: dict[str, Any] = {
                "retriever": "codecompass",
                "source_id": source_id,
                "source_version": source_version,
                "source_provenance": dict(provenance),
                "trust_boundary": "untrusted_retrieval_content",
                "prompt_injection_risk": False,
            }
            if candidate_manifest:
                released_provenance["manifest_hash"] = candidate_manifest
            record_kind = (
                str(candidate.get("record_kind") or metadata.get("record_kind") or provenance.get("record_kind") or "")
                .strip()
                .lower()
            )
            conflict_key = str(
                candidate.get("evidence_conflict_key")
                or metadata.get("evidence_conflict_key")
                or provenance.get("evidence_conflict_key")
                or ""
            ).strip()
            assertion_digest = (
                str(
                    candidate.get("assertion_digest")
                    or metadata.get("assertion_digest")
                    or provenance.get("assertion_digest")
                    or ""
                )
                .strip()
                .lower()
            )
            if record_kind:
                released_provenance["record_kind"] = record_kind[:120]
            if (
                conflict_key
                and len(conflict_key) <= 200
                and all(char.isalnum() or char in "._:/-" for char in conflict_key)
            ):
                released_provenance["evidence_conflict_key"] = conflict_key
            if len(assertion_digest) == 64 and all(char in "0123456789abcdef" for char in assertion_digest):
                released_provenance["assertion_digest"] = assertion_digest
            for line_key in ("line_start", "line_end"):
                if metadata.get(line_key) is not None:
                    released_provenance[line_key] = metadata[line_key]
            out.append(
                RetrievedSource(
                    source_id=source_id,
                    source_version=source_version,
                    tenant_id=request.tenant_id,
                    scope=request.scope,
                    path=str(candidate.get("path") or candidate.get("source") or ""),
                    content=content,
                    score=float(candidate.get("final_score") or candidate.get("score") or 0.0),
                    provenance=released_provenance,
                    source_ref=authoritative_ref,
                )
            )
        return out, rejected


def retrieval_request_from_payload(
    *,
    query: str,
    payload: dict[str, Any],
    default_scope: str,
    max_results: int = 5,
) -> RetrievalRequest:
    """Build the one runtime-neutral request used by native/LC/LG adapters."""

    raw_ids = payload.get("allowed_source_ids") or payload.get("source_ids") or ()
    if not isinstance(raw_ids, (list, tuple, set, frozenset)):
        raise ValueError("retrieval_allowed_source_ids_invalid")
    allowed = frozenset(str(item).strip() for item in raw_ids if str(item).strip())
    raw_refs = payload.get("allowed_source_refs") or ()
    if not isinstance(raw_refs, (list, tuple)):
        raise ValueError("retrieval_allowed_source_refs_invalid")
    refs = tuple(SourceRef.from_mapping(item) for item in raw_refs if isinstance(item, Mapping))
    if len(refs) != len(raw_refs):
        raise ValueError("retrieval_allowed_source_refs_invalid")
    tenant_id = str(payload.get("tenant_id") or "unbound")
    scope = str(payload.get("retrieval_scope") or default_scope)
    if len({ref.source_id for ref in refs}) != len(refs):
        raise ValueError("retrieval_duplicate_source_ref")
    if any(ref.tenant_id != tenant_id for ref in refs):
        raise ValueError("retrieval_source_ref_tenant_mismatch")
    if any(ref.scope != scope for ref in refs):
        raise ValueError("retrieval_source_ref_scope_mismatch")
    allowed = frozenset({*allowed, *(ref.source_id for ref in refs)})
    return RetrievalRequest(
        query=str(query),
        tenant_id=tenant_id,
        scope=scope,
        allowed_source_ids=allowed,
        max_results=max_results,
        allowed_source_refs=refs,
        repository_revision=str(payload.get("repository_revision") or ""),
        manifest_hash=str(payload.get("manifest_hash") or payload.get("codecompass_manifest_hash") or ""),
        source_allowlist_version=str(payload.get("source_allowlist_version") or ""),
    )


def _contains_prompt_injection(content: str) -> bool:
    normalized = " ".join(str(content).lower().split())
    markers = (
        "ignore previous instructions",
        "ignore all instructions",
        "system prompt",
        "developer message",
        "reveal your secret",
    )
    return any(marker in normalized for marker in markers)


__all__ = ["CodeCompassRetriever", "retrieval_request_from_payload"]
