"""Closed response budgets propagate to Ollama; malformed usage fails closed."""

import json
from unittest.mock import MagicMock, Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_media_transport import validate_response_budget, validate_result
from tests.test_meet_media import result, turn
from worker.meet_media import llm
from worker.meet_media.contract import validate_turn

pytestmark = pytest.mark.timeout(30)


@pytest.mark.parametrize(
    "limits",
    [
        None,
        {},
        {"max_output_tokens": 1},
        {"max_output_tokens": True, "max_reply_chars": 100},
        {"max_output_tokens": 129, "max_reply_chars": 100},
        {"max_output_tokens": 1, "max_reply_chars": 451},
        {"max_output_tokens": 1, "max_reply_chars": 0},
        {"max_output_tokens": 1, "max_reply_chars": 10, "tools": True},
    ],
)
def test_invalid_response_limits(limits):
    envelope = turn() | {"response_limits": limits}
    with pytest.raises(ValueError, match="response_limits"):
        validate_turn(envelope, envelope["deadline"] - 100)


def test_closed_limits_are_additive_to_existing_turns():
    envelope = turn()
    assert validate_turn(envelope, envelope["deadline"] - 100)
    assert validate_turn(
        envelope | {"response_limits": {"max_output_tokens": 1, "max_reply_chars": 1}}, envelope["deadline"] - 100
    )


def opener(monkeypatch, **changes):
    payload = {
        "message": {"content": "Eine ausreichend lange Antwort."},
        "done": True,
        "prompt_eval_count": 50,
        "eval_count": 8,
    } | changes
    models = {
        "models": [
            {
                "name": "qwen2.5:1.5b",
                "size_vram": 100,
                "digest": "65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b",
            }
        ]
    }
    responses = []
    for value in (payload, models):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(value).encode()
        responses.append(response)
    transport = Mock()
    transport.open.side_effect = responses
    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *_: transport)
    monkeypatch.delenv("MEET_LLM_MODEL", raising=False)
    monkeypatch.delenv("MEET_LLM_DIGEST", raising=False)
    return transport


@pytest.mark.parametrize("characters", [1, 10, 450])
def test_ollama_receives_exact_token_limit_and_bounded_text(monkeypatch, characters):
    transport = opener(monkeypatch)
    generated = llm.generate("untrusted room content", max_output_tokens=8, max_reply_chars=characters)
    request = transport.open.call_args_list[0].args[0]
    body = json.loads(request.data)
    assert body["options"]["num_predict"] == 8
    assert body["messages"][1] == {"role": "user", "content": "untrusted room content"}
    assert "tools" not in body
    assert 0 < len(generated.text) <= characters
    assert generated.output_tokens == 8 and generated.input_tokens == 50
    assert generated.text not in repr(generated)


@pytest.mark.parametrize(
    "changes",
    [
        {"eval_count": None},
        {"eval_count": True},
        {"eval_count": 9},
        {"eval_count": 0},
        {"prompt_eval_count": 2049},
        {"prompt_eval_count": 0},
        {"prompt_eval_count": 1.5},
    ],
)
def test_unusable_or_over_budget_usage_is_not_a_success(monkeypatch, changes):
    opener(monkeypatch, **changes)
    with pytest.raises(ValueError, match="usage_invalid"):
        llm.generate("hello", max_output_tokens=8)


def test_hub_rejects_missing_or_excess_worker_usage():
    limits = {"max_output_tokens": 8, "max_reply_chars": 10}
    request = turn() | {"response_limits": limits}
    response = result() | {"usage": {"input_tokens": 50, "output_tokens": 8}}
    validate_result(response)
    validate_response_budget(request, response)
    for invalid in (
        result(),
        response | {"text": "x" * 11},
        response | {"usage": {"input_tokens": 50, "output_tokens": 9}},
    ):
        with pytest.raises(MeetError):
            validate_response_budget(request, invalid)
    with pytest.raises(MeetError, match="usage_mismatch"):
        validate_response_budget(turn(), response)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 20, "output_tokens": True},
        {"input_tokens": 2049, "output_tokens": 8},
        {"input_tokens": 20, "output_tokens": 129},
        {"input_tokens": 20, "output_tokens": 8, "cost": 0},
    ],
)
def test_closed_worker_usage(usage):
    with pytest.raises(MeetError, match="usage_invalid"):
        validate_result(result() | {"usage": usage})
