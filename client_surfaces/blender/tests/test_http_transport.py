from __future__ import annotations

import io
import json
from urllib import error

from client_surfaces.blender.addon.client import BlenderHubClient, HttpJsonTransport
from client_surfaces.blender.addon.settings import BlenderAddonSettings


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
    opener = FakeOpener([FakeHttpResponse({"status": "success", "data": {"status": "connected"}})])
    settings = BlenderAddonSettings(
        endpoint="https://hub.local",
        token="secret-token",
        profile="blender",
        transport_mode="http",
    )
    client = BlenderHubClient.with_http_transport(settings, opener=opener)

    response = client.health()

    request_obj, timeout = opener.requests[0]
    assert response["status"] == "connected"
    assert request_obj.full_url.endswith("/api/client-surfaces/blender/health")
    assert request_obj.get_header("Authorization") == "Bearer secret-token"
    assert request_obj.get_header("X-ananta-profile") == "blender"
    assert timeout == settings.request_timeout_seconds


def test_http_transport_maps_approval_required_error() -> None:
    http_error = error.HTTPError(
        url="https://hub.local/api/client-surfaces/blender/executions",
        code=409,
        msg="Conflict",
        hdrs=None,
        fp=io.BytesIO(json.dumps({"reason": "approval_required"}).encode("utf-8")),
    )
    transport = HttpJsonTransport(BlenderAddonSettings(endpoint="https://hub.local", transport_mode="http"), opener=FakeOpener(http_error=http_error))

    response = transport.send("execute_action", {"action": "rename"})

    assert response["status"] == "approval_required"
    assert response["http_status"] == 409
