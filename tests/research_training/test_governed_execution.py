from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

pytest.importorskip("torch")
pytest.importorskip("safetensors")

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.research_training_artifact_service import ResearchTrainingArtifactService
from agent.services.research_training_assignment_store import ResearchTrainingAssignmentStore
from agent.services.research_training_capability_service import ResearchTrainingCapabilityService
from agent.services.research_training_completion_service import ResearchTrainingCompletionService
from agent.services.research_training_dataset_service import ResearchTrainingDatasetService
from agent.services.research_training_dispatch_service import ResearchTrainingDispatchService
from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService
from agent.services.research_training_lineage_service import ResearchTrainingLineageService
from agent.services.research_training_policy import ResearchTrainingPolicy
from agent.services.research_training_quota_service import ResearchTrainingQuotaService
from agent.services.research_training_recipe_service import ResearchTrainingRecipeService
from agent.services.research_training_result_ingress import ResearchTrainingResultIngress
from agent.services.research_training_retention_service import ResearchTrainingRetentionService
from agent.services.research_training_run_service import ResearchTrainingRunService
from agent.services.research_training_safety_policy import ResearchTrainingSafetyPolicy
from agent.services.research_training_state_store import ResearchTrainingStateStore
from agent.services.research_training_telemetry_service import (
    InMemoryResearchTelemetrySink,
    ResearchTrainingTelemetryService,
)
from agent.services.research_training_worker_registry import ResearchTrainingWorkerRegistry
from ananta_contracts.research_training import canonical_json
from worker.training.research.job_runner import execute_assignment

from .real_helpers import pipeline_spec, stage


def registry() -> HubEvidenceRegistryService:
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        database,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    return HubEvidenceRegistryService(SqlEvidenceIdentityRepository(database))


def policy() -> ResearchTrainingPolicy:
    return ResearchTrainingPolicy.from_mapping(
        {
            "schema": "ananta.research-training-policy.v1",
            "enabled": True,
            "mode": "local",
            "automatic_release_enabled": True,
            "allowed_model_families": ["tiny-local"],
            "max_gpu_hours": 10,
            "max_storage_bytes": 100_000_000,
            "max_estimated_cost_microunits": 1_000_000,
            "max_world_size": 2,
            "max_stages": 16,
            "max_artifact_bytes": 10_000_000,
            "human_intervention_required": False,
        }
    )


def safety(*capabilities: str) -> ResearchTrainingSafetyPolicy:
    return ResearchTrainingSafetyPolicy.from_mapping(
        {
            "schema": "ananta.research-training-safety-policy.v1",
            "enabled_capabilities": list(capabilities),
            "maximum_dataset_bytes": 10_000_000,
            "maximum_checkpoint_count": 10,
            "maximum_checkpoint_bytes": 10_000_000,
            "code_evaluation_enabled": False,
            "rl_training_enabled": False,
            "multi_gpu_training_enabled": False,
            "network_mode": "none",
            "filesystem_scope": "task_workspace_only",
            "human_intervention_required": False,
        }
    )


