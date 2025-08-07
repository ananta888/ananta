from flask import Flask
from src.controller import routes
from src.models import ModelPool


def setup_app():
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    routes.controller_agent.tasks = []
    routes.controller_agent.blacklist.clear()
    routes.controller_agent._log.clear()
    routes.model_pool = ModelPool()
    return app


def test_next_task_and_blacklist():
    app = setup_app()
    routes.controller_agent.tasks.extend(["one", "two"])
    client = app.test_client()

    resp = client.get("/controller/next-task")
    assert resp.status_code == 200
    assert resp.get_json()["task"] == "one"

    resp = client.post("/controller/blacklist", json={"task": "two"})
    assert resp.status_code == 204

    resp = client.get("/controller/blacklist")
    assert resp.get_json() == ["two"]

    resp = client.get("/controller/next-task")
    assert resp.get_json()["task"] is None

    resp = client.get("/controller/status")
    data = resp.get_json()
    assert "assigned:one" in data
    assert "blacklisted:two" in data

    resp = client.delete("/controller/status")
    assert resp.status_code == 204
    assert routes.controller_agent.log_status() == []


def test_model_routes():
    app = setup_app()
    client = app.test_client()

    resp = client.post(
        "/controller/models", json={"provider": "p", "model": "m", "limit": 2}
    )
    assert resp.status_code == 204

    resp = client.get("/controller/models")
    assert resp.get_json() == {"p": {"m": {"limit": 2, "in_use": 0, "waiters": 0}}}
