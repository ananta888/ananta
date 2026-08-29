from __future__ import annotations

from agent.services.dendritic_memory_release_gate import DendriticMemoryReleaseGate


def test_release_gate_requires_exact_assignment_bound_evidence_without_human() -> None:
    gate = DendriticMemoryReleaseGate()
    denied = gate.evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=[],
        allowed_run_refs=[],
        requested_source_refs=[],
        requested_run_refs=[],
    )
    assert denied["eligible"] is False
    assert denied["human_intervention_required"] is False
    malformed = gate.evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=["invented"],
        allowed_run_refs=["invented"],
        requested_source_refs=["invented"],
        requested_run_refs=["invented"],
    )
    assert malformed["eligible"] is False
    allowed = gate.evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=["SRC_release"],
        allowed_run_refs=["RUN_staging"],
        requested_source_refs=["SRC_release"],
        requested_run_refs=["RUN_staging"],
    )
    assert allowed["eligible"] is True
    assert allowed["claims_verified"] is True
