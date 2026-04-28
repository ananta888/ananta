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
