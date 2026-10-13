"""Actual signed model lifecycle HTTP/SQL; explicit synthetic model fixtures."""

import time
from copy import deepcopy

import pytest
from flask import g, request

from agent.bootstrap.meet_companion import configure_companion_model_runs
from ananta_contracts.meet_companion_task_model import (
    REQUEST_SCHEMA,
    SIGNATURE_HEADER,
    request_signature,
    response_signature,
)
from tests.meet.test_meet_companion_model_runs import generated, state, value
from tests.meet.test_meet_companion_model_runs import models as models
from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grant_client import assignment
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import KEY
from tests.meet.test_meet_companion_task_tools import hub as hub
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.companion_task_model_client import AssignedCompanionModelClient
from worker.meet_media.contract import encode

PATH = "/api/meet/v1/internal/companion/task-model"
pytestmark = pytest.mark.timeout(15)


@pytest.fixture
def http(models):
    f, service = models
    f.app.extensions["meet_companion_model_runs"] = service
    return f


def packet(f, action="reserve", payload=None):
    return {"schema": REQUEST_SCHEMA, "nonce": "c" * 32, "sent_at": int(time.time()),
            "assignment": deepcopy(f.request["assignment"]), "action": action,
            "payload": value() if payload is None else payload}


def post(f, value=None, *, headers=None, path=PATH):
    raw = encode(packet(f) if value is None else value)
    return f.app.test_client().post(path, data=raw, headers={"Content-Type": "application/json",
        SIGNATURE_HEADER: request_signature(KEY, raw), **(headers or {})})


def client(f, endpoint):
    full = assignment() | {"session_id": "hub-session", "deadline": f.f.context["deadline"]}
    return AssignedCompanionModelClient(AssignedCompanionGrantClient(full, endpoint, KEY))


def test_actual_worker_http_client_reserves_and_finishes_exact_model_result(http):
    with running_hub(http.app) as endpoint:
        worker = client(http, endpoint)
        reserved = worker.call("reserve", value())
        assert state(http, reserved["run"]["run_id"])["state"] == "reserved"
        finished = worker.call("finish", {"run": reserved["run"], "input_source": reserved["input_source"],
                                           "result": generated()})
        assert finished["run"] == reserved["run"]
        assert state(http, reserved["run"]["run_id"])["state"] == "succeeded"


@pytest.mark.parametrize("bad", ["signature", "query", "authorization", "assignment", "unknown_action", "stale"])
def test_untrusted_request_does_not_reserve_any_run(http, bad):
    request, headers, path = packet(http), {}, PATH
    if bad == "signature":
        headers[SIGNATURE_HEADER] = "f" * 64
    elif bad == "query":
        path += "?fallback=legacy"
    elif bad == "authorization":
        headers["Authorization"] = "Bearer synthetic"
    elif bad == "assignment":
        request["assignment"]["lease_id"] = "foreign"
    elif bad == "unknown_action":
        request["action"] = "issue-evidence"
    else:
        request["sent_at"] -= 11
    response = post(http, request, headers=headers, path=path)
    assert response.status_code == (401 if bad == "signature" else 403)
    assert "run" not in response.get_json()


@pytest.mark.parametrize("bad", ["unsigned", "foreign_result", "foreign_run"])
def test_worker_rejects_modified_or_unsigned_finish_response(http, bad):
    @http.app.before_request
    def remember_exact_request():
        g.model_request_bytes = request.get_data()

    @http.app.after_request
    def alter(response):
        body = response.get_json(silent=True)
        if body and body.get("schema") == "ananta.meet-companion-model-completed.v1":
            if bad == "unsigned":
                response.headers.pop(SIGNATURE_HEADER, None)
            else:
                body["result_digest" if bad == "foreign_result" else "run"] = (
                    "f" * 64 if bad == "foreign_result" else {}
                )
                response.set_data(encode(body))
                # A valid response signature is insufficient when its result
                # digest or closed assignment does not match this execution.
                response.headers[SIGNATURE_HEADER] = response_signature(KEY, g.model_request_bytes, response.data)
        return response

    with running_hub(http.app) as endpoint:
        worker = client(http, endpoint)
        reserved = worker.call("reserve", value())
        with pytest.raises(ValueError, match="unavailable_or_invalid"):
            worker.call("finish", {"run": reserved["run"], "input_source": reserved["input_source"],
                                   "result": generated()})


def test_withdrawn_task_result_never_passes_actual_http_route(http):
    with running_hub(http.app) as endpoint:
        worker = client(http, endpoint)
        reserved = worker.call("reserve", value())
        http.f.task.status = "cancelled"
        with pytest.raises(ValueError, match="unavailable_or_invalid"):
            worker.call("finish", {"run": reserved["run"], "input_source": reserved["input_source"],
                                   "result": generated()})
        assert state(http, reserved["run"]["run_id"])["state"] != "succeeded"


def test_model_bootstrap_is_opt_in_and_requires_current_hub_tools(models, monkeypatch):
    f, _service = models
    monkeypatch.delenv("ANANTA_MEET_COMPANION_MODEL_RUNS_ENABLED", raising=False)
    tools = f.app.extensions["meet_companion_task_tools"]
    configure_companion_model_runs(f.app, f.f.authority, tools, f.database)
    assert "meet_companion_model_runs" not in f.app.extensions
    f.app.config["ANANTA_MEET_COMPANION_MODEL_RUNS_ENABLED"] = True
    configure_companion_model_runs(f.app, f.f.authority, tools, f.database)
    assert "meet_companion_model_runs" in f.app.extensions
    f.app.config["ROLE"] = "worker"
    with pytest.raises(ValueError, match="hub_required"):
        configure_companion_model_runs(f.app, f.f.authority, tools, f.database)


def test_disabled_or_worker_model_route_does_not_provide_fallback(http):
    http.app.config["ROLE"] = "worker"
    assert post(http).status_code == 403
    http.app.config["ROLE"] = "hub"
    http.app.extensions.pop("meet_companion_model_runs")
    assert post(http).status_code == 404
