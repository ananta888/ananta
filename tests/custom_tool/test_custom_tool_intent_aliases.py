"""HDE-014: intent aliases, negative examples and usage metadata."""
import pytest

from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService
from agent.services.hub_direct_execution_router import HubDirectExecutionRouter


def _spec(**overrides):
    spec = {
        "name": "custom.count_todos",
        "description": "Count TODO markers",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execution_kind": "command_template",
        "command_template": ["grep", "-rc", "TODO", "{path}"],
        "intent_aliases": ["zähle todos", {"alias": "count todos in src", "arguments": {"path": "src"}}],
        "example_prompts": ["zähle todos"],
        "negative_examples": ["lösche todos"],
        "confidence_hint": 0.9,
    }
    spec.update(overrides)
    return spec


@pytest.fixture
def registry(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    registry.store_promoted_tool(
        name="custom.count_todos",
        spec=_spec(),
        proposal_digest="d1",
        validated_digest="d1",
        validation_report_ref=None,
        approval_status="granted",
    )
    return registry


def _cfg():
    return {
        "hub_direct_execution": {
            "enabled": True,
            "allowed_tools": [],
            "confidence_threshold": 0.8,
        }
    }


def test_exact_alias_matches(registry):
    record = registry.match_intent_alias("zähle todos")
    assert record is not None
    assert record["alias_arguments"] == {}


def test_alias_object_carries_arguments(registry):
    record = registry.match_intent_alias("count todos in src")
    assert record is not None
    assert record["alias_arguments"] == {"path": "src"}


def test_non_exact_prompt_does_not_match(registry):
    assert registry.match_intent_alias("bitte zähle todos und committe") is None
    assert registry.match_intent_alias("todos") is None


def test_router_uses_alias_and_negative_examples(registry):
    router = HubDirectExecutionRouter(dynamic_registry=registry)
    decision = router.classify("Zähle TODOs", agent_cfg=_cfg())
    assert decision.eligible
    assert decision.tool_name == "custom.count_todos"
    assert decision.source == "dynamic"
    assert decision.confidence == 0.9

    blocked = router.classify("lösche todos", agent_cfg=_cfg())
    assert blocked.eligible is False


def test_disabled_tool_alias_never_matches(registry):
    registry.set_status("custom.count_todos", "disabled")
    assert registry.match_intent_alias("zähle todos") is None


def test_usage_metadata_updates(registry):
    registry.record_usage("custom.count_todos", success=True)
    registry.record_usage("custom.count_todos", success=False, failure_reason="exit_code_2")
    usage = registry.get_record("custom.count_todos")["usage"]
    assert usage["success_count"] == 1
    assert usage["fail_count"] == 1
    assert usage["last_failure_reason"] == "exit_code_2"
    assert usage["last_used"] is not None
