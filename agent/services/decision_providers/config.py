"""Configuration of decision providers and decision areas (DPRV).

Hub config section ``decision_providers`` (``POST /config``; defaults in
``agent/config_defaults.py``). Everything is off by default; with
``enabled: false`` (or an area in mode ``off``) Ananta behaves exactly as
before. Example::

    decision_providers:
      enabled: true
      confidence_threshold: 0.9          # default for every area
      providers:
        jev:                              # TypeSafe Jev (cloud)
          enabled: true
          external_calls_allowed: true    # required: the decision text leaves the machine
          model: jev-latest
          timeout_seconds: 5
        local_decision:                   # llama.cpp POST /v1/decision (local)
          enabled: true
          base_url_env: ANANTA_PARALLEL_DECISION_URL
        llm:                              # Ananta's configured LLM (System 2)
          enabled: true
      areas:
        tool_routing:     {mode: shadow, cascade: [jev], confidence_threshold: 0.9}
        retrieval_intent: {mode: off, provider: local_decision}

Area modes: ``off`` (today's behaviour), ``shadow`` (decide and record, today's
behaviour wins) and ``active`` (an accepted decision is used; a deferral keeps
today's path). An area names one ``provider`` or a ``cascade`` of providers.

The TypeSafe key never lives in config: only ``api_key_env`` (default
``TYPESAFE_API_KEY``; ``<name>_FILE`` works too) names where it comes from.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SECTION = "decision_providers"
PROVIDERS = ("jev", "local_decision", "llm")
EXTERNAL_PROVIDERS = frozenset({"jev"})
AREAS = ("tool_routing", "companion_route", "companion_knowledge", "retrieval_intent", "rag_needed",
         "chat_intent", "hub_direct")
MODES = ("off", "shadow", "active")
DEFAULT_THRESHOLD = 0.9
_SECRET_KEYS = frozenset({"api_key", "key", "token", "secret", "authorization"})

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "confidence_threshold": DEFAULT_THRESHOLD,
    "deadline_seconds": 8.0,
    "providers": {
        "jev": {"enabled": False, "external_calls_allowed": False, "model": "jev-latest",
                "base_url": "https://api.typesafe.ai", "api_key_env": "TYPESAFE_API_KEY",
                "timeout_seconds": 5.0, "max_retries": 2, "price_input_per_million": 0.042,
                "price_output_per_million": 0.0},
        "local_decision": {"enabled": False, "base_url": "", "base_url_env": "ANANTA_PARALLEL_DECISION_URL",
                           "timeout_seconds": 5.0},
        "llm": {"enabled": False, "timeout_seconds": 30.0},
    },
    "areas": {},
}


class DecisionConfigError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _threshold(value: Any, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise DecisionConfigError(reason)
    return float(value)


def _positive(value: Any, reason: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 < float(value) <= maximum:
        raise DecisionConfigError(reason)
    return float(value)


def _contains_secret(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(str(k).lower() in _SECRET_KEYS or _contains_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _provider(name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DecisionConfigError(f"decision_provider_invalid:{name}")
    unknown = set(raw) - set(DEFAULTS["providers"][name])
    if unknown:
        raise DecisionConfigError(f"decision_provider_unknown_keys:{name}:{','.join(sorted(unknown))}")
    result = {**DEFAULTS["providers"][name], **dict(raw)}
    result["enabled"] = bool(result["enabled"])
    result["timeout_seconds"] = _positive(result["timeout_seconds"], f"decision_provider_timeout_invalid:{name}", 120)
    if name == "jev":
        result["external_calls_allowed"] = bool(result["external_calls_allowed"])
        if result["enabled"] and not result["external_calls_allowed"]:
            raise DecisionConfigError("decision_provider_external_calls_not_allowed:jev")
        base_url = str(result["base_url"] or "").strip().rstrip("/")
        if not base_url.startswith("https://") and not base_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise DecisionConfigError("decision_provider_base_url_invalid:jev")
        result["base_url"] = base_url
        result["model"] = str(result["model"] or "jev-latest").strip()
        result["api_key_env"] = str(result["api_key_env"] or "TYPESAFE_API_KEY").strip()
        if not result["api_key_env"].replace("_", "").isalnum():
            raise DecisionConfigError("decision_provider_api_key_env_invalid:jev")
        retries = result["max_retries"]
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
            raise DecisionConfigError("decision_provider_max_retries_invalid:jev")
        for price in ("price_input_per_million", "price_output_per_million"):
            if isinstance(result[price], bool) or not isinstance(result[price], (int, float)) or result[price] < 0:
                raise DecisionConfigError(f"decision_provider_price_invalid:jev:{price}")
    if name == "local_decision":
        result["base_url"] = str(result["base_url"] or "").strip().rstrip("/")
        result["base_url_env"] = str(result["base_url_env"] or "").strip()
    return result


def _area(name: str, raw: Any, providers: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DecisionConfigError(f"decision_area_invalid:{name}")
    unknown = set(raw) - {"mode", "provider", "cascade", "confidence_threshold", "question_thresholds"}
    if unknown:
        raise DecisionConfigError(f"decision_area_unknown_keys:{name}:{','.join(sorted(unknown))}")
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode not in MODES:
        raise DecisionConfigError(f"decision_area_mode_invalid:{name}")
    if raw.get("provider") and raw.get("cascade"):
        raise DecisionConfigError(f"decision_area_provider_and_cascade:{name}")
    cascade = [raw["provider"]] if raw.get("provider") else list(raw.get("cascade") or [])
    if not all(isinstance(item, str) and item in PROVIDERS for item in cascade) or len(set(cascade)) != len(cascade):
        raise DecisionConfigError(f"decision_area_cascade_invalid:{name}")
    if mode != "off":
        if not cascade:
            raise DecisionConfigError(f"decision_area_cascade_required:{name}")
        disabled = [item for item in cascade if not providers[item]["enabled"]]
        if disabled:
            raise DecisionConfigError(f"decision_area_provider_disabled:{name}:{disabled[0]}")
    area: dict[str, Any] = {"mode": mode, "cascade": cascade}
    if raw.get("confidence_threshold") is not None:
        area["confidence_threshold"] = _threshold(raw["confidence_threshold"],
                                                  f"decision_area_threshold_invalid:{name}")
    thresholds = raw.get("question_thresholds") or {}
    if not isinstance(thresholds, Mapping):
        raise DecisionConfigError(f"decision_area_threshold_invalid:{name}")
    area["question_thresholds"] = {str(k): _threshold(v, f"decision_area_threshold_invalid:{name}")
                                   for k, v in thresholds.items()}
    return area


def normalize_decision_config(raw: Any, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A validated, complete section: ``raw`` merged over ``current`` over the defaults."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise DecisionConfigError("decision_providers_invalid")
    if _contains_secret(raw):
        raise DecisionConfigError("decision_providers_secret_not_allowed_in_config_use_api_key_env")
    unknown = set(raw) - {"enabled", "confidence_threshold", "deadline_seconds", "providers", "areas"}
    if unknown:
        raise DecisionConfigError("decision_providers_unknown_keys:" + ",".join(sorted(unknown)))
    base = dict(current or {})
    raw_providers = dict(raw.get("providers") or {})
    if set(raw_providers) - set(PROVIDERS):
        raise DecisionConfigError("decision_provider_unknown:" + ",".join(sorted(set(raw_providers) - set(PROVIDERS))))
    providers = {}
    for name in PROVIDERS:
        merged = {**dict((base.get("providers") or {}).get(name) or {}), **dict(raw_providers.get(name) or {})}
        providers[name] = _provider(name, merged)
    raw_areas = dict(raw.get("areas") or {})
    if set(raw_areas) - set(AREAS):
        raise DecisionConfigError("decision_area_unknown:" + ",".join(sorted(set(raw_areas) - set(AREAS))))
    areas = {name: dict(value) for name, value in dict(base.get("areas") or {}).items() if name in AREAS}
    for name, value in raw_areas.items():
        areas[name] = {**areas.get(name, {}), **dict(value or {})}
    return {
        "enabled": bool(raw.get("enabled", base.get("enabled", DEFAULTS["enabled"]))),
        "confidence_threshold": _threshold(raw.get("confidence_threshold", base.get(
            "confidence_threshold", DEFAULT_THRESHOLD)), "decision_providers_threshold_invalid"),
        "deadline_seconds": _positive(raw.get("deadline_seconds", base.get("deadline_seconds", 8.0)),
                                      "decision_providers_deadline_invalid", 120),
        "providers": providers,
        "areas": {name: _area(name, value, providers) for name, value in areas.items()},
    }


def area_settings(section: Mapping[str, Any] | None, area: str) -> dict[str, Any] | None:
    """The effective settings of ``area``, or ``None`` when it is off (globally or itself)."""
    try:
        cfg = normalize_decision_config(section or {})
    except DecisionConfigError:
        return None  # an invalid stored section never activates anything
    settings = cfg["areas"].get(area)
    if not cfg["enabled"] or not settings or settings["mode"] == "off":
        return None
    return {**settings, "confidence_threshold": settings.get("confidence_threshold", cfg["confidence_threshold"]),
            "deadline_seconds": cfg["deadline_seconds"],
            "providers": {name: cfg["providers"][name] for name in settings["cascade"]}}
