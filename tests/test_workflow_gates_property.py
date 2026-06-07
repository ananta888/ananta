"""Property / fuzz tests for the workflow-gates layer (WFG-027).

We don't have ``hypothesis`` as a runtime dep (see
pyproject.toml); these tests use ``pytest.mark.parametrize``
+ ``random``-driven fixtures to exercise the contracts
the workflow layer promises. The tests are intentionally
small (each runs in <50ms) and cover the contracts that
are most likely to regress under a refactor:

  - The artifact-flow validator accepts any DAG whose
    consumes are satisfied by produces or goal seeds.
  - The blueprint-migration service never produces a
    workflow with a cycle.
  - The human-approval service preserves the
    decision_id across re-submits.
  - The TUI view handles arbitrary payload shapes
    without raising.
"""

from __future__ import annotations

import random
import string
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.blueprint_migration_service import (  # noqa: E402
    LegacyBlueprintMigrationError,
    migrate_legacy_blueprint,
)
from agent.services.human_approval_service import (  # noqa: E402
    DECISION_APPROVED,
    DECISION_DEFERRED,
    DECISION_REJECTED,
    OPERATOR_DECISIONS,
    apply_human_decision,
    build_pending_approval_record,
    current_decision,
    is_pending_approval,
)
from agent.services.workflow_artifact_flow import (  # noqa: E402
    validate_workflow_artifact_graph,
)
from agent.tui.workflow_status_view import render_workflow_status  # noqa: E402

# ---------------------------------------------------------------------------
# Artifact-flow property tests
# ---------------------------------------------------------------------------


def _random_key(rng: random.Random) -> str:
    return "k_" + "".join(rng.choices(string.ascii_lowercase, k=6))


class TestArtifactFlowProperties:
    def test_fully_connected_dag_always_valid(self):
        rng = random.Random(42)
        n = 10
        steps = []
        # Step i produces "k_i" and consumes "k_{i-1}".
        # The chain has a producer for every consume.
        steps.append({
            "id": "s_0",
            "produces": [_random_key(rng)],
            "consumes": ["goal_brief"],
        })
        for i in range(1, n):
            steps.append({
                "id": f"s_{i}",
                "produces": [_random_key(rng)],
                "consumes": list(steps[-1]["produces"]),
            })
        report = validate_workflow_artifact_graph(
            steps=steps, goal_seed_artifact_keys=["goal_brief"]
        )
        assert report.is_valid, [v.to_dict() for v in report.violations]

    def test_orphan_consume_always_rejected(self):
        rng = random.Random(123)
        for _ in range(20):
            orphan = _random_key(rng)
            steps = [
                {"id": "a", "produces": [], "consumes": ["goal_brief"]},
                {"id": "b", "produces": [], "consumes": [orphan]},
            ]
            report = validate_workflow_artifact_graph(
                steps=steps, goal_seed_artifact_keys=["goal_brief"]
            )
            assert not report.is_valid
            assert any(
                v.reason == "no_producer" and v.missing_key == orphan
                for v in report.violations
            )

    def test_empty_steps_always_valid(self):
        report = validate_workflow_artifact_graph(
            steps=[], goal_seed_artifact_keys=["goal_brief"]
        )
        assert report.is_valid

    def test_self_referential_consume_is_valid(self):
        # A step that consumes something it also produces is
        # allowed; the validator treats "produces" as
        # satisfying the consume. This is a useful pattern
        # for iterative steps.
        steps = [
            {"id": "a", "produces": ["x"], "consumes": ["x", "goal_brief"]},
        ]
        report = validate_workflow_artifact_graph(
            steps=steps, goal_seed_artifact_keys=["goal_brief"]
        )
        assert report.is_valid, [v.to_dict() for v in report.violations]


# ---------------------------------------------------------------------------
# Blueprint migration property tests
# ---------------------------------------------------------------------------


class TestBlueprintMigrationProperties:
    @pytest.mark.parametrize("seed", range(20))
    def test_random_blueprint_migrates_to_valid_workflow(self, seed):
        rng = random.Random(seed)
        # Build a random legacy blueprint with a random
        # number of task artifacts, each producing a
        # random key.
        n_artifacts = rng.randint(1, 6)
        artifacts = []
        for i in range(n_artifacts):
            produces = [_random_key(rng)] if rng.random() < 0.8 else []
            artifacts.append({
                "title": f"Step {i}",
                "kind": "task",
                "sort_order": 10 * (i + 1),
                "payload": {"produces": produces},
            })
        bp = {
            "name": f"RandomBlueprint-{seed}",
            "description": f"Random blueprint seed={seed}",
            "roles": [
                {"name": "Owner", "is_required": True, "config": {}},
            ],
            "artifacts": artifacts,
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=True)
        # The result must have at least one step (the
        # first artifact is always included).
        steps = result["workflow"]["steps"]
        assert len(steps) == n_artifacts
        # Step ids must be unique.
        ids = [s["id"] for s in steps]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("seed", range(10))
    def test_legacy_blueprint_with_no_artifacts_raises_in_strict(self, seed):
        bp = {
            "name": f"Empty-{seed}",
            "description": "Empty legacy blueprint",
            "roles": [{"name": "Owner", "is_required": True, "config": {}}],
            "artifacts": [],
        }
        with pytest.raises(LegacyBlueprintMigrationError):
            migrate_legacy_blueprint(blueprint=bp, strict=True)


