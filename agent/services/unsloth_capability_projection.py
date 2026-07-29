"""Pure read-model projection for discovered Unsloth capabilities."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_OPERATIONS = frozenset({"cleanup", "export", "runtime_handoff", "mcp"})


def project_unsloth_capabilities(
    snapshot: Mapping[str, Any] | None,
    *,
    executable_operations: Sequence[str],
) -> dict[str, Any]:
    """Project the generic contract snapshot into the additive UI read model."""

    raw = dict(snapshot or {})
    raw_facets = raw.get("facets")
    facets: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw_facets, list):
        for candidate in raw_facets:
            if not isinstance(candidate, Mapping):
                continue
            facet_id = str(candidate.get("id") or "")
            if facet_id and facet_id not in facets:
                facets[facet_id] = candidate
    executable = frozenset(operation for operation in executable_operations if operation in _OPERATIONS)
    detected_version = _bounded_metadata(raw.get("detected_version"))
    detected_variant = _bounded_metadata(raw.get("detected_variant"))
    operating_mode = _bounded_metadata(raw.get("operating_mode")) or "unknown"

    core = _project_facet(
        facets.get("training.text"),
        missing_reason="unsloth_core_capability_unavailable",
        version=detected_version,
    )
    studio = _project_facet(
        facets.get("studio.management"),
        missing_reason="unsloth_studio_capability_unavailable",
        version=detected_version,
    )
    mcp = _project_facet(
        facets.get("mcp.control"),
        missing_reason="unsloth_mcp_capability_unavailable",
        version=detected_version,
    )
    modalities = {
        modality: _project_facet(
            facets.get(f"training.{modality}"),
            missing_reason=f"unsloth_{modality}_capability_unavailable",
            version=detected_version,
        )
        for modality in ("text", "vision", "audio", "embedding")
    }
    release_available = bool(detected_version) and bool(core["available"])
    release_profile = {
        "name": detected_variant or operating_mode,
        "available": release_available,
        "reason_code": (None if release_available else "unsloth_release_profile_unavailable"),
        "version": detected_version,
    }
    operations = {
        "export": _project_operation(
            "export",
            facets.get("export.adapter"),
            executable,
            missing_reason="unsloth_export_capability_unavailable",
        ),
        "runtime_handoff": {
            "available": "runtime_handoff" in executable,
            "reason_code": (
                None if "runtime_handoff" in executable else "unsloth_runtime_handoff_composition_unavailable"
            ),
            "version": "ananta.runtime-handoff.v2" if "runtime_handoff" in executable else None,
        },
        "mcp": _project_operation(
            "mcp",
            facets.get("mcp.control"),
            executable,
            missing_reason="unsloth_mcp_composition_unavailable",
        ),
        "cleanup": {
            "available": "cleanup" in executable,
            "reason_code": (None if "cleanup" in executable else "unsloth_cleanup_composition_unavailable"),
            "version": ("ananta.unsloth-storage-cleanup-task.v1" if "cleanup" in executable else None),
        },
    }
    return {
        "core": core,
        "studio": studio,
        "modalities": modalities,
        "mcp": mcp,
        "release_profile": release_profile,
        "operations": operations,
    }


def _project_facet(
    facet: Mapping[str, Any] | None,
    *,
    missing_reason: str,
    version: str | None,
) -> dict[str, Any]:
    if not isinstance(facet, Mapping) or not isinstance(facet.get("available"), bool):
        return {
            "available": False,
            "reason_code": missing_reason,
            "version": version,
        }
    available = facet["available"] is True
    reason = facet.get("reason_code")
    if available and reason is not None:
        return {
            "available": False,
            "reason_code": "unsloth_capability_contract_invalid",
            "version": version,
        }
    if not available and not str(reason or "").strip():
        reason = missing_reason
    return {
        "available": available,
        "reason_code": None if available else str(reason),
        "version": version,
    }


def _project_operation(
    operation: str,
    facet: Mapping[str, Any] | None,
    executable: frozenset[str],
    *,
    missing_reason: str,
) -> dict[str, Any]:
    projected = _project_facet(facet, missing_reason=missing_reason, version=None)
    if not projected["available"]:
        return projected
    if operation not in executable:
        return {
            "available": False,
            "reason_code": f"unsloth_{operation}_composition_unavailable",
            "version": None,
        }
    return projected


def _bounded_metadata(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized if 1 <= len(normalized) <= 128 else None
