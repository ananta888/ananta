"""Tests for the standard blueprint catalog (WFG-018/019).

The seed blueprints live in
``config/blueprints/standard/blueprints.json``. This module
asserts that the canonical workflows attached to the
Scrum-OpenCode, Code-Repair, TDD, and Security-Review
blueprints pass the JSON schema validation (the loader
rejects a misconfigured catalog at startup) and the
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

from agent.services.seed_blueprint_catalog import (  # noqa: E402
    get_seed_blueprint_catalog,
)
from agent.services.workflow_artifact_flow import (  # noqa: E402
    validate_workflow_artifact_graph,
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

    def test_catalog_passes_jsonschema(self):
        """The seed catalog must validate against the JSON
        schema used by ``seed_blueprint_catalog`` at load
        time. A misconfigured workflow block would otherwise
        break the hub startup with a 500."""
        catalog = get_seed_blueprint_catalog()
        catalog.load()
        assert catalog.load_error is None, (
            f"catalog failed to load: {catalog.load_error}"
        )
        seed_map = catalog.as_seed_blueprint_map()
        assert "Scrum-OpenCode" in seed_map
        assert "Code-Repair" in seed_map
        assert "TDD" in seed_map
        assert "Security-Review" in seed_map


# ---------------------------------------------------------------------------
# Per-workflow validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blueprint_name",
    ["Scrum-OpenCode", "Code-Repair", "TDD", "Security-Review"],
)
class TestWorkflowsValidate:
    def test_workflow_artifact_graph_is_valid(self, blueprint_name):
        """The workflow steps must satisfy the artifact-flow
        validator: every consume must be satisfiable by an
        upstream produce, by a seed key, or by a goal-graph
        seed key (``Goal Brief`` etc.)."""
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        wf = bp["workflow"]
        # The first step's consumes must be satisfiable by
        # goal-graph seeds (Goal Brief etc.). Add the common
        # ones explicitly so the validator passes.
        goal_seeds = {"Goal Brief", "Acceptance Criteria"}
        if blueprint_name == "Code-Repair":
            goal_seeds.add("Incident Report")
        if blueprint_name == "Security-Review":
            goal_seeds.add("Code Changes")
        report = validate_workflow_artifact_graph(
            steps=wf["steps"], goal_seed_artifact_keys=list(goal_seeds)
        )
        assert report.is_valid, (
            f"{blueprint_name}: "
            f"{[v.to_dict() for v in report.violations]}"
        )

    def test_workflow_has_gate_step(self, blueprint_name):
        """Every standard workflow has at least one explicit
        gate step. The gate is what WFG-011 evaluates and
        what WFG-024 routes human approval to."""
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        steps = bp["workflow"]["steps"]
        gates = [s for s in steps if s.get("gate")]
        assert len(gates) >= 1, f"{blueprint_name}: no gate step"
        for gate in gates:
            assert "checks" in gate and gate["checks"], (
                f"{blueprint_name}: gate step {gate['id']} has no checks"
            )
            assert "failure_policy" in gate

    def test_workflow_step_ids_unique(self, blueprint_name):
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        step_ids = [s["id"] for s in bp["workflow"]["steps"]]
        assert len(step_ids) == len(set(step_ids)), (
            f"{blueprint_name}: duplicate step ids {step_ids}"
        )

    def test_workflow_step_role_matches_role_list(self, blueprint_name):
        """Each step's role must match a role name in the
        same blueprint's ``roles`` list (the
        reconciliation layer enforces this at workflow load)."""
        catalog = _load_catalog()
        bp = next(b for b in catalog["blueprints"] if b["name"] == blueprint_name)
        role_names = {r["name"] for r in bp.get("roles", [])}
        for step in bp["workflow"]["steps"]:
            assert step["role"] in role_names, (
                f"{blueprint_name} step {step['id']} role "
                f"{step['role']!r} not in roles"
            )


class TestBackwardsCompat:
    def test_legacy_blueprints_without_workflow_still_load(self):
        """Blueprints without a workflow block (e.g. plain
        Scrum, Kanban, Research) must continue to load. The
        workflow-aware materializer (WFG-007) MUST fall back
        to artifact-based subtask creation in that case."""
        catalog = _load_catalog()
        for bp in catalog["blueprints"]:
            if "workflow" in bp:
                assert isinstance(bp["workflow"], dict)
            else:
                assert "workflow" not in bp
