import json
import os
import re
import time
import uuid

from flask import Blueprint, Response, current_app, g, has_request_context, request, stream_with_context
from sqlmodel import Session, select

from agent.auth import admin_required, check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import ConfigDB, RoleDB, TeamDB, TeamMemberDB, TeamTypeRoleLink, TemplateDB
from agent.llm_benchmarks import (
    BENCH_TASK_KINDS,
    DEFAULT_BENCH_MODEL_ORDER,
    DEFAULT_BENCH_PROVIDER_ORDER,
    DEFAULT_BENCH_RETENTION,
    benchmark_identity_precedence_config,
    benchmark_retention_config,
    benchmark_rows,
    load_benchmarks,
    record_benchmark_sample,
    save_benchmarks,
    timeseries_from_samples,
)
from agent.llm_integration import _list_lmstudio_candidates, _load_lmstudio_history, generate_text
from agent.local_llm_backends import get_local_openai_backends, resolve_local_openai_backend
from agent.models import TemplateCreateRequest
from agent.repository import agent_repo, config_repo, role_repo, task_repo, team_repo, template_repo
from agent.runtime_policy import normalize_task_kind
from agent.tool_capabilities import (
    build_capability_contract,
    describe_capabilities,
    resolve_allowed_tools,
    validate_tool_calls_against_contract,
)
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry
from agent.utils import log_llm_entry, rate_limit, validate_request

ALLOWED_TEMPLATE_VARIABLES = {
    "agent_name",
    "task_title",
    "task_description",
    "team_name",
    "role_name",
    "team_goal",
    "anforderungen",
    "funktion",
    "feature_name",
    "title",
    "description",
    "task",
    "endpoint_name",
    "beschreibung",
    "sprache",
    "api_details",
}


def _get_template_allowlist() -> set:
    cfg = current_app.config.get("AGENT_CONFIG", {})
    allowlist_cfg = cfg.get("template_variables_allowlist")
    if isinstance(allowlist_cfg, list) and allowlist_cfg:
        return set(allowlist_cfg)
    return ALLOWED_TEMPLATE_VARIABLES


def validate_template_variables(template_text: str) -> list[str]:
    """Extrahiert {{variablen}} und prueft sie gegen die Whitelist."""
    if not template_text:
        return []
    found_vars = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", template_text)
    allowlist = _get_template_allowlist()
    unknown_vars = [v for v in found_vars if v not in allowlist]
    return unknown_vars


config_bp = Blueprint("config", __name__)
_LLM_BENCHMARKS_FILE = "llm_model_benchmarks.json"
_LMSTUDIO_CATALOG_CACHE: dict[str, dict] = {}
_LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES = 64
_BENCH_TASK_KINDS = BENCH_TASK_KINDS
_DEFAULT_BENCH_MODEL_ORDER = ["proposal_model", "llm_config_model", "default_model", "model"]
_SENSITIVE_CONFIG_KEYS = {"token", "secret", "password", "api_key"}


def _parse_bool_query_flag(value: str | None) -> bool:
    v = str(value or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _provider_alias(provider: str | None, agent_cfg: dict | None = None, provider_urls: dict | None = None) -> str:
    value = str(provider or "").strip().lower()
    local_backend = resolve_local_openai_backend(value, agent_cfg=agent_cfg, provider_urls=provider_urls)
    if local_backend:
        return str(local_backend.get("transport_provider") or "openai")
    return "openai" if value == "codex" else value


def _resolve_provider_base_url(
    provider: str | None,
    requested_base_url: str | None,
    llm_cfg: dict | None,
    agent_cfg: dict | None,
    provider_urls: dict | None,
) -> tuple[str | None, str]:
    requested = str(requested_base_url or "").strip()
    llm_cfg = llm_cfg or {}
    agent_cfg = agent_cfg or {}
    provider_urls = provider_urls or {}
    normalized_provider = str(provider or "").strip().lower()
    local_backend = resolve_local_openai_backend(normalized_provider, agent_cfg=agent_cfg, provider_urls=provider_urls)
    provider_lookup = _provider_alias(normalized_provider, agent_cfg=agent_cfg, provider_urls=provider_urls)

    if requested:
        return requested, "request.config.base_url"
    llm_cfg_base_url = str(llm_cfg.get("base_url") or "").strip()
    if llm_cfg_base_url:
        return llm_cfg_base_url, "agent_config.llm_config.base_url"
    if local_backend and local_backend.get("base_url"):
        return local_backend.get("base_url"), f"local_openai_backends.{local_backend['provider']}"
    if normalized_provider:
        if provider_urls.get(normalized_provider):
            return provider_urls.get(normalized_provider), f"provider_urls.{normalized_provider}"
        if provider_urls.get(provider_lookup):
            return provider_urls.get(provider_lookup), f"provider_urls.{provider_lookup}"
        if agent_cfg.get(f"{normalized_provider}_url"):
            return agent_cfg.get(f"{normalized_provider}_url"), f"agent_config.{normalized_provider}_url"
        if agent_cfg.get(f"{provider_lookup}_url"):
            return agent_cfg.get(f"{provider_lookup}_url"), f"agent_config.{provider_lookup}_url"
    return None, "provider_urls"


def _resolve_provider_api_key(
    provider: str | None,
    explicit_api_key: str | None,
    api_key_profile: str | None,
    agent_cfg: dict | None,
) -> str | None:
    api_key = str(explicit_api_key or "").strip() or None
    if api_key:
        return api_key

    provider_name = str(provider or "").strip().lower()
    profile_name = str(api_key_profile or "").strip()
    agent_cfg = agent_cfg or {}
    if provider_name == "lmstudio":
        return "sk-no-key-needed"
    if profile_name:
        profiles = agent_cfg.get("llm_api_key_profiles") or {}
        selected_profile = profiles.get(profile_name) if isinstance(profiles, dict) else None
        if isinstance(selected_profile, str):
            return selected_profile.strip() or None
        if isinstance(selected_profile, dict):
            profile_provider = str(selected_profile.get("provider") or "").strip().lower()
            if not profile_provider or profile_provider in {provider_name, _provider_alias(provider_name, agent_cfg=agent_cfg)}:
                value = str(selected_profile.get("api_key") or "").strip()
                if value:
                    return value
    local_backend = resolve_local_openai_backend(provider_name, agent_cfg=agent_cfg)
    if local_backend:
        local_api_key = str(local_backend.get("api_key") or "").strip()
        if local_api_key:
            return local_api_key
        local_profile = str(local_backend.get("api_key_profile") or "").strip()
        if local_profile and isinstance(agent_cfg.get("llm_api_key_profiles"), dict):
            selected_profile = (agent_cfg.get("llm_api_key_profiles") or {}).get(local_profile)
            if isinstance(selected_profile, str):
                return selected_profile.strip() or None
            if isinstance(selected_profile, dict):
                return str(selected_profile.get("api_key") or "").strip() or None
        return "sk-no-key-needed"
    return None


def _sanitize_assistant_config(value):
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            if any(s in str(k).lower() for s in _SENSITIVE_CONFIG_KEYS):
                cleaned[k] = "***"
            else:
                cleaned[k] = _sanitize_assistant_config(v)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_assistant_config(v) for v in value]
    return value


def _assistant_editable_settings_inventory() -> list[dict]:
    return [
        {
            "key": "default_provider",
            "path": "config.default_provider",
            "type": "enum",
            "editable": True,
            "allowed_values": ["ollama", "lmstudio", "openai", "codex", "anthropic"],
            "endpoint": "POST /config",
        },
        {
            "key": "default_model",
            "path": "config.default_model",
            "type": "string",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "codex_cli",
            "path": "config.codex_cli",
            "type": "object",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "research_backend",
            "path": "config.research_backend",
            "type": "object",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "template_agent_name",
            "path": "config.template_agent_name",
            "type": "string",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "team_agent_name",
            "path": "config.team_agent_name",
            "type": "string",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "quality_gates",
            "path": "config.quality_gates",
            "type": "object",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "benchmark_retention",
            "path": "config.benchmark_retention",
            "type": "object",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "benchmark_identity_precedence",
            "path": "config.benchmark_identity_precedence",
            "type": "object",
            "editable": True,
            "endpoint": "POST /config",
        },
        {
            "key": "http_timeout",
            "path": "config.http_timeout",
            "type": "integer",
            "editable": True,
            "min": 1,
            "endpoint": "POST /config",
        },
        {
            "key": "command_timeout",
            "path": "config.command_timeout",
            "type": "integer",
            "editable": True,
            "min": 1,
            "endpoint": "POST /config",
        },
        {
            "key": "agent_offline_timeout",
            "path": "config.agent_offline_timeout",
            "type": "integer",
            "editable": True,
            "min": 10,
            "endpoint": "POST /config",
        },
        {
            "key": "log_level",
            "path": "config.log_level",
            "type": "enum",
            "editable": True,
            "allowed_values": ["DEBUG", "INFO", "WARNING", "ERROR"],
            "endpoint": "POST /config",
        },
        {"key": "templates", "path": "templates", "type": "collection", "editable": True, "endpoint": "/templates"},
        {"key": "teams", "path": "teams", "type": "collection", "editable": True, "endpoint": "/teams"},
        {
            "key": "team_types",
            "path": "teams.types",
            "type": "collection",
            "editable": True,
            "endpoint": "/teams/types",
        },
        {"key": "roles", "path": "teams.roles", "type": "collection", "editable": True, "endpoint": "/teams/roles"},
        {
            "key": "autopilot",
            "path": "tasks.autopilot",
            "type": "object",
            "editable": True,
            "endpoint": "/tasks/autopilot/start|stop|tick",
        },
        {
            "key": "auto_planner",
            "path": "tasks.auto_planner",
            "type": "object",
            "editable": True,
            "endpoint": "/tasks/auto-planner/configure",
        },
        {
            "key": "triggers",
            "path": "triggers",
            "type": "object",
            "editable": True,
            "endpoint": "/triggers/configure",
        },
    ]


