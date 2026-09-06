from io import BytesIO
from types import SimpleNamespace

import pytest

from agent.services.repository_registry import get_repository_registry


def test_artifact_upload_and_detail_flow(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "collection_name": "team-docs",
            "file": (BytesIO(b"# Hello\nartifact body"), "README.md"),
        },
        content_type="multipart/form-data",
    )

    assert upload_res.status_code == 201
    upload_payload = upload_res.get_json()["data"]
    artifact = upload_payload["artifact"]
    version = upload_payload["version"]
    collection = upload_payload["collection"]

    assert artifact["id"]
    assert artifact["latest_version_id"] == version["id"]
    assert artifact["latest_filename"] == "README.md"
    assert artifact["latest_sha256"]
    assert version["version_number"] == 1
    assert collection["name"] == "team-docs"

    list_res = client.get("/artifacts", headers=admin_auth_header)
    assert list_res.status_code == 200
    assert any(item["id"] == artifact["id"] for item in list_res.get_json()["data"])

    detail_res = client.get(f"/artifacts/{artifact['id']}", headers=admin_auth_header)
    assert detail_res.status_code == 200
    detail = detail_res.get_json()["data"]
    assert detail["artifact"]["id"] == artifact["id"]
    assert detail["versions"][0]["id"] == version["id"]
    assert detail["knowledge_links"][0]["artifact_id"] == artifact["id"]
    assert detail["knowledge_links"][0]["link_metadata"]["collection_name"] == "team-docs"


def test_public_artifact_content_route_still_serves_uploaded_bytes(
    client,
    admin_auth_header,
) -> None:
    content = b"public artifact content"
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"file": (BytesIO(content), "public.txt")},
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    response = client.get(
        f"/artifacts/{artifact_id}/content",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert response.get_data() == content
    assert "public.txt" in response.headers["Content-Disposition"]


@pytest.mark.parametrize(
    "system_artifact_kind",
    [
        "knowledge_index_job_payload",
        "knowledge_index_worker_output",
        "persona_media_image",
        "persona_media_preview",
    ],
)
def test_system_managed_artifacts_are_hidden_from_generic_routes(
    client,
    app,
    admin_auth_header,
    monkeypatch,
    system_artifact_kind,
) -> None:
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b"internal capability data"), "internal.bin")
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]
    with app.app_context():
        repository = get_repository_registry().artifact_repo
        artifact = repository.get_by_id(artifact_id)
        artifact.artifact_metadata = {
            "system_artifact_kind": system_artifact_kind
        }
        repository.save(artifact)

    class _ForbiddenService:
        def __getattr__(self, name):
            pytest.fail(f"system artifact reached generic service: {name}")

    monkeypatch.setattr(
        "agent.routes.artifacts.get_ingestion_service",
        lambda: _ForbiddenService(),
    )
    monkeypatch.setattr(
        "agent.routes.artifacts.get_rag_helper_index_service",
        lambda: _ForbiddenService(),
    )
    monkeypatch.setattr(
        "agent.routes.artifacts.get_knowledge_index_job_service",
        lambda: _ForbiddenService(),
    )

    listed = client.get("/artifacts", headers=admin_auth_header)
    assert listed.status_code == 200
    assert artifact_id not in {
        item["id"] for item in listed.get_json()["data"]
    }

    requests = [
        ("get", f"/artifacts/{artifact_id}", None),
        ("get", f"/artifacts/{artifact_id}/content", None),
        ("post", f"/artifacts/{artifact_id}/extract", None),
        ("post", f"/artifacts/{artifact_id}/rag-index", {}),
        ("get", f"/artifacts/{artifact_id}/rag-status", None),
        ("get", f"/artifacts/{artifact_id}/rag-preview", None),
        ("get", f"/artifacts/{artifact_id}/rag-jobs/job-1", None),
    ]
    for method, path, json_payload in requests:
        response = getattr(client, method)(
            path,
            headers=admin_auth_header,
            json=json_payload,
        )
        assert response.status_code == 404, path


def test_artifact_extract_structured_document_is_fully_indexed(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b'{"hello":"world"}'), "doc.json"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "fully-indexed"
    assert payload["document"]["extraction_mode"] == "fully-indexed"
    assert '"hello":"world"' in payload["document"]["text_content"]
    assert payload["document"]["document_metadata"]["json_root_type"] == "dict"
    assert payload["document"]["document_metadata"]["content_family"] == "structured_text"


