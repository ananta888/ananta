from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import (
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.agent_safety_release_gate import (
    AgentSafetyEvidenceBinding,
    AgentSafetyReleaseGate,
)
from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_REVISION = "1" * 40


def test_release_gate_never_promotes_local_or_unverified_evidence() -> None:
    result = AgentSafetyReleaseGate().evaluate(
        local_gates={
            "contracts": True,
            "security": True,
            "chaos": True,
            "api": True,
            "frontend": True,
        },
        containment_available=True,
        source_refs=[],
        run_refs=[],
    )
    assert result["release_allowed"] is False
    assert result["state"] == "blocked"
    assert "agent_safety_authoritative_source_evidence_unavailable" in result["reason_codes"]
    assert "agent_safety_runtime_evidence_unavailable" in result["reason_codes"]
    assert result["human_intervention_required"] is False


def test_release_gate_reports_missing_automatic_containment_and_local_gates() -> None:
    result = AgentSafetyReleaseGate().evaluate(
        local_gates={},
        containment_available=False,
        source_refs=["not-authoritative"],
        run_refs=["not-authoritative"],
    )
    assert result["release_allowed"] is False
    assert "agent_safety_local_gates_incomplete" in result["reason_codes"]
    assert "agent_safety_containment_adapter_unavailable" in result["reason_codes"]
    assert result["source_refs"] == []
    assert result["run_refs"] == []


def test_release_gate_accepts_only_exact_assignment_allowlists_without_human_review() -> None:
    local_gates = {gate: True for gate in AgentSafetyReleaseGate.REQUIRED_LOCAL_GATES}
    unprovided = AgentSafetyReleaseGate().evaluate(
        local_gates=local_gates,
        containment_available=True,
        source_refs=["SRC_fixture_source"],
        run_refs=["RUN_fixture_runtime"],
    )
    assert unprovided["release_allowed"] is False

    result = AgentSafetyReleaseGate(
        allowed_source_refs={"SRC_fixture_source"},
        allowed_run_refs={"RUN_fixture_runtime"},
    ).evaluate(
        local_gates=local_gates,
        containment_available=True,
        source_refs=["SRC_fixture_source"],
        run_refs=["RUN_fixture_runtime"],
    )
    assert result["release_allowed"] is True
    assert result["human_intervention_required"] is False


def test_release_gate_uses_successful_hub_registry_binding_without_allowlist() -> None:
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        database,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(database))
    outcome = HubEvidenceGateService(registry).execute(
        EvidenceGateRequest(
            tenant_id="tenant-1",
            project_id="project-1",
            task_id="ads-047",
            assignment_id="assignment-1",
            dispatch_lease_id="lease-1",
            repository_revision=_REVISION,
            input_digest=_A,
            execution_profile_digest=_B,
            environment_digest=_C,
            evidence_scope="local",
            required_scope="local",
            idempotency_key="ads-chaos-gate-0001",
            sources=(
                EvidenceGateSourceAdmission(
                    origin_type="agent_safety_chaos_bundle",
                    origin_digest=_A,
                    content_digest=_B,
                    policy_digest=_C,
                ),
            ),
        ),
        lambda _assignment: {"passed": True, "chaos_trials": 1},
    )
    binding = AgentSafetyEvidenceBinding(
        tenant_id="tenant-1",
        project_id="project-1",
        task_id="ads-047",
        repository_revision=_REVISION,
    )
    local_gates = {gate: True for gate in AgentSafetyReleaseGate.REQUIRED_LOCAL_GATES}

    result = AgentSafetyReleaseGate(evidence_registry=registry).evaluate(
        local_gates=local_gates,
        containment_available=True,
        source_refs=list(outcome.source_ids),
        run_refs=[outcome.run_id],
        evidence_binding=binding,
    )

    assert result["release_allowed"] is True
    assert result["evidence_reason_code"] == "verified"

    stale = AgentSafetyReleaseGate(evidence_registry=registry).evaluate(
        local_gates=local_gates,
        containment_available=True,
        source_refs=list(outcome.source_ids),
        run_refs=[outcome.run_id],
        evidence_binding=AgentSafetyEvidenceBinding(
            tenant_id="tenant-1",
            project_id="project-1",
            task_id="ads-047",
            repository_revision="2" * 40,
        ),
    )
    assert stale["release_allowed"] is False
    assert stale["evidence_reason_code"].endswith("evidence_run_binding_mismatch")
