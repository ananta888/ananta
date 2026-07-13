"""Thin CodeCompass retriever facade for workflow adapters (LCG-009, LCG-010).

The only allowed retriever source; wraps the existing HybridRetrievalService.
Returns a simplified dict so adapters stay decoupled from retrieval internals.

LCG-010: optionally honours EmbeddingProviderConfigService so the workflow
layer shares the same embedding model selection as the rest of Ananta.
The wiring is opt-in: if no provider_config is passed, the retriever
falls back to the default HybridRetrievalService (the pre-LCG path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ananta_contracts.retrieval import (
    RetrievalRequest,
    RetrievalResult,
    RetrievedSource,
)

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
    ) -> None:
        self._provider_config = provider_config
        self._scope = scope
        self._resolved_provider: dict[str, Any] | None = None
        self._resolved_provider_error: str | None = None

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

            svc = HybridRetrievalService()
            result = svc.retrieve(
                query=request.query,
                pipeline_contract=None,
                channel_results={},
                top_k=request.max_results,
            )
            sources, rejected = self._extract_sources(result, request)
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
                "consistency_state": "current",
            },
        )

    @staticmethod
    def _extract_sources(
        result: dict[str, Any],
        request: RetrievalRequest,
    ) -> tuple[list[RetrievedSource], list[str]]:
        candidates = result.get("candidates") or result.get("chunks") or []
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
            provenance = candidate.get("provenance") or metadata.get("provenance") or {}
            if not isinstance(provenance, dict):
                rejected.append("source_provenance_invalid")
                continue
            provenance_source_id = str(provenance.get("source_id") or "").strip()
            provenance_version = str(provenance.get("source_version") or "").strip()
            if (
                (provenance_source_id and provenance_source_id != source_id)
                or (provenance_version and provenance_version != source_version)
            ):
                rejected.append("source_provenance_mismatch")
                continue
            dedupe_key = (source_id, source_version, candidate_scope)
            if dedupe_key in seen:
                rejected.append("source_duplicate")
                continue
            seen.add(dedupe_key)
            content = str(candidate.get("content") or "")[:500]
            out.append(
                RetrievedSource(
                    source_id=source_id,
                    source_version=source_version,
                    tenant_id=request.tenant_id,
                    scope=request.scope,
                    path=str(candidate.get("path") or candidate.get("source") or ""),
                    content=content,
                    score=float(candidate.get("score") or 0.0),
                    provenance={
                        "retriever": "codecompass",
                        "source_id": source_id,
                        "source_version": source_version,
                        "source_provenance": dict(provenance),
                        "trust_boundary": "untrusted_retrieval_content",
                        "prompt_injection_risk": _contains_prompt_injection(content),
                    },
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
    return RetrievalRequest(
        query=str(query),
        tenant_id=str(payload.get("tenant_id") or "unbound"),
        scope=str(payload.get("retrieval_scope") or default_scope),
        allowed_source_ids=allowed,
        max_results=max_results,
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
