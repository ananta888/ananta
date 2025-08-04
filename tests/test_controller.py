import importlib
import os
import sys
import pytest

# Ensure the repository root is on the Python path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Provide a Flask test client with a temporary DATA_DIR."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    controller = importlib.import_module("controller")
    importlib.reload(controller)
    controller.app.config["TESTING"] = True
    with controller.app.test_client() as client:
        yield client

def test_next_config(client):
    resp = client.get("/next-config")
    assert resp.status_code == 200

def test_approve(client):
    resp = client.post("/approve", data={"cmd": "echo hi", "summary": "summary"})
    assert resp.status_code == 200

def test_dashboard_get(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Rolle: Architect" in html
    assert "Zweck:" in html
    assert "Bevorzugte Hardware: CPU" in html
    assert "🔀 Pipeline Order" in html
    assert "<li>Architect</li>" in html
    assert "GitHub Issues" in html

def test_dashboard_post(client):
    resp = client.post("/", data={}, follow_redirects=True)
    assert resp.status_code == 200


def test_update_prompt(client):
    cfg = client.get("/next-config").get_json()
    data = {
        "agent": cfg["agent"],
        "prompt": "custom prompt",
        "tasks": "",
    }
    resp = client.post("/", data=data, follow_redirects=True)
    assert resp.status_code == 200
    resp = client.get("/next-config")
    assert resp.get_json()["prompt"] == "custom prompt"


def test_agent_toggle_and_log(client):
    cfg = client.get("/next-config").get_json()
    name = cfg["agent"]
    resp1 = client.post(f"/agent/{name}/toggle_active")
    assert resp1.status_code == 200
    status1 = resp1.get_json()["controller_active"]
    resp2 = client.post(f"/agent/{name}/toggle_active")
    assert resp2.status_code == 200
    status2 = resp2.get_json()["controller_active"]
    assert status1 != status2
    resp_log = client.get(f"/agent/{name}/log")
    assert resp_log.status_code == 200
    assert isinstance(resp_log.get_data(as_text=True), str)

def test_stop(client):
    resp = client.post("/stop")
    assert resp.status_code == 200

def test_restart(client):
    resp = client.post("/restart")
    assert resp.status_code == 200

def test_export(client):
    resp = client.get("/export")
    assert resp.status_code == 200


def test_issues_route(client, monkeypatch):
    view_globals = client.application.view_functions["issues"].__globals__
    sample = [{"number": 1, "title": "Test", "html_url": "http://example.com"}]
    monkeypatch.setitem(view_globals, "fetch_issues", lambda repo, token: sample)
    resp = client.get("/issues?repo=foo/bar")
    assert resp.status_code == 200
    assert resp.get_json() == sample
