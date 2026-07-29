from __future__ import annotations

import time
import re
from collections.abc import Mapping
from typing import Any

from agent.cli_backends.sgpt import (
    SUPPORTED_CLI_BACKENDS,
    get_cli_backend_capabilities,
    get_cli_backend_preflight,
    get_cli_backend_runtime_status,
)
from agent.local_llm_backends import get_local_openai_backends, list_openai_compatible_models
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.ollama_model_discovery_service import (
    OllamaModelDiscovery,
    OllamaModelDiscoveryService,
    get_ollama_model_discovery_service,
)
from agent.services.platform_governance_service import get_platform_governance_service
from agent.services.routing_decision_service import get_routing_decision_service


class IntegrationRegistryService:
    """Central registry for provider, execution-backend and exposure-adapter metadata."""

    def __init__(
        self,
        *,
        ollama_model_discovery: OllamaModelDiscoveryService | None = None,
    ) -> None:
        self._ollama_model_discovery = (
            ollama_model_discovery
            if ollama_model_discovery is not None
            else get_ollama_model_discovery_service()
        )

    def list_execution_backends(self, *, include_preflight: bool = True, preflight_scope: str = "full") -> dict[str, Any]:
        payload = {
            "supported_backends": sorted(SUPPORTED_CLI_BACKENDS),
            "capabilities": get_cli_backend_capabilities(),
            "runtime": get_cli_backend_runtime_status(),
        }
        if include_preflight:
            payload["preflight"] = get_cli_backend_preflight(runtime_scope=preflight_scope)
        return payload

    def normalize_runtime_endpoint_descriptor(
        self,
        *,
        provider_descriptor: Mapping[str, Any],
        endpoint_descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate a provider identity without accepting a direct target."""

        provider_allowed = {
            "provider_id",
            "provider_type",
            "model_id",
            "provider_revision",
            "capabilities",
            "limits",
        }
        endpoint_allowed = {"endpoint_id", "display_name", "routing_key"}
        if (
            not isinstance(provider_descriptor, Mapping)
            or set(provider_descriptor) - provider_allowed
            or not isinstance(endpoint_descriptor, Mapping)
            or set(endpoint_descriptor) - endpoint_allowed
        ):
            raise ValueError("runtime_endpoint_descriptor_unknown_fields")
        opaque = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
        provider_id = str(provider_descriptor.get("provider_id") or "").strip()
        provider_type = str(
            provider_descriptor.get("provider_type") or ""
        ).strip()
        model_id = str(provider_descriptor.get("model_id") or "").strip()
        provider_revision = str(
            provider_descriptor.get("provider_revision") or ""
        ).strip()
        endpoint_id = str(endpoint_descriptor.get("endpoint_id") or "").strip()
        routing_key = str(endpoint_descriptor.get("routing_key") or "").strip()
        model_identity = re.compile(
            r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
        )
        if (
            any(
                opaque.fullmatch(value) is None
                for value in (
                    provider_id,
                    provider_type,
                    provider_revision,
                    endpoint_id,
                    routing_key,
                )
            )
            or model_identity.fullmatch(model_id) is None
            or ".." in model_id
            or model_id.startswith("/")
        ):
            raise ValueError("runtime_endpoint_descriptor_identity_invalid")
        display_name = str(
            endpoint_descriptor.get("display_name") or endpoint_id
        ).strip()
        if not 1 <= len(display_name) <= 128:
            raise ValueError("runtime_endpoint_display_name_invalid")
        capability_names = {
            "openai_chat",
            "openai_responses",
            "anthropic_messages",
            "streaming",
            "tools",
            "structured_output",
        }
        raw_capabilities = provider_descriptor.get("capabilities")
        if (
            not isinstance(raw_capabilities, Mapping)
            or set(raw_capabilities) != capability_names
            or not all(
                isinstance(value, bool)
                for value in raw_capabilities.values()
            )
            or not any(raw_capabilities.values())
        ):
            raise ValueError("runtime_endpoint_capabilities_invalid")
        capabilities = {
            key: bool(raw_capabilities[key]) for key in sorted(capability_names)
        }
        primary_api = (
            capabilities["openai_chat"]
            or capabilities["openai_responses"]
            or capabilities["anthropic_messages"]
        )
        if (
            (capabilities["streaming"] and not primary_api)
            or (capabilities["tools"] and not primary_api)
            or (capabilities["structured_output"] and not primary_api)
        ):
            raise ValueError("runtime_endpoint_capability_dependency_invalid")
        raw_limits = provider_descriptor.get("limits")
        expected_limits = {
            "timeout_seconds": (1, 300),
            "context_tokens": (128, 2_097_152),
            "max_output_tokens": (1, 262_144),
            "stream_idle_timeout_seconds": (1, 300),
        }
        if not isinstance(raw_limits, Mapping) or set(raw_limits) != set(
            expected_limits
        ):
            raise ValueError("runtime_endpoint_limits_invalid")
        limits: dict[str, int] = {}
        for name, (minimum, maximum) in expected_limits.items():
            value = raw_limits.get(name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise ValueError("runtime_endpoint_limits_invalid")
            limits[name] = value
        return {
            "schema": "ananta.runtime-endpoint-descriptor.v1",
            "provider": {
                "provider_id": provider_id,
                "provider_type": provider_type,
                "model_id": model_id,
                "provider_revision": provider_revision,
            },
            "endpoint": {
                "endpoint_id": endpoint_id,
                "display_name": display_name,
                "routing_key": routing_key,
            },
            "api_capabilities": capabilities,
            "limits": limits,
            "fallback": None,
        }

    def list_exposure_adapters(self, *, cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
        exposure_service = get_exposure_policy_service()
        policies = exposure_service.normalize_exposure_policy(get_platform_governance_service().resolve_exposure_policy(cfg))
        openai_policy = policies.get("openai_compat") or {}
        mcp_policy = policies.get("mcp") or {}
        remote_hubs_policy = policies.get("remote_hubs") or {}
        return [
            {
                "adapter": "openai_compat",
                "enabled": bool(openai_policy.get("enabled")),
                "auth": {
                    "allow_agent_auth": bool(openai_policy.get("allow_agent_auth")),
                    "allow_user_auth": bool(openai_policy.get("allow_user_auth")),
                    "require_admin_for_user_auth": bool(openai_policy.get("require_admin_for_user_auth")),
                },
                "features": {
                    "models": True,
                    "chat_completions": True,
                    "responses": True,
                    "files": bool(openai_policy.get("allow_files_api")),
                    "session_metadata": True,
                },
                "routing": {
                    "instance_id": openai_policy.get("instance_id"),
                    "max_hops": openai_policy.get("max_hops"),
                },
            },
            {
                "adapter": "mcp",
                "enabled": bool(mcp_policy.get("enabled")),
                "auth": {
                    "allow_agent_auth": bool(mcp_policy.get("allow_agent_auth")),
                    "allow_user_auth": bool(mcp_policy.get("allow_user_auth")),
                    "require_admin_for_user_auth": bool(mcp_policy.get("require_admin_for_user_auth")),
                },
                "features": {
                    "tools": True,
                    "resources": True,
                    "jsonrpc": True,
                },
                "routing": {},
            },
            {
                "adapter": "remote_hubs",
                "enabled": bool(remote_hubs_policy.get("enabled")),
                "auth": {
                    "require_admin_for_user_auth": bool(remote_hubs_policy.get("require_admin_for_user_auth")),
                },
                "features": {
                    "remote_ananta": True,
                    "openai_compatible": True,
                },
                "routing": {
                    "max_hops": remote_hubs_policy.get("max_hops"),
                },
            },
        ]

    def list_inference_provider_specs(
        self,
        *,
        agent_cfg: dict[str, Any],
        provider_urls: dict[str, Any],
        default_provider: str,
        default_model: str,
        has_openai_api_key: bool = False,
        has_anthropic_api_key: bool = False,
    ) -> list[dict[str, Any]]:
        static_providers = [
            {
                "provider": "ollama",
                "display_name": "Ollama",
                "base_url": provider_urls.get("ollama"),
                "available": bool(provider_urls.get("ollama")),
                "models": ["llama3", "mistral"],
                "transport_provider": "ollama",
                "supports_tool_calls": False,
                "provider_type": "local_openai_compatible",
                "capabilities": {
                    "dynamic_models": True,
                    "supports_chat": True,
                    "openai_compatible": True,
                    "transport_provider": "ollama",
                    "provider_type": "local_openai_compatible",
                    "locality": "local",
                },
            },
            {
                "provider": "openai",
                "display_name": "OpenAI",
                "base_url": provider_urls.get("openai"),
                "available": bool(provider_urls.get("openai") or has_openai_api_key),
                "models": ["gpt-4o", "gpt-4-turbo"],
                "capabilities": {"dynamic_models": False, "requires_api_key": True},
            },
            {
                "provider": "codex",
                "display_name": "OpenAI Codex",
                "base_url": provider_urls.get("openai"),
                "available": bool(provider_urls.get("openai") or has_openai_api_key),
                "models": ["gpt-5-codex", "gpt-5-codex-mini"],
                "capabilities": {"dynamic_models": False, "requires_api_key": True, "specialization": "code"},
            },
            {
                "provider": "anthropic",
                "display_name": "Anthropic",
                "base_url": provider_urls.get("anthropic"),
                "available": bool(provider_urls.get("anthropic") or has_anthropic_api_key),
                "models": ["claude-3-5-sonnet-20240620"],
                "capabilities": {"dynamic_models": False, "requires_api_key": True},
            },
        ]
        providers: list[dict[str, Any]] = [dict(item) for item in static_providers]
        remote_hubs_policy = get_exposure_policy_service().resolve_remote_hubs_policy(agent_cfg)
        routing_fallback_policy = get_routing_decision_service().resolve_fallback_policy(agent_cfg)
        for backend in get_local_openai_backends(
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
            default_provider=default_provider,
            default_model=default_model,
        ):
            is_remote_hub = bool(backend.get("remote_hub"))
            remote_hub_allowed = (
                (not is_remote_hub)
                or (bool(remote_hubs_policy.get("enabled")) and bool(routing_fallback_policy.get("allow_remote_hubs", True)))
            )
            providers.append(
                {
                    "provider": backend["provider"],
                    "name": backend.get("name") or backend["provider"],
                    "display_name": backend.get("name") or backend["provider"],
                    "base_url": backend.get("base_url"),
                    "available": bool(backend.get("base_url")) and remote_hub_allowed,
                    "models": list(backend.get("configured_models") or []),
                    "transport_provider": backend.get("transport_provider"),
                    "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                    "provider_type": backend.get("provider_type") or "local_openai_compatible",
                    "remote_hub": is_remote_hub,
                    "instance_id": backend.get("instance_id"),
                    "max_hops": backend.get("max_hops"),
                    "trust_level": backend.get("trust_level"),
                    "allowed_operations": list(backend.get("allowed_operations") or []),
                    "allow_artifact_access": bool(backend.get("allow_artifact_access", False)),
                    "allow_file_access": bool(backend.get("allow_file_access", False)),
                    "capabilities": {
                        "dynamic_models": True,
                        "supports_chat": True,
                        "openai_compatible": True,
                        "transport_provider": backend.get("transport_provider"),
                        "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                        "provider_type": backend.get("provider_type") or "local_openai_compatible",
                        "remote_hub": is_remote_hub,
                        "remote_hub_policy": dict(remote_hubs_policy) if is_remote_hub else None,
                        "federation_policy": dict(backend.get("federation_policy") or {}) if is_remote_hub else None,
                        "trust_level": backend.get("trust_level"),
                        "allowed_operations": list(backend.get("allowed_operations") or []),
                        "allow_artifact_access": bool(backend.get("allow_artifact_access", False)),
                        "allow_file_access": bool(backend.get("allow_file_access", False)),
                        "instance_id": backend.get("instance_id"),
                        "max_hops": backend.get("max_hops"),
                    },
                }
            )
        return providers

    def discover_ollama_models(
        self,
        *,
        base_url: str | None,
        configured_models: list[object] | tuple[object, ...] = (),
        timeout_seconds: int = 5,
        cache_ttl_seconds: int = 15,
        force_refresh: bool = False,
    ) -> OllamaModelDiscovery:
        """Expose one injectable discovery seam to catalog projections."""

        return self._ollama_model_discovery.discover(
            base_url=base_url,
            configured_models=configured_models,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
        )

    def list_openai_compat_models(
        self,
        *,
        agent_cfg: dict[str, Any],
        provider_urls: dict[str, Any],
        default_provider: str,
        default_model: str,
        model_lister=None,
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        items: list[dict[str, Any]] = []
        lister = model_lister or list_openai_compatible_models
        specs = self.list_inference_provider_specs(
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
            default_provider=default_provider,
            default_model=default_model,
            has_openai_api_key=bool(agent_cfg.get("openai_api_key")),
            has_anthropic_api_key=bool(agent_cfg.get("anthropic_api_key")),
        )
        for spec in specs:
            provider = str(spec.get("provider") or "")
            static_models = list(spec.get("models") or [])
            if provider == "ollama":
                discovery = self.discover_ollama_models(
                    base_url=spec.get("base_url"),
                    configured_models=static_models,
                    timeout_seconds=5,
                )
                for item in discovery.models:
                    model_id = str(item.get("id") or "").strip()
                    if not model_id:
                        continue
                    items.append(
                        {
                            "id": f"{provider}:{model_id}",
                            "object": "model",
                            "created": now,
                            "owned_by": "ananta",
                            "provider": provider,
                            "selected": default_provider == provider and default_model == model_id,
                        }
                    )
                continue
            for model in static_models:
                model_id = str(model or "").strip()
                if not model_id:
                    continue
                items.append(
                    {
                        "id": f"{provider}:{model_id}",
                        "object": "model",
                        "created": now,
                        "owned_by": "ananta",
                        "provider": provider,
                        "selected": default_provider == provider and default_model == model_id,
                    }
                )

            if not bool((spec.get("capabilities") or {}).get("dynamic_models")):
                continue
            for item in lister(spec.get("base_url"), timeout=5):
                dynamic_model = str(item.get("id") or "").strip()
                if not dynamic_model:
                    continue
                items.append(
                    {
                        "id": f"{provider}:{dynamic_model}",
                        "object": "model",
                        "created": now,
                        "owned_by": "ananta",
                        "provider": provider,
                        "selected": default_provider == provider and default_model == dynamic_model,
                    }
                )

        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            deduped[item["id"]] = item
        return list(deduped.values())


integration_registry_service = IntegrationRegistryService()


def get_integration_registry_service() -> IntegrationRegistryService:
    return integration_registry_service