def test_artifact_extract_plain_text_document_uses_text_extracted_mode(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b"plain text log line"), "notes.txt"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "text-extracted"
    assert payload["document"]["extraction_mode"] == "text-extracted"
    assert payload["document"]["text_content"] == "plain text log line"
    assert payload["document"]["document_metadata"]["content_family"] == "plain_text"


def test_artifact_extract_office_document_falls_back_to_metadata_only(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b"%PDF-1.4 placeholder"), "report.pdf"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "metadata-only"
    assert payload["document"]["extraction_mode"] == "metadata-only"
    assert payload["document"]["text_content"] is None
    assert payload["document"]["document_metadata"]["content_family"] == "office_document"


def test_artifact_extract_html_document_uses_text_extracted_mode(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b"<html><body><h1>Hello</h1><p>artifact body</p></body></html>"), "page.html"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "text-extracted"
    assert payload["document"]["extraction_mode"] == "text-extracted"
    assert "Hello artifact body" in payload["document"]["text_content"]
    assert payload["document"]["document_metadata"]["content_family"] == "html_document"


def test_artifact_extract_pdf_document_uses_text_when_extractor_succeeds(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr("agent.services.extraction_service.extraction_service._pdf_text", lambda path: ("PDF body text", "pdf_text_extracted"))
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b"%PDF-1.4 placeholder"), "report.pdf"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "text-extracted"
    assert payload["document"]["extraction_mode"] == "text-extracted"
    assert payload["document"]["text_content"] == "PDF body text"
    assert payload["document"]["document_metadata"]["reason"] == "pdf_text_extracted"


def test_artifact_extract_binary_document_falls_back_to_raw_only(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b"\x89PNG\r\n\x1a\nbinary"), "image.png"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "raw-only"
    assert payload["document"]["extraction_mode"] == "raw-only"
    assert payload["document"]["text_content"] is None
    assert payload["document"]["document_metadata"]["content_family"] == "binary_reference"


