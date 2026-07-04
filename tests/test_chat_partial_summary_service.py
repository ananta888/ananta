"""Unit-Tests für ChatPartialSummaryService (LLM-first, extraktiver Fallback)."""
from __future__ import annotations

from unittest.mock import patch

from agent.services.chat_partial_summary_service import (
    ChatPartialSummaryService,
    get_chat_partial_summary_service,
)

# call_llm_text is a module-global of the service module — both the LLM path
# of the service and the reorganize endpoint resolve it there at call time.
LLM_PATCH_TARGET = "agent.services.chat_partial_summary_service.call_llm_text"

MESSAGES = [
    {"sender": "user", "text": "Der Parser wirft einen Fehler. Bitte prüfen."},
    {"sender": "ai", "text": "Der Fehler liegt in tokenizer.py Zeile 12! Ich schlage einen Fix vor."},
]


def _service() -> ChatPartialSummaryService:
    return ChatPartialSummaryService()


# ── LLM path ──────────────────────────────────────────────────────────────

def test_llm_success_uses_llm_method_and_text():
    fixed = "Parser-Fehler in tokenizer.py Zeile 12, Fix vorgeschlagen."
    with patch(LLM_PATCH_TARGET, return_value=fixed):
        result = _service().summarize(MESSAGES)
    assert result.method == "llm"
    assert result.summary == fixed
    assert result.source_count == 2
    assert result.chars == len(fixed)


def test_llm_long_answer_is_truncated_at_word_boundary():
    long_answer = "wort " * 300  # ~1500 chars
    with patch(LLM_PATCH_TARGET, return_value=long_answer.strip()):
        result = _service().summarize(MESSAGES, target_chars=200)
    assert result.method == "llm"
    assert len(result.summary) <= 200
    assert result.summary.endswith("…")
    assert result.chars == len(result.summary)


def test_llm_empty_response_falls_back_to_extractive():
    with patch(LLM_PATCH_TARGET, return_value=""):
        result = _service().summarize(MESSAGES)
    assert result.method == "extractive"
    assert result.summary
    assert result.source_count == 2


def test_llm_exception_falls_back_to_extractive_and_never_raises():
    with patch(LLM_PATCH_TARGET, side_effect=RuntimeError("backend unreachable")):
        result = _service().summarize(MESSAGES)  # must not raise
    assert result.method == "extractive"
    assert result.summary


# ── Extractive fallback ───────────────────────────────────────────────────

def test_extractive_is_deterministic():
    with patch(LLM_PATCH_TARGET, return_value=""):
        first = _service().summarize(MESSAGES, target_chars=300)
        second = _service().summarize(MESSAGES, target_chars=300)
    assert first.summary == second.summary
    assert first.method == second.method == "extractive"


def test_extractive_uses_first_sentence_with_sender_prefix():
    with patch(LLM_PATCH_TARGET, return_value=""):
        result = _service().summarize(MESSAGES, target_chars=500)
    lines = result.summary.split("\n")
    assert lines[0] == "user: Der Parser wirft einen Fehler."
    # First sentence splits at the FIRST .!? occurrence — the "." in the
    # filename "tokenizer.py" already terminates the ai sentence.
    assert lines[1] == "ai: Der Fehler liegt in tokenizer."


def test_extractive_respects_target_chars_and_ends_with_ellipsis():
    long_messages = [
        {"sender": "user", "text": ("lang " * 60).strip() + "."},
        {"sender": "ai", "text": ("noch länger " * 40).strip() + "."},
    ]
    with patch(LLM_PATCH_TARGET, return_value=""):
        result = _service().summarize(long_messages, target_chars=150)
    assert result.method == "extractive"
    assert len(result.summary) <= 150
    assert result.summary.endswith("…")
    assert result.chars == len(result.summary)


# ── target_chars clamping ─────────────────────────────────────────────────

def test_target_chars_below_minimum_is_clamped_to_100():
    captured: dict = {}

    def fake_llm(prompt: str, *, timeout: int = 30) -> str:
        captured["prompt"] = prompt
        return ""

    long_messages = [{"sender": "user", "text": ("wort " * 60).strip() + "."}]
    with patch(LLM_PATCH_TARGET, side_effect=fake_llm):
        result = _service().summarize(long_messages, target_chars=10)
    # The LLM prompt announces the clamped value …
    assert "Maximal 100 Zeichen." in captured["prompt"]
    # … and the extractive fallback truncates to 100, not to 10
    assert 10 < len(result.summary) <= 100
    assert result.summary.endswith("…")


def test_target_chars_above_maximum_is_clamped_to_5000():
    captured: dict = {}

    def fake_llm(prompt: str, *, timeout: int = 30) -> str:
        captured["prompt"] = prompt
        return ""

    # Extractive material well beyond 5000 chars
    long_messages = [
        {"sender": f"user{i}", "text": ("wort " * 300).strip() + "."}
        for i in range(5)
    ]
    with patch(LLM_PATCH_TARGET, side_effect=fake_llm):
        result = _service().summarize(long_messages, target_chars=99999)
    assert "Maximal 5000 Zeichen." in captured["prompt"]
    assert len(result.summary) <= 5000
    assert result.summary.endswith("…")


# ── Degenerate inputs ─────────────────────────────────────────────────────

def test_empty_and_invalid_messages_handled_gracefully():
    with patch(LLM_PATCH_TARGET, side_effect=AssertionError("LLM must not be called")):
        result = _service().summarize([
            {"sender": "user", "text": ""},
            {"sender": "ai", "text": "   "},
            "not-a-dict",
            42,
        ])
    assert result.summary == ""
    assert result.method == "extractive"
    assert result.source_count == 0
    assert result.chars == 0


def test_empty_message_list_returns_empty_extractive_result():
    with patch(LLM_PATCH_TARGET, side_effect=AssertionError("LLM must not be called")):
        result = _service().summarize([])
    assert result.summary == ""
    assert result.method == "extractive"
    assert result.source_count == 0


def test_mixed_valid_and_empty_messages_counts_only_valid():
    messages = [
        {"sender": "user", "text": "Nur diese Nachricht zählt."},
        {"sender": "ai", "text": ""},
    ]
    with patch(LLM_PATCH_TARGET, return_value=""):
        result = _service().summarize(messages)
    assert result.source_count == 1
    assert result.summary == "user: Nur diese Nachricht zählt."


def test_singleton_accessor_returns_same_instance():
    assert get_chat_partial_summary_service() is get_chat_partial_summary_service()
