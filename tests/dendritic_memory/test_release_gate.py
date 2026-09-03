from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.dendritic_memory_release_gate import DendriticEvidenceBinding, DendriticMemoryReleaseGate
from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService


def test_release_gate_requires_exact_assignment_bound_evidence_without_human() -> None:
    gate = DendriticMemoryReleaseGate()
    denied = gate.evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=[],
        allowed_run_refs=[],
        requested_source_refs=[],
        requested_run_refs=[],
    )
    assert denied["eligible"] is False
    assert denied["human_intervention_required"] is False
    malformed = gate.evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=["invented"],
        allowed_run_refs=["invented"],
        requested_source_refs=["invented"],
        requested_run_refs=["invented"],
    )
    assert malformed["eligible"] is False
    allowed = gate.evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=["SRC_release"],
        allowed_run_refs=["RUN_staging"],
        requested_source_refs=["SRC_release"],
        requested_run_refs=["RUN_staging"],
    )
    assert allowed["eligible"] is True
    assert allowed["claims_verified"] is True


def test_release_gate_uses_exact_hub_registry_binding() -> None:
    database = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(
        database,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(database))
    revision = "1" * 40
    outcome = HubEvidenceGateService(registry).execute(
        EvidenceGateRequest(
            tenant_id="tenant-1",
            project_id="project-1",
            task_id="dend-071",
            assignment_id="assignment-1",
            dispatch_lease_id="lease-1",
            repository_revision=revision,
            input_digest="a" * 64,
            execution_profile_digest="b" * 64,
            environment_digest="c" * 64,
            evidence_scope="local",
            required_scope="local",
            idempotency_key="dendritic-local-gate-1",
            sources=(EvidenceGateSourceAdmission("repository_bundle", "a" * 64, "b" * 64, "c" * 64),),
        ),
        lambda _assignment: {"passed": True},
    )
    result = DendriticMemoryReleaseGate(evidence_registry=registry).evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=[],
        allowed_run_refs=[],
        requested_source_refs=outcome.source_ids,
        requested_run_refs=[outcome.run_id],
        evidence_binding=DendriticEvidenceBinding("tenant-1", "project-1", "dend-071", revision, "local"),
    )
    assert result["eligible"] is True
    assert result["evidence_reason_code"] == "verified"

    stale = DendriticMemoryReleaseGate(evidence_registry=registry).evaluate(
        p0_complete=True,
        ci_green=True,
        seed_count=3,
        task_family_count=2,
        critical_security_findings=0,
        rollback_verified=True,
        revoke_verified=True,
        deletion_verified=True,
        allowed_source_refs=[],
        allowed_run_refs=[],
        requested_source_refs=outcome.source_ids,
        requested_run_refs=[outcome.run_id],
        evidence_binding=DendriticEvidenceBinding("tenant-1", "project-1", "dend-071", "2" * 40, "local"),
    )
    assert stale["eligible"] is False
    assert stale["evidence_reason_code"].endswith("evidence_run_binding_mismatch")
