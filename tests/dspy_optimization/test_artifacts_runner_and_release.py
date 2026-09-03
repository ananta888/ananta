from __future__ import annotations

from dataclasses import replace

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.dspy_program_artifact_store import DspyProgramArtifactStore
from agent.services.dspy_release_gate import DspyEvidenceBinding, DspyReleaseGate
from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from tests.dspy_optimization.helpers import program, spec
from worker.optimization.dspy.job_runner import DspyOptimizationJobRunner


class FakeEngine:
    def optimize(self, _spec, baseline, _records):
        return replace(baseline, program_id="planning-candidate")


def test_headless_worker_e2e_produces_tenant_scoped_json_artifact_without_delegation(tmp_path) -> None:
    runner = DspyOptimizationJobRunner(
        FakeEngine(), DspyProgramArtifactStore(tmp_path / "artifacts"), authorization_verifier=lambda job: job["ok"]
    )
    result = runner.run(
        job={"ok": True, "tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()},
        baseline=program(),
        records=[{"goal": "ship", "constraints": []}],
    )
    assert result["state"] == "completed"
    assert result["hub_task_created"] is False
    assert result["worker_delegation_performed"] is False
    assert result["artifact"]["artifact_ref"].startswith("dspy-program:tenant-1:run-1:")


def test_cancelled_worker_stops_before_optimization_without_human_wait(tmp_path) -> None:
    result = DspyOptimizationJobRunner(
        FakeEngine(), DspyProgramArtifactStore(tmp_path / "artifacts"), authorization_verifier=lambda _job: True
    ).run(
        job={"tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()},
        baseline=program(),
        records=[],
        cancelled=lambda: True,
    )
    assert result["state"] == "cancelled"
    assert result["human_intervention_required"] is False


def test_release_gate_never_invents_or_promotes_missing_evidence() -> None:
    result = DspyReleaseGate().evaluate(
        local_gates={key: True for key in DspyReleaseGate.REQUIRED}, source_refs=[], run_refs=[]
    )
    assert result["release_allowed"] is False
    assert result["source_refs"] == []
    assert result["run_refs"] == []


def test_release_gate_accepts_exact_successful_hub_registry_binding() -> None:
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
            task_id="dspy-001",
            assignment_id="assignment-1",
            dispatch_lease_id="lease-1",
            repository_revision=revision,
            input_digest="a" * 64,
            execution_profile_digest="b" * 64,
            environment_digest="c" * 64,
            evidence_scope="local",
            required_scope="local",
            idempotency_key="dspy-baseline-1",
            sources=(EvidenceGateSourceAdmission("repository_bundle", "a" * 64, "b" * 64, "c" * 64),),
        ),
        lambda _assignment: {"passed": True},
    )
    binding = DspyEvidenceBinding("tenant-1", "project-1", "dspy-001", revision, "local")

    result = DspyReleaseGate(evidence_registry=registry).evaluate(
        local_gates={gate: True for gate in DspyReleaseGate.REQUIRED},
        source_refs=list(outcome.source_ids),
        run_refs=[outcome.run_id],
        evidence_binding=binding,
    )

    assert result["release_allowed"] is True
    assert result["evidence_reason_code"] == "verified"

    stale = DspyReleaseGate(evidence_registry=registry).evaluate(
        local_gates={gate: True for gate in DspyReleaseGate.REQUIRED},
        source_refs=list(outcome.source_ids),
        run_refs=[outcome.run_id],
        evidence_binding=DspyEvidenceBinding("tenant-1", "project-1", "dspy-001", "2" * 40, "local"),
    )
    assert stale["release_allowed"] is False
    assert stale["evidence_reason_code"].endswith("evidence_run_binding_mismatch")
