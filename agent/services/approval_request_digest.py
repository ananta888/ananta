"""Canonical, digest-bound identity of an approval request (ALWA-DD-001/007).

Pure functions: deterministic argument normalization, content-hash
substitution for content-bearing fields, argument digests and the
tenant/project/goal-bound approval intent key.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CONTENT_BEARING_FIELDS = ("content", "unified_diff")
_DIGEST_PREFIX_LEN = 12


def _normalize_value(value: Any) -> Any:
    """Deterministic normalization: dicts sorted via json, None kept, no NaN."""
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, float) and value != value:  # NaN is not canonicalizable
        return None
    return value


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonicalize_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Return (canonical_arguments, content_payload, content_hash).

    ALWA-DD-007: content-bearing fields are extracted into a payload dict
    and replaced in the canonical arguments by
    ``{"__content_hash__": sha256}`` so the digest stays bound to the
    exact content without persisting it.
    """
    normalized = _normalize_value(dict(arguments or {}))
    payload: dict[str, Any] = {}
    for field in CONTENT_BEARING_FIELDS:
        if field in normalized and isinstance(normalized[field], str) and normalized[field]:
            payload[field] = normalized[field]
            normalized[field] = {"__content_hash__": _sha256_text(payload[field])}
    content_hash = None
    if payload:
        content_hash = _sha256_text(
            json.dumps(_normalize_value(payload), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        )
    return normalized, (payload or None), content_hash


def compute_arguments_digest(
    tool_name: str,
    canonical_arguments: dict[str, Any],
    target_fingerprint: str | None = None,
) -> str:
    canonical_json = json.dumps(
        _normalize_value(canonical_arguments), sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    raw = "\x00".join([str(tool_name or "").strip(), canonical_json, str(target_fingerprint or "")])
    return _sha256_text(raw)


def digest_prefix(digest: str | None) -> str:
    return str(digest or "")[:_DIGEST_PREFIX_LEN]


def canonical_approval_intent_key(
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
    goal_id: str,
    operation: str,
    artifact_revision_id: str,
    artifact_digest: str,
    policy_hash: str,
) -> str:
    fields = (
        tenant_id,
        project_id,
        organization_id,
        goal_id,
        operation,
        artifact_revision_id,
        artifact_digest,
        policy_hash,
    )
    normalized = tuple(str(value or "").strip() for value in fields)
    if any(not value for value in normalized):
        raise ValueError("approval_intent_binding_required")
    return _sha256_text("\x00".join(normalized))
