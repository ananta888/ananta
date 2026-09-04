from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.verification_orchestration_service import VerificationOrchestrationService
from agent.services.verification_profiles import VerificationProfileService
from agent.services.verification_result_ingress import VerificationResultIngress
from agent.services.verification_target_selector import VerificationTargetSelector
from ananta_contracts.verification import CounterexampleV1, VerificationReportV1, VerificationStatus
from tests.verification.helpers import assignment

ROOT = Path(__file__).parents[2]


def _report() -> VerificationReportV1:
    bound = assignment()
    return VerificationReportV1(
        assignment_digest=bound.digest,
        run_ref=bound.evidence_assignment["run_id"],
        repository_revision=bound.repository_revision,
        profile_id=bound.profile_id,
        profile_digest=bound.profile_digest,
        toolchain_digest=bound.toolchain_digest,
        backend=bound.backend,
        target_symbols=bound.target_symbols,
        status="passed",
        reason_code="properties_satisfied",
        cases_executed=25,
        duration_ms=10,
        output_digest="e" * 64,
    )


def test_assignment_rejects_unknown_backend_and_broadened_targets() -> None:
    bound = assignment()
    with pytest.raises(ValueError, match="verification_backend_invalid"):
        replace(bound, backend="unknown")
    with pytest.raises(ValueError, match="verification_target_budget_exceeded"):
        replace(bound, target_symbols=tuple(f"target-{index}" for index in range(21)))


@pytest.mark.parametrize(
    ("backend", "target"),
    [
        ("hypothesis", "--collect-only"),
        ("hypothesis", "tests/verification/../conftest.py::test_escape"),
        ("hypothesis", "/tmp/test_escape.py::test_escape"),
        ("hypothesis", "tests/verification/test_property_pilot.py::-c"),
        ("crosshair_check", "--plugin=escape"),
        ("crosshair_cover", "agent.policy.fn\n--unblock=EVERYTHING"),
    ],
)
def test_assignment_rejects_cli_and_path_injection_targets(backend: str, target: str) -> None:
    with pytest.raises(ValueError, match="verification_target_grammar_invalid"):
        assignment(backend, (target,))


def test_diff_assignment_requires_exactly_two_python_symbols() -> None:
    with pytest.raises(ValueError, match="verification_diff_target_count_invalid"):
        assignment("crosshair_diff", ("worker.verification.pilot_targets.clamp",))


def test_report_rejects_unknown_status_and_missing_counterexample() -> None:
    report = _report()
    with pytest.raises(ValueError, match="verification_report_status_invalid"):
        replace(report, status="successful")
    with pytest.raises(ValueError, match="verification_counterexample_required"):
        replace(report, status=VerificationStatus.COUNTEREXAMPLE_FOUND)


def test_report_accepts_legacy_payload_without_additive_test_counts() -> None:
    payload = _report().to_dict()
    for field in ("collected_tests", "passed_tests", "failed_tests", "bounded_search_metadata"):
        payload.pop(field)
    restored = VerificationReportV1.from_mapping(payload)
    assert (restored.collected_tests, restored.passed_tests, restored.failed_tests) == (0, 0, 0)


def test_counterexample_rejects_symbolic_or_custom_values() -> None:
    class SymbolicProxy:
        pass

    with pytest.raises(ValueError, match="verification_counterexample_not_concrete"):
        CounterexampleV1(
            run_ref="RUN_test",
            property_ref="property",
            target_symbol="module.fn",
            concrete_arguments=SymbolicProxy(),
            observed_result=None,
            expected_invariant="must hold",
            reproduction_command=["pytest", "-q"],
            test_candidate_digest="f" * 64,
        )


def test_profile_service_enforces_isolation_and_builds_assignment() -> None:
    service = VerificationProfileService(ROOT / "config/verification/profiles.v1.json")
    profile = service.get_enabled("hypothesis-pr-fast")
    original = assignment()
    built = VerificationOrchestrationService(service).build_assignment(
        evidence_assignment=original.evidence_assignment,
        repository_revision=original.repository_revision,
        profile_id=profile.profile_id,
        toolchain_digest=original.toolchain_digest,
        target_symbols=("tests/verification/test_property_pilot.py::test_clamp_stays_within_bounds",),
    )
    assert built.backend == "hypothesis"
    assert built.evidence_assignment["evidence_scope"] == "test"


def test_target_selector_falls_back_only_to_explicit_allowlisted_targets() -> None:
    selector = VerificationTargetSelector(ROOT / "config/verification/property-catalog.v1.json")
    selected = selector.select(
        changed_symbols=["unknown.symbol"],
        explicit_targets=["worker.verification.pilot_targets.clamp"],
    )
    assert selected == ("worker.verification.pilot_targets.clamp",)
    with pytest.raises(ValueError, match="verification_no_bounded_targets"):
        selector.select(changed_symbols=[], explicit_targets=[])
    with pytest.raises(ValueError, match="verification_no_bounded_targets"):
        selector.select(
            changed_symbols=[],
            explicit_targets=["agent.services.optimization_hypothesis_service.OptimizationHypothesisService.generate"],
        )


def test_hub_ingress_is_idempotent_and_rejects_stale_or_mismatched_results() -> None:
    bound = assignment()
    report = _report().to_dict()
    ingress = VerificationResultIngress(lease_is_current=lambda *_: True)
    accepted, created = ingress.accept(bound, report)
    duplicate, duplicate_created = ingress.accept(bound, report)
    assert created is True
    assert duplicate_created is False
    assert duplicate == accepted

    stale = VerificationResultIngress(lease_is_current=lambda *_: False)
    with pytest.raises(ValueError, match="verification_dispatch_lease_stale"):
        stale.accept(bound, report)

    mismatched = dict(report)
    mismatched["repository_revision"] = "0" * 64
    with pytest.raises(ValueError, match="verification_result_repository_revision_mismatch"):
        ingress.accept(bound, mismatched)


def test_fake_test_scope_cannot_be_promoted_to_production_identity() -> None:
    bound = assignment()
    assert bound.evidence_assignment["evidence_scope"] == "test"
    tampered = dict(bound.evidence_assignment)
    tampered["evidence_scope"] = "production"
    with pytest.raises(ValueError, match="hub_evidence_assignment_digest_mismatch"):
        replace(bound, evidence_assignment=tampered)
