from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

from ananta_contracts.dspy_optimization import (
    OptimizationRunV1,
    PromotionPlanV1,
    canonical_digest,
    upcast_prompt_program,
)
from tests.dspy_optimization.helpers import program


def test_run_and_promotion_contracts_match_closed_json_schemas() -> None:
    run = OptimizationRunV1(
        tenant_id="tenant-1",
        run_id="run-1",
        attempt_id="attempt-1",
        state="running",
        revision=2,
        spec_digest="a" * 64,
        created_at="2026-09-04T00:00:00Z",
        updated_at="2026-09-04T00:00:01Z",
        reason_code="dspy_worker_running",
        usage={"model_calls": 1, "tokens": 9, "cost_micros": 0},
    )
    plan = PromotionPlanV1(
        tenant_id="tenant-1",
        scope_id="planning-en-v1",
        candidate_digest="b" * 64,
        baseline_digest="c" * 64,
        evaluation_digest="d" * 64,
        dataset_digest="e" * 64,
        metric_set_digest="f" * 64,
        thresholds_digest="1" * 64,
        expected_registry_revision=0,
        canary_percent=10,
        automatic_stop_reason_codes=["security_regression", "cost_regression"],
    )
    root = Path(__file__).parents[2] / "schemas" / "dspy"
    for payload, name in ((run.to_dict(), "optimization_run.v1.json"), (plan.to_dict(), "promotion_plan.v1.json")):
        jsonschema.Draft202012Validator(json.loads((root / name).read_text())).validate(payload)
    assert run.digest == canonical_digest(run.to_dict())
    assert plan.digest == canonical_digest(plan.to_dict())


def test_contracts_reject_unknown_fields_and_unsupported_upcasts() -> None:
    raw = program().to_dict()
    assert upcast_prompt_program(raw).digest == program().digest
    with pytest.raises(ValueError, match="upcast_unavailable"):
        upcast_prompt_program({**raw, "schema": "ananta.prompt-program.v0"})
    with pytest.raises(ValueError, match="unknown_field"):
        OptimizationRunV1.from_mapping({"unexpected": True})
    plan = PromotionPlanV1(
        tenant_id="tenant-1",
        scope_id="scope-1",
        candidate_digest="a" * 64,
        baseline_digest="b" * 64,
        evaluation_digest="c" * 64,
        dataset_digest="d" * 64,
        metric_set_digest="e" * 64,
        thresholds_digest="f" * 64,
        expected_registry_revision=0,
        canary_percent=5,
        automatic_stop_reason_codes=["security_regression"],
    )
    with pytest.raises(ValueError, match="canary_percent_invalid"):
        replace(plan, canary_percent=0)