def _assistant_settings_summary(cfg: dict, teams: list[dict], templates: list[dict]) -> dict:
    qg = (cfg or {}).get("quality_gates", {}) or {}
    return {
        "llm": {
            "default_provider": cfg.get("default_provider"),
            "default_model": cfg.get("default_model"),
            "template_agent_name": cfg.get("template_agent_name"),
            "team_agent_name": cfg.get("team_agent_name"),
            "codex_cli": {
                "base_url": ((cfg.get("codex_cli") or {}).get("base_url") if isinstance(cfg.get("codex_cli"), dict) else None),
                "api_key_profile": (
                    (cfg.get("codex_cli") or {}).get("api_key_profile") if isinstance(cfg.get("codex_cli"), dict) else None
                ),
                "prefer_lmstudio": (
                    (cfg.get("codex_cli") or {}).get("prefer_lmstudio") if isinstance(cfg.get("codex_cli"), dict) else None
                ),
            },
            "research_backend": {
                "provider": (
                    (cfg.get("research_backend") or {}).get("provider")
                    if isinstance(cfg.get("research_backend"), dict)
                    else None
                ),
                "enabled": (
                    (cfg.get("research_backend") or {}).get("enabled")
                    if isinstance(cfg.get("research_backend"), dict)
                    else None
                ),
                "mode": (
                    (cfg.get("research_backend") or {}).get("mode")
                    if isinstance(cfg.get("research_backend"), dict)
                    else None
                ),
                "command": (
                    (cfg.get("research_backend") or {}).get("command")
                    if isinstance(cfg.get("research_backend"), dict)
                    else None
                ),
                "working_dir": (
                    (cfg.get("research_backend") or {}).get("working_dir")
                    if isinstance(cfg.get("research_backend"), dict)
                    else None
                ),
            },
        },
        "system": {
            "log_level": cfg.get("log_level"),
            "http_timeout": cfg.get("http_timeout"),
            "command_timeout": cfg.get("command_timeout"),
            "agent_offline_timeout": cfg.get("agent_offline_timeout"),
        },
        "quality_gates": {
            "enabled": qg.get("enabled"),
            "autopilot_enforce": qg.get("autopilot_enforce"),
            "min_output_chars": qg.get("min_output_chars"),
        },
        "counts": {
            "teams": len(teams),
            "templates": len(templates),
        },
    }


def _assistant_automation_snapshot() -> dict:
    snapshot = {"autopilot": None, "auto_planner": None, "triggers": None}
    try:
        from agent.routes.tasks.autopilot import autonomous_loop

        snapshot["autopilot"] = autonomous_loop.status()
    except Exception:
        pass
    try:
        from agent.routes.tasks.auto_planner import auto_planner

        snapshot["auto_planner"] = auto_planner.status()
    except Exception:
        pass
    try:
        from agent.routes.tasks.triggers import trigger_engine

        snapshot["triggers"] = trigger_engine.status()
    except Exception:
        pass
    return snapshot


def _lmstudio_catalog_runtime_options() -> tuple[int, int, bool]:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    timeout_default = int((cfg.get("provider_catalog", {}) or {}).get("lmstudio_timeout_seconds") or 5)
    ttl_default = int((cfg.get("provider_catalog", {}) or {}).get("cache_ttl_seconds") or 15)

    timeout_seconds = int(request.args.get("lmstudio_timeout_seconds") or timeout_default or 5)
    timeout_seconds = max(1, min(60, timeout_seconds))

    cache_ttl_seconds = int(request.args.get("cache_ttl_seconds") or ttl_default or 15)
    cache_ttl_seconds = max(0, min(3600, cache_ttl_seconds))

    force_refresh = _parse_bool_query_flag(request.args.get("force_refresh"))
    return timeout_seconds, cache_ttl_seconds, force_refresh


def _get_lmstudio_candidates_cached(
    lmstudio_url: str | None, timeout_seconds: int, cache_ttl_seconds: int, force_refresh: bool
) -> list[dict]:
    if not lmstudio_url:
        return []

    now = time.time()
    cache_key = f"{lmstudio_url}|t={timeout_seconds}"
    cached = _LMSTUDIO_CATALOG_CACHE.get(cache_key) or {}
    cached_items = cached.get("items")
    cached_ts = float(cached.get("ts") or 0.0)
    if (
        not force_refresh
        and cache_ttl_seconds > 0
        and isinstance(cached_items, list)
        and (now - cached_ts) <= cache_ttl_seconds
    ):
        return cached_items

    try:
        fresh_items = _list_lmstudio_candidates(lmstudio_url, timeout=timeout_seconds)
    except Exception:
        fresh_items = []

    _prune_lmstudio_catalog_cache(now=now)
    _LMSTUDIO_CATALOG_CACHE[cache_key] = {"ts": now, "items": fresh_items}
    return fresh_items


def _catalog_models_for_local_backend(
    backend: dict,
    *,
    timeout_seconds: int,
    cache_ttl_seconds: int,
    force_refresh: bool,
) -> list[dict]:
    candidates = _get_lmstudio_candidates_cached(
        backend.get("base_url"),
        timeout_seconds=timeout_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=force_refresh,
    )
    if candidates:
        return candidates
    return [{"id": model_id, "context_length": None} for model_id in (backend.get("configured_models") or [])]


def _prune_lmstudio_catalog_cache(now: float | None = None) -> None:
    now = float(now or time.time())
    # Drop very old entries first, then enforce a strict max-entry bound (LRU by timestamp).
    ttl_cutoff = now - 3600.0
    for key, value in list(_LMSTUDIO_CATALOG_CACHE.items()):
        ts = float((value or {}).get("ts") or 0.0)
        if ts <= 0.0 or ts < ttl_cutoff:
            _LMSTUDIO_CATALOG_CACHE.pop(key, None)

    overflow = len(_LMSTUDIO_CATALOG_CACHE) - _LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES + 1
    if overflow <= 0:
        return
    oldest = sorted(_LMSTUDIO_CATALOG_CACHE.items(), key=lambda item: float((item[1] or {}).get("ts") or 0.0))
    for key, _ in oldest[:overflow]:
        _LMSTUDIO_CATALOG_CACHE.pop(key, None)


def _benchmarks_path() -> str:
    data_dir = current_app.config.get("DATA_DIR") or "data"
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, _LLM_BENCHMARKS_FILE)


def _benchmark_retention_config() -> dict:
    return benchmark_retention_config(current_app.config.get("AGENT_CONFIG", {}) or {})


def _benchmark_identity_precedence_config() -> dict:
    return benchmark_identity_precedence_config(current_app.config.get("AGENT_CONFIG", {}) or {})


def _default_metric_bucket() -> dict:
    return {
        "total": 0,
        "success": 0,
        "failed": 0,
        "quality_pass": 0,
        "quality_fail": 0,
        "latency_ms_total": 0,
        "tokens_total": 0,
        "cost_units_total": 0.0,
        "last_seen": None,
    }


def _append_sample(
    target: dict, now: int, success: bool, quality_passed: bool, latency_ms: int, tokens_total: int
) -> None:
    retention = _benchmark_retention_config()
    max_samples = retention["max_samples"]
    max_days = retention["max_days"]
    min_ts = int(now) - (max_days * 86400)

    samples = target.setdefault("samples", [])
    if not isinstance(samples, list):
        samples = []
        target["samples"] = samples
    else:
        samples[:] = [s for s in samples if int((s or {}).get("ts") or 0) >= min_ts]
    samples.append(
        {
            "ts": int(now),
            "success": bool(success),
            "quality_passed": bool(quality_passed),
            "latency_ms": max(0, int(latency_ms or 0)),
            "tokens_total": max(0, int(tokens_total or 0)),
        }
    )
    if len(samples) > max_samples:
        del samples[: len(samples) - max_samples]


def _load_benchmarks() -> dict:
    return load_benchmarks(current_app.config.get("DATA_DIR") or "data")


def _save_benchmarks(data: dict) -> None:
    save_benchmarks(current_app.config.get("DATA_DIR") or "data", data)


