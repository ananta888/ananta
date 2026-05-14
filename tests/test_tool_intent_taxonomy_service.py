from __future__ import annotations

from agent.services.tool_intent_taxonomy_service import get_tool_intent_taxonomy_service


def test_tool_intent_taxonomy_contains_shell_and_file_write() -> None:
    svc = get_tool_intent_taxonomy_service()
    defs = svc.all_definitions()
    intents = {item["intent"] for item in defs}
    assert "shell_command" in intents
    assert "file_write" in intents


def test_tool_intent_taxonomy_classifies_known_and_unknown_tools() -> None:
    svc = get_tool_intent_taxonomy_service()
    shell = svc.classify_tool("bash")
    unknown = svc.classify_tool("mystery_tool")
    assert shell["intent"] == "shell_command"
    assert shell["tool_class"] == "admin"
    assert unknown["intent"] == "unknown"

