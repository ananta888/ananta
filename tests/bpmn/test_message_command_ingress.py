"""Authenticated, signed message ingress with synthetic recording execution."""

import copy
import time

import pytest

from agent.auth import generate_token
from agent.config import settings
from agent.services.workflow_control_command_receipts import admitted_receipt_command
from tests.bpmn.test_workflow_preflight import preflight_setup as preflight_setup
from tests.test_workflow_control_composition import _request


@pytest.fixture
def message_setup(preflight_setup):
    s = preflight_setup
    request = _request("message-ingress-test")
    s["ownership"].reserve(request.workflow_id, s["owner"])
    status = s["bound"].start_workflow(request)
    s["initial_status"] = status
    now = time.time()
    s["url"] = f"/api/visual-process/workflow/{request.workflow_id}/message"
    s["message"] = {
        "command_id": "message-command-1",
        "expected_revision": status["revision"],
        "plan_hash": status["plan_hash"],
        "step_id": "step-1",
        "payload": {
            "activation_id": "activation-1",
            "message_id": "message-1",
            "name": "order",
            "correlation_key": "order-1",
            "schema_id": "order-v1",
            "sent_at": now - 1,
            "expires_at": now + 60,
            "payload": {"value": 42},
        },
    }
    return s


def post(s, body=None, headers=None):
    return s["client"].post(
        s["url"], json=s["message"] if body is None else body, headers=s["headers"] if headers is None else headers
    )


def test_message_is_signed_receipted_and_replayed_once(message_setup):
    s = message_setup
    response = post(s)
    assert response.status_code == 200, response.get_json()
    receipt = s["bound"]._command_receipts.get(s["message"]["command_id"])
    command = admitted_receipt_command(receipt)
    assert command.command_type == "bpmn_message"
    assert command.command_id == s["message"]["command_id"]
    assert command.expected_revision == s["message"]["expected_revision"]
    assert command.plan_hash == s["message"]["plan_hash"]
    assert command.tenant_id == s["owner"].tenant_id
    assert command.actor_id == s["owner"].subject
    assert command.step_id == s["message"]["step_id"]
    assert command.payload == s["message"]["payload"]
    assert command.signature
    assert receipt.state == "completed"
    assert post(s).status_code == 200
    assert len(s["backend"].signals) == 1
    assert s["backend"].signals[0].name == "bpmn_message"


@pytest.mark.parametrize("change", ["revision", "target", "data"])
def test_changed_retry_identity_conflicts(message_setup, change):
    s = message_setup
    assert post(s).status_code == 200
    body = copy.deepcopy(s["message"])
    if change == "revision":
        body["expected_revision"] += 1
    elif change == "target":
        body["step_id"] = "other-step"
    else:
        body["payload"]["payload"]["value"] = 43
    response = post(s, body)
    assert response.status_code == 409
    assert "workflow_control_command_id_conflict" in response.get_data(as_text=True)
    assert len(s["backend"].signals) == 1


@pytest.mark.parametrize("field,value", [("expected_revision", 55), ("plan_hash", "0" * 64), ("step_id", "wrong-step")])
def test_stale_plan_revision_and_wrong_target_cannot_dispatch(message_setup, field, value):
    s = message_setup
    body = copy.deepcopy(s["message"])
    body[field] = value
    response = post(s, body)
    assert response.status_code == 409, response.get_json()
    assert s["backend"].signals == []
    assert s["bound"]._command_receipts.get(body["command_id"]) is None


def test_cross_tenant_and_anonymous_messages_are_denied(message_setup):
    s = message_setup
    assert post(s, headers={}).status_code == 401
    token = generate_token({"sub": s["owner"].subject, "tenant_id": "foreign", "role": "user"}, settings.secret_key)
    assert post(s, headers={"Authorization": f"Bearer {token}"}).status_code == 404
    assert s["backend"].signals == []


