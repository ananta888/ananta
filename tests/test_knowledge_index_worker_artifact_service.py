from __future__ import annotations

import hashlib

import pytest

from agent.services.knowledge_index_worker_artifact_service import (
    KnowledgeIndexWorkerArtifactService,
)


class Repository:
    def __init__(self):
        self.items = {}

    def get_by_id(self, item_id):
        return self.items.get(item_id)

    def save(self, item):
        self.items[item.id] = item
        return item


class Downloader:
    def __init__(self, content_by_artifact):
        self.content_by_artifact = dict(content_by_artifact)
        self.calls = []

    def download(self, *, worker_url, worker_token, reference):
        self.calls.append((worker_url, worker_token, dict(reference)))
        content = self.content_by_artifact[reference["artifact_id"]]
        assert hashlib.sha256(content).hexdigest() == reference["sha256"]
        return content


def reference(*, artifact_id: str, role: str, filename: str, content: bytes) -> dict:
    return {
        "artifact_id": artifact_id,
        "sha256": hashlib.sha256(content).hexdigest(),
        "media_type": "application/json" if role == "manifest" else "application/x-ndjson",
        "role": role,
        "filename": filename,
        "size_bytes": len(content),
        "knowledge_index_id": "idx-1",
        "run_id": "run-1",
    }


def test_hub_materializes_verified_worker_outputs_and_persists_local_paths(tmp_path) -> None:
    manifest = b'{"index_record_count": 1}'
    index = b'{"file": "agent/runtime.py"}\n'
    downloader = Downloader({"artifact-manifest": manifest, "artifact-index": index})
    index_repository = Repository()
    run_repository = Repository()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=index_repository,
        knowledge_index_run_repository=run_repository,
        output_root=tmp_path,
    )
    task = {
        "assigned_agent_url": "http://worker-a:5000",
        "assigned_agent_token": "worker-token",
        "worker_execution_context": {
            "knowledge_index_job": {
                "job_id": "knowledge-index-" + "a" * 32,
                "job_type": "source_records",
                "source_scope": "artifact",
            }
        },
    }
    result = {
        "status": "completed",
        "knowledge_index": {
            "id": "idx-1",
            "source_scope": "artifact",
            "status": "completed",
            "index_metadata": {"codecompass_snapshot_revision": "b" * 64},
        },
        "run": {
            "id": "run-1",
            "knowledge_index_id": "idx-1",
            "status": "completed",
            "run_metadata": {},
        },
        "results": None,
        "artifact_refs": [
            reference(
                artifact_id="artifact-manifest",
                role="manifest",
                filename="manifest.json",
                content=manifest,
            ),
            reference(
                artifact_id="artifact-index",
                role="index",
                filename="index.jsonl",
                content=index,
            ),
        ],
    }

    materialized = service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=result,
        task=task,
    )

    output_dir = tmp_path / "artifact" / "idx-1" / "run-1"
    assert (output_dir / "manifest.json").read_bytes() == manifest
    assert (output_dir / "index.jsonl").read_bytes() == index
    assert materialized["knowledge_index"]["output_dir"] == str(output_dir)
    assert index_repository.items["idx-1"].index_metadata["codecompass_snapshot_revision"] == "b" * 64
    assert run_repository.items["run-1"].manifest_path == str(output_dir / "manifest.json")
    assert len(downloader.calls) == 2


def test_hub_rejects_completed_result_without_manifest_and_index(tmp_path) -> None:
    service = KnowledgeIndexWorkerArtifactService(
        downloader=Downloader({}),
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )
    task = {
        "assigned_agent_url": "http://worker-a:5000",
        "assigned_agent_token": "worker-token",
        "worker_execution_context": {
            "knowledge_index_job": {
                "job_id": "knowledge-index-" + "a" * 32,
                "job_type": "artifact",
                "source_scope": None,
            }
        },
    }

    with pytest.raises(ValueError, match="artifacts_incomplete"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result={
                "status": "completed",
                "knowledge_index": {"id": "idx-1"},
                "run": {"id": "run-1", "knowledge_index_id": "idx-1"},
                "results": None,
                "artifact_refs": [],
            },
            task=task,
        )
