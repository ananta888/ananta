from __future__ import annotations

from ananta_contracts.hub_evidence import build_hub_evidence_assignment
from ananta_contracts.verification import VerificationAssignmentV1, VerificationBudgets

_DIGEST = "a" * 64


def assignment(
    backend: str = "hypothesis", targets: tuple[str, ...] = ("tests/verification",)
) -> VerificationAssignmentV1:
    evidence = build_hub_evidence_assignment(
        run_id="RUN_verification_test",
        task_id="task-verification-test",
        assignment_id="assignment-verification-test",
        dispatch_lease_id="lease-verification-test",
        source_ids=["SRC_verification_fixture"],
        evidence_scope="test",
        binding_digest="b" * 64,
    )
    return VerificationAssignmentV1(
        evidence_assignment=evidence,
        repository_revision=_DIGEST,
        profile_id=f"{backend}-test",
        profile_digest="c" * 64,
        toolchain_digest="d" * 64,
        backend=backend,
        target_symbols=targets,
        budgets=VerificationBudgets(
            timeout_seconds=30,
            max_cases=25,
            max_targets=20,
            max_output_bytes=128_000,
            memory_mb=1536,
        ),
    )
