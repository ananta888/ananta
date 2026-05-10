from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from worker.core.sanitizer import OutputSanitizer

_SAN = OutputSanitizer()
_SIDE_EFFECT_CLAIM_RE = re.compile(
    r"\b(modified files?|executed commands?|approval granted|wrote file|applied patch)\b",
    re.IGNORECASE,
)


@dataclass
class HermesParseResult:
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    reason_code: str = "ok"
    parse_retry_used: bool = False
    raw_snippet: str = ""


def parse_hermes_json_output(raw_text: str) -> HermesParseResult:
    text = str(raw_text or "").strip()
    if not text:
        return HermesParseResult(ok=False, reason_code="parse_error_empty")

    # Allow exactly one JSON code fence.
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    if _SIDE_EFFECT_CLAIM_RE.search(text):
        return HermesParseResult(
            ok=False,
            reason_code="parse_error_unsafe_side_effect_claim",
            raw_snippet=_bounded_redacted_snippet(text),
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return HermesParseResult(
            ok=False,
            reason_code="parse_error_malformed_json",
            raw_snippet=_bounded_redacted_snippet(text),
        )

    if not isinstance(payload, dict):
        return HermesParseResult(ok=False, reason_code="parse_error_not_object", raw_snippet=_bounded_redacted_snippet(text))
    return HermesParseResult(ok=True, payload=payload)


def validate_common_payload_schema(payload: dict[str, Any]) -> str | None:
    required = [
        "status",
        "artifact_type",
        "summary",
        "findings",
        "risks",
        "suggested_tests",
        "confidence",
        "requires_approval_for_apply",
        "no_side_effects_claimed",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        return "parse_error_missing_required_fields"
    if payload.get("no_side_effects_claimed") is not True:
        return "parse_error_side_effect_claim"
    return None


def validate_payload_for_mode(payload: dict[str, Any], *, mode: str) -> str | None:
    common_error = validate_common_payload_schema(payload)
    if common_error:
        return common_error
    mode_norm = str(mode).strip().lower()
    if mode_norm == "patch_propose":
        if not payload.get("patch_unified_diff") and not payload.get("patch_description"):
            return "parse_error_patch_payload_missing"
        if "touched_files" not in payload:
            return "parse_error_patch_payload_missing"
    if mode_norm == "review":
        findings = payload.get("findings")
        if not isinstance(findings, list):
            return "parse_error_review_findings_invalid"
    if mode_norm == "research_limited":
        if "claims" not in payload:
            return "parse_error_research_claims_missing"
    return None


def _bounded_redacted_snippet(text: str) -> str:
    clean = _SAN.sanitize(text).text
    return clean[:400]

