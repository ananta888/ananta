from __future__ import annotations

from flask import Blueprint, current_app, g, request
from pydantic import ValidationError

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings as runtime_settings
from agent.config_defaults import sync_runtime_state
from agent.repositories.model_default_selection import (
    SqlModelDefaultSelectionRepository,
)
from agent.repositories.model_routing_configuration import (
    SqlModelRoutingConfigurationRepository,
)
from agent.services.dashboard_feature_flag_service import (
    resolve_dashboard_feature_flags,
)
from agent.services.model_catalog_service import (
    MODEL_CATALOG_REFRESH_CAPABILITY,
    MODEL_DEFAULT_SELECT_CAPABILITY,
    CatalogQuery,
    ModelCatalogCapabilityPolicy,
    ModelCatalogService,
    ModelDefaultSelectionError,
    ModelDefaultSelectionService,
    ProviderDiscovery,
)
from agent.services.model_invocation_service import (
    ModelInvocationService,
    ModelRoutingConfigurationError,
)
from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader
from agent.services.model_selection_service import (
    EffectiveModelRoutingService,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
    ModelRoutingConflict,
)
from agent.services.ollama_model_discovery_service import OllamaModelDiscovery
from agent.services.routing_decision_service import get_routing_decision_service
from agent.services.service_registry import get_core_services
from agent.services.surface_rate_limit_policy import (
    MODEL_CATALOG_REFRESH,
    MODEL_DEFAULT_SELECTION,
    surface_rate_limit_policy,
)
from agent.services.voice_provider import VoiceProviderError, get_voice_provider_service
from ananta_contracts.model_catalog import ModelDefaultSelectionCommand
from ananta_contracts.model_selection import (
    ModelRoutingDryRunCommand,
    ModelRoutingMutationCommand,
)

from . import shared

providers_bp = Blueprint("config_providers", __name__)

MODEL_ROUTING_READ_CAPABILITY = "model_routing.read"
MODEL_ROUTING_MUTATE_CAPABILITY = "model_routing.mutate"


def _known_model_profiles() -> tuple[ModelProfile, ...]:
    path = str(current_app.config.get("MODEL_PROFILES_PATH") or "").strip()
    if not path:
        import os
        path = str(os.environ.get("MODEL_PROFILES_PATH") or "").strip()
    if not path:
        return ()
    result = ModelProfileLoader().load_file(path)
    return tuple(profile for profile in result.profiles if profile.enabled)


def _model_routing_service() -> ModelRoutingAssignmentService:
    profiles = _known_model_profiles()
    return ModelRoutingAssignmentService(
        repository=SqlModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=(profile.profile_id for profile in profiles),
        known_models=((profile.provider_id, profile.model) for profile in profiles),
    )


def _effective_model_routing_service() -> EffectiveModelRoutingService:
    resolver = ModelInvocationService.get_profile_resolver()
    if resolver is None:
        raise ModelRoutingConfigurationError("model_profiles_not_configured")
    return EffectiveModelRoutingService(
        repository=SqlModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        resolver=resolver,
    )


def _force_refresh_forbidden() -> bool:
    return shared.parse_bool_query_flag(request.args.get("force_refresh")) and not bool(getattr(g, "is_admin", False))


def _provider_specs(*, app_cfg: dict, urls: dict, default_provider: str, default_model: str) -> list[dict]:
    return get_core_services().integration_registry_service.list_inference_provider_specs(
        agent_cfg=app_cfg,
        provider_urls=urls,
        default_provider=default_provider,
        default_model=default_model,
        has_openai_api_key=bool(current_app.config.get("OPENAI_API_KEY")),
        has_anthropic_api_key=bool(current_app.config.get("ANTHROPIC_API_KEY")),
    )


def _catalog_models_for_dynamic_backend(
    backend: dict,
    *,
    timeout_seconds: int,
    cache_ttl_seconds: int,
    force_refresh: bool,
) -> tuple[list[dict], OllamaModelDiscovery | None]:
    if str(backend.get("provider") or "").strip().lower() == "ollama":
        discovery = get_core_services().integration_registry_service.discover_ollama_models(
            base_url=backend.get("base_url"),
            configured_models=list(backend.get("models") or []),
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
        )
        return [dict(item) for item in discovery.models], discovery
    return (
        shared.catalog_models_for_local_backend(
            backend,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
        ),
        None,
    )


