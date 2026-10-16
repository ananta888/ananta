from flask import Flask

from agent.bootstrap import codecompass_worker_tools


def test_worker_advertises_registered_codecompass_tools(monkeypatch):
    monkeypatch.setattr(codecompass_worker_tools.settings, "role", "worker")
    app = Flask(__name__)
    app.extensions["workflow_adapter_worker_registration"] = {
        "capabilities": ["coding", "workflow.adapter.native"],
        "runtime_targets": [{"runtime_id": "native"}],
    }

    codecompass_worker_tools.initialize_codecompass_worker_tools(app)

    registration = app.extensions["workflow_adapter_worker_registration"]
    assert "codecompass" in registration["capabilities"]
    assert "workflow.adapter.native" in registration["capabilities"]
    assert registration["runtime_targets"] == [{"runtime_id": "native"}]
    assert app.extensions["codecompass_worker_tools_status"]["registered"] is True
    assert "codecompass_retrieval_capability_resolver" not in app.extensions


def test_hub_does_not_advertise_worker_tools(monkeypatch):
    monkeypatch.setattr(codecompass_worker_tools.settings, "role", "hub")
    app = Flask(__name__)

    codecompass_worker_tools.initialize_codecompass_worker_tools(app)

    assert "workflow_adapter_worker_registration" not in app.extensions
    assert app.extensions["codecompass_worker_tools_status"]["registered"] is False


def test_missing_core_tool_prevents_advertisement(monkeypatch):
    monkeypatch.setattr(codecompass_worker_tools.settings, "role", "worker")
    registry = codecompass_worker_tools.get_ananta_tool_registry_service()
    original = registry.get_tool
    monkeypatch.setattr(registry, "get_tool", lambda name: None if name == "codecompass.plan_context" else original(name))
    app = Flask(__name__)

    codecompass_worker_tools.initialize_codecompass_worker_tools(app)

    assert "workflow_adapter_worker_registration" not in app.extensions
    status = app.extensions["codecompass_worker_tools_status"]
    assert status["registered"] is False
    assert status["missing_tools"] == ["codecompass.plan_context"]
