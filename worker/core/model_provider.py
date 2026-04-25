from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import requests


@dataclass(frozen=True)
class ModelProviderResult:
    text: str
    metadata: dict[str, Any]


class WorkerModelProvider(Protocol):
    def complete(self, *, prompt: str, prompt_template_version: str) -> ModelProviderResult:
        ...


class OpenAICompatibleModelProvider:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        timeout_seconds: int = 60,
        api_key_env: str = "OPENAI_API_KEY",
    ) -> None:
        self.provider = str(provider).strip() or "openai_compatible"
        self.model = str(model).strip()
        self.base_url = str(base_url).rstrip("/")
        self.timeout_seconds = int(timeout_seconds)
        self.api_key_env = str(api_key_env).strip()

    def complete(self, *, prompt: str, prompt_template_version: str) -> ModelProviderResult:
        api_key = os.environ.get(self.api_key_env)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": str(prompt)}],
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        choices = list(body.get("choices") or [])
        if not choices:
            raise ValueError("empty_model_choices")
        message = dict(choices[0].get("message") or {})
        content = str(message.get("content") or "")
        if not content:
            raise ValueError("empty_model_content")
        return ModelProviderResult(
            text=content,
            metadata={
                "provider": self.provider,
                "model": self.model,
                "base_url_label": self.base_url,
                "timeout_seconds": self.timeout_seconds,
                "prompt_template_version": str(prompt_template_version).strip(),
                "llm_used": True,
            },
        )


class LocalModelProvider:
    def __init__(self, *, provider: str = "local", model: str = "mock-local", timeout_seconds: int = 30) -> None:
        self.provider = str(provider).strip()
        self.model = str(model).strip()
        self.timeout_seconds = int(timeout_seconds)

    def complete(self, *, prompt: str, prompt_template_version: str) -> ModelProviderResult:
        return ModelProviderResult(
            text=str(prompt),
            metadata={
                "provider": self.provider,
                "model": self.model,
                "base_url_label": "local://provider",
                "timeout_seconds": self.timeout_seconds,
                "prompt_template_version": str(prompt_template_version).strip(),
                "llm_used": True,
            },
        )


class DeterministicMockModelProvider:
    def __init__(self, *, responses: list[str], provider: str = "mock", model: str = "deterministic-mock") -> None:
        self.responses = list(responses)
        self.provider = str(provider).strip()
        self.model = str(model).strip()

    def complete(self, *, prompt: str, prompt_template_version: str) -> ModelProviderResult:
        del prompt
        if not self.responses:
            raise ValueError("mock_responses_exhausted")
        text = self.responses.pop(0)
        return ModelProviderResult(
            text=text,
            metadata={
                "provider": self.provider,
                "model": self.model,
                "base_url_label": "mock://deterministic",
                "timeout_seconds": 0,
                "prompt_template_version": str(prompt_template_version).strip(),
                "llm_used": True,
            },
        )


def build_model_provider(config: dict[str, Any]) -> WorkerModelProvider | None:
    provider_type = str(config.get("provider_type") or "").strip().lower()
    if not provider_type:
        return None
    if provider_type == "openai_compatible":
        return OpenAICompatibleModelProvider(
            provider=str(config.get("provider") or "openai_compatible"),
            model=str(config.get("model") or ""),
            base_url=str(config.get("base_url") or "").rstrip("/"),
            timeout_seconds=int(config.get("timeout_seconds") or 60),
            api_key_env=str(config.get("api_key_env") or "OPENAI_API_KEY"),
        )
    if provider_type == "local":
        return LocalModelProvider(
            provider=str(config.get("provider") or "local"),
            model=str(config.get("model") or "mock-local"),
            timeout_seconds=int(config.get("timeout_seconds") or 30),
        )
    if provider_type == "mock":
        return DeterministicMockModelProvider(
            responses=[str(item) for item in list(config.get("responses") or [])],
            provider=str(config.get("provider") or "mock"),
            model=str(config.get("model") or "deterministic-mock"),
        )
    return None