def _score_bucket(bucket: dict) -> dict:
    total = max(0, int(bucket.get("total") or 0))
    success = max(0, int(bucket.get("success") or 0))
    quality_pass = max(0, int(bucket.get("quality_pass") or 0))
    latency_ms_total = max(0, int(bucket.get("latency_ms_total") or 0))
    tokens_total = max(0, int(bucket.get("tokens_total") or 0))

    success_rate = (success / total) if total else 0.0
    quality_rate = (quality_pass / total) if total else 0.0
    avg_latency_ms = (latency_ms_total / total) if total else 0.0
    avg_tokens = (tokens_total / total) if total else 0.0
    latency_score = max(0.0, min(1.0, 1.0 - (avg_latency_ms / 30000.0)))
    token_score = max(0.0, min(1.0, 1.0 - (avg_tokens / 8000.0)))
    efficiency = (latency_score + token_score) / 2.0
    suitability_score = round((0.45 * success_rate + 0.35 * quality_rate + 0.20 * efficiency) * 100.0, 2)

    return {
        "total": total,
        "success_rate": round(success_rate, 4),
        "quality_rate": round(quality_rate, 4),
        "avg_latency_ms": round(avg_latency_ms, 2),
        "avg_tokens": round(avg_tokens, 2),
        "suitability_score": suitability_score,
    }


def _timeseries_from_samples(samples: list[dict], bucket: str = "day") -> list[dict]:
    return timeseries_from_samples(samples, bucket=bucket)


def _benchmark_rows(task_kind: str | None = None, top_n: int | None = None) -> tuple[list[dict], dict]:
    return benchmark_rows(data_dir=current_app.config.get("DATA_DIR") or "data", task_kind=task_kind, top_n=top_n)


def _runtime_model_available(
    provider: str,
    model: str,
    *,
    agent_cfg: dict,
    provider_urls: dict,
    timeout_seconds: int = 5,
) -> bool:
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    if not provider or not model:
        return False
    local_backend = resolve_local_openai_backend(provider, agent_cfg=agent_cfg, provider_urls=provider_urls)
    if local_backend:
        candidates = _catalog_models_for_local_backend(
            local_backend,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=15,
            force_refresh=False,
        )
        return any(str(item.get("id") or "").strip() == model for item in candidates)
    if provider == "ollama":
        return model in {"llama3", "mistral"} and bool(provider_urls.get("ollama"))
    if provider in {"openai", "codex"}:
        return model in {"gpt-4o", "gpt-4-turbo", "gpt-5-codex", "gpt-5-codex-mini"} and bool(
            provider_urls.get("openai") or current_app.config.get("OPENAI_API_KEY")
        )
    if provider == "anthropic":
        return model == "claude-3-5-sonnet-20240620" and bool(
            provider_urls.get("anthropic") or current_app.config.get("ANTHROPIC_API_KEY")
        )
    return False


def _recommend_runtime_selection(
    *,
    task_kind: str,
    current_provider: str | None,
    current_model: str | None,
    agent_cfg: dict,
    provider_urls: dict,
) -> dict | None:
    rows, _ = _benchmark_rows(task_kind=task_kind, top_n=10)
    for row in rows:
        provider = str(row.get("provider") or "").strip().lower()
        model = str(row.get("model") or "").strip()
        if _runtime_model_available(provider, model, agent_cfg=agent_cfg, provider_urls=provider_urls):
            return {
                "provider": provider,
                "model": model,
                "selection_source": "benchmarks_available_top_ranked",
                "replaces": {"provider": current_provider, "model": current_model},
            }
    return None


@config_bp.route("/llm/history", methods=["GET"])
@check_auth
def get_llm_history():
    """
    Gibt den Verlauf der genutzten LLM-Modelle zurück (aktuell LMStudio Fokus).
    """
    history = _load_lmstudio_history()
    return api_response(data=history)


@config_bp.route("/llm/benchmarks/record", methods=["POST"])
@admin_required
def record_llm_benchmark():
    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider") or "").strip().lower()
    model = str(data.get("model") or "").strip()
    task_kind = str(data.get("task_kind") or "analysis").strip().lower()
    if task_kind not in _BENCH_TASK_KINDS:
        task_kind = "analysis"

    if not provider or not model:
        return api_response(status="error", message="provider_and_model_required", code=400)

    success = bool(data.get("success", False))
    quality_passed = bool(data.get("quality_gate_passed", success))
    latency_ms = max(0, int(data.get("latency_ms") or 0))
    tokens_total = max(0, int(data.get("tokens_total") or 0))
    cost_units = float(data.get("cost_units") or 0.0)
    result = record_benchmark_sample(
        data_dir=current_app.config.get("DATA_DIR") or "data",
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        provider=provider,
        model=model,
        task_kind=task_kind,
        success=success,
        quality_gate_passed=quality_passed,
        latency_ms=latency_ms,
        tokens_total=tokens_total,
        cost_units=cost_units,
    )
    model_key = result.get("model_key")
    log_audit("llm_benchmark_recorded", {"model_key": model_key, "task_kind": task_kind, "success": success})
    return api_response(data={"recorded": True, "model_key": model_key, "task_kind": task_kind})


@config_bp.route("/llm/benchmarks", methods=["GET"])
@check_auth
def get_llm_benchmarks():
    task_kind = str(request.args.get("task_kind") or "").strip().lower()
    top_n = max(1, min(100, int(request.args.get("top_n") or 20)))
    rows, db = _benchmark_rows(task_kind=task_kind, top_n=top_n)

    return api_response(
        data={
            "task_kind": task_kind if task_kind in _BENCH_TASK_KINDS else None,
            "updated_at": db.get("updated_at"),
            "items": rows,
        }
    )


@config_bp.route("/llm/benchmarks/timeseries", methods=["GET"])
@check_auth
def get_llm_benchmarks_timeseries():
    provider = str(request.args.get("provider") or "").strip().lower()
    model = str(request.args.get("model") or "").strip()
    task_kind = str(request.args.get("task_kind") or "").strip().lower()
    bucket = str(request.args.get("bucket") or "day").strip().lower()
    if bucket not in {"day", "hour"}:
        bucket = "day"
    days = max(1, min(365, int(request.args.get("days") or 30)))
    min_ts = int(time.time()) - (days * 86400)
    retention = _benchmark_retention_config()
    retention_days = retention["max_days"]
    effective_min_ts = max(min_ts, int(time.time()) - (retention_days * 86400))

    db = _load_benchmarks()
    items = []
    for key, entry in (db.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        p = str(entry.get("provider") or "").strip().lower()
        m = str(entry.get("model") or "").strip()
        if provider and p != provider:
            continue
        if model and m != model:
            continue
        source_bucket = entry.get("overall") or {}
        if task_kind in _BENCH_TASK_KINDS:
            source_bucket = (entry.get("task_kinds") or {}).get(task_kind) or {}
        samples = [s for s in (source_bucket.get("samples") or []) if int((s or {}).get("ts") or 0) >= effective_min_ts]
        points = _timeseries_from_samples(samples, bucket=bucket)
        items.append(
            {
                "id": key,
                "provider": p,
                "model": m,
                "task_kind": task_kind if task_kind in _BENCH_TASK_KINDS else None,
                "bucket": bucket,
                "points": points,
            }
        )

    return api_response(
        data={
            "updated_at": db.get("updated_at"),
            "days": days,
            "bucket": bucket,
            "retention": retention,
            "items": items,
        }
    )


@config_bp.route("/llm/benchmarks/config", methods=["GET"])
@check_auth
def get_llm_benchmarks_config():
    return api_response(
        data={
            "retention": _benchmark_retention_config(),
            "identity_precedence": _benchmark_identity_precedence_config(),
            "defaults": {
                "retention": DEFAULT_BENCH_RETENTION,
                "identity_precedence": {
                    "provider_order": DEFAULT_BENCH_PROVIDER_ORDER,
                    "model_order": DEFAULT_BENCH_MODEL_ORDER,
                },
            },
        }
    )


@config_bp.route("/config", methods=["GET"])
@check_auth
def get_config():
    """
    Aktuelle Konfiguration abrufen
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Aktuelle Agenten-Konfiguration
    """
    return api_response(data=current_app.config.get("AGENT_CONFIG", {}))


@config_bp.route("/assistant/read-model", methods=["GET"])
@check_auth
def assistant_read_model():
    cfg = _sanitize_assistant_config(current_app.config.get("AGENT_CONFIG", {}) or {})
    teams = [t.model_dump() for t in team_repo.get_all()]
    roles = [r.model_dump() for r in role_repo.get_all()]
    templates = [t.model_dump() for t in template_repo.get_all()]
    agents = [a.model_dump() for a in agent_repo.get_all()]
    for a in agents:
        if "token" in a:
            a["token"] = "***"
    capability_contract = build_capability_contract(current_app.config.get("AGENT_CONFIG", {}) or {})
    allowed_tools = resolve_allowed_tools(
        current_app.config.get("AGENT_CONFIG", {}) or {},
        is_admin=bool(getattr(g, "is_admin", False)),
        contract=capability_contract,
    )
    capability_meta = describe_capabilities(
        capability_contract, allowed_tools=allowed_tools, is_admin=bool(getattr(g, "is_admin", False))
    )
    settings_inventory = _assistant_editable_settings_inventory()
    settings_summary = _assistant_settings_summary(cfg, teams, templates)
    return api_response(
        data={
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "settings": {
                "summary": settings_summary,
                "editable_inventory": settings_inventory,
                "editable_count": len(settings_inventory),
            },
            "automation": _assistant_automation_snapshot(),
            "assistant_capabilities": capability_meta,
            "context_timestamp": int(time.time()),
        }
    )


@config_bp.route("/dashboard/read-model", methods=["GET"])
@check_auth
def dashboard_read_model():
    cfg = _sanitize_assistant_config(current_app.config.get("AGENT_CONFIG", {}) or {})
    teams = [t.model_dump() for t in team_repo.get_all()]
    roles = [r.model_dump() for r in role_repo.get_all()]
    templates = [t.model_dump() for t in template_repo.get_all()]
    agents = [a.model_dump() for a in agent_repo.get_all()]
    tasks = [t.model_dump() for t in task_repo.get_all()]
    for a in agents:
        if "token" in a:
            a["token"] = "***"

    task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0, "blocked": 0}
    for task in tasks:
        status = str(task.get("status") or "todo").strip().lower()
        if status not in task_counts:
            task_counts[status] = 0
        task_counts[status] += 1

    recent_tasks = sorted(
        tasks,
        key=lambda t: float(t.get("updated_at") or t.get("created_at") or 0.0),
        reverse=True,
    )[:30]
    recent_timeline = [
        {
            "task_id": t.get("id"),
            "title": t.get("title"),
            "status": t.get("status"),
            "updated_at": t.get("updated_at") or t.get("created_at"),
        }
        for t in recent_tasks
    ]

    benchmark_task_kind = str(request.args.get("benchmark_task_kind") or "analysis").strip().lower()
    bench_rows, bench = _benchmark_rows(task_kind=benchmark_task_kind, top_n=8)

    return api_response(
        data={
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "tasks": {"counts": task_counts, "recent": recent_timeline},
            "benchmarks": {
                "task_kind": benchmark_task_kind if benchmark_task_kind in _BENCH_TASK_KINDS else "analysis",
                "updated_at": bench.get("updated_at"),
                "items": bench_rows,
            },
            "context_timestamp": int(time.time()),
        }
    )


