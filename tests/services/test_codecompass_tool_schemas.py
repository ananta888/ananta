"""UTCR-010 main acceptance test: all 8 codecompass tools in both formats.

For each of the 8 codecompass tools, asserts:
- Appears in describe_for_openai_tools()
- Has exactly type="function"
- Has the correct function.parameters.properties keys
- Appears in describe_for_prompt()
- Prompt description and native schema produced from same registry
"""
from __future__ import annotations

import pytest

from agent.services.ananta_tool_registry_service import AnantaToolRegistryService


_CODECOMPASS_SPECS: dict[str, set[str]] = {
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
    "codecompass.semantic_equivalents": {"symbol", "file", "language", "target_languages", "semantic_kind"},
    "codecompass.translation_plan": {"source_path", "source_code", "target_language", "allowed_rule_ids"},
    "codecompass.verify_translation": {"source_path", "source_code", "target_code", "transform_artifact"},
}


@pytest.fixture(scope="module")
def registry() -> AnantaToolRegistryService:
    return AnantaToolRegistryService()


@pytest.fixture(scope="module")
def native_tools(registry) -> list[dict]:
    return registry.describe_for_openai_tools()


@pytest.fixture(scope="module")
def prompt_text(registry) -> str:
    return registry.describe_for_prompt()


def _find_native(tools: list[dict], name: str) -> dict | None:
    for t in tools:
        if t.get("function", {}).get("name") == name:
            return t
    return None


@pytest.mark.parametrize("tool_name", list(_CODECOMPASS_SPECS.keys()))
class TestCodecompassToolSchemas:
    def test_appears_in_native_tools(self, tool_name, native_tools):
        entry = _find_native(native_tools, tool_name)
        assert entry is not None, f"{tool_name} missing from describe_for_openai_tools()"

    def test_has_type_function(self, tool_name, native_tools):
        entry = _find_native(native_tools, tool_name)
        assert entry is not None
        assert entry["type"] == "function", f"{tool_name}: type is not 'function'"

    def test_correct_properties(self, tool_name, native_tools):
        entry = _find_native(native_tools, tool_name)
        assert entry is not None
        actual = set(entry["function"]["parameters"]["properties"].keys())
        expected = _CODECOMPASS_SPECS[tool_name]
        assert actual == expected, (
            f"{tool_name} property mismatch\n  expected: {expected}\n  actual:   {actual}"
        )

    def test_appears_in_prompt_description(self, tool_name, prompt_text):
        # The prompt format is: - `tool_name` (risk): description
        assert f"`{tool_name}`" in prompt_text, f"{tool_name} missing from describe_for_prompt()"

    def test_same_registry_source(self, tool_name, registry, native_tools, prompt_text):
        """Native schema name and prompt name come from the same spec."""
        spec = registry.get_tool(tool_name)
        assert spec is not None, f"spec for {tool_name} not found in registry"
        # Verify native schema name matches spec name
        entry = _find_native(native_tools, tool_name)
        assert entry is not None
        assert entry["function"]["name"] == spec.name
        # Verify prompt includes spec name
        assert f"`{spec.name}`" in prompt_text
