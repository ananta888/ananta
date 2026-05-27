"""ChatQueryClassifier — keyword-based intent classification, no LLM calls.

IntentKinds and confidence levels:
  1.0  — selected_artifact + matching intent (user explicitly chose context)
  0.9  — selected_artifact alone
  0.7  — keyword match (single strong keyword)
  0.5  — keyword match (weak / generic)
  0.3  — general_project_question
  0.0  — unknown
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.services.heuristic_runtime.decision_context import DecisionContext


class IntentKind(str, Enum):
    EXPLAIN_FILE = "explain_file"
    FIND_SYMBOL = "find_symbol"
    EXPLAIN_ERROR = "explain_error"
    TODO_STATUS = "todo_status"
    ARTIFACT_LOOKUP = "artifact_lookup"
    HELPCENTER_LOOKUP = "helpcenter_lookup"
    GENERAL_PROJECT_QUESTION = "general_project_question"
    UNKNOWN = "unknown"


@dataclass
class ClassificationResult:
    intent_kind: IntentKind
    confidence: float
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_kind": self.intent_kind.value,
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
        }


# ── Keyword tables ────────────────────────────────────────────────────────────

_EXPLAIN_FILE_KW = frozenset({
    "erklaer", "erkläre", "was macht", "was ist", "explain", "describe",
    "what does", "what is", "zeig mir", "show me", "übersicht", "overview",
})

_FIND_SYMBOL_KW = frozenset({
    "wo ist", "where is", "find", "suche", "such nach", "symbol",
    "function", "class", "method", "def ", "klasse", "methode", "funktion",
    "wo wird", "where is defined", "definition", "declared",
})

_EXPLAIN_ERROR_KW = frozenset({
    "fehler", "error", "exception", "traceback", "crash", "absturz",
    "stack trace", "problem", "bug", "issue", "warum schlägt fehl",
    "why fails", "failing", "broken",
})

_TODO_STATUS_KW = frozenset({
    "todo", "aufgabe", "task", "backlog", "open tasks", "offene aufgaben",
    "was steht noch aus", "what's left", "nächste schritte", "next steps",
    "fortschritt", "progress",
})

_HELPCENTER_KW = frozenset({
    "help", "hilfe", "anleitung", "guide", "faq", "documentation",
    "how to", "wie geht", "anleitung", "tutorial", "wie kann ich",
    "wie funktioniert",
})

_ARTIFACT_KW = frozenset({
    "artifact", "artefakt", "datei", "file", "modul", "module",
    "script", "open file", "zeig datei", "show file",
})


class ChatQueryClassifier:
    def classify(self, query: str, context: DecisionContext) -> ClassificationResult:
        q = query.lower().strip()

        # If user has explicitly selected artifacts, bias heavily toward artifact_lookup
        if context.selected_artifacts:
            # Check if query also matches a specific intent
            matched = self._match_intent(q)
            if matched and matched != IntentKind.UNKNOWN:
                return ClassificationResult(
                    intent_kind=matched,
                    confidence=1.0,
                    reason_codes=["selected_artifact", f"keyword:{matched.value}"],
                )
            return ClassificationResult(
                intent_kind=IntentKind.ARTIFACT_LOOKUP,
                confidence=0.9,
                reason_codes=["selected_artifact"],
            )

        # Pure keyword matching
        matched = self._match_intent(q)
        if matched == IntentKind.UNKNOWN:
            # Check if it looks like a general project question
            if len(q.split()) >= 3 and "?" in query:
                return ClassificationResult(
                    intent_kind=IntentKind.GENERAL_PROJECT_QUESTION,
                    confidence=0.3,
                    reason_codes=["question_syntax"],
                )
            return ClassificationResult(
                intent_kind=IntentKind.UNKNOWN,
                confidence=0.0,
                reason_codes=["no_keyword_match"],
            )

        confidence = self._confidence_for(matched, q)
        return ClassificationResult(
            intent_kind=matched,
            confidence=confidence,
            reason_codes=[f"keyword:{matched.value}"],
        )

    def _match_intent(self, q: str) -> IntentKind:
        # Priority order matches most-specific first
        if any(kw in q for kw in _EXPLAIN_ERROR_KW):
            return IntentKind.EXPLAIN_ERROR
        if any(kw in q for kw in _FIND_SYMBOL_KW):
            return IntentKind.FIND_SYMBOL
        if any(kw in q for kw in _EXPLAIN_FILE_KW):
            return IntentKind.EXPLAIN_FILE
        if any(kw in q for kw in _TODO_STATUS_KW):
            return IntentKind.TODO_STATUS
        if any(kw in q for kw in _HELPCENTER_KW):
            return IntentKind.HELPCENTER_LOOKUP
        if any(kw in q for kw in _ARTIFACT_KW):
            return IntentKind.ARTIFACT_LOOKUP
        return IntentKind.UNKNOWN

    def _confidence_for(self, intent: IntentKind, q: str) -> float:
        # Strong matches get 0.7; if multiple signals present, bump to 0.8
        kw_map = {
            IntentKind.EXPLAIN_ERROR: _EXPLAIN_ERROR_KW,
            IntentKind.FIND_SYMBOL: _FIND_SYMBOL_KW,
            IntentKind.EXPLAIN_FILE: _EXPLAIN_FILE_KW,
            IntentKind.TODO_STATUS: _TODO_STATUS_KW,
            IntentKind.HELPCENTER_LOOKUP: _HELPCENTER_KW,
            IntentKind.ARTIFACT_LOOKUP: _ARTIFACT_KW,
        }
        kws = kw_map.get(intent, frozenset())
        matches = sum(1 for kw in kws if kw in q)
        return 0.8 if matches >= 2 else 0.7
