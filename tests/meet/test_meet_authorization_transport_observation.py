"""Fixture observer preserves the transport contract and never exports secrets."""

import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from tests.meet_authorization_transport_observation import observe_authorization_transport


def test_observer_preserves_response_and_bounds_status_only_history(monkeypatch):
    response = SimpleNamespace(status=200)
    calls = []
    delegate = SimpleNamespace(open=lambda *args, **kwargs: calls.append((args, kwargs)) or response)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args, **_kwargs: delegate)
    rows = []
    observe_authorization_transport(monkeypatch, rows)
    opener = urllib.request.build_opener()
    request = urllib.request.Request("https://private.invalid/api/machine/sessions/authorization",
                                     headers={"Authorization": "Bearer private-token"})
    for _ in range(50):
        assert opener.open(request, timeout=3) is response
    assert len(rows) == 32
    assert rows == [{"kind": "response", "status": 200}] * 32
    assert calls[0] == ((request,), {"timeout": 3})


def test_observer_preserves_http_exception_without_body_or_url(monkeypatch):
    error = urllib.error.HTTPError("https://private.invalid", 429, "private-token", {}, None)

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args, **_kwargs: SimpleNamespace(open=fail))
    rows = []
    observe_authorization_transport(monkeypatch, rows)
    with pytest.raises(urllib.error.HTTPError) as captured:
        urllib.request.build_opener().open(urllib.request.Request(
            "https://private.invalid/api/machine/sessions/authorization"))
    assert captured.value is error
    assert rows == [{"kind": "HTTPError", "status": 429}]
