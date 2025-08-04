import os
import sys

from flask import Flask
import pytest

# Ensure repository root is at the beginning of sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.controller.routes import bp as controller_bp, controller_agent


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(controller_bp)
    controller_agent.tasks = ["one", "two"]
    controller_agent.blacklist.clear()
    controller_agent._log.clear()
    with app.test_client() as client:
        yield client, controller_agent


def test_assign_task(client):
    client, agent = client
    resp = client.get("/controller/next-task")
    assert resp.status_code == 200
    assert resp.get_json() == {"task": "one"}
    resp = client.get("/controller/next-task")
    assert resp.get_json() == {"task": "two"}
    resp = client.get("/controller/next-task")
    assert resp.get_json() == {"task": None}


def test_blacklist(client):
    client, agent = client
    # Reset tasks
    agent.tasks = ["one", "two"]
    resp = client.post("/controller/blacklist", json={"task": "one"})
    assert resp.status_code == 204
    resp = client.get("/controller/next-task")
    assert resp.get_json() == {"task": "two"}
    resp = client.get("/controller/blacklist")
    assert resp.get_json() == ["one"]
    assert agent.log_status() == ["blacklisted:one", "assigned:two"]
