from __future__ import annotations

import io
import json
from urllib import error

from client_surfaces.freecad.workbench.client import FreecadHubClient
from client_surfaces.freecad.workbench.http_transport import HttpJsonTransport
from client_surfaces.freecad.workbench.settings import FreecadWorkbenchSettings


class FakeHttpResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeOpener:
    def __init__(self, responses: list[FakeHttpResponse] | None = None, *, http_error: error.HTTPError | None = None) -> None:
        self.responses = list(responses or [])
        self.http_error = http_error
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        if self.http_error is not None:
            raise self.http_error
        return self.responses.pop(0)


def test_http_transport_sends_profile_and_auth_headers() -> None:
    opener = FakeOpener([FakeHttpResponse({"status": "connected"})])
    settings = FreecadWorkbenchSettings(
        endpoint="https://hub.local",
        token="secret-token",
        profile="freecad",
        transport_mode="http",
    )
    client = FreecadHubClient.with_http_transport(settings, opener=opener)

    response = client.health()

    request_obj, timeout = opener.requests[0]
    assert response["status"] == "connected"
    assert request_obj.full_url.endswith("/api/client-surfaces/freecad/health")
    assert request_obj.get_header("Authorization") == "Bearer secret-token"
    assert request_obj.get_header("X-ananta-profile") == "freecad"
    assert timeout == settings.request_timeout_seconds


def test_http_transport_posts_json_payloads() -> None:
    opener = FakeOpener([FakeHttpResponse({"status": "accepted", "task_id": "fc-2"})])
    settings = FreecadWorkbenchSettings(endpoint="https://hub.local", transport_mode="http")
    client = FreecadHubClient.with_http_transport(settings, opener=opener)

    response = client.submit_goal(goal="Inspect", context={"document": {"name": "Doc"}}, capability_id="freecad.model.inspect")

    request_obj, _timeout = opener.requests[0]
    body = json.loads(request_obj.data.decode("utf-8"))
    assert response["task_id"] == "fc-2"
    assert request_obj.get_method() == "POST"
    assert body["goal"] == "Inspect"
    assert body["capability_id"] == "freecad.model.inspect"


def test_http_transport_maps_http_error_to_structured_status() -> None:
    http_error = error.HTTPError(
        url="https://hub.local/api/client-surfaces/freecad/approvals/decision",
        code=409,
        msg="Conflict",
        hdrs=None,
        fp=io.BytesIO(json.dumps({"reason": "approval_required"}).encode("utf-8")),
    )
    opener = FakeOpener(http_error=http_error)
    transport = HttpJsonTransport(FreecadWorkbenchSettings(endpoint="https://hub.local", transport_mode="http"), opener=opener)

    response = transport.send("approval_decision", {"approval_id": "APR-1", "decision": "approve"})

    assert response["status"] == "approval_required"
    assert response["http_status"] == 409
