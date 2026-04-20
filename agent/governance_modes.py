from __future__ import annotations

from typing import Any


_DEFAULT_GOVERNANCE_MODE = "balanced"

_GOVERNANCE_MODE_CATALOG: dict[str, dict[str, Any]] = {
    "safe": {
        "label": "Safe",
        "control_level": "conservative",
        "description": "Konservative Defaults: eher eingeschraenkte Ausfuehrung, fruehe Explainability und klare Blockierungsgruende.",
    },
    "balanced": {
        "label": "Balanced",
        "control_level": "standard",
        "description": "Sinnvoller Standard fuer Teams: Policies sichtbar, aber ohne maximal restriktive Defaults.",
    },
    "strict": {
        "label": "Strict",
        "control_level": "max",
        "description": "Maximale Kontrolle: strikte Tool-Grenzen, klare Freigabe- und Review-Pflichten, Audit-first.",
    },
}

_GOVERNANCE_MODE_ALIASES = {
    "default": "balanced",
    "standard": "balanced",
    "secure": "safe",
    "locked-down": "strict",
}


def governance_mode_catalog() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in _GOVERNANCE_MODE_CATALOG.items()}


def resolve_governance_mode(config: dict | None = None) -> dict[str, Any]:
    cfg = dict(config or {})
    catalog = governance_mode_catalog()
    requested = str(cfg.get("governance_mode") or "").strip().lower()
    requested = _GOVERNANCE_MODE_ALIASES.get(requested, requested)
    source = "config.governance_mode"

    if not requested:
        requested = _DEFAULT_GOVERNANCE_MODE
        source = "default"

    valid = requested in catalog
    effective = requested if valid else _DEFAULT_GOVERNANCE_MODE
    validation_status = "ok" if valid else "error"
    validation_message = None if valid else f"invalid_governance_mode:{requested or '<empty>'}"

    return {
        "requested": requested,
        "effective": effective,
        "valid": valid,
        "source": source,
        "catalog": catalog,
        "mode": dict(catalog.get(effective) or {}),
        "validation": {"status": validation_status, "message": validation_message},
    }

