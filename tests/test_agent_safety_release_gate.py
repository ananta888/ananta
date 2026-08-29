from agent.services.agent_safety_release_gate import AgentSafetyReleaseGate


def test_release_gate_never_promotes_local_or_unverified_evidence() -> None:
    result = AgentSafetyReleaseGate().evaluate(
        local_gates={
            "contracts": True,
            "security": True,
            "chaos": True,
            "api": True,
            "frontend": True,
        },
        containment_available=True,
        source_refs=[],
        run_refs=[],
    )
    assert result["release_allowed"] is False
    assert result["state"] == "blocked"
    assert "agent_safety_authoritative_source_evidence_unavailable" in result["reason_codes"]
    assert "agent_safety_runtime_evidence_unavailable" in result["reason_codes"]
    assert result["human_intervention_required"] is False


def test_release_gate_reports_missing_automatic_containment_and_local_gates() -> None:
    result = AgentSafetyReleaseGate().evaluate(
        local_gates={},
        containment_available=False,
        source_refs=["not-authoritative"],
        run_refs=["not-authoritative"],
    )
    assert result["release_allowed"] is False
    assert "agent_safety_local_gates_incomplete" in result["reason_codes"]
    assert "agent_safety_containment_adapter_unavailable" in result["reason_codes"]
    assert result["source_refs"] == []
    assert result["run_refs"] == []
