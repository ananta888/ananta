import pytest

from agent.config import settings
from agent.services.source_chat_service import SourceChatService


class _Context:
    def __init__(self, *, selected=True):
        self.selected = selected
        self.kwargs = None

    def build_context(self, **kwargs):
        self.kwargs = kwargs
        return {
            "grounded_prompt": "grounded",
            "selected_sources": [{"source": "s"}] if self.selected else [],
            "source_references": [{"source_id": "s"}] if self.selected else [],
            "context_hash": "hash",
            "context_bundle": {"explainability": {}},
            "budget": {},
        }


def test_actual_provider_scope_is_used(monkeypatch):
    monkeypatch.setattr(settings, "default_provider", "ollama")
    context = _Context()
    result = SourceChatService(context_service=context, llm_call=lambda _prompt: "answer").answer(
        prompt="question", source_ref="source"
    )
    assert result["llm_scope"] == "local_only"
    assert context.kwargs["llm_scope"] == "local_only"


def test_caller_cannot_claim_a_different_scope(monkeypatch):
    monkeypatch.setattr(settings, "default_provider", "openai")
    with pytest.raises(ValueError, match="llm_scope_mismatch"):
        SourceChatService(context_service=_Context(), llm_call=lambda _prompt: "answer").answer(
            prompt="question",
            source_ref="source",
            requested_llm_scope="local_only",
        )


def test_empty_governed_context_never_calls_llm(monkeypatch):
    monkeypatch.setattr(settings, "default_provider", "ollama")
    calls = []
    with pytest.raises(ValueError, match="source_context_unavailable"):
        SourceChatService(context_service=_Context(selected=False), llm_call=lambda prompt: calls.append(prompt)).answer(
            prompt="question", source_ref="source"
        )
    assert calls == []
