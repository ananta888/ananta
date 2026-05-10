from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re

from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.sanitizer import OutputSanitizer


_SENSITIVE_BLOCKS = frozenset({ContextSensitivity.secret, ContextSensitivity.confidential})
_SAN = OutputSanitizer()
_SUSPICIOUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("prompt_injection_ignore_previous", re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE)),
    ("prompt_injection_exfiltrate", re.compile(r"exfiltrate\s+secrets?|leak\s+token", re.IGNORECASE)),
    ("prompt_injection_change_tools", re.compile(r"change\s+tools?|enable\s+shell", re.IGNORECASE)),
    ("prompt_injection_run_command", re.compile(r"\brun\s+(?:this\s+)?command\b|execute\s+command", re.IGNORECASE)),
    ("prompt_injection_hidden_unicode", re.compile(r"[\u200b\u200c\u200d\u2060]")),
)


@dataclass
class ContextConversionResult:
    prompt_text: str
    included: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    truncated: list[dict[str, Any]] = field(default_factory=list)
    suspicious: list[dict[str, Any]] = field(default_factory=list)
    total_chars: int = 0
    has_required_context: bool = True


def convert_context_blocks_to_prompt(
    blocks: list[ContextBlock],
    *,
    max_context_chars: int,
    allow_sensitive: bool,
) -> ContextConversionResult:
    lines: list[str] = []
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    truncated: list[dict[str, Any]] = []
    suspicious: list[dict[str, Any]] = []
    total = 0

    for block in blocks:
        if block.sensitivity in _SENSITIVE_BLOCKS and not allow_sensitive:
            skipped.append({"origin_id": block.origin_id, "reason_code": "sensitive_block_excluded"})
            continue
        content = str(block.content or "")
        if not content.strip():
            skipped.append({"origin_id": block.origin_id, "reason_code": "empty_context_block"})
            continue

        finding = _detect_suspicious(content)
        if finding is not None:
            skipped.append({"origin_id": block.origin_id, "reason_code": finding})
            suspicious.append({"origin_id": block.origin_id, "reason_code": finding})
            continue

        entry = f"[source={block.source_type}:{block.origin_id}] {_SAN.sanitize(content).text}"
        if total + len(entry) > max_context_chars:
            remaining = max_context_chars - total
            if remaining <= 0:
                truncated.append({"origin_id": block.origin_id, "reason_code": "budget_exhausted"})
                continue
            entry = entry[:remaining]
            truncated.append({"origin_id": block.origin_id, "reason_code": "truncated_for_budget"})
        lines.append(entry)
        included.append({"origin_id": block.origin_id, "reason_code": "included"})
        total += len(entry)
        if total >= max_context_chars:
            break

    return ContextConversionResult(
        prompt_text="\n".join(lines).strip(),
        included=included,
        skipped=skipped,
        truncated=truncated,
        suspicious=suspicious,
        total_chars=total,
        has_required_context=bool(included),
    )


def _detect_suspicious(content: str) -> str | None:
    for reason, pattern in _SUSPICIOUS_PATTERNS:
        if pattern.search(content):
            return reason
    return None
