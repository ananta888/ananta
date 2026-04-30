from __future__ import annotations

from typing import Any

SENSITIVITY_CLASSES = {
    "public",
    "internal_low",
    "internal_medium",
    "internal_high",
    "confidential",
    "secret",
    "credential",
    "customer_data",
    "legal",
    "security_sensitive",
}

LLM_SCOPES = {
    "local_only",
    "trusted_private_cloud",
    "external_cloud_allowed",
    "no_llm",
}

_DEFAULT_ALLOWED_BY_SCOPE: dict[str, set[str]] = {
    "local_only": set(SENSITIVITY_CLASSES),
    "trusted_private_cloud": {
        "public",
        "internal_low",
        "internal_medium",
        "internal_high",
        "legal",
    },
    "external_cloud_allowed": {
        "public",
        "internal_low",
    },
    "no_llm": set(),
}


def normalize_sensitivity(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in SENSITIVITY_CLASSES:
        return value
    # Default-deny baseline: unknown nodes are treated as at least internal_medium.
    return "internal_medium"


def normalize_llm_scope(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in LLM_SCOPES:
        return value
    # Unknown provider/scope is treated as external cloud.
    return "external_cloud_allowed"


def is_chunk_allowed_for_scope(*, chunk: dict[str, Any], llm_scope: str) -> tuple[bool, str]:
    scope = normalize_llm_scope(llm_scope)
    metadata = dict(chunk.get("metadata") or {})
    sensitivity = normalize_sensitivity(metadata.get("sensitivity"))
    contains_secrets = bool(metadata.get("contains_secrets"))
    contains_customer_data = bool(metadata.get("contains_customer_data"))
    raw_allowed = metadata.get("raw_allowed")
    raw_allowed = True if raw_allowed is None else bool(raw_allowed)

    if scope == "no_llm":
        return False, "no_llm_scope"

    if sensitivity not in _DEFAULT_ALLOWED_BY_SCOPE.get(scope, set()):
        return False, f"sensitivity_blocked:{sensitivity}"

    if scope in {"external_cloud_allowed", "trusted_private_cloud"} and (contains_secrets or contains_customer_data):
        return False, "secret_or_customer_data_blocked_for_cloud_scope"

    if scope == "external_cloud_allowed" and not raw_allowed:
        return False, "raw_not_allowed_for_external_scope"

    return True, "allowed"
