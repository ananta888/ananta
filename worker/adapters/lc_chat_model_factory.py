"""ChatModel factory für LangChain-Integration (LCG-036).

Wählt den passenden ChatModel-Provider basierend auf model_provider_ref.
Gibt None zurück wenn Provider-Extra nicht installiert oder model_ref unbekannt —
Caller fällt auf SimplexRunner/RunnableLambda zurück.
"""
from __future__ import annotations

from typing import Any


def build_lc_chat_model(model_provider_ref: str,
                         base_url: str | None = None) -> Any | None:
    """Build a BaseChatModel from model_provider_ref or return None.

    Prefix mapping:
    - 'ollama.*' or 'local.*' -> ChatOllama (if langchain_ollama installed)
    - 'openai.*' or 'cloud.openai.*' -> ChatOpenAI (if langchain_openai installed)
    - 'anthropic.*' or 'cloud.anthropic.*' -> ChatAnthropic (if langchain_anthropic installed)
    - anything else -> None (SimplexRunner fallback)
    """
    if not model_provider_ref:
        return None

    ref_lower = model_provider_ref.lower()

    # Extract model name (everything after first dot)
    if "." in model_provider_ref:
        model_name = model_provider_ref.split(".", 1)[1]
    else:
        model_name = model_provider_ref

    # Ollama / local
    if ref_lower.startswith("ollama.") or ref_lower.startswith("local."):
        try:
            from langchain_ollama import ChatOllama  # type: ignore
            kwargs: dict[str, Any] = {"model": model_name}
            if base_url:
                kwargs["base_url"] = base_url
            return ChatOllama(**kwargs)
        except ImportError:
            return None

    # OpenAI
    if ref_lower.startswith("openai.") or ref_lower.startswith("cloud.openai."):
        try:
            from langchain_openai import ChatOpenAI  # type: ignore
            kwargs = {"model": model_name}
            if base_url:
                kwargs["base_url"] = base_url
            return ChatOpenAI(**kwargs)
        except ImportError:
            return None

    # Anthropic
    if ref_lower.startswith("anthropic.") or ref_lower.startswith("cloud.anthropic."):
        try:
            from langchain_anthropic import ChatAnthropic  # type: ignore
            kwargs = {"model": model_name}
            return ChatAnthropic(**kwargs)
        except ImportError:
            return None

    return None
