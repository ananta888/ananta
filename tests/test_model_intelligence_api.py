from __future__ import annotations

import hashlib
import json

from agent.services.model_analysis_job_service import (
    InMemoryModelAnalysisJobRepository,
    ModelAnalysisJobRecord,
    ModelAnalysisJobService,
    ModelAnalysisJobState,
)
from agent.services.model_analysis_task_port import ModelAnalysisTaskReference
from agent.services.model_intelligence_artifact_store import (
    FileSystemModelIntelligenceArtifactStore,
)
from ananta_contracts.model_intelligence import AnalysisJob, ArtifactRef
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    CompletionOutcome,
)

MODEL_ID = f"model_{'a' * 64}"
CREATE_BODY = {
    "schema": "ananta.model-intelligence.create-job.v1",
    "hub_task_id": "task-001",
    "model_id": MODEL_ID,
    "analysis_kind": "static.tensor-statistics",
    "profile_id": "profile.static-safe.v1",
    "request_sha256": "b" * 64,
    "requested_artifact_kinds": ["tensor.statistics"],
    "max_runtime_seconds": 60,
    "max_output_bytes": 4096,
}


class _Tasks:
    def __init__(self) -> None:
        self.submissions = 0

    def submit(self, job):
        self.submissions += 1
        return ModelAnalysisTaskReference(
            job.hub_task_id,
            f"execution-{job.job_id}",
            "assigned",
        )

    def mark_running(self, job, *, worker_id):
        pass

    def mark_cancel_requested(self, job, *, reason_code):
        pass

    def finish(self, job, *, status, reason_code):
        pass


def _install(app):
    tasks = _Tasks()
    service = ModelAnalysisJobService(
        repository=InMemoryModelAnalysisJobRepository(),
        tasks=tasks,
        epoch_ms=lambda: 1000,
    )
    app.extensions["model_analysis_job_service"] = service
    return service, tasks


def test_capabilities_require_auth_and_model_intelligence_rbac(
    client,
    admin_auth_header,
    user_auth_header,
) -> None:
    assert client.get(
        "/api/model-intelligence/capabilities"
    ).status_code == 401
    denied = client.get(
        "/api/model-intelligence/capabilities",
        headers=user_auth_header,
    )
    assert denied.status_code == 403
    allowed = client.get(
        "/api/model-intelligence/capabilities",
        headers=admin_auth_header,
    )
    assert allowed.status_code == 200
    assert allowed.json["data"]["limits"]["max_job_page_size"] == 100


def test_create_is_idempotent_and_tenant_is_never_client_controlled(
    client,
    app,
    admin_auth_header,
) -> None:
    _service, tasks = _install(app)
    headers = {
        **admin_auth_header,
        "Idempotency-Key": "model-analysis-request-001",
    }
    first = client.post(
        "/api/model-intelligence/jobs",
        headers=headers,
        json=CREATE_BODY,
    )
    replay = client.post(
        "/api/model-intelligence/jobs",
        headers=headers,
        json=CREATE_BODY,
    )
    forged = client.post(
        "/api/model-intelligence/jobs",
        headers={
            **admin_auth_header,
            "Idempotency-Key": "model-analysis-request-002",
        },
        json={**CREATE_BODY, "tenant_id": "other-tenant"},
    )

    assert first.status_code == replay.status_code == 202
    assert first.json["data"]["job"] == replay.json["data"]["job"]
    assert tasks.submissions == 1
    assert forged.status_code == 400


def test_list_cursor_etag_and_cancel_precondition(
    client,
    app,
    admin_auth_header,
) -> None:
    _install(app)
    for number in range(3):
        response = client.post(
            "/api/model-intelligence/jobs",
            headers={
                **admin_auth_header,
                "Idempotency-Key": f"model-analysis-request-{number:03d}",
            },
            json={
                **CREATE_BODY,
                "hub_task_id": f"task-{number:03d}",
                "request_sha256": f"{number + 1:064x}",
            },
        )
        assert response.status_code == 202

    first_page = client.get(
        "/api/model-intelligence/jobs?page_size=2",
        headers=admin_auth_header,
    )
    cursor = first_page.json["data"]["next_cursor"]
    second_page = client.get(
        f"/api/model-intelligence/jobs?page_size=2&cursor={cursor}",
        headers=admin_auth_header,
    )
    job_id = first_page.json["data"]["jobs"][0]["job"]["job_id"]
    loaded = client.get(
        f"/api/model-intelligence/jobs/{job_id}",
        headers=admin_auth_header,
    )
    not_modified = client.get(
        f"/api/model-intelligence/jobs/{job_id}",
        headers={
            **admin_auth_header,
            "If-None-Match": loaded.headers["ETag"],
        },
    )
    stale = client.post(
        f"/api/model-intelligence/jobs/{job_id}/cancel",
        headers={
            **admin_auth_header,
            "Idempotency-Key": "cancel-request-001",
            "If-Match": 'W/"wrong:1"',
        },
        json={},
    )
    cancelled = client.post(
        f"/api/model-intelligence/jobs/{job_id}/cancel",
        headers={
            **admin_auth_header,
            "Idempotency-Key": "cancel-request-001",
            "If-Match": loaded.headers["ETag"],
        },
        json={},
    )

    assert len(first_page.json["data"]["jobs"]) == 2
    assert len(second_page.json["data"]["jobs"]) == 1
    assert not_modified.status_code == 304
    assert stale.status_code == 412
    assert cancelled.status_code == 200
    assert cancelled.json["data"]["job"]["status"] == "cancelled"


