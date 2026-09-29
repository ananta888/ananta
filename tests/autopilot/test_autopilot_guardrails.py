from agent.routes.tasks.autopilot_guardrails import (
    resolve_resilience_config,
)


def test_resilience_retry_and_backoff_values_are_operationally_bounded():
    resolved = resolve_resilience_config(
        agent_config={
            "autonomous_resilience": {
                "retry_attempts": 999_999,
                "retry_backoff_seconds": float("inf"),
                "retry_max_backoff_seconds": 999_999,
                "retry_jitter_factor": float("nan"),
            }
        }
    )

    assert resolved["retry_attempts"] == 5
    assert resolved["retry_backoff_seconds"] == 0.2
    assert resolved["retry_max_backoff_seconds"] == 300.0
    assert resolved["retry_jitter_factor"] == 0.2


def test_resilience_invalid_values_fall_back_without_breaking_dispatch():
    resolved = resolve_resilience_config(
        agent_config={
            "autonomous_resilience": {
                "retry_attempts": "not-an-int",
                "retry_backoff_seconds": "not-a-number",
            }
        }
    )

    assert resolved["retry_attempts"] == 2
    assert resolved["retry_backoff_seconds"] == 0.2
