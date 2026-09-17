"""Deterministic question router for the companion avatar.

Decides *where* an answer is grounded before any model is called:

* ``self_explanation``           – "Wie hast du diese Antwort erzeugt?" is
  answered from the recorded runtime trace, never generated.
* ``ananta_code_architecture``   – Ananta/repository/self questions go through
  CodeCompass retrieval.
* ``meet_runtime_current_dialog``– questions about the running meeting use the
  runtime context (plus CodeCompass when the question is also about code).
* ``general_question``           – smalltalk and general questions use the
  configured LLM only; they are never routed through CodeCompass.

Keyword matching is intentionally simple and auditable; it is a policy
decision, so no LLM is involved and the result is reproducible.
"""

import re
from dataclasses import dataclass

SELF_EXPLANATION = "self_explanation"
ANANTA_CODE_ARCHITECTURE = "ananta_code_architecture"
MEET_RUNTIME_CURRENT_DIALOG = "meet_runtime_current_dialog"
GENERAL_QUESTION = "general_question"
ROUTES = frozenset({SELF_EXPLANATION, ANANTA_CODE_ARCHITECTURE, MEET_RUNTIME_CURRENT_DIALOG, GENERAL_QUESTION})

_SELF_EXPLANATION = re.compile(
    r"(wie hast du (diese|die|deine) antwort|wie bist du (auf|zu) (diese|die) antwort|"
    r"woher (hast|weißt|weisst) du das|how did you (come up with|generate|produce) (that|this|the) answer|"
    r"erkl[aä]r(e)? (mir )?(deine|die) letzte antwort|quellen (für|fuer) (deine|die) (letzte )?antwort)",
    re.IGNORECASE,
)
_SELF_PIPELINE = re.compile(
    r"(deine (sprachausgabe|stimme|avatar|mund|animation|pipeline|funktionsweise|architektur)|"
    r"wie funktionierst du|wie arbeitest du|your (speech|voice|avatar|mouth|animation|pipeline)|"
    r"how do you (work|speak|talk|render)|lip.?sync|lippensync)",
    re.IGNORECASE,
)
_CODE = re.compile(
    r"(\bananta\b|codecompass|\brepositor|\brepo\b|\bhub\b|\bworker\b|symbolgraph|architektur|architecture|"
    r"\bdatei\b|\bfile\b|\bmodul|\bklasse\b|\bclass\b|\bfunktion|\bfunction\b|\bendpoint|\broute\b|"
    r"\bcommit\b|\bservice\b|\bcode\b|welche datei|which file|wo (steht|ist) (der|das|die) code|implementiert)",
    re.IGNORECASE,
)
_MEET = re.compile(
    r"(\bmeet(ing)?\b|\braum\b|\broom\b|teilnehmer|participant|\bchat\b|webrtc|\bmikro|\baudio\b|\bvideo\b|"
    r"gerade|jetzt|aktuell|dieses gespr[aä]ch|this (call|meeting|conversation))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteDecision:
    route: str
    codecompass: bool
    reason: str


def classify(text, *, codecompass_enabled=True):
    """Return the grounded route for ``text``; deterministic and bounded."""
    text = " ".join(str(text or "").split())[:1000]
    if not text:
        return RouteDecision(GENERAL_QUESTION, False, "empty")
    if _SELF_EXPLANATION.search(text):
        return RouteDecision(SELF_EXPLANATION, False, "self_explanation_phrase")
    is_code = bool(_CODE.search(text)) or bool(_SELF_PIPELINE.search(text))
    is_meet = bool(_MEET.search(text))
    if is_meet and not _CODE.search(text) and not _SELF_PIPELINE.search(text):
        return RouteDecision(MEET_RUNTIME_CURRENT_DIALOG, False, "meet_runtime_phrase")
    if is_code:
        route = MEET_RUNTIME_CURRENT_DIALOG if is_meet and not _SELF_PIPELINE.search(text) else ANANTA_CODE_ARCHITECTURE
        return RouteDecision(route, bool(codecompass_enabled), "code_or_self_phrase")
    return RouteDecision(GENERAL_QUESTION, False, "no_repository_signal")