def test_tenant_scope_is_applied_to_get(
    client,
    app,
    admin_auth_header,
    monkeypatch,
) -> None:
    _install(app)
    created = client.post(
        "/api/model-intelligence/jobs",
        headers={
            **admin_auth_header,
            "Idempotency-Key": "model-analysis-request-001",
        },
        json=CREATE_BODY,
    )
    job_id = created.json["data"]["job"]["job"]["job_id"]
    monkeypatch.setattr(
        "agent.routes.model_intelligence._tenant_id",
        lambda: "different-tenant",
    )

    hidden = client.get(
        f"/api/model-intelligence/jobs/{job_id}",
        headers=admin_auth_header,
    )

    assert hidden.status_code == 404
    assert hidden.json["error"]["reason_code"] == (
        "model_analysis_job_not_found"
    )


def test_artifact_report_and_bounded_graph_use_hub_stores(
    client,
    app,
    admin_auth_header,
    tmp_path,
    monkeypatch,
) -> None:
    tenant_id = "tenant-fixture"
    store = FileSystemModelIntelligenceArtifactStore(root=tmp_path)
    graph_payload = {
        "schema_version": "model_graph.v1",
        "model_id": MODEL_ID,
        "nodes": [
            {
                "node_id": "node-1",
                "kind": "model",
                "label": "fixture",
                "attributes": {},
            }
        ],
        "edges": [],
    }
    graph_content = json.dumps(
        graph_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    report_content = b'{"schema":"ananta.model-intelligence-report.v1"}\n'
    graph_stored = store.put_bytes(
        tenant_id,
        graph_content,
        media_type="application/json",
        artifact_kind="model.graph",
    )
    report_stored = store.put_bytes(
        tenant_id,
        report_content,
        media_type="application/json",
        artifact_kind="report.json",
    )

    def canonical(reference, artifact_id, kind):
        return ArtifactRef(
            artifact_id=artifact_id,
            job_id="job-001",
            kind=kind,
            sha256=reference.digest.removeprefix("sha256:"),
            size_bytes=reference.size_bytes,
            media_type=reference.media_type,
        )

    artifacts = (
        canonical(graph_stored, "artifact-graph", "model.graph"),
        canonical(report_stored, "artifact-report", "report.json"),
    )
    job = AnalysisJob(
        job_id="job-001",
        hub_task_id="task-001",
        tenant_id=tenant_id,
        model_id=MODEL_ID,
        analysis_kind="static.tensor-statistics",
        profile_id="profile.static-safe.v1",
        request_sha256="b" * 64,
        requested_artifact_kinds=("model.graph", "report.json"),
        max_runtime_seconds=60,
        max_output_bytes=4096,
    )
    completion = AnalysisCompletion(
        job_id=job.job_id,
        lease_id="lease-001",
        lease_generation=1,
        completion_key=f"completion_{'c' * 64}",
        outcome=CompletionOutcome.SUCCEEDED,
        artifacts=artifacts,
    )
    record = ModelAnalysisJobRecord(
        job=job,
        state=ModelAnalysisJobState.SUCCEEDED,
        version=3,
        attempt=1,
        lease=None,
        completion=completion,
        reason_code="model_analysis_succeeded",
        projection_pending=False,
        updated_epoch_ms=1000,
    )

    class _ReadService:
        def get(self, *, tenant_id, job_id):
            if tenant_id != tenant_id_fixture or job_id != "job-001":
                raise AssertionError("tenant/job binding lost")
            return record

    tenant_id_fixture = tenant_id
    app.extensions["model_analysis_job_service"] = _ReadService()
    app.extensions["model_intelligence_artifact_store"] = store
    monkeypatch.setattr(
        "agent.routes.model_intelligence._tenant_id",
        lambda: tenant_id,
    )

    metadata = client.get(
        "/api/model-intelligence/jobs/job-001/artifacts/artifact-graph",
        headers=admin_auth_header,
    )
    report = client.get(
        "/api/model-intelligence/jobs/job-001/report",
        headers=admin_auth_header,
    )
    graph = client.get(
        "/api/model-intelligence/jobs/job-001/graph"
        "?start_node_id=node-1&page_size=1&max_nodes=1",
        headers=admin_auth_header,
    )

    assert metadata.status_code == 200
    assert report.status_code == 200
    assert report.data == report_content
    assert graph.status_code == 200
    assert len(graph.json["data"]["graph"]["nodes"]) == 1
