from agent.common.utils.structured_action_utils import normalize_structured_action_payload, parse_structured_action_payload


def test_normalize_structured_action_payload_combines_command_with_args():
    payload = normalize_structured_action_payload(
        {
            "reason": "Run local command",
            "command": "cat",
            "args": ["/tmp/demo file.txt"],
            "tool_calls": [],
        }
    )

    assert payload is not None
    assert payload["command"] == "cat '/tmp/demo file.txt'"
    assert payload["tool_calls"] == []


def test_parse_structured_action_payload_fallback_recovers_command_with_args():
    malformed = """{
      "reason": "Inspect workspace file",
      "command": "cat",
      "args": ["/mnt/c/Users/pst/IdeaProjects/ananta/data/local-hub/worker-runtime/default/goal
-27dc44ac-local/AGENTS.md"],
      "tool_calls": []
    }"""

    payload = parse_structured_action_payload(malformed)

    assert payload is not None
    assert payload["command"] is not None
    assert payload["command"].startswith("cat ")
    assert "AGENTS.md" in payload["command"]


def test_placeholder_command_is_no_command():
    assert normalize_structured_action_payload({"command": "null", "tool_calls": []}) is None
    payload = normalize_structured_action_payload(
        {"command": "None", "tool_calls": [{"name": "file_write", "args": {"path": "result.md", "content": "x"}}]})
    assert payload["command"] is None and payload["tool_calls"][0]["name"] == "file_write"
    assert parse_structured_action_payload('{"command": "null", "reason": "nichts"}') is None
