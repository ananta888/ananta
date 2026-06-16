"""UTCR-010: Tests for AnantaToolRegistryService.describe_for_openai_tools()."""
from __future__ import annotations

import pytest

from agent.services.ananta_tool_registry_service import (
    CATEGORY_BLOCKED,
    AnantaToolRegistryService,
)


@pytest.fixture()
def svc() -> AnantaToolRegistryService:
    return AnantaToolRegistryService()


def _openai_tools(svc: AnantaToolRegistryService, allowed=None):
    return svc.describe_for_openai_tools(allowed)


def _tool_by_name(tools: list, name: str) -> dict | None:
    for t in tools:
        if t.get("function", {}).get("name") == name:
            return t
    return None


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_codecompass_plan_context_present(svc):
    tools = _openai_tools(svc)
    entry = _tool_by_name(tools, "codecompass.plan_context")
    assert entry is not None, "codecompass.plan_context must appear in describe_for_openai_tools()"
    assert entry["type"] == "function"


def test_all_entries_have_type_function(svc):
    tools = _openai_tools(svc)
    for t in tools:
        assert t.get("type") == "function", f"tool {t} missing type=function"
        assert "function" in t
        assert "name" in t["function"]


# ---------------------------------------------------------------------------
# BLOCKED tools never appear
# ---------------------------------------------------------------------------

def test_blocked_tools_never_appear(svc):
    blocked_names = {spec.name for spec in svc.list_tools() if spec.category == CATEGORY_BLOCKED}
    tools = _openai_tools(svc)
    returned_names = {t["function"]["name"] for t in tools}
    overlap = blocked_names & returned_names
    assert not overlap, f"Blocked tools appeared in schema: {overlap}"


# ---------------------------------------------------------------------------
# allowed_tools filter
# ---------------------------------------------------------------------------

def test_allowed_tools_filter(svc):
    tools = _openai_tools(svc, allowed=["codecompass.search"])
    names = [t["function"]["name"] for t in tools]
    assert names == ["codecompass.search"]


def test_empty_allowed_returns_all(svc):
    tools_unfiltered = _openai_tools(svc, allowed=None)
    tools_empty = _openai_tools(svc, allowed=[])
    assert len(tools_unfiltered) == len(tools_empty)


# ---------------------------------------------------------------------------
# No duplicates
# ---------------------------------------------------------------------------

def test_no_duplicates(svc):
    tools = _openai_tools(svc)
    names = [t["function"]["name"] for t in tools]
    assert len(names) == len(set(names)), f"Duplicate tool names: {sorted(names)}"


# ---------------------------------------------------------------------------
# Prompt description and native schema from same registry
# ---------------------------------------------------------------------------

def test_prompt_and_native_same_registry(svc):
    """Names in describe_for_prompt() and describe_for_openai_tools() must match."""
    prompt_text = svc.describe_for_prompt()
    native_tools = _openai_tools(svc)
    native_names = {t["function"]["name"] for t in native_tools}
    for line in prompt_text.splitlines():
        if line.startswith("- `"):
            name = line.split("`")[1]
            assert name in native_names, f"{name} in prompt but not in native schema"


# ---------------------------------------------------------------------------
# All 8 codecompass tools present with correct property names
# ---------------------------------------------------------------------------

_CODECOMPASS_TOOL_PROPERTIES: dict[str, set[str]] = {
    "codecompass.plan_context": {"query", "max_ranges", "include_neighbors", "task_kind"},
    "codecompass.resolve_context": {
        "query", "task_kind", "mode", "working_files", "domain_hint",
        "domain_scope", "max_tokens", "max_files", "include_original_files",
        "include_jsonl_records", "include_graph", "llm_scope",
    },
    "codecompass.search": {"query", "limit"},
    "codecompass.search_symbols": {"query", "record_kinds", "path_globs", "domain_hint", "limit"},
    "codecompass.expand_graph": {"node", "seeds", "depth", "max_depth", "limit", "max_nodes"},
    "codecompass.get_file_context": {
        "paths", "line_ranges", "max_bytes_per_file", "max_total_bytes",
        "redaction_mode", "reason",
    },
    "codecompass.get_domain_map": {"domain_hint", "include_files", "include_edges", "max_entries"},
    "codecompass.architecture_query": {"question"},
}


@pytest.mark.parametrize("tool_name,expected_props", list(_CODECOMPASS_TOOL_PROPERTIES.items()))
def test_codecompass_tool_properties(svc, tool_name, expected_props):
    tools = _openai_tools(svc)
    entry = _tool_by_name(tools, tool_name)
    assert entry is not None, f"{tool_name} not found in describe_for_openai_tools()"
    assert entry["type"] == "function"
    actual_props = set(entry["function"]["parameters"]["properties"].keys())
    assert actual_props == expected_props, (
        f"{tool_name}: property mismatch\n  expected: {expected_props}\n  actual:   {actual_props}"
    )
