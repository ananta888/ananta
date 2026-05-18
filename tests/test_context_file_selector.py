from __future__ import annotations

import pytest

from agent.services.context_file_selector import (
    ContextFileSelector,
    provider_to_llm_scope,
)
from agent.services.workspace_context_policy import WorkspaceContextPolicy


def _make_chunk(path: str, sensitivity: str = "public", rank: int = 100) -> dict:
    return {"path": path, "sensitivity": sensitivity, "score": rank}


@pytest.fixture
def selector():
    return ContextFileSelector()


@pytest.fixture
def full_policy():
    return WorkspaceContextPolicy(scope_mode="full", max_files=200, sensitivity_ceiling="security_sensitive")


class TestContextFileSelector:
    def test_sensitivity_blocks_secret_for_external_provider(self, selector, full_policy):
        chunks = [_make_chunk("agent/config.py", sensitivity="secret")]
        result = selector.select(chunks, full_policy, "external_cloud_allowed")
        assert "agent/config.py" in result.excluded_paths
        assert result.exclusion_reasons["agent/config.py"] == "sensitivity_blocked"
        assert "agent/config.py" not in result.selected_paths

    def test_local_provider_allows_secret(self, selector, full_policy):
        chunks = [_make_chunk("agent/config.py", sensitivity="secret")]
        result = selector.select(chunks, full_policy, "local_only")
        assert "agent/config.py" in result.selected_paths

    def test_external_cloud_blocks_internal_medium(self, selector, full_policy):
        chunks = [_make_chunk("agent/internal.py", sensitivity="internal_medium")]
        result = selector.select(chunks, full_policy, "external_cloud_allowed")
        assert "agent/internal.py" in result.excluded_paths

    def test_external_cloud_allows_internal_low(self, selector, full_policy):
        chunks = [_make_chunk("agent/pub.py", sensitivity="internal_low")]
        result = selector.select(chunks, full_policy, "external_cloud_allowed")
        assert "agent/pub.py" in result.selected_paths

    def test_max_files_truncates_to_highest_ranked(self, selector):
        policy = WorkspaceContextPolicy(scope_mode="selective", max_files=3, sensitivity_ceiling="confidential")
        chunks = [_make_chunk(f"file{i}.py", rank=100 - i) for i in range(10)]
        result = selector.select(chunks, policy, "local_only")
        assert len(result.selected_paths) == 3

    def test_max_files_exceeded_reason(self, selector):
        policy = WorkspaceContextPolicy(scope_mode="selective", max_files=2, sensitivity_ceiling="confidential")
        chunks = [_make_chunk(f"file{i}.py") for i in range(5)]
        result = selector.select(chunks, policy, "local_only")
        for p in result.excluded_paths:
            if result.exclusion_reasons.get(p) == "max_files_exceeded":
                break
        else:
            pytest.fail("Expected at least one max_files_exceeded exclusion")

    def test_allowed_paths_glob_filters(self, selector):
        policy = WorkspaceContextPolicy(
            scope_mode="selective",
            allowed_paths=("agent/services/**",),
            max_files=200,
            sensitivity_ceiling="confidential",
        )
        chunks = [
            _make_chunk("agent/services/foo.py"),
            _make_chunk("agent/tools.py"),
            _make_chunk("tests/test_foo.py"),
        ]
        result = selector.select(chunks, policy, "local_only")
        assert "agent/services/foo.py" in result.selected_paths
        assert "agent/tools.py" in result.excluded_paths
        assert result.exclusion_reasons.get("agent/tools.py") == "path_not_allowed"

    def test_empty_chunks_returns_empty_selection(self, selector, full_policy):
        result = selector.select([], full_policy, "local_only")
        assert result.selected_paths == []
        assert result.excluded_paths == []
        assert result.total_chunks_evaluated == 0

    def test_exclusion_reasons_populated(self, selector):
        policy = WorkspaceContextPolicy(
            scope_mode="selective",
            allowed_paths=("agent/**",),
            max_files=1,
            sensitivity_ceiling="confidential",
        )
        chunks = [
            _make_chunk("agent/foo.py", sensitivity="secret"),
            _make_chunk("tests/bar.py"),
            _make_chunk("agent/baz.py"),
            _make_chunk("agent/qux.py"),
        ]
        result = selector.select(chunks, policy, "external_cloud_allowed")
        assert "agent/foo.py" in result.exclusion_reasons
        assert "tests/bar.py" in result.exclusion_reasons
        assert result.exclusion_reasons["tests/bar.py"] == "path_not_allowed"

    def test_selector_is_pure(self, selector, full_policy):
        chunks = [_make_chunk("agent/foo.py")]
        r1 = selector.select(chunks, full_policy, "local_only")
        r2 = selector.select(chunks, full_policy, "local_only")
        assert r1.selected_paths == r2.selected_paths

    def test_deduplication_of_same_path(self, selector, full_policy):
        chunks = [_make_chunk("agent/foo.py")] * 3
        result = selector.select(chunks, full_policy, "local_only")
        assert result.selected_paths.count("agent/foo.py") == 1

    def test_sensitivity_ceiling_blocks_confidential_when_ceiling_internal_high(self, selector):
        policy = WorkspaceContextPolicy(
            scope_mode="selective",
            max_files=200,
            sensitivity_ceiling="internal_medium",
        )
        chunks = [
            _make_chunk("f1.py", sensitivity="internal_low"),
            _make_chunk("f2.py", sensitivity="confidential"),
        ]
        result = selector.select(chunks, policy, "local_only")
        assert "f1.py" in result.selected_paths
        assert "f2.py" in result.excluded_paths
        assert result.exclusion_reasons["f2.py"] == "ceiling_exceeded"


class TestProviderToLlmScope:
    def test_ollama_is_local_only(self):
        assert provider_to_llm_scope("ollama", None) == "local_only"

    def test_lmstudio_is_local_only(self):
        assert provider_to_llm_scope("lmstudio", None) == "local_only"

    def test_localhost_url_is_local_only(self):
        assert provider_to_llm_scope("custom", "http://localhost:11434") == "local_only"

    def test_openai_is_external_cloud(self):
        assert provider_to_llm_scope("openai", None) == "external_cloud_allowed"

    def test_anthropic_is_external_cloud(self):
        assert provider_to_llm_scope("anthropic", "https://api.anthropic.com") == "external_cloud_allowed"

    def test_private_url_is_trusted_private(self):
        assert provider_to_llm_scope("custom", "http://internal.company.com/llm") == "trusted_private_cloud"
