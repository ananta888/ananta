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

FINDING_CRITERION_UNCONFIRMED = "criterion_unconfirmed"
FINDING_REPORTED = "review_findings_reported"
FINDING_DISAGREEMENT = "review_verdict_decision_disagreed"
FINDING_ABSTAINED = "review_verdict_decision_unconfirmed"

# The two engines agree on the side of 0.5; the threshold only refuses true
# coin flips. Laya's general checkpoints are honestly uncertain on review
# evidence (confidence |2p-1| between 0.1 and 0.35 even on satisfied
# verdicts), so a higher bar would veto every acceptance instead of every
# manipulation.
AGREEMENT_MIN_CONFIDENCE = 0.1

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
    """Verdicts from an agreed decision stage; every anomaly fails closed."""

    def __init__(
        self,
        *,
        stage: Any,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._stage = stage
        self._timeout = timeout_seconds

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
            raise AcceptanceReviewError("review_verdict_criteria_mismatch")
        if len(criteria) + 1 > 32:
            raise AcceptanceReviewError("review_verdict_schema_too_large")
        request = DecisionRequest(
            state=_decision_text(requirements=requirements, changed_files=changed_files, analysis=analysis),
            questions=_questions(criteria),
            purpose="self_development_review",
        )
        try:
            trace, result = self._stage.attempt(request, self._timeout)
        except DecisionProviderError as exc:
            _log.warning("review verdict decision failed: %s", exc.reason_code)
            raise AcceptanceReviewError("review_verdict_decision_failed") from exc
        _log.info(
            "review verdict stage=%s outcome=%s latency_ms=%.0f",
            trace.provider_id,
            trace.outcome,
            trace.latency_ms,
        )
        if result is None:
            if trace.outcome == "disagreement":
                return _blocked(len(criteria), FINDING_DISAGREEMENT)
            if trace.outcome == "low_confidence":
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


def _provider_settings() -> Mapping[str, Any] | None:
    from agent.cli_backends.helpers import _get_agent_config
    from agent.services.decision_providers.config import SECTION, normalize_decision_config

    section = (_get_agent_config() or {}).get(SECTION)
    if not section:
        return None
    return normalize_decision_config(section)


def _build_stage(settings: Mapping[str, Any]) -> Any:
    from agent.services.decision_providers.cascade import AgreementStage, CascadeStage
    from agent.services.decision_providers.service import build_provider

    providers_cfg = settings["providers"]
    names = [name for name in ("laya", "local_decision") if providers_cfg[name]["enabled"]]
    providers = [build_provider(name, providers_cfg[name]) for name in names]
    if len(providers) > 1:
        return AgreementStage(providers, AGREEMENT_MIN_CONFIDENCE)
    return CascadeStage(providers[0], AGREEMENT_MIN_CONFIDENCE)


def build_review_verdict_producer() -> ReviewVerdictProducer | None:
    """Producer active only when the operator opted in via the decision areas.

    Decision-scored review verdicts are their own named area
    (``self_development_review``, mode ``active``) like every other decision
    use. Without that opt-in the review falls back to the strict verdict JSON
    contract the reviewer answers directly.
    """
    settings = _provider_settings()
    if settings is None or not settings["enabled"]:
        return None
    area = (settings.get("areas") or {}).get("self_development_review") or {}
    if str(area.get("mode") or "").strip().lower() != "active":
        return None
    providers_cfg = settings["providers"]
    if not any(providers_cfg[name]["enabled"] for name in ("laya", "local_decision")):
        return None
    return DecisionReviewVerdictProducer(stage=_build_stage(settings))
