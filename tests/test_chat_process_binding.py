import copy
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from agent.routes.chat import chat_bp
from agent.services.chat_process_binding import normalize_process_ref, resolve_effective_process
from client_surfaces.operator_tui.chat_state import make_session


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    return app.test_client()


def _manager_for(session):
    data = {"chat_sessions": [session], "chat_active_session_id": session["id"], "chat_profiles": [], "chat_folders": []}
    manager = MagicMock()
    manager.load.side_effect = lambda: copy.deepcopy(data)
    manager.save.side_effect = lambda values: data.update(copy.deepcopy(values)) or True
    return data, manager


def test_process_reference_is_strict_and_normalized():
    assert normalize_process_ref(None) is None
    assert normalize_process_ref({"graph_id": "vp-1"}) == {"graph_id": "vp-1", "version": "latest"}
    with pytest.raises(ValueError, match="process_graph_id_required"):
        normalize_process_ref({"version": "1.0"})


def test_session_process_overrides_profile_without_mutating_definition():
    session = {"process_ref": {"graph_id": "vp-session", "version": "2"}}
    profile = {"process_ref": {"graph_id": "vp-profile", "version": "1"}}
    graph = {"id": "vp-session", "steps": [{"id": "step-1"}]}
    with patch("agent.services.chat_process_binding.load_graph", return_value=graph):
        result = resolve_effective_process(session, profile)
    assert result["source"] == "session"
    assert result["graph"] == graph
    assert "run_state" not in result["graph"]["steps"][0]


def test_profile_process_is_inherited_when_session_has_no_override():
    profile = {"process_ref": {"graph_id": "vp-profile", "version": "1"}}
    with patch("agent.services.chat_process_binding.load_graph", return_value={"id": "vp-profile"}):
        result = resolve_effective_process({}, profile)
    assert result["source"] == "profile"
    assert result["process_ref"] == profile["process_ref"]


def test_session_process_run_endpoints_persist_and_return_overlay(client):
    session = make_session(session_id="chat-1", name="Chat")
    session["process_ref"] = {"graph_id": "vp-1", "version": "1"}
    data, manager = _manager_for(session)
    graph = {"id": "vp-1", "name": "Flow", "version": "1", "steps": [], "edges": [], "tags": []}
    run = {"run_id": "wf-1", "workflow_id": "wf-1", "process_id": "vp-1", "process_version": "1", "snapshot_hash": "abc", "status": "running", "started_at": 1}
    overlay = {"run_id": "wf-1", "workflow_id": "wf-1", "overall_status": "running", "steps": {}, "step_states": {}}
    with (
        patch("agent.routes.chat.get_manager", return_value=manager),
        patch("agent.routes.chat.resolve_effective_process", return_value={"graph": graph, "process_ref": session["process_ref"], "source": "session_override"}),
        patch("agent.routes.chat.start_session_process", return_value=run),
        patch("agent.routes.chat.runtime_overlay", return_value=overlay),
    ):
        started = client.post("/api/chat/sessions/chat-1/process/runs", json={"message_id": "msg-1"})
        listed = client.get("/api/chat/sessions/chat-1/process/runs")
        status = client.get("/api/chat/sessions/chat-1/process/runs/wf-1")
    assert started.status_code == 201
    assert data["chat_sessions"][0]["process_runs"][0]["snapshot_hash"] == "abc"
    assert listed.json[0]["workflow_id"] == "wf-1"
    assert status.json["overall_status"] == "running"
