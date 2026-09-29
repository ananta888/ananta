"""Tests for the EmbeddingProviderConfig wiring in CodeCompassRetriever (LCG-010).

The retriever is the only allowed retrieval source for LangChain/LangGraph
chains. LCG-010 requires it to share the EmbeddingProviderConfigService
with the rest of Ananta so a profile's embedding model selection
propagates to the workflow layer.

Backwards compat: pre-LCG callers (no provider_config) must keep working
with no behavioural change beyond a new 'embedding_provider' key in the
metadata that is None when no config is injected.
"""
from __future__ import annotations

import pytest

from worker.retrieval.codecompass_retriever import CodeCompassRetriever


# ── Backwards compat: no config injected ───────────────────────────────


def test_default_retriever_has_no_provider_config():
    """Pre-LCG: CodeCompassRetriever() with no args must still work."""
    r = CodeCompassRetriever()
    # resolved_provider is None until query() is called (lazy resolve).
    assert r.resolved_provider is None
    assert r.resolved_provider_error is None
    result = r.query("anything", max_results=3)
    assert result["query"] == "anything"
    assert isinstance(result["sources"], list)
    assert result["metadata"]["source"] == "codecompass"
    assert result["metadata"]["embedding_provider"] is None
    assert result["metadata"]["max_results"] == 3


# ── LCG-010: config injected, resolves to a provider dict ─────────────


def test_injected_provider_config_resolves():
    """A provider_config dict flows through EmbeddingProviderConfigService.

    The hash provider is the documented default; the resolved dict
    must include the provider name and the model_version that the
    service computed. This proves the wiring end-to-end.
    """
    r = CodeCompassRetriever(provider_config={"provider": "hash"})
    resolved = r.resolved_provider
    assert resolved is not None
    assert resolved["provider"] == "hash"
    # model_version is filled in by the service from the model name.
    assert resolved["model_version"]
    # external_calls_allowed defaults to False (default-deny posture).
    assert resolved["external_calls_allowed"] is False


def test_injected_provider_config_lazy_resolution():
    """resolved_provider is resolved lazily on first access."""
    r = CodeCompassRetriever(provider_config={"provider": "hash"})
    # Internal flag is still unset before first read.
    assert r._resolved_provider is None  # noqa: SLF001
    _ = r.resolved_provider
    assert r._resolved_provider is not None  # noqa: SLF001


def test_injected_provider_config_query_exposes_provider():
    """query()'s metadata surfaces the resolved provider."""
    r = CodeCompassRetriever(provider_config={"provider": "hash"})
    result = r.query("x")
    assert result["metadata"]["embedding_provider"]["provider"] == "hash"


# ── Custom scope ──────────────────────────────────────────────────────


def test_custom_scope_is_passed_to_service(monkeypatch):
    """The scope kwarg flows to EmbeddingProviderConfigService.resolve_for_build.

    The default scope is 'worker_retrieval'. Custom scopes (e.g. when
    a chain wants a different provider from the global default) must
    be honoured. We verify by spying on resolve_for_build.
    """
    from agent.services import embedding_provider_config_service as mod

    captured: dict[str, str] = {}

    class FakeService:
        def __init__(self, global_config=None, scope_overrides=None):
            pass

        def resolve_for_build(self, scope: str = "worker_retrieval") -> dict:
            captured["scope"] = scope
            return {
                "provider": "hash",
                "model": None,
                "model_version": "hash-v1",
                "dimensions": 12,
                "base_url": None,
                "api_key": None,
                "timeout_seconds": 20,
                "external_calls_allowed": False,
                "allowed_base_urls": [],
            }

    monkeypatch.setattr(mod, "EmbeddingProviderConfigService", FakeService)

    r = CodeCompassRetriever(provider_config={"provider": "hash"},
                             scope="workflow_chain")
    _ = r.resolved_provider
    assert captured["scope"] == "workflow_chain"


# ── Resilience: service import failure must not break query() ──────────


def test_service_import_failure_does_not_crash_query(monkeypatch):
    """If EmbeddingProviderConfigService is missing, query() must still
    return a result with embedding_provider=None. LCG-010 is additive:
    pre-LCG code paths must not regress on a missing optional service.
    """
    from agent.services import embedding_provider_config_service as mod
    # Make the import blow up.
    monkeypatch.setattr(mod, "EmbeddingProviderConfigService", None)

    r = CodeCompassRetriever(provider_config={"provider": "hash"})
    result = r.query("x")
    assert result["sources"] == []  # cold index
    assert result["metadata"]["embedding_provider"] is None
    # Error is captured for observability, not raised.
    assert r.resolved_provider_error


# ── Adapter-side wiring (LCG-010 deliverable closure) ────────────────


def test_langchain_adapter_uses_configured_embedding_scope():
    """LangChainAdapter forwards embedding_provider_scope to the retriever."""
    from agent.providers.lc_lg import LangChainProviderConfig
    from worker.adapters.langchain_adapter import LangChainAdapter

    cfg = LangChainProviderConfig(enabled=True, mode="dry_run",
                                  embedding_provider_scope="custom_scope")
    a = LangChainAdapter(cfg)
    assert a._retriever._scope == "custom_scope"  # noqa: SLF001


def test_langchain_adapter_default_scope_is_codecompass_vector():
    from worker.adapters.langchain_adapter import LangChainAdapter
    a = LangChainAdapter()  # default-off config
    assert a._retriever._scope == "codecompass_vector"  # noqa: SLF001


def test_langgraph_adapter_uses_codecompass_vector_scope():
    from worker.adapters.langgraph_adapter import LangGraphAdapter
    g = LangGraphAdapter()
    assert g._retriever._scope == "codecompass_vector"  # noqa: SLF001
