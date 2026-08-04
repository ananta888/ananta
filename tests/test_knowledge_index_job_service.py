from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from jsonschema import Draft202012Validator

from agent.services.knowledge_index_job_service import (
    KNOWLEDGE_INDEX_RESULT_SCHEMA,
    KnowledgeIndexJobService,
)
from worker.retrieval.knowledge_index_job_handler import (
    KnowledgeIndexWorkerTaskHandler,
    WorkerKnowledgeIndexArtifactPublisher,
    build_knowledge_index_task_handler,
)


class _Task:
    def __init__(self, payload):
        self.payload = dict(payload)

    def model_dump(self):
        return dict(self.payload)


class _Repository:
    def __init__(self):
        self.items = {}

    def get_by_id(self, task_id):
        return self.items.get(task_id)


class _Queue:
    def __init__(self, repository):
        self.repository = repository
        self.calls = []

    def ingest_task(self, **kwargs):
        self.calls.append(kwargs)
        fields = dict(kwargs.get("extra_fields") or {})
        self.repository.items[kwargs["task_id"]] = _Task(
            {
                "id": kwargs["task_id"],
                "status": kwargs["status"],
                "created_at": 10.0,
                "updated_at": 10.0,
                "verification_status": {},
                **fields,
            }
        )


class _OutputPublisher:
    def publish(self, *, job_id, knowledge_index, run):
        return [
            {
                "artifact_id": f"artifact-{role}",
                "sha256": "a" * 64,
                "media_type": media_type,
                "role": role,
                "filename": filename,
                "size_bytes": 10,
                "knowledge_index_id": knowledge_index["id"],
                "run_id": run["id"],
            }
            for role, filename, media_type in (
                ("manifest", "manifest.json", "application/json"),
                ("index", "index.jsonl", "application/x-ndjson"),
            )
        ]


def _service(*, payload_store=None):
    repository = _Repository()
    queue = _Queue(repository)
    return (
        KnowledgeIndexJobService(
            task_queue=queue,
            task_repository=repository,
            payload_store=payload_store,
            clock=lambda: 10.0,
        ),
        queue,
        repository,
    )


def test_knowledge_index_job_is_persistent_idempotent_and_never_uses_hub_executor() -> None:
    service, queue, _repository = _service()

    first = service.submit_source_records_job(
        source_scope="repo",
        source_id="ananta",
        records=[{"kind": "document", "content": "safe"}],
        created_by="admin",
        profile_name="deep_code",
        source_metadata={"snapshot_revision": "a" * 64},
    )
    second = service.submit_source_records_job(
        source_scope="repo",
        source_id="ananta",
        records=[{"kind": "document", "content": "safe"}],
        created_by="another-user",
        profile_name="deep_code",
        source_metadata={"snapshot_revision": "a" * 64},
    )

    assert first["job_id"] == second["job_id"]
    assert first["status"] == "queued"
    assert len(queue.calls) == 1
    assert not hasattr(service, "_executor")
    assert queue.calls[0]["extra_fields"]["task_kind"] == "codecompass_index_build"
    assert queue.calls[0]["extra_fields"]["required_capabilities"] == ["retrieval", "index_write"]


def test_knowledge_index_job_envelope_validates_against_worker_schema() -> None:
    service, queue, _repository = _service()
    service.submit_collection_job(
        collection_id="collection-a",
        artifact_ids=["b", "a", "a"],
        created_by="admin",
        profile_name=None,
        profile_overrides=None,
    )
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"]["knowledge_index_job"]
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "worker" / "knowledge_index_job.v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert envelope["artifact_ids"] == ["a", "b"]
    assert list(Draft202012Validator(schema).iter_errors(envelope)) == []


def test_worker_handler_returns_bound_result_without_orchestrating() -> None:
    service, queue, _repository = _service()
    job = service.submit_artifact_job(
        artifact_id="artifact-a",
        created_by="admin",
        profile_name="default",
        profile_overrides=None,
    )
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"]["knowledge_index_job"]

    class Execution:
        def execute(self, value):
            assert value["job_id"] == job["job_id"]
            return {
                "status": "completed",
                "knowledge_index": {"id": "idx-1"},
                "run": {"id": "run-1"},
                "artifact_refs": [{"artifact_id": "manifest-1", "sha256": "a" * 64, "media_type": "application/json"}],
            }

    result = KnowledgeIndexWorkerTaskHandler(Execution()).execute(envelope)
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "worker" / "knowledge_index_job_result.v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["schema"] == KNOWLEDGE_INDEX_RESULT_SCHEMA
    assert result["status"] == "completed"
    assert list(Draft202012Validator(schema).iter_errors(result)) == []


