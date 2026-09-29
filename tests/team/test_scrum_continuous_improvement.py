from __future__ import annotations

import pytest

from agent.services.scrum_architecture_loop_service import ScrumArchitectureLoopService
from agent.services.scrum_retrospective_service import ScrumRetrospectiveService
from agent.services.scrum_sprint_control_service import ScrumSprintControlService
from agent.services.scrum_state_store import ScrumStateConflictError, ScrumStateStore


class _Analysis:
    def analyze(self, **_kwargs):
        return [{"proposal_id": "evolution-1", "title": "Bounded improvement"}]


def _services(tmp_path):
    store = ScrumStateStore(tmp_path / "scrum.sqlite3")
    architecture = ScrumArchitectureLoopService(store)
    sprints = ScrumSprintControlService(store, architecture)
    retrospectives = ScrumRetrospectiveService(store, sprints, analysis=_Analysis())
    return store, architecture, sprints, retrospectives


def _active_baseline(architecture):
    architecture.create_baseline(
        scope_id="project-1",
        revision_id="arch-1",
        author_id="architect-agent",
        parent_revision_id=None,
        target_architecture={"style": "hub-worker"},
        guardrails=[
            {"guardrail_id": "hub-owner", "rule": "Hub owns queue", "scopes": ["api"]},
            {"guardrail_id": "global", "rule": "Workers do not orchestrate", "scopes": []},
        ],
        adr_refs=["ADR-1"],
    )
    return architecture.activate_baseline(
        revision_id="arch-1",
        reviewer_id="decision-reviewer-agent",
        checks={
            "scope": True,
            "security": True,
            "compatibility": True,
            "migration": True,
            "evidence": True,
        },
        evidence_refs=["architecture-review-1"],
    )


def _plan_first(sprints):
    return sprints.plan(
        sprint_id="sprint-1",
        scope_id="project-1",
        sequence=1,
        predecessor_sprint_id=None,
        product_goal="Deliver the product",
        sprint_goal="Ship a governed vertical slice",
        task_ids=["task-1", "task-2"],
        sprint_scope=["api"],
        boundary={"task_count": 3, "token_count": 1000},
        planned_at="2026-08-28T10:00:00Z",
    )


