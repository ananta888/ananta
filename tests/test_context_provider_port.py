"""Tests for agent/services/context_providers/context_provider_port.py (AUG-001)."""
from __future__ import annotations

import pytest

from agent.services.context_providers.context_provider_port import (
    ContextItem,
    ContextProvider,
    ContextProviderResult,
    ContextScope,
    ProviderCapabilities,
    ProviderHealth,
)
from agent.services.context_providers.context_item_normalizer import FakeContextProvider


# ── ContextScope ──────────────────────────────────────────────────────────────

class TestContextScope:
    def test_basic_construction(self):
        scope = ContextScope(
            workspace_id="ws-1",
            allowed_paths=["src/"],
            denied_paths=[".env"],
        )
        assert scope.workspace_id == "ws-1"
        assert scope.max_results == 10
        assert scope.timeout_seconds == 30

    def test_custom_max_results(self):
        scope = ContextScope(
            workspace_id="ws-2",
            allowed_paths=[],
            denied_paths=[],
            max_results=5,
        )
        assert scope.max_results == 5

    def test_correlation_id_defaults_none(self):
        scope = ContextScope(workspace_id="ws-3", allowed_paths=[], denied_paths=[])
        assert scope.correlation_id is None


# ── ContextItem ───────────────────────────────────────────────────────────────

class TestContextItem:
    def _make_item(self, **kwargs) -> ContextItem:
        defaults = dict(
            item_id="abc123",
            provider="fake",
            path="src/main.py",
            symbol=None,
            line_start=1,
            line_end=10,
            snippet="def foo(): pass",
            score=0.8,
            reason="test reason",
            source_kind="keyword",
            redaction_state="clean",
            warnings=[],
            correlation_id=None,
        )
        defaults.update(kwargs)
        return ContextItem(**defaults)

    def test_basic_fields(self):
        item = self._make_item()
        assert item.provider == "fake"
        assert item.path == "src/main.py"
        assert item.score == 0.8

    def test_confidence_default(self):
        item = self._make_item()
        assert item.confidence == 0.5

    def test_freshness_default(self):
        item = self._make_item()
        assert item.freshness == 1.0

    def test_policy_status_default(self):
        item = self._make_item()
        assert item.policy_status == "allowed"

    def test_warnings_list(self):
        item = self._make_item(warnings=["snippet_truncated"])
        assert "snippet_truncated" in item.warnings


# ── ContextProviderResult ─────────────────────────────────────────────────────

class TestContextProviderResult:
    def test_basic_construction(self):
        result = ContextProviderResult(
            provider="fake",
            query="find main",
            workspace_ref="ws-1",
            items=[],
            provider_metadata={},
            truncated=False,
            error=None,
        )
        assert result.provider == "fake"
        assert result.items == []
        assert result.error is None

    def test_with_error(self):
        result = ContextProviderResult(
            provider="fake",
            query="q",
            workspace_ref="ws-1",
            items=[],
            provider_metadata={},
            truncated=False,
            error="connection refused",
        )
        assert result.error == "connection refused"


# ── ProviderHealth ────────────────────────────────────────────────────────────

class TestProviderHealth:
    def test_basic_construction(self):
        health = ProviderHealth(
            provider="fake",
            status="ok",
            message="all good",
            checks={"ready": True},
        )
        assert health.status == "ok"
        assert health.checks["ready"] is True


# ── ProviderCapabilities ──────────────────────────────────────────────────────

class TestProviderCapabilities:
    def test_basic_construction(self):
        caps = ProviderCapabilities(
            provider="fake",
            supports_semantic_search=True,
            supports_symbol_lookup=False,
            supports_cross_repo=False,
            max_results=10,
            supports_streaming=False,
        )
        assert caps.provider == "fake"
        assert caps.supports_semantic_search is True
        assert caps.max_results == 10


# ── FakeContextProvider satisfies ContextProvider protocol ────────────────────

class TestContextProviderProtocol:
    def test_fake_satisfies_protocol(self):
        provider = FakeContextProvider()
        assert isinstance(provider, ContextProvider)

    def test_fake_retrieve_returns_result(self):
        provider = FakeContextProvider()
        scope = ContextScope(workspace_id="ws-1", allowed_paths=[], denied_paths=[])
        result = provider.retrieve("test query", scope)
        assert isinstance(result, ContextProviderResult)
        assert result.provider == "fake"
        assert result.query == "test query"

    def test_fake_healthcheck(self):
        provider = FakeContextProvider(health_status="ok")
        health = provider.healthcheck()
        assert health.status == "ok"

    def test_fake_capabilities(self):
        provider = FakeContextProvider()
        caps = provider.capabilities()
        assert isinstance(caps, ProviderCapabilities)

    def test_fake_tracks_calls(self):
        provider = FakeContextProvider()
        scope = ContextScope(workspace_id="ws-1", allowed_paths=[], denied_paths=[])
        provider.retrieve("first", scope)
        provider.retrieve("second", scope)
        calls = provider.get_calls()
        assert len(calls) == 2
        assert calls[0]["query"] == "first"
        assert calls[1]["query"] == "second"

    def test_fake_returns_configured_items(self):
        from agent.services.context_providers.context_provider_port import ContextItem
        item = ContextItem(
            item_id="x1", provider="fake", path="a.py", symbol=None,
            line_start=1, line_end=5, snippet="code", score=0.9,
            reason="", source_kind="keyword", redaction_state="clean",
            warnings=[], correlation_id=None,
        )
        provider = FakeContextProvider(items=[item])
        scope = ContextScope(workspace_id="ws-1", allowed_paths=[], denied_paths=[])
        result = provider.retrieve("q", scope)
        assert len(result.items) == 1
        assert result.items[0].item_id == "x1"

    def test_fake_degraded_health(self):
        provider = FakeContextProvider(health_status="degraded")
        health = provider.healthcheck()
        assert health.status == "degraded"
        assert health.checks["ready"] is False