@pytest.mark.parametrize("field", ["tenant_id", "plan_hash", "actor_roles", "approval", "tools"])
def test_message_payload_cannot_add_authority_fields(message_setup, field):
    s = message_setup
    body = copy.deepcopy(s["message"])
    body["payload"][field] = "forged"
    assert post(s, body).status_code == 422
    assert s["backend"].signals == []


@pytest.mark.parametrize(
    "field,value",
    [("expected_revision", True), ("expected_revision", -1), ("command_id", ""), ("step_id", ""), ("payload", [])],
)
def test_message_requires_closed_explicit_envelope(message_setup, field, value):
    s = message_setup
    body = copy.deepcopy(s["message"])
    body[field] = value
    assert post(s, body).status_code == 422
    assert s["backend"].signals == []


def test_business_fields_do_not_become_approval_authority(message_setup):
    s = message_setup
    body = copy.deepcopy(s["message"])
    body["payload"]["payload"] = {"approved": True, "roles": ["admin"]}
    assert post(s, body).status_code == 200
    signal = s["backend"].signals[0]
    assert signal.name == "bpmn_message"
    assert signal.payload == body["payload"]
    command = admitted_receipt_command(s["bound"]._command_receipts.get(body["command_id"]))
    assert command.actor_roles == s["owner"].roles


def test_generic_signal_cannot_bypass_message_envelope(message_setup):
    s = message_setup
    response = s["client"].post(
        s["url"].removesuffix("message") + "signal",
        headers=s["headers"],
        json={"name": "bpmn_message", "payload": s["message"]["payload"]},
    )
    assert response.status_code == 422
    assert s["backend"].signals == []


@pytest.mark.parametrize("endpoint", ["signal", "cancel", "resume", "retry"])
@pytest.mark.parametrize("revision,code", [(55, 409), (None, 422)])
def test_existing_command_routes_never_substitute_a_supplied_revision(message_setup, endpoint, revision, code):
    s = message_setup
    response = s["client"].post(
        s["url"].removesuffix("message") + endpoint,
        headers=s["headers"],
        json={"name": "pause", "expected_revision": revision},
    )
    assert response.status_code == code, response.get_json()
    assert s["backend"].signals == []
    assert s["backend"].cancels == 0


@pytest.mark.parametrize("endpoint", ["cancel", "resume", "retry"])
@pytest.mark.parametrize("field", ["run_id", "checkpoint_ref"])
def test_ui_control_binding_mismatch_is_rejected(message_setup, endpoint, field):
    s = message_setup
    bindings = {key: s["initial_status"][key] for key in ("plan_hash", "run_id", "checkpoint_ref")}
    bindings["expected_revision"] = s["initial_status"]["revision"]
    bindings[field] = "foreign"
    if endpoint == "cancel":
        body = bindings
    else:
        body = {
            "expected_revision": bindings.pop("expected_revision"),
            "plan_hash": bindings.pop("plan_hash"),
            "payload": bindings,
        }
    response = s["client"].post(s["url"].removesuffix("message") + endpoint, json=body, headers=s["headers"])
    assert response.status_code == 409, response.get_json()
    assert s["backend"].signals == []
    assert s["backend"].cancels == 0


def test_message_preserves_run_checkpoint_and_rejects_changed_retry(message_setup):
    s = message_setup
    body = {
        **s["message"],
        "run_id": s["initial_status"]["run_id"],
        "checkpoint_ref": s["initial_status"]["checkpoint_ref"],
    }
    assert post(s, body).status_code == 200
    receipt = s["bound"]._command_receipts.get(body["command_id"])
    command = admitted_receipt_command(receipt)
    assert command.run_id == body["run_id"]
    assert command.checkpoint_id == body["checkpoint_ref"]
    assert post(s, body).status_code == 200
    body["checkpoint_ref"] = "changed"
    assert post(s, body).status_code == 409
    assert len(s["backend"].signals) == 1
