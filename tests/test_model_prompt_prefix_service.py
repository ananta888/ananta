from __future__ import annotations

from types import SimpleNamespace

from agent.services.model_invocation_service import ModelInvocationService
from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader
from agent.services.model_prompt_prefix_service import ModelPromptPrefixService


def _profile(prefix: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        system_prompt_prefix=prefix,
        temperature=0.1,
        max_output_tokens=128,
    )


def test_prefixes_existing_system_message_without_mutating_input() -> None:
    messages = [
        {"role": "system", "content": "Return a concise answer."},
        {"role": "user", "content": "Ready?"},
    ]

    prepared = ModelPromptPrefixService.apply(
        messages,
        profile=_profile("<|think|>"),
    )

    assert prepared[0]["content"] == "<|think|>\nReturn a concise answer."
    assert messages[0]["content"] == "Return a concise answer."


def test_inserts_system_message_and_is_idempotent() -> None:
    messages = [{"role": "user", "content": "Ready?"}]
    profile = _profile("<|think|>")

    prepared = ModelPromptPrefixService.apply(messages, profile=profile)
    repeated = ModelPromptPrefixService.apply(prepared, profile=profile)

    assert prepared == [
        {"role": "system", "content": "<|think|>"},
        {"role": "user", "content": "Ready?"},
    ]
    assert repeated == prepared


def test_provider_request_body_applies_profile_prefix() -> None:
    body, is_native_ollama = ModelInvocationService._provider_request_body(
        provider="ollama",
        url="http://ollama:11434/v1/chat/completions",
        model="ananta-gemma4-reasoning-8k",
        messages=[
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "{}"},
        ],
        profile=_profile("<|think|>"),
        provider_context=None,
        tools=None,
        send_native_tools=False,
        response_format={"type": "json_object"},
    )

    assert is_native_ollama is False
    assert body["messages"][0] == {
        "role": "system",
        "content": "<|think|>\nReturn JSON.",
    }


def test_native_ollama_generate_keeps_prefix_in_system_field() -> None:
    body, is_native_ollama = ModelInvocationService._provider_request_body(
        provider="ollama",
        url="http://ollama:11434/api/generate",
        model="ananta-gemma4-reasoning-8k",
        messages=[
            {"role": "system", "content": "Return a concise answer."},
            {"role": "user", "content": "Ready?"},
        ],
        profile=_profile("<|think|>"),
        provider_context=None,
        tools=None,
        send_native_tools=False,
        response_format=None,
    )

    assert is_native_ollama is True
    assert body["system"] == "<|think|>\nReturn a concise answer."
    assert body["prompt"] == "user: Ready?"
    assert "<|think|>" not in body["prompt"]


def test_profile_input_budget_reserves_prompt_prefix_tokens() -> None:
    profile = ModelProfile(
        profile_id="gemma",
        provider_id="ollama",
        model="gemma4:e4b-it-qat",
        context_tokens=8_192,
        max_output_tokens=3_072,
        system_prompt_prefix="<|think|>",
    )

    assert profile.system_prompt_prefix_tokens() == 9
    assert profile.max_input_tokens() == 5_111


def test_profile_loader_validates_prompt_prefix_boundary() -> None:
    valid = ModelProfileLoader().load_dict(
        {
            "profiles": [
                {
                    "profile_id": "gemma",
                    "provider_id": "ollama",
                    "model": "gemma4:e4b-it-qat",
                    "system_prompt_prefix": "<|think|>",
                }
            ]
        }
    )
    invalid = ModelProfileLoader().load_dict(
        {
            "profiles": [
                {
                    "profile_id": "invalid",
                    "provider_id": "ollama",
                    "model": "model",
                    "system_prompt_prefix": "x" * 1_025,
                }
            ]
        }
    )

    assert valid.ok
    assert valid.profiles[0].system_prompt_prefix == "<|think|>"
    assert not invalid.ok
    assert any("system_prompt_prefix" in error for error in invalid.errors)
