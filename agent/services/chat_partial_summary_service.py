"""Partial chat-history summarization.

Summarizes a user-selected slice of chat messages. Tries the configured
LLM backend first; falls back to a deterministic extractive summary so the
feature works offline (method is reported so the UI can show which one ran).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

_log = logging.getLogger(__name__)

_LLM_TIMEOUT_SECONDS = 30

_MIN_TARGET_CHARS = 100
_MAX_TARGET_CHARS = 5000


def call_llm_text(prompt: str, *, timeout: int = _LLM_TIMEOUT_SECONDS) -> str:
    """Send a plain prompt to the configured LLM backend and return the text.

    Thin shared wrapper around :func:`agent.llm_integration.generate_text`
    (used by the partial-summary service and the ai-reorganize endpoint).
    Returns "" when the backend is unreachable or produced no usable text;
    may raise on unexpected errors — callers should treat both as failure.
    """
    from agent.llm_integration import generate_text

    result = generate_text(prompt=prompt, timeout=timeout)
    if isinstance(result, str):
        return result.strip()
    return ""


def _clamp_target_chars(target_chars: int) -> int:
    try:
        value = int(target_chars)
    except (TypeError, ValueError):
        value = 800
    return max(_MIN_TARGET_CHARS, min(_MAX_TARGET_CHARS, value))


def _truncate_at_word_boundary(text: str, max_chars: int) -> str:
    """Truncate ``text`` to at most ``max_chars`` chars at a word boundary."""
    if len(text) <= max_chars:
        return text
    cut = text[: max(0, max_chars - 1)]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _first_sentence(text: str) -> str:
    """Return the first sentence of ``text`` (split on ``.!?``)."""
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    match = re.search(r"[.!?]", stripped)
    if match:
        return stripped[: match.end()].strip()
    return stripped


@dataclass
class SummaryResult:
    summary: str
    method: str  # "llm" | "extractive"
    source_count: int
    chars: int


class ChatPartialSummaryService:
    """Summarizes a slice of chat messages (LLM first, extractive fallback)."""

    def summarize(
        self,
        messages: list[dict],
        *,
        target_chars: int = 800,
        instruction: str = "",
    ) -> SummaryResult:
        target_chars = _clamp_target_chars(target_chars)
        clean: list[tuple[str, str]] = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            sender = str(msg.get("sender") or "").strip() or "unbekannt"
            text = str(msg.get("text") or "").strip()
            if text:
                clean.append((sender, text))

        if not clean:
            return SummaryResult(summary="", method="extractive", source_count=0, chars=0)

        summary = ""
        method = "extractive"
        try:
            llm_summary = self._llm_summarize(clean, target_chars=target_chars, instruction=instruction)
            if llm_summary:
                summary = _truncate_at_word_boundary(llm_summary, target_chars)
                method = "llm"
        except Exception as exc:  # noqa: BLE001 — any LLM failure falls back
            _log.debug("Partial-summary LLM call failed, using extractive fallback: %s", exc)

        if not summary:
            summary = self._extractive_summary(clean, target_chars=target_chars)
            method = "extractive"

        return SummaryResult(
            summary=summary,
            method=method,
            source_count=len(clean),
            chars=len(summary),
        )

    # ── LLM path ─────────────────────────────────────────────────────────

    def _llm_summarize(
        self,
        messages: list[tuple[str, str]],
        *,
        target_chars: int,
        instruction: str,
    ) -> str:
        lines = [
            f"Fasse den folgenden Chat-Ausschnitt zusammen. Maximal {target_chars} Zeichen.",
            "Behalte konkrete Fakten, Entscheidungen, Datei-/Funktionsnamen und offene Punkte.",
            "Antworte NUR mit der Zusammenfassung, ohne Einleitung.",
        ]
        instruction = str(instruction or "").strip()
        if instruction:
            lines.append(instruction)
        lines.append("---")
        for sender, text in messages:
            lines.append(f"{sender}: {text}")
        prompt = "\n".join(lines)
        return call_llm_text(prompt, timeout=_LLM_TIMEOUT_SECONDS)

    # ── Extractive fallback (deterministic, never raises) ────────────────

    def _extractive_summary(
        self,
        messages: list[tuple[str, str]],
        *,
        target_chars: int,
    ) -> str:
        lines: list[str] = []
        for sender, text in messages:
            sentence = _first_sentence(text)
            if sentence:
                lines.append(f"{sender}: {sentence}")
        joined = "\n".join(lines)
        return _truncate_at_word_boundary(joined, target_chars)


_service: ChatPartialSummaryService | None = None


def get_chat_partial_summary_service() -> ChatPartialSummaryService:
    """Module-level singleton accessor."""
    global _service
    if _service is None:
        _service = ChatPartialSummaryService()
    return _service
