"""Decision-scored verdict production for the self-development acceptance review.

The decision service (``POST /v1/decision``) scores every verdict field as a
decision tree against the review evidence, so the verdict JSON is assembled from
scored booleans instead of generated free text: its shape is guaranteed and a
worker answer that is not valid JSON can no longer block a review round. Values
stay fail-closed: an unconfirmed field never passes a criterion, and approval is
derived only from confirmed criteria plus a confirmed absence of findings.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.services.self_development.acceptance_review import (
    AcceptanceReviewError,
    AcceptanceReviewResult,
)
from agent.services.vision_decision_provider import (
    MAX_FIELDS,
    MAX_TEXT_CHARS,
    VisionContext,
    VisionDecisionSchema,
)

VERDICT_TASK_ID = "self_development_review_verdict"

FINDING_CRITERION_UNCONFIRMED = "criterion_unconfirmed"
FINDING_REPORTED = "review_findings_reported"
FINDING_UNCONFIRMED = "review_findings_unconfirmed"

_REQUIREMENTS_CHARS = 600
_FILES_CHARS = 350
_ANALYSIS_CHARS = 2_700

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


def _verdict_schema(criteria: Sequence[str]) -> VisionDecisionSchema:
    spec: dict[str, dict[str, Any]] = {
        f"criterion_{index}": {
            "type": "boolean",
            "description": (
                f"Acceptance criterion {index}: {text} "
                "Is this criterion satisfied by the changed files and the analysis?"
            ),
        }
        for index, text in enumerate(criteria)
    }
    spec["no_findings"] = {
        "type": "boolean",
        "description": (
            "Does the analysis report no blocking findings, issues or objections about the change?"
        ),
    }
    return VisionDecisionSchema.from_mapping(VERDICT_TASK_ID, spec)


def _decision_text(
    *, requirements: Mapping[str, Any], changed_files: Mapping[str, str], analysis: str
) -> str:
    parts = [
        "Independent acceptance review of one change (decision scoring).",
        "Task requirements (JSON):",
        json.dumps(dict(requirements), ensure_ascii=False, sort_keys=True)[:_REQUIREMENTS_CHARS],
        "Changed files:",
        "\n".join(f"- {path}" for path in sorted(changed_files))[:_FILES_CHARS],
        "Reviewer analysis:",
        str(analysis or "")[:_ANALYSIS_CHARS],
    ]
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def _default_runtime_loader() -> Any:
    from agent.services.vision_decision_hub_service import get_vision_hub_runtime

    return get_vision_hub_runtime()


class DecisionReviewVerdictProducer:
    """VisionDecision-backed producer; every anomaly fails closed."""

    def __init__(
        self,
        *,
        runtime_loader: Callable[[], Any] | None = None,
    ) -> None:
        self._runtime_loader = runtime_loader or _default_runtime_loader

    def produce(
        self,
        *,
        requirements: Mapping[str, Any],
        changed_files: Mapping[str, str],
        analysis: str,
    ) -> AcceptanceReviewResult:
        try:
            return self._produce(
                requirements=requirements, changed_files=changed_files, analysis=analysis
            )
        except AcceptanceReviewError:
            raise
        except Exception as exc:
            _log.exception("review verdict decision scoring failed")
            raise AcceptanceReviewError("review_verdict_decision_failed") from exc

    def _produce(
        self,
        *,
        requirements: Mapping[str, Any],
        changed_files: Mapping[str, str],
        analysis: str,
    ) -> AcceptanceReviewResult:
        criteria = _criteria(requirements)
        if not criteria:
            raise AcceptanceReviewError("review_verdict_criteria_mismatch")
        if len(criteria) + 1 > MAX_FIELDS:
            raise AcceptanceReviewError("review_verdict_schema_too_large")
        runtime = self._runtime_loader()
        if runtime is None:
            raise AcceptanceReviewError("review_verdict_decision_failed")
        outcome = runtime.provider.decide(
            _verdict_schema(criteria),
            [
                VisionContext(
                    images=(),
                    text=_decision_text(
                        requirements=requirements, changed_files=changed_files, analysis=analysis
                    ),
                )
            ],
            require_media=False,
        )
        if not outcome.ok or not outcome.results:
            raise AcceptanceReviewError("review_verdict_decision_failed")
        fields = outcome.results[0].fields
        passed: list[bool] = []
        findings: list[str] = []
        for index in range(len(criteria)):
            field = fields.get(f"criterion_{index}")
            if field is None or not field.accepted:
                passed.append(False)
                findings.append(f"{FINDING_CRITERION_UNCONFIRMED}:{index}")
            else:
                passed.append(field.value is True)
        clean = fields.get("no_findings")
        if clean is None or not clean.accepted:
            findings.append(FINDING_UNCONFIRMED)
        elif clean.value is not True:
            findings.append(FINDING_REPORTED)
        return AcceptanceReviewResult(
            approved=all(passed) and not findings,
            criteria_passed=tuple(passed),
            findings=tuple(findings),
        )


def build_review_verdict_producer() -> ReviewVerdictProducer | None:
    """Producer active only when the operator enabled the verdict task; misconfiguration fails loud."""
    runtime = _default_runtime_loader()
    if runtime is None:
        return None
    config = runtime.provider.config
    if config.schemas and VERDICT_TASK_ID not in config.schemas:
        return None
    return DecisionReviewVerdictProducer()
