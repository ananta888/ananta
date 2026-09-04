from __future__ import annotations

import pytest

from agent.services.local_runtime_response_policy import (
    LocalRuntimeResponsePolicy,
    LocalRuntimeResponsePolicyError,
)


def response(*, content: str, reasoning: str | None = None, tool_calls=None) -> dict:
    return {
        "choices": [{
            "message": {
                "content": content,
                "reasoning_content": reasoning,
                "tool_calls": tool_calls or [],
            }
        }]
    }


def test_qwen_policy_separates_and_discards_reasoning_content() -> None:
    policy = LocalRuntimeResponsePolicy()

    projected = policy.apply(
        response(content="<think>private chain</think>final answer"),
        policy_id="qwen3_reasoning_safe",
        tools_requested=False,
    )

    message = projected["choices"][0]["message"]
    assert message["content"] == "final answer"
    assert "reasoning_content" not in message
    observation = projected["metadata"]["reasoning_observation"]
    assert observation["present"] is True
    assert observation["persisted"] is False
    assert observation["authorization_input"] is False
    assert "private chain" not in str(projected)


def test_native_reasoning_is_redacted_without_changing_final_content() -> None:
    projected = LocalRuntimeResponsePolicy().apply(
        response(content="final", reasoning="native private chain"),
        policy_id="qwen3_reasoning_safe",
        tools_requested=False,
    )

    assert projected["choices"][0]["message"]["content"] == "final"
    assert "native private chain" not in str(projected)


@pytest.mark.parametrize(
    "payload,reason",
    [
        (
            response(content="<think>one</think>final", reasoning="two"),
            "local_runtime_reasoning_ambiguous",
        ),
        (
            response(content="before <think>nested</think> after"),
            "local_runtime_reasoning_markup_invalid",
        ),
        (
            response(content='<tool_call>{"name":"shell"}</tool_call>'),
            "local_runtime_unparsed_tool_markup",
        ),
    ],
)
def test_ambiguous_or_unparsed_markup_fails_closed(payload, reason) -> None:
    with pytest.raises(LocalRuntimeResponsePolicyError, match=reason):
        LocalRuntimeResponsePolicy().apply(
            payload,
            policy_id="qwen3_reasoning_safe",
            tools_requested=True,
        )


def test_native_tool_calls_survive_without_using_reasoning_as_authority() -> None:
    projected = LocalRuntimeResponsePolicy().apply(
        response(
            content="",
            reasoning="I considered the safe lookup",
            tool_calls=[{
                "id": "call-1",
                "type": "function",
                "function": {"name": "search_code", "arguments": "{}"},
            }],
        ),
        policy_id="qwen3_reasoning_safe",
        tools_requested=True,
    )

    assert projected["choices"][0]["message"]["tool_calls"][0]["id"] == "call-1"
    assert projected["metadata"]["reasoning_observation"]["authorization_input"] is False


def test_unconfigured_policy_is_a_json_copy_without_semantic_changes() -> None:
    payload = response(content="<think>plain text</think>")

    projected = LocalRuntimeResponsePolicy().apply(
        payload, policy_id=None, tools_requested=True
    )

    assert projected == payload
    assert projected is not payload
