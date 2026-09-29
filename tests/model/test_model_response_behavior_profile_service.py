from __future__ import annotations

from types import SimpleNamespace

from agent.services.model_response_behavior_profile_service import ModelResponseBehaviorProfileService


def test_behavior_profile_includes_learning_state_and_style(monkeypatch):
    import agent.services.model_response_behavior_profile_service as mod

    monkeypatch.setattr(
        mod,
        "get_planning_model_profile_service",
        lambda: SimpleNamespace(
            resolve_profile=lambda **_: {
                "model_name_pattern": "*gemma*",
                "model_family": "gemma",
                "profile_name": "lmstudio_laptop",
                "preferred_output_format": "markdown",
                "learning_state": {
                    "state": "candidate",
                    "observed_output_shape": "json_in_markdown_fence",
                },
            }
        ),
    )

    result = ModelResponseBehaviorProfileService().resolve(provider="lmstudio", model_name="google/gemma-4-e4b")
    assert result["learning_state"]["state"] == "candidate"
    assert result["preferred_output_format"] == "markdown"
    assert result["preferred_prompt_style"] == "example_driven_json"
    assert result["markdown_handling"] == "allow_and_strip"
    assert result["observed_output_shape"] == "json_in_markdown_fence"


def test_behavior_profile_derives_prompt_style_from_observed_output_shape_only(monkeypatch):
    import agent.services.model_response_behavior_profile_service as mod

    monkeypatch.setattr(
        mod,
        "get_planning_model_profile_service",
        lambda: SimpleNamespace(
            resolve_profile=lambda **_: {
                "model_name_pattern": "*qwen*",
                "model_family": "qwen",
                "profile_name": "local_qwen",
                "preferred_output_format": "json",
                "learning_state": {
                    "state": "learning",
                    "observed_output_shape": "markdown_bullets",
                },
            }
        ),
    )

    result = ModelResponseBehaviorProfileService().resolve(provider="lmstudio", model_name="qwen/qwen-2.5-coder-32b-instruct")
    assert result["preferred_prompt_style"] == "stepwise_then_json"
    assert result["observed_output_shape"] == "markdown_bullets"
