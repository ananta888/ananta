from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.local_adapter_evaluation_fixture_gate import (
    LocalAdapterEvaluationFixtureGate,
)
from agent.services.local_adapter_evaluation_service import (
    LocalAdapterEvaluationService,
    ToolEvaluationCase,
)
from ananta_contracts.local_adapter_evaluation_fixture import (
    REQUIRED_EVALUATION_SLICES,
    CuratedLocalAdapterEvaluationFixture,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scenarios" / "local-adapter-curated-evaluation.v1.json"


def _load() -> CuratedLocalAdapterEvaluationFixture:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return CuratedLocalAdapterEvaluationFixture.from_mapping(payload)


def test_curated_fixture_is_minimal_deterministic_and_not_training_evidence():
    fixture = _load()

    assert len(fixture.cases) == len(REQUIRED_EVALUATION_SLICES)
    assert set(fixture.thresholds) == REQUIRED_EVALUATION_SLICES
    assert fixture.sha256 == "dd9321f7a50a53ae12a7a1c208e9afd91dafe2d286352e14b7c61ece2f811638"
    assert all(case.provenance.candidate_generated is False for case in fixture.cases)
    assert all(case.provenance.training_eligible is False for case in fixture.cases)
    assert all(case.provenance.source_ids == () for case in fixture.cases)
    assert all(case.provenance.run_ids == () for case in fixture.cases)


def test_curated_fixture_drives_the_existing_independent_evaluation_gate():
    fixture = _load()
    cases = []
    for definition in fixture.cases:
        expected = {
            "tool": definition.expected_tool,
            "arguments": dict(definition.expected_arguments),
        }
        cases.append(
            ToolEvaluationCase(
                case_id=definition.case_id,
                slice_id=definition.slice_id,
                allowed_tools=frozenset(definition.allowed_tools),
                expected_tool=definition.expected_tool,
                required_arguments={
                    key: {
                        "string": str,
                        "integer": int,
                        "number": float,
                        "boolean": bool,
                    }[type_name]
                    for key, type_name in definition.required_argument_types.items()
                },
                expected_arguments=dict(definition.expected_arguments),
                base_decision=expected,
                candidate_decisions=(expected, expected),
                candidate_confidence=0.99,
                latency_ms=1.0,
                memory_bytes=1,
            )
        )
    report = LocalAdapterEvaluationService().evaluate(
        cases,
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        golden_set_sha256=fixture.sha256,
        policy_sha256="c" * 64,
        evaluation_seed=fixture.evaluation_seed,
    )

    result = LocalAdapterEvaluationFixtureGate().assess(fixture, report)

    assert result.passed is True
    assert result.reason_codes == ("local_adapter_fixture_gate_passed",)
    assert set(result.slice_results) == REQUIRED_EVALUATION_SLICES


def test_curated_fixture_rejects_candidate_authorship_and_missing_thresholds():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["provenance"]["candidate_generated"] = True
    with pytest.raises(ValueError):
        CuratedLocalAdapterEvaluationFixture.from_mapping(payload)

    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["thresholds"].pop("injection")
    with pytest.raises(ValueError, match="threshold_slices_invalid"):
        CuratedLocalAdapterEvaluationFixture.from_mapping(payload)