def unwrap_config(data):
    """Rekursives Entpacken von API-Response-Wrappern in der Config."""
    if not isinstance(data, dict):
        return data

    if "data" in data and ("status" in data or "code" in data):
        nested = data.get("data")
        if isinstance(nested, dict):
            unwrapped = unwrap_api_envelope(data)
            return {k: unwrap_config(v) for k, v in unwrapped.items()}
        return unwrap_config(nested)

    unwrapped = data
    return {k: unwrap_config(v) for k, v in unwrapped.items()}


def _infer_tool_calls_from_prompt(prompt: str, context: dict | None = None) -> list[dict]:
    """
    Deterministischer Fallback fuer haeufige Intent-Muster, wenn das LLM
    keine gueltigen tool_calls liefert (z.B. wegen Thinking-Output).
    """
    p = (prompt or "").strip().lower()
    if not p:
        return []

    wants_templates = any(k in p for k in ["template", "templates", "vorlage", "vorlagen"])
    wants_role_links = any(
        k in p
        for k in [
            "rolle verkn",
            "rollen verkn",
            "role link",
            "role links",
            "rollen zuordnen",
            "roles zuordnen",
        ]
    )

    team_types: list[str] = []
    if "scrum" in p:
        team_types.append("Scrum")
    if "kanban" in p:
        team_types.append("Kanban")

    if wants_templates or wants_role_links:
        if not team_types:
            return []
        return [{"name": "ensure_team_templates", "args": {"team_types": team_types}}]

    wants_create_team = any(
        k in p
        for k in [
            "team erstellen",
            "team anlegen",
            "create team",
            "neues team",
            "new team",
        ]
    )
    if wants_create_team:
        if "scrum" in p:
            inferred_type = "Scrum"
        elif "kanban" in p:
            inferred_type = "Kanban"
        else:
            return []

        # Safeguard: nur ausfuehren, wenn ein expliziter Team-Name erkennbar ist.
        team_name = ""
        quoted = re.search(r"['\"]([^'\"]{2,80})['\"]", prompt or "")
        if quoted:
            team_name = quoted.group(1).strip()
        if not team_name:
            m = re.search(r"(?:team(?:name)?\s*[:=]\s*)([a-zA-Z0-9 _-]{2,80})", prompt or "", flags=re.IGNORECASE)
            if m:
                team_name = m.group(1).strip(" .,:;")
        if not team_name:
            return []

        return [{"name": "create_team", "args": {"name": team_name, "team_type": inferred_type}}]

    wants_assign_role = any(
        k in p
        for k in [
            "rolle zuweisen",
            "assign role",
            "agent zuordnen",
            "agent zuweisen",
            "mitglied zuordnen",
        ]
    )
    if wants_assign_role:
        # Safeguard: Tool erwartet IDs/URL. Nur inferieren, wenn alles explizit im Prompt steht.
        team_id_match = re.search(r"team_id\s*[:=]\s*([a-zA-Z0-9._:-]+)", prompt or "", flags=re.IGNORECASE)
        role_id_match = re.search(r"role_id\s*[:=]\s*([a-zA-Z0-9._:-]+)", prompt or "", flags=re.IGNORECASE)
        agent_url_match = re.search(r"agent_url\s*[:=]\s*(https?://\S+)", prompt or "", flags=re.IGNORECASE)
        if not (team_id_match and role_id_match and agent_url_match):
            return []
        return [
            {
                "name": "assign_role",
                "args": {
                    "team_id": team_id_match.group(1).strip(),
                    "role_id": role_id_match.group(1).strip(),
                    "agent_url": agent_url_match.group(1).strip().rstrip(".,;"),
                },
            }
        ]

    return []


@config_bp.route("/config", methods=["POST"])
@admin_required
def set_config():
    """
    Konfiguration aktualisieren
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Konfiguration erfolgreich aktualisiert
    """
    new_cfg = request.get_json()
    if not isinstance(new_cfg, dict):
        return api_response(status="error", message="invalid_json", code=400)

    # Robustes Entpacken
    new_cfg = unwrap_config(new_cfg)

    current_cfg = current_app.config.get("AGENT_CONFIG", {})

    # Ensure nested llm_config fields merge instead of replacing the whole block,
    # so mode toggles such as lmstudio_api_mode are not dropped.
    if "llm_config" in new_cfg and isinstance(new_cfg["llm_config"], dict):
        merged_llm = (current_cfg.get("llm_config", {}) or {}).copy()
        merged_llm.update(new_cfg["llm_config"])
        new_cfg = {**new_cfg, "llm_config": merged_llm}
    if "research_backend" in new_cfg and isinstance(new_cfg["research_backend"], dict):
        merged_research = (current_cfg.get("research_backend", {}) or {}).copy()
        merged_research.update(new_cfg["research_backend"])
        new_cfg = {**new_cfg, "research_backend": merged_research}
    current_cfg.update(new_cfg)
    current_app.config["AGENT_CONFIG"] = current_cfg

    from agent.ai_agent import sync_runtime_state

    sync_runtime_state(current_app, current_cfg, changed_keys=set(new_cfg.keys()))

    # In DB persistieren (nur valide Config-Keys, keine Response-Wrapper)
    try:
        # Reservierte API-Response-Keys ignorieren um Korruption zu vermeiden
        reserved_keys = {"data", "status", "message", "error", "code"}

        config_to_save = new_cfg

        for k, v in config_to_save.items():
            if k not in reserved_keys:
                config_repo.save(ConfigDB(key=k, value_json=json.dumps(v)))
    except Exception as e:
        current_app.logger.error(f"Fehler beim Speichern der Konfiguration in DB: {e}")

    log_audit("config_updated", {"keys": list(new_cfg.keys())})
    return api_response(data={"status": "updated"})


