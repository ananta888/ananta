import copy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from agent.routes.chat import chat_bp
from agent.services.chat_process_binding import (
    derive_chat_workflow_id,
    normalize_process_ref,
    resolve_effective_process,
    runtime_overlay,
)
from agent.services.chat_session_security import (
    ChatSessionPrincipal,
    GateCommand,
    authorize_owned_record,
)
from agent.services.user_session_tokens import issue_user_access_token
from client_surfaces.operator_tui.chat_state import make_session


@pytest.fixture
def client():
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(chat_bp)
    return app.test_client()


def _manager_for(session):
    data = {
        "chat_sessions": [session],
        "chat_active_session_id": session["id"],
        "chat_profiles": [],
        "chat_folders": [],
    }
    manager = MagicMock()
    manager.load.side_effect = lambda: copy.deepcopy(data)
    manager.save.side_effect = lambda values: data.update(copy.deepcopy(values)) or True
    return data, manager


def _auth_headers(username: str = "test-user") -> dict[str, str]:
    token = issue_user_access_token(username=username, role="admin")
    return {"Authorization": f"Bearer {token}"}


def test_process_reference_is_strict_and_normalized():
    assert normalize_process_ref(None) is None
    assert normalize_process_ref({"graph_id": "vp-1"}) == {"graph_id": "vp-1", "version": "latest"}
    with pytest.raises(ValueError, match="process_graph_id_required"):
        normalize_process_ref({"version": "1.0"})


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("idempotency_key", " key", "idempotency_key_invalid"),
        ("idempotency_key", "key\x7f", "idempotency_key_invalid"),
        ("step_id", " approval", "gate_step_id_invalid"),
        ("step_id", "approval\n", "gate_step_id_invalid"),
    ],
)
def test_gate_command_rejects_noncanonical_identifiers(field, value, reason):
    values = {
        "idempotency_key": "canonical-key",
        "principal": ChatSessionPrincipal.from_values("tenant-a", "user-a"),
        "session_id": "session-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "approval",
        "decision": "approve",
    }
    values[field] = value

    with pytest.raises(ValueError, match=reason):
        GateCommand.from_values(**values)


def test_session_process_overrides_profile_without_mutating_definition():
    session = {"process_ref": {"graph_id": "vp-session", "version": "2"}}
    profile = {"process_ref": {"graph_id": "vp-profile", "version": "1"}}
    graph = {"id": "vp-session", "steps": [{"id": "step-1"}]}
    with patch("agent.services.chat_process_binding.load_graph", return_value=graph):
        result = resolve_effective_process(session, profile)
    assert result["source"] == "session_override"
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
    session["owner_principal"] = {"tenant_id": "test-user", "subject_id": "test-user"}
    session["process_ref"] = {"graph_id": "vp-1", "version": "1"}
    data, manager = _manager_for(session)
    graph = {"id": "vp-1", "name": "Flow", "version": "1", "steps": [], "edges": [], "tags": []}
    run = {
        "run_id": "wf-1",
        "workflow_id": "wf-1",
        "process_id": "vp-1",
        "process_version": "1",
        "snapshot_hash": "abc",
        "status": "running",
        "control_principal": {"tenant_id": "test-user", "subject_id": "test-user"},
        "started_at": 1,
    }
    overlay = {"run_id": "wf-1", "workflow_id": "wf-1", "overall_status": "running", "steps": {}, "step_states": {}}
    with (
        patch("agent.routes.chat.get_manager", return_value=manager),
        patch(
            "agent.routes.chat.resolve_effective_process",
            return_value={"graph": graph, "process_ref": session["process_ref"], "source": "session_override"},
        ),
        patch("agent.routes.chat.start_session_process", return_value=run) as start,
        patch("agent.routes.chat.runtime_overlay", return_value=overlay),
    ):
        headers = _auth_headers()
        started = client.post(
            "/api/chat/sessions/chat-1/process/runs",
            json={"message_id": "msg-1"},
            headers=headers,
        )
        listed = client.get("/api/chat/sessions/chat-1/process/runs", headers=headers)
        status = client.get("/api/chat/sessions/chat-1/process/runs/wf-1", headers=headers)
    assert started.status_code == 201
    assert start.call_args.kwargs["tenant_id"] == "test-user"
    assert start.call_args.kwargs["subject_id"] == "test-user"
    assert data["chat_sessions"][0]["process_runs"][0]["snapshot_hash"] == "abc"
    assert listed.json[0]["workflow_id"] == "wf-1"
    assert status.json["overall_status"] == "running"


