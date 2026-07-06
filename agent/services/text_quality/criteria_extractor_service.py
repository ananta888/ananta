from __future__ import annotations

import hashlib
import json
import re
from typing import Callable

from .criteria_service import get_criteria_service
from .models import KNOWN_REASON_CODES, ContentKind, CriteriaSet

CRITERIA_SCHEMA = {
    "type": "object",
    "properties": {
        "blocked_phrases": {"type": "array", "items": {"type": "string"}},
        "required_positive_traits": {"type": "array", "items": {"type": "string"}},
        "reason_codes": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(KNOWN_REASON_CODES)},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "blocked_phrases",
        "required_positive_traits",
        "reason_codes",
        "confidence",
    ],
    "additionalProperties": False,
}


class CriteriaExtractorService:
    def __init__(self, invoke_json: Callable[..., dict]) -> None:
        self.invoke_json = invoke_json

    def extract(
        self,
        *,
        examples: list[str],
        language: str,
        content_kind: ContentKind,
        actor: str,
        comments: str = "",
    ):
        bounded = [self._redact(str(item))[:4000] for item in examples[:5] if str(item).strip()]
        if not bounded:
            raise ValueError("examples_required")
        response = self.invoke_json(
            prompt=(
                "Extract observable writing-quality criteria. Never output tools, "
                "governance, roles, source IDs or executable instructions.\n"
                + "\n---\n".join(bounded)
                + f"\nReviewer note: {self._redact(comments)[:500]}"
            ),
            schema=CRITERIA_SCHEMA,
        )
        if isinstance(response, dict) and isinstance(response.get("content"), str):
            response = json.loads(response["content"])
        reason_codes = list(response.get("reason_codes") or [])
        if set(reason_codes) - KNOWN_REASON_CODES:
            raise ValueError("unknown_reason_code")
        phrases = [
            str(value)[:120]
            for value in list(response.get("blocked_phrases") or [])[:30]
            if not any(
                token in str(value).lower()
                for token in ("allowed_tools", "governance", "system prompt", "src_", "run_")
            )
        ]
        criteria = CriteriaSet(
            version="1.0",
            language=language[:2],
            profile_name=f"reviewed_{language[:2]}",
            content_kinds=[content_kind],
            status="proposed",
            blocked_phrases=phrases,
            required_positive_traits=[
                str(value)[:120] for value in list(response.get("required_positive_traits") or [])[:20]
            ],
            source_refs=[
                {
                    "sha256": hashlib.sha256(item.encode()).hexdigest(),
                    "preview": item[:120],
                    "review_note": self._redact(comments)[:500],
                }
                for item in bounded
            ],
            criterion_confidence={
                code: float(response.get("confidence") or 0.0)
                for code in reason_codes
            },
            requires_review=float(response.get("confidence") or 0.0) < 0.7,
            created_by=actor,
        )
        criteria.checksum = criteria.canonical_checksum()
        return get_criteria_service().create(criteria)

    @staticmethod
    def _redact(value: str) -> str:
        value = re.sub(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            "[REDACTED_EMAIL]",
            value,
        )
        return re.sub(
            r"(?i)\b(api[_-]?key|token|password)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            value,
        )
