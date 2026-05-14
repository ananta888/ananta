from agent.services.tool_intent_resolver import ToolIntentResolver


def test_unknown_text_payload_not_mapped_to_shell():
    result = ToolIntentResolver().resolve(
        [{"name": "mystery_tool", "args": {"text": "hello"}}],
        known_tools=["file_read", "file_write", "shell_execute"],
    )
    assert result.resolved_tool_calls == []
    assert any(item.reason_code == "unknown_tool" for item in result.unresolved)


def test_path_content_maps_to_file_write():
    result = ToolIntentResolver().resolve(
        [{"name": "mystery_tool", "args": {"path": "README.md", "content": "ok"}}],
        known_tools=["file_read", "file_write", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "README.md"


def test_unknown_command_payload_requires_explicit_shell():
    result = ToolIntentResolver().resolve(
        [{"name": "mystery_tool", "args": {"command": "ls -la"}}],
        known_tools=["file_read", "file_write", "shell_execute"],
    )
    assert result.resolved_tool_calls == []
    assert any(item.reason_code == "unknown_tool_command_payload_requires_explicit_shell" for item in result.unresolved)


def test_function_name_and_arguments_aliases_are_supported():
    result = ToolIntentResolver().resolve(
        [{"function_name": "file_write", "arguments": {"path": "README.md", "content": "hello"}}],
        known_tools=["file_read", "file_write", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "README.md"
