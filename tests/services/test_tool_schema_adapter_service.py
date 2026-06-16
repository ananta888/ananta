"""UTCR-010: Tests for ToolSchemaAdapterService."""
from __future__ import annotations

import pytest

from agent.services.ananta_tool_registry_service import AnantaToolRegistryService
from agent.services.tool_schema_adapter_service import ToolSchemaAdapterService


@pytest.fixture()
def adapter() -> ToolSchemaAdapterService:
    return ToolSchemaAdapterService()


@pytest.fixture()
def registry() -> AnantaToolRegistryService:
    return AnantaToolRegistryService()


def test_get_prompt_description_same_as_registry(adapter, registry):
    assert adapter.get_prompt_description() == registry.describe_for_prompt()


def test_get_openai_tools_returns_valid_schemas(adapter):
    tools = adapter.get_openai_tools()
    assert isinstance(tools, list)
    assert len(tools) > 0
    for t in tools:
        assert t.get("type") == "function"
        assert "function" in t
        assert "name" in t["function"]


def test_get_debug_snapshot_codecompass_static_read(adapter):
    snap = adapter.get_debug_snapshot()
    assert snap["schema"] == "ananta_tool_schema_debug.v1"
    cc_tools = [t for t in snap["tools"] if t["name"].startswith("codecompass.")]
    assert len(cc_tools) > 0
    for t in cc_tools:
        assert t["source"] == "static"
        assert t["risk_class"] == "read"


def test_allowed_tools_filter(adapter):
    tools = adapter.get_openai_tools(allowed_tools=["codecompass.search"])
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "codecompass.search"


def test_no_duplicates_in_adapter(adapter):
    tools = adapter.get_openai_tools()
    names = [t["function"]["name"] for t in tools]
    assert len(names) == len(set(names))


def test_debug_snapshot_no_blocked_tools(adapter):
    """Blocked tools must not appear in the debug snapshot."""
    from agent.services.ananta_tool_registry_service import CATEGORY_BLOCKED, AnantaToolRegistryService

    blocked_names = {spec.name for spec in AnantaToolRegistryService().list_tools() if spec.category == CATEGORY_BLOCKED}
    snap = adapter.get_debug_snapshot()
    returned = {t["name"] for t in snap["tools"]}
    assert not (blocked_names & returned), f"Blocked tools in snapshot: {blocked_names & returned}"


def test_debug_snapshot_argument_properties_sorted(adapter):
    snap = adapter.get_debug_snapshot()
    for t in snap["tools"]:
        props = t.get("argument_properties", [])
        assert props == sorted(props), f"{t['name']}: argument_properties not sorted"
