"""Content-free observability contract for semantic media and speech paths."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

PUBLIC_REASON_CODES = frozenset(
    {
        "accepted",
        "capability_disabled",
        "consent_required",
        "contract_expired",
        "dependency_unavailable",
        "invalid_contract",
        "lease_fenced",
        "policy_denied",
        "quota_exceeded",
        "revoked",
        "stale_epoch",
        "transport_degraded",
    }
)

FORBIDDEN_FIELD_FRAGMENTS = frozenset(
    {
        "audio",
        "image",
        "pixel",
        "transcript",
        "feature",
        "embedding",
        "key",
        "secret",
        "token",
        "path",
        "partner",
        "peer_id",
        "speaker",
        "payload",
        "content",
    }
)


@dataclass(frozen=True, slots=True)
class EventRule:
    allowed_fields: frozenset[str]
    max_serialized_bytes: int = 1024
    max_string_chars: int = 96
    max_distinct_values_per_window: int = 128


EVENT_RULES: dict[str, EventRule] = {
    "semantic_transport": EventRule(
        frozenset({"reason_code", "direction", "transport", "state", "duration_ms", "item_count", "scope_digest"})
    ),
    "semantic_control": EventRule(
        frozenset({"reason_code", "operation", "state", "duration_ms", "item_count", "scope_digest"})
    ),
    "semantic_worker": EventRule(
        frozenset({"reason_code", "worker_kind", "state", "duration_ms", "item_count", "scope_digest"})
    ),
    "semantic_privacy": EventRule(frozenset({"reason_code", "operation", "state", "item_count", "scope_digest"})),
}


class ObservabilityPolicyError(ValueError):
    def __init__(self, reason_code: str, field: str = "") -> None:
        super().__init__(f"{reason_code}:{field}" if field else reason_code)
        self.reason_code = reason_code
        self.field = field


def _is_safe_field_name(field: str) -> bool:
    normalized = field.casefold()
    return not any(fragment in normalized for fragment in FORBIDDEN_FIELD_FRAGMENTS)


def _normalize_scalar(value: Any, *, max_chars: int) -> str | int | float | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ObservabilityPolicyError("invalid_metric_value")
        return value
    if isinstance(value, str) and len(value) <= max_chars and "\n" not in value and "\r" not in value:
        return value
    raise ObservabilityPolicyError("unsafe_observability_value")


def sanitize_observability_event(event_type: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    rule = EVENT_RULES.get(str(event_type or ""))
    if rule is None:
        raise ObservabilityPolicyError("unknown_observability_event")
    unknown = sorted(set(fields) - set(rule.allowed_fields))
    if unknown:
        raise ObservabilityPolicyError("field_not_allowed", unknown[0])
    unsafe = sorted(field for field in fields if not _is_safe_field_name(field))
    if unsafe:
        raise ObservabilityPolicyError("content_field_forbidden", unsafe[0])
    sanitized = {field: _normalize_scalar(value, max_chars=rule.max_string_chars) for field, value in fields.items()}
    reason = sanitized.get("reason_code")
    if reason is not None and reason not in PUBLIC_REASON_CODES:
        raise ObservabilityPolicyError("unknown_public_reason_code", str(reason))
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > rule.max_serialized_bytes:
        raise ObservabilityPolicyError("observability_event_too_large")
    return {"event_type": event_type, **sanitized}


def scope_digest(scope: str, *, secret: bytes, now_seconds: float, ttl_seconds: int = 3600) -> str:
    """Produce an epoch-bound, non-reversible digest for bounded correlation."""

    if not secret or ttl_seconds < 60:
        raise ObservabilityPolicyError("invalid_scope_digest_configuration")
    epoch = int(now_seconds) // ttl_seconds
    digest = hmac.new(secret, f"{epoch}:{scope}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    return f"v1.{epoch}.{digest}"


__all__ = [
    "EVENT_RULES",
    "FORBIDDEN_FIELD_FRAGMENTS",
    "ObservabilityPolicyError",
    "PUBLIC_REASON_CODES",
    "sanitize_observability_event",
    "scope_digest",
]
