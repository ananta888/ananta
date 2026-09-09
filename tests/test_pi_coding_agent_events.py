"""Minimal deterministic protocol examples; no live session dumps or identities."""

import copy
import json
from pathlib import Path

import pytest

from agent.cli_backends.pi_events import PiProtocolError, parse_pi_one_shot


def events():
    user = {"role": "user", "content": [{"type": "text", "text": "Explain this code."}]}
    assistant = {
        "role": "assistant", "content": [{"type": "text", "text": "A\u2028B"}],
        "api": "openai-completions", "provider": "ananta", "model": "selected-model", "stopReason": "stop",
    }
    return [
        {"type": "session", "version": 3, "cwd": "/workspace"},
        {"type": "agent_start"}, {"type": "turn_start"},
        {"type": "message_start", "message": {"role": "user"}},
        {"type": "message_end", "message": copy.deepcopy(user)},
        {"type": "message_start", "message": {"role": "assistant"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "not authoritative"}},
        {"type": "message_end", "message": copy.deepcopy(assistant)},
        {"type": "turn_end", "message": copy.deepcopy(assistant), "toolResults": []},
        {"type": "agent_end", "messages": [copy.deepcopy(user), copy.deepcopy(assistant)], "willRetry": False},
        {"type": "agent_settled"},
    ]


def parse(records):
    return parse_pi_one_shot(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in records),
        provider="ananta", model="selected-model", workspace=Path("/workspace"),
    )


def test_pi_accepts_complete_no_tools_lifecycle_and_authoritative_text():
    assert parse(events()) == "A\u2028B"


@pytest.mark.parametrize("index", range(11))
def test_pi_rejects_missing_lifecycle_event(index):
    records = events()
    records.pop(index)
    if index == 6:  # Streaming deltas are optional, never the authoritative result.
        assert parse(records) == "A\u2028B"
    else:
        with pytest.raises(PiProtocolError):
            parse(records)


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5, 7, 8, 9, 10])
def test_pi_rejects_duplicate_lifecycle_event(index):
    records = events()
    records.insert(index, copy.deepcopy(records[index]))
    with pytest.raises(PiProtocolError):
        parse(records)


@pytest.mark.parametrize("index", [8, 9])
def test_pi_rejects_conflicting_terminal_message(index):
    records = events()
    message = records[index]["message"] if index == 8 else records[index]["messages"][-1]
    message["content"][0]["text"] = "different"
    with pytest.raises(PiProtocolError, match="^pi_terminal_conflict$"):
        parse(records)


@pytest.mark.parametrize("reason", ["length", "error", "aborted", "toolUse", None])
def test_pi_rejects_incomplete_or_failed_assistant(reason):
    records = events()
    records[7]["message"]["stopReason"] = reason
    with pytest.raises(PiProtocolError, match="^pi_assistant_failed$"):
        parse(records)


@pytest.mark.parametrize("field", ["provider", "model", "api"])
def test_pi_rejects_model_or_provider_substitution(field):
    records = events()
    records[7]["message"][field] = "unselected"
    with pytest.raises(PiProtocolError, match="^pi_model_binding_mismatch$"):
        parse(records)


@pytest.mark.parametrize("event", [
    {"type": "tool_execution_start"},
    {"type": "message_update", "assistantMessageEvent": {"type": "toolcall_start"}},
])
def test_pi_rejects_tool_execution_in_no_tools_profile(event):
    records = events()
    records[6] = event
    with pytest.raises(PiProtocolError, match="^pi_tool_execution_denied$"):
        parse(records)


@pytest.mark.parametrize("raw", [
    '[]', '{"type":"session","type":"agent_settled"}', '{"value":NaN}', '{broken',
    '{"type":[]}', '[' * 2000 + '0' + ']' * 2000,
])
def test_pi_rejects_malformed_json_with_closed_diagnostic(raw):
    with pytest.raises(PiProtocolError, match="^pi_event_contract_invalid$"):
        parse_pi_one_shot(raw, provider="ananta", model="selected-model", workspace=Path("/workspace"))


def test_pi_rejects_retry_after_apparently_successful_answer():
    records = events()
    records[9]["willRetry"] = True
    with pytest.raises(PiProtocolError, match="^pi_retry_not_authorized$"):
        parse(records)


def test_pi_rejects_workspace_substitution():
    records = events()
    records[0]["cwd"] = "/other-task"
    with pytest.raises(PiProtocolError, match="^pi_workspace_binding_mismatch$"):
        parse(records)


@pytest.mark.parametrize("failure", [None, "reason", "message", "duplicate"])
def test_pi_checks_streaming_terminal_against_authoritative_completion(failure):
    records = events()
    update = {"type": "done", "reason": "stop", "message": copy.deepcopy(records[7]["message"])}
    records[6]["assistantMessageEvent"] = update
    if failure == "reason":
        update["reason"] = "length"
    elif failure == "message":
        update["message"]["content"][0]["text"] = "contradictory completion"
    elif failure == "duplicate":
        records.insert(6, copy.deepcopy(records[6]))
    if failure is None:
        assert parse(records) == "A\u2028B"
    else:
        with pytest.raises(PiProtocolError):
            parse(records)
