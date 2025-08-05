from flask import Flask
from src.controller import routes


def setup_app():
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    routes.controller_agent.tasks = []
    routes.controller_agent.blacklist.clear()
    routes.controller_agent._log.clear()
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