def test_worker_handler_composition_executes_task_context_through_rag_helper_port() -> None:
    service, queue, _repository = _service()
    job = service.submit_source_records_job(
        source_scope="wiki",
        source_id="wiki-mvp",
        records=[{"kind": "document", "content": "safe"}],
        created_by="admin",
        profile_name="deep_code",
    )
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"]["knowledge_index_job"]
    captured = {}

    class Model:
        def __init__(self, **payload):
            self.payload = payload

        def model_dump(self):
            return dict(self.payload)

    class IndexService:
        def index_source_records(self, **kwargs):
            captured.update(kwargs)
            return (
                Model(id="idx-wiki", status="completed"),
                Model(id="run-wiki", status="completed", manifest_path=None),
            )

    handler = build_knowledge_index_task_handler(
        IndexService(),
        artifact_publisher=_OutputPublisher(),
    )
    task = {"worker_execution_context": {"knowledge_index_job": envelope}}

    proposal = handler.propose(task=task)
    result = handler.execute(task=task)

    assert proposal["tool_calls"][0]["name"] == "codecompass_index_build"
    assert proposal["tool_calls"][0]["arguments"]["job_id"] == job["job_id"]
    assert captured["source_scope"] == "wiki"
    assert captured["source_id"] == "wiki-mvp"
    assert captured["records"][0]["content"] == "safe"
    assert result["status"] == "completed"
    assert result["knowledge_index"]["id"] == "idx-wiki"


def test_large_job_payload_is_artifact_first_and_worker_verifies_it() -> None:
    class PayloadStore:
        content = b""

        def store_payload(self, *, content, fingerprint, created_by):
            self.content = content
            return {
                "artifact_id": f"payload-{fingerprint[:12]}",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "media_type": "application/vnd.ananta.knowledge-index-job+json",
            }

    store = PayloadStore()
    service, queue, _repository = _service(payload_store=store)
    service.submit_source_records_job(
        source_scope="wiki",
        source_id="wiki-large",
        records=[{"kind": "document", "content": "x" * 150_000}],
        created_by="admin",
        profile_name="deep_code",
    )
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"]["knowledge_index_job"]
    reference = envelope["payload"]["payload_artifact_ref"]
    captured = {}

    class PayloadLoader:
        def load(self, value):
            assert value == reference
            return store.content

    class Model:
        def __init__(self, **payload):
            self.payload = payload

        def model_dump(self):
            return dict(self.payload)

    class IndexService:
        def index_source_records(self, **kwargs):
            captured.update(kwargs)
            return Model(id="idx-large", status="completed"), Model(
                id="run-large",
                status="completed",
                manifest_path=None,
            )

    result = build_knowledge_index_task_handler(
        IndexService(),
        payload_loader=PayloadLoader(),
        artifact_publisher=_OutputPublisher(),
    ).execute(envelope)

    assert set(envelope["payload"]) == {"payload_artifact_ref"}
    assert reference["size_bytes"] == len(store.content)
    assert captured["records"][0]["content"] == "x" * 150_000
    assert result["status"] == "completed"


def test_accept_worker_result_persists_terminal_status(monkeypatch) -> None:
    service, queue, repository = _service()
    job = service.submit_artifact_job(
        artifact_id="artifact-a",
        created_by="admin",
        profile_name="default",
        profile_overrides=None,
    )
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"]["knowledge_index_job"]

    def update(task_id, status, **fields):
        task = repository.items[task_id]
        task.payload.update(fields)
        task.payload["status"] = status

    monkeypatch.setattr("agent.services.task_runtime_service.update_local_task_status", update)
    completed = service.accept_worker_result(
        job_id=job["job_id"],
        result={
            "schema": KNOWLEDGE_INDEX_RESULT_SCHEMA,
            "job_id": job["job_id"],
            "idempotency_fingerprint": envelope["idempotency_fingerprint"],
            "status": "completed",
            "reason_code": None,
            "knowledge_index": {"id": "idx-1"},
            "run": {"id": "run-1"},
            "results": None,
            "artifact_refs": [],
            "error": None,
        },
    )

    assert completed["status"] == "completed"
    assert completed["knowledge_index"] == {"id": "idx-1"}


