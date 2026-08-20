from client_surfaces.operator_tui.operation_policy_inventory import (
    filter_operation_policy_inventory,
    normalize_operation_policy_inventory,
    render_operation_policy_rows,
)


def test_tui_operation_policy_inventory_uses_shared_read_only_contract() -> None:
    inventory = normalize_operation_policy_inventory(
        {
            "data": {
                "schema": "ananta.operation_policy_inventory.v1",
                "registered_count": 2,
                "future_field": "ignored",
                "items": [
                    {
                        "operation_id": "api.config.get",
                        "transport": "api",
                        "name_or_route": "/config",
                        "http_method": "GET",
                        "access_class": "read",
                        "risk_class": "low",
                        "lifecycle": "future_state",
                        "policy_status": "allowed",
                        "decision": {
                            "allowed": True,
                            "reason_code": "operation_allowed",
                            "matched_rule_id": "allow:group:api.read.v1",
                        },
                    },
                    {
                        "operation_id": "api.config.update.post",
                        "transport": "api",
                        "access_class": "admin",
                        "risk_class": "high",
                        "policy_status": "denied",
                        "decision": {
                            "allowed": False,
                            "reason_code": "operation_not_allowlisted",
                            "matched_rule_id": "default:deny",
                        },
                    },
                ],
            }
        }
    )
    filtered = filter_operation_policy_inventory(inventory, access_class="read", status="allowed")
    assert filtered["count"] == 1
    assert filtered["items"][0]["lifecycle"] == "future_state"
    assert render_operation_policy_rows(filtered) == [
        "api.config.get [allowed] read/low operation_allowed"
    ]
    assert "allow_operations" not in filtered["items"][0]
