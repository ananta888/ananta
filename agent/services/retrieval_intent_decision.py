"""Retrieval intent via the decision provider layer (DPRV, area ``retrieval_intent``).

Today's keyword classifier (``classify_retrieval_intent``) stays the first and
default answer. With the area on, a decision provider cascade (TypeSafe Jev,
the local ``/v1/decision`` server, an LLM) is asked the same question; in mode
``active`` its answer replaces the keyword intent only when a stage accepted it
at its confidence threshold, in ``shadow`` it is only recorded. "Needs
retrieval at all" is part of the same choice (``generic_chat`` = no), which
the benchmark showed to be far more reliable than a separate yes/no question.

Never overridden: an explicit trigger mode chosen by the user, and an empty
query (both are decided by the rules alone).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AREA = "retrieval_intent"
QUESTION_KEY = "retrieval_intent"
INSTRUCTIONS = "Decide which kind of project-knowledge retrieval this chat question needs."
# Kept identical to benchmarks/decision_providers/retrieval_intent.v1.json (a test checks it): what runs is measured.
INTENT_OPTIONS: dict[str, str] = {
    "implemented_code_explanation": "Explain how existing implemented code works: a function, class, method, "
    "module, file, mechanism, or 'what is / what does X' about a project component; show me the code for X.",
    "architecture_overview": "Describe the structure of a system: architecture, overview, components, dependencies, "
    "how parts relate, data/control flow, without asking for a diagram or a complete full-system analysis.",
    "docs_overview": "Asks for documentation material: docs, README, guide, manual/Anleitung, runbook document, ADR, "
    "concept paper.",
    "tutorial_help": "Asks to learn or be guided: how do I / how to, step-by-step instructions, beginner help, "
    "examples.",
    "game_design": "About the design of the Ananta game: levels, game mechanics, rules, points/score, challenges.",
    "ops_runbook": "Operating the running system: restart, deploy, monitoring, alerts, on-call, recovery, "
    "configuration and setup of the installation.",
    "mermaid_request": "Asks for a diagram of a specific part: mermaid, flowchart, sequence diagram, class or ER "
    "diagram.",
    "architecture_full_scan": "Asks for a complete whole-system analysis: full architecture diagram, overall "
    "architecture, full scan, all relevant components, dependency map.",
    "generic_chat": "Smalltalk or a general question that needs no retrieval from project knowledge.",
}


def decide_retrieval_intent(query: str, rule_intent: str, cfg: Mapping[str, Any] | None = None, *,
                            service: Any = None) -> tuple[str, list[str]]:
    """``(intent, reasons)``: the rule intent, or the accepted decision in active mode."""
    trigger_mode = str((cfg or {}).get("chat_codecompass_trigger_mode") or "auto").strip().lower()
    if trigger_mode != "auto" or not str(query or "").strip():
        return rule_intent, []
    if service is None:
        from agent.services.decision_providers.service import get_decision_service

        service = get_decision_service()
    if service.area_mode(AREA) == "off":
        return rule_intent, []
    from agent.services.decision_providers.types import DecisionProviderError, DecisionQuestion, DecisionRequest

    try:
        request = DecisionRequest(state=str(query)[:4000], purpose=AREA, questions=(
            DecisionQuestion.choice(QUESTION_KEY, INSTRUCTIONS, INTENT_OPTIONS),))
        decision = service.decide(AREA, request, current={QUESTION_KEY: rule_intent})
    except DecisionProviderError:
        return rule_intent, ["decision_provider:invalid_request"]
    if decision is None:
        return rule_intent, []
    if not decision.usable:
        state = "deferred" if decision.outcome.deferred else "shadow"
        return rule_intent, [f"decision_provider:{decision.mode}:{state}"]
    answer = decision.outcome.result.answer(QUESTION_KEY)
    if answer.choice == rule_intent:
        return rule_intent, [f"decision_provider:{decision.outcome.decided_by}:agrees"]
    return answer.choice, [f"decision_provider:{decision.outcome.decided_by}:{answer.confidence:.2f}",
                           f"rule_intent:{rule_intent}"]