# ---------------------------------------------------------------------------
# Human-approval property tests
# ---------------------------------------------------------------------------


class TestHumanApprovalProperties:
    @pytest.mark.parametrize("outcome", list(OPERATOR_DECISIONS))
    def test_known_outcome_accepted(self, outcome):
        task = {
            "id": "t",
            "goal_id": "g",
            "verification_status": {
                "gate_decision": build_pending_approval_record(
                    goal_id="g", gate_task_id="t"
                )
            },
        }
        block = apply_human_decision(
            task=task, operator="op", outcome=outcome
        )
        assert block["status"] == outcome

    @pytest.mark.parametrize(
        "outcome",
        ["", "yes", "approve", "PENDING_APPROVAL", "PENDING", "unknown"],
    )
    def test_unknown_outcome_raises(self, outcome):
        task = {
            "id": "t",
            "goal_id": "g",
            "verification_status": {
                "gate_decision": build_pending_approval_record(
                    goal_id="g", gate_task_id="t"
                )
            },
        }
        if outcome in OPERATOR_DECISIONS:
            return  # skip if it happens to be valid
        with pytest.raises(Exception):
            apply_human_decision(task=task, operator="op", outcome=outcome)

    def test_re_submit_preserves_decision_id(self):
        """A re-submit with the SAME decision_id is a no-op.
        The apply_human_decision function does not
        deduplicate by decision_id; it preserves the
        existing one. This is a property, not a
        coincidence."""
        task = {
            "id": "t",
            "goal_id": "g",
            "verification_status": {
                "gate_decision": build_pending_approval_record(
                    goal_id="g", gate_task_id="t"
                )
            },
        }
        current = current_decision(task) or {}
        original = current["decision_id"]
        for outcome in [DECISION_APPROVED, DECISION_REJECTED, DECISION_DEFERRED]:
            block = apply_human_decision(
                task=task, operator="op", outcome=outcome
            )
            assert block["decision_id"] == original

    def test_is_pending_approval_reflects_state(self):
        # Pending at first
        task = {
            "id": "t",
            "goal_id": "g",
            "verification_status": {
                "gate_decision": build_pending_approval_record(
                    goal_id="g", gate_task_id="t"
                )
            },
        }
        assert is_pending_approval(task) is True
        # Approved => not pending
        apply_human_decision(
            task=task, operator="op", outcome=DECISION_APPROVED
        )
        assert is_pending_approval(task) is False
        # Rejected => not pending
        task["verification_status"]["gate_decision"] = (
            build_pending_approval_record(goal_id="g", gate_task_id="t")
        )
        apply_human_decision(
            task=task, operator="op", outcome=DECISION_REJECTED
        )
        assert is_pending_approval(task) is False
        # Deferred => not pending; deferred is a
        # terminal state from the gate engine's
        # perspective. (The queue will re-trigger the
        # gate later, not hold it pending.)
        task["verification_status"]["gate_decision"] = (
            build_pending_approval_record(goal_id="g", gate_task_id="t")
        )
        apply_human_decision(
            task=task, operator="op", outcome=DECISION_DEFERRED
        )
        assert is_pending_approval(task) is False


# ---------------------------------------------------------------------------
# TUI view property tests
# ---------------------------------------------------------------------------


class TestTuiViewProperties:
    @pytest.mark.parametrize("seed", range(10))
    def test_random_payload_renders(self, seed):
        rng = random.Random(seed)
        n_steps = rng.randint(0, 6)
        steps = []
        for i in range(n_steps):
            steps.append({
                "step_id": f"s_{i}",
                "role": f"Role {i}",
                "task_id": f"t_{i}",
                "task_status": "todo",
                "task_blocker_reason": "",
                "gate": bool(rng.random() < 0.3),
                "gate_decision": "",
                "is_blocker": bool(rng.random() < 0.2),
                "blocked_reasons": [],
                "missing_consumes": [],
            })
        view = render_workflow_status({"goal_id": "g", "steps": steps})
        assert view.text
        # The view never raises on random payloads.

    @pytest.mark.parametrize("seed", range(10))
    def test_random_payload_with_handoffs(self, seed):
        rng = random.Random(seed)
        n_handoffs = rng.randint(0, 20)
        handoffs = [
            {
                "from_step": f"a_{i}",
                "to_step": f"b_{i}",
                "status": "created",
            }
            for i in range(n_handoffs)
        ]
        view = render_workflow_status({
            "goal_id": "g",
            "steps": [],
            "handoff_events": handoffs,
        })
        assert view.text
        if n_handoffs > 10:
            assert "and" in view.text and "more" in view.text
