from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re

from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.sanitizer import OutputSanitizer


_SENSITIVE_BLOCKS = frozenset({ContextSensitivity.secret, ContextSensitivity.customer_confidential})
_SAN = OutputSanitizer()
_SUSPICIOUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Instruction override (HF-T010)
    ("prompt_injection_ignore_previous", re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE)),
    ("prompt_injection_override_system", re.compile(r"disregard\s+(?:all\s+)?(?:prior|previous|above)\s+(?:instructions?|rules?|context)", re.IGNORECASE)),
    # Hidden HTML comments with instruction text (HF-T010)
    ("prompt_injection_html_comment", re.compile(r"<!--.*?(?:ignore|disregard|override|instructions?).*?-->", re.IGNORECASE | re.DOTALL)),
    # Credential/environment access (HF-T010)
    ("prompt_injection_env_access", re.compile(r"(?:cat|read|open|print)\s+['\"]?\.env['\"]?|(?:os\.environ|process\.env)", re.IGNORECASE)),
    ("prompt_injection_credentials", re.compile(r"(?:cat|read|print)\s+(?:credentials?|secrets?|key(?:file)?s?)\b", re.IGNORECASE)),
    # Exfiltration commands (HF-T010)
    ("prompt_injection_exfiltrate", re.compile(r"exfiltrate\s+secrets?|leak\s+token", re.IGNORECASE)),
    ("prompt_injection_curl_exfil", re.compile(r"(?:curl|wget)\s+.*?(?:\$(?:TOKEN|API_KEY|SECRET|PASSWORD|\{TOKEN\}|\{API_KEY\}|\{SECRET\}))", re.IGNORECASE)),
    # Tool/shell override (HF-T010)
    ("prompt_injection_change_tools", re.compile(r"change\s+tools?|enable\s+shell", re.IGNORECASE)),
    ("prompt_injection_run_command", re.compile(r"\brun\s+(?:this\s+)?command\b|execute\s+command", re.IGNORECASE)),
    # Zero-width and bidi override characters (HF-T010)
    ("prompt_injection_hidden_unicode", re.compile(r"[\u200b\u200c\u200d\u2060]")),
    ("prompt_injection_bidi_override", re.compile(r"[\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200f]")),
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

        label = f"[source={block.source_type}:{block.origin_id}] "
        sanitized_content = _SAN.sanitize(content).text
        entry = label + sanitized_content
        original_chars = len(entry)
        if total + len(entry) > max_context_chars:
            remaining = max_context_chars - total
            suffix = "[truncated_for_budget]"
            content_budget = remaining - len(label) - len(suffix)
            if content_budget <= 0:
                # Not enough room even for label + suffix — skip entirely
                truncated.append({
                    "origin_id": block.origin_id,
                    "reason_code": "budget_exhausted",
                    "original_chars": original_chars,
                    "included_chars": 0,
                })
                continue
            # Keep the provenance label intact; truncate content only (HF-T009)
            entry = label + sanitized_content[:content_budget] + suffix
            truncated.append({
                "origin_id": block.origin_id,
                "reason_code": "truncated_for_budget",
                "original_chars": original_chars,
                "included_chars": len(entry),
            })
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
