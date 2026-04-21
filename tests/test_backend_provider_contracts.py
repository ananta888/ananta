from agent.backend_provider_contracts import build_backend_provider_contract_catalog


def test_backend_provider_contract_catalog_unifies_local_remote_and_cli_shapes():
    catalog = build_backend_provider_contract_catalog()

    assert catalog["version"] == "v1"
    required = set(catalog["schema"]["required_fields"])
    contracts = catalog["contracts"]
    assert {"local", "remote", "hosted"}.issubset({item["location"] for item in contracts})
    assert {"local_openai_compatible", "remote_ananta", "cli_backend", "hosted_api"}.issubset(
        {item["provider_type"] for item in contracts}
    )
    for contract in contracts:
        assert required.issubset(set(contract))
        assert "eligible_for_inference" in contract["routing"]
        assert "audit_required" in contract["governance"]
        assert "failure_mode" in contract["health"]