def test_worker_result_validation_rejects_unbound_or_extended_payloads() -> None:
    service, queue, _repository = _service()
    job = service.submit_artifact_job(
        artifact_id="artifact-a",
        created_by="admin",
        profile_name="default",
        profile_overrides=None,
    )
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"]["knowledge_index_job"]
    result = {
        "schema": KNOWLEDGE_INDEX_RESULT_SCHEMA,
        "job_id": job["job_id"],
        "idempotency_fingerprint": envelope["idempotency_fingerprint"],
        "status": "completed",
        "reason_code": None,
        "knowledge_index": {"id": "idx-1"},
        "run": {"id": "run-1"},
        "results": None,
        "artifact_refs": [],
        "error": None,
    }

    with pytest.raises(ValueError, match="fingerprint_mismatch"):
        service.validate_worker_result(
            job_id=job["job_id"],
            result={**result, "idempotency_fingerprint": "f" * 64},
        )
    with pytest.raises(ValueError, match="fields_unknown"):
        service.validate_worker_result(
            job_id=job["job_id"],
            result={**result, "unexpected": True},
        )

    oversized_graph_reference = {
        "artifact_id": "graph-1",
        "sha256": "a" * 64,
        "media_type": "application/vnd.ananta.codecompass-graph-index+json",
        "role": "graph_index",
        "filename": "cc_graph_index.json",
        "size_bytes": 32 * 1024 * 1024 + 1,
        "knowledge_index_id": "idx-1",
        "run_id": "run-1",
        "artifact_schema": "codecompass_graph_index.v1",
        "graph_revision": "sha256:" + "b" * 64,
        "graph_content_hash": "sha256:" + "c" * 64,
    }
    oversized_result = {
        **result,
        "artifact_refs": [oversized_graph_reference],
    }
    with pytest.raises(ValueError, match="graph_artifact_ref_size_invalid"):
        service.validate_worker_result(
            job_id=job["job_id"],
            result=oversized_result,
        )

    result_schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "worker"
            / "knowledge_index_job_result.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert list(
        Draft202012Validator(result_schema).iter_errors(oversized_result)
    )


def test_internal_payload_route_serves_only_integrity_checked_system_artifact(
    monkeypatch,
    tmp_path,
) -> None:
    from agent.routes.artifacts import get_knowledge_index_payload_artifact

    content = b'{"records": []}'
    payload_path = tmp_path / "knowledge-index-payload.json"
    payload_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    artifact = SimpleNamespace(
        latest_version_id="version-1",
        artifact_metadata={"system_artifact_kind": "knowledge_index_job_payload"},
    )
    version = SimpleNamespace(
        storage_path=str(payload_path),
        original_filename=payload_path.name,
        media_type="application/vnd.ananta.knowledge-index-job+json",
        size_bytes=len(content),
        sha256=digest,
    )
    monkeypatch.setattr(
        "agent.routes.artifacts._artifact_repo",
        lambda: SimpleNamespace(get_by_id=lambda _artifact_id: artifact),
    )
    monkeypatch.setattr(
        "agent.routes.artifacts._artifact_version_repo",
        lambda: SimpleNamespace(get_by_id=lambda _version_id: version),
    )
    app = Flask(__name__)

    with app.test_request_context():
        response = get_knowledge_index_payload_artifact.__wrapped__("artifact-1")
        response.direct_passthrough = False

    assert response.get_data() == content
    assert response.headers["X-Artifact-SHA256"] == digest
    assert response.headers["X-Artifact-Size"] == str(len(content))


def test_worker_output_publisher_creates_real_artifact_references(monkeypatch, tmp_path) -> None:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "index.jsonl").write_text('{"id": "row-1"}\n', encoding="utf-8")
    saved = []

    class Ingestion:
        def upload_artifact(self, *, filename, content, created_by, media_type):
            artifact = SimpleNamespace(
                id=f"artifact-{len(saved) + 1}",
                artifact_metadata={},
            )
            version = SimpleNamespace(
                sha256=hashlib.sha256(content).hexdigest(),
                media_type=media_type,
                size_bytes=len(content),
            )
            return artifact, version, None

    monkeypatch.setattr(
        "agent.services.ingestion_service.get_ingestion_service",
        lambda: Ingestion(),
    )
    monkeypatch.setattr(
        "agent.repository.artifact_repo",
        SimpleNamespace(save=lambda artifact: saved.append(artifact) or artifact),
    )

    references = WorkerKnowledgeIndexArtifactPublisher().publish(
        job_id="knowledge-index-" + "a" * 32,
        knowledge_index={"id": "idx-1", "output_dir": str(tmp_path)},
        run={"id": "run-1", "output_dir": str(tmp_path)},
    )

    assert {reference["role"] for reference in references} == {"manifest", "index"}
    assert all(reference["artifact_id"].startswith("artifact-") for reference in references)
    assert all(len(reference["sha256"]) == 64 for reference in references)
    assert {artifact.artifact_metadata["output_role"] for artifact in saved} == {
        "manifest",
        "index",
    }


def test_worker_publisher_preflights_graph_size_before_parsing(
    monkeypatch,
    tmp_path,
) -> None:
    graph_path = tmp_path / "cc_graph_index.json"
    metrics_path = tmp_path / "cc_graph_index.visual_metrics.json"
    graph_path.write_bytes(b"x" * 33)
    metrics_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        WorkerKnowledgeIndexArtifactPublisher,
        "_MAX_GRAPH_OUTPUT_BYTES",
        32,
    )

    with pytest.raises(RuntimeError, match="graph_artifact_too_large"):
        WorkerKnowledgeIndexArtifactPublisher().publish(
            job_id="knowledge-index-" + "a" * 32,
            knowledge_index={"id": "idx-1", "output_dir": str(tmp_path)},
            run={"id": "run-1", "output_dir": str(tmp_path)},
        )
