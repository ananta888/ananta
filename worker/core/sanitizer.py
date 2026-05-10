"""Tool output sanitizer and secret redaction pipeline.

EW-T017: Masks API keys, tokens, auth headers, private keys, .env values, DB passwords,
          SSH keys before model-visible output, audit log, UI, and artifact publication.
          Redaction preserves enough shape for debugging without exposing secret value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Secret patterns ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SecretPattern:
    name: str
    pattern: re.Pattern[str]
    replacement: str


_PATTERNS: list[SecretPattern] = [
    SecretPattern("openai_key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED:openai_key]"),
    SecretPattern("anthropic_key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED:anthropic_key]"),
    SecretPattern("github_pat",
        re.compile(r"\bgh[op]_[A-Za-z0-9]{20,}\b"),
        "[REDACTED:github_pat]"),
    SecretPattern("aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED:aws_access_key]"),
    SecretPattern("aws_secret",
        re.compile(r"(?i)\baws[_-]secret[_-]access[_-]key\s*[=:]\s*\S+"),
        "[REDACTED:aws_secret]"),
    SecretPattern("bearer_token",
        re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+\S+"),
        "Authorization: Bearer [REDACTED:token]"),
    SecretPattern("basic_auth",
        re.compile(r"(?i)\bAuthorization\s*:\s*Basic\s+[A-Za-z0-9+/=]{8,}"),
        "Authorization: Basic [REDACTED:basic_auth]"),
    SecretPattern("private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "[REDACTED:private_key]"),
    SecretPattern("env_secret",
        re.compile(
            r"(?i)(?:api[_-]?key|secret|token|password|passwd|pwd|auth|credential)\s*[=:]\s*['\"]?[^\s'\",;>]{6,}['\"]?",
        ),
        lambda m: _mask_env_value(m.group(0))),  # type: ignore[arg-type]
    SecretPattern("db_connection_string",
        re.compile(r"(?i)(?:postgres|mysql|mongodb|redis)://[^\s\"']+"),
        "[REDACTED:db_connection_string]"),
    SecretPattern("ssh_private_key_line",
        re.compile(r"(?m)^[A-Za-z0-9+/]{60,}={0,2}$"),
        "[REDACTED:key_material]"),
]


def _mask_env_value(match_text: str) -> str:
    """Keep the key name, replace value with [REDACTED:value]."""
    sep_match = re.search(r"[=:]", match_text)
    if sep_match:
        key_part = match_text[:sep_match.end()]
        return f"{key_part}[REDACTED:value]"
    return "[REDACTED:env_secret]"


# ── SanitizationResult ────────────────────────────────────────────────────────

@dataclass
class SanitizationResult:
    text: str
    redactions: list[str] = field(default_factory=list)

    @property
    def was_redacted(self) -> bool:
        return bool(self.redactions)


# ── Sanitizer ─────────────────────────────────────────────────────────────────

class OutputSanitizer:
    """Pipeline that runs all secret patterns over tool/model output.

    Must be called before:
      - output is injected into model context
      - output is written to audit log
      - output is shown in UI
      - output is published as artifact
    """

    def sanitize(self, text: str) -> SanitizationResult:
        if not text:
            return SanitizationResult(text="")
        result = str(text)
        redactions: list[str] = []
        for sp in _PATTERNS:
            if callable(sp.replacement):
                replaced, count = re.subn(sp.pattern, sp.replacement, result)
            else:
                replaced, count = re.subn(sp.pattern, sp.replacement, result)
            if count:
                redactions.append(f"{sp.name}({count})")
                result = replaced
        return SanitizationResult(text=result, redactions=redactions)

    def sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively sanitize all string values in a dict."""
        return _walk(data, self.sanitize)

    def sanitize_env(self, env: dict[str, str], sensitive_keys: set[str]) -> dict[str, str]:
        """Redact values for sensitive environment variable keys."""
        return {
            k: ("[REDACTED:env]" if k.upper() in {s.upper() for s in sensitive_keys} else v)
            for k, v in env.items()
        }


def _walk(obj: Any, sanitize_fn: Any) -> Any:
    if isinstance(obj, str):
        return sanitize_fn(obj).text
    if isinstance(obj, dict):
        return {k: _walk(v, sanitize_fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(item, sanitize_fn) for item in obj]
    return obj


# ── Module-level singleton ─────────────────────────────────────────────────────

_DEFAULT_SANITIZER = OutputSanitizer()


def sanitize(text: str) -> SanitizationResult:
    """Convenience wrapper using the default sanitizer."""
    return _DEFAULT_SANITIZER.sanitize(text)


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    return _DEFAULT_SANITIZER.sanitize_dict(data)