def test_three_control_loops_are_versioned_headless_and_cross_sprint(tmp_path):
    store, architecture, sprints, retrospectives = _services(tmp_path)
    _active_baseline(architecture)
    sprint = _plan_first(sprints)
    assert sprint["architecture_handoff"]["architecture_revision_id"] == "arch-1"
    assert {item["guardrail_id"] for item in sprint["architecture_handoff"]["guardrails"]} == {
        "hub-owner",
        "global",
    }
    sprints.transition(sprint_id="sprint-1", target_state="active", occurred_at="2026-08-28T10:01:00Z")
    snapshot = sprints.snapshot(
        sprint_id="sprint-1",
        snapshot_id="snapshot-1",
        task_states={"task-1": "done", "task-2": "blocked"},
        handoff_failures=0,
        gate_failures=0,
        rework_count=1,
        consumed_boundary={"task_count": 2, "token_count": 500},
        architecture_finding_ids=[],
        observed_at="2026-08-28T10:02:00Z",
    )
    assert snapshot["completion_ratio"] == 0.5
    decision = sprints.inspect_and_adapt(
        sprint_id="sprint-1",
        control_id="control-1",
        snapshot_id="snapshot-1",
        trigger="blocked",
        trigger_sequence=1,
    )
    assert decision["recommendation"] == "adjust_sprint_backlog"
    adjusted = sprints.adjust_backlog(
        sprint_id="sprint-1",
        control_id="control-1",
        add_task_ids=["task-3"],
        remove_task_ids=["task-2"],
        reason="Replace blocked scope without changing the Sprint Goal",
    )
    assert adjusted["original_task_ids"] == ["task-1", "task-2"]
    assert adjusted["task_ids"] == ["task-1", "task-3"]

    snapshot_2 = sprints.snapshot(
        sprint_id="sprint-1",
        snapshot_id="snapshot-2",
        task_states={"task-1": "done", "task-3": "done"},
        handoff_failures=0,
        gate_failures=0,
        rework_count=1,
        consumed_boundary={"task_count": 3, "token_count": 700},
        architecture_finding_ids=[],
        observed_at="2026-08-28T10:03:00Z",
    )
    assert snapshot_2["completion_ratio"] == 1.0
    sprints.transition(sprint_id="sprint-1", target_state="review", occurred_at="2026-08-28T10:04:00Z")
    sprints.transition(
        sprint_id="sprint-1",
        target_state="retrospective",
        occurred_at="2026-08-28T10:05:00Z",
    )
    bundle = retrospectives.build_evidence_bundle(
        bundle_id="bundle-1",
        sprint_id="sprint-1",
        snapshot_ids=["snapshot-1", "snapshot-2"],
        artifact_refs=["increment-1"],
        audit_refs=["audit-1"],
        delivery_metrics={"rework": 2, "cycle_time": 10},
        process_signals=[{"signal_id": "sig-1", "summary": "Blocked handoff increased rework"}],
    )
    assert bundle["raw_prompts_included"] is False
    retrospective = retrospectives.analyze(
        retrospective_id="retro-1",
        bundle_id="bundle-1",
        perspectives=[
            {
                "role": "product_owner",
                "stance": "support",
                "summary": "The scope was recoverable",
                "supported_signal_ids": ["sig-1"],
                "alternative_causes": ["dependency"],
            },
            {
                "role": "scrum_master",
                "stance": "support",
                "summary": "Earlier checks can help",
                "supported_signal_ids": ["sig-1"],
                "alternative_causes": ["handoff timing"],
            },
            {
                "role": "developer",
                "stance": "challenge",
                "summary": "The dependency contract was the root concern",
                "supported_signal_ids": ["sig-1"],
                "alternative_causes": ["contract drift"],
            },
        ],
    )
    assert retrospective["analysis_status"] == "evolution_engine_completed"
    assert retrospective["dissent_preserved"] is True
    proposal = retrospectives.propose_improvement(
        proposal_id="improvement-1",
        retrospective_id="retro-1",
        hypothesis_ids=[retrospective["hypotheses"][0]["hypothesis_id"]],
        proposal_type="process",
        target_ref="workflow:handoff-check",
        description="Run the existing contract check before task activation",
        expected_effect="Reduce rework",
        risk_level="low",
        experiment={"mode": "stable_cohort"},
    )
    reviewed = retrospectives.review_improvement(
        proposal_id=proposal["proposal_id"],
        reviewer_id="policy-reviewer-agent",
        checks={"evidence": True, "scope": True, "security": True, "rollback": True, "measurable": True},
    )
    assert reviewed["status"] == "accepted"
    commitment = retrospectives.create_commitment(
        commitment_id="commitment-1",
        proposal_id="improvement-1",
        owner_role="scrum_master",
        metric_names=["rework"],
        rollback_rule="Rollback when rework increases",
    )
    assert commitment["status"] == "accepted"
    sprints.transition(
        sprint_id="sprint-1",
        target_state="improvement_pending",
        occurred_at="2026-08-28T10:06:00Z",
    )
    sprints.transition(sprint_id="sprint-1", target_state="closed", occurred_at="2026-08-28T10:07:00Z")
    next_sprint = sprints.plan(
        sprint_id="sprint-2",
        scope_id="project-1",
        sequence=2,
        predecessor_sprint_id="sprint-1",
        product_goal="Deliver the product",
        sprint_goal="Validate the improved handoff",
        task_ids=["task-4"],
        sprint_scope=["api"],
        boundary={"task_count": 1},
        planned_at="2026-08-28T11:00:00Z",
        improvement_commitment_ids=["commitment-1"],
    )
    assert next_sprint["improvement_commitment_ids"] == ["commitment-1"]
    first_assignment = retrospectives.experiment_assignment(
        commitment_id="commitment-1",
        sprint_id="sprint-2",
        subject_id="task-4",
        treatment_basis_points=5000,
    )
    assert first_assignment == retrospectives.experiment_assignment(
        commitment_id="commitment-1",
        sprint_id="sprint-2",
        subject_id="task-4",
        treatment_basis_points=5000,
    )
    sprints.transition(sprint_id="sprint-2", target_state="active", occurred_at="2026-08-28T11:01:00Z")
    sprints.transition(sprint_id="sprint-2", target_state="review", occurred_at="2026-08-28T11:02:00Z")
    sprints.transition(
        sprint_id="sprint-2",
        target_state="retrospective",
        occurred_at="2026-08-28T11:03:00Z",
    )
    sprints.transition(sprint_id="sprint-2", target_state="closed", occurred_at="2026-08-28T11:04:00Z")
    effect = retrospectives.evaluate_commitment(
        evaluation_id="effect-1",
        commitment_id="commitment-1",
        sprint_id="sprint-2",
        baseline_metrics={"rework": 2},
        observed_metrics={"rework": 3},
        sample_size=5,
    )
    assert effect["outcome"] == "regressed"
    assert store.get("improvement_commitment", "commitment-1")["status"] == "rolled_back"


