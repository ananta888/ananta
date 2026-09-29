"""Schema identifiers, contract error, and bounded validation/redaction primitives for the
Temporal transport contracts.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

WORKFLOW_INPUT_SCHEMA = "ananta.temporal-workflow-input.v1"
WORKFLOW_STEP_SCHEMA = "ananta.temporal-workflow-step.v1"
ACTIVITY_INPUT_SCHEMA = "ananta.temporal-activity-input.v1"
ACTIVITY_RESULT_SCHEMA = "ananta.temporal-activity-result.v1"
LEGACY_COMMAND_SCHEMA = "ananta.workflow_command.v2"
COMMAND_SCHEMA = "ananta.workflow_command.v3"
COMMAND_RESULT_SCHEMA = "ananta.temporal-workflow-command-result.v2"
COMMAND_AUTHORITY_ACTIVITY = "ananta.temporal.verify-workflow-command.v1"
COMMAND_AUTHORITY_RESULT_SCHEMA = "ananta.temporal-command-authority-result.v1"
STATUS_SCHEMA = "ananta.temporal-workflow-status.v1"
PROBE_SCHEMA = "ananta.temporal-probe.v1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_DIGEST_RE = re.compile(r"^(?:sha256:)?[a-fA-F0-9]{64}$")
_COMMAND_SIGNATURE_ALGORITHMS = frozenset({"ed25519", "hmac-sha256"})
_COMMAND_SEMANTIC_PAYLOAD_SCHEMA = "ananta.workflow-command-semantic-payload.v1"
_COMMAND_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "prompt",
        "raw_content",
        "secret",
        "token",
    }
)
_SAFE_TOKEN_KEYS = frozenset(
    {
        "cached_tokens",
        "fencing_token",
        "input_tokens",
        "max_tokens",
        "output_tokens",
        "reasoning_tokens",
        "token_count",
        "token_usage",
    }
)


class TemporalContractError(ValueError):
    """A fail-closed wire-contract validation error with a stable reason."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "invalid_temporal_contract")


def _bounded_integer(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
    reason_code: str,
) -> int:
    """Parse a wire integer without bool coercion, truncation or overflow."""

    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            raise ValueError
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalContractError(
            reason_code,
            f"{field_name} is invalid",
        ) from exc
    if not minimum <= normalized <= maximum:
        raise TemporalContractError(reason_code, f"{field_name} is invalid")
    return normalized


def _bounded_float(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
    reason_code: str,
) -> float:
    """Parse a finite wire float within an explicit interoperable range."""

    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalContractError(
            reason_code,
            f"{field_name} is invalid",
        ) from exc
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise TemporalContractError(reason_code, f"{field_name} is invalid")
    return normalized


def _workflow_command_numeric_fields(
    raw: Mapping[str, Any],
) -> tuple[int, float, float]:
    expected_revision = _bounded_integer(
        raw.get("expected_revision"),
        field_name="expected_revision",
        minimum=0,
        maximum=_COMMAND_MAX_SAFE_INTEGER,
        reason_code="invalid_command_revision",
    )
    issued_at = _bounded_float(
        raw.get("issued_at"),
        field_name="issued_at",
        minimum=0.0,
        maximum=float(_COMMAND_MAX_SAFE_INTEGER),
        reason_code="invalid_command_issued_at",
    )
    expires_at = _bounded_float(
        raw.get("expires_at"),
        field_name="expires_at",
        minimum=0.0,
        maximum=float(_COMMAND_MAX_SAFE_INTEGER),
        reason_code="invalid_command_expires_at",
    )
    if expires_at <= issued_at:
        raise TemporalContractError(
            "invalid_command_expiry",
            "workflow command expiry is invalid",
        )
    return expected_revision, issued_at, expires_at


def _identifier(value: object, *, field_name: str, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if not normalized and not required:
        return ""
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise TemporalContractError("invalid_identifier", f"{field_name} is invalid")
    return normalized


def _bounded_strings(
    value: object,
    *,
    field_name: str,
    maximum: int = 128,
    identifiers: bool = True,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TemporalContractError("invalid_sequence", f"{field_name} must be a sequence")
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if identifiers:
            text = _identifier(text, field_name=field_name)
        elif len(text) > 512 or "\x00" in text:
            raise TemporalContractError("invalid_value", f"{field_name} contains an invalid value")
        if text not in normalized:
            normalized.append(text)
    if len(normalized) > maximum:
        raise TemporalContractError("sequence_too_large", f"{field_name} exceeds {maximum} entries")
    return tuple(normalized)


def _bounded_contract_items(
    value: object,
    *,
    field_name: str,
    maximum: int = 8,
) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TemporalContractError(
            "invalid_sequence",
            f"{field_name} must be a sequence",
        )
    items = tuple(value)
    if len(items) > maximum:
        raise TemporalContractError(
            "sequence_too_large",
            f"{field_name} exceeds {maximum} entries",
        )
    return items


def _mapping(value: object, *, field_name: str, maximum_bytes: int = 32_768) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TemporalContractError("invalid_mapping", f"{field_name} must be an object")
    payload = {str(key): item for key, item in value.items()}
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TemporalContractError("invalid_mapping", f"{field_name} is not JSON serializable") from exc
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise TemporalContractError("mapping_too_large", f"{field_name} exceeds its size limit")
    return payload


def redact_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a deterministic, recursively redacted diagnostic mapping."""

    def _redact(item: Any, key: str = "") -> Any:
        lowered = key.strip().lower()
        if _is_sensitive_key(lowered):
            return "[REDACTED]"
        if isinstance(item, Mapping):
            return {str(k): _redact(v, str(k)) for k, v in sorted(item.items(), key=lambda pair: str(pair[0]))}
        if isinstance(item, (list, tuple)):
            return [_redact(entry) for entry in item[:128]]
        if isinstance(item, str):
            return item[:512]
        if isinstance(item, (bool, int, float)) or item is None:
            return item
        return str(item)[:128]

    return _redact(dict(value or {}))


def _contains_sensitive_keys(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if _is_sensitive_key(key):
                return True
            if _contains_sensitive_keys(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_keys(item) for item in value)
    return False


def _is_sensitive_key(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized not in _SAFE_TOKEN_KEYS and any(
        normalized == part or normalized.endswith(f"_{part}") or normalized.startswith(f"{part}_")
        for part in _SENSITIVE_KEYS
    )
