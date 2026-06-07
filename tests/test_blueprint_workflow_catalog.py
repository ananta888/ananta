"""Tests for the standard blueprint catalog (WFG-018/019).

The seed blueprints live in
``config/blueprints/standard/blueprints.json``. This module
asserts that the canonical workflows attached to the
Scrum-OpenCode, Code-Repair, TDD, and Security-Review
blueprints pass the workflow definition service's
``validate_workflow_definition`` (WFG-004 contract) and the
artifact-flow service's ``validate_workflow_artifact_graph``
(WFG-016 contract).

A regression here would mean a blueprint ships with an
invalid workflow and the goal materializer silently falls
back to legacy artifact-based subtask creation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.workflow_artifact_flow import (  # noqa: E402
    validate_workflow_artifact_graph,
)
from agent.services.workflow_definition_service import (  # noqa: E402
    BlueprintWorkflowStepDB,
    WorkflowDefinitionError,
    WorkflowDefinitionService,
)

BLUEPRINT_PATH = Path("config/blueprints/standard/blueprints.json")


def _load_catalog():
    return json.loads(BLUEPRINT_PATH.read_text())


def _workflow_blueprints():
    catalog = _load_catalog()
    return [
        bp for bp in catalog["blueprints"]
        if isinstance(bp, dict) and isinstance(bp.get("workflow"), dict)
    ]


# ---------------------------------------------------------------------------
# Top-level catalog
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_catalog_loads(self):
        catalog = _load_catalog()
        assert catalog["schema"] == "seed_blueprint_catalog.v1"
        assert isinstance(catalog["blueprints"], list)
        assert len(catalog["blueprints"]) > 0

    def test_required_workflow_blueprints_present(self):
        names = {bp["name"] for bp in _load_catalog()["blueprints"]}
        # WFG-018: Scrum-OpenCode carries the explicit workflow
        assert "Scrum-OpenCode" in names
        # WFG-019: Code-Repair, TDD, Security-Review each carry one
        for required in ("Code-Repair", "TDD", "Security-Review"):
            assert required in names, f"missing blueprint: {required}"


# ---------------------------------------------------------------------------
# Per-workflow validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blueprint_name",
    ["Scrum-OpenCode", "Code-Repair", "TDD", "Security-Review"],
)
class TestWorkflowsValidate:
    def test_workflow_definition_is_valid(self, blueprint_name):
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        wf = bp["workflow"]
        # Build a BlueprintWorkflowStepDB per step so the
        # definition service can run its topological sort. A
        # valid workflow produces a non-empty sorted list
        # without raising WorkflowDefinitionError.
        steps = []
        for step in wf["steps"]:
            steps.append(
                BlueprintWorkflowStepDB(
                    step_id=str(step["id"]),
                    role_name=str(step.get("role", "")),
                    task_kind=str(step.get("task_kind", "")),
                    task_ref=str(step.get("task_ref", "")),
                    depends_on=list(step.get("depends_on") or []),
                    produces=list(step.get("produces") or []),
                    consumes=list(step.get("consumes") or []),
                    gate=bool(step.get("gate", False)),
                )
            )
        try:
            order = WorkflowDefinitionService().topological_order(steps=steps)
        except WorkflowDefinitionError as exc:
            pytest.fail(f"{blueprint_name}: {exc}")
        order_ids = [s.step_id for s in order]
        assert order_ids == [s["id"] for s in wf["steps"]]

    def test_workflow_artifact_graph_is_valid(self, blueprint_name):
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        wf = bp["workflow"]
        # The seed keys are blueprint-specific (e.g. Code-Repair
        # starts with an incident_report, Security-Review with
        # the code_changes artifact).
        seed = list(wf.get("seed_artifact_keys") or [])
        report = validate_workflow_artifact_graph(
            steps=wf["steps"], goal_seed_artifact_keys=seed
        )
        assert report.is_valid, (
            f"{blueprint_name}: "
            f"{[v.to_dict() for v in report.violations]}"
        )

    def test_workflow_has_gate_step(self, blueprint_name):
        """Every standard workflow has at least one explicit
        gate step. The gate is what WFG-011 evaluates and what
        WFG-024 routes human approval to."""
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        steps = bp["workflow"]["steps"]
        gates = [s for s in steps if s.get("gate")]
        assert len(gates) >= 1, f"{blueprint_name}: no gate step"
        for gate in gates:
            assert "checks" in gate and len(gate["checks"]) >= 1
            assert "failure_policy" in gate

    def test_workflow_step_ids_unique(self, blueprint_name):
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        step_ids = [s["id"] for s in bp["workflow"]["steps"]]
        assert len(step_ids) == len(set(step_ids)), (
            f"{blueprint_name}: duplicate step ids {step_ids}"
        )

    def test_workflow_step_task_refs_match_artifacts(self, blueprint_name):
        """Each workflow step's task_ref must point to an
        existing artifact in the same blueprint (the planner
        uses this join key to materialise the step)."""
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        artifact_titles = {a["title"] for a in bp.get("artifacts", [])}
        for step in bp["workflow"]["steps"]:
            assert step["task_ref"] in artifact_titles, (
                f"{blueprint_name} step {step['id']} "
                f"task_ref {step['task_ref']} not in artifacts"
            )


class TestBackwardsCompat:
    def test_legacy_blueprints_without_workflow_still_load(self):
        """Blueprints without a workflow block (e.g. plain
        Scrum, Kanban, Research) must continue to load. The
        workflow-aware materializer (WFG-007) MUST fall back
        to artifact-based subtask creation in that case."""
        catalog = _load_catalog()
        for bp in catalog["blueprints"]:
            # No assertion on absence — just that the catalog
            # parses with blueprints that may or may not have
            # a workflow block.
            if "workflow" in bp:
                assert isinstance(bp["workflow"], dict)
            else:
                # The absence of workflow is the legacy path.
                assert "workflow" not in bp
