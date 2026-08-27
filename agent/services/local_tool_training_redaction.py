"""Fail-closed secret and unnecessary-PII gate for tool-learning facts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


class ToolTrainingRedactionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class LocalToolTrainingRedactionPolicy:
    VERSION = "local-tool-training-redaction-v1"
    _SENSITIVE_KEYS = frozenset(
        {
            "api_key",
            "apikey",
            "authorization",
            "bearer_token",
            "credential",
            "credentials",
            "password",
            "private_key",
            "secret",
            "token",
        }
    )
    _SECRET_PATTERNS = (
        re.compile(r"\b(?:api[-_]?key|password|secret|token|credential)\s*[:=]\s*\S{4,}", re.I),
        re.compile(r"\bbearer\s+\S{12,}", re.I),
        re.compile(r"-----BEGIN(?: RSA)? PRIVATE KEY-----"),
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    )
    _PII_PATTERNS = (
        re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I),
        re.compile(r"(?<!\w)(?:\+?\d[\d ()/.-]{7,}\d)(?!\w)"),
        re.compile(r"\b(?:SSN|Sozialversicherungsnummer)\s*[:=]\s*[A-Z0-9 -]{6,}", re.I),
    )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.VERSION.encode()).hexdigest()

    def sanitize_arguments(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or len(value) > 64:
            raise ToolTrainingRedactionError("arguments_invalid")
        return self._sanitize_mapping(value, depth=0)

    def _sanitize_mapping(self, value: Mapping[str, Any], *, depth: int) -> dict[str, Any]:
        if depth > 4:
            raise ToolTrainingRedactionError("arguments_too_deep")
        result: dict[str, Any] = {}
        for raw_key, child in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key).strip().lower()
            if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", key):
                raise ToolTrainingRedactionError("argument_key_invalid")
            if key in self._SENSITIVE_KEYS or any(
                marker in key for marker in ("password", "secret", "token", "credential")
            ):
                raise ToolTrainingRedactionError("secret_field_blocked")
            result[key] = self._sanitize_value(child, depth=depth + 1)
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 16_384:
            raise ToolTrainingRedactionError("arguments_too_large")
        return result

    def _sanitize_value(self, value: Any, *, depth: int) -> Any:
        if isinstance(value, Mapping):
            return self._sanitize_mapping(value, depth=depth)
        if isinstance(value, list):
            if len(value) > 64:
                raise ToolTrainingRedactionError("argument_list_too_large")
            return [self._sanitize_value(item, depth=depth + 1) for item in value]
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            if value != value or value in {float("inf"), float("-inf")}:
                raise ToolTrainingRedactionError("argument_number_invalid")
            return value
        if not isinstance(value, str) or len(value) > 2048:
            raise ToolTrainingRedactionError("argument_value_invalid")
        if any(pattern.search(value) for pattern in self._SECRET_PATTERNS):
            raise ToolTrainingRedactionError("secret_value_blocked")
        if any(pattern.search(value) for pattern in self._PII_PATTERNS):
            raise ToolTrainingRedactionError("pii_value_blocked")
        return value


__all__ = ["LocalToolTrainingRedactionPolicy", "ToolTrainingRedactionError"]
