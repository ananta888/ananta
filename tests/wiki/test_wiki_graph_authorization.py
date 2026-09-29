from types import SimpleNamespace

import pytest

from agent.routes import wiki_graph
from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
)


def _bound_wiki_index(tmp_path, *, projection_state: str):
    return SimpleNamespace(
        id="wiki-index-1",
        source_scope="wiki",
        status="completed",
        output_dir=str(tmp_path),
        index_metadata={
            "source_control_scope": {
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "owner_id": "owner-a",
            },
            KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY: {
                "schema": KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
                "projection_state": projection_state,
                "execution_job_schema": KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
                "job_id": "knowledge-index-" + ("1" * 32),
                "knowledge_index_id": "wiki-index-1",
                "authority_binding_digest": "a" * 64,
                "assignment_id": "assignment-1",
            },
        },
    )


def _install_repository(monkeypatch, index):
    repository = SimpleNamespace(
        get_by_id=lambda index_id: index if index_id == index.id else None
    )
    monkeypatch.setattr(
        wiki_graph,
        "get_repository_registry",
        lambda: SimpleNamespace(knowledge_index_repo=repository),
    )


def test_wiki_graph_hides_foreign_scoped_index_before_reading_content(
    client,
    user_auth_header,
    monkeypatch,
    tmp_path,
):
    index = _bound_wiki_index(tmp_path, projection_state="projected")
    _install_repository(monkeypatch, index)
    monkeypatch.setattr(
        wiki_graph._svc,
        "get_build_status",
        lambda _output_dir: pytest.fail(
            "foreign wiki index must not be read"
        ),
    )

    response = client.get(
        "/api/wiki-graph/status?index_id=wiki-index-1",
        headers=user_auth_header,
    )

    assert response.status_code == 404


def test_wiki_graph_hides_pending_v2_index_after_hub_authorization(
    client,
    admin_auth_header,
    monkeypatch,
    tmp_path,
):
    index = _bound_wiki_index(tmp_path, projection_state="pending")
    _install_repository(monkeypatch, index)
    monkeypatch.setattr(
        wiki_graph._svc,
        "get_build_status",
        lambda _output_dir: pytest.fail(
            "pending wiki index must not be read"
        ),
    )

    response = client.get(
        "/api/wiki-graph/status?index_id=wiki-index-1",
        headers=admin_auth_header,
    )

    assert response.status_code == 404


def test_wiki_graph_reads_projected_v2_after_hub_authorization(
    client,
    admin_auth_header,
    monkeypatch,
    tmp_path,
):
    index = _bound_wiki_index(tmp_path, projection_state="projected")
    _install_repository(monkeypatch, index)
    monkeypatch.setattr(
        wiki_graph._svc,
        "get_build_status",
        lambda output_dir: {
            "status": "ready",
            "output_dir": str(output_dir),
        },
    )

    response = client.get(
        "/api/wiki-graph/status?index_id=wiki-index-1",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "ready"


def test_wiki_graph_post_authorizes_the_body_index_not_a_query_override(
    client,
    admin_auth_header,
    monkeypatch,
):
    query_index = SimpleNamespace(id="query-index")
    body_index = SimpleNamespace(id="body-index")
    indices = {query_index.id: query_index, body_index.id: body_index}
    monkeypatch.setattr(
        wiki_graph,
        "get_repository_registry",
        lambda: SimpleNamespace(
            knowledge_index_repo=SimpleNamespace(get_by_id=indices.get)
        ),
    )
    authorized_ids = []

    def _deny_after_recording(**kwargs):
        authorized_ids.append(kwargs["object_id"])
        return "", 404

    monkeypatch.setattr(
        wiki_graph,
        "authorize_route_request",
        _deny_after_recording,
    )

    response = client.post(
        "/api/wiki-graph/build?index_id=query-index",
        json={"index_id": "body-index"},
        headers=admin_auth_header,
    )

    assert response.status_code == 404
    assert authorized_ids == ["body-index"]
