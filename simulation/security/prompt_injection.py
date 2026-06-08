"""Prompt Injection Protection (SIM-035)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Patterns that suggest injection in LLM-generated text
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"forget\s+your\s+(instructions|context|role)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a\s+)?(?!an?\s+agent)", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+are", re.IGNORECASE),
    re.compile(r"system\s*:\s*override", re.IGNORECASE),
    re.compile(r"\[INST\]|\[\/INST\]|<\|im_start\|>|<\|im_end\|>"),
    re.compile(r"<system>|<\/system>"),
    re.compile(r"DAN\s+mode|jailbreak", re.IGNORECASE),
]

_MAX_REASON_LENGTH = 200
_MAX_ARG_VALUE_LENGTH = 500


@dataclass
class InjectionScanResult:
    safe: bool
    violations: list[str]


def scan_text(text: str) -> InjectionScanResult:
    violations = [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]
    return InjectionScanResult(safe=not violations, violations=violations)


def sanitize_proposal(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip or truncate fields that could carry injected instructions."""
    sanitized = dict(raw)

    # Truncate reason
    if isinstance(sanitized.get("reason"), str):
        sanitized["reason"] = sanitized["reason"][:_MAX_REASON_LENGTH]

    # Sanitize args values
    if isinstance(sanitized.get("args"), dict):
        clean_args: dict[str, Any] = {}
        for k, v in sanitized["args"].items():
            if isinstance(v, str):
                result = scan_text(v)
                if not result.safe:
                    v = "[REDACTED:injection_detected]"
                else:
                    v = v[:_MAX_ARG_VALUE_LENGTH]
            clean_args[k] = v
        sanitized["args"] = clean_args

    # Check reason for injection
    if isinstance(sanitized.get("reason"), str):
        if not scan_text(sanitized["reason"]).safe:
            sanitized["reason"] = "[REDACTED:injection_detected]"

    return sanitized


class PromptInjectionGuard:
    """Wraps adapter calls to screen output before parsing."""

    def scan_response(self, raw_text: str, agent_id: str) -> InjectionScanResult:
        return scan_text(raw_text)

    def sanitize(self, proposal_dict: dict[str, Any]) -> dict[str, Any]:
        return sanitize_proposal(proposal_dict)
