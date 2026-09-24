"""Worker-only CodeCompass context route returns bounded, citable snippets."""

import json
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.routes.meet import meet_bp
from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
from worker.meet_media.contract import encode, signature

pytestmark = pytest.mark.timeout(15)
KEY = b"synthetic-assist-test-key-00000000"
PATH = "/api/meet/v1/internal/assist/retrieve"


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    app.extensions["meet_binding_service"] = Mock()
    app.extensions["meet_media_worker_key"] = KEY
    retrieval = Mock()
    retrieval.search_records.return_value = [
        {
            "path": "worker/meet_media/companion.py",
            "content": "x" * 2000,
            "score": 0.8,
            "symbol": "speak",
            "metadata": {
                "source_revision": "26aa8f8763891e8190b2a13ec18a2a27d73ddd8a",
                "private_host_path": "/srv/x",
                "line_start": 42,
            },
        },
        {"path": "", "content": "no symbol", "score": 0.1, "metadata": {}},
        "not-a-record",
    ]
    monkeypatch.setattr(
        "agent.services.knowledge_index_retrieval_service.KnowledgeIndexRetrievalService",
        lambda *args, **kwargs: retrieval,
    )
    return app


def post(client, body, *, signed=True):
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["X-Ananta-Task-Signature"] = signature(KEY, body)
    return client.post(PATH, data=body, headers=headers)


def test_snippets_carry_path_symbol_revision_and_bounded_excerpt(app):
    response = post(app.test_client(), encode({"query": "Wie funktioniert deine Sprachausgabe?", "limit": 3}))
    assert response.status_code == 200
    payload = json.loads(response.data)
    assert payload["schema"] == "ananta.meet-assist-retrieve.v1"
    first, second = payload["snippets"]
    assert set(first) == {"path", "score", "excerpt", "symbol", "revision", "line"}
    assert first["line"] == 42 and second["line"] is None
    assert first["symbol"] == "speak" and first["revision"] == "26aa8f8763891e8190b2a13ec18a2a27d73ddd8a"
    assert len(first["excerpt"]) == 1200 and "private_host_path" not in json.dumps(payload)
    assert second["symbol"] == "" and second["revision"] == ""


@pytest.mark.parametrize("body, signed, status", [
    (encode({"query": "x"}), False, 401),
    (encode({"query": ""}), True, 400),
    (encode({"query": "x" * 1001}), True, 400),
])
def test_unsigned_or_invalid_queries_are_rejected(app, body, signed, status):
    response = post(app.test_client(), body, signed=signed)
    assert response.status_code == status


def test_citable_hits_come_first_and_the_display_path_is_used(app, monkeypatch):
    """MCK-008: a pathless blob must not displace hits the companion can cite."""
    retrieval = Mock()
    retrieval.search_records.return_value = [
        {"path": "", "content": "registry blob", "score": 242.4, "metadata": {}},
        {
            "path": "",
            "content": "class RagHelperIndexService",
            "score": 16.2,
            "metadata": {"display_path": "agent/services/rag_helper_index_service.py"},
        },
        {"path": "docs/rag-helper.md", "content": "RAG-Helper", "score": 8.4, "metadata": {}},
    ]
    monkeypatch.setattr(
        "agent.services.knowledge_index_retrieval_service.KnowledgeIndexRetrievalService",
        lambda *args, **kwargs: retrieval,
    )

    response = post(app.test_client(), encode({"query": "rag-helper", "limit": 2}))

    paths = [snippet["path"] for snippet in json.loads(response.data)["snippets"]]
    assert paths == ["agent/services/rag_helper_index_service.py", "docs/rag-helper.md"]
    # Over-fetched so the pathless hit can be dropped without losing a slot.
    assert retrieval.search_records.call_args.kwargs["limit"] == 4


def _live_shaped_index(tmp_path):
    """The shape of the live repo_path index: records ingestion keeps the file
    only in ``metadata.relative_path``; a registry blob repeats the query."""
    from types import SimpleNamespace

    def record(path, content, file_type="python"):
        return {"id": path, "content": content, "metadata": {"file_type": file_type, "relative_path": path}}

    registry = json.dumps({"schema": "codecompass.file-type-support-registry.v1"}) + (
        ' {"parser_strategy": "rag_helper", "storage": "rag-helper helper pipeline"}' * 400
    )
    records = [
        record("config/codecompass/file_type_support.v1.json", registry, "json"),
        {"id": "registry-without-path", "content": registry},
        record("agent/services/rag_helper_index_service.py",
               "class RagHelperIndexService: runs the rag_helper pipeline for a repository path"),
        record("agent/services/rag_helper_file_type_policy.py",
               "class RagHelperFileTypePolicy: which files the rag_helper pipeline reads"),
        record("tests/test_rag_helper_index_service.py",
               "def test_rag_helper_index_service(): RagHelperIndexService rag_helper pipeline"),
        record("docs/rag-helper.md", "# RAG Helper\n\nWhat the rag-helper is good for.", "text"),
        record(".hermes/plans/e2e-pipeline.md", "# E2E pipeline plan: pipeline stages of the pipeline", "text"),
    ]
    output_dir = tmp_path / "records-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
    repository = SimpleNamespace(list_completed=lambda: [SimpleNamespace(
        id="idx-live-shape", artifact_id=None, source_scope="repo_path", profile_name="deep_code",
        output_dir=str(output_dir))])
    return KnowledgeIndexRetrievalService(knowledge_index_repository=repository)


@pytest.mark.parametrize("query", ["rag-helper", "rag_helper", "rag helper", "rag_helper pipeline", "RAG helper Ananta"])
def test_rag_helper_variants_return_citable_modules_and_docs_before_tests_and_registry(app, monkeypatch, tmp_path, query):
    """Live regression: "rag-helper" answered from a registry blob without a citable path."""
    service = _live_shaped_index(tmp_path)
    monkeypatch.setattr(
        "agent.services.knowledge_index_retrieval_service.KnowledgeIndexRetrievalService",
        lambda *args, **kwargs: service,
    )

    response = post(app.test_client(), encode({"query": query, "limit": 5}))

    paths = [snippet["path"] for snippet in json.loads(response.data)["snippets"]]
    assert all(paths), paths
    # The modules named after the query lead, their tests follow them, and
    # the doc page always makes the five slots the companion sees.
    assert set(paths[:2]) == {
        "agent/services/rag_helper_index_service.py",
        "agent/services/rag_helper_file_type_policy.py",
    }, paths
    assert "docs/rag-helper.md" in paths
    assert paths.index("tests/test_rag_helper_index_service.py") > 1
    blob = "config/codecompass/file_type_support.v1.json"
    assert blob not in paths or paths.index(blob) > paths.index("docs/rag-helper.md")
