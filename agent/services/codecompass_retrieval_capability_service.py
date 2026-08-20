"""Hub-owned resolution and sealing of CodeCompass retrieval authority."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Mapping, Protocol

_DEFAULT_TTL_SECONDS = 900
_DIGEST_FIELDS = (
    "subject_id",
    "tenant_id",
    "workspace_id",
    "repository_id",
    "source_scope",
    "revision",
    "allowed_paths",
    "allowed_index_ids",
    "allowed_signals",
    "issued_at_epoch",
    "expires_at_epoch",
)


class CodeCompassRetrievalCapabilityResolverPort(Protocol):
    def resolve(
        self,
        *,
        principal: Any,
        requested_scope: Mapping[str, Any],
    ) -> Mapping[str, Any] | None: ...


def _canonical_capability(value: Mapping[str, Any]) -> dict[str, Any]:
    canonical = {field: value.get(field) for field in _DIGEST_FIELDS}
    for field in ("allowed_paths", "allowed_index_ids", "allowed_signals"):
        canonical[field] = sorted(
            {str(item).strip() for item in list(canonical.get(field) or []) if str(item).strip()}
        )
    for field in (
        "subject_id",
        "tenant_id",
        "workspace_id",
        "repository_id",
        "source_scope",
        "revision",
    ):
        canonical[field] = str(canonical.get(field) or "").strip()
    canonical["issued_at_epoch"] = int(float(canonical.get("issued_at_epoch") or 0))
    canonical["expires_at_epoch"] = int(float(canonical.get("expires_at_epoch") or 0))
    return canonical


def retrieval_capability_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _canonical_capability(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def bind_retrieval_capability(
    value: Mapping[str, Any],
    *,
    subject_id: str,
    tenant_id: str | None = None,
    now_epoch: float | None = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Bind a resolver result to its authenticated principal and seal it."""

    now = int(time.time() if now_epoch is None else now_epoch)
    raw = dict(value)
    resolved_subject = str(subject_id or "").strip()
    supplied_subject = str(raw.get("subject_id") or "").strip()
    if not resolved_subject or (supplied_subject and supplied_subject != resolved_subject):
        raise ValueError("retrieval_capability_subject_mismatch")
    resolved_tenant = str(tenant_id or raw.get("tenant_id") or "").strip()
    supplied_tenant = str(raw.get("tenant_id") or "").strip()
    if tenant_id and supplied_tenant and supplied_tenant != str(tenant_id).strip():
        raise ValueError("retrieval_capability_tenant_mismatch")
    raw["subject_id"] = resolved_subject
    raw["tenant_id"] = resolved_tenant
    raw["issued_at_epoch"] = int(float(raw.get("issued_at_epoch") or now))
    raw["expires_at_epoch"] = int(
        float(raw.get("expires_at_epoch") or (raw["issued_at_epoch"] + max(1, int(ttl_seconds))))
    )
    canonical = _canonical_capability(raw)
    canonical["capability_digest"] = retrieval_capability_digest(canonical)
    return canonical


def verify_retrieval_capability(
    value: Mapping[str, Any] | None,
    *,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Validate required authority, expiry and mutation-detecting digest."""

    if not isinstance(value, Mapping):
        raise ValueError("retrieval_capability_required")
    canonical = _canonical_capability(value)
    required = (
        "subject_id",
        "tenant_id",
        "workspace_id",
        "repository_id",
        "source_scope",
        "revision",
    )
    if any(not canonical[field] for field in required) or not canonical["allowed_paths"]:
        raise ValueError("retrieval_capability_scope_required")
    now = int(time.time() if now_epoch is None else now_epoch)
    if canonical["issued_at_epoch"] <= 0 or canonical["expires_at_epoch"] <= now:
        raise ValueError("retrieval_capability_expired")
    if canonical["expires_at_epoch"] <= canonical["issued_at_epoch"]:
        raise ValueError("retrieval_capability_lifetime_invalid")
    supplied_digest = str(value.get("capability_digest") or "").strip()
    expected_digest = retrieval_capability_digest(canonical)
    if not supplied_digest or not hmac.compare_digest(supplied_digest, expected_digest):
        raise ValueError("retrieval_capability_digest_invalid")
    canonical["capability_digest"] = expected_digest
    return canonical


def resolve_request_capability(
    *,
    application: Any,
    principal: Any,
    requested_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve authority from a Hub extension; absence always fails closed."""

    resolver = application.extensions.get("codecompass_retrieval_capability_resolver")
    method = getattr(resolver, "resolve", None)
    if not callable(method) or principal is None:
        return None
    resolved = method(principal=principal, requested_scope=dict(requested_scope or {}))
    if not isinstance(resolved, Mapping):
        return None
    try:
        return bind_retrieval_capability(
            resolved,
            subject_id=str(getattr(principal, "subject_id", "") or ""),
            tenant_id=str(getattr(principal, "tenant_id", "") or "") or None,
        )
    except (TypeError, ValueError):
        return None


__all__ = [
    "CodeCompassRetrievalCapabilityResolverPort",
    "bind_retrieval_capability",
    "resolve_request_capability",
    "retrieval_capability_digest",
    "verify_retrieval_capability",
]
