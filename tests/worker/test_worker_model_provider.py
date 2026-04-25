from __future__ import annotations

from typing import Any

from worker.core.model_provider import (
    DeterministicMockModelProvider,
    LocalModelProvider,
    build_model_provider,
)


def test_build_model_provider_returns_none_when_missing_type() -> None:
    assert build_model_provider({}) is None


def test_deterministic_mock_provider_returns_configured_response() -> None:
    provider = DeterministicMockModelProvider(responses=["hello"])
    result = provider.complete(prompt="ignored", prompt_template_version="v1")
    assert result.text == "hello"
    assert result.metadata["provider"] == "mock"


def test_local_provider_echoes_prompt() -> None:
    provider = LocalModelProvider(provider="local", model="m1")
    result = provider.complete(prompt="abc", prompt_template_version="v1")
    assert result.text == "abc"
    assert result.metadata["model"] == "m1"


def test_openai_compatible_provider_parses_chat_completion(monkeypatch) -> None:
    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "{\"patch\":\"diff --git\"}"}}]}

    def _fake_post(*args, **kwargs):  # type: ignore[no-untyped-def]
        return _Response()

    monkeypatch.setattr("worker.core.model_provider.requests.post", _fake_post)
    provider = build_model_provider(
        {
            "provider_type": "openai_compatible",
            "provider": "openai",
            "model": "gpt-test",
            "base_url": "http://localhost:11434/v1",
            "timeout_seconds": 5,
        }
    )
    assert provider is not None
    result = provider.complete(prompt="hello", prompt_template_version="v1")
    assert "patch" in result.text
    assert result.metadata["model"] == "gpt-test"
