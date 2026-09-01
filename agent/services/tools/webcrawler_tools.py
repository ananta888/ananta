"""Hub-controlled dispatch adapter for the external Webcrawler tools."""

from __future__ import annotations

from typing import Any

from agent.services.tools._evidence import build_tool_result


def run_webcrawler_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tool_call_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the external provider from trusted Hub configuration and run it.

    The model-controlled ``arguments`` mapping cannot grant authorization.
    Authorization is read only from the separate Hub policy context.
    """

    from agent.providers.webcrawler import (
        AnantaWebcrawlerBackendProvider,
        AnantaWebcrawlerProviderConfig,
        AnantaWebcrawlerToolProvider,
        WebcrawlerConfigError,
    )

    cfg = dict(config or {})
    providers = cfg.get("providers")
    provider_raw = providers.get("ananta_webcrawler") if isinstance(providers, dict) else None
    if not isinstance(provider_raw, dict):
        agent_cfg = cfg.get("agent_cfg")
        nested_providers = agent_cfg.get("providers") if isinstance(agent_cfg, dict) else None
        provider_raw = (
            nested_providers.get("ananta_webcrawler")
            if isinstance(nested_providers, dict)
            else None
        )
    try:
        provider_config = AnantaWebcrawlerProviderConfig.from_mapping(provider_raw)
    except WebcrawlerConfigError as exc:
        return build_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="error",
            risk_class="external_agent",
            error=str(exc),
        )
    if not provider_config.enabled:
        return build_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="policy_blocked",
            risk_class="external_agent",
            error="webcrawler_disabled",
        )

    policy_context = cfg.get("webcrawler_policy_context")
    authorization_granted = bool(
        isinstance(policy_context, dict)
        and policy_context.get("authorization_granted") is True
    )
    provider = AnantaWebcrawlerToolProvider(
        provider_config,
        AnantaWebcrawlerBackendProvider(provider_config),
    )
    return provider.run(
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id=tool_call_id,
        authorization_granted=authorization_granted,
    )
