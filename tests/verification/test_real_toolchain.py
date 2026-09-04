from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.verification_promotion_service import VerificationPromotionService
from agent.services.verification_result_ingress import VerificationResultIngress
from ananta_contracts.verification import VerificationBudgets
from tests.verification.helpers import assignment
from worker.verification.adapters import (
    CrossHairCheckAdapter,
    CrossHairCoverAdapter,
    CrossHairDiffBehaviorAdapter,
    HypothesisCrossHairBackendAdapter,
)
from worker.verification.reproducer import CounterexampleReproducer

ROOT = Path(__file__).parents[2]
pytestmark = [
    pytest.mark.verification_real,
    pytest.mark.skipif(
        importlib.util.find_spec("crosshair") is None, reason="optional verification extra not installed"
    ),
]


def test_crosshair_checks_five_bounded_ananta_targets() -> None:
    targets = tuple(
        f"worker.verification.pilot_targets.{name}"
        for name in (
            "clamp",
            "normalize_identifier",
            "unique_in_order",
            "permission_subset_is_monotone",
            "normalize_dependencies",
        )
    )
    bound = assignment("crosshair_check", targets)
    bound = replace(
        bound,
        budgets=VerificationBudgets(
            timeout_seconds=60,
            max_cases=25,
            max_targets=20,
            max_output_bytes=128_000,
            memory_mb=1536,
        ),
    )
    report = CrossHairCheckAdapter().check(bound, repository=ROOT)
    assert report.status.value == "passed_with_bounded_search"
    assert report.reason_code == "bounded_search_no_counterexample"


def test_crosshair_finds_and_materializes_seeded_defect() -> None:
    target = "worker.verification.pilot_targets.intentionally_wrong_abs"
    bound = assignment("crosshair_check", (target,))
    report = CrossHairCheckAdapter().check(bound, repository=ROOT)
    assert report.status.value == "counterexample_found"
    assert report.counterexamples[0]["concrete_arguments"] == {"args": [-1]}
    accepted, created = VerificationResultIngress(lease_is_current=lambda *_: True).accept(bound, report.to_dict())
    assert created is True
    command = (
        "python",
        "-c",
        "from worker.verification.pilot_targets import intentionally_wrong_abs; "
        "assert intentionally_wrong_abs(-1) >= 0",
    )
    reproduction, _ = CounterexampleReproducer().reproduce(bound, repository=ROOT, command=command)
    decision = VerificationPromotionService(auto_approval_enabled=True).decide(
        counterexample=accepted["counterexamples"][0],
        test_candidate=accepted["counterexamples"][0],
        reproduction_status=reproduction.value,
        evidence_scope="test",
    )
    assert decision.allowed is True


def test_crosshair_cover_generates_concrete_pytest_candidate() -> None:
    target = "worker.verification.pilot_targets.clamp"
    report = CrossHairCoverAdapter().check(assignment("crosshair_cover", (target,)), repository=ROOT)
    assert report.status.value == "passed_with_bounded_search"
    assert report.reason_code == "cover_candidates_generated"


def test_diffbehavior_distinguishes_semantic_patch_and_bounds_equivalent_result() -> None:
    semantic_targets = (
        "worker.verification.pilot_targets.clamp",
        "worker.verification.pilot_targets.changed_clamp",
    )
    semantic = CrossHairDiffBehaviorAdapter().compare(
        assignment("crosshair_diff", semantic_targets),
        repository=ROOT,
        left_symbol=semantic_targets[0],
        right_symbol=semantic_targets[1],
    )
    assert semantic.status.value == "passed_with_bounded_search"
    assert semantic.reason_code == "behavior_difference_observed"

    equivalent_targets = (
        "worker.verification.pilot_targets.clamp",
        "worker.verification.pilot_targets.equivalent_clamp",
    )
    equivalent = CrossHairDiffBehaviorAdapter().compare(
        assignment("crosshair_diff", equivalent_targets),
        repository=ROOT,
        left_symbol=equivalent_targets[0],
        right_symbol=equivalent_targets[1],
    )
    assert equivalent.status.value == "inconclusive"
    assert equivalent.reason_code == "bounded_diff_no_difference"


def test_five_properties_run_with_crosshair_backend() -> None:
    targets = tuple(
        f"tests/verification/test_property_pilot.py::{name}"
        for name in (
            "test_clamp_stays_within_bounds",
            "test_identifier_normalization_is_idempotent",
            "test_unique_output_contains_no_duplicates",
            "test_unique_output_preserves_first_occurrence_order",
            "test_permission_allow_is_monotone",
        )
    )
    bound = assignment("crosshair_backend", targets)
    bound = replace(
        bound,
        budgets=VerificationBudgets(
            timeout_seconds=180,
            max_cases=5,
            max_targets=20,
            max_output_bytes=128_000,
            memory_mb=1536,
        ),
    )
    report = HypothesisCrossHairBackendAdapter().run(bound, repository=ROOT)
    assert report.status.value == "passed_with_bounded_search"
