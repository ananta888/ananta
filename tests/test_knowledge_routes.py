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

    class StubRagService:
        def index_artifact(self, artifact_id: str, *, created_by: str | None):
            return (
                SimpleNamespace(model_dump=lambda: {"id": "idx-1", "artifact_id": artifact_id, "status": "completed"}),
                SimpleNamespace(model_dump=lambda: {"id": "run-1", "artifact_id": artifact_id, "status": "completed"}),
            )

    monkeypatch.setattr("agent.routes.knowledge.get_rag_helper_index_service", lambda: StubRagService())

    response = client.post(f"/knowledge/collections/{collection_id}/index", headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["results"][0]["artifact_id"] == artifact_id
    assert payload["results"][0]["run"]["status"] == "completed"


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
        def search(self, query: str, *, top_k: int = 4, artifact_ids=None):
            assert query == "timeout"
            assert artifact_ids == {artifact_id}
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
    assert payload["chunks"][0]["engine"] == "knowledge_index"
    assert payload["chunks"][0]["metadata"]["artifact_id"] == artifact_id
