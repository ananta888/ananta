"""Validation of file-type registry, coverage and capability evidence in output manifests."""

from __future__ import annotations

from typing import Any

_CAPABILITY_MODES = {
    "indexed": frozenset({"none", "plain_text", "structured"}),
    "symbols": frozenset({"none", "heuristic", "parser_backed"}),
    "relationships": frozenset({"none", "structural", "referential", "semantic"}),
}


def _normalize_capability_claim(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    claim = dict(raw or {})
    effective = str(claim.get("effective") or "none").strip().lower() or "none"
    if effective not in _CAPABILITY_MODES[name]:
        raise ValueError(f"invalid_{name}_effective:{effective}")
    configured = bool(claim.get("configured", False))
    runtime_available = bool(claim.get("runtime_available", False))
    verified = bool(claim.get("verified", False))
    if verified and not configured:
        raise ValueError(f"invalid_{name}_verified_claim")
    if effective != "none" and not (configured and runtime_available and verified):
        raise ValueError(f"invalid_{name}_effective_claim")
    return {
        "configured": configured,
        "runtime_available": runtime_available,
        "verified": verified,
        "effective": effective,
    }


def normalize_file_type_capabilities(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate and deterministically project aggregate file-type capabilities.

    This is an additive view on the canonical VPA output manifest. It does not
    enumerate files or authorize paths. Duplicate type/pipeline aggregates are
    rejected so one run cannot publish competing capability truths.
    """

    raw_entries = list(entries or [])
    if len(raw_entries) > 1_024:
        raise ValueError("file_type_capability_limit_exceeded")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("invalid_file_type_capability")
        detected_type = str(raw.get("detected_type") or "").strip()
        pipeline = str(raw.get("pipeline") or "").strip()
        if not detected_type or not pipeline:
            raise ValueError("missing_file_type_capability_identity")
        identity = (detected_type, pipeline)
        if identity in seen:
            raise ValueError(f"duplicate_file_type_capability:{detected_type}:{pipeline}")
        seen.add(identity)
        claims = {
            name: _normalize_capability_claim(name, dict(raw.get(name) or {}))
            for name in ("indexed", "symbols", "relationships")
        }
        if claims["symbols"]["effective"] != "none" and claims["indexed"]["effective"] == "none":
            raise ValueError("symbols_require_indexed")
        if claims["relationships"]["effective"] != "none" and claims["symbols"]["effective"] == "none":
            raise ValueError("relationships_require_symbols")
        diagnostic_codes = list(raw.get("diagnostic_codes") or [])
        if len(diagnostic_codes) > 256:
            raise ValueError("file_type_diagnostic_limit_exceeded")
        normalized.append(
            {
                "detected_type": detected_type,
                "pipeline": pipeline,
                **claims,
                "parser_id": str(raw.get("parser_id") or "").strip() or None,
                "parser_version": str(raw.get("parser_version") or "").strip() or None,
                "fallback_reason": str(raw.get("fallback_reason") or "").strip() or None,
                "diagnostic_codes": sorted({str(value).strip() for value in diagnostic_codes if str(value).strip()}),
                "file_count": _non_negative_int(raw.get("file_count"), field_name="file_count"),
            }
        )
    return sorted(normalized, key=lambda item: (item["detected_type"], item["pipeline"]))


def normalize_coverage(coverage: dict[str, Any] | None) -> dict[str, Any] | None:
    if coverage is None:
        return None
    raw = dict(coverage or {})
    counts = {
        key: _non_negative_int(raw.get(key), field_name=key)
        for key in ("manifest_candidate_count", "indexed", "excluded", "unsupported", "failed")
    }
    classified = counts["indexed"] + counts["excluded"] + counts["unsupported"] + counts["failed"]
    if classified != counts["manifest_candidate_count"]:
        raise ValueError("coverage_count_mismatch")
    diagnostics = {
        str(key): _non_negative_int(value, field_name="diagnostic_count")
        for key, value in dict(raw.get("diagnostic_counts") or {}).items()
        if str(key).strip()
    }
    return {
        **counts,
        "truncated": bool(raw.get("truncated", False)),
        "diagnostic_counts": dict(sorted(diagnostics.items())),
    }


def normalize_file_type_registry(metadata: dict[str, Any] | None) -> dict[str, str] | None:
    if metadata is None:
        return None
    raw = dict(metadata or {})
    normalized = {
        "schema_version": str(raw.get("schema_version") or "").strip(),
        "registry_version": str(raw.get("registry_version") or "").strip(),
        "snapshot_hash": str(raw.get("snapshot_hash") or "").strip().lower(),
    }
    if not normalized["schema_version"] or not normalized["registry_version"]:
        raise ValueError("invalid_file_type_registry_version")
    if len(normalized["snapshot_hash"]) != 64 or any(
        char not in "0123456789abcdef" for char in normalized["snapshot_hash"]
    ):
        raise ValueError("invalid_file_type_registry_snapshot_hash")
    return normalized


def _non_negative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid_{field_name}")
    return value


def _strict_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"invalid_{field_name}")
    return value
