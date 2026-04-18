from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.service_registry import get_core_services

from . import shared

providers_bp = Blueprint("config_providers", __name__)


def _decorate_model(provider_id: str, model_id: str, item: dict, task_kind: str, benchmark_index: dict[str, dict]) -> dict:
    enriched = dict(item)
    if not task_kind:
        return enriched
    bench = benchmark_index.get(f"{provider_id}:{model_id}")
    if bench:
        enriched["benchmark"] = (bench.get("row") or {}).get("focus") or {}
        enriched["recommended_rank"] = bench.get("rank")
    return enriched


def _catalog_entry(pid: str, url: str | None, available: bool, models: list[dict], capabilities: dict | None = None, task_kind: str = "") -> dict:
    recommended_model = None
    if task_kind:
        ranked_models = [item for item in models if isinstance(item, dict) and item.get("recommended_rank")]
        if ranked_models:
            ranked_models.sort(key=lambda item: int(item.get("recommended_rank") or 9999))
            recommended_model = ranked_models[0].get("id")
    return {
        "provider": pid,
        "base_url": url,
        "available": bool(available),
        "model_count": len(models),
        "models": models,
        "capabilities": capabilities or {},
        "recommended_model": recommended_model,
    }


def _provider_specs(*, app_cfg: dict, urls: dict, default_provider: str, default_model: str) -> list[dict]:
    return get_core_services().integration_registry_service.list_inference_provider_specs(
        agent_cfg=app_cfg,
        provider_urls=urls,
        default_provider=default_provider,
        default_model=default_model,
        has_openai_api_key=bool(current_app.config.get("OPENAI_API_KEY")),
        has_anthropic_api_key=bool(current_app.config.get("ANTHROPIC_API_KEY")),
    )


@providers_bp.route("/providers", methods=["GET"])
@check_auth
def list_providers():
    urls = current_app.config.get("PROVIDER_URLS", {})
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    provider_default = str(app_cfg.get("default_provider") or "")
    model_default = str(app_cfg.get("default_model") or "")
    providers = []

    for spec in _provider_specs(app_cfg=app_cfg, urls=urls, default_provider=provider_default, default_model=model_default):
        if not spec.get("available") and not bool((spec.get("capabilities") or {}).get("dynamic_models")):
            continue
        provider = str(spec.get("provider") or "")
        display_name = str(spec.get("display_name") or provider)
        for model_id in list(spec.get("models") or []):
            model = str(model_id or "").strip()
            if not model:
                continue
            providers.append(
                {
                    "id": f"{provider}:{model}",
                    "name": f"{display_name} ({model})",
                    "selected": provider_default == provider and model_default == model,
                }
            )

    timeout_seconds, cache_ttl_seconds, force_refresh = shared.lmstudio_catalog_runtime_options()
    local_backends = [item for item in _provider_specs(app_cfg=app_cfg, urls=urls, default_provider=provider_default, default_model=model_default) if bool((item.get("capabilities") or {}).get("dynamic_models"))]
    for backend in local_backends:
        backend_models = shared.catalog_models_for_local_backend(
            backend,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
        )
        if backend_models:
            for item in backend_models[:30]:
                model_id = str(item.get("id") or "").strip()
                if model_id:
                    backend_display = str(backend["display_name"])
                    if str(backend.get("provider_type") or "") == "remote_ananta":
                        backend_display = f"{backend_display} (Remote Ananta)"
                    providers.append(
                        {
                            "id": f"{backend['provider']}:{model_id}",
                            "name": f"{backend_display} ({model_id})",
                            "selected": provider_default == backend["provider"] and model_default == model_id,
                        }
                    )
        else:
            backend_display = str(backend["display_name"])
            if str(backend.get("provider_type") or "") == "remote_ananta":
                backend_display = f"{backend_display} (Remote Ananta)"
            providers.append({"id": f"{backend['provider']}:model", "name": backend_display, "selected": provider_default == backend["provider"]})

    if not providers:
        providers = [
            {"id": "ollama:llama3", "name": "Ollama (Llama3)", "selected": True},
            {"id": "openai:gpt-4o", "name": "OpenAI (GPT-4o)", "selected": False},
            {"id": "codex:gpt-5-codex", "name": "OpenAI Codex (GPT-5 Codex)", "selected": False},
            {"id": "anthropic:claude-3-5-sonnet-20240620", "name": "Claude 3.5 Sonnet", "selected": False},
            {"id": "lmstudio:model", "name": "LM Studio", "selected": False},
        ]
    return api_response(data=providers)


