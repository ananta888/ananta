from unittest.mock import patch

from agent.services.model_invocation_service import LLMUnavailableError
from agent.services.propose_strategies.flexible_llm_normalization_strategy import (
    FlexibleLLMNormalizationStrategy,
)
from worker.core.propose_orchestrator import ProposeContext


def _context() -> ProposeContext:
    return ProposeContext(
        goal_id="g-1",
        task_id="t-1",
        task={"id": "t-1", "goal_id": "g-1"},
        base_prompt="return a command",
    )


def test_flexible_strategy_attaches_real_llm_profile_on_success():
    strategy = FlexibleLLMNormalizationStrategy()
    llm_profile = [
        {
            "name": "chat_completions",
            "backend": "llm_api",
            "provider": "ollama",
            "model": "qwen2.5",
            "success": True,
            "latency_ms": 123,
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "source": "model_invocation_service",
            "estimated": False,
            "error_type": None,
            "error_message": None,
            "started_at": 1.0,
            "ended_at": 2.0,
        }
    ]
    with patch(
        "agent.services.propose_strategies.flexible_llm_normalization_strategy.ModelInvocationService.invoke_result",
        return_value={"content": '{"command":"echo ok"}', "metadata": {"llm_call_profile": llm_profile}},
    ):
        result = strategy.run(_context())

    assert result.metadata["llm_call_profile"][0]["estimated"] is False
    assert result.proposal is not None
    assert result.proposal.metadata["llm_call_profile"][0]["source"] == "model_invocation_service"


def test_flexible_strategy_attaches_llm_profile_on_declined_unavailable():
    strategy = FlexibleLLMNormalizationStrategy()
    llm_profile = [{"source": "model_invocation_service", "estimated": False, "success": False}]
    with patch(
        "agent.services.propose_strategies.flexible_llm_normalization_strategy.ModelInvocationService.invoke_result",
        side_effect=LLMUnavailableError("down", llm_call_profile=llm_profile),
    ):
        result = strategy.run(_context())

    assert result.status == "declined"
    assert result.metadata["llm_call_profile"][0]["success"] is False

