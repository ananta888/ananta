from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def build_provider_provenance(
    *,
    provider_id: str,
    provider_family: str,
    provider_version: str | None = None,
    external_ref: str | None = None,
    source_ref: str | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = {
        "provider_id": _clean(provider_id),
        "provider_family": _clean(provider_family),
        "provider_version": _clean(provider_version),
        "external_ref": _clean(external_ref),
        "source_ref": _clean(source_ref),
        "run_id": _clean(run_id),
        "trace_id": _clean(trace_id),
    }
    if not base["provider_id"]:
        raise ValueError("provider_id_required")
    if not base["provider_family"]:
        raise ValueError("provider_family_required")
    payload: dict[str, Any] = {
        "provider_id": base["provider_id"],
        "provider_family": base["provider_family"],
    }
    for key in ("provider_version", "external_ref", "source_ref", "run_id", "trace_id"):
        value = base[key]
        if value is not None:
            payload[key] = value
    if isinstance(extra, dict) and extra:
        payload["extra"] = dict(extra)
    return payload
