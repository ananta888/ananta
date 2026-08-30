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


def test_release_gate_accepts_only_exact_assignment_allowlists_without_human_review() -> None:
    local_gates = {gate: True for gate in AgentSafetyReleaseGate.REQUIRED_LOCAL_GATES}
    unprovided = AgentSafetyReleaseGate().evaluate(
        local_gates=local_gates,
        containment_available=True,
        source_refs=["SRC_fixture_source"],
        run_refs=["RUN_fixture_runtime"],
    )
    assert unprovided["release_allowed"] is False

    result = AgentSafetyReleaseGate(
        allowed_source_refs={"SRC_fixture_source"},
        allowed_run_refs={"RUN_fixture_runtime"},
    ).evaluate(
        local_gates=local_gates,
        containment_available=True,
        source_refs=["SRC_fixture_source"],
        run_refs=["RUN_fixture_runtime"],
    )
    assert result["release_allowed"] is True
    assert result["human_intervention_required"] is False
