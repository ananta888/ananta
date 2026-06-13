"""RWY-010: Regression tests für /rag why — typische Blindheitsfälle."""
from __future__ import annotations

import json
import pytest


# ── Flask app fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def app():
    from flask import Flask
    from agent.routes.snakes import snakes_bp
    a = Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(snakes_bp)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


# ── _snake_retrieval_dry_run unit tests ───────────────────────────────────────

def test_dry_run_returns_profile():
    from agent.routes.snakes import _snake_retrieval_dry_run
    result = _snake_retrieval_dry_run("Wo wird TaskRoutingContract definiert?")
    assert "retrieval_profile" in result
    prof = result["retrieval_profile"]
    assert "profile_id" in prof
    assert "domain" in prof
    assert "source_types" in prof


def test_dry_run_returns_scope():
    from agent.routes.snakes import _snake_retrieval_dry_run
    result = _snake_retrieval_dry_run("zeige alle Tests")
    scope = result.get("resolver_scope") or {}
    assert "include_source" in scope
    assert "include_test_paths" in scope
    assert "include_docs" in scope


def test_dry_run_preset_hint_todos(monkeypatch):
    """RWY-010 Testfall: 'zeige mir Todos' zeigt hint wenn docs deaktiviert."""
    from agent.routes.snakes import _snake_retrieval_dry_run
    from worker.retrieval import codecompass_candidate_resolver as ccr

    class _FakeScope:
        include_source = True
        include_test_paths = False
        include_docs = False
        include_workflows = False
        include_third_party = False
        include_xml_nodes = False

    monkeypatch.setattr(ccr.ResolverConfig, "from_env", classmethod(lambda cls, *a, **kw: _FakeScope()))
    result = _snake_retrieval_dry_run("zeige mir Todos und README")
    hints = result.get("preset_hints") or []
    assert any("docs" in h.lower() or "docs_first" in h for h in hints), f"expected docs hint, got: {hints}"


def test_dry_run_preset_hint_tests(monkeypatch):
    """RWY-010 Testfall: 'was sagen Tests dazu' zeigt hint wenn tests deaktiviert."""
    from agent.routes.snakes import _snake_retrieval_dry_run
    from worker.retrieval import codecompass_candidate_resolver as ccr

    class _FakeScope:
        include_source = True
        include_test_paths = False
        include_docs = False
        include_workflows = False
        include_third_party = False
        include_xml_nodes = False

    monkeypatch.setattr(ccr.ResolverConfig, "from_env", classmethod(lambda cls, *a, **kw: _FakeScope()))
    result = _snake_retrieval_dry_run("was sagen die pytest Tests dazu?")
    hints = result.get("preset_hints") or []
    assert any("test" in h.lower() for h in hints), f"expected test hint, got: {hints}"


def test_dry_run_preset_hint_readme(monkeypatch):
    """RWY-010 Testfall: 'was steht in README' zeigt docs disabled als Diagnose."""
    from agent.routes.snakes import _snake_retrieval_dry_run
    from worker.retrieval import codecompass_candidate_resolver as ccr

    class _FakeScope:
        include_source = True
        include_test_paths = False
        include_docs = False
        include_workflows = False
        include_third_party = False
        include_xml_nodes = False

    monkeypatch.setattr(ccr.ResolverConfig, "from_env", classmethod(lambda cls, *a, **kw: _FakeScope()))
    result = _snake_retrieval_dry_run("was steht in der README Datei?")
    hints = result.get("preset_hints") or []
    assert any("docs" in h.lower() or "readme" in h.lower() for h in hints), f"expected docs hint, got: {hints}"


def test_dry_run_preset_hint_workflows(monkeypatch):
    """RWY-010 Testfall: Workflows-Frage zeigt hint wenn workflows deaktiviert."""
    from agent.routes.snakes import _snake_retrieval_dry_run
    from worker.retrieval import codecompass_candidate_resolver as ccr

    class _FakeScope:
        include_source = True
        include_test_paths = False
        include_docs = False
        include_workflows = False
        include_third_party = False
        include_xml_nodes = False

    monkeypatch.setattr(ccr.ResolverConfig, "from_env", classmethod(lambda cls, *a, **kw: _FakeScope()))
    result = _snake_retrieval_dry_run("welche Blueprint-Workflows gibt es für Ops-Runbooks?")
    hints = result.get("preset_hints") or []
    assert any("workflow" in h.lower() for h in hints), f"expected workflow hint, got: {hints}"


# ── /snake/ask trace_only endpoint tests ─────────────────────────────────────

def test_trace_only_returns_without_llm(client):
    """/snake/ask trace_only=True returns rag_why without answer."""
    resp = client.post("/snake/ask", json={
        "question": "Wo ist der TaskRouter definiert?",
        "trace_only": True,
    })
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data.get("trace_only") is True
    assert "rag_why" in data
    assert "answer" not in data


def test_trace_only_empty_question(client):
    resp = client.post("/snake/ask", json={"trace_only": True, "question": ""})
    assert resp.status_code == 400


def test_trace_only_includes_profile(client):
    resp = client.post("/snake/ask", json={
        "question": "zeige mir die Architektur",
        "trace_only": True,
    })
    assert resp.status_code == 200
    data = json.loads(resp.data)
    rag_why = data.get("rag_why") or {}
    profile = rag_why.get("retrieval_profile") or {}
    assert "profile_id" in profile
    assert "source_types" in profile


def test_trace_only_includes_scope(client):
    resp = client.post("/snake/ask", json={
        "question": "was steht in den Tests?",
        "trace_only": True,
    })
    assert resp.status_code == 200
    data = json.loads(resp.data)
    scope = (data.get("rag_why") or {}).get("resolver_scope") or {}
    assert "include_source" in scope
    assert "include_test_paths" in scope


def test_trace_only_with_retrieval_config_override(client):
    resp = client.post("/snake/ask", json={
        "question": "Wo werden Blueprints verarbeitet?",
        "trace_only": True,
        "retrieval_config": {"chat_retrieval_profile": "repo_first"},
    })
    assert resp.status_code == 200
    data = json.loads(resp.data)
    prof = (data.get("rag_why") or {}).get("retrieval_profile") or {}
    assert str(prof.get("feature_flag") or "") == "repo_first"


def test_trace_only_honors_source_scope_retrieval_overrides(client):
    resp = client.post("/snake/ask", json={
        "question": "Erklaere den lokalen Projektcode",
        "trace_only": True,
        "retrieval_config": {
            "chat_retrieval_profile": "repo_first",
            "chat_codecompass_trigger_mode": "force_repo_first",
            "chat_use_codecompass": True,
            "chat_include_local_project": False,
            "chat_include_wikipedia": False,
            "chat_include_task_memory": False,
            "chat_source_pack_id": "local-project",
        },
    })
    assert resp.status_code == 200
    data = json.loads(resp.data)
    prof = (data.get("rag_why") or {}).get("retrieval_profile") or {}
    assert str(prof.get("trigger_mode") or "") == "force_repo_first"
    assert "repo" not in list(prof.get("source_types") or [])
    assert any("repo" in str(w) for w in list(prof.get("warnings") or []))