def test_hub_automatically_admits_dispatches_executes_and_records_local_evidence(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    artifact_root = tmp_path / "artifacts"
    input_root.mkdir()
    output_root.mkdir()
    (input_root / "train.txt").write_text("hello world\nhello ananta\n")
    evidence_registry = registry()
    evidence = ResearchTrainingEvidenceService(evidence_registry)
    datasets = ResearchTrainingDatasetService(
        input_root,
        evidence=evidence,
        allowed_licenses=["synthetic-test"],
        maximum_dataset_bytes=10_000_000,
    )
    dataset = datasets.admit(
        tenant_id="tenant-a",
        project_id="project-a",
        candidates=[
            {
                "origin": {"kind": "generated-fixture", "version": "v1"},
                "relative_ref": "train.txt",
                "split": "train",
                "media_type": "text_plain",
                "license_id": "synthetic-test",
                "consent_class": "synthetic",
            }
        ],
        policy={"usage": "test-only"},
        evidence_scope="local",
    )
    assert dataset["shards"][0]["source_id"].startswith("SRC_")

    configured_policy = policy()
    recipes = ResearchTrainingRecipeService(configured_policy)
    capabilities = ResearchTrainingCapabilityService(configured_policy)
    capabilities.report_worker(
        {
            "state": "available",
            "reason_code": None,
            "engine_version": "local-torch-v1",
            "capabilities": ["tokenizer_training"],
            "gpu_profiles": ["cpu"],
            "network_probe_performed": False,
        }
    )
    runs = ResearchTrainingRunService(
        ResearchTrainingStateStore(tmp_path / "runs.sqlite3"),
        policy=configured_policy,
        capabilities=capabilities,
        recipes=recipes,
        signing_key=b"r" * 32,
    )
    definition = stage("tokenizer", "tokenizer_train", [], "tokenizer_training")
    spec = pipeline_spec(dataset, [definition])
    created = runs.create(spec=spec, idempotency_key="real-e2e-tokenizer")
    workers = ResearchTrainingWorkerRegistry()
    workers.report(
        {
            "worker_id": "worker-real-cpu",
            "state": "available",
            "capabilities": ["tokenizer_training"],
            "backend_versions": {"ananta-local-torch": "v1"},
            "gpu_count": 0,
            "vram_bytes_per_gpu": 0,
            "compute_capability": "cpu",
            "supported_dtypes": ["float32"],
            "distributed_available": True,
            "storage_headroom_bytes": 20_000_000,
            "expires_at_epoch": 4_102_444_800.0,
        }
    )
    quota = ResearchTrainingQuotaService(
        tmp_path / "quota.sqlite3",
        maximum_bytes_per_tenant=100_000_000,
    )
    assignments = ResearchTrainingAssignmentStore(tmp_path / "assignments.sqlite3")
    dispatch = ResearchTrainingDispatchService(
        runs=runs,
        workers=workers,
        evidence=evidence,
        assignments=assignments,
        safety=safety("tokenizer_training"),
        quota=quota,
    )
    prepared = dispatch.prepare(
        tenant_id="tenant-a",
        project_id="project-a",
        run_id=created["run_id"],
        task_id="task-tokenizer",
        assignment_id="assignment-tokenizer",
        dispatch_lease_id="lease-tokenizer",
        expected_revision=created["revision"],
        dataset_manifest=dataset,
        runtime={
            "schema": "ananta.research-training-runtime.v1",
            "repository_revision": "a" * 64,
            "image_digest": "c" * 64,
            "python_version": "3.12.14",
            "torch_version": "2.6.0+cpu",
            "cuda_version": "none",
            "backend_name": "ananta-local-torch",
            "backend_version": "v1",
            "hardware_profile_digest": "d" * 64,
            "deterministic_algorithms": True,
        },
        inputs=[],
        parameters={"special_tokens": ["<assistant>", "</assistant>"]},
        workspace_subdir="workspace",
        required_storage_bytes=1_000_000,
        lease_seconds=300,
        evidence_scope="local",
        evidence_idempotency_key="research-evidence-tokenizer",
    )
    assert assignments.resolve_for_worker(
        assignment_id="assignment-tokenizer",
        worker_id="worker-real-cpu",
    )["assignment"]["run_id"] == created["run_id"]
    with pytest.raises(PermissionError, match="worker_assignment_binding_invalid"):
        assignments.resolve_for_worker(
            assignment_id="assignment-tokenizer",
            worker_id="worker-other",
        )
    assignment_path = input_root / "assignment.json"
    assignment_path.write_text(canonical_json(prepared["assignment"]))
    envelope = execute_assignment(
        assignment_path=assignment_path,
        input_root=input_root,
        output_root=output_root,
        maximum_input_bytes=10_000_000,
    )
    assert envelope["human_intervention_required"] is False

    lineage = ResearchTrainingLineageService(tmp_path / "lineage.sqlite3")
    artifacts = ResearchTrainingArtifactService(
        artifact_root,
        max_artifact_bytes=10_000_000,
        quota=quota,
    )
    ingress = ResearchTrainingResultIngress(
        output_root,
        evidence=evidence,
        assignments=assignments,
        artifacts=artifacts,
        lineage=lineage,
        maximum_result_bytes=10_000_000,
    )
    completed = ResearchTrainingCompletionService(
        assignments=assignments,
        ingress=ingress,
        runs=runs,
    ).complete(
        tenant_id="tenant-a",
        project_id="project-a",
        worker_id="worker-real-cpu",
        assignment_id="assignment-tokenizer",
        result_ref="result.json",
        retention_class="checkpoint",
    )
    assert completed["run"]["state"] == "completed"
    assert completed["evidence"]["run_id"].startswith("RUN_")
    verification = evidence_registry.verify_release_binding(
        tenant_id="tenant-a",
        project_id="project-a",
        run_id=completed["evidence"]["run_id"],
        required_scope="local",
        task_id="task-tokenizer",
        repository_revision="a" * 64,
        source_ids=[dataset["shards"][0]["source_id"]],
    )
    assert verification.verified is True
    assert lineage.list_run(tenant_id="tenant-a", run_id=created["run_id"])["items"]


def test_dataset_admission_fails_closed_on_secrets_pii_duplicates_and_contamination(
    tmp_path: Path,
) -> None:
    evidence = ResearchTrainingEvidenceService(registry())
    service = ResearchTrainingDatasetService(
        tmp_path,
        evidence=evidence,
        allowed_licenses=["MIT"],
        maximum_dataset_bytes=10_000,
    )
    candidate = {
        "origin": {"repository": "local"},
        "relative_ref": "train.txt",
        "split": "train",
        "media_type": "text_plain",
        "license_id": "MIT",
        "consent_class": "public",
    }
    (tmp_path / "train.txt").write_text("person@example.com\n")
    with pytest.raises(PermissionError, match="pii_detected"):
        service.admit(
            tenant_id="tenant-a",
            project_id="project-a",
            candidates=[candidate],
            policy={},
            evidence_scope="test",
            synthetic=True,
        )
    (tmp_path / "train.txt").write_text("api_key=abcdefghijklmnop\n")
    with pytest.raises(PermissionError, match="secret_detected"):
        service.admit(
            tenant_id="tenant-a",
            project_id="project-a",
            candidates=[candidate],
            policy={},
            evidence_scope="test",
            synthetic=True,
        )
    (tmp_path / "train.txt").write_text("same\nsame\n")
    with pytest.raises(PermissionError, match="duplicate_record"):
        service.admit(
            tenant_id="tenant-a",
            project_id="project-a",
            candidates=[candidate],
            policy={},
            evidence_scope="test",
            synthetic=True,
        )


def test_worker_inventory_drift_quota_and_telemetry_are_bounded(tmp_path: Path) -> None:
    now = [100.0]
    workers = ResearchTrainingWorkerRegistry(clock=lambda: now[0])
    report = workers.report(
        {
            "worker_id": "worker-cpu",
            "state": "available",
            "capabilities": ["full_weight_training"],
            "backend_versions": {"torch": "v2.6.0"},
            "gpu_count": 0,
            "vram_bytes_per_gpu": 0,
            "compute_capability": "cpu",
            "supported_dtypes": ["float32"],
            "distributed_available": False,
            "storage_headroom_bytes": 1000,
            "expires_at_epoch": 200.0,
        }
    )
    assert workers.select(
        required_capability="full_weight_training",
        world_size=1,
        precision="float32",
        required_storage_bytes=500,
    )["worker_id"] == "worker-cpu"
    with pytest.raises(ValueError, match="capability_drift"):
        workers.revalidate(worker_id="worker-cpu", expected_report_digest="0" * 64)
    now[0] = 201.0
    with pytest.raises(LookupError, match="inventory_expired"):
        workers.revalidate(worker_id="worker-cpu", expected_report_digest=report["report_digest"])

    quota = ResearchTrainingQuotaService(
        tmp_path / "quota.sqlite3", maximum_bytes_per_tenant=100, clock=lambda: now[0]
    )
    quota.reserve(tenant_id="tenant-a", reservation_id="one", expected_bytes=80, lease_seconds=10)
    with pytest.raises(ValueError, match="quota_exceeded"):
        quota.reserve(tenant_id="tenant-a", reservation_id="two", expected_bytes=30, lease_seconds=10)

    sink = InMemoryResearchTelemetrySink()
    telemetry = ResearchTrainingTelemetryService([sink])
    event = {
        "schema": "ananta.research-training-metric.v1",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "stage_id": "stage-a",
        "attempt_id": "attempt-a",
        "sequence": 0,
        "metric": "train_loss",
        "value": 1.5,
        "unit": "ratio",
    }
    telemetry.ingest(event)
    with pytest.raises(ValueError, match="sequence_stale"):
        telemetry.ingest(event)
    assert sink.events == [event]
    assert "secret" not in json.dumps(sink.events).lower()


def test_retention_never_deletes_a_referenced_parent(tmp_path: Path) -> None:
    quota = ResearchTrainingQuotaService(
        tmp_path / "quota.sqlite3", maximum_bytes_per_tenant=10_000
    )
    lineage = ResearchTrainingLineageService(tmp_path / "lineage.sqlite3")
    artifacts = ResearchTrainingArtifactService(
        tmp_path / "artifacts", max_artifact_bytes=1000, quota=quota
    )

    def publish(name: str, content: bytes, parents: list[str]) -> str:
        digest = hashlib.sha256(content).hexdigest()
        quota.reserve(
            tenant_id="tenant-a",
            reservation_id=f"reservation-{name}",
            expected_bytes=100,
            lease_seconds=60,
        )
        manifest = {
            "schema": "ananta.research-training-artifact.v1",
            "tenant_id": "tenant-a",
            "run_id": "run-retention",
            "stage_id": name,
            "attempt_id": f"attempt-{name}",
            "artifact_kind": "base_checkpoint" if not parents else "sft_checkpoint",
            "artifact_digest": digest,
            "size_bytes": len(content),
            "parent_artifact_digests": parents,
            "recipe_digest": "a" * 64,
            "dataset_digest": "b" * 64,
            "executable": False,
            "source_refs": ["SRC_retention"],
            "run_refs": ["RUN_retention"],
        }
        receipt = artifacts.publish(
            manifest=manifest,
            content=content,
            reservation_id=f"reservation-{name}",
            retention_class="ephemeral",
        )
        lineage.register(manifest=manifest, artifact_ref=receipt["artifact_ref"])
        return digest

    parent = publish("parent", b"parent", [])
    child = publish("child", b"child", [parent])
    retention = ResearchTrainingRetentionService(tmp_path / "artifacts", quota, lineage)
    first = retention.collect(tenant_id="tenant-a", referenced_digests=[])
    assert first["deleted_digests"] == [child]
    assert lineage.get(tenant_id="tenant-a", artifact_digest=parent)
    second = retention.collect(tenant_id="tenant-a", referenced_digests=[])
    assert second["deleted_digests"] == [parent]
