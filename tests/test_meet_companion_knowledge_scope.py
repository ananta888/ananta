"""Companion knowledge lookups: matching passage with lines, total hits, project scope."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.routes.meet import meet_bp
from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
from agent.services.knowledge_passage import best_passage, query_tokens
from agent.services.meet_knowledge_scope import ENV_NAME, allowed_index_ids, load_bindings
from worker.meet_media.contract import encode, signature

pytestmark = pytest.mark.timeout(15)
KEY = b"synthetic-knowledge-scope-key-0000"
RETRIEVE = "/api/meet/v1/internal/assist/retrieve"

SUPERVISOR = "\n".join(
    ['"""Keep the companion in its room."""', "", "import time", ""]
    + ["# filler line %d" % n for n in range(40)]
    + ["def supervise(run, stop_requested):", "    while not stop_requested():", "        run()"]
)


# --- passage selection --------------------------------------------------------


def test_passage_is_the_matching_window_with_its_line_range():
    passage = best_passage(SUPERVISOR, "supervise stop_requested", max_chars=120)
    assert passage.text.splitlines()[2] == "def supervise(run, stop_requested):"
    assert passage.line_start == 43 and passage.line_end == 47
    assert len(passage.text) <= 120


def test_passage_lines_are_offset_by_the_record_start_line():
    passage = best_passage("a\nb\nneedle here\nc", "needle", max_chars=200, base_line=100)
    assert passage.line_start == 100 and passage.line_end == 103


def test_passage_without_a_token_hit_starts_at_the_top_and_empty_content_has_none():
    assert best_passage("x\ny", "zzz", max_chars=10).line_start == 1
    assert best_passage("", "x", max_chars=10) is None
    assert best_passage("x", "x", max_chars=0) is None


def test_an_overlong_single_line_is_cut_to_the_budget():
    passage = best_passage("needle " * 100, "needle", max_chars=50)
    assert len(passage.text) == 50 and passage.line_start == passage.line_end == 1


def test_query_tokens_match_kebab_and_snake_case():
    assert query_tokens("rag-helper RAG_helper x") == ["rag_helper"]


# --- search_records_page ------------------------------------------------------


def _index(tmp_path, index_id, records):
    output_dir = tmp_path / index_id
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
    return SimpleNamespace(
        id=index_id, artifact_id=None, source_scope="repo_path", profile_name="deep_code",
        output_dir=str(output_dir), index_metadata={"source_id": index_id + "-source"},
    )


@pytest.fixture
def service(tmp_path):
    project = _index(tmp_path, "idx-project", [
        {"path": "worker/meet_media/companion_supervisor.py", "file": "companion_supervisor.py",
         "content": SUPERVISOR},
        {"path": "docs/meet-companion-avatar.md", "content": "# Companion\nsupervise rejoin"},
        {"path": "agent/other.py", "content": "unrelated"},
    ])
    fixture = _index(tmp_path, "idx-fixture", [{"path": "fixture/supervise.py", "content": "supervise"}])
    repository = SimpleNamespace(list_completed=lambda: [project, fixture])
    return KnowledgeIndexRetrievalService(knowledge_index_repository=repository)


def test_page_reports_the_total_beyond_the_returned_head(service):
    page = service.search_records_page("supervise", limit=1)
    assert len(page["records"]) == 1 and page["total"] == 3
    assert "passage" not in page["records"][0]


def test_page_passage_points_at_the_matching_lines(service):
    page = service.search_records_page("supervise stop_requested", limit=5, passage_chars=200)
    top = next(r for r in page["records"] if r["path"] == "worker/meet_media/companion_supervisor.py")
    assert top["passage"]["line_start"] <= 45 <= top["passage"]["line_end"]
    assert "def supervise" in top["passage"]["text"]


def test_passage_lines_ignore_the_index_header_of_whole_file_records(tmp_path):
    header = "# agent/x.py\n# Themen: demo\n"
    index = _index(tmp_path, "idx-header", [
        {"path": "agent/x.py", "content": header + "import os\n\ndef target():\n    pass"},
        {"path": "agent/y.py", "content": "# not/the/path\ndef target():"},
    ])
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=SimpleNamespace(list_completed=lambda: [index]))
    records = {r["path"]: r for r in service.search_records_page("target", passage_chars=500)["records"]}
    assert records["agent/x.py"]["passage"]["line_start"] == 1
    assert records["agent/x.py"]["passage"]["text"].splitlines()[2] == "def target():"
    # A foreign first line is file content, not an index header.
    assert records["agent/y.py"]["passage"]["line_start"] == 1
    assert records["agent/y.py"]["passage"]["text"].startswith("# not/the/path")


def test_index_ids_filter_every_index_including_legacy_ones(service):
    page = service.search_records_page("supervise", limit=5, index_ids={"idx-project"})
    assert page["total"] == 2
    assert all(not r["path"].startswith("fixture/") for r in page["records"])
    assert service.search_records_page("supervise", index_ids=set())["total"] == 0


def test_search_records_is_unchanged_by_the_page_api(service):
    records = service.search_records("supervise", limit=2)
    assert len(records) == 2 and "passage" not in records[0]


# --- project binding ------------------------------------------------------------


def test_bindings_select_indices_by_id_source_or_connection(service):
    completed = lambda: service._iter_completed_indices(allowed_index_ids=None)  # noqa: E731
    environ = {ENV_NAME: json.dumps({"p1": ["idx-project-source"], "p2": ["idx-fixture"], "p3": ["nope"]})}
    assert allowed_index_ids("p1", completed, environ=environ) == {"idx-project"}
    assert allowed_index_ids("p2", completed, environ=environ) == {"idx-fixture"}
    assert allowed_index_ids("p3", completed, environ=environ) == set()
    assert allowed_index_ids("unbound", completed, environ=environ) is None


def test_default_binding_applies_to_unbound_projects_and_to_calls_without_project(service):
    completed = lambda: service._iter_completed_indices(allowed_index_ids=None)  # noqa: E731
    environ = {ENV_NAME: json.dumps({"*": ["idx-project"]})}
    assert allowed_index_ids("any", completed, environ=environ) == {"idx-project"}
    assert allowed_index_ids(None, completed, environ=environ) == {"idx-project"}


@pytest.mark.parametrize("raw", ["", "not json", "[1]"])
def test_missing_or_malformed_bindings_stay_unscoped(raw):
    assert load_bindings({ENV_NAME: raw}) == {}


# --- the retrieve route ---------------------------------------------------------------


@pytest.fixture
def app(monkeypatch, service):
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    app.extensions["meet_binding_service"] = Mock()
    app.extensions["meet_media_worker_key"] = KEY
    monkeypatch.setattr(
        "agent.services.knowledge_index_retrieval_service.KnowledgeIndexRetrievalService",
        lambda *args, **kwargs: service,
    )
    return app


def retrieve(app, query, limit=5):
    body = encode({"query": query, "limit": limit})
    headers = {"Content-Type": "application/json", "X-Ananta-Task-Signature": signature(KEY, body)}
    response = app.test_client().post(RETRIEVE, data=body, headers=headers)
    assert response.status_code == 200, response.data
    return json.loads(response.data)


def test_route_returns_total_and_passage_lines(app, monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    payload = retrieve(app, "supervise stop_requested", limit=1)
    assert payload["total"] == 3 and len(payload["snippets"]) == 1
    payload = retrieve(app, "supervise stop_requested", limit=5)
    snippet = next(s for s in payload["snippets"] if s["path"] == "worker/meet_media/companion_supervisor.py")
    assert snippet["line"] <= 45 <= snippet["line_end"] and "def supervise" in snippet["excerpt"]


def test_route_honours_the_default_binding(app, monkeypatch):
    monkeypatch.setenv(ENV_NAME, json.dumps({"*": ["idx-fixture"]}))
    payload = retrieve(app, "supervise")
    assert payload["total"] == 1 and payload["snippets"][0]["path"] == "fixture/supervise.py"
    monkeypatch.setenv(ENV_NAME, json.dumps({"*": []}))
    assert retrieve(app, "supervise") == {
        "schema": "ananta.meet-assist-retrieve.v1", "snippets": [], "total": 0,
    }