@providers_bp.route("/providers/catalog", methods=["GET"])
@check_auth
def list_provider_catalog():
    urls = current_app.config.get("PROVIDER_URLS", {})
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    default_provider = str(app_cfg.get("default_provider") or "")
    default_model = str(app_cfg.get("default_model") or "")
    task_kind = str(request.args.get("task_kind") or "").strip().lower()
    if task_kind not in shared._BENCH_TASK_KINDS:
        task_kind = ""

    bench_rows, bench_db = shared.benchmark_rows_for_task(task_kind=task_kind, top_n=8 if task_kind else None)
    benchmark_index = {str(item.get("id") or ""): {"rank": index + 1, "row": item} for index, item in enumerate(bench_rows)}
    catalog = {"default_provider": default_provider, "default_model": default_model, "providers": []}
    available_model_ids: set[str] = set()

    timeout_seconds, cache_ttl_seconds, force_refresh = shared.lmstudio_catalog_runtime_options()
    provider_specs = _provider_specs(app_cfg=app_cfg, urls=urls, default_provider=default_provider, default_model=default_model)
    local_backends = [item for item in provider_specs if bool((item.get("capabilities") or {}).get("dynamic_models"))]
    for backend in local_backends:
        local_models = []
        discovered_models = list(
            shared.catalog_models_for_local_backend(
                backend,
                timeout_seconds=timeout_seconds,
                cache_ttl_seconds=cache_ttl_seconds,
                force_refresh=force_refresh,
            )
        )
        if not discovered_models:
            discovered_models = [{"id": model_id} for model_id in list(backend.get("models") or [])]
        for item in discovered_models:
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            if bool(backend.get("available")):
                available_model_ids.add(f"{backend['provider']}:{model_id}")
            local_models.append(
                _decorate_model(
                    backend["provider"],
                    model_id,
                    {
                        "id": model_id,
                        "display_name": model_id,
                        "context_length": item.get("context_length"),
                        "selected": default_provider == backend["provider"] and default_model == model_id,
                    },
                    task_kind,
                    benchmark_index,
                )
            )
        catalog["providers"].append(
            _catalog_entry(
                backend["provider"],
                backend.get("base_url"),
                bool(backend.get("available")) and bool(local_models or list(backend.get("models") or [])),
                local_models,
                capabilities={
                    "dynamic_models": True,
                    "supports_chat": True,
                    "openai_compatible": True,
                    "transport_provider": backend.get("transport_provider"),
                    "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                    "provider_type": backend.get("provider_type") or "local_openai_compatible",
                    "remote_hub": bool(backend.get("remote_hub")),
                    "instance_id": backend.get("instance_id"),
                    "max_hops": backend.get("max_hops"),
                    "remote_hub_policy": (backend.get("capabilities") or {}).get("remote_hub_policy"),
                },
                task_kind=task_kind,
            )
        )

    static_providers = [item for item in provider_specs if not bool((item.get("capabilities") or {}).get("dynamic_models"))]
    for provider in static_providers:
        models = []
        for model_id in provider["models"]:
            if provider["available"]:
                available_model_ids.add(f"{provider['provider']}:{model_id}")
            models.append(
                _decorate_model(
                    provider["provider"],
                    model_id,
                    {
                        "id": model_id,
                        "display_name": model_id,
                        "selected": default_provider == provider["provider"] and default_model == model_id,
                    },
                    task_kind,
                    benchmark_index,
                )
            )
        catalog["providers"].append(
            _catalog_entry(
                provider["provider"],
                provider["base_url"],
                provider["available"],
                models,
                capabilities=provider["capabilities"],
                task_kind=task_kind,
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
        catalog["recommendations"] = {"task_kind": task_kind, "updated_at": bench_db.get("updated_at"), "items": recommended_items}
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
