"""Side-effect-free Tiny Router observation for delegated AI-Snake requests."""
from __future__ import annotations

from typing import Any, Mapping


def observe_snake_candidate(
    prompt: str,
    *,
    agent_config: Mapping[str, Any],
) -> str:
    tool_loop = agent_config.get("ananta_worker_tool_loop")
    if not isinstance(tool_loop, Mapping):
        return "not_configured"
    tiny = tool_loop.get("tiny_router")
    if not isinstance(tiny, Mapping) or str(tiny.get("mode") or "").lower() != "shadow":
        return "not_shadow"

    from agent.services.tiny_router.service import get_tiny_tool_router_service

    decision = get_tiny_tool_router_service().route(
        prompt=prompt,
        allowed_tools=tool_loop.get("allowed_tools"),
        config={**dict(tiny), "mode": "shadow"},
        mutation_mode="read_only",
    )
    return decision.reason_code