def test_runtime_overlay_normalizes_states_and_redacts_credential_fields():
    backend = MagicMock()
    backend.get_workflow_status.return_value = {
        "status": "done",
        "steps": [{"step_id": "s1", "status": "done", "credential_token": "must-not-leak"}],
    }
    with patch("agent.services.chat_process_binding._controlled_backend", return_value=backend):
        overlay = runtime_overlay(
            {"run_id": "r", "workflow_id": "r", "process_id": "vp", "process_version": "1", "snapshot_hash": "h"}
        )
    assert overlay["steps"]["s1"]["status"] == "succeeded"
    assert "credential_token" not in overlay["steps"]["s1"]


def test_gate_endpoint_requires_idempotency_and_replays_duplicate_safely(client):
    session = make_session(session_id="chat-gate", name="Gate")
    session["process_runs"] = [
        {
            "run_id": "wf-gate",
            "workflow_id": "wf-gate",
            "control_principal": {"tenant_id": "test-user", "subject_id": "test-user"},
        }
    ]
    data, manager = _manager_for(session)
    with (
        patch("agent.routes.chat.get_manager", return_value=manager),
        patch("agent.routes.chat.signal_session_gate", return_value={"status": "running"}) as signal,
    ):
        headers = _auth_headers()
        missing = client.post(
            "/api/chat/sessions/chat-gate/process/runs/wf-gate/gate",
            json={"step_id": "approval", "decision": "approve"},
            headers=headers,
        )
        first = client.post(
            "/api/chat/sessions/chat-gate/process/runs/wf-gate/gate",
            json={"step_id": "approval", "decision": "approve", "idempotency_key": "gate-1"},
            headers=headers,
        )
        duplicate = client.post(
            "/api/chat/sessions/chat-gate/process/runs/wf-gate/gate",
            json={"step_id": "approval", "decision": "approve", "idempotency_key": "gate-1"},
            headers=headers,
        )
    assert missing.status_code == 400
    assert first.status_code == 200 and duplicate.json["status"] == "already_applied"
    signal.assert_called_once()
    assert data["chat_sessions"][0]["process_gate_actions"][0]["workflow_id"] == "wf-gate"
    assert data["chat_sessions"][0]["process_gate_actions"][0]["state"] == "applied"
    assert len(data["chat_sessions"][0]["process_gate_actions"][0]["request_hash"]) == 64


def test_process_run_endpoints_require_user_authentication(client):
    assert client.get("/api/chat/sessions/chat-1/process/runs").status_code == 401
    assert client.post("/api/chat/sessions/chat-1/process/runs", json={}).status_code == 401
    assert client.get("/api/chat/sessions/chat-1/process/runs/wf-1").status_code == 401
    assert client.post("/api/chat/sessions/chat-1/process/runs/wf-1/gate", json={}).status_code == 401


def test_process_run_reads_fail_closed_across_tenants(client):
    session = make_session(session_id="chat-private", name="Private")
    session["process_runs"] = [
        {
            "run_id": "wf-private",
            "workflow_id": "wf-private",
            "started_at": 1,
            "control_principal": {"tenant_id": "owner", "subject_id": "owner"},
        }
    ]
    _, manager = _manager_for(session)
    with patch("agent.routes.chat.get_manager", return_value=manager):
        headers = _auth_headers("intruder")
        listed = client.get("/api/chat/sessions/chat-private/process/runs", headers=headers)
        fetched = client.get(
            "/api/chat/sessions/chat-private/process/runs/wf-private",
            headers=headers,
        )
    assert listed.status_code == 404
    assert fetched.status_code == 404


def test_gate_idempotency_rejects_payload_reuse_without_second_signal(client):
    session = make_session(session_id="chat-gate-reuse", name="Gate")
    session["owner_principal"] = {"tenant_id": "test-user", "subject_id": "test-user"}
    session["process_runs"] = [
        {
            "run_id": "wf-gate",
            "workflow_id": "wf-gate",
            "control_principal": {"tenant_id": "test-user", "subject_id": "test-user"},
        }
    ]
    _, manager = _manager_for(session)
    with (
        patch("agent.routes.chat.get_manager", return_value=manager),
        patch("agent.routes.chat.signal_session_gate", return_value={"status": "running"}) as signal,
    ):
        headers = _auth_headers()
        first = client.post(
            "/api/chat/sessions/chat-gate-reuse/process/runs/wf-gate/gate",
            json={"step_id": "approval", "decision": "approve", "idempotency_key": "same-key"},
            headers=headers,
        )
        conflict = client.post(
            "/api/chat/sessions/chat-gate-reuse/process/runs/wf-gate/gate",
            json={"step_id": "approval", "decision": "reject", "idempotency_key": "same-key"},
            headers=headers,
        )
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json["error_code"] == "idempotency_key_reused"
    signal.assert_called_once()


