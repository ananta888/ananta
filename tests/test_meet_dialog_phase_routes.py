"""Additive phase route uses explicit headless user auth and a bodyless contract."""

from unittest.mock import Mock

import pytest

from tests.test_meet_avatar_image_routes import application


@pytest.fixture
def http(monkeypatch):
    import agent.auth as auth

    app, runtime = application()
    phases = Mock()
    phases.inspect.return_value = {"phase": "joined"}
    app.extensions["meet_dialog_phases"] = phases
    monkeypatch.setattr(
        auth,
        "_validate_user_jwt",
        lambda token: (
            {"sub": "owner", "tenant_id": "tenant", "project_id": "project", "role": "user"}
            if token == "synthetic-user"
            else None
        ),
    )
    monkeypatch.setattr(auth, "_user_token_allows_current_request", lambda _: True)
    return app.test_client(), app, phases, runtime


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_read_and_explicit_refresh_are_user_owned_without_join_or_source_activation(http, method):
    client, _, phases, runtime = http
    url = "/api/meet/v1/projects/project/dialogs/task/phase"
    assert client.open(url, method=method).status_code == 401
    response = client.open(url, method=method, headers={"Authorization": "Bearer synthetic-user"})
    assert response.status_code == 200 and response.json == {"phase": "joined"}
    assert response.headers["Cache-Control"] == "no-store"
    principal, project, task = phases.inspect.call_args.args
    assert (principal.subject_id, principal.tenant_id, project, task) == ("owner", "tenant", "project", "task")
    assert phases.inspect.call_args.kwargs == {"refresh": method == "POST"}
    runtime.start.assert_not_called()
    runtime.control.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("mutation", ["query", "body", "huge_body", "chunked", "disabled", "worker"])
def test_malformed_or_unavailable_phase_route_never_calls_observation(http, method, mutation):
    import io

    client, app, phases, _ = http
    url = "/api/meet/v1/projects/project/dialogs/task/phase"
    kwargs = {}
    if mutation == "query":
        url += "?room=foreign"
    elif mutation in {"body", "huge_body"}:
        kwargs["data"] = b"x" * (1 if mutation == "body" else 100_000)
    elif mutation == "chunked":
        kwargs["environ_overrides"] = {"wsgi.input_terminated": True, "wsgi.input": io.BytesIO(b"x" * 100_000)}
    elif mutation == "disabled":
        app.extensions.pop("meet_dialog_phases")
    else:
        app.config["ROLE"] = "worker"
    response = client.open(url, method=method, headers={"Authorization": "Bearer synthetic-user"}, **kwargs)
    assert response.status_code == (409 if mutation == "disabled" else 403 if mutation == "worker" else 400)
    phases.inspect.assert_not_called()