@config_bp.route("/providers", methods=["GET"])
@check_auth
def list_providers():
    """
    Verfügbare LLM-Provider abrufen
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Liste der verfügbaren LLM-Provider
    """
    urls = current_app.config.get("PROVIDER_URLS", {})
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    provider_default = str(app_cfg.get("default_provider") or "")
    model_default = str(app_cfg.get("default_model") or "")

    providers = []
    if urls.get("ollama"):
        providers.append(
            {
                "id": "ollama:llama3",
                "name": "Ollama (Llama3)",
                "selected": provider_default == "ollama" and model_default == "llama3",
            }
        )
        providers.append(
            {
                "id": "ollama:mistral",
                "name": "Ollama (Mistral)",
                "selected": provider_default == "ollama" and model_default == "mistral",
            }
        )

    if urls.get("openai") or current_app.config.get("OPENAI_API_KEY"):
        providers.append(
            {
                "id": "openai:gpt-4o",
                "name": "OpenAI (GPT-4o)",
                "selected": provider_default == "openai" and model_default == "gpt-4o",
            }
        )
        providers.append(
            {
                "id": "openai:gpt-4-turbo",
                "name": "OpenAI (GPT-4 Turbo)",
                "selected": provider_default == "openai" and model_default == "gpt-4-turbo",
            }
        )
        providers.append(
            {
                "id": "codex:gpt-5-codex",
                "name": "OpenAI Codex (GPT-5 Codex)",
                "selected": provider_default == "codex" and model_default == "gpt-5-codex",
            }
        )

    if urls.get("anthropic") or current_app.config.get("ANTHROPIC_API_KEY"):
        providers.append(
            {
                "id": "anthropic:claude-3-5-sonnet-20240620",
                "name": "Claude 3.5 Sonnet",
                "selected": provider_default == "anthropic" and model_default == "claude-3-5-sonnet-20240620",
            }
        )

    lmstudio_timeout_seconds, cache_ttl_seconds, force_refresh = _lmstudio_catalog_runtime_options()
    local_backends = get_local_openai_backends(
        agent_cfg=app_cfg,
        provider_urls=urls,
        default_provider=provider_default,
        default_model=model_default,
    )
    for backend in local_backends:
        backend_models = _catalog_models_for_local_backend(
            backend,
            timeout_seconds=lmstudio_timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
        )
        if backend_models:
            for item in backend_models[:30]:
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    continue
                providers.append(
                    {
                        "id": f"{backend['provider']}:{model_id}",
                        "name": f"{backend['name']} ({model_id})",
                        "selected": provider_default == backend["provider"] and model_default == model_id,
                    }
                )
        else:
            providers.append(
                {
                    "id": f"{backend['provider']}:model",
                    "name": backend["name"],
                    "selected": provider_default == backend["provider"],
                }
            )

    if not providers:
        providers = [
            {"id": "ollama:llama3", "name": "Ollama (Llama3)", "selected": True},
            {"id": "openai:gpt-4o", "name": "OpenAI (GPT-4o)", "selected": False},
            {"id": "codex:gpt-5-codex", "name": "OpenAI Codex (GPT-5 Codex)", "selected": False},
            {"id": "anthropic:claude-3-5-sonnet-20240620", "name": "Claude 3.5 Sonnet", "selected": False},
            {"id": "lmstudio:model", "name": "LM Studio", "selected": False},
        ]

    return api_response(data=providers)


