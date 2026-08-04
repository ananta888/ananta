from __future__ import annotations

from typing import Any

from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexAuthorityBinding,
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexExecutionJob,
    KnowledgeIndexExecutionPayload,
    KnowledgeIndexFileManifest,
    KnowledgeIndexPayloadArtifactRef,
    KnowledgeIndexResourceBudget,
)


def build_execution_job(
    *,
    max_runtime_seconds: int = 60,
) -> KnowledgeIndexExecutionJob:
    authority = KnowledgeIndexAuthorityBinding.create(
        tenant_id="tenant-alpha",
        project_id="project-atlas",
        source_revision_id=f"srev_{'a' * 64}",
        source_revision_digest="b" * 64,
        admission_digest="c" * 64,
        policy_snapshot_id="policy-snapshot-v7",
        policy_snapshot_digest="d" * 64,
        destination_id=f"dst_{'e' * 64}",
        destination_digest="f" * 64,
        source_access_grant_id=f"grant_{'1' * 64}",
        source_access_grant_digest="2" * 64,
    )
    manifest = KnowledgeIndexFileManifest.create(
        [
            {
                "relative_path": "agent/main.py",
                "sha256": "4" * 64,
                "size_bytes": 128,
            }
        ]
    )
    resources = KnowledgeIndexResourceBudget(
        max_files=10,
        max_total_bytes=1024,
        max_file_bytes=512,
        max_runtime_seconds=max_runtime_seconds,
        max_memory_bytes=128 * 1024 * 1024,
        max_output_bytes=4096,
    )
    assignment = KnowledgeIndexExecutionAssignment(
        assignment_id="assignment-1",
        worker_id="worker-index-01",
        lease_id="lease-1",
        lease_generation=1,
        lease_issued_epoch_ms=1,
        lease_expires_epoch_ms=9_999_999_999_999,
    )
    payload = KnowledgeIndexExecutionPayload(
        payload_artifact_ref=(
            KnowledgeIndexPayloadArtifactRef(
                artifact_id="artifact-payload-001",
                sha256="5" * 64,
                size_bytes=256,
                media_type=(
                    "application/vnd.ananta.knowledge-index-job+json"
                ),
                encoding="json",
            )
        )
    )
    return KnowledgeIndexExecutionJob.create(
        hub_task_id="hub-task-001",
        job_type="source_records",
        scope_id="source-alpha",
        source_scope="repository",
        profile_name="deep-code",
        created_by="owner-alice",
        created_at_epoch_ms=1,
        attempt=1,
        idempotency_key_digest="3" * 64,
        authority_binding=authority,
        file_manifest=manifest,
        resources=resources,
        assignment=assignment,
        payload=payload,
    )


def build_execution_task(
    *,
    max_runtime_seconds: int = 60,
    include_manifest: bool = False,
) -> dict[str, Any]:
    job = build_execution_job(
        max_runtime_seconds=max_runtime_seconds,
    )
    envelope = job.to_wire()
    if include_manifest:
        envelope["source_access_enforcement_manifest"] = {
            "schema": "test-enforcement-manifest"
        }
    return {
        "id": job.job_id,
        "task_kind": "codecompass_index_build",
        "worker_execution_context": {
            "knowledge_index_job": envelope,
        },
    }


__all__ = ["build_execution_job", "build_execution_task"]
