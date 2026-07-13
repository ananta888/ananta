"""Versioned contract for one Hub-delegated LangGraph node.

The contract lives in the neutral package so Hub and Worker code share schema
identifiers without either side importing the other's implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA = "ananta.langgraph_hub_node.v1"
LANGGRAPH_HUB_NODE_RESULT_SCHEMA = "ananta.langgraph_hub_node_result.v1"
LANGGRAPH_EXECUTION_CAPABILITIES = frozenset(
    {
        "approval",
        "audit",
        "authorization",
        "bounded_parallel",
        "checkpoint",
        "deterministic_merge",
        "policy",
        "resume",
        "retrieval",
        "side_effect_guard",
        "structured_output",
        "tool_calling",
    }
)


def langgraph_node_result(
    *,
    node_id: str,
    status: str,
    plan_hash: str,
    reason_code: str = "",
    value: Any = None,
    artifacts: Mapping[str, Any] | None = None,
    tokens: int = 0,
    cost_micros: int = 0,
) -> dict[str, Any]:
    """Build and validate the sole authoritative result for a delegated node."""

    result = {
        "schema": LANGGRAPH_HUB_NODE_RESULT_SCHEMA,
        "node_id": str(node_id),
        "status": str(status),
        "reason_code": str(reason_code),
        "value": value,
        "artifacts": dict(artifacts or {}),
        "tokens": int(tokens),
        "cost_micros": int(cost_micros),
        "plan_hash": str(plan_hash),
    }
    validate_langgraph_node_result(result)
    return result


def validate_langgraph_node_result(raw: Mapping[str, Any]) -> None:
    if str(raw.get("schema") or "") != LANGGRAPH_HUB_NODE_RESULT_SCHEMA:
        raise ValueError("langgraph_node_result_schema_unsupported")
    if not str(raw.get("node_id") or "").strip():
        raise ValueError("langgraph_node_result_node_required")
    if str(raw.get("status") or "") not in {"completed", "failed", "cancelled"}:
        raise ValueError("langgraph_node_result_status_invalid")
    if not str(raw.get("plan_hash") or "").strip():
        raise ValueError("langgraph_node_result_plan_hash_required")
    if int(raw.get("tokens") or 0) < 0 or int(raw.get("cost_micros") or 0) < 0:
        raise ValueError("langgraph_node_result_usage_invalid")


__all__ = [
    "LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA",
    "LANGGRAPH_HUB_NODE_RESULT_SCHEMA",
    "LANGGRAPH_EXECUTION_CAPABILITIES",
    "langgraph_node_result",
    "validate_langgraph_node_result",
]
