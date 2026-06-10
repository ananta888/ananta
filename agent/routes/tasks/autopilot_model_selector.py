from __future__ import annotations

import math
from typing import Any

from agent.llm_benchmarks import recommend_model_for_context
from agent.model_selection import normalize_legacy_model_name
from agent.services.repository_registry import get_repository_registry
from agent.services.task_template_resolution import resolve_task_role_template


def _normalize_override_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip().lower()
        normalized_value = str(value or "").strip()
        if normalized_key and normalized_value:
            out[normalized_key] = normalized_value
    return out


def _normalize_model_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        model_name = str(item or "").strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        out.append(model_name)
    return out


def _normalize_temperature_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized):
        return None
    if normalized < 0.0:
        normalized = 0.0
    if normalized > 2.0:
        normalized = 2.0
    return round(normalized, 3)


def _normalize_temperature_list(raw: Any) -> list[float]:
    if not isinstance(raw, list):
        return []
    out: list[float] = []
    seen: set[float] = set()
    for item in raw:
        normalized = _normalize_temperature_value(item)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _preferred_benchmark_provider(cfg: dict[str, Any]) -> str | None:
    llm_cfg = cfg.get("llm_config") if isinstance(cfg.get("llm_config"), dict) else {}
    for candidate in (llm_cfg.get("provider"), cfg.get("default_provider"), cfg.get("provider")):
        provider = str(candidate or "").strip().lower()
        if provider:
            return provider
    return None


def _normalize_model_candidate(model: str | None, *, cfg: dict[str, Any]) -> str | None:
    return normalize_legacy_model_name(model, provider=_preferred_benchmark_provider(cfg))


def _select_model_for_task(*, loop: Any, task: Any, excluded_models: set[str] | None = None) -> tuple[str | None, dict[str, Any]]:
    cfg = loop._agent_config() or {}
    role_overrides = _normalize_override_map(cfg.get("role_model_overrides"))
    template_overrides = _normalize_override_map(cfg.get("template_model_overrides"))
    task_kind_overrides = _normalize_override_map(cfg.get("task_kind_model_overrides"))
    excluded = {str(item or "").strip() for item in (excluded_models or set()) if str(item or "").strip()}

    task_kind = str(getattr(task, "task_kind", None) or "").strip().lower()
    repos = get_repository_registry()
    resolved = resolve_task_role_template(task, repos=repos)
    role_id = str(resolved.get("role_id") or "").strip()
    role_name = str(resolved.get("role_name") or "").strip()
    template_id = str(resolved.get("template_id") or "").strip()
    template_name = str(resolved.get("template_name") or "").strip()

    selected_model = None
    source = "none"
    if role_name and role_name.lower() in role_overrides:
        candidate = _normalize_model_candidate(role_overrides[role_name.lower()], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "role_model_overrides"
    elif template_name and template_name.lower() in template_overrides:
        candidate = _normalize_model_candidate(template_overrides[template_name.lower()], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "template_model_overrides:name"
    elif template_id and template_id.lower() in template_overrides:
        candidate = _normalize_model_candidate(template_overrides[template_id.lower()], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "template_model_overrides:id"
    elif task_kind and task_kind in task_kind_overrides:
        candidate = _normalize_model_candidate(task_kind_overrides[task_kind], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "task_kind_model_overrides"

    if selected_model is None:
        default_model_candidate = _normalize_model_candidate(str(cfg.get("default_model") or cfg.get("model") or "").strip(), cfg=cfg)
        if default_model_candidate and default_model_candidate not in excluded:
            selected_model = default_model_candidate
            source = "agent_default_model"

    if selected_model is None and bool(cfg.get("adaptive_model_routing_enabled", True)):
        try:
            app = getattr(loop, "_app", None)
            data_dir = (getattr(app, "config", {}) or {}).get("DATA_DIR") or "data"
            preferred_provider = _preferred_benchmark_provider(cfg)
            learned = recommend_model_for_context(
                data_dir=data_dir,
                task_kind=task_kind or "analysis",
                role_name=role_name or None,
                template_name=template_name or None,
                provider=preferred_provider,
                min_samples=int(cfg.get("adaptive_model_routing_min_samples") or 3),
            )
            if isinstance(learned, dict) and str(learned.get("model") or "").strip():
                candidate = _normalize_model_candidate(str(learned.get("model") or "").strip(), cfg=cfg)
                if candidate not in excluded:
                    selected_model = candidate
                    source = str(learned.get("selection_source") or "benchmark_context_learning")
                else:
                    source = "benchmark_context_learning:excluded"
        except Exception:
            pass

    return selected_model, {
        "selected_model": selected_model,
        "source": source,
        "role_id": role_id or None,
        "role_name": role_name or None,
        "template_id": template_id or None,
        "template_name": template_name or None,
        "task_kind": task_kind or None,
    }
