from __future__ import annotations

from typing import Any


def normalize_text_quality_config(value: object) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    external = raw.get("external_detectors")
    external = dict(external) if isinstance(external, dict) else {}
    avoid = external.get("avoid_ai_writing")
    avoid = dict(avoid) if isinstance(avoid, dict) else {}
    context_mode = str(avoid.get("context_mode") or "technical")
    if context_mode not in {"general", "technical"}:
        context_mode = "technical"
    return {
        "enabled": bool(raw.get("enabled", False)),
        "evaluate_planning_outputs": bool(raw.get("evaluate_planning_outputs", False)),
        "llm_judge_enabled": bool(raw.get("llm_judge_enabled", False)),
        "rewrite_enabled": bool(raw.get("rewrite_enabled", False)),
        "default_profile": str(raw.get("default_profile") or "critical_editor_de")[:80],
        "max_input_chars": _bounded_int(raw.get("max_input_chars"), 12000, 100, 100000),
        "max_input_words": _bounded_int(raw.get("max_input_words"), 2500, 20, 20000),
        "max_output_bytes": _bounded_int(raw.get("max_output_bytes"), 262144, 1024, 1048576),
        "max_slop_score": _bounded_float(raw.get("max_slop_score"), 0.35),
        "min_depth_score": _bounded_float(raw.get("min_depth_score"), 0.7),
        "min_canary_runs": _bounded_int(raw.get("min_canary_runs"), 10, 3, 10000),
        "external_detectors": {
            "avoid_ai_writing": {
                "enabled": bool(avoid.get("enabled", False)),
                "execution_plane": "sandbox",
                "network": "none",
                "pinned_commit": str(avoid.get("pinned_commit") or "") or None,
                "detector_sha256": str(avoid.get("detector_sha256") or "") or None,
                "context_mode": context_mode,
            }
        },
    }


def text_quality_read_model(value: object) -> dict[str, Any]:
    normalized = normalize_text_quality_config(value)
    provider = normalized["external_detectors"]["avoid_ai_writing"]
    return {
        **{key: val for key, val in normalized.items() if key != "external_detectors"},
        "external_detectors": {
            "avoid_ai_writing": {
                "enabled": provider["enabled"],
                "execution_plane": provider["execution_plane"],
                "network": provider["network"],
                "context_mode": provider["context_mode"],
                "pin_configured": bool(provider["pinned_commit"]),
                "checksum_configured": bool(provider["detector_sha256"]),
                "health": "disabled" if not provider["enabled"] else "configuration_pending",
            }
        },
    }


def _bounded_int(value: object, default: int, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _bounded_float(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))
