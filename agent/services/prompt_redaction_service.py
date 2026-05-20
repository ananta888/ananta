"""PromptRedactionService: mask secrets in prompts before storage/display. PTI-012."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RedactionResult:
    redacted_text: str
    secrets_detected: int
    redaction_count: int
    pattern_ids: list[str] = field(default_factory=list)


# Each entry: (pattern_id, compiled_regex, replacement_template)
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("bearer_token",      re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), "[REDACTED:bearer_token]"),
    ("authorization_header", re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), "Authorization: [REDACTED:bearer_token]"),
    ("openai_api_key",    re.compile(r"OPENAI_API_KEY\s*[=:]\s*[A-Za-z0-9\-._]+", re.IGNORECASE), "OPENAI_API_KEY=[REDACTED:api_key]"),
    ("anthropic_api_key", re.compile(r"ANTHROPIC_API_KEY\s*[=:]\s*[A-Za-z0-9\-._]+", re.IGNORECASE), "ANTHROPIC_API_KEY=[REDACTED:api_key]"),
    ("hermes_api_key",    re.compile(r"HERMES_API_KEY\s*[=:]\s*[A-Za-z0-9\-._]+", re.IGNORECASE), "HERMES_API_KEY=[REDACTED:api_key]"),
    ("openrouter_api_key", re.compile(r"OPENROUTER_API_KEY\s*[=:]\s*[A-Za-z0-9\-._]+", re.IGNORECASE), "OPENROUTER_API_KEY=[REDACTED:api_key]"),
    ("generic_api_key",   re.compile(r"api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9\-._]{8,}['\"]?", re.IGNORECASE), "api_key=[REDACTED:api_key]"),
    ("generic_token",     re.compile(r"(?<![a-z])token\s*[=:]\s*['\"]?[A-Za-z0-9\-._]{8,}['\"]?", re.IGNORECASE), "token=[REDACTED:token]"),
    ("password_field",    re.compile(r"password\s*[=:]\s*['\"]?[^\s'\"]{4,}['\"]?", re.IGNORECASE), "password=[REDACTED:password]"),
    ("secret_field",      re.compile(r"(?<![a-z])secret\s*[=:]\s*['\"]?[A-Za-z0-9\-._]{4,}['\"]?", re.IGNORECASE), "secret=[REDACTED:secret]"),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL | re.IGNORECASE), "[REDACTED:private_key]"),
    ("sk_key",            re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED:sk_key]"),
    ("env_secret",        re.compile(r"[A-Z][A-Z0-9_]*SECRET[A-Z0-9_]*\s*[=:]\s*\S+", re.IGNORECASE), "[REDACTED:env_secret]"),
]


class PromptRedactionService:
    def redact(self, text: str | None) -> RedactionResult:
        if not text:
            return RedactionResult(redacted_text=text or "", secrets_detected=0, redaction_count=0)

        result = text
        secrets_detected = 0
        redaction_count = 0
        pattern_ids: list[str] = []

        for pattern_id, regex, replacement in _PATTERNS:
            new_result, n = regex.subn(replacement, result)
            if n > 0:
                redaction_count += n
                secrets_detected += n
                pattern_ids.append(pattern_id)
                result = new_result

        return RedactionResult(
            redacted_text=result,
            secrets_detected=secrets_detected,
            redaction_count=redaction_count,
            pattern_ids=list(set(pattern_ids)),
        )

    def redact_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact string values in a dict."""
        out: dict[str, Any] = {}
        for k, v in d.items():
            if k.lower() in {"authorization", "api_key", "apikey", "token", "password", "secret"}:
                out[k] = "[REDACTED]"
            elif isinstance(v, str):
                out[k] = self.redact(v).redacted_text
            elif isinstance(v, dict):
                out[k] = self.redact_dict(v)
            elif isinstance(v, list):
                out[k] = [self.redact_dict(i) if isinstance(i, dict) else (self.redact(i).redacted_text if isinstance(i, str) else i) for i in v]
            else:
                out[k] = v
        return out


_SVC: PromptRedactionService | None = None


def get_redaction_service() -> PromptRedactionService:
    global _SVC
    if _SVC is None:
        _SVC = PromptRedactionService()
    return _SVC
