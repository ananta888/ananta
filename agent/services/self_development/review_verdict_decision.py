"""Decision-scored verdict production for the self-development acceptance review.

Two independent decision engines answer the same questions about the review
evidence and the verdict is accepted only when they agree: the local Laya
server (typed decisions, single forward pass) and the Bonsai decision server
(``POST /v1/decision``, scored decision tree with world knowledge). Two models
have to be wrong -- or manipulated -- the same way at once, which is what makes
the pair worth more than either alone.

The verdict JSON is assembled from the agreed answers, so its shape is
guaranteed and nothing is parsed out of free text. Everything stays
fail-closed: disagreement, low confidence and transport failures never approve
an acceptance; a veto-only gate where the decision never grants approval by
itself.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping, Protocol, Sequence

from agent.services.self_development.acceptance_review import (
    AcceptanceReviewError,
    AcceptanceReviewResult,
)
from agent.services.vision_decision_provider import MAX_TEXT_CHARS

VERDICT_TASK_ID = "self_development_review_verdict"
AREA = "self_development_review"

FINDING_CRITERION_UNCONFIRMED = "criterion_unconfirmed"
FINDING_REPORTED = "review_findings_reported"
FINDING_DISAGREEMENT = "review_verdict_decision_disagreed"
FINDING_ABSTAINED = "review_verdict_decision_unconfirmed"

# The two engines agree on the side of 0.5; the coin-flip floor lives in the
# area configuration (``confidence_threshold``): laya's general checkpoints
# are honestly uncertain on review evidence (confidence |2p-1| between 0.1
# and 0.35 even on satisfied verdicts), so a high bar would veto every
# acceptance instead of every manipulation.

_REQUIREMENTS_CHARS = 600
_FILES_CHARS = 350
_ANALYSIS_CHARS = 2_700
_CONCLUSIONS_CHARS = 2_200
_ITEM_CONCLUSION_CHARS = 200

_log = logging.getLogger(__name__)


class ReviewVerdictProducer(Protocol):
    """Minimal port: produce the acceptance verdict from the review evidence."""

    def produce(
        self,
        *,
        requirements: Mapping[str, Any],
        changed_files: Mapping[str, str],
        analysis: str,
    ) -> AcceptanceReviewResult: ...


def _criteria(requirements: Mapping[str, Any]) -> tuple[str, ...]:
    raw = requirements.get("acceptance_criteria")
    if not isinstance(raw, list) or not raw:
        return ()
    return tuple(str(item) for item in raw)


def _decision_text(*, requirements: Mapping[str, Any], changed_files: Mapping[str, str], analysis: str) -> str:
    parts = [
        "Independent acceptance review of one change (decision scoring).",
        "Task requirements (JSON):",
        json.dumps(dict(requirements), ensure_ascii=False, sort_keys=True)[:_REQUIREMENTS_CHARS],
        "Changed files:",
        "\n".join(f"- {path}" for path in sorted(changed_files))[:_FILES_CHARS],
        "Reviewer item conclusions (numbered, in criteria order):",
        _item_conclusions(analysis)[:_CONCLUSIONS_CHARS],
        "Reviewer analysis:",
        str(analysis or "")[:_ANALYSIS_CHARS],
    ]
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def _item_conclusions(analysis: str) -> str:
    """The analysis' numbered per-criterion conclusion lines, kept complete.

    The state budget truncates long prose from the head; the verdict questions
    ask about the numbered items, so those lines are extracted first and survive
    the truncation even when the full analysis does not fit.
    """
    lines: list[str] = []
    for match in re.finditer(r"(?m)^\s*(\d+)\.[^\n]*", str(analysis or "")):
        lines.append(match.group(0).strip()[:_ITEM_CONCLUSION_CHARS])
        if len(lines) >= 16:
            break
    return "\n".join(lines)


def _questions(criteria: Sequence[str]) -> tuple:
    from agent.services.decision_providers.types import DecisionQuestion

    total = len(criteria)
    items = [
        DecisionQuestion.noul(
            f"criterion_{index}",
            f"The review analysis discusses acceptance criterion {index + 1} of {total} in order: {text} "
            f"Look at the analysis's numbered item {index + 1} only: does that item conclude the criterion "
            "is SATISFIED? Answer yes only when that item is marked SATISFIED.",
        )
        for index, text in enumerate(criteria)
    ]
    items.append(
        DecisionQuestion.noul(
            "findings_reported",
            "The review analysis marks any acceptance criterion as NOT SATISFIED or reports a blocking "
            "violation of the acceptance criteria. Answer yes only for a NOT SATISFIED marker or a "
            "blocking violation, not for minor remarks.",
        )
    )
    return tuple(items)


def _blocked(criteria_count: int, finding: str) -> AcceptanceReviewResult:
    return AcceptanceReviewResult(
        approved=False,
        criteria_passed=(False,) * criteria_count,
        findings=(finding,),
    )


class DecisionReviewVerdictProducer:
    """Verdicts from the ``self_development_review`` decision area; anomalies fail closed.

    The area's ``cascade`` selects the scoring providers (one ``local_decision``
    entry for a single-provider stage, ``["laya", "local_decision"]`` for an
    agreement group) and its ``confidence_threshold`` is the coin-flip floor.
    Every produced verdict leaves a bounded trace in ``last_evidence`` for the
    item record.
    """

    def __init__(self, *, service: Any) -> None:
        self._service = service
        self.last_evidence: dict = {}

    def produce(
        self,
        *,
        requirements: Mapping[str, Any],
        changed_files: Mapping[str, str],
        analysis: str,
    ) -> AcceptanceReviewResult:
        from agent.services.decision_providers.types import (
            DecisionProviderError,
            DecisionRequest,
        )

        criteria = _criteria(requirements)
        if not criteria:
            self.last_evidence = {"verdict_error": "review_verdict_criteria_mismatch"}
            raise AcceptanceReviewError("review_verdict_criteria_mismatch")
        if len(criteria) + 1 > 32:
            self.last_evidence = {"verdict_error": "review_verdict_schema_too_large"}
            raise AcceptanceReviewError("review_verdict_schema_too_large")
        request = DecisionRequest(
            state=_decision_text(requirements=requirements, changed_files=changed_files, analysis=analysis),
            questions=_questions(criteria),
            purpose=AREA,
        )
        try:
            decision = self._service.decide(AREA, request)
        except DecisionProviderError as exc:
            self.last_evidence = {"verdict_error": str(exc)[:200]}
            _log.warning("review verdict decision failed: %s", exc.reason_code)
            raise AcceptanceReviewError("review_verdict_decision_failed") from exc
        self.last_evidence = evidence_from_decision(decision)
        outcome = getattr(decision, "outcome", None) if decision is not None else None
        result = getattr(outcome, "result", None) if outcome is not None else None
        if result is None:
            if outcome is not None and outcome.disagreed:
                return _blocked(len(criteria), FINDING_DISAGREEMENT)
            stage_outcomes = {getattr(stage, "outcome", "") for stage in (getattr(outcome, "trace", ()) or ())}
            if stage_outcomes & {"low_confidence"}:
                return _blocked(len(criteria), FINDING_ABSTAINED)
            raise AcceptanceReviewError("review_verdict_decision_failed")
        passed = tuple(
            float(result.answers[f"criterion_{index}"].probability or 0.0) >= 0.5 for index in range(len(criteria))
        )
        flagged = float(result.answers["findings_reported"].probability or 0.0) >= 0.5
        findings = (FINDING_REPORTED,) if flagged else ()
        return AcceptanceReviewResult(
            approved=all(passed) and not findings,
            criteria_passed=passed,
            findings=findings,
        )


def evidence_from_decision(decision: Any) -> dict:
    """Bounded decision trace for the item record (scores, never secrets)."""
    if decision is None:
        return {"verdict_decision": "unavailable"}
    outcome = getattr(decision, "outcome", None)
    result = getattr(outcome, "result", None) if outcome is not None else None
    answers: dict = {}
    if result is not None:
        for key, answer in result.answers.items():
            answers[str(key)] = {
                "probability": round(float(getattr(answer, "probability", 0.0) or 0.0), 4),
                "confidence": round(float(getattr(answer, "confidence", 0.0) or 0.0), 4),
            }
    return {
        "verdict_decision": {
            "area": getattr(decision, "area", AREA),
            "mode": getattr(decision, "mode", ""),
            "usable": bool(getattr(decision, "usable", False)),
            "decided_by": getattr(outcome, "decided_by", "") if outcome is not None else "",
            "deferred": bool(getattr(outcome, "deferred", True)) if outcome is not None else True,
            "disagreed": bool(getattr(outcome, "disagreed", False)) if outcome is not None else False,
            "answers": answers,
        }
    }


def build_review_verdict_producer() -> ReviewVerdictProducer | None:
    """Producer active only when the operator opted in via the decision areas.

    Decision-scored review verdicts are their own named area
    (``self_development_review``, mode ``active``) like every other decision
    use; the area's ``cascade`` picks the scoring providers. Without that
    opt-in the review falls back to the strict verdict JSON contract the
    reviewer answers directly.
    """
    from agent.services.decision_providers.service import get_decision_service

    service = get_decision_service()
    if service is None or service.area_mode(AREA) != "active":
        return None
    return DecisionReviewVerdictProducer(service=service)
