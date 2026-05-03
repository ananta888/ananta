from io import BytesIO
from types import SimpleNamespace


def test_knowledge_collection_create_list_and_detail(client, admin_auth_header):
    create_res = client.post(
        "/knowledge/collections",
        headers=admin_auth_header,
        json={"name": "team-docs", "description": "shared docs"},
    )

    assert create_res.status_code == 201
    collection = create_res.get_json()["data"]
    assert collection["name"] == "team-docs"

    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "collection_name": "team-docs",
            "file": (BytesIO(b"# Hello\nartifact body"), "README.md"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    list_res = client.get("/knowledge/collections", headers=admin_auth_header)
    assert list_res.status_code == 200
    assert any(item["id"] == collection["id"] for item in list_res.get_json()["data"])

    detail_res = client.get(f"/knowledge/collections/{collection['id']}", headers=admin_auth_header)
    assert detail_res.status_code == 200
    payload = detail_res.get_json()["data"]
    assert payload["collection"]["name"] == "team-docs"
    assert payload["knowledge_links"][0]["artifact_id"] == artifact_id


def test_knowledge_collection_index_route_indexes_linked_artifacts(client, admin_auth_header, monkeypatch):
    create_res = client.post(
        "/knowledge/collections",
        headers=admin_auth_header,
        json={"name": "team-docs"},
    )
    collection_id = create_res.get_json()["data"]["id"]

    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "collection_name": "team-docs",
            "file": (BytesIO(b"# Hello\nartifact body"), "README.md"),
        },
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
            return (
                SimpleNamespace(model_dump=lambda: {"id": "idx-1", "artifact_id": artifact_id, "status": "completed"}),
                SimpleNamespace(model_dump=lambda: {"id": "run-1", "artifact_id": artifact_id, "status": "completed"}),
            )

    monkeypatch.setattr("agent.routes.knowledge.get_rag_helper_index_service", lambda: StubRagService())

    response = client.post(
        f"/knowledge/collections/{collection_id}/index",
        headers=admin_auth_header,
        json={"profile_name": "fast_docs"},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["results"][0]["artifact_id"] == artifact_id
    assert payload["results"][0]["run"]["status"] == "completed"
    assert captured["profile_name"] == "fast_docs"


def test_knowledge_collection_search_route_returns_collection_chunks(client, admin_auth_header, monkeypatch):
    create_res = client.post(
        "/knowledge/collections",
        headers=admin_auth_header,
        json={"name": "team-docs"},
    )
    collection_id = create_res.get_json()["data"]["id"]

    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "collection_name": "team-docs",
            "file": (BytesIO(b"# Hello\nartifact body"), "README.md"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    class StubKnowledgeRetrieval:
        def search(self, query: str, *, top_k: int = 4, artifact_ids=None, source_scopes=None):
            assert query == "timeout"
            assert artifact_ids == {artifact_id}
            assert source_scopes is None
            return [
                SimpleNamespace(
                    engine="knowledge_index",
                    source="README.md",
                    content="timeout handling",
                    score=2.5,
                    metadata={"artifact_id": artifact_id},
                )
            ]

    monkeypatch.setattr("agent.routes.knowledge.get_knowledge_index_retrieval_service", lambda: StubKnowledgeRetrieval())

    response = client.post(
        f"/knowledge/collections/{collection_id}/search",
        headers=admin_auth_header,
        json={"query": "timeout", "top_k": 3},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["collection"]["id"] == collection_id
    assert payload["source_policy"]["effective_scopes"] == ["artifact"]
    assert payload["chunks"][0]["engine"] == "knowledge_index"
    assert payload["chunks"][0]["metadata"]["artifact_id"] == artifact_id


def test_knowledge_index_profiles_route_returns_catalog(client, admin_auth_header, monkeypatch):
    class StubRagService:
        def list_profiles(self):
            return [{"name": "default", "label": "Default", "is_default": True}]

    monkeypatch.setattr("agent.routes.knowledge.get_rag_helper_index_service", lambda: StubRagService())

    response = client.get("/knowledge/index-profiles", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["items"][0]["name"] == "default"


def test_knowledge_wiki_presets_route_returns_multiple_download_sources(client, admin_auth_header):
    response = client.get("/knowledge/wiki/presets", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    items = payload["items"]
    assert len(items) >= 4
    de_multistream = next(item for item in items if item["id"] == "wikipedia-de-multistream-latest")
    assert de_multistream["corpus_url"].endswith("dewiki-latest-pages-articles-multistream.xml.bz2")
    assert de_multistream["index_url"].endswith("dewiki-latest-pages-articles-multistream-index.txt.bz2")
    assert any(item["id"] == "wikipedia-de-pages-latest" for item in items)
    zim_mini = next(item for item in items if item["id"] == "wikipedia-de-zim-mini-2026-04")
    assert zim_mini["supported"] is False


def test_knowledge_collection_index_route_supports_async_jobs(client, admin_auth_header, monkeypatch):
    create_res = client.post(
        "/knowledge/collections",
        headers=admin_auth_header,
        json={"name": "team-docs"},
    )
    collection_id = create_res.get_json()["data"]["id"]

    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"collection_name": "team-docs", "file": (BytesIO(b"# Hello\nartifact body"), "README.md")},
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    class StubJobService:
        def submit_collection_job(self, **kwargs):
            assert kwargs["artifact_ids"] == [artifact_id]
            return {"job_id": "job-collection-1", "scope_id": kwargs["collection_id"], "status": "queued"}

        def get_job(self, job_id: str):
            return {"job_id": job_id, "scope_id": collection_id, "status": "completed"}

    monkeypatch.setattr("agent.routes.knowledge.get_knowledge_index_job_service", lambda: StubJobService())

    response = client.post(
        f"/knowledge/collections/{collection_id}/index",
        headers=admin_auth_header,
        json={"async": True, "profile_name": "default"},
    )

    assert response.status_code == 202
    assert response.get_json()["data"]["job"]["job_id"] == "job-collection-1"

    status_res = client.get("/knowledge/index-jobs/job-collection-1", headers=admin_auth_header)
    assert status_res.status_code == 200
    assert status_res.get_json()["data"]["job"]["status"] == "completed"


def test_knowledge_collection_search_route_accepts_source_type_policy(client, admin_auth_header, monkeypatch):
    create_res = client.post(
        "/knowledge/collections",
        headers=admin_auth_header,
        json={"name": "team-docs"},
    )
    collection_id = create_res.get_json()["data"]["id"]

    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={"collection_name": "team-docs", "file": (BytesIO(b"# Hello\nartifact body"), "README.md")},
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    class StubKnowledgeRetrieval:
        def search(self, query: str, *, top_k: int = 4, artifact_ids=None, source_scopes=None):
            assert query == "timeout"
            assert top_k == 3
            assert artifact_ids == {artifact_id}
            assert source_scopes == {"artifact"}
            return []

    monkeypatch.setattr("agent.routes.knowledge.get_knowledge_index_retrieval_service", lambda: StubKnowledgeRetrieval())

    response = client.post(
        f"/knowledge/collections/{collection_id}/search",
        headers=admin_auth_header,
        json={"query": "timeout", "top_k": 3, "source_types": ["artifact"]},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["source_policy"]["requested"] == ["artifact"]
    assert payload["source_policy"]["effective_scopes"] == ["artifact"]


def test_knowledge_collection_search_route_rejects_invalid_source_type(client, admin_auth_header):
    create_res = client.post(
        "/knowledge/collections",
        headers=admin_auth_header,
        json={"name": "team-docs"},
    )
    collection_id = create_res.get_json()["data"]["id"]
    response = client.post(
        f"/knowledge/collections/{collection_id}/search",
        headers=admin_auth_header,
        json={"query": "timeout", "source_types": ["repo"]},
    )
    assert response.status_code == 400
    assert response.get_json()["message"] == "invalid_source_types"


def test_knowledge_retrieval_preflight_route_returns_source_diagnostics(client, admin_auth_header, monkeypatch):
    class StubRetrievalService:
        def get_source_preflight(self):
            return {
                "status": "ok",
                "source_policy": {"enabled": ["repo", "artifact", "task_memory"], "requested": [], "effective": ["repo", "artifact"]},
                "sources": {
                    "repo": {"enabled": True, "status": "ok", "issues": []},
                    "artifact": {"enabled": True, "status": "ok", "issues": []},
                    "wiki": {"enabled": False, "status": "degraded", "issues": ["no_completed_indices"]},
                    "task_memory": {"enabled": True, "status": "ok", "issues": []},
                },
            }

    monkeypatch.setattr("agent.routes.knowledge.get_retrieval_service", lambda: StubRetrievalService())
    response = client.get("/knowledge/retrieval-preflight", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["status"] == "ok"
    assert payload["sources"]["artifact"]["status"] == "ok"


def test_knowledge_source_records_index_route_indexes_wiki_records(client, admin_auth_header, monkeypatch):
    class StubRagService:
        def index_source_records(
            self,
            *,
            source_scope: str,
            source_id: str,
            records: list[dict],
            created_by: str | None,
            profile_name: str | None = None,
            source_metadata: dict | None = None,
        ):
            assert source_scope == "wiki"
            assert source_id == "wiki-mvp"
            assert records and records[0]["article_title"] == "Payment retries"
            return (
                SimpleNamespace(model_dump=lambda: {"id": "idx-wiki-1", "source_scope": "wiki", "status": "completed"}),
                SimpleNamespace(model_dump=lambda: {"id": "run-wiki-1", "status": "completed"}),
            )

    monkeypatch.setattr("agent.routes.knowledge.get_rag_helper_index_service", lambda: StubRagService())
    response = client.post(
        "/knowledge/sources/index-records",
        headers=admin_auth_header,
        json={
            "source_scope": "wiki",
            "source_id": "wiki-mvp",
            "records": [
                {
                    "kind": "wiki_section",
                    "article_title": "Payment retries",
                    "section_title": "Timeout",
                    "file": "wiki/payment.md",
                    "content": "Workers retry payment after timeout.",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["knowledge_index"]["source_scope"] == "wiki"
    assert payload["run"]["status"] == "completed"


def test_knowledge_source_records_index_route_supports_async_jobs(client, admin_auth_header, monkeypatch):
    class StubJobService:
        def submit_source_records_job(self, **kwargs):
            assert kwargs["source_scope"] == "wiki"
            assert kwargs["source_id"] == "wiki-mvp"
            return {"job_id": "job-source-1", "status": "queued", "source_scope": kwargs["source_scope"]}

    monkeypatch.setattr("agent.routes.knowledge.get_knowledge_index_job_service", lambda: StubJobService())
    response = client.post(
        "/knowledge/sources/index-records",
        headers=admin_auth_header,
        json={
            "source_scope": "wiki",
            "source_id": "wiki-mvp",
            "records": [{"kind": "wiki_section", "file": "wiki/payment.md", "content": "x"}],
            "async": True,
        },
    )

    assert response.status_code == 202
    assert response.get_json()["data"]["job"]["job_id"] == "job-source-1"


def test_knowledge_orchestration_contract_route_exposes_hub_owned_states(client, admin_auth_header):
    response = client.get("/knowledge/orchestration-contract", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["version"] == "retrieval-orchestration-v1"
    assert payload["entrypoint_group"] == "knowledge"
    assert "retry_triggering" in payload["ownership"]["hub_owned"]
    assert payload["state_machine"]["states"] == ["queued", "running", "completed", "failed"]


def test_knowledge_wiki_import_route_indexes_normalized_records(client, admin_auth_header, monkeypatch):
    class StubIngestionService:
        def import_wiki_jsonl(self, *, corpus_path: str, source_id: str | None, default_language: str, strict: bool):
            assert corpus_path == "/tmp/wiki.jsonl"
            assert source_id == "wiki-mvp"
            assert default_language == "en"
            assert strict is False
            return {
                "source_scope": "wiki",
                "source_id": "wiki-mvp",
                "corpus_path": corpus_path,
                "records": [
                    {
                        "kind": "wiki_section_chunk",
                        "article_title": "Payment retries",
                        "section_title": "Timeout handling",
                        "language": "en",
                        "content": "Workers retry payment after timeout.",
                    }
                ],
                "issues": [],
                "stats": {"input_lines": 1, "normalized_records": 1, "issues": 0},
            }

    class StubRagService:
        def index_source_records(self, **kwargs):
            assert kwargs["source_scope"] == "wiki"
            assert kwargs["source_id"] == "wiki-mvp"
            assert kwargs["records"][0]["article_title"] == "Payment retries"
            assert kwargs["source_metadata"]["import_stats"]["normalized_records"] == 1
            return (
                SimpleNamespace(model_dump=lambda: {"id": "idx-wiki-1", "source_scope": "wiki", "status": "completed"}),
                SimpleNamespace(model_dump=lambda: {"id": "run-wiki-1", "status": "completed"}),
            )

    monkeypatch.setattr("agent.routes.knowledge.get_ingestion_service", lambda: StubIngestionService())
    monkeypatch.setattr("agent.routes.knowledge.get_rag_helper_index_service", lambda: StubRagService())
    response = client.post(
        "/knowledge/wiki/import",
        headers=admin_auth_header,
        json={"corpus_path": "/tmp/wiki.jsonl", "source_id": "wiki-mvp"},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["import_report"]["source_scope"] == "wiki"
    assert payload["import_report"]["stats"]["normalized_records"] == 1
    assert payload["knowledge_index"]["source_scope"] == "wiki"


def test_knowledge_wiki_import_route_supports_async_jobs(client, admin_auth_header, monkeypatch):
    class StubIngestionService:
        def import_wiki_jsonl(self, **kwargs):
            return {
                "source_scope": "wiki",
                "source_id": "wiki-mvp",
                "corpus_path": kwargs["corpus_path"],
                "records": [{"kind": "wiki_section_chunk", "content": "x"}],
                "issues": [],
                "stats": {"input_lines": 1, "normalized_records": 1, "issues": 0},
            }

    class StubJobService:
        def submit_source_records_job(self, **kwargs):
            assert kwargs["source_scope"] == "wiki"
            assert kwargs["source_id"] == "wiki-mvp"
            return {"job_id": "job-wiki-import-1", "status": "queued"}

    monkeypatch.setattr("agent.routes.knowledge.get_ingestion_service", lambda: StubIngestionService())
    monkeypatch.setattr("agent.routes.knowledge.get_knowledge_index_job_service", lambda: StubJobService())
    response = client.post(
        "/knowledge/wiki/import",
        headers=admin_auth_header,
        json={"corpus_path": "/tmp/wiki.jsonl", "source_id": "wiki-mvp", "async": True},
    )

    assert response.status_code == 202
    payload = response.get_json()["data"]
    assert payload["job"]["job_id"] == "job-wiki-import-1"


def test_knowledge_wiki_import_route_rejects_missing_corpus_path(client, admin_auth_header):
    response = client.post("/knowledge/wiki/import", headers=admin_auth_header, json={"source_id": "wiki-mvp"})
    assert response.status_code == 400
    assert response.get_json()["message"] == "corpus_path_required"


def test_knowledge_wiki_import_url_route_passes_multistream_index(client, admin_auth_header, monkeypatch):
    captured: dict[str, object] = {}

    class StubIngestionService:
        def import_wiki_jsonl_from_url(self, **kwargs):
            captured.update(kwargs)
            return {
                "source_scope": "wiki",
                "source_id": "dewiki",
                "corpus_path": "/tmp/dewiki.xml.bz2",
                "index_path": "/tmp/dewiki-index.txt",
                "jsonl_cache_path": "/tmp/dewiki.normalized.jsonl",
                "records": [{"kind": "wiki_section_chunk", "content": "x"}],
                "issues": [],
                "stats": {"normalized_records": 1, "issues": 0},
                "download": {"url": kwargs["corpus_url"], "index": {"url": kwargs["index_url"]}},
            }

    class StubRagService:
        def index_source_records(self, **kwargs):
            return (
                SimpleNamespace(model_dump=lambda: {"id": "idx-wiki", "source_scope": "wiki", "status": "completed"}),
                SimpleNamespace(model_dump=lambda: {"id": "run-wiki", "status": "completed"}),
            )

    monkeypatch.setattr("agent.routes.knowledge.get_ingestion_service", lambda: StubIngestionService())
    monkeypatch.setattr("agent.routes.knowledge.get_rag_helper_index_service", lambda: StubRagService())

    response = client.post(
        "/knowledge/wiki/import-url",
        headers=admin_auth_header,
        json={"preset_id": "wikipedia-de-multistream-latest", "async": False, "codecompass_prerender": False},
    )

    assert response.status_code == 200
    assert captured["corpus_url"].endswith("dewiki-latest-pages-articles-multistream.xml.bz2")
    assert captured["index_url"].endswith("dewiki-latest-pages-articles-multistream-index.txt.bz2")
    assert response.get_json()["data"]["import_report"]["jsonl_cache_path"] == "/tmp/dewiki.normalized.jsonl"


def test_knowledge_wiki_import_url_route_rejects_zim_prototype(client, admin_auth_header):
    response = client.post(
        "/knowledge/wiki/import-url",
        headers=admin_auth_header,
        json={"preset_id": "wikipedia-de-zim-mini-2026-04"},
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "wiki_preset_not_supported"
