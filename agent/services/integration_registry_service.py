from __future__ import annotations

import time
from typing import Any

from agent.common.sgpt import (
    SUPPORTED_CLI_BACKENDS,
    get_cli_backend_capabilities,
    get_cli_backend_preflight,
    get_cli_backend_runtime_status,
)
from agent.local_llm_backends import get_local_openai_backends, list_openai_compatible_models
from agent.services.exposure_policy_service import get_exposure_policy_service


class IntegrationRegistryService:
    """Central registry for provider, execution-backend and exposure-adapter metadata."""

    def list_execution_backends(self, *, include_preflight: bool = True) -> dict[str, Any]:
        payload = {
            "supported_backends": sorted(SUPPORTED_CLI_BACKENDS),
            "capabilities": get_cli_backend_capabilities(),
            "runtime": get_cli_backend_runtime_status(),
        }
        if include_preflight:
            payload["preflight"] = get_cli_backend_preflight()
        return payload

    def list_exposure_adapters(self, *, cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
        policies = get_exposure_policy_service().normalize_exposure_policy((cfg or {}).get("exposure_policy"))
        openai_policy = policies.get("openai_compat") or {}
        mcp_policy = policies.get("mcp") or {}
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
                "capabilities": {"dynamic_models": False},
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
        for backend in get_local_openai_backends(
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
            default_provider=default_provider,
            default_model=default_model,
        ):
            providers.append(
                {
                    "provider": backend["provider"],
                    "name": backend.get("name") or backend["provider"],
                    "display_name": backend.get("name") or backend["provider"],
                    "base_url": backend.get("base_url"),
                    "available": bool(backend.get("base_url")),
                    "models": list(backend.get("models") or []),
                    "transport_provider": backend.get("transport_provider"),
                    "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                    "provider_type": backend.get("provider_type") or "local_openai_compatible",
                    "remote_hub": bool(backend.get("remote_hub")),
                    "instance_id": backend.get("instance_id"),
                    "max_hops": backend.get("max_hops"),
                    "capabilities": {
                        "dynamic_models": True,
                        "supports_chat": True,
                        "openai_compatible": True,
                        "transport_provider": backend.get("transport_provider"),
                        "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                        "provider_type": backend.get("provider_type") or "local_openai_compatible",
                        "remote_hub": bool(backend.get("remote_hub")),
                        "instance_id": backend.get("instance_id"),
                        "max_hops": backend.get("max_hops"),
                    },
                }
            )
        return providers

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
