from agent.integration_guidelines import build_integration_guidelines


def test_integration_guidelines_define_minimum_evidence_for_adapters():
    guidelines = build_integration_guidelines()

    assert guidelines["version"] == "v1"
    required = set(guidelines["minimum_required_ids"])
    assert {
        "contract_first",
        "least_privilege",
        "auditability",
        "fail_closed",
        "hub_boundary",
        "test_evidence",
    }.issubset(required)
    hub_boundary = next(item for item in guidelines["requirements"] if item["id"] == "hub_boundary")
    assert "worker-to-worker" in hub_boundary["requirement"]
