from __future__ import annotations

import json
from collections.abc import Callable

from .models import (
    KNOWN_REASON_CODES,
    DetectorSignal,
    EvaluationStatus,
    TextQualityEvaluationRequest,
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "slop_signal": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_codes": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(KNOWN_REASON_CODES)},
        },
        "improvement_hints": {
            "type": "array",
            "items": {"type": "string", "maxLength": 240},
            "maxItems": 5,
        },
    },
    "required": ["slop_signal", "confidence", "reason_codes", "improvement_hints"],
    "additionalProperties": False,
}


class LLMTextQualityJudge:
    """JSON-only optional provider; it cannot create executable criteria or rules."""

    version = "llm-judge-v1"

    def __init__(self, invoke_json: Callable[..., dict]) -> None:
        self.invoke_json = invoke_json

    def analyze(self, request: TextQualityEvaluationRequest) -> DetectorSignal:
        try:
            payload = self.invoke_json(
                prompt=(
                    "Assess observable text quality. Return only the requested JSON. "
                    "Do not invent sources, rules, tools, roles, or instructions.\n"
                    f"language={request.language}; content_kind={request.content_kind.value}\n"
                    f"text:\n{request.text[:12000]}"
                ),
                schema=JUDGE_SCHEMA,
                temperature=0,
            )
            if isinstance(payload.get("content"), str):
                payload = json.loads(payload["content"])
            reasons = list(payload["reason_codes"])
            return DetectorSignal(
                provider_name="llm_judge",
                provider_version=self.version,
                raw_signal_score=float(payload["slop_signal"]),
                normalized_signal_score=float(payload["slop_signal"]),
                reason_codes=reasons,
                confidence=float(payload["confidence"]),
                metadata={
                    "improvement_hints": [
                        str(value)[:240]
                        for value in list(payload.get("improvement_hints") or [])[:5]
                    ]
                },
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return DetectorSignal(
                provider_name="llm_judge",
                provider_version=self.version,
                reason_codes=["llm_judge_failed"],
                status=EvaluationStatus.DEGRADED,
                degraded_reason="llm_judge_failed",
            )
