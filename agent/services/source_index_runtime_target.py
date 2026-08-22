"""Validated worker advertisement for the governed source-index runtime.

The target is configured as one JSON document so a deployment advertises an
atomic, auditable capability. Missing configuration is a safe opt-out;
malformed or insufficiently scoped configuration is rejected.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any


SOURCE_INDEX_RUNTIME_TARGET_ENV = "ANANTA_SOURCE_INDEX_RUNTIME_TARGET_JSON"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MODEL_IDENTIFIER = re.compile(
    r"^(?!.*://)[A-Za-z0-9][A-Za-z0-9._:@/-]{0,190}$"
)
_PROVIDER_LOCATIONS = {
    "local_container",
    "private_network",
    "tenant_region",
    "external_region",
}
_ALLOWED_KEYS = {
    "runtime_target_id",
    "runtime_id",
    "runtime_kind",
    "provider_id",
    "model_provider_id",
    "model_ids",
    "provider_location",
    "data_residency",
    "model_class",
    "source_access_authorized",
    "global_source_access",
    "source_access_scopes",
    "enabled",
    "worker_kind",
}
_REQUIRED_IDENTIFIERS = {
    "runtime_target_id",
    "runtime_id",
    "runtime_kind",
    "provider_id",
}


def _required_identifier(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{key} must be a non-empty deployment identifier")
    return value


def _validate_scopes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("source_access_scopes must be a non-empty list")

    scopes: list[dict[str, str]] = []
    for scope in value:
        if not isinstance(scope, dict) or set(scope) != {"tenant_id", "project_id"}:
            raise ValueError(
                "each source access scope must contain only tenant_id and project_id"
            )
        tenant_id = scope.get("tenant_id")
        project_id = scope.get("project_id")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("source access scope tenant_id must be non-empty")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("source access scope project_id must be non-empty")
        scopes.append(
            {"tenant_id": tenant_id.strip(), "project_id": project_id.strip()}
        )
    return scopes


def load_source_index_runtime_target(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return a validated runtime target, or ``None`` when not configured."""

    environment = os.environ if environ is None else environ
    raw = str(environment.get(SOURCE_INDEX_RUNTIME_TARGET_ENV, "")).strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{SOURCE_INDEX_RUNTIME_TARGET_ENV} must contain valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{SOURCE_INDEX_RUNTIME_TARGET_ENV} must contain an object")

    unexpected = sorted(set(parsed) - _ALLOWED_KEYS)
    if unexpected:
        raise ValueError(f"unsupported runtime target fields: {', '.join(unexpected)}")

    target = dict(parsed)
    for key in _REQUIRED_IDENTIFIERS:
        target[key] = _required_identifier(target, key)

    model_ids = target.get("model_ids")
    if (
        not isinstance(model_ids, list)
        or not model_ids
        or any(
            not isinstance(model_id, str)
            or not _MODEL_IDENTIFIER.fullmatch(model_id)
            for model_id in model_ids
        )
    ):
        raise ValueError("model_ids must be a non-empty list of model identifiers")
    target["model_ids"] = list(dict.fromkeys(model_ids))

    provider_location = target.get("provider_location")
    if provider_location not in _PROVIDER_LOCATIONS:
        raise ValueError(
            "provider_location must be one of "
            + ", ".join(sorted(_PROVIDER_LOCATIONS))
        )
    data_residency = target.get("data_residency")
    if not isinstance(data_residency, str) or not data_residency.strip():
        raise ValueError("data_residency must be non-empty")
    target["data_residency"] = data_residency.strip()

    if target.get("source_access_authorized") is not True:
        raise ValueError("source_access_authorized must explicitly be true")
    global_access = target.get("global_source_access", False)
    if not isinstance(global_access, bool):
        raise ValueError("global_source_access must be a boolean")
    scopes = target.get("source_access_scopes")
    if global_access:
        if scopes is not None:
            target["source_access_scopes"] = _validate_scopes(scopes)
    else:
        target["source_access_scopes"] = _validate_scopes(scopes)

    enabled = target.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    target["enabled"] = enabled

    for optional_identifier in (
        "model_provider_id",
        "model_class",
        "worker_kind",
    ):
        if target.get(optional_identifier) is not None:
            target[optional_identifier] = _required_identifier(
                target, optional_identifier
            )
    return target
