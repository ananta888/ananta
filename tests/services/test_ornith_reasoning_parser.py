from __future__ import annotations

import pytest

from agent.services.local_runtime_response_policy import (
    LocalRuntimeResponsePolicy,
    LocalRuntimeResponsePolicyError,
)


def apply(message, *, tools=False):
    return LocalRuntimeResponsePolicy().apply(
        {"choices": [{"message": message}]}, policy_id="qwen3_reasoning_safe", tools_requested=tools
    )


def test_reasoning_is_hashed_removed_and_never_authorizes() -> None:
    result = apply({"content": "<think>private chain</think>final"})
    message = result["choices"][0]["message"]
    observation = result["metadata"]["reasoning_observation"]

    assert message["content"] == "final"
    assert "private chain" not in str(result)
    assert observation["present"] is True
    assert observation["persisted"] is False
    assert observation["authorization_input"] is False


@pytest.mark.parametrize("content", ["<tool_call>{}</tool_call>", "<think>x</think><think>y</think>z"])
def test_ambiguous_markup_fails_closed(content: str) -> None:
    with pytest.raises(LocalRuntimeResponsePolicyError):
        apply({"content": content}, tools=True)


def test_native_and_markup_reasoning_cannot_be_combined() -> None:
    with pytest.raises(LocalRuntimeResponsePolicyError, match="local_runtime_reasoning_ambiguous"):
        apply({"content": "<think>one</think>final", "reasoning_content": "two"})
