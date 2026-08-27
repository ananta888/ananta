from __future__ import annotations

from types import SimpleNamespace

from flask import Flask, g

from agent.services.model_invocation_service import ModelInvocationService


class _Observer:
    def __init__(self) -> None:
        self.calls = []

    def observe_attempt(self, observation):
        self.calls.append(observation)


def test_local_attempt_observation_uses_request_correlation_without_content() -> None:
    app = Flask(__name__)
    observer = _Observer()
    app.extensions["model_invocation_observation_port"] = observer
    profile = SimpleNamespace(
        profile_id="local_kat_coder_v25_heavy",
        context_tokens=65_536,
    )

    with app.app_context():
        g.llm_goal_id = "goal-7"
        g.llm_task_id = "task-8"
        ModelInvocationService._observe_model_invocation_attempt(
            attempt={
                "profile": profile,
                "provider": "openai_compatible",
                "model": "kat-coder-v2.5-dev",
            },
            resolution_info={"fallback_index": 1},
            success=False,
            reason_code="timeout",
            call_profile={"latency_ms": 50, "prompt_tokens": 12},
        )

    assert len(observer.calls) == 1
    assert observer.calls[0].profile_id == "local_kat_coder_v25_heavy"
    assert observer.calls[0].provider_id == "openai_compatible"
    assert observer.calls[0].reason_code == "timeout"
    assert observer.calls[0].call_profile == {"latency_ms": 50, "prompt_tokens": 12}
    assert observer.calls[0].fallback_index == 1
    assert observer.calls[0].context_capacity == 65_536
    assert observer.calls[0].goal_id == "goal-7"
    assert observer.calls[0].task_id == "task-8"


def test_provider_neutral_port_can_receive_non_local_attempt() -> None:
    app = Flask(__name__)
    observer = _Observer()
    app.extensions["model_invocation_observation_port"] = observer

    with app.app_context():
        ModelInvocationService._observe_model_invocation_attempt(
            attempt={
                "profile": SimpleNamespace(profile_id="cloud_profile", context_tokens=1),
                "provider": "cloud",
                "model": "cloud-model",
            },
            resolution_info={},
            success=True,
            reason_code="invocation_completed",
            call_profile=None,
        )

    assert len(observer.calls) == 1
    assert observer.calls[0].profile_id == "cloud_profile"
