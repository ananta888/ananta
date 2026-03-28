from __future__ import annotations

from typing import Any

from agent.llm_integration import extract_llm_text_and_usage, generate_text as _generate_text


class HubLLMService:
    """Shared hub-owned adapter for all outward-facing LLM entry points."""

    def generate_text(self, **kwargs) -> Any:
        return _generate_text(**kwargs)

    def generate_text_and_usage(self, **kwargs) -> tuple[str, dict[str, int], Any]:
        result = self.generate_text(**kwargs)
        text, usage = extract_llm_text_and_usage(result)
        return text, usage, result


hub_llm_service = HubLLMService()


def get_hub_llm_service() -> HubLLMService:
    return hub_llm_service


def generate_text(**kwargs) -> Any:
    return get_hub_llm_service().generate_text(**kwargs)


def generate_text_and_usage(**kwargs) -> tuple[str, dict[str, int], Any]:
    return get_hub_llm_service().generate_text_and_usage(**kwargs)
