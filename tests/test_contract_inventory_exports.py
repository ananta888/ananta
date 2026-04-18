from devtools.export_contract_inventories import (
    build_capability_inventory,
    build_contract_inventories,
    build_policy_inventory,
)


def test_contract_inventory_exports_capabilities_and_policies():
    inventory = build_contract_inventories()

    assert inventory["version"] == "v1"
    assert any(capability["name"] == "list_agents" for capability in inventory["capabilities"])
    assert any(policy["name"] == "review_policy" for policy in inventory["policies"])


def test_capability_inventory_is_sorted_and_structured():
    capabilities = build_capability_inventory()
    names = [capability["name"] for capability in capabilities]

    assert names == sorted(names)
    assert {"name", "category", "requires_admin", "mutates_state", "description"}.issubset(capabilities[0])


def test_policy_inventory_points_to_source_locations():
    policies = build_policy_inventory()

    assert policies
    assert all(policy["path"] and policy["lineno"] > 0 for policy in policies)
