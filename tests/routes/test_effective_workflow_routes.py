from __future__ import annotations

from flask import Flask

import agent.routes.effective_workflow as route_module
from agent.routes.effective_workflow import effective_workflow_bp
from tests.services.test_effective_workflow_resolver import (
    make_graph,
    sample_blueprints,
    sample_templates,
)


def app_with_effective_workflow(monkeypatch) -> Flask:
    graph, cfg = make_graph()

    def fake_context():
        return graph, cfg, sample_blueprints(), sample_templates()

    monkeypatch.setattr(route_module, "_build_graph_and_context", fake_context)
    app = Flask(__name__)
    app.register_blueprint(effective_workflow_bp)
    return app


def test_options_route_returns_autocomplete_dimensions(monkeypatch) -> None:
    client = app_with_effective_workflow(monkeypatch).test_client()

    response = client.get("/api/effective-workflow/options")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "ananta.effective_workflow.options.v1"
    assert "ai_snake_chat" in payload["surfaces"]


def test_resolve_route_returns_effective_workflow(monkeypatch) -> None:
    client = app_with_effective_workflow(monkeypatch).test_client()

    response = client.post("/api/effective-workflow/resolve", json={
        "surface": "ai_snake_chat",
        "task_kind": "bugfix",
        "path": "agent/routes/tasks/goals.py",
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "ananta.effective_workflow.v1"
    assert payload["selected"]["blueprint"]["id"] == "bp-bugfix"


def test_resolve_route_requires_surface(monkeypatch) -> None:
    client = app_with_effective_workflow(monkeypatch).test_client()

    response = client.post("/api/effective-workflow/resolve", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "surface is required"


def test_compare_route_returns_differences(monkeypatch) -> None:
    client = app_with_effective_workflow(monkeypatch).test_client()

    response = client.post("/api/effective-workflow/compare", json={
        "left": {"surface": "ai_snake_chat", "task_kind": "bugfix"},
        "right": {"surface": "unknown_surface_xyz"},
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "ananta.effective_workflow.compare.v1"
    assert payload["status"] == "changed"
