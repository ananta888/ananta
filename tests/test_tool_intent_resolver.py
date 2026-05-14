from agent.services.tool_intent_resolver import ToolIntentResolver


def test_unknown_text_payload_maps_to_file_write_fallback():
    result = ToolIntentResolver().resolve(
        [{"name": "mystery_tool", "args": {"text": "hello"}}],
        known_tools=["file_read", "file_write", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "mystery_tool.md"
    assert result.remap_events
    assert result.remap_events[0].resolved_intent == "file_write"
    assert result.remap_events[0].tool_class == "write"


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


def test_heuristic_scope_tool_maps_to_file_write():
    result = ToolIntentResolver().resolve(
        [{"name": "process_project_scope", "args": {"scope_elements": ["Problem", "Zielgruppe"]}}],
        known_tools=["file_read", "file_write", "web_search", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "PROJECT_SCOPE.md"


def test_heuristic_google_tool_maps_to_web_search():
    result = ToolIntentResolver().resolve(
        [{"name": "google:tool_a", "args": {"query": "java angular fibonacci"}}],
        known_tools=["file_read", "file_write", "web_search", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "web_search"


def test_heuristic_generate_blueprint_maps_to_file_write():
    result = ToolIntentResolver().resolve(
        [{"name": "generate_blueprint", "args": {"language": "java", "frontend": "angular"}}],
        known_tools=["file_read", "file_write", "web_search", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "generate_blueprint.md"


def test_heuristic_architecture_designer_maps_to_file_write():
    result = ToolIntentResolver().resolve(
        [{"name": "architecture_designer", "args": {"goal": "Create architecture blueprint"}}],
        known_tools=["file_read", "file_write", "web_search", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "architecture_designer.md"


def test_heuristic_architecture_blueprint_uses_nested_args():
    result = ToolIntentResolver().resolve(
        [
            {
                "name": "create_architecture_blueprint",
                "args": {"args": {"scope": "backend+frontend", "security_assumptions": ["least privilege"]}},
            }
        ],
        known_tools=["file_read", "file_write", "web_search", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "create_architecture_blueprint.md"


def test_heuristic_declare_maps_to_file_write():
    result = ToolIntentResolver().resolve(
        [{"name": "declare", "args": {"backlog": ["task-a", "task-b"]}}],
        known_tools=["file_read", "file_write", "web_search", "shell_execute"],
    )
    assert len(result.resolved_tool_calls) == 1
    assert result.resolved_tool_calls[0]["name"] == "file_write"
    assert result.resolved_tool_calls[0]["args"]["path"] == "declare.md"
