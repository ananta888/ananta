"""Worker-only CodeCompass context route returns bounded, citable snippets."""

import json
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.routes.meet import meet_bp
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
