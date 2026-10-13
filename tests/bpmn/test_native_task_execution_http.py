"""Synthetic signed projections and execution-only HTTP admission, no release evidence."""

import base64
import copy
import time

import pytest
from flask import Flask

from ananta_contracts.native_task_execution import (
    MAX_NATIVE_TASK_HTTP_BYTES,
    native_execution_request,
    sign_native_execution_request,
    verify_native_execution_request,
)
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint

TOKEN = "synthetic-worker-service-token-" + "x" * 32
WORKER = {"worker_id": "synthetic-worker", "worker_url": "http://synthetic-worker:8080"}


@pytest.fixture
def signed():
    signer = Ed25519SigningKeyRing({"synthetic": base64.b64encode(b"s" * 32).decode()}, active_key_id="synthetic")
    verifier = Ed25519VerificationKeyRing.from_mapping(signer.verification_mapping())
    task = {
        "id": "synthetic-task", "tenant_id": "synthetic-tenant", "task_kind": "tool_task",
        "derivation_reason": "native_graph_hub_delegation", "project_id": "synthetic-project", "input_files": [],
        "worker_execution_context": {"schema": "ananta.native_graph_worker_context.v1",
                                     "runtime_path": "native_graph_node",
                                     "native_node_command": {"tenant_id": "synthetic-tenant",
                                                             "command_id": "synthetic-command",
                                                             "node": {"task_kind": "tool_task"}}},
        "assigned_agent_token": "must-never-cross", "history": [{"private": "must-never-cross"}],
    }
    value = sign_native_execution_request(task, key_ring=signer, **WORKER, expires_at=time.time() + 30)
    return value, verifier


def test_closed_projection_is_signed_and_contains_no_task_history_or_credentials(signed):
    value, verifier = signed
    parsed = verify_native_execution_request(value, key_ring=verifier, **WORKER)
    assert parsed["id"] == "synthetic-task"
    assert "must-never-cross" not in repr(value)
    parsed["input_files"].append("mutated-copy")
    assert value["task"]["input_files"] == []
    assert not hasattr(verifier, "sign")


@pytest.mark.parametrize("field,value", [
    ("id", "foreign-task"), ("tenant_id", "foreign-tenant"), ("project_id", "foreign-project"),
    ("task_kind", "pi_coding_agent"), ("input_files", ["foreign-content"]),
    ("worker_execution_context", {}), ("history", []), ("assigned_agent_token", "forged"),
])
def test_any_projection_mutation_is_denied(signed, field, value):
    request, verifier = signed
    request["task"][field] = value
    with pytest.raises(ValueError):
        verify_native_execution_request(request, key_ring=verifier, **WORKER)


@pytest.mark.parametrize("field,value", [
    ("worker_id", "foreign-worker"), ("worker_url", "http://foreign-worker"),
    ("expires_at", 1), ("expires_at", True), ("expires_at", float("nan")),
    ("expires_at", float("inf")), ("key_id", "unknown"), ("signature", "forged"), ("extra", {}),
])
def test_recipient_expiry_key_and_contract_mutation_are_denied(signed, field, value):
    request, verifier = signed
    request[field] = value
    with pytest.raises(ValueError):
        verify_native_execution_request(request, key_ring=verifier, **WORKER)


def test_unsigned_projection_and_missing_verification_authority_are_denied(signed):
    value, verifier = signed
    unsigned = native_execution_request(value["task"])
    with pytest.raises(ValueError, match="signed_request_required"):
        verify_native_execution_request(unsigned, key_ring=verifier, **WORKER)
    with pytest.raises(ValueError, match="public_verification_required"):
        verify_native_execution_request(value, key_ring=None, **WORKER)


@pytest.fixture
def http(signed):
    value, verifier = signed
    calls = []
    app = Flask(__name__)
    app.config["ROLE"] = "worker"

    def consume(task):
        calls.append(copy.deepcopy(task))
        return {"status": "completed", "synthetic": True}

    app.register_blueprint(create_native_execution_blueprint(
        token_provider=lambda: TOKEN, recipient_provider=lambda: (WORKER["worker_id"], WORKER["worker_url"]),
        verification_provider=lambda: verifier, consumer=consume,
    ))
    return app, value, calls


def test_authenticated_signed_http_projection_reaches_consumer_only(http):
    app, value, calls = http
    response = app.test_client().post("/api/internal/native-tasks/execute", json=value,
                                      headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert calls == [value["task"]]


@pytest.mark.parametrize("mutation,expected", [
    ("no_auth", 401), ("wrong_auth", 401), ("unicode_auth", 401), ("hub_role", 403), ("unsigned", 422),
    ("tampered", 422), ("oversized", 413), ("non_json", 400),
])
def test_denied_http_call_never_enters_consumer(http, mutation, expected):
    app, value, calls = http
    headers = {"Authorization": "Bearer " + TOKEN}
    arguments = {"json": value}
    if mutation == "no_auth":
        headers = {}
    elif mutation == "wrong_auth":
        headers["Authorization"] = "Bearer invalid"
    elif mutation == "unicode_auth":
        headers["Authorization"] = "Bearer üngültig"
    elif mutation == "hub_role":
        app.config["ROLE"] = "hub"
    elif mutation == "unsigned":
        arguments["json"] = native_execution_request(value["task"])
    elif mutation == "tampered":
        value["task"]["project_id"] = "foreign-project"
    elif mutation == "oversized":
        arguments = {"data": "x" * (MAX_NATIVE_TASK_HTTP_BYTES + 1), "content_type": "application/json"}
    elif mutation == "non_json":
        arguments = {"data": "not-json"}
    response = app.test_client().post("/api/internal/native-tasks/execute", headers=headers, **arguments)
    assert response.status_code == expected
    assert calls == []