def test_architecture_delivery_feedback_materializes_only_reviewed_future_revision(tmp_path):
    _store, architecture, _sprints, _retrospectives = _services(tmp_path)
    _active_baseline(architecture)
    evidence = architecture.record_delivery_evidence(
        evidence_id="arch-evidence-1",
        scope_id="project-1",
        sprint_id="sprint-1",
        architecture_revision_id="arch-1",
        evidence_type="integration_failure",
        severity="high",
        artifact_refs=["test-report-1"],
        summary="The integration boundary failed repeatedly",
    )
    debt = architecture.register_debt(
        debt_id="debt-1",
        scope_id="project-1",
        cause="Ambiguous boundary",
        risk="Repeated integration failures",
        evidence_ids=[evidence["evidence_id"]],
        workaround="Explicit adapter",
        expected_effect="Lower integration failure rate",
    )
    assert debt["status"] == "open"
    architecture.propose_change(
        proposal_id="arch-change-1",
        scope_id="project-1",
        parent_revision_id="arch-1",
        author_id="architecture-agent",
        evidence_ids=["arch-evidence-1"],
        affected_guardrails=["hub-owner"],
        alternatives=["Keep current adapter"],
        tradeoffs=["One additional contract"],
        migration={"breaking": False, "strategy": "additive", "compatibility": "backward"},
    )
    reviewed = architecture.review_change(
        proposal_id="arch-change-1",
        reviewer_id="architecture-decision-reviewer-agent",
        checks={"evidence": True, "security": True, "compatibility": True, "migration": True, "scope": True},
        decision="accepted",
    )
    assert reviewed["status"] == "accepted"
    materialized = architecture.materialize_accepted_change(
        proposal_id="arch-change-1",
        revision_id="arch-2",
        target_architecture={"style": "hub-worker", "adapter": "explicit"},
        guardrails=[{"guardrail_id": "hub-owner", "rule": "Hub owns queue", "scopes": ["api"]}],
        adr_refs=["ADR-2"],
        known_debt_ids=["debt-1"],
    )
    assert materialized["baseline"]["lifecycle_state"] == "draft"
    assert architecture.handoff(scope_id="project-1", sprint_scope=["api"])["architecture_revision_id"] == "arch-1"
    architecture.activate_baseline(
        revision_id="arch-2",
        reviewer_id="architecture-release-reviewer-agent",
        checks={"scope": True, "security": True, "compatibility": True, "migration": True, "evidence": True},
        evidence_refs=["arch-change-1"],
    )
    assert architecture.handoff(scope_id="project-1", sprint_scope=["api"])["architecture_revision_id"] == "arch-2"
    effect = architecture.evaluate_revision_effect(
        evaluation_id="arch-effect-1",
        scope_id="project-1",
        revision_id="arch-2",
        baseline_metrics={"integration_failures": 3, "change_cost": 5},
        observed_metrics={"integration_failures": 1, "change_cost": 5},
        sample_size=4,
    )
    assert effect["outcome"] == "improved"


