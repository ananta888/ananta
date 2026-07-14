"""Release-probe-only normalization into append-only per-run evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent.services.workflow_runtime.conformance import RuntimeObservation
from agent.services.workflow_runtime.reference_workflows import ReferenceWorkflow
from agent.services.workflow_runtime.release_gate import (
    PROOF_CATEGORIES,
    load_workflow_release_gate_config,
    record_runtime_release_evidence,
)


def emit_reference_run_evidence(
    *,
    runtime_id: str,
    scenario: ReferenceWorkflow,
    iteration: int,
    run_id: str,
    terminal_status: str,
    event_types: Iterable[str],
    artifact_ids: Iterable[str],
    gate_ids: Iterable[str] = (),
    side_effect_operations: Iterable[str] = (),
    policy_decisions: Iterable[str] = ("policy.allowed",),
    budget_usage: Mapping[str, int | float] | None = None,
    proofs: Mapping[str, str],
) -> None:
    """Emit only explicit proofs after the caller asserted their source.

    The helper deliberately has no scenario- or durability-based defaults.
    Callers must name every proof they actually established; omitted categories
    remain ``not_applicable`` and are evaluated by the release gate's aggregate
    capability-coverage policy.
    """

    config = load_workflow_release_gate_config()
    requirement = config.requirement_for(runtime_id)
    durable = scenario.scenario_id in requirement.durable_scenarios
    events = tuple(sorted(set(event_types)))
    artifacts = tuple(sorted(set(artifact_ids)))
    gates = tuple(sorted(set(gate_ids)))
    operations = tuple(sorted(set(side_effect_operations)))
    policies = tuple(sorted(set(policy_decisions)))
    if not set(scenario.invariants.required_event_types).issubset(events):
        raise AssertionError("release_evidence_required_event_not_observed")
    if not set(scenario.invariants.required_artifacts).issubset(artifacts):
        raise AssertionError("release_evidence_required_artifact_not_observed")
    if not set(scenario.invariants.required_gates).issubset(gates):
        raise AssertionError("release_evidence_required_gate_not_observed")
    if not set(scenario.invariants.side_effect_operations).issubset(operations):
        raise AssertionError("release_evidence_required_side_effect_not_observed")
    if not set(scenario.invariants.required_policy_decisions).issubset(policies):
        raise AssertionError("release_evidence_required_policy_not_observed")
    explicit_proofs = {str(key): str(value) for key, value in proofs.items()}
    if set(explicit_proofs) - set(PROOF_CATEGORIES):
        raise AssertionError("release_evidence_proof_category_unknown")
    if set(explicit_proofs.values()) - {"passed", "failed", "incompatible"}:
        raise AssertionError("release_evidence_proof_status_invalid")
    normalized_proofs = {
        category: explicit_proofs.get(category, "not_applicable")
        for category in PROOF_CATEGORIES
    }
    observation = RuntimeObservation(
        runtime_id=runtime_id,
        terminal_status=terminal_status,
        capabilities=requirement.capabilities,
        event_types=events,
        artifact_ids=artifacts,
        gate_ids=gates,
        side_effect_operations=operations,
        policy_decisions=policies,
        budget_usage=dict(budget_usage or {"attempts": 1, "tokens": 0, "cost_micros": 0}),
    )
    record_runtime_release_evidence(
        runtime_id=runtime_id,
        runtime_version=requirement.runtime_version,
        scenario_id=scenario.scenario_id,
        iteration=iteration,
        run_id=run_id,
        capabilities=requirement.capabilities,
        durable=durable,
        observation=observation,
        proofs=normalized_proofs,
    )
