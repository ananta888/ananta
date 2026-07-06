"""CTA-002: Frage-/Hilfebedarf-Erkennung, zweistufig.

Stufe 1 ist deterministisch und kostenlos (offline-testbar). Stufe 2
laeuft NUR bei Kandidat-Signal und geht ueber den bestehenden
Schema-Pfad (model_invocation_service.invoke_with_json_schema_result,
per Konstruktor injizierbar). Ohne injizierten LLM-Pfad liefert der
deterministische Fallback ein konservatives Ergebnis.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

INTENT_QUESTION = "question"
INTENT_HELP_REQUEST = "help_request"
INTENT_BLOCKED = "blocked"
INTENT_IRONIC = "ironic_or_ambiguous"
INTENT_SMALLTALK = "smalltalk"
INTENT_ORGANIZATIONAL = "organizational"
INTENT_OFFTOPIC = "offtopic"

INTENTS = (
    INTENT_QUESTION,
    INTENT_HELP_REQUEST,
    INTENT_BLOCKED,
    INTENT_IRONIC,
    INTENT_SMALLTALK,
    INTENT_ORGANIZATIONAL,
    INTENT_OFFTOPIC,
)

ACTIONABLE_INTENTS = frozenset({INTENT_QUESTION, INTENT_HELP_REQUEST, INTENT_BLOCKED})

INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_codes": {"type": "array", "items": {"type": "string"}},
        "evidence_spans": {"type": "array", "items": {"type": "string"}},
        "needs_teacher_attention": {"type": "boolean"},
    },
    "required": ["intent", "confidence", "reason_codes", "evidence_spans", "needs_teacher_attention"],
    "additionalProperties": False,
}

_W_QUESTION_PATTERN = re.compile(r"\b(wie|was|warum|wieso|weshalb|wo|welche[rsn]?|wann|wer)\b", re.IGNORECASE)
_HELP_PATTERN = re.compile(
    r"geht nicht|funktioniert nicht|klappt nicht|wo finde ich|komme nicht weiter|h[aä]ngt|kaputt|hilfe",
    re.IGNORECASE,
)
_ERROR_PATTERN = re.compile(
    r"traceback|exception|error|fehler(meldung)?|status\s*(code)?\s*[45]\d\d|problem in node",
    re.IGNORECASE,
)
_N8N_TERM_PATTERN = re.compile(
    r"\b(webhook|http request|trigger|merge|wait|credential|node|workflow|n8n|switch|subworkflow)\b",
    re.IGNORECASE,
)
_IRONY_PATTERN = re.compile(r"\b(super|toll|na klasse|klasse|wieder mal|natürlich)\b", re.IGNORECASE)
_ORGANIZATIONAL_PATTERN = re.compile(
    r"\b(pause|abgabe(frist)?|anwesenheit|zoom.?link|raumwechsel|wann ist schluss)\b", re.IGNORECASE
)
_OFFTOPIC_PATTERN = re.compile(r"\b(fu[ßs]ball\w*|kino|urlaub|party|wetter)\b", re.IGNORECASE)


def detect_signals(text: str) -> dict:
    """Stufe 1: deterministische Signale, keine LLM-Kosten."""
    value = str(text or "")
    signals: list[str] = []
    if "?" in value:
        signals.append("question_mark")
    if _W_QUESTION_PATTERN.search(value):
        signals.append("w_question")
    if _HELP_PATTERN.search(value):
        signals.append("help_phrase")
    if _ERROR_PATTERN.search(value):
        signals.append("error_message")
    if _N8N_TERM_PATTERN.search(value):
        signals.append("n8n_term")
    if _IRONY_PATTERN.search(value):
        signals.append("irony_marker")
    if _ORGANIZATIONAL_PATTERN.search(value):
        signals.append("organizational_phrase")
    if _OFFTOPIC_PATTERN.search(value):
        signals.append("offtopic_phrase")
    has_candidate = bool(set(signals) & {"question_mark", "w_question", "help_phrase", "error_message"})
    return {"signals": signals, "has_candidate": has_candidate}


class StudentQuestionDetectionService:
    def __init__(
        self,
        invoke_json: Callable[..., dict] | None = None,
        confidence_threshold: float = 0.6,
    ) -> None:
        self.invoke_json = invoke_json
        self.confidence_threshold = confidence_threshold

    def detect(self, text: str, context: dict | None = None) -> dict:
        stage1 = detect_signals(text)
        signals = stage1["signals"]

        if not stage1["has_candidate"]:
            intent = (
                INTENT_ORGANIZATIONAL
                if "organizational_phrase" in signals
                else INTENT_OFFTOPIC
                if "offtopic_phrase" in signals
                else INTENT_SMALLTALK
            )
            return self._result(intent, 0.9, ["no_candidate_signal", *signals], [], False, llm_used=False)

        if self.invoke_json is None:
            return self._deterministic_fallback(text, signals)

        payload = self.invoke_json(
            schema=INTENT_SCHEMA,
            prompt=self._build_prompt(text, context),
        )
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            try:
                payload = json.loads(payload["content"])
            except (TypeError, ValueError):
                payload = {}
        return self._normalize_llm_result(payload, signals)

    # ── intern ───────────────────────────────────────────────────────────

    def _deterministic_fallback(self, text: str, signals: list[str]) -> dict:
        # Ironie/Ambiguitaet wird NIE als sichere technische Frage
        # behandelt (Acceptance CTA-002).
        if "irony_marker" in signals:
            return self._result(
                INTENT_IRONIC, 0.5, ["irony_with_problem_marker", *signals], [str(text)[:120]], True, llm_used=False
            )
        if "offtopic_phrase" in signals:
            return self._result(INTENT_OFFTOPIC, 0.8, signals, [], False, llm_used=False)
        if "organizational_phrase" in signals:
            return self._result(INTENT_ORGANIZATIONAL, 0.7, signals, [], False, llm_used=False)
        if "error_message" in signals or "help_phrase" in signals:
            return self._result(INTENT_HELP_REQUEST, 0.65, signals, [str(text)[:120]], False, llm_used=False)
        if "question_mark" in signals or "w_question" in signals:
            return self._result(INTENT_QUESTION, 0.7, signals, [str(text)[:120]], False, llm_used=False)
        return self._result(INTENT_SMALLTALK, 0.55, signals, [], False, llm_used=False)

    def _build_prompt(self, text: str, context: dict | None) -> str:
        hint = ""
        if isinstance(context, dict) and context.get("module_scope"):
            hint = f" Wahrscheinlicher Modul-Scope: {context['module_scope']}."
        return (
            "Klassifiziere das folgende Unterrichts-Transkriptsegment eines Schuelers. "
            "Ironische oder mehrdeutige Aeusserungen sind ironic_or_ambiguous, nie question."
            f"{hint}\nSegment: {str(text)[:1500]}"
        )

    def _normalize_llm_result(self, payload: object, signals: list[str]) -> dict:
        data = payload if isinstance(payload, dict) else {}
        intent = str(data.get("intent") or "").strip()
        if intent not in INTENTS:
            return self._result(INTENT_IRONIC, 0.4, ["llm_result_invalid", *signals], [], True, llm_used=True)
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence"))))
        except (TypeError, ValueError):
            confidence = 0.4
        result = self._result(
            intent,
            confidence,
            [str(code) for code in (data.get("reason_codes") or [])] or signals,
            [str(span)[:200] for span in (data.get("evidence_spans") or [])][:5],
            bool(data.get("needs_teacher_attention")),
            llm_used=True,
        )
        if intent == INTENT_IRONIC:
            result["needs_teacher_attention"] = True
        return result

    @staticmethod
    def _result(
        intent: str,
        confidence: float,
        reason_codes: list[str],
        evidence_spans: list[str],
        needs_teacher: bool,
        *,
        llm_used: bool,
    ) -> dict:
        return {
            "intent": intent,
            "confidence": confidence,
            "reason_codes": reason_codes,
            "evidence_spans": evidence_spans,
            "needs_teacher_attention": needs_teacher,
            "llm_used": llm_used,
        }
