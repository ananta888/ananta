"""Current Hub callbacks authenticate exact request bytes and never accept bearer authority."""
import time
from unittest.mock import Mock

from flask import Flask

from ananta_contracts.meet_dialog import request_signature, response_signature
from worker.meet_media.contract import encode
from agent.routes.meet import meet_bp


def test_callback_closed_hmac_protocol_and_request_bound_response():
    app = Flask(__name__); app.config.update(TESTING=True, ROLE="hub"); app.register_blueprint(meet_bp)
    key = b"synthetic-test-key" * 2; runtime = Mock()
    app.extensions.update(meet_binding_service=Mock(), meet_dialog_service=runtime, meet_media_worker_key=key)
    value = {"schema": "ananta.meet-dialog-callback.v1", "action": "exchange", "task_id": "task", "lease_id": "dispatch",
             "runtime_id": "runtime", "nonce": "a" * 32, "sent_at": int(time.time()), "meet_session_id": "ms_" + "a" * 32}
    runtime.exchange.return_value = {"schema": "ananta.meet-dialog-state.v1", "nonce": value["nonce"],
                                     "authorization": {}, "renewal": None, "audio_job": None}
    client = app.test_client(); body = encode(value)
    def post(raw, **headers):
        return client.post("/api/meet/v1/internal/dialog", data=raw, headers={"Content-Type": "application/json",
            "X-Ananta-Dialog-Signature": request_signature(key, raw), **headers})
    response = post(body)
    assert response.status_code == 200
    assert response.headers["X-Ananta-Dialog-Signature"] == response_signature(key, body, response.data)
    assert response.headers["Cache-Control"] == "no-store"
    runtime.exchange.assert_called_once_with(value)
    for raw in (encode(value | {"allowed": True}), encode(value | {"action": "start"}),
                encode(value | {"sent_at": value["sent_at"] - 20}), body[:-1] + b',"action":"exchange"}'):
        assert post(raw).status_code == 400
    assert post(body, Authorization="Bearer service-token").status_code == 403
    assert post(body, **{"X-Ananta-Dialog-Signature": "forged"}).status_code == 401
    runtime.exchange.assert_called_once()


def test_task_authorization_uses_explicit_owner_not_internal_http_identity(monkeypatch):
    from agent.bootstrap.meet import _task_access
    from agent.services.source_control_access_policy import HubSourcePrincipal
    from agent.services import repository_registry
    app = Flask(__name__); guard = Mock()
    app.extensions.update(task_read_access_service=guard, project_access_authority=Mock(), organization_membership_service=Mock())
    registry = Mock(); task = Mock(project_id="project", tenant_id="tenant", archived=False)
    task.model_dump.return_value = {"id": "task", "tenant_id": "tenant", "project_id": "project"}
    registry.task_repo.get_by_id.return_value = task
    monkeypatch.setattr(repository_registry, "get_repository_registry", lambda: registry)
    principal = HubSourcePrincipal("owner", "tenant", "project", frozenset({"user"}))
    with app.test_request_context("/internal/dialog"):
        _task_access(principal, "project", "task")
    assert guard.require.call_args.kwargs["principal"] is principal
