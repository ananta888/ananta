from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.workflow_runtime.conformance import (
    DeterministicReferenceRuntime,
    RuntimeDifferentialEvaluator,
    RuntimeObservation,
    WorkflowConformanceEvaluator,
    WorkflowConformanceHarness,
)
from agent.services.workflow_runtime.reference_workflows import (
    REFERENCE_WORKFLOW_CATALOG_PATH,
    load_reference_workflows,
)


def test_catalog_contains_five_small_deterministic_valid_plans() -> None:
    raw_text = REFERENCE_WORKFLOW_CATALOG_PATH.read_text(encoding="utf-8")
    catalog = json.loads(raw_text)
    scenarios = load_reference_workflows()

    assert len(scenarios) == 5
    assert len(raw_text) < 30_000
    assert len({scenario.plan.plan_hash for scenario in scenarios}) == 5
    assert not re.search(r"/(home|Users|tmp)/", raw_text)
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw_text)
    assert not re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        raw_text,
        re.IGNORECASE,
    )
    assert all("plan_hash" not in item["plan"] for item in catalog["scenarios"])


def test_each_reference_workflow_has_policy_and_failure_variants() -> None:
    for scenario in load_reference_workflows():
        assert {variant.kind for variant in scenario.variants} == {
            "policy_denial",
            "runtime_failure",
        }
        assert all(variant.terminal_status == "failed" for variant in scenario.variants)
        assert all(variant.reason_code for variant in scenario.variants)
        assert all(variant.required_event_types for variant in scenario.variants)


def test_support_matrix_targets_first_four_for_native_and_langgraph() -> None:
    scenarios = load_reference_workflows()

    assert all(scenario.support_for("native") == "target" for scenario in scenarios[:4])
    assert all(scenario.support_for("langgraph") == "target" for scenario in scenarios[:4])
    assert scenarios[-1].support_for("native") == "incompatible"
    assert all(scenario.support_for("temporal") == "target" for scenario in scenarios)


@pytest.mark.parametrize("scenario", load_reference_workflows(), ids=lambda value: value.scenario_id)
def test_reference_oracle_satisfies_every_scenario(scenario) -> None:
    runtime = DeterministicReferenceRuntime()
    observation = runtime.observe(scenario)
    observation = RuntimeObservation(**{**observation.__dict__, "runtime_id": "temporal"})

    result = WorkflowConformanceEvaluator().evaluate(scenario, observation)

    assert result.status == "passed"
    assert result.issues == ()


def test_unsupported_capability_is_incompatible_never_success() -> None:
    scenario = load_reference_workflows()[0]
    observation = RuntimeObservation(
        runtime_id="native",
        terminal_status="completed",
        capabilities=frozenset(),
        event_types=scenario.invariants.required_event_types,
        artifact_ids=scenario.invariants.required_artifacts,
    )

    result = WorkflowConformanceEvaluator().evaluate(scenario, observation)

    assert result.status == "incompatible"
    assert result.status != "passed"


def test_invariant_failure_can_be_reported_as_expected_failure() -> None:
    scenario = load_reference_workflows()[0]
    observation = RuntimeObservation(
        runtime_id="native",
        terminal_status="failed",
        capabilities=frozenset(scenario.plan.capabilities),
    )

    result = WorkflowConformanceEvaluator().evaluate(
        scenario,
        observation,
        expected_failure=True,
    )

    assert result.status == "expected_failure"
    assert {issue.code for issue in result.issues} >= {
        "terminal_status_mismatch",
        "required_events_missing",
        "required_artifacts_missing",
    }


def test_differential_evaluator_ignores_nondeterministic_text() -> None:
    left = RuntimeObservation(
        runtime_id="native",
        terminal_status="completed",
        event_types=("workflow.run.started", "workflow.run.completed"),
        artifact_ids=("report",),
    )
    right = RuntimeObservation(
        runtime_id="langgraph",
        terminal_status="completed",
        event_types=("workflow.run.completed", "workflow.run.started"),
        artifact_ids=("report",),
    )

    assert RuntimeDifferentialEvaluator().compare(left, right) == ()


def test_conformance_harness_runs_every_scenario_ten_times_deterministically() -> None:
    records = WorkflowConformanceHarness(repetitions=10).run(
        load_reference_workflows(),
        (DeterministicReferenceRuntime(),),
    )

    assert len(records) == 5
    assert all(record.repetitions == 10 for record in records)
    assert all(record.status == "passed" for record in records)
    assert all(len(record.observation_digest) == 64 for record in records)


def test_conformance_failure_names_runtime_invariant_sequence_and_reproduction() -> None:
    scenario = load_reference_workflows()[0]
    observation = DeterministicReferenceRuntime().observe(scenario)
    broken = replace(observation, event_types=("workflow.run.started",))

    result = WorkflowConformanceEvaluator().evaluate(scenario, broken)

    issue = next(item for item in result.issues if item.code == "required_events_missing")
    assert result.runtime_id == "reference"
    assert issue.event_sequence == ("workflow.run.started",)
    assert issue.minimal_reproduction == {
        "runtime_id": "reference",
        "scenario_id": "research",
        "invariant": "required_events_missing",
    }


def test_checked_in_conformance_command_is_network_free_and_machine_readable() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/run-workflow-runtime-conformance.py", "--repetitions", "10"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "ananta.workflow_conformance_suite.v1"
    assert len(payload["records"]) == 5