def test_gate_idempotency_is_atomic_for_concurrent_replays():
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(chat_bp)
    session = make_session(session_id="chat-gate-race", name="Gate")
    session["owner_principal"] = {"tenant_id": "test-user", "subject_id": "test-user"}
    session["process_runs"] = [
        {
            "run_id": "wf-race",
            "workflow_id": "wf-race",
            "control_principal": {"tenant_id": "test-user", "subject_id": "test-user"},
        }
    ]
    _, manager = _manager_for(session)
    headers = _auth_headers()

    def send_gate():
        with app.test_client() as concurrent_client:
            return concurrent_client.post(
                "/api/chat/sessions/chat-gate-race/process/runs/wf-race/gate",
                json={"step_id": "approval", "decision": "approve", "idempotency_key": "race-key"},
                headers=headers,
            )

    with (
        patch("agent.routes.chat.get_manager", return_value=manager),
        patch("agent.routes.chat.signal_session_gate", return_value={"status": "running"}) as signal,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        responses = list(pool.map(lambda _: send_gate(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 200]
    assert {response.json["status"] for response in responses} == {"running", "already_applied"}
    signal.assert_called_once()


def test_gate_aborts_before_signal_when_reservation_is_not_durable(client):
    session = make_session(session_id="chat-gate-save-failure", name="Gate")
    session["owner_principal"] = {"tenant_id": "test-user", "subject_id": "test-user"}
    session["process_runs"] = [
        {
            "run_id": "wf-save-failure",
            "workflow_id": "wf-save-failure",
            "control_principal": {"tenant_id": "test-user", "subject_id": "test-user"},
        }
    ]
    _, manager = _manager_for(session)
    manager.save.side_effect = lambda _values: False
    with (
        patch("agent.routes.chat.get_manager", return_value=manager),
        patch("agent.routes.chat.signal_session_gate") as signal,
    ):
        response = client.post(
            "/api/chat/sessions/chat-gate-save-failure/process/runs/wf-save-failure/gate",
            json={"step_id": "approval", "decision": "approve", "idempotency_key": "durable-key"},
            headers=_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json["error_code"] == "gate_idempotency_persistence_failed"
    signal.assert_not_called()


def test_stale_pending_gate_requires_manual_reconciliation_without_resignal(client):
    principal = ChatSessionPrincipal.from_values("test-user", "test-user")
    command = GateCommand.from_values(
        idempotency_key="stale-key",
        principal=principal,
        session_id="chat-gate-stale",
        workflow_id="wf-stale",
        run_id="wf-stale",
        step_id="approval",
        decision="approve",
    )
    action = command.action(state="pending", created_at=1.0)
    action["reconcile_after"] = 2.0
    session = make_session(session_id="chat-gate-stale", name="Gate")
    session["owner_principal"] = principal.to_dict()
    session["process_runs"] = [
        {
            "run_id": "wf-stale",
            "workflow_id": "wf-stale",
            "control_principal": principal.to_dict(),
        }
    ]
    session["process_gate_actions"] = [action]
    data, manager = _manager_for(session)
    with (
        patch("agent.routes.chat.get_manager", return_value=manager),
        patch("agent.routes.chat.signal_session_gate") as signal,
    ):
        response = client.post(
            "/api/chat/sessions/chat-gate-stale/process/runs/wf-stale/gate",
            json={"step_id": "approval", "decision": "approve", "idempotency_key": "stale-key"},
            headers=_auth_headers(),
        )

    assert response.status_code == 409
    assert response.json["error_code"] == "gate_signal_outcome_unknown"
    assert data["chat_sessions"][0]["process_gate_actions"][0]["state"] == "manual_reconcile_required"
    signal.assert_not_called()


def test_chat_workflow_id_is_tenant_and_session_bound(monkeypatch):
    fixed_uuid = MagicMock(hex="a" * 32)
    monkeypatch.setattr("agent.services.chat_process_binding.uuid.uuid4", lambda: fixed_uuid)
    tenant_a = derive_chat_workflow_id(tenant_id="tenant-a", session_id="chat", graph_id="graph")
    tenant_b = derive_chat_workflow_id(tenant_id="tenant-b", session_id="chat", graph_id="graph")
    other_session = derive_chat_workflow_id(tenant_id="tenant-a", session_id="other", graph_id="graph")
    assert tenant_a != tenant_b
    assert tenant_a != other_session
    assert tenant_a.startswith("chat-")


def test_malformed_owner_metadata_is_never_claimed_as_legacy():
    principal = ChatSessionPrincipal.from_values("tenant-a", "user-a")
    record = {"id": "damaged", "owner_principal": {"tenant_id": "", "subject_id": "user-a"}}

    authorized, migrated = authorize_owned_record(
        record,
        principal,
        legacy_default_owner=principal,
    )

    assert authorized is False
    assert migrated is False
    assert record["owner_principal"] == {"tenant_id": "", "subject_id": "user-a"}