def _voice_runtime_catalog_entry(app_cfg: dict) -> dict:
    voice_cfg = app_cfg.get("voice_runtime") if isinstance(app_cfg.get("voice_runtime"), dict) else {}
    base_url = str(voice_cfg.get("base_url") or current_app.config.get("VOICE_RUNTIME_URL") or "").strip()
    provider_name = str(voice_cfg.get("provider") or current_app.config.get("VOICE_PROVIDER") or "voice-runtime")
    max_audio_mb = int(voice_cfg.get("max_audio_mb") or current_app.config.get("VOICE_MAX_AUDIO_MB") or 25)
    models: list[dict] = []
    available = False
    status = "unavailable"
    reason = None
    try:
        provider = get_voice_provider_service()
        models = provider.models()
        health = provider.health()
        available = bool(health.get("ok"))
        status = str(health.get("status") or ("ok" if available else "degraded"))
    except VoiceProviderError as exc:
        reason = exc.code
    except Exception:
        reason = "voice.runtime_unavailable"
    return {
        "provider": provider_name,
        "base_url": base_url,
        "available": available,
        "model_count": len(models),
        "models": models,
        "capabilities": {
            "dynamic_models": True,
            "supports_chat": False,
            "openai_compatible": False,
            "provider_type": "local_voice_runtime",
            "voice_capabilities": ["audio_input", "transcription", "voice_command", "multimodal_audio_prompt"],
            "limits": {"max_audio_mb": max_audio_mb},
            "status": status,
            "status_reason": reason,
        },
        "recommended_model": (models[0].get("id") if models else None),
    }


class _FlaskProviderInventory:
    def __init__(self, *, app_cfg: dict, urls: dict) -> None:
        self._app_cfg = app_cfg
        self._urls = urls

    def list_specs(self, query: CatalogQuery):
        return _provider_specs(
            app_cfg=self._app_cfg,
            urls=self._urls,
            default_provider=query.default_provider,
            default_model=query.default_model,
        )

    def discover(self, provider, query: CatalogQuery) -> ProviderDiscovery:
        models, ollama_discovery = _catalog_models_for_dynamic_backend(
            dict(provider),
            timeout_seconds=query.timeout_seconds,
            cache_ttl_seconds=query.cache_ttl_seconds,
            force_refresh=query.force_refresh,
        )
        available = (
            bool(ollama_discovery.available)
            if ollama_discovery is not None
            else bool(provider.get("available"))
        )
        metadata = None
        if ollama_discovery is not None:
            metadata = {
                "status": ollama_discovery.status,
                "source": (
                    "configured_fallback"
                    if ollama_discovery.used_configured_fallback
                    else "ollama_api_tags"
                ),
                "used_configured_fallback": (
                    ollama_discovery.used_configured_fallback
                ),
            }
        return ProviderDiscovery(
            models=tuple(dict(item) for item in models),
            available=available,
            metadata=metadata,
        )

    def voice_entry(self):
        return _voice_runtime_catalog_entry(self._app_cfg)


class _FlaskCatalogPolicy:
    def __init__(self, *, app_cfg: dict) -> None:
        self._app_cfg = app_cfg

    def benchmark_rows(self, task_kind: str):
        return shared.benchmark_rows_for_task(
            task_kind=task_kind,
            top_n=8 if task_kind else None,
        )

    def routing_decision(self, provider_entry, task_kind: str):
        return get_routing_decision_service().provider_catalog_decision(
            cfg=self._app_cfg,
            provider=dict(provider_entry),
            task_kind=task_kind,
        )

    def fallback_policy(self):
        return get_routing_decision_service().resolve_fallback_policy(
            self._app_cfg
        )


class _FlaskDefaultSelectionRuntime:
    def apply(self, *, provider_id: str, model_id: str) -> None:
        current = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
        current.update(
            {
                "default_provider": provider_id,
                "default_model": model_id,
            }
        )
        current_app.config["AGENT_CONFIG"] = current
        sync_runtime_state(
            current_app,
            current,
            changed_keys={"default_provider", "default_model"},
        )


def _model_catalog_service() -> ModelCatalogService:
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    urls = current_app.config.get("PROVIDER_URLS", {}) or {}
    return ModelCatalogService(
        inventory=_FlaskProviderInventory(app_cfg=app_cfg, urls=urls),
        policy=_FlaskCatalogPolicy(app_cfg=app_cfg),
    )


def _catalog_query(*, force_refresh: bool | None = None) -> CatalogQuery:
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    task_kind = str(request.args.get("task_kind") or "").strip().lower()
    if task_kind not in shared._BENCH_TASK_KINDS:
        task_kind = ""
    timeout_seconds, cache_ttl_seconds, requested_refresh = (
        shared.lmstudio_catalog_runtime_options()
    )
    return CatalogQuery(
        default_provider=str(app_cfg.get("default_provider") or ""),
        default_model=str(app_cfg.get("default_model") or ""),
        task_kind=task_kind,
        timeout_seconds=timeout_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=(
            requested_refresh if force_refresh is None else force_refresh
        ),
    )