def test_goal_exception_and_protected_improvement_fail_closed_without_humans(tmp_path):
    store, architecture, sprints, retrospectives = _services(tmp_path)
    _active_baseline(architecture)
    _plan_first(sprints)
    sprints.transition(sprint_id="sprint-1", target_state="active", occurred_at="2026-08-28T10:01:00Z")
    sprints.snapshot(
        sprint_id="sprint-1",
        snapshot_id="snapshot-risk",
        task_states={"task-1": "failed", "task-2": "blocked"},
        handoff_failures=1,
        gate_failures=1,
        rework_count=2,
        consumed_boundary={"task_count": 4, "token_count": 1200},
        architecture_finding_ids=["finding-1"],
        observed_at="2026-08-28T10:02:00Z",
    )
    decision = sprints.inspect_and_adapt(
        sprint_id="sprint-1",
        control_id="control-risk",
        snapshot_id="snapshot-risk",
        trigger="gate_failed",
        trigger_sequence=1,
    )
    assert decision["reachability"] == "unreachable"
    with pytest.raises(ValueError, match="not_authorized"):
        sprints.apply_goal_exception(
            sprint_id="sprint-1",
            control_id="control-risk",
            action="abort",
            replacement_goal=None,
            evidence_refs=["risk-report"],
            automated_policy_passed=False,
            occurred_at="2026-08-28T10:03:00Z",
        )
    aborted = sprints.apply_goal_exception(
        sprint_id="sprint-1",
        control_id="control-risk",
        action="abort",
        replacement_goal=None,
        evidence_refs=["risk-report"],
        automated_policy_passed=True,
        occurred_at="2026-08-28T10:03:00Z",
    )
    assert aborted["lifecycle_state"] == "aborted"

    # Protected Hub-core changes are rejected by policy, not parked for a person.
    retrospective = {
        "schema": "ananta.scrum-retrospective-analysis.v1",
        "scope_id": "project-1",
        "retrospective_id": "retro-protected",
        "sprint_id": "sprint-1",
        "hypotheses": [{"hypothesis_id": "hypothesis-1"}],
    }
    store.append("retrospective", "retro-protected", retrospective, expected_revision=0)
    retrospectives.propose_improvement(
        proposal_id="protected-1",
        retrospective_id="retro-protected",
        hypothesis_ids=["hypothesis-1"],
        proposal_type="process",
        target_ref="hub_core:task-state-machine",
        description="Unsafe direct mutation",
        expected_effect="Unknown",
        risk_level="low",
    )
    reviewed = retrospectives.review_improvement(
        proposal_id="protected-1",
        reviewer_id="automated-policy",
        checks={"evidence": True, "scope": True, "security": True, "rollback": True, "measurable": True},
    )
    assert reviewed["status"] == "rejected_protected_target"


def test_revision_store_detects_stale_writes_and_survives_restart(tmp_path):
    path = tmp_path / "scrum.sqlite3"
    store = ScrumStateStore(path)
    first = store.append("sample", "one", {"scope_id": "scope"}, expected_revision=0)
    with pytest.raises(ScrumStateConflictError):
        store.append("sample", "one", {"scope_id": "scope"}, expected_revision=0)
    restored = ScrumStateStore(path)
    assert restored.get("sample", "one") == first
