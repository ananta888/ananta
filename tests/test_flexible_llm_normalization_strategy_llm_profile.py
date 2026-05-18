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


# TRM-003: malformed response still carries llm_call_profile with provider/model
def test_flexible_strategy_profile_survives_malformed_response():
    strategy = FlexibleLLMNormalizationStrategy()
    llm_profile = [
        {
            "source": "model_invocation_service",
            "estimated": False,
            "success": True,
            "provider": "ollama",
            "model": "qwen2.5",
            "latency_ms": 400,
        }
    ]
    with patch(
        "agent.services.propose_strategies.flexible_llm_normalization_strategy.ModelInvocationService.invoke_result",
        return_value={"content": "NOT VALID JSON {{{", "metadata": {"llm_call_profile": llm_profile}},
    ):
        result = strategy.run(_context())

    # Malformed response may be advisory or declined, but profile must be present with diagnostics
    profile = result.metadata.get("llm_call_profile")
    assert profile is not None, "llm_call_profile must survive malformed response"
    assert profile[0]["provider"] == "ollama"
    assert profile[0]["model"] == "qwen2.5"


def test_flexible_strategy_profile_survives_general_exception():
    strategy = FlexibleLLMNormalizationStrategy()
    with patch(
        "agent.services.propose_strategies.flexible_llm_normalization_strategy.ModelInvocationService.invoke_result",
        side_effect=RuntimeError("unexpected crash"),
    ):
        result = strategy.run(_context())

    # Status is failed but the result must not throw — profile may be empty
    assert result.status == "failed"
    # llm_call_profile key may be missing (no profile captured before exception) — that's acceptable
    # but the result itself must be a ProposeStrategyResult
    from worker.core.propose import ProposeStrategyResult
    assert isinstance(result, ProposeStrategyResult)

