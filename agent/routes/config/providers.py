from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.local_llm_backends import get_local_openai_backends

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


@providers_bp.route("/providers", methods=["GET"])
@check_auth
def list_providers():
    urls = current_app.config.get("PROVIDER_URLS", {})
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    provider_default = str(app_cfg.get("default_provider") or "")
    model_default = str(app_cfg.get("default_model") or "")
    providers = []

    if urls.get("ollama"):
        providers.extend(
            [
                {"id": "ollama:llama3", "name": "Ollama (Llama3)", "selected": provider_default == "ollama" and model_default == "llama3"},
                {"id": "ollama:mistral", "name": "Ollama (Mistral)", "selected": provider_default == "ollama" and model_default == "mistral"},
            ]
        )
    if urls.get("openai") or current_app.config.get("OPENAI_API_KEY"):
        providers.extend(
            [
                {"id": "openai:gpt-4o", "name": "OpenAI (GPT-4o)", "selected": provider_default == "openai" and model_default == "gpt-4o"},
                {"id": "openai:gpt-4-turbo", "name": "OpenAI (GPT-4 Turbo)", "selected": provider_default == "openai" and model_default == "gpt-4-turbo"},
                {"id": "codex:gpt-5-codex", "name": "OpenAI Codex (GPT-5 Codex)", "selected": provider_default == "codex" and model_default == "gpt-5-codex"},
            ]
        )
    if urls.get("anthropic") or current_app.config.get("ANTHROPIC_API_KEY"):
        providers.append(
            {
                "id": "anthropic:claude-3-5-sonnet-20240620",
                "name": "Claude 3.5 Sonnet",
                "selected": provider_default == "anthropic" and model_default == "claude-3-5-sonnet-20240620",
            }
        )

    timeout_seconds, cache_ttl_seconds, force_refresh = shared.lmstudio_catalog_runtime_options()
    local_backends = get_local_openai_backends(
        agent_cfg=app_cfg,
        provider_urls=urls,
        default_provider=provider_default,
        default_model=model_default,
    )
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
                    providers.append(
                        {
                            "id": f"{backend['provider']}:{model_id}",
                            "name": f"{backend['name']} ({model_id})",
                            "selected": provider_default == backend["provider"] and model_default == model_id,
                        }
                    )
        else:
            providers.append({"id": f"{backend['provider']}:model", "name": backend["name"], "selected": provider_default == backend["provider"]})

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
    local_backends = get_local_openai_backends(
        agent_cfg=app_cfg,
        provider_urls=urls,
        default_provider=default_provider,
        default_model=default_model,
    )
    for backend in local_backends:
        local_models = []
        for item in shared.catalog_models_for_local_backend(
            backend,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
        ):
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
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
                bool(local_models),
                local_models,
                capabilities={
                    "dynamic_models": True,
                    "supports_chat": True,
                    "openai_compatible": True,
                    "transport_provider": backend.get("transport_provider"),
                    "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                },
                task_kind=task_kind,
            )
        )

    static_providers = [
        {
            "provider": "ollama",
            "base_url": urls.get("ollama"),
            "available": bool(urls.get("ollama")),
            "models": ["llama3", "mistral"],
            "capabilities": {"dynamic_models": False},
        },
        {
            "provider": "openai",
            "base_url": urls.get("openai"),
            "available": bool(urls.get("openai") or current_app.config.get("OPENAI_API_KEY")),
            "models": ["gpt-4o", "gpt-4-turbo"],
            "capabilities": {"dynamic_models": False, "requires_api_key": True},
        },
        {
            "provider": "codex",
            "base_url": urls.get("openai"),
            "available": bool(urls.get("openai") or current_app.config.get("OPENAI_API_KEY")),
            "models": ["gpt-5-codex", "gpt-5-codex-mini"],
            "capabilities": {"dynamic_models": False, "requires_api_key": True, "specialization": "code"},
        },
        {
            "provider": "anthropic",
            "base_url": urls.get("anthropic"),
            "available": bool(urls.get("anthropic") or current_app.config.get("ANTHROPIC_API_KEY")),
            "models": ["claude-3-5-sonnet-20240620"],
            "capabilities": {"dynamic_models": False, "requires_api_key": True},
        },
    ]
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
