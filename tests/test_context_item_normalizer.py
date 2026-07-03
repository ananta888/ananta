"""Tests for agent/services/context_providers/context_item_normalizer.py (AUG-002)."""
from __future__ import annotations

import pytest

from agent.services.context_providers.context_item_normalizer import (
    MAX_ITEMS,
    MAX_SNIPPET_CHARS,
    ContextItemNormalizer,
    FakeContextProvider,
    _safe_int,
)
from agent.services.context_providers.context_provider_port import (
    ContextItem,
    ContextScope,
)


def _make_scope(
    workspace_id: str = "ws-1",
    allowed_paths: list[str] | None = None,
    denied_paths: list[str] | None = None,
    correlation_id: str | None = None,
) -> ContextScope:
    return ContextScope(
        workspace_id=workspace_id,
        allowed_paths=allowed_paths or [],
        denied_paths=denied_paths or [],
        correlation_id=correlation_id,
    )


def _make_raw(**kwargs) -> dict:
    defaults = dict(path="src/main.py", snippet="def foo(): pass")
    defaults.update(kwargs)
    return defaults


# ── normalize_item ────────────────────────────────────────────────────────────

class TestNormalizeItem:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()
        self.scope = _make_scope()

    def test_basic_normalization(self):
        raw = _make_raw()
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.provider == "fake"
        assert item.path == "src/main.py"

    def test_no_path_returns_none(self):
        raw = {"snippet": "some code"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is None

    def test_no_snippet_returns_none(self):
        raw = {"path": "src/main.py"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is None

    def test_filepath_alias(self):
        raw = {"filepath": "src/utils.py", "snippet": "x = 1"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.path == "src/utils.py"

    def test_content_alias_for_snippet(self):
        raw = {"path": "a.py", "content": "class Foo: pass"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert "class Foo" in item.snippet

    def test_text_alias_for_snippet(self):
        raw = {"path": "b.py", "text": "import os"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None

    def test_score_clamped_low(self):
        raw = _make_raw(score=-5.0)
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.score == 0.0

    def test_score_clamped_high(self):
        raw = _make_raw(score=999.0)
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.score == 1.0

    def test_score_default_half(self):
        raw = _make_raw()
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.score == 0.5

    def test_correlation_id_propagated(self):
        scope = _make_scope(correlation_id="corr-42")
        raw = _make_raw()
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=scope)
        assert item is not None
        assert item.correlation_id == "corr-42"

    def test_symbol_field(self):
        raw = _make_raw(symbol="MyClass")
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.symbol == "MyClass"

    def test_function_alias_for_symbol(self):
        raw = _make_raw(function="my_func")
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.symbol == "my_func"

    def test_line_start_and_end(self):
        raw = _make_raw(line_start=10, line_end=20)
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.line_start == 10
        assert item.line_end == 20

    def test_source_kind_defaults_to_keyword(self):
        raw = _make_raw()
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.source_kind == "keyword"

    def test_policy_status_allowed(self):
        raw = _make_raw()
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.policy_status == "allowed"

    def test_denied_path_returns_none(self):
        scope = _make_scope(denied_paths=["src/"])
        raw = _make_raw(path="src/main.py")
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=scope)
        assert item is None

    def test_always_blocked_env_returns_none(self):
        raw = {"path": ".env", "snippet": "SECRET=abc123"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is None

    def test_always_blocked_git_returns_none(self):
        raw = {"path": ".git/config", "snippet": "some git config"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is None

    def test_node_modules_blocked(self):
        raw = {"path": "node_modules/lodash/index.js", "snippet": "exports.chunk = chunk;"}
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is None


# ── apply_path_filter ─────────────────────────────────────────────────────────

class TestApplyPathFilter:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()

    def test_empty_scope_allows_all(self):
        scope = _make_scope()
        allowed, reason = self.normalizer.apply_path_filter("src/main.py", scope)
        assert allowed is True

    def test_always_blocked_env(self):
        scope = _make_scope()
        allowed, reason = self.normalizer.apply_path_filter(".env", scope)
        assert allowed is False
        assert "always_blocked" in reason

    def test_always_blocked_secrets(self):
        scope = _make_scope()
        allowed, reason = self.normalizer.apply_path_filter("secrets/key.txt", scope)
        assert allowed is False

    def test_denied_path_prefix(self):
        scope = _make_scope(denied_paths=["private/"])
        allowed, reason = self.normalizer.apply_path_filter("private/stuff.py", scope)
        assert allowed is False
        assert "denied_path" in reason

    def test_allowed_paths_restriction(self):
        scope = _make_scope(allowed_paths=["src/"])
        allowed, _ = self.normalizer.apply_path_filter("src/main.py", scope)
        assert allowed is True
        allowed2, reason2 = self.normalizer.apply_path_filter("tests/test_foo.py", scope)
        assert allowed2 is False
        assert "not_in_allowed_paths" in reason2

    def test_denied_overrides_allowed(self):
        scope = _make_scope(allowed_paths=["src/"], denied_paths=["src/secret/"])
        allowed, reason = self.normalizer.apply_path_filter("src/secret/key.py", scope)
        assert allowed is False

    def test_windows_paths_normalized(self):
        scope = _make_scope()
        allowed, _ = self.normalizer.apply_path_filter("src\\main.py", scope)
        assert allowed is True


# ── truncate_snippet ──────────────────────────────────────────────────────────

class TestTruncateSnippet:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()

    def test_short_snippet_unchanged(self):
        snippet, was_truncated = self.normalizer.truncate_snippet("short")
        assert snippet == "short"
        assert was_truncated is False

    def test_long_snippet_truncated(self):
        long_snippet = "x" * (MAX_SNIPPET_CHARS + 100)
        snippet, was_truncated = self.normalizer.truncate_snippet(long_snippet)
        assert len(snippet) == MAX_SNIPPET_CHARS
        assert was_truncated is True

    def test_exactly_max_length_not_truncated(self):
        exact = "a" * MAX_SNIPPET_CHARS
        snippet, was_truncated = self.normalizer.truncate_snippet(exact)
        assert was_truncated is False

    def test_custom_max_chars(self):
        snippet, was_truncated = self.normalizer.truncate_snippet("hello world", max_chars=5)
        assert snippet == "hello"
        assert was_truncated is True


# ── redact_sensitive_content ──────────────────────────────────────────────────

class TestRedactSensitiveContent:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()

    def test_clean_content_unchanged(self):
        snippet, was_redacted = self.normalizer.redact_sensitive_content("def foo(): return 42")
        assert was_redacted is False
        assert snippet == "def foo(): return 42"

    def test_openai_key_redacted(self):
        snippet = "api_key = sk-abcdefghijklmnopqrstuvwxyz"
        result, was_redacted = self.normalizer.redact_sensitive_content(snippet)
        assert was_redacted is True
        assert "[REDACTED]" in result

    def test_aws_key_redacted(self):
        snippet = "AKIAIOSFODNN7EXAMPLE"
        result, was_redacted = self.normalizer.redact_sensitive_content(snippet)
        assert was_redacted is True

    def test_github_pat_redacted(self):
        snippet = "token = ghp_" + "A" * 36
        result, was_redacted = self.normalizer.redact_sensitive_content(snippet)
        assert was_redacted is True

    def test_password_key_value_redacted(self):
        snippet = "password=hunter2"
        result, was_redacted = self.normalizer.redact_sensitive_content(snippet)
        assert was_redacted is True

    def test_slack_token_redacted(self):
        snippet = "xoxb-12345-abcdef"
        result, was_redacted = self.normalizer.redact_sensitive_content(snippet)
        assert was_redacted is True


# ── build_item_id ─────────────────────────────────────────────────────────────

class TestBuildItemId:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()

    def test_deterministic(self):
        id1 = self.normalizer.build_item_id("fake", "src/main.py", 10)
        id2 = self.normalizer.build_item_id("fake", "src/main.py", 10)
        assert id1 == id2

    def test_different_providers_differ(self):
        id1 = self.normalizer.build_item_id("fake", "src/main.py", 10)
        id2 = self.normalizer.build_item_id("augment", "src/main.py", 10)
        assert id1 != id2

    def test_different_paths_differ(self):
        id1 = self.normalizer.build_item_id("fake", "src/a.py", 1)
        id2 = self.normalizer.build_item_id("fake", "src/b.py", 1)
        assert id1 != id2

    def test_length_32_chars(self):
        item_id = self.normalizer.build_item_id("fake", "src/main.py", None)
        assert len(item_id) == 32


# ── deduplicate ───────────────────────────────────────────────────────────────

class TestDeduplicate:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()

    def _make_item(self, item_id: str, provider: str, path: str, line_start: int | None, score: float) -> ContextItem:
        return ContextItem(
            item_id=item_id, provider=provider, path=path, symbol=None,
            line_start=line_start, line_end=None, snippet="code",
            score=score, reason="", source_kind="keyword",
            redaction_state="clean", warnings=[], correlation_id=None,
        )

    def test_deduplicates_same_path_line(self):
        a = self._make_item("id1", "fake", "src/a.py", 1, 0.5)
        b = self._make_item("id2", "fake", "src/a.py", 1, 0.9)
        result = self.normalizer.deduplicate([a, b])
        assert len(result) == 1
        assert result[0].score == 0.9

    def test_keeps_different_paths(self):
        a = self._make_item("id1", "fake", "src/a.py", 1, 0.5)
        b = self._make_item("id2", "fake", "src/b.py", 1, 0.5)
        result = self.normalizer.deduplicate([a, b])
        assert len(result) == 2

    def test_empty_list(self):
        assert self.normalizer.deduplicate([]) == []


# ── sort_by_score ─────────────────────────────────────────────────────────────

class TestSortByScore:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()

    def _make_item(self, score: float) -> ContextItem:
        return ContextItem(
            item_id="id", provider="fake", path="a.py", symbol=None,
            line_start=1, line_end=None, snippet="code",
            score=score, reason="", source_kind="keyword",
            redaction_state="clean", warnings=[], correlation_id=None,
        )

    def test_descending_order(self):
        items = [self._make_item(0.3), self._make_item(0.9), self._make_item(0.6)]
        sorted_items = self.normalizer.sort_by_score(items)
        scores = [i.score for i in sorted_items]
        assert scores == [0.9, 0.6, 0.3]

    def test_empty_list(self):
        assert self.normalizer.sort_by_score([]) == []

    def test_single_item(self):
        items = [self._make_item(0.5)]
        result = self.normalizer.sort_by_score(items)
        assert result[0].score == 0.5


# ── _safe_int ─────────────────────────────────────────────────────────────────

class TestSafeInt:
    def test_int_value(self):
        assert _safe_int(10) == 10

    def test_string_int(self):
        assert _safe_int("42") == 42

    def test_none_returns_none(self):
        assert _safe_int(None) is None

    def test_invalid_string_returns_none(self):
        assert _safe_int("abc") is None

    def test_float_converts(self):
        assert _safe_int(3.7) == 3


# ── redaction_state in normalized items ───────────────────────────────────────

class TestRedactionStateInNormalizedItems:
    def setup_method(self):
        self.normalizer = ContextItemNormalizer()
        self.scope = _make_scope()

    def test_clean_state_for_normal_snippet(self):
        raw = _make_raw(snippet="def foo(): return 42")
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.redaction_state == "clean"
        assert item.warnings == []

    def test_truncated_state_for_long_snippet(self):
        raw = _make_raw(snippet="x" * (MAX_SNIPPET_CHARS + 100))
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.redaction_state == "truncated"
        assert "snippet_truncated" in item.warnings

    def test_redacted_state_for_secret(self):
        raw = _make_raw(snippet="password=hunter2")
        item = self.normalizer.normalize_item(raw, provider="fake", query="test", scope=self.scope)
        assert item is not None
        assert item.redaction_state == "redacted"
        assert "content_redacted" in item.warnings