@config_bp.route("/providers/catalog", methods=["GET"])
@check_auth
def list_provider_catalog():
    """
    Dynamischer Provider/Model-Katalog inklusive einfacher Health/Capability-Infos.
    """
    urls = current_app.config.get("PROVIDER_URLS", {})
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    default_provider = str(app_cfg.get("default_provider") or "")
    default_model = str(app_cfg.get("default_model") or "")
    task_kind = str(request.args.get("task_kind") or "").strip().lower()
    if task_kind not in _BENCH_TASK_KINDS:
        task_kind = ""
    bench_rows, bench_db = _benchmark_rows(task_kind=task_kind, top_n=8 if task_kind else None)
    benchmark_index = {str(item.get("id") or ""): {"rank": idx + 1, "row": item} for idx, item in enumerate(bench_rows)}

    catalog = {
        "default_provider": default_provider,
        "default_model": default_model,
        "providers": [],
    }
    available_model_ids: set[str] = set()

    def _entry(
        pid: str, url: str | None, available: bool, models: list[dict], capabilities: dict | None = None
    ) -> dict:
        recommended_model = None
        if task_kind:
            ranked_models = [m for m in models if isinstance(m, dict) and m.get("recommended_rank")]
            if ranked_models:
                recommended_model = sorted(ranked_models, key=lambda m: int(m.get("recommended_rank") or 9999))[0].get("id")
        return {
            "provider": pid,
            "base_url": url,
            "available": bool(available),
            "model_count": len(models),
            "models": models,
            "capabilities": capabilities or {},
            "recommended_model": recommended_model,
        }

    def _decorate_model(provider_id: str, model_id: str, item: dict) -> dict:
        enriched = dict(item)
        if not task_kind:
            return enriched
        bench_key = f"{provider_id}:{model_id}"
        bench = benchmark_index.get(bench_key)
        if bench:
            enriched["benchmark"] = (bench.get("row") or {}).get("focus") or {}
            enriched["recommended_rank"] = bench.get("rank")
        return enriched

    lmstudio_timeout_seconds, cache_ttl_seconds, force_refresh = _lmstudio_catalog_runtime_options()
    local_backends = get_local_openai_backends(
        agent_cfg=app_cfg,
        provider_urls=urls,
        default_provider=default_provider,
        default_model=default_model,
    )
    for backend in local_backends:
        local_models = []
        for item in _catalog_models_for_local_backend(
            backend,
            timeout_seconds=lmstudio_timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
        ):
            mid = str(item.get("id") or "").strip()
            if not mid:
                continue
            available_model_ids.add(f"{backend['provider']}:{mid}")
            local_models.append(
                _decorate_model(
                    backend["provider"],
                    mid,
                    {
                        "id": mid,
                        "display_name": mid,
                        "context_length": item.get("context_length"),
                        "selected": default_provider == backend["provider"] and default_model == mid,
                    },
                )
            )
        catalog["providers"].append(
            _entry(
                backend["provider"],
                backend.get("base_url"),
                bool(local_models),
                local_models,
                capabilities={
                    "dynamic_models": True,
                    "supports_chat": True,
                    "openai_compatible": True,
                    "transport_provider": backend.get("transport_provider"),
                    "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                },
            )
        )

    ollama_url = urls.get("ollama")
    ollama_models = [
        _decorate_model("ollama", "llama3", {
            "id": "llama3",
            "display_name": "llama3",
            "selected": default_provider == "ollama" and default_model == "llama3",
        }),
        _decorate_model("ollama", "mistral", {
            "id": "mistral",
            "display_name": "mistral",
            "selected": default_provider == "ollama" and default_model == "mistral",
        }),
    ]
    for item in ollama_models:
        available_model_ids.add(f"ollama:{item['id']}")
    catalog["providers"].append(
        _entry(
            "ollama",
            ollama_url,
            bool(ollama_url),
            ollama_models,
            capabilities={"dynamic_models": False},
        )
    )

    openai_url = urls.get("openai")
    openai_models = [
        _decorate_model("openai", "gpt-4o", {
            "id": "gpt-4o",
            "display_name": "gpt-4o",
            "selected": default_provider == "openai" and default_model == "gpt-4o",
        }),
        _decorate_model("openai", "gpt-4-turbo", {
            "id": "gpt-4-turbo",
            "display_name": "gpt-4-turbo",
            "selected": default_provider == "openai" and default_model == "gpt-4-turbo",
        }),
    ]
    for item in openai_models:
        if openai_url or current_app.config.get("OPENAI_API_KEY"):
            available_model_ids.add(f"openai:{item['id']}")
    catalog["providers"].append(
        _entry(
            "openai",
            openai_url,
            bool(openai_url or current_app.config.get("OPENAI_API_KEY")),
            openai_models,
            capabilities={"dynamic_models": False, "requires_api_key": True},
        )
    )
    codex_models = [
        _decorate_model("codex", "gpt-5-codex", {
            "id": "gpt-5-codex",
            "display_name": "gpt-5-codex",
            "selected": default_provider == "codex" and default_model == "gpt-5-codex",
        }),
        _decorate_model("codex", "gpt-5-codex-mini", {
            "id": "gpt-5-codex-mini",
            "display_name": "gpt-5-codex-mini",
            "selected": default_provider == "codex" and default_model == "gpt-5-codex-mini",
        }),
    ]
    for item in codex_models:
        if openai_url or current_app.config.get("OPENAI_API_KEY"):
            available_model_ids.add(f"codex:{item['id']}")
    catalog["providers"].append(
        _entry(
            "codex",
            openai_url,
            bool(openai_url or current_app.config.get("OPENAI_API_KEY")),
            codex_models,
            capabilities={"dynamic_models": False, "requires_api_key": True, "specialization": "code"},
        )
    )

    anthropic_url = urls.get("anthropic")
    anthropic_models = [
        _decorate_model("anthropic", "claude-3-5-sonnet-20240620", {
            "id": "claude-3-5-sonnet-20240620",
            "display_name": "claude-3-5-sonnet-20240620",
            "selected": default_provider == "anthropic" and default_model == "claude-3-5-sonnet-20240620",
        })
    ]
    for item in anthropic_models:
        if anthropic_url or current_app.config.get("ANTHROPIC_API_KEY"):
            available_model_ids.add(f"anthropic:{item['id']}")
    catalog["providers"].append(
        _entry(
            "anthropic",
            anthropic_url,
            bool(anthropic_url or current_app.config.get("ANTHROPIC_API_KEY")),
            anthropic_models,
            capabilities={"dynamic_models": False, "requires_api_key": True},
        )
    )

    if task_kind:
        recommended_items = [
            {
                "id": row.get("id"),
                "provider": row.get("provider"),
                "model": row.get("model"),
                "suitability_score": ((row.get("focus") or {}).get("suitability_score")),
                "available": str(row.get("id") or "") in available_model_ids,
            }
            for row in bench_rows[:5]
        ]
        catalog["recommendations"] = {
            "task_kind": task_kind,
            "updated_at": bench_db.get("updated_at"),
            "items": recommended_items,
        }
        selected = next((item for item in recommended_items if item.get("available")), None)
        if selected:
            catalog["selection"] = {
                "task_kind": task_kind,
                "provider": selected.get("provider"),
                "model": selected.get("model"),
                "id": selected.get("id"),
                "selection_source": "benchmarks_available_top_ranked",
            }

    return api_response(data=catalog)


@config_bp.route("/templates", methods=["GET"])
@check_auth
def list_templates():
    tpls = template_repo.get_all()
    return api_response(data=[t.model_dump() for t in tpls])


@config_bp.route("/templates", methods=["POST"])
@admin_required
@validate_request(TemplateCreateRequest)
def create_template():
    data: TemplateCreateRequest = g.validated_data
    prompt_tpl = data.prompt_template

    unknown = validate_template_variables(prompt_tpl)
    warnings = []
    if unknown:
        warnings.append(
            {
                "type": "unknown_variables",
                "details": f"Unknown variables: {', '.join(unknown)}",
                "allowed": list(_get_template_allowlist()),
            }
        )

    new_tpl = TemplateDB(name=data.name, description=data.description, prompt_template=prompt_tpl)
    template_repo.save(new_tpl)
    log_audit("template_created", {"template_id": new_tpl.id, "name": new_tpl.name})
    res = new_tpl.model_dump()
    if warnings:
        res["warnings"] = warnings
    return api_response(data=res, code=201)


@config_bp.route("/templates/<tpl_id>", methods=["PUT", "PATCH"])
@admin_required
def update_template(tpl_id):
    data = request.get_json()
    tpl = template_repo.get_by_id(tpl_id)
    if not tpl:
        return api_response(status="error", message="not_found", code=404)

    warnings = []
    if "prompt_template" in data:
        unknown = validate_template_variables(data["prompt_template"])
        if unknown:
            warnings.append(
                {
                    "type": "unknown_variables",
                    "details": f"Unknown variables: {', '.join(unknown)}",
                    "allowed": list(_get_template_allowlist()),
                }
            )
        tpl.prompt_template = data["prompt_template"]

    if "name" in data:
        tpl.name = data["name"]
    if "description" in data:
        tpl.description = data["description"]

    template_repo.save(tpl)
    log_audit("template_updated", {"template_id": tpl_id, "name": tpl.name})
    res = tpl.model_dump()
    if warnings:
        res["warnings"] = warnings
    return api_response(data=res)


@config_bp.route("/templates/<tpl_id>", methods=["DELETE"])
@admin_required
def delete_template(tpl_id):
    try:
        with Session(engine) as session:
            tpl = session.get(TemplateDB, tpl_id)
            if not tpl:
                return api_response(status="error", message="not_found", code=404)

            roles = session.exec(select(RoleDB).where(RoleDB.default_template_id == tpl_id)).all()
            links = session.exec(select(TeamTypeRoleLink).where(TeamTypeRoleLink.template_id == tpl_id)).all()
            members = session.exec(select(TeamMemberDB).where(TeamMemberDB.custom_template_id == tpl_id)).all()
            teams = session.exec(select(TeamDB)).all()

            cleared = {
                "roles": [r.id for r in roles],
                "team_type_links": [link.role_id for link in links],
                "team_members": [m.id for m in members],
                "teams": [],
            }

            for role in roles:
                role.default_template_id = None
                session.add(role)
            for link in links:
                link.template_id = None
                session.add(link)
            for member in members:
                member.custom_template_id = None
                session.add(member)
            for team in teams:
                if isinstance(team.role_templates, dict) and tpl_id in team.role_templates.values():
                    team.role_templates = {k: v for k, v in team.role_templates.items() if v != tpl_id}
                    cleared["teams"].append(team.id)
                    session.add(team)

            if any(cleared.values()):
                current_app.logger.warning(f"Template delete clearing references: {tpl_id} refs={cleared}")

            session.delete(tpl)
            session.commit()

            log_audit("template_deleted", {"template_id": tpl_id, "cleared_refs": cleared})
            return api_response(data={"status": "deleted", "cleared": cleared})
    except Exception as e:
        current_app.logger.exception(f"Template delete failed for {tpl_id}: {e}")
        return api_response(
            status="error", message="delete_failed", data={"details": "Template delete failed"}, code=500
        )


@config_bp.route("/llm/generate", methods=["POST"])
@check_auth
@rate_limit(limit=30, window=60)
def llm_generate():  # noqa: C901
    """
    LLM-Generierung mit Tool-Calling Unterstützung
    """
    request_id = str(uuid.uuid4())
    g.llm_request_id = request_id

    def _log(event: str, **kwargs):
        try:
            log_llm_entry(event=event, request_id=request_id, **kwargs)
        except Exception:
            pass

    raw_data = request.get_json()

    def _preflight_with_meta(payload: dict, raw_payload: dict | None = None) -> dict:
        raw_payload = raw_payload if isinstance(raw_payload, dict) else {}
        return {
            **payload,
            "routing": {
                "policy_version": "llm-generate-v1",
                "requested": {
                    "provider": str(raw_payload.get("config", {}).get("provider") or "").strip() or None
                    if isinstance(raw_payload.get("config"), dict)
                    else None,
                    "model": str(raw_payload.get("config", {}).get("model") or "").strip() or None
                    if isinstance(raw_payload.get("config"), dict)
                    else None,
                    "base_url": str(raw_payload.get("config", {}).get("base_url") or "").strip() or None
                    if isinstance(raw_payload.get("config"), dict)
                    else None,
                },
                "effective": {"provider": None, "model": None, "base_url": None},
                "fallback": {
                    "provider_source": "preflight_validation",
                    "model_source": "preflight_validation",
                    "base_url_source": "preflight_validation",
                },
            },
        }

    data = {} if raw_data is None else raw_data
    if not isinstance(data, dict):
        _log("llm_error", error="invalid_json")
        return api_response(status="error", message="invalid_json", data=_preflight_with_meta({}), code=400)

    user_prompt = data.get("prompt") or ""
    tool_calls_input = data.get("tool_calls")
    confirm_tool_calls = bool(data.get("confirm_tool_calls") or data.get("confirmed"))
    stream = bool(data.get("stream"))
    if not user_prompt and not tool_calls_input:
        _log("llm_error", error="missing_prompt")
        return api_response(status="error", message="missing_prompt", data=_preflight_with_meta({}, data), code=400)

    # LLM-Konfiguration und Tool-Allowlist
    agent_cfg = current_app.config.get("AGENT_CONFIG", {})
    llm_cfg = agent_cfg.get("llm_config", {})

    is_admin = getattr(g, "is_admin", False)
    denylist_cfg = agent_cfg.get("llm_tool_denylist", [])
    capability_contract = build_capability_contract(agent_cfg)
    allowed_tools = resolve_allowed_tools(agent_cfg, is_admin=is_admin, contract=capability_contract)
    capability_meta = describe_capabilities(capability_contract, allowed_tools=allowed_tools, is_admin=is_admin)

    # Tool-Definitionen für den Prompt (gefiltert)
    tools_desc = json.dumps(
        tool_registry.get_tool_definitions(allowlist=allowed_tools, denylist=denylist_cfg), indent=2, ensure_ascii=False
    )

    system_instruction = f"""Du bist ein hilfreicher KI-Assistent für das Ananta Framework.
Dir stehen folgende Werkzeuge zur Verfügung:
{tools_desc}
"""

    context = data.get("context")
    if context:
        system_instruction += (
            f"\nAktueller Kontext (Templates, Rollen, Teams):\n{json.dumps(context, indent=2, ensure_ascii=False)}\n"
        )

    system_instruction += """
Wenn du eine Aktion ausführen möchtest, antworte AUSSCHLIESSLICH im folgenden JSON-Format.
Beginne die Antwort mit '{' und ende mit '}'. Keine Vor- oder Nachtexte, kein Markdown, kein Prefix wie 'Assistant:'.
{
  "thought": "Deine Überlegung, warum du dieses Tool wählst",
  "tool_calls": [
    { "name": "tool_name", "args": { "arg1": "value1" } }
  ],
  "answer": "Eine kurze Bestätigung für den Nutzer, was du tust"
}

Falls keine Aktion nötig ist, antworte ebenfalls als JSON-Objekt mit leerem tool_calls.

"""
    if stream:
        system_instruction += "\nAntworte im Streaming-Modus als Klartext ohne tool_calls oder JSON.\n"

    history = data.get("history", [])
    if not isinstance(history, list):
        history = []
    # System-Instruction als erste Nachricht in der Historie mitgeben
    full_history = [{"role": "system", "content": system_instruction}] + history

    # LLM-Parameter auflösen
    cfg = data.get("config") or {}
    requested_provider = str(cfg.get("provider") or "").strip()
    requested_model = str(cfg.get("model") or "").strip()
    requested_base_url = str(cfg.get("base_url") or "").strip()
    inferred_task_kind = normalize_task_kind(data.get("task_kind"), user_prompt or "")

    provider = cfg.get("provider") or llm_cfg.get("provider") or agent_cfg.get("default_provider")
    model = cfg.get("model") or llm_cfg.get("model") or agent_cfg.get("default_model")
    base_url = cfg.get("base_url") or llm_cfg.get("base_url")
    api_key = cfg.get("api_key") or llm_cfg.get("api_key")
    timeout_val = cfg.get("timeout")
    temperature_val = cfg.get("temperature")
    if temperature_val is None:
        temperature_val = llm_cfg.get("temperature")
    context_limit_val = cfg.get("context_limit")
    if context_limit_val is None:
        context_limit_val = llm_cfg.get("context_limit")

    try:
        temperature_val = float(temperature_val) if temperature_val is not None else None
    except (TypeError, ValueError):
        temperature_val = None
    if temperature_val is not None:
        temperature_val = max(0.0, min(2.0, temperature_val))

    try:
        context_limit_val = int(context_limit_val) if context_limit_val is not None else None
    except (TypeError, ValueError):
        context_limit_val = None
    if context_limit_val is not None:
        context_limit_val = max(256, min(200000, context_limit_val))

    provider_source = "agent_config.default_provider"
    if cfg.get("provider"):
        provider_source = "request.config.provider"
    elif llm_cfg.get("provider"):
        provider_source = "agent_config.llm_config.provider"

    model_source = "agent_config.default_model"
    if cfg.get("model"):
        model_source = "request.config.model"
    elif llm_cfg.get("model"):
        model_source = "agent_config.llm_config.model"

    api_key_profile = cfg.get("api_key_profile") or llm_cfg.get("api_key_profile")
    base_url, base_url_source = _resolve_provider_base_url(
        provider=provider,
        requested_base_url=cfg.get("base_url"),
        llm_cfg=llm_cfg,
        agent_cfg=agent_cfg,
        provider_urls=current_app.config.get("PROVIDER_URLS", {}),
    )
    api_key = _resolve_provider_api_key(
        provider=provider,
        explicit_api_key=api_key,
        api_key_profile=api_key_profile,
        agent_cfg=agent_cfg,
    )
    recommendation = None
    if not requested_provider and not requested_model:
        recommendation = _recommend_runtime_selection(
            task_kind=inferred_task_kind,
            current_provider=str(provider or "").strip().lower() or None,
            current_model=str(model or "").strip() or None,
            agent_cfg=agent_cfg,
            provider_urls=current_app.config.get("PROVIDER_URLS", {}),
        )
        if recommendation:
            provider = recommendation["provider"]
            model = recommendation["model"]
            provider_source = recommendation["selection_source"]
            model_source = recommendation["selection_source"]
            base_url, base_url_source = _resolve_provider_base_url(
                provider=provider,
                requested_base_url=cfg.get("base_url"),
                llm_cfg=llm_cfg,
                agent_cfg=agent_cfg,
                provider_urls=current_app.config.get("PROVIDER_URLS", {}),
            )
            api_key = _resolve_provider_api_key(
                provider=provider,
                explicit_api_key=api_key,
                api_key_profile=api_key_profile,
                agent_cfg=agent_cfg,
            )
    local_backend = resolve_local_openai_backend(
        provider,
        agent_cfg=agent_cfg,
        provider_urls=current_app.config.get("PROVIDER_URLS", {}),
        default_provider=str(agent_cfg.get("default_provider") or ""),
        default_model=str(agent_cfg.get("default_model") or ""),
    )
    transport_provider = str(local_backend.get("transport_provider") or "openai") if local_backend else provider

    llm_routing_meta = {
        "policy_version": "llm-generate-v1",
        "task_kind": inferred_task_kind,
        "requested": {
            "provider": requested_provider or None,
            "model": requested_model or None,
            "base_url": requested_base_url or None,
        },
        "effective": {
            "provider": str(provider or "").strip() or None,
            "transport_provider": str(transport_provider or "").strip() or None,
            "model": str(model or "").strip() or None,
            "base_url": str(base_url or "").strip() or None,
        },
        "fallback": {
            "provider_source": provider_source,
            "model_source": model_source,
            "base_url_source": base_url_source,
        },
    }
    if recommendation:
        llm_routing_meta["recommendation"] = recommendation

    def _with_meta(payload: dict) -> dict:
        return {**payload, "routing": llm_routing_meta, "assistant_capabilities": capability_meta}

    if not provider:
        _log("llm_error", error="llm_not_configured", reason="missing_provider")
        current_app.logger.warning("LLM request blocked: provider missing")
        return api_response(
            status="error",
            message="llm_not_configured",
            data=_with_meta({"details": "LLM provider is not configured"}),
            code=400,
        )

    if transport_provider in {"openai", "codex", "anthropic"} and not api_key:
        _log("llm_error", error="llm_api_key_missing", provider=provider)
        current_app.logger.warning(f"LLM request blocked: api_key missing for {provider}")
        return api_response(
            status="error",
            message="llm_api_key_missing",
            data=_with_meta({"details": f"API key missing for {provider}"}),
            code=400,
        )

    if not base_url:
        _log("llm_error", error="llm_base_url_missing", provider=provider)
        current_app.logger.warning(f"LLM request blocked: base_url missing for {provider}")
        return api_response(
            status="error",
            message="llm_base_url_missing",
            data=_with_meta({"details": f"Base URL missing for {provider}"}),
            code=400,
        )

    _log(
        "llm_request",
        prompt=user_prompt,
        stream=stream,
        confirm_tool_calls=confirm_tool_calls,
        tool_calls_input=tool_calls_input,
        history_len=len(history) if isinstance(history, list) else 0,
        provider=provider,
        transport_provider=transport_provider,
        model=model,
        base_url=base_url,
        is_admin=is_admin,
    )

    def _extract_json(text: str) -> dict | None:
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1].split("```")[0].strip()
        if clean_text.lower().startswith("assistant:"):
            clean_text = clean_text.split(":", 1)[1].strip()
        # Strip leading/trailing chatter around a JSON object/array.
        first_brace = clean_text.find("{")
        first_bracket = clean_text.find("[")
        if first_brace == -1 and first_bracket == -1:
            return None
        if first_brace == -1:
            start = first_bracket
            end = clean_text.rfind("]")
        elif first_bracket == -1:
            start = first_brace
            end = clean_text.rfind("}")
        else:
            start = min(first_brace, first_bracket)
            end = clean_text.rfind("}" if start == first_brace else "]")
        if end == -1:
            return None
        clean_text = clean_text[start : end + 1].strip()
        try:
            return json.loads(clean_text)
        except Exception:
            return None

    response_text = ""
    res_json = None
    tool_calls = []

    if tool_calls_input and confirm_tool_calls:
        if not isinstance(tool_calls_input, list):
            _log("llm_error", error="invalid_tool_calls")
            return api_response(status="error", message="invalid_tool_calls", code=400)
        tool_calls = tool_calls_input
        res_json = {"answer": ""}
    else:
        response_text = generate_text(
            prompt=user_prompt,
            provider=transport_provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            history=full_history,
            temperature=temperature_val,
            max_context_tokens=context_limit_val,
            timeout=timeout_val,
        )
        if not response_text or not response_text.strip():
            _log("llm_error", error="llm_empty_response")
            return api_response(
                data=_with_meta({"response": "LLM returned empty response. Please try again."}), status="ok"
            )

        if stream:
            _log("llm_response", response=response_text, tool_calls=[], status="stream")

            def _event_stream(text: str):
                chunk_size = 80
                for i in range(0, len(text), chunk_size):
                    chunk = text[i : i + chunk_size]
                    yield f"data: {chunk}\\n\\n"
                yield "event: done\\ndata: [DONE]\\n\\n"

            return Response(stream_with_context(_event_stream(response_text)), mimetype="text/event-stream")

        res_json = _extract_json(response_text)
        if res_json is None:
            _log("llm_response", response=response_text, tool_calls=[], status="no_json")
            inferred_tool_calls = _infer_tool_calls_from_prompt(
                user_prompt, context=context if isinstance(context, dict) else None
            )
            if inferred_tool_calls:
                res_json = {
                    "answer": "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen.",
                    "tool_calls": inferred_tool_calls,
                    "thought": "Intent fallback",
                }
            # If it's just plain text and no JSON was expected but LLM gave it anyway, or vice-versa.
            # We try to wrap it if it looks like a simple answer.
            elif response_text and len(response_text.strip()) > 0:
                res_json = {"answer": response_text.strip(), "tool_calls": [], "thought": ""}
            else:
                repair_prompt = (
                    f"Assistant (invalid JSON): {response_text}\n\n"
                    "System: Antworte AUSSCHLIESSLICH mit gueltigem JSON im oben beschriebenen Format. "
                    "Beginne mit '{' und ende mit '}'. Kein Freitext, keine Markdown-Bloecke, "
                    "kein Prefix wie 'Assistant:'."
                )
                response_text = generate_text(
                    prompt=repair_prompt,
                    provider=transport_provider,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    history=full_history,
                    temperature=temperature_val,
                    max_context_tokens=context_limit_val,
                    timeout=timeout_val,
                )
                if not response_text or not response_text.strip():
                    _log("llm_error", error="llm_empty_response")
                    return api_response(
                        data=_with_meta({"response": "LLM returned empty response during repair. Please try again."}),
                        status="ok",
                    )
                res_json = _extract_json(response_text)
                if res_json is None:
                    inferred_tool_calls = _infer_tool_calls_from_prompt(
                        user_prompt, context=context if isinstance(context, dict) else None
                    )
                    if inferred_tool_calls:
                        res_json = {
                            "answer": "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen.",
                            "tool_calls": inferred_tool_calls,
                            "thought": "Intent fallback",
                        }
                if res_json is None and response_text:
                    res_json = {"answer": response_text.strip(), "tool_calls": [], "thought": ""}

        if res_json is None:
            _log("llm_response", response=response_text, tool_calls=[], status="no_json")
            return api_response(data=_with_meta({"response": response_text}))

        tool_calls = res_json.get("tool_calls", [])
        if not tool_calls:
            inferred_tool_calls = _infer_tool_calls_from_prompt(
                user_prompt, context=context if isinstance(context, dict) else None
            )
            if inferred_tool_calls:
                tool_calls = inferred_tool_calls
                res_json["tool_calls"] = inferred_tool_calls
                if not res_json.get("answer"):
                    res_json["answer"] = "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen."
        if tool_calls and not confirm_tool_calls:
            if not is_admin:
                _log("llm_blocked", tool_calls=tool_calls, reason="admin_required")
                return api_response(
                    data=_with_meta(
                        {
                            "response": res_json.get("answer") or "Tool calls require admin privileges.",
                            "tool_calls": tool_calls,
                            "blocked": True,
                        }
                    )
                )
            _log("llm_requires_confirmation", tool_calls=tool_calls)
            return api_response(
                data=_with_meta(
                    {
                        "response": res_json.get("answer"),
                        "requires_confirmation": True,
                        "thought": res_json.get("thought"),
                        "tool_calls": tool_calls,
                    }
                )
            )

    if tool_calls_input and confirm_tool_calls and not tool_calls:
        _log("llm_no_tool_calls")
        return api_response(data=_with_meta({"response": "No tool calls to execute."}))

    if tool_calls and not confirm_tool_calls:
        _log("llm_requires_confirmation", tool_calls=tool_calls)
        return api_response(
            data=_with_meta(
                {
                    "response": "Pending actions require confirmation.",
                    "requires_confirmation": True,
                    "tool_calls": tool_calls,
                }
            )
        )

    if tool_calls:
        if not is_admin:
            return api_response(
                status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403
            )

        blocked_tools, blocked_reasons_by_tool = validate_tool_calls_against_contract(
            tool_calls, allowed_tools=allowed_tools, contract=capability_contract, is_admin=is_admin
        )

        if blocked_tools:
            log_audit("tool_calls_blocked", {"tools": blocked_tools, "reasons_by_tool": blocked_reasons_by_tool})
            _log("llm_blocked", tool_calls=blocked_tools, reason="tool_not_allowed")
            blocked_results = [
                {
                    "tool": name,
                    "success": False,
                    "output": None,
                    "error": blocked_reasons_by_tool.get(name, "tool_not_allowed"),
                }
                for name in blocked_tools
            ]
            return api_response(
                data=_with_meta(
                    {
                        "response": f"Tool calls blocked: {', '.join(blocked_tools)}",
                        "tool_results": blocked_results,
                        "blocked_tools": blocked_tools,
                        "blocked_reasons_by_tool": blocked_reasons_by_tool,
                    }
                )
            )

        token_usage = {
            "prompt_tokens": estimate_text_tokens(user_prompt),
            "history_tokens": estimate_text_tokens(json.dumps(full_history, ensure_ascii=False)),
            "completion_tokens": estimate_text_tokens(response_text or json.dumps(res_json or {}, ensure_ascii=False)),
            "tool_calls_tokens": estimate_tool_calls_tokens(tool_calls),
        }
        provider_usage = getattr(g, "llm_last_usage", {}) if has_request_context() else {}
        if isinstance(provider_usage, dict) and provider_usage:
            if provider_usage.get("prompt_tokens") is not None:
                token_usage["prompt_tokens"] = int(provider_usage.get("prompt_tokens") or 0)
            if provider_usage.get("completion_tokens") is not None:
                token_usage["completion_tokens"] = int(provider_usage.get("completion_tokens") or 0)
            if provider_usage.get("total_tokens") is not None:
                token_usage["estimated_total_tokens"] = int(provider_usage.get("total_tokens") or 0)
            token_usage["provider_usage"] = {
                "prompt_tokens": int(provider_usage.get("prompt_tokens") or 0),
                "completion_tokens": int(provider_usage.get("completion_tokens") or 0),
                "total_tokens": int(provider_usage.get("total_tokens") or 0),
            }
            token_usage["token_source"] = "provider_usage"
        else:
            token_usage["token_source"] = "estimated"
        if token_usage.get("estimated_total_tokens") is None:
            token_usage["estimated_total_tokens"] = (
                int(token_usage.get("prompt_tokens") or 0)
                + int(token_usage.get("history_tokens") or 0)
                + int(token_usage.get("completion_tokens") or 0)
                + int(token_usage.get("tool_calls_tokens") or 0)
            )
        guardrail_decision = evaluate_tool_call_guardrails(tool_calls, agent_cfg, token_usage=token_usage)
        if not guardrail_decision.allowed:
            details = {
                "tools": guardrail_decision.blocked_tools,
                "reasons": guardrail_decision.reasons,
                **guardrail_decision.details,
            }
            log_audit("tool_calls_guardrail_blocked", details)
            _log("llm_blocked", tool_calls=guardrail_decision.blocked_tools, reason="tool_guardrail_blocked")
            blocked_results = [
                {"tool": name, "success": False, "output": None, "error": "tool_guardrail_blocked"}
                for name in (guardrail_decision.blocked_tools or [tc.get("name", "<missing>") for tc in tool_calls])
            ]
            return api_response(
                data=_with_meta(
                    {
                        "response": "Tool calls blocked by guardrails.",
                        "tool_results": blocked_results,
                        "blocked_tools": guardrail_decision.blocked_tools,
                        "blocked_reasons": guardrail_decision.reasons,
                        "guardrails": guardrail_decision.details,
                    }
                )
            )

        results = []
        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            current_app.logger.info(f"KI ruft Tool auf: {name} mit {args}")
            tool_res = tool_registry.execute(name, args)
            results.append(
                {"tool": name, "success": tool_res.success, "output": tool_res.output, "error": tool_res.error}
            )
        _log("llm_tool_results", tool_calls=tool_calls, results=results)

        tool_history = full_history + [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": json.dumps({"tool_calls": tool_calls})},
            {"role": "system", "content": f"Tool Results: {json.dumps(results)}"},
        ]

        final_response = generate_text(
            prompt="Bitte gib eine finale Antwort an den Nutzer basierend auf diesen Ergebnissen.",
            provider=transport_provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            history=tool_history,
            temperature=temperature_val,
            max_context_tokens=context_limit_val,
            timeout=timeout_val,
        )
        if not final_response or not final_response.strip():
            _log("llm_error", error="llm_empty_response")
            return api_response(
                status="error", message="llm_failed", data={"details": "LLM returned empty response"}, code=502
            )

        if stream:
            _log("llm_response", response=final_response, tool_calls=tool_calls, status="stream")

            def _event_stream(text: str):
                chunk_size = 80
                for i in range(0, len(text), chunk_size):
                    chunk = text[i : i + chunk_size]
                    yield f"data: {chunk}\\n\\n"
                yield "event: done\\ndata: [DONE]\\n\\n"

            return Response(stream_with_context(_event_stream(final_response)), mimetype="text/event-stream")
        _log("llm_response", response=final_response, tool_calls=tool_calls, status="tool_results")
        return api_response(data=_with_meta({"response": final_response, "tool_results": results}))

    final_text = res_json.get("answer", response_text)
    _log("llm_response", response=final_text, tool_calls=tool_calls, status="ok")
    return api_response(data=_with_meta({"response": final_text}))
