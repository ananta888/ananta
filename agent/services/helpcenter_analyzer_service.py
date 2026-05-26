from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent.services.helpcenter_contract_service import validate_helpcenter_analysis, validate_helpcenter_message
from agent.services.helpcenter_log_extractor_service import extract_failure_log_insights


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_helpcenter_analysis_prompt(*, message: dict[str, Any], log_excerpt_lines: list[str] | None = None) -> str:
    payload = {
        "task": "Analyze failure/support input and return HelpcenterAnalysis JSON only.",
        "rules": [
            "analysis only, no automatic fix actions",
            "must set no_auto_fix=true",
            "must include likely_causes, next_steps, source_refs, provenance_refs",
        ],
        "message": dict(message or {}),
        "log_excerpt_lines": [str(item) for item in list(log_excerpt_lines or [])],
        "response_schema": "helpcenter-analysis-v1",
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_llm_analysis_response(response_text: str, *, fallback_message: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(str(response_text or ""))
    except json.JSONDecodeError:
        parsed = {}
    candidate = dict(parsed or {})
    issues = (
        validate_helpcenter_analysis(candidate)
        if candidate
        else [{"reason_code": "invalid_json", "path": "$", "human_message": "invalid json"}]
    )
    if not issues:
        return candidate
    base = analyze_helpcenter_message(fallback_message, log_text="")
    base["status"] = "degraded"
    base["machine_readable_findings"].append(
        {
            "reason_code": "llm_analysis_invalid",
            "details": issues[0]["reason_code"],
        }
    )
    return base


def analyze_helpcenter_message(message: dict[str, Any], *, log_text: str = "") -> dict[str, Any]:
    message_payload = dict(message or {})
    message_issues = validate_helpcenter_message(message_payload)
    if message_issues:
        raise ValueError(f"helpcenter_message_invalid:{message_issues[0]['reason_code']}")
    log_insights = extract_failure_log_insights(log_text, max_lines=200)
    findings = [str(item) for item in list(log_insights.get("detected_patterns") or []) if str(item).strip()]
    likely_causes = {
        "pytest_failure": "test regression or assertion mismatch",
        "npm_failure": "frontend dependency/build failure",
        "type_error": "static typing mismatch",
        "import_error": "missing module or bad import path",
        "lint_error": "style or static checks failing",
        "timeout": "resource exhaustion or stuck process",
        "unknown_failure_pattern": "insufficient structured signal in logs",
    }
    causes = [likely_causes.get(code, "unknown cause") for code in findings]
    failure_summary = str(
        message_payload.get("normalized_summary") or message_payload.get("title") or "failure"
    ).strip()
    source_ref = str(message_payload.get("source_ref") or "").strip()
    source_kind = str(message_payload.get("source_kind") or "").strip()
    analysis = {
        "analysis_id": f"analysis-{uuid4().hex[:12]}",
        "message_id": str(message_payload.get("message_id") or "").strip(),
        "generated_at": _now_iso(),
        "status": "ready",
        "failure_summary": failure_summary,
        "likely_causes": causes,
        "affected_files": [],
        "affected_tasks": [],
        "next_steps": [
            "reproduce failure locally",
            "inspect referenced logs and workflow/job metadata",
            "create follow-up task manually if remediation is required",
        ],
        "confidence": 0.55 if "unknown_failure_pattern" in findings else 0.75,
        "source_refs": [source_ref],
        "provenance_refs": [f"src:{source_kind}:{source_ref}"],
        "machine_readable_findings": [{"reason_code": item} for item in findings],
        "human_summary": f"Detected patterns: {', '.join(findings)}. Automatic repair remains disabled.",
        "no_auto_fix": True,
    }
    issues = validate_helpcenter_analysis(analysis)
    if issues:
        raise ValueError(f"helpcenter_analysis_invalid:{issues[0]['reason_code']}")
    return analysis