def _model_catalog_feature_enabled() -> bool:
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    defaults = {
        "feature_angular_model_dashboard_enabled": getattr(
            runtime_settings,
            "feature_angular_model_dashboard_enabled",
            False,
        ),
        "feature_tui_model_menu_enabled": getattr(
            runtime_settings,
            "feature_tui_model_menu_enabled",
            False,
        ),
    }
    return resolve_dashboard_feature_flags(
        app_cfg,
        defaults=defaults,
    ).model_catalog_enabled


def _feature_disabled_response():
    return api_response(
        status="error",
        message="model_catalog_feature_disabled",
        code=404,
    )


def _capability_allowed(capability: str) -> bool:
    claims = {
        **dict(getattr(g, "auth_payload", {}) or {}),
        **dict(getattr(g, "user", {}) or {}),
    }
    return ModelCatalogCapabilityPolicy().allows(
        capability,
        is_admin=bool(getattr(g, "is_admin", False)),
        claims=claims,
    )


def _capability_denied_response(capability: str):
    log_audit(
        "model_catalog_capability_denied",
        {"capability": capability, "path": request.path},
    )
    return api_response(
        status="error",
        message="forbidden",
        data={"reason_code": "model_catalog_capability_required"},
        code=403,
    )


def _model_catalog_input_error(message: str):
    return api_response(
        status="error",
        message=message,
        code=400,
    )


def _query_args_are_valid(*allowed: str) -> bool:
    return not (set(request.args.keys()) - set(allowed))


def _refresh_body_is_valid() -> bool:
    body = request.get_json(silent=True)
    if body == {}:
        return True
    return body is None and not request.get_data(cache=True).strip()


def _surface_rate_limit_response(namespace: str):
    decision = surface_rate_limit_policy.consume(
        config=current_app.config,
        namespace=namespace,
        auth_payload=getattr(g, "auth_payload", None),
        user=getattr(g, "user", None),
        remote_addr=request.remote_addr,
    )
    if decision.allowed:
        return None
    result = api_response(
        status="error",
        message="rate_limit_exceeded",
        data={
            "reason_code": "rate_limit_exceeded",
            "retry_after_seconds": decision.retry_after_seconds,
        },
        code=429,
    )
    response = result[0] if isinstance(result, tuple) else result
    response.headers["Retry-After"] = str(decision.retry_after_seconds)
    return result


