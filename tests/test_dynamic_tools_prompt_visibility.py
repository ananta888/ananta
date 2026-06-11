"""HDE-013: dynamic tools in prompt descriptions and the direct router."""
import pytest

from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service
from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService
from agent.services.hub_direct_execution_router import HubDirectExecutionRouter


def _spec(name="custom.count_todos", **overrides):
    spec = {
        "name": name,
        "description": "Count TODO markers",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execution_kind": "command_template",
        "command_template": ["grep", "-rc", "TODO", "{path}"],
        "intent_aliases": ["zähle todos"],
    }
    spec.update(overrides)
    return spec


@pytest.fixture
def dynamic_registry(tmp_path, monkeypatch):
    registry = DynamicToolRegistryService(tmp_path)
    import agent.services.dynamic_tool_registry_service as registry_module

    monkeypatch.setattr(registry_module, "dynamic_tool_registry_service", registry)
    return registry


def _activate(registry, name="custom.count_todos", **spec_overrides):
    registry.store_promoted_tool(
        name=name,
        spec=_spec(name, **spec_overrides),
        proposal_digest="d1",
        validated_digest="d1",
        validation_report_ref=None,
        approval_status="granted",
    )


def test_describe_for_prompt_without_dynamic_is_unchanged(dynamic_registry):
    _activate(dynamic_registry)
    text = get_ananta_tool_registry_service().describe_for_prompt()
    assert "custom.count_todos" not in text


def test_describe_for_prompt_includes_active_dynamic_tools(dynamic_registry):
    _activate(dynamic_registry)
    text = get_ananta_tool_registry_service().describe_for_prompt(include_dynamic=True)
    assert "custom.count_todos" in text
    assert "Count TODO markers" in text
    # No script internals leak into the prompt line of the dynamic tool:
    line = next(row for row in text.splitlines() if "custom.count_todos" in row)
    assert "-rc" not in line
    assert "command_template" not in line


def test_allowed_tools_filters_dynamic_tools(dynamic_registry):
    _activate(dynamic_registry)
    text = get_ananta_tool_registry_service().describe_for_prompt(
        allowed_tools=["repo.grep"], include_dynamic=True
    )
    assert "custom.count_todos" not in text


def test_disabled_dynamic_tool_is_not_described(dynamic_registry):
    _activate(dynamic_registry)
    dynamic_registry.set_status("custom.count_todos", "disabled")
    text = get_ananta_tool_registry_service().describe_for_prompt(include_dynamic=True)
    assert "custom.count_todos" not in text


def test_registry_snapshot_marks_sources(dynamic_registry):
    _activate(dynamic_registry)
    snapshot = get_ananta_tool_registry_service().registry_snapshot(include_dynamic=True)
    sources = {row["name"]: row["source"] for row in snapshot["tools"]}
    assert sources["repo.grep"] == "static"
    assert sources["custom.count_todos"] == "dynamic"


def test_router_only_selects_dynamic_tools_via_alias(dynamic_registry):
    _activate(dynamic_registry)
    router = HubDirectExecutionRouter(dynamic_registry=dynamic_registry)
    cfg = {"hub_direct_execution": {"enabled": True, "allowed_tools": [], "confidence_threshold": 0.8}}
    assert router.classify("zähle todos", agent_cfg=cfg).eligible
    assert router.classify("zähle bitte alle todos im projekt", agent_cfg=cfg).eligible is False
