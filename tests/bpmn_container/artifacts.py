"""Deterministic real artifact fixture and cross-container admission assertion."""

from __future__ import annotations

import hashlib
from pathlib import Path

CONTENT = b'{"classification":"synthetic_test","answer":42}\n'


def prepare_artifact_store():
    """Bootstrap before Worker readiness, outside the bounded execution request."""
    from sqlmodel import SQLModel

    import agent.db_models  # noqa: F401 - worker-local production schema
    from agent.database import engine
    from agent.services.artifact_store import ArtifactStore
    from agent.services.ingestion_service import IngestionService

    SQLModel.metadata.create_all(engine)
    return IngestionService(artifact_store=ArtifactStore("/tmp/worker-artifacts"))


def produce_artifact(store, command, hub_task_id):
    """Worker-local production storage; no claim of a Hub receipt or evidence ID."""
    artifact, version, _collection = store.upload_artifact(
        filename="synthetic-result.json",
        content=CONTENT,
        media_type="application/json",
        created_by="bpmn-acceptance-worker",
        artifact_metadata={
            "evidence_classification": "synthetic_test_technical_observation",
            "synthetic": True,
            "task_id": hub_task_id,
            "attempt_id": command.attempt_id,
            "fencing_token": command.fencing_token,
        },
    )
    assert Path(version.storage_path).read_bytes() == CONTENT
    assert version.sha256 == hashlib.sha256(CONTENT).hexdigest()
    return {"report": "artifact://" + artifact.id}, {
        "worker_artifact_id": artifact.id,
        "worker_artifact_version_id": version.id,
        "sha256": version.sha256,
        "size_bytes": version.size_bytes,
        "worker_bytes_verified": True,
    }


def artifact_case(hub):
    """Negative acceptance only: missing transfer support must deny all work."""
    from bpmn_container.acceptance import compile_request
    from bpmn_container.fixtures import artifact

    from agent.services.workflow_runtime.errors import ContractValidationError

    before = {task.id for task in hub.tasks.get_all()}
    try:
        request = compile_request(artifact(), "artifact-ingress")
        hub.orchestrator.start(request)
    except ContractValidationError as exc:
        assert "bpmn_artifact_ingress_unavailable" in str(exc), str(exc)
    else:
        raise AssertionError("unadmitted_worker_artifacts_must_not_start")
    assert {task.id for task in hub.tasks.get_all()} == before
    return {
        "status": "denied_before_execution",
        "reason_code": "bpmn_artifact_ingress_unavailable",
        "positive_artifact_transfer_verified": False,
        "tasks_created": 0,
    }
