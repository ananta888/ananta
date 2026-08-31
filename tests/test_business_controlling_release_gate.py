from __future__ import annotations

from dataclasses import replace

from agent.services.business_controlling_release_gate import (
    BusinessControllingAcceptanceReport,
    BusinessControllingReleaseGate,
)


def _report() -> BusinessControllingAcceptanceReport:
    return BusinessControllingAcceptanceReport(
        schema_version="ananta.business-controlling-acceptance.v1",
        synthetic_pilot_passed=True,
        deterministic_rules_passed=True,
        tenant_isolation_passed=True,
        malformed_input_passed=True,
        executable_content_denied=True,
        policy_bypass_denied=True,
        provenance_tampering_denied=True,
        false_positive_rate=0.01,
        maximum_false_positive_rate=0.05,
        p95_runtime_ms=80,
        maximum_p95_runtime_ms=100,
        rollback_verified=True,
        global_switch_verified=True,
        catalog_switch_verified=True,
        automatic_financial_action_count=0,
    )


def test_local_acceptance_passes_but_production_stays_unverified_without_ids() -> None:
    decision = BusinessControllingReleaseGate().assess(_report())

    assert decision.local_acceptance_passed is True
    assert decision.production_release_allowed is False
    assert decision.reason_codes == (
        "controlling_gate_production_evidence_unverified",
    )


def test_gate_rejects_financial_action_budget_and_invented_ids() -> None:
    report = replace(
        _report(),
        automatic_financial_action_count=1,
        source_refs=("source-local",),
        run_refs=("run-local",),
    )

    decision = BusinessControllingReleaseGate().assess(report)

    assert decision.local_acceptance_passed is False
    assert decision.production_release_allowed is False
    assert "controlling_gate_automatic_financial_action_detected" in decision.reason_codes
    assert "controlling_gate_source_ref_invalid" in decision.reason_codes
    assert "controlling_gate_run_ref_invalid" in decision.reason_codes
