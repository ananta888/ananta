"""Authenticated, bodyless identity receipt route; no start, grant or source control."""

import io
from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_phase_routes import http as phase_http_fixture


@pytest.fixture
def http(monkeypatch):
    return phase_http_fixture.__wrapped__(monkeypatch)


@pytest.fixture(
    params=[
        ("meet_dialog_principals", "dialogs/task/principal"),
        ("meet_organization_principal_preflight", "tasks/task/machine-principal"),
    ]
)
def endpoint(request):
    return request.param


def test_principal_receipt_uses_explicit_user_auth_and_does_not_mutate_a_dialog(http, endpoint):
    client, app, _, runtime = http
    receipts = Mock()
    receipts.inspect.return_value = {"schema": "ananta.meet-machine-principal-receipt.v1"}
    extension, path = endpoint
    app.extensions[extension] = receipts
    url = "/api/meet/v1/projects/project/" + path
    assert client.get(url).status_code == 401
    response = client.get(url, headers={"Authorization": "Bearer synthetic-user"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    principal, project, task = receipts.inspect.call_args.args
    assert (principal.subject_id, principal.tenant_id, project, task) == ("owner", "tenant", "project", "task")
    runtime.start.assert_not_called()
    runtime.control.assert_not_called()
    runtime.inspect.assert_not_called()


@pytest.mark.parametrize("mutation", ["query", "body", "oversize", "chunked", "disabled", "worker", "post"])
def test_bad_identity_receipt_requests_never_touch_authority(http, mutation, endpoint):
    client, app, _, _ = http
    receipts = Mock()
    extension, path = endpoint
    app.extensions[extension] = receipts
    url = "/api/meet/v1/projects/project/" + path
    kwargs = {"method": "GET", "headers": {"Authorization": "Bearer synthetic-user"}}
    expected = 400
    if mutation == "query":
        url += "?subject=foreign"
    elif mutation in {"body", "oversize"}:
        kwargs["data"] = b"x" * (1 if mutation == "body" else 100000)
    elif mutation == "chunked":
        kwargs["environ_overrides"] = {"wsgi.input_terminated": True, "wsgi.input": io.BytesIO(b"x" * 100000)}
    elif mutation == "disabled":
        app.extensions.pop(extension)
        expected = 409
    elif mutation == "worker":
        app.config["ROLE"] = "worker"
        expected = 403
    else:
        kwargs["method"] = "POST"
        expected = 405
    assert client.open(url, **kwargs).status_code == expected
    receipts.inspect.assert_not_called()