@providers_bp.route("/providers", methods=["GET"])
@check_auth
def list_providers():
    if _force_refresh_forbidden():
        return api_response(
            status="error",
            message="admin_required_for_force_refresh",
            code=403,
        )
    urls = current_app.config.get("PROVIDER_URLS", {})
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    provider_default = str(app_cfg.get("default_provider") or "")
    model_default = str(app_cfg.get("default_model") or "")
    providers = []

    for spec in _provider_specs(
        app_cfg=app_cfg, urls=urls, default_provider=provider_default, default_model=model_default
    ):
        if not spec.get("available") and not bool((spec.get("capabilities") or {}).get("dynamic_models")):
            continue
        if bool((spec.get("capabilities") or {}).get("dynamic_models")):
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
    local_backends = [
        item
        for item in _provider_specs(
            app_cfg=app_cfg, urls=urls, default_provider=provider_default, default_model=model_default
        )
        if bool((item.get("capabilities") or {}).get("dynamic_models"))
    ]
    for backend in local_backends:
        backend_models, _discovery = _catalog_models_for_dynamic_backend(
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
            providers.append(
                {
                    "id": f"{backend['provider']}:model",
                    "name": backend_display,
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


@providers_bp.route("/providers/catalog", methods=["GET"])
@check_auth
def list_provider_catalog():
    if _force_refresh_forbidden():
        return api_response(
            status="error",
            message="admin_required_for_force_refresh",
            code=403,
        )
    snapshot = _model_catalog_service().snapshot(_catalog_query())
    return api_response(data=dict(snapshot.legacy_catalog))


@providers_bp.route("/models/catalog/v1", methods=["GET"])
@check_auth
def get_versioned_model_catalog():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _query_args_are_valid("task_kind"):
        return _model_catalog_input_error("model_catalog_query_invalid")
    catalog = _model_catalog_service().versioned_catalog(
        _catalog_query(force_refresh=False)
    )
    return api_response(data=catalog.to_wire())


@providers_bp.route("/models/catalog/v1/refresh", methods=["POST"])
@check_auth
def refresh_versioned_model_catalog():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_CATALOG_REFRESH_CAPABILITY):
        return _capability_denied_response(
            MODEL_CATALOG_REFRESH_CAPABILITY
        )
    if not _query_args_are_valid("task_kind") or not _refresh_body_is_valid():
        return _model_catalog_input_error(
            "model_catalog_refresh_command_invalid"
        )
    rate_limited = _surface_rate_limit_response(MODEL_CATALOG_REFRESH)
    if rate_limited is not None:
        return rate_limited
    catalog = _model_catalog_service().versioned_catalog(
        _catalog_query(force_refresh=True)
    )
    log_audit(
        "model_catalog_refreshed",
        {"model_count": len(catalog.models)},
    )
    return api_response(data=catalog.to_wire())


@providers_bp.route("/models/default/v1", methods=["POST"])
@check_auth
def select_versioned_model_default():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_DEFAULT_SELECT_CAPABILITY):
        return _capability_denied_response(
            MODEL_DEFAULT_SELECT_CAPABILITY
        )
    try:
        command = ModelDefaultSelectionCommand.model_validate(
            request.get_json(silent=True)
        )
    except ValidationError:
        return api_response(
            status="error",
            message="model_default_selection_command_invalid",
            code=400,
        )
    rate_limited = _surface_rate_limit_response(MODEL_DEFAULT_SELECTION)
    if rate_limited is not None:
        return rate_limited
    catalog = _model_catalog_service()
    selector = ModelDefaultSelectionService(
        catalog=catalog,
        store=SqlModelDefaultSelectionRepository(),
        runtime=_FlaskDefaultSelectionRuntime(),
    )
    try:
        selected = selector.select(
            command,
            query=_catalog_query(force_refresh=False),
        )
    except ModelDefaultSelectionError as exc:
        return api_response(
            status="error",
            message=exc.reason_code,
            code=exc.status_code,
        )
    except Exception:
        current_app.logger.exception("Model default selection failed")
        return api_response(
            status="error",
            message="model_default_selection_persistence_failed",
            code=503,
        )
    log_audit(
        "model_default_selected",
        {
            "provider_id": selected.provider_id,
            "model_id": selected.model_id,
        },
    )
    return api_response(
        data=selected.model_dump(mode="json", by_alias=True)
    )


@providers_bp.route("/models/consumers/v1", methods=["GET"])
@check_auth
def get_model_consumers():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    consumers = ModelConsumerRegistry.defaults().all()
    return api_response(data={
        "schema": "ananta.model-consumer-registry.v1",
        "consumers": [item.model_dump(mode="json", by_alias=True) for item in consumers],
    })


@providers_bp.route("/models/routing/v1", methods=["GET"])
@check_auth
def get_model_routing_configuration():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    value = _model_routing_service().read()
    return api_response(data=value.model_dump(mode="json", by_alias=True))


@providers_bp.route("/models/routing/v1/dry-run", methods=["POST"])
@check_auth
def dry_run_model_routing_configuration():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    try:
        command = ModelRoutingDryRunCommand.model_validate(
            request.get_json(silent=True)
        )
        route = _effective_model_routing_service().dry_run(command)
    except ValidationError:
        return _model_catalog_input_error("model_routing_dry_run_command_invalid")
    except ValueError as exc:
        return api_response(
            status="error",
            message=str(exc)[:160],
            code=400,
        )
    except ModelRoutingConfigurationError as exc:
        return api_response(
            status="error",
            message=str(exc)[:160],
            code=503,
        )
    return api_response(data=route.model_dump(mode="json", by_alias=True))


@providers_bp.route("/models/routing/v1/validate", methods=["POST"])
@check_auth
def validate_model_routing_configuration():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_MUTATE_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_MUTATE_CAPABILITY)
    try:
        command = ModelRoutingMutationCommand.model_validate(request.get_json(silent=True))
        service = _model_routing_service()
        service.validate(command)
    except (ValidationError, ValueError) as exc:
        return api_response(
            status="error", message="model_routing_configuration_invalid",
            data={"reason_code": str(exc).splitlines()[0][:160]}, code=400,
        )
    return api_response(data={
        "schema": "ananta.model-routing-validation-report.v1",
        "valid": True,
        "expected_revision": command.expected_revision,
        "errors": [],
        "warnings": [],
    })


@providers_bp.route("/models/routing/v1", methods=["PUT"])
@check_auth
def put_model_routing_configuration():
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_MUTATE_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_MUTATE_CAPABILITY)
    try:
        command = ModelRoutingMutationCommand.model_validate(request.get_json(silent=True))
        updated = _model_routing_service().apply(command)
    except ValidationError:
        return _model_catalog_input_error("model_routing_mutation_command_invalid")
    except ModelRoutingConflict as exc:
        return api_response(
            status="error", message=exc.reason_code,
            data={"current_revision": exc.current_revision}, code=409,
        )
    except ValueError as exc:
        return api_response(
            status="error", message="model_routing_configuration_invalid",
            data={"reason_code": str(exc)[:160]}, code=400,
        )
    log_audit("model_routing_configuration_updated", {
        "previous_revision": command.expected_revision,
        "revision": updated.revision,
        "assignment_count": len(updated.assignments),
        "fallback_group_count": len(updated.fallback_groups),
    })
    return api_response(data=updated.model_dump(mode="json", by_alias=True))
