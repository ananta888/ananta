"""Tests for AugmentContextProvider and ProviderRouter (AUG-100 to AUG-104)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.services.augment.augment_config import AugmentConfig
from agent.services.augment.augment_context_provider import (
    AugmentContextProvider,
    AugmentRawResult,
    ProviderRouter,
    RoutingMode,
)
from agent.services.context_providers.context_provider_port import (
    ContextItem,
    ContextProviderResult,
    ContextScope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_health(ready: bool = True) -> MagicMock:
    h = MagicMock()
    h.is_ready_for_context_provider.return_value = ready
    h.overall = "ok" if ready else "unavailable"
    return h


def _cfg(enabled: bool = True) -> AugmentConfig:
    cfg = AugmentConfig()
    cfg.mcp.enabled = enabled
    cfg.mcp.max_results = 5
    cfg.mcp.tool_name = "codebase-retrieval"
    cfg.security.denied_paths = [".env", ".git"]
    cfg.security.allowed_paths = []
    return cfg


def _scope(
    allowed: list[str] | None = None,
    denied: list[str] | None = None,
    workspace_id: str = "ws-test",
) -> ContextScope:
    return ContextScope(
        workspace_id=workspace_id,
        allowed_paths=allowed or [],
        denied_paths=denied or [],
    )


def _make_item(
    item_id: str = "x1",
    provider: str = "cc",
    path: str = "a.py",
    snippet: str = "code",
    score: float = 0.8,
    source_kind: str = "keyword",
) -> ContextItem:
    """Build a minimal ContextItem with all required fields."""
    return ContextItem(
        item_id=item_id,
        provider=provider,
        path=path,
        symbol=None,
        line_start=None,
        line_end=None,
        snippet=snippet,
        score=score,
        reason="",
        source_kind=source_kind,
        redaction_state="clean",
        warnings=[],
        correlation_id=None,
    )


# ---------------------------------------------------------------------------
# AUG-100: Provider activation guard
# ---------------------------------------------------------------------------

def test_disabled_when_mcp_disabled():
    cfg = _cfg(enabled=False)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True))
    assert provider.is_enabled() is False


def test_disabled_when_health_not_ready():
    cfg = _cfg(enabled=True)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(False))
    assert provider.is_enabled() is False


def test_disabled_when_no_health_status():
    cfg = _cfg(enabled=True)
    provider = AugmentContextProvider(config=cfg, health_status=None)
    assert provider.is_enabled() is False


def test_enabled_when_config_and_health_ok():
    cfg = _cfg(enabled=True)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True))
    assert provider.is_enabled() is True


def test_retrieve_disabled_returns_error():
    cfg = _cfg(enabled=False)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True))
    result = provider.retrieve("query")
    assert result.error == "provider_disabled"
    assert result.items == []


# ---------------------------------------------------------------------------
# AUG-101: Default-deny and path policy
# ---------------------------------------------------------------------------

def test_default_deny_scope_with_no_allowed_paths():
    """Scope provided but allowed_paths empty (from both scope and config) → no_allowed_paths."""
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = []
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True))
    result = provider.retrieve("query", scope=_scope(allowed=[], denied=[]))
    assert result.error == "no_allowed_paths"
    assert result.items == []


def test_no_scope_uses_config_allowed_paths():
    """No scope → allowed_paths come from config.security.allowed_paths."""
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = ["src/"]
    fake_results = [AugmentRawResult(path="src/foo.py", snippet="x" * 10, score=0.8)]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    result = provider.retrieve("query")  # no scope
    assert result.error is None
    assert len(result.items) == 1


def test_retrieve_with_fake_mcp_caller():
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = ["src/"]
    fake_results = [AugmentRawResult(path="src/foo.py", snippet="x" * 100, score=0.8)]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    result = provider.retrieve("query", scope=_scope(allowed=["src/"], denied=[]))
    assert result.error is None
    assert len(result.items) == 1
    assert result.items[0].path == "src/foo.py"
    # AUG-104: external origin marked via source_kind
    assert result.items[0].source_kind == "augment_mcp"


def test_denied_path_blocked():
    """Items on denied_paths are filtered out and counted (AUG-101)."""
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = []
    fake_results = [AugmentRawResult(path=".env", snippet="SECRET=abc", score=0.9)]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    result = provider.retrieve("query")
    assert result.items == []
    assert result.provider_metadata.get("blocked", 0) >= 1


def test_denied_path_not_forwarded_to_mcp():
    """denied_paths must never appear in the MCP call args (AUG-101)."""
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = ["src/"]
    cfg.security.denied_paths = [".env", "secrets"]
    mcp_caller = MagicMock(return_value=[])
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    provider.retrieve("query", scope=_scope(allowed=["src/"], denied=[]))
    call_args = mcp_caller.call_args[0][0]
    assert "denied_paths" not in call_args
    assert ".env" not in str(call_args.get("allowed_paths", []))


def test_always_blocked_segments_filtered():
    """Items in node_modules / secrets etc. are blocked even without explicit deny (AUG-101)."""
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = []
    cfg.security.denied_paths = []
    fake_results = [
        AugmentRawResult(path="node_modules/foo/index.js", snippet="module", score=0.7),
    ]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    result = provider.retrieve("query")
    assert result.items == []
    assert result.provider_metadata.get("blocked", 0) >= 1


# ---------------------------------------------------------------------------
# AUG-103: Snippet and char limits
# ---------------------------------------------------------------------------

def test_snippet_truncated():
    """Snippets longer than MAX_SNIPPET_CHARS are clipped (AUG-103)."""
    cfg = _cfg(enabled=True)
    long_snippet = "a" * 5000
    fake_results = [AugmentRawResult(path="src/big.py", snippet=long_snippet, score=0.7)]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    result = provider.retrieve("query", scope=_scope(allowed=["src/"], denied=[]))
    assert result.truncated is True
    assert len(result.items) == 1
    assert len(result.items[0].snippet) <= provider.MAX_SNIPPET_CHARS
    # AUG-103: redaction_state marks truncated items (no silent full-file dump)
    assert result.items[0].redaction_state == "truncated"
    assert "snippet_truncated" in result.items[0].warnings


def test_snippet_not_truncated_when_within_limit():
    cfg = _cfg(enabled=True)
    snippet = "x" * 100
    fake_results = [AugmentRawResult(path="src/small.py", snippet=snippet, score=0.7)]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    result = provider.retrieve("query", scope=_scope(allowed=["src/"], denied=[]))
    assert result.truncated is False
    assert result.items[0].redaction_state == "clean"
    assert result.items[0].warnings == []


def test_max_results_capped_by_config():
    cfg = _cfg(enabled=True)
    cfg.mcp.max_results = 3
    fake_results = [
        AugmentRawResult(path=f"src/f{i}.py", snippet="x", score=0.5)
        for i in range(10)
    ]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    result = provider.retrieve("query", scope=_scope(allowed=["src/"], denied=[]))
    # MCP is called with max_results <= config.mcp.max_results
    call_args = mcp_caller.call_args[0][0]
    assert call_args["max_results"] <= 3


# ---------------------------------------------------------------------------
# Capabilities and health surface
# ---------------------------------------------------------------------------

def test_capabilities_is_semantic_search():
    cfg = _cfg()
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health())
    caps = provider.capabilities()
    assert caps.supports_semantic_search is True
    assert caps.provider == AugmentContextProvider.PROVIDER_ID


def test_capabilities_not_cross_repo():
    """Augment provider is workspace-scoped, not cross-repo."""
    cfg = _cfg()
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health())
    caps = provider.capabilities()
    assert caps.supports_cross_repo is False


def test_health_returns_ok_when_ready():
    cfg = _cfg()
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True))
    h = provider.health()
    assert h.status == "ok"


def test_health_returns_disabled_when_mcp_disabled():
    cfg = _cfg(enabled=False)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True))
    h = provider.health()
    assert h.status == "disabled"


def test_health_returns_unavailable_when_no_health():
    cfg = _cfg(enabled=True)
    provider = AugmentContextProvider(config=cfg, health_status=None)
    h = provider.health()
    assert h.status == "unavailable"


def test_health_returns_unavailable_when_not_ready():
    cfg = _cfg(enabled=True)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(False))
    h = provider.health()
    assert h.status == "unavailable"


# ---------------------------------------------------------------------------
# AUG-104: Stats / audit trail
# ---------------------------------------------------------------------------

def test_stats_recorded_after_retrieve():
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = ["src/"]
    fake_results = [AugmentRawResult(path="src/a.py", snippet="code", score=0.7)]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    provider.retrieve("my query", scope=_scope(allowed=["src/"], denied=[]))
    stats = provider.last_stats()
    assert stats is not None
    assert stats.query == "my query"
    assert stats.provider == AugmentContextProvider.PROVIDER_ID
    assert stats.items_retrieved == 1
    assert stats.routing_mode == "direct"


def test_stats_tracks_blocked_items():
    cfg = _cfg(enabled=True)
    cfg.security.allowed_paths = []
    fake_results = [
        AugmentRawResult(path=".env", snippet="secret", score=0.9),
        AugmentRawResult(path="src/ok.py", snippet="code", score=0.7),
    ]
    mcp_caller = MagicMock(return_value=fake_results)
    provider = AugmentContextProvider(config=cfg, health_status=_mock_health(True),
                                      mcp_caller=mcp_caller)
    provider.retrieve("q")
    stats = provider.last_stats()
    assert stats is not None
    assert stats.items_blocked >= 1


# ---------------------------------------------------------------------------
# AUG-102: ProviderRouter
# ---------------------------------------------------------------------------

def _fake_cc(items: list[ContextItem] | None = None) -> object:
    class FakeCC:
        def retrieve(self, query: str, *, scope=None, max_results=None) -> ContextProviderResult:
            return ContextProviderResult(
                provider="cc",
                query=query,
                workspace_ref=scope.workspace_id if scope else "",
                items=list(items or []),
                provider_metadata={},
                truncated=False,
                error=None,
            )
    return FakeCC()


def _fake_aug(items: list[ContextItem] | None = None, enabled: bool = True) -> object:
    class FakeAug:
        def is_enabled(self) -> bool:
            return enabled

        def retrieve(self, query: str, *, scope=None, max_results=None) -> ContextProviderResult:
            return ContextProviderResult(
                provider="augment_mcp",
                query=query,
                workspace_ref=scope.workspace_id if scope else "",
                items=list(items or []),
                provider_metadata={},
                truncated=False,
                error=None,
            )
    return FakeAug()


def test_router_codecompass_only_uses_only_cc():
    cc_items = [_make_item(item_id="1", provider="cc", path="a.py", score=0.8)]
    router = ProviderRouter(
        codecompass_provider=_fake_cc(cc_items),
        augment_provider=_fake_aug([]),
        mode=RoutingMode.CODECOMPASS_ONLY,
    )
    items = router.retrieve("q")
    assert len(items) == 1
    assert items[0].provider == "cc"


def test_router_codecompass_only_ignores_augment():
    """Even if augment has results, CODECOMPASS_ONLY never calls it."""
    cc_items = [_make_item(item_id="1", provider="cc", path="a.py", score=0.5)]
    aug_items = [_make_item(item_id="2", provider="augment_mcp", path="b.py", score=0.9,
                            source_kind="augment_mcp")]
    router = ProviderRouter(
        codecompass_provider=_fake_cc(cc_items),
        augment_provider=_fake_aug(aug_items),
        mode=RoutingMode.CODECOMPASS_ONLY,
    )
    items = router.retrieve("q")
    assert all(i.provider == "cc" for i in items)


def test_router_augment_only_returns_aug_items():
    aug_items = [_make_item(item_id="2", provider="augment_mcp", path="b.py", score=0.9,
                            source_kind="augment_mcp")]
    router = ProviderRouter(
        codecompass_provider=_fake_cc([]),
        augment_provider=_fake_aug(aug_items, enabled=True),
        mode=RoutingMode.AUGMENT_ONLY,
    )
    items = router.retrieve("q")
    assert len(items) == 1
    assert items[0].provider == "augment_mcp"


def test_router_augment_only_disabled_returns_empty():
    router = ProviderRouter(
        codecompass_provider=_fake_cc([]),
        augment_provider=_fake_aug(enabled=False),
        mode=RoutingMode.AUGMENT_ONLY,
    )
    items = router.retrieve("q")
    assert items == []


def test_router_hybrid_fallback_uses_aug_when_cc_empty():
    """When CC returns no items (avg_score=0 < threshold), Augment is called."""
    aug_item = _make_item(item_id="2", provider="augment_mcp", path="b.py", score=0.9,
                          source_kind="augment_mcp")
    router = ProviderRouter(
        codecompass_provider=_fake_cc([]),
        augment_provider=_fake_aug([aug_item], enabled=True),
        mode=RoutingMode.HYBRID_FALLBACK,
        min_quality_threshold=0.5,
    )
    items = router.retrieve("q")
    assert any(i.provider == "augment_mcp" for i in items)


def test_router_hybrid_fallback_skips_aug_when_cc_good():
    """When CC items have high avg_score, Augment is NOT called."""
    cc_items = [_make_item(item_id="1", provider="cc", path="a.py", score=0.9)]
    aug_item = _make_item(item_id="2", provider="augment_mcp", path="b.py", score=0.5,
                          source_kind="augment_mcp")
    router = ProviderRouter(
        codecompass_provider=_fake_cc(cc_items),
        augment_provider=_fake_aug([aug_item], enabled=True),
        mode=RoutingMode.HYBRID_FALLBACK,
        min_quality_threshold=0.5,
    )
    items = router.retrieve("q")
    assert all(i.provider == "cc" for i in items)


def test_router_hybrid_parallel_merges_results():
    cc_item = _make_item(item_id="1", provider="cc", path="a.py", score=0.8)
    aug_item = _make_item(item_id="2", provider="augment_mcp", path="b.py", score=0.9,
                          source_kind="augment_mcp")
    router = ProviderRouter(
        codecompass_provider=_fake_cc([cc_item]),
        augment_provider=_fake_aug([aug_item], enabled=True),
        mode=RoutingMode.HYBRID_PARALLEL,
    )
    items = router.retrieve("q")
    assert len(items) == 2
    # aug has higher score → should be first after merge-sort
    assert items[0].score >= items[1].score


def test_router_hybrid_parallel_deduplicates():
    """Same path from both providers → keep only the higher-score hit."""
    cc_item = _make_item(item_id="1", provider="cc", path="a.py", score=0.8)
    aug_item = _make_item(item_id="2", provider="augment_mcp", path="a.py", score=0.7,
                          source_kind="augment_mcp")
    router = ProviderRouter(
        codecompass_provider=_fake_cc([cc_item]),
        augment_provider=_fake_aug([aug_item], enabled=True),
        mode=RoutingMode.HYBRID_PARALLEL,
    )
    items = router.retrieve("q")
    paths = [i.path for i in items]
    assert paths.count("a.py") == 1


def test_router_hybrid_parallel_augment_disabled():
    """When augment is disabled, parallel mode returns only CC results."""
    cc_item = _make_item(item_id="1", provider="cc", path="a.py", score=0.8)
    router = ProviderRouter(
        codecompass_provider=_fake_cc([cc_item]),
        augment_provider=_fake_aug(enabled=False),
        mode=RoutingMode.HYBRID_PARALLEL,
    )
    items = router.retrieve("q")
    assert len(items) == 1
    assert items[0].provider == "cc"
