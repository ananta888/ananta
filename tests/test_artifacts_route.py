from io import BytesIO
from types import SimpleNamespace


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
