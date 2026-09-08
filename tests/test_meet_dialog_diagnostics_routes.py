"""New signed callback and bodyless owner read do not broaden existing APIs."""

import io
import json
import time
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_dialog_diagnostics import request_signature, response_signature
from tests.test_meet_dialog_diagnostics_contract import request
from tests.test_meet_dialog_phase_routes import http  # noqa: F401

CALLBACK = "/api/meet/v1/internal/dialog/diagnostics"
READ = "/api/meet/v1/projects/project/dialogs/task/diagnostics"


@pytest.fixture
def diagnostic_http(http):  # noqa: F811
    client, app, _, runtime = http
    service = Mock()
    service.accept.side_effect = lambda payload: {
        "schema": "ananta.meet-dialog-diagnostics-accepted.v1", "nonce": payload["nonce"], "accepted": True
    }
    service.inspect.return_value = {"observation_status": "missing"}
    app.extensions["meet_dialog_diagnostics"] = service
    return client, app, service, runtime


def post(f, *, payload=None, raw=None, headers=None, url=CALLBACK, **kwargs):
    client, app, _, _ = f
    if raw is None:
        raw = json.dumps(request() | {"sent_at": int(time.time())} | (payload or {})).encode()
    return client.post(url, data=raw, headers={
        "Content-Type": "application/json",
        "X-Ananta-Dialog-Diagnostics-Signature": request_signature(app.extensions["meet_media_worker_key"], raw),
        **(headers or {}),
    }, **kwargs), raw


def test_signed_callback_binds_request_response_without_calling_execution(diagnostic_http):
    _, app, service, runtime = diagnostic_http
    response, raw = post(diagnostic_http)
    assert response.status_code == 200 and response.json["accepted"] is True
    assert response.headers["X-Ananta-Dialog-Diagnostics-Signature"] == response_signature(
        app.extensions["meet_media_worker_key"], raw, response.data
    )
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    service.accept.assert_called_once_with(json.loads(raw))
    assert not runtime.mock_calls


@pytest.mark.parametrize("mutation", [
    "signature", "other-domain", "auth", "query", "type", "large", "duplicate", "expired", "content", "chunked",
    "disabled", "worker",
])
def test_callback_rejects_mutation_before_admission(diagnostic_http, mutation):
    _, app, service, _ = diagnostic_http
    kwargs = {}
    if mutation == "signature":
        kwargs["headers"] = {"X-Ananta-Dialog-Diagnostics-Signature": "bad"}
    elif mutation == "other-domain":
        from ananta_contracts.meet_dialog import request_signature as normal_signature
        kwargs["raw"] = json.dumps(request() | {"sent_at": int(time.time())}).encode()
        kwargs["headers"] = {"X-Ananta-Dialog-Diagnostics-Signature": normal_signature(
            app.extensions["meet_media_worker_key"], kwargs["raw"]
        )}
    elif mutation == "auth":
        kwargs["headers"] = {"Authorization": "Bearer synthetic-user"}
    elif mutation == "query":
        kwargs["url"] = CALLBACK + "?task=foreign"
    elif mutation == "type":
        kwargs["headers"] = {"Content-Type": "text/plain"}
    elif mutation == "large":
        kwargs["raw"] = b" " * 2049
    elif mutation == "duplicate":
        kwargs["raw"] = b'{"schema":"first","schema":"second"}'
    elif mutation == "expired":
        kwargs["payload"] = {"sent_at": 1}
    elif mutation == "content":
        kwargs["payload"] = {"private_message": "forbidden"}
    elif mutation == "chunked":
        kwargs["headers"] = {"Transfer-Encoding": "chunked"}
    elif mutation == "disabled":
        app.extensions.pop("meet_dialog_diagnostics")
    else:
        app.config["ROLE"] = "worker"
    response, _ = post(diagnostic_http, **kwargs)
    assert response.status_code in {400, 401, 403, 404}
    service.accept.assert_not_called()


def test_read_uses_current_user_and_no_store_without_control_or_dispatch(diagnostic_http):
    client, _, service, runtime = diagnostic_http
    assert client.get(READ).status_code == 401
    response = client.get(READ, headers={"Authorization": "Bearer synthetic-user"})
    assert response.status_code == 200 and response.json == {"observation_status": "missing"}
    assert response.headers["Cache-Control"] == "no-store"
    principal, project, task = service.inspect.call_args.args
    assert (principal.subject_id, principal.tenant_id, project, task) == ("owner", "tenant", "project", "task")
    assert not runtime.mock_calls


@pytest.mark.parametrize("mutation", ["query", "body", "chunked", "disabled", "worker"])
def test_read_rejects_ambiguous_input_and_unavailable_service(diagnostic_http, mutation):
    client, app, service, _ = diagnostic_http
    url, kwargs = READ, {}
    if mutation == "query":
        url += "?project=foreign"
    elif mutation == "body":
        kwargs["data"] = b"unexpected"
    elif mutation == "chunked":
        kwargs["environ_overrides"] = {"wsgi.input_terminated": True, "wsgi.input": io.BytesIO(b"x" * 10000)}
    elif mutation == "disabled":
        app.extensions.pop("meet_dialog_diagnostics")
    else:
        app.config["ROLE"] = "worker"
    response = client.get(url, headers={"Authorization": "Bearer synthetic-user"}, **kwargs)
    assert response.status_code in {400, 403, 404}
    service.inspect.assert_not_called()
