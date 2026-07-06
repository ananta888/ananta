from __future__ import annotations

from ..models import DetectorSignal, EvaluationStatus, Finding
from .avoid_ai_writing_category_map import map_category

UPSTREAM_COMMIT = "f9f8265061eaee1004e9ef86383959163dd2477d"
DETECTOR_SHA256 = "66cc34590ed17d4b7d7c59323b204e2b231e41ed58502ab89fc9753a64c278f5"


def normalize_result(payload: object, *, strict: bool = True) -> DetectorSignal:
    if not isinstance(payload, dict):
        raise ValueError("provider_invalid_response")
    label = str(payload.get("label") or "")
    if label in {"Empty", "Too short", "Text too long"}:
        reason = "text_too_short" if label != "Text too long" else "text_too_long"
        return DetectorSignal(
            provider_name="avoid_ai_writing",
            provider_version=UPSTREAM_COMMIT,
            reason_codes=[reason],
            status=EvaluationStatus.UNSCORABLE,
            metadata={"label": label},
        )
    score = float(payload.get("score") or 0.0)
    if not 0 <= score <= 100:
        raise ValueError("provider_score_out_of_range")
    reasons: list[str] = []
    findings: list[Finding] = []
    for issue in list(payload.get("issues") or [])[:50]:
        if not isinstance(issue, dict):
            continue
        reason = map_category(str(issue.get("type") or ""), strict=strict)
        reasons.append(reason)
        start = max(0, int(issue.get("start") or 0))
        end = max(start, int(issue.get("end") or start))
        findings.append(
            Finding(
                reason_code=reason,
                start=start,
                end=end,
                excerpt=str(issue.get("text") or "")[:160],
                severity=str(issue.get("severity") or "medium"),
            )
        )
    confidence_map = {"low": 0.4, "medium": 0.7, "high": 1.0}
    confidence_category = str(payload.get("confidence_category") or "medium")
    return DetectorSignal(
        provider_name="avoid_ai_writing",
        provider_version=UPSTREAM_COMMIT,
        raw_signal_score=score,
        normalized_signal_score=score / 100.0,
        reason_codes=list(dict.fromkeys(reasons)),
        findings=findings,
        confidence=confidence_map.get(confidence_category, 0.5),
        metadata={
            "label": label,
            "document_classification": payload.get("document_classification"),
            "class_probabilities": payload.get("class_probabilities"),
            "confidence_category": confidence_category,
        },
    )
