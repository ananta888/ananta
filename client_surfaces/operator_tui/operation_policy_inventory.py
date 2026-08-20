from __future__ import annotations

from typing import Any


class OperationPolicyInventoryError(ValueError):
    pass


def normalize_operation_policy_inventory(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OperationPolicyInventoryError("operation_policy_response_invalid")
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise OperationPolicyInventoryError("operation_policy_items_invalid")
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict) or not isinstance(raw.get("operation_id"), str):
            continue
        decision = raw.get("decision") if isinstance(raw.get("decision"), dict) else {}
        if not isinstance(decision.get("allowed"), bool):
            continue
        items.append(
            {
                "operation_id": raw["operation_id"],
                "transport": str(raw.get("transport") or "unknown"),
                "name_or_route": str(raw.get("name_or_route") or ""),
                "http_method": str(raw.get("http_method") or ""),
                "access_class": str(raw.get("access_class") or "unknown"),
                "risk_class": str(raw.get("risk_class") or "unknown"),
                "lifecycle": str(raw.get("lifecycle") or "unknown"),
                "policy_status": str(raw.get("policy_status") or ("allowed" if decision["allowed"] else "denied")),
                "reason_code": str(decision.get("reason_code") or "unknown"),
                "matched_rule_id": str(decision.get("matched_rule_id") or "unknown"),
            }
        )
    return {
        "schema": str(data.get("schema") or "ananta.operation_policy_inventory.unknown"),
        "policy": dict(data.get("policy") or {}) if isinstance(data.get("policy"), dict) else {},
        "items": items,
        "count": len(items),
        "registered_count": int(data.get("registered_count") or len(items)),
    }


def filter_operation_policy_inventory(
    inventory: dict[str, Any],
    *,
    transport: str = "",
    access_class: str = "",
    status: str = "",
) -> dict[str, Any]:
    items = [
        item
        for item in list(inventory.get("items") or [])
        if isinstance(item, dict)
        and (not transport or item.get("transport") == transport)
        and (not access_class or item.get("access_class") == access_class)
        and (not status or item.get("policy_status") == status)
    ]
    return {**inventory, "items": items, "count": len(items)}


def render_operation_policy_rows(inventory: dict[str, Any]) -> list[str]:
    rows = [
        f"{item['operation_id']} [{item['policy_status']}] {item['access_class']}/{item['risk_class']} "
        f"{item['reason_code']}"
        for item in list(inventory.get("items") or [])
        if isinstance(item, dict)
    ]
    return rows or ["no operation policy entries"]