def test_artifact_upload_requires_file(client, admin_auth_header):
    response = client.post("/artifacts/upload", headers=admin_auth_header, data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["message"] == "file_required"


def test_artifact_upload_is_blocked_when_mutation_gate_denies(client, admin_auth_header, monkeypatch):
    class _BlockedDecision:
        def as_dict(self):
            return {
                "classification": "blocked",
                "reason_code": "mutation_gate_unknown_high_risk_classification",
                "mutation_class": "artifact_mutation",
                "normalized_target": {"target_type": "artifact"},
                "approval_scope": {},
            }

    class _BlockedGate:
        def evaluate(self, **kwargs):
            return _BlockedDecision()

    monkeypatch.setattr("agent.routes.artifacts.get_mutation_gate_service", lambda: _BlockedGate())

    response = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"file": (BytesIO(b"demo"), "demo.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 403
    assert response.get_json()["message"] == "mutation_gate_blocked"


def test_artifact_rag_index_route_returns_index_and_run(client, admin_auth_header, monkeypatch):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"file": (BytesIO(b"# Hello\nartifact body"), "README.md")},
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]
    captured: dict[str, object] = {}

    class StubRagService:
        def index_artifact(
            self,
            artifact_id: str,
            *,
            created_by: str | None,
            profile_name: str | None = None,
            profile_overrides: dict | None = None,
        ):
            captured["profile_name"] = profile_name
            captured["profile_overrides"] = profile_overrides
            return (
                SimpleNamespace(model_dump=lambda: {
                    "id": "idx-1",
                    "artifact_id": artifact_id,
                    "status": "completed",
                    "profile_name": profile_name or "default",
                }),
                SimpleNamespace(model_dump=lambda: {
                    "id": "run-1",
                    "artifact_id": artifact_id,
                    "status": "completed",
                }),
            )

        def get_artifact_status(self, artifact_id: str):
            return None, []

    monkeypatch.setattr("agent.routes.artifacts.get_rag_helper_index_service", lambda: StubRagService())

    response = client.post(
        f"/artifacts/{artifact_id}/rag-index",
        headers=admin_auth_header,
        json={"profile_name": "deep_code"},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["knowledge_index"]["artifact_id"] == artifact_id
    assert payload["knowledge_index"]["status"] == "completed"
    assert payload["run"]["status"] == "completed"
    assert captured["profile_name"] == "deep_code"


def test_artifact_rag_status_route_returns_runs(client, admin_auth_header, monkeypatch):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"file": (BytesIO(b"# Hello\nartifact body"), "README.md")},
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    class StubRagService:
        def index_artifact(self, artifact_id: str, *, created_by: str | None):
            raise AssertionError("not expected")

        def get_artifact_status(self, artifact_id: str):
            return (
                SimpleNamespace(model_dump=lambda: {
                    "id": "idx-1",
                    "artifact_id": artifact_id,
                    "status": "completed",
                }),
                [
                    SimpleNamespace(model_dump=lambda: {
                        "id": "run-1",
                        "artifact_id": artifact_id,
                        "status": "completed",
                    })
                ],
            )

    monkeypatch.setattr("agent.routes.artifacts.get_rag_helper_index_service", lambda: StubRagService())

    response = client.get(f"/artifacts/{artifact_id}/rag-status", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["knowledge_index"]["artifact_id"] == artifact_id
    assert payload["runs"][0]["status"] == "completed"


def test_artifact_rag_preview_route_returns_manifest_and_records(client, admin_auth_header, monkeypatch):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"file": (BytesIO(b"# Hello\nartifact body"), "README.md")},
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    class StubRagService:
        def get_artifact_preview(self, artifact_id: str, *, limit: int = 5):
            return {
                "knowledge_index": {"id": "idx-1", "artifact_id": artifact_id, "status": "completed"},
                "manifest": {"file_count": 1, "index_record_count": 2},
                "available_outputs": {"xml_overview": ["xml_overview.jsonl"]},
                "preview": {
                    "index": [{"kind": "md_file", "file": "README.md"}],
                    "details": [{"kind": "md_section", "heading": "Hello"}],
                    "relations": [{"type": "contains_section"}],
                    "xml_overview": [{"kind": "xml_overview", "file": "README.xml"}],
                },
            }

    monkeypatch.setattr("agent.routes.artifacts.get_rag_helper_index_service", lambda: StubRagService())

    response = client.get(f"/artifacts/{artifact_id}/rag-preview?limit=3", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["manifest"]["file_count"] == 1
    assert payload["preview"]["index"][0]["file"] == "README.md"
    assert payload["available_outputs"]["xml_overview"] == ["xml_overview.jsonl"]
    assert payload["preview"]["xml_overview"][0]["kind"] == "xml_overview"


def test_artifact_rag_index_route_supports_async_jobs(client, admin_auth_header, monkeypatch):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"file": (BytesIO(b"# Hello\nartifact body"), "README.md")},
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    class StubJobService:
        def submit_artifact_job(self, **kwargs):
            return {"job_id": "job-1", "scope_id": kwargs["artifact_id"], "status": "queued"}

        def get_job(self, job_id: str):
            return {"job_id": job_id, "scope_id": artifact_id, "status": "completed"}

    monkeypatch.setattr("agent.routes.artifacts.get_knowledge_index_job_service", lambda: StubJobService())

    response = client.post(
        f"/artifacts/{artifact_id}/rag-index",
        headers=admin_auth_header,
        json={"async": True, "profile_name": "default"},
    )

    assert response.status_code == 202
    assert response.get_json()["data"]["job"]["job_id"] == "job-1"

    status_res = client.get(f"/artifacts/{artifact_id}/rag-jobs/job-1", headers=admin_auth_header)
    assert status_res.status_code == 200
    assert status_res.get_json()["data"]["job"]["status"] == "completed"


def test_artifact_retrieval_preflight_route_returns_source_diagnostics(client, admin_auth_header, monkeypatch):
    class StubRetrievalService:
        def get_source_preflight(self):
            return {
                "status": "degraded",
                "source_policy": {"enabled": ["repo", "artifact"], "requested": [], "effective": ["repo", "artifact"]},
                "sources": {
                    "repo": {"enabled": True, "status": "ok", "issues": []},
                    "artifact": {"enabled": True, "status": "degraded", "issues": ["no_completed_indices"]},
                    "wiki": {"enabled": False, "status": "degraded", "issues": ["disabled"]},
                    "task_memory": {"enabled": False, "status": "ok", "issues": []},
                },
            }

    monkeypatch.setattr("agent.routes.artifacts.get_retrieval_service", lambda: StubRetrievalService())
    response = client.get("/artifacts/retrieval-preflight", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["status"] == "degraded"
    assert payload["sources"]["repo"]["status"] == "ok"
    assert payload["sources"]["artifact"]["issues"] == ["no_completed_indices"]


def test_artifact_orchestration_contract_route_exposes_hub_owned_states(client, admin_auth_header):
    response = client.get("/artifacts/orchestration-contract", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["version"] == "retrieval-orchestration-v1"
    assert payload["entrypoint_group"] == "artifacts"
    assert "job_state_transitions" in payload["ownership"]["hub_owned"]
    assert payload["state_machine"]["states"] == ["queued", "running", "completed", "failed"]
