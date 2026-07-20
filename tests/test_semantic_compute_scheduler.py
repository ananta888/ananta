from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.semantic_compute_policy import ComputeCandidate, SemanticComputePolicy
from agent.services.semantic_compute_scheduler import (
    ScheduleRequest,
    SemanticComputeScheduler,
    SemanticComputeSchedulingError,
)


class LeaseStore:
    def __init__(self):
        self.requests = []

    def acquire(self, request):
        self.requests.append(request)
        return type("Lease", (), {"id": f"lease-{request.role}", "fencing_token": len(self.requests)})()


class ConsentAuthority:
    def __init__(self, denied: set[str] | None = None):
        self.denied = denied or set()

    def authorized(self, context):
        return context.candidate_id not in self.denied


def candidate(candidate_id: str, **values) -> ComputeCandidate:
    defaults = dict(
        offered_roles=frozenset({"primary", "validator", "standby"}),
        task_types=frozenset({"visual_extract"}),
        self_capacity=3,
        measured_capacity=3,
        user_limit=3,
        reserve_capacity=1,
        recent_error_rate=0.0,
        reputation=50,
        active_assignments=0,
        failure_domain=candidate_id,
        consent=True,
    )
    defaults.update(values)
    return ComputeCandidate(candidate_id=candidate_id, **defaults)


def request(**values) -> ScheduleRequest:
    defaults = dict(
        tenant_id="tenant-a",
        owner_subject="owner-a",
        contract_id="contract-a",
        contract_digest="a" * 64,
        session_id="session-a",
        room_id=None,
        epoch=1,
        task_type="visual_extract",
        audience="viewer-a",
        sequence_start=0,
        sequence_end=4,
        resource_budget={"cpu_ms": 10, "memory_bytes": 1_048_576, "artifact_bytes": 100},
        deadline_at=9_999_999_999.0,
    )
    defaults.update(values)
    return ScheduleRequest(**defaults)


def test_observed_capacity_user_limit_reserve_and_errors_override_self_claim() -> None:
    policy = SemanticComputePolicy()
    item = candidate("peer", self_capacity=99, measured_capacity=2, user_limit=1, reserve_capacity=1)
    reduced = policy.reduce(item, role="primary", task_type="visual_extract", minimum_capacity=1)
    assert reduced.effective_capacity == 0
    assert reduced.reason_code == "capacity_insufficient"
    unhealthy = replace(item, reserve_capacity=0, recent_error_rate=0.5)
    assert not policy.reduce(unhealthy, role="primary", task_type="visual_extract", minimum_capacity=1).eligible


def test_scheduler_tie_break_fairness_validator_independence_and_standby() -> None:
    leases = LeaseStore()
    scheduler = SemanticComputeScheduler(leases, consent_authority=ConsentAuthority())  # type: ignore[arg-type]
    roles = scheduler.schedule(
        request(validator_count=1, hot_standby=True),
        [candidate("peer-b"), candidate("peer-a"), candidate("peer-c")],
    )
    assert [(item.role, item.candidate_id) for item in roles] == [
        ("primary", "peer-a"),
        ("validator", "peer-b"),
        ("standby", "peer-c"),
    ]
    assert all(item.executor_id == role.candidate_id for item, role in zip(leases.requests, roles))


def test_scheduler_reports_under_capacity_and_consent_revocation() -> None:
    scheduler = SemanticComputeScheduler(LeaseStore(), consent_authority=ConsentAuthority({"peer"}))  # type: ignore[arg-type]
    with pytest.raises(SemanticComputeSchedulingError, match="no_eligible_primary"):
        scheduler.schedule(request(), [candidate("peer")])


def test_scheduler_prefers_less_loaded_healthy_candidate_and_skips_failed_peer() -> None:
    leases = LeaseStore()
    scheduler = SemanticComputeScheduler(leases, consent_authority=ConsentAuthority())  # type: ignore[arg-type]
    roles = scheduler.schedule(
        request(),
        [
            candidate("busy", active_assignments=4),
            candidate("failed", available=False, reputation=100),
            candidate("idle", active_assignments=0),
        ],
    )
    assert roles[0].candidate_id == "idle"


def test_all_validators_are_pairwise_fault_domain_independent() -> None:
    scheduler = SemanticComputeScheduler(LeaseStore(), consent_authority=ConsentAuthority())  # type: ignore[arg-type]
    roles = scheduler.schedule(
        request(validator_count=2),
        [
            candidate("primary", failure_domain="zone-a"),
            candidate("validator-a", failure_domain="zone-b"),
            candidate("validator-b", failure_domain="zone-c"),
        ],
    )
    assert [role.candidate_id for role in roles] == [
        "primary",
        "validator-a",
        "validator-b",
    ]

    with pytest.raises(SemanticComputeSchedulingError, match="no_eligible_validator"):
        scheduler.plan(
            request(validator_count=2),
            [
                candidate("primary", failure_domain="zone-a"),
                candidate("validator-a", failure_domain="zone-b"),
                candidate("validator-b", failure_domain="zone-b"),
            ],
        )
