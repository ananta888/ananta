from __future__ import annotations

import gzip
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent.services.opaque_secret_reference_service import OpaqueSecretReferenceService
from agent.services.unsloth_studio_transport import (
    MAX_CONNECT_TIMEOUT_SECONDS,
    MAX_DECOMPRESSED_RESPONSE_BYTES,
    MAX_TOTAL_TIMEOUT_SECONDS,
    UnslothStudioHttpResponse,
    UnslothStudioTransport,
    UnslothStudioTransportConfig,
    UnslothStudioTransportError,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "unsloth_studio"
_PASSWORD = "studio-test-password-123"
_MCP_TOKEN = "studio-test-mcp-token-123"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class _StudioHandler(BaseHTTPRequestHandler):
    authorizations: list[tuple[str, str]] = []
    login_payloads: list[dict[str, Any]] = []
    refresh_payloads: list[dict[str, Any]] = []
    reject_health_once = False

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
        payload = json.loads(body or b"{}")
        if self.path == "/api/auth/login":
            type(self).login_payloads.append(payload)
            self._json_response(_fixture("login.v1.json"))
            return
        if self.path == "/api/auth/refresh":
            type(self).refresh_payloads.append(payload)
            rotated = _fixture("login.v1.json")
            rotated["access_token"] = "studio-rotated-access-token"
            rotated["refresh_token"] = "studio-rotated-refresh-token"
            self._json_response(rotated)
            return
        self._json_response({"jsonrpc": "2.0", "id": "test", "result": {}}, 200)

    def do_GET(self) -> None:
        type(self).authorizations.append(
            (self.path, str(self.headers.get("Authorization") or ""))
        )
        if self.path == "/api/auth/status":
            self._json_response(
                {
                    "initialized": True,
                    "default_username": "unsloth",
                    "requires_password_change": False,
                }
            )
            return
        if self.path == "/api/health":
            if type(self).reject_health_once:
                type(self).reject_health_once = False
                self._json_response({"detail": "expired"}, 401)
                return
            self._json_response(_fixture("health.v1.json"))
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/api/health")
            self.end_headers()
            return
        if self.path == "/gzip-bomb":
            body = gzip.compress(b"x" * (MAX_DECOMPRESSED_RESPONSE_BYTES + 1))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json_response({"status": "ready"})

    def _json_response(self, value: Mapping[str, Any], status: int = 200) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _config(port: int) -> UnslothStudioTransportConfig:
    return UnslothStudioTransportConfig(
        base_url=f"http://studio.test:{port}",
        credential_secret_ref="env://UNSLOTH_TEST_PASSWORD",
        expected_studio_version=str(_fixture("health.v1.json")["studio_version"]),
        allowed_hosts=("studio.test",),
        allowed_ip_cidrs=("127.0.0.0/8",),
        local_network_enabled=True,
        allow_plaintext_internal=True,
    )


def _transport(port: int, **kwargs: Any) -> UnslothStudioTransport:
    return UnslothStudioTransport(
        config=_config(port),
        resolver=lambda _host, _port: ("127.0.0.1",),
        secret_resolver=OpaqueSecretReferenceService(
            {
                "UNSLOTH_TEST_PASSWORD": _PASSWORD,
                "UNSLOTH_TEST_MCP_TOKEN": _MCP_TOKEN,
            }
        ),
        **kwargs,
    )


def _server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    _StudioHandler.authorizations = []
    _StudioHandler.login_payloads = []
    _StudioHandler.refresh_payloads = []
    _StudioHandler.reject_health_once = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StudioHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_probe_logs_in_with_password_ref_and_validates_pinned_contract() -> None:
    server, thread = _server()
    try:
        payload = _transport(server.server_port).probe()
        login = _fixture("login.v1.json")
        assert payload["available"] is True
        assert payload["studio_version"] == _fixture("health.v1.json")["studio_version"]
        assert _StudioHandler.login_payloads == [
            {"username": "unsloth", "password": _PASSWORD}
        ]
        assert _StudioHandler.authorizations == [
            ("/api/auth/status", f"Bearer {login['access_token']}"),
            ("/api/health", f"Bearer {login['access_token']}"),
        ]
        assert _config(server.server_port).connect_timeout_seconds == MAX_CONNECT_TIMEOUT_SECONDS
        assert _config(server.server_port).total_timeout_seconds == MAX_TOTAL_TIMEOUT_SECONDS
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_transport_refreshes_rotated_single_use_token_once_after_401() -> None:
    server, thread = _server()
    _StudioHandler.reject_health_once = True
    try:
        transport = _transport(server.server_port)
        payload = transport.request_json(method="GET", path="/api/health")
        assert payload["status"] == "healthy"
        assert _StudioHandler.refresh_payloads == [
            {"refresh_token": _fixture("login.v1.json")["refresh_token"]}
        ]
        assert _StudioHandler.authorizations[-1] == (
            "/api/health",
            "Bearer studio-rotated-access-token",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_transport_rejects_redirects_and_decompressed_bodies_over_one_mib() -> None:
    server, thread = _server()
    transport = _transport(server.server_port)
    try:
        with pytest.raises(
            UnslothStudioTransportError,
            match="unsloth_studio_redirect_forbidden",
        ):
            transport.request_json(method="GET", path="/redirect")
        with pytest.raises(
            UnslothStudioTransportError,
            match="unsloth_studio_response_too_large",
        ):
            transport.request_json(method="GET", path="/gzip-bomb")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _QueuedAdapter:
    def __init__(self, results: list[Any]) -> None:
        self._results = deque(results)
        self.requests: list[Any] = []

    def send(self, request: Any) -> UnslothStudioHttpResponse:
        self.requests.append(request)
        value = self._results.popleft()
        if isinstance(value, Exception):
            raise value
        return value


def _response(value: Mapping[str, Any]) -> UnslothStudioHttpResponse:
    return UnslothStudioHttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(value, separators=(",", ":")).encode("utf-8"),
    )


def test_transport_retries_only_idempotent_gets_after_authentication() -> None:
    transient = UnslothStudioTransportError(
        "unsloth_studio_connection_failed",
        retryable=True,
    )
    login = _response(_fixture("login.v1.json"))
    get_adapter = _QueuedAdapter([login, transient, _response({"ok": True})])
    get_transport = _transport(
        8888,
        adapter=get_adapter,
        sleep=lambda _delay: None,
    )
    assert get_transport.request_json(method="GET", path="/api/health") == {"ok": True}
    assert len(get_adapter.requests) == 3

    post_adapter = _QueuedAdapter([transient, _response({"ok": True})])
    post_transport = _transport(
        8888,
        adapter=post_adapter,
        sleep=lambda _delay: None,
    )
    with pytest.raises(UnslothStudioTransportError):
        post_transport.request_json(
            method="POST",
            path="/mcp/",
            payload={"method": "tools/call"},
            service_bearer_secret_ref="env://UNSLOTH_TEST_MCP_TOKEN",
        )
    assert len(post_adapter.requests) == 1


def test_transport_fails_closed_when_resolved_ip_is_not_allowlisted() -> None:
    config = UnslothStudioTransportConfig(
        base_url="https://studio.test",
        credential_secret_ref="env://UNSLOTH_TEST_PASSWORD",
        expected_studio_version="2026.7.0",
        allowed_hosts=("studio.test",),
        allowed_ip_cidrs=("192.0.2.0/24",),
        external_network_enabled=True,
    )
    with pytest.raises(
        UnslothStudioTransportError,
        match="unsloth_studio_endpoint_address_forbidden",
    ):
        UnslothStudioTransport(
            config=config,
            resolver=lambda _host, _port: ("198.51.100.7",),
            secret_resolver=OpaqueSecretReferenceService(
                {"UNSLOTH_TEST_PASSWORD": _PASSWORD}
            ),
        )


def test_dns_resolution_is_inside_the_transport_total_deadline() -> None:
    release_lookup = threading.Event()
    lookup_calls = 0

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return ("127.0.0.1",)
        release_lookup.wait(timeout=2)
        return ("127.0.0.1",)

    config = UnslothStudioTransportConfig(
        base_url="http://studio.test:8888",
        credential_secret_ref="env://UNSLOTH_TEST_PASSWORD",
        expected_studio_version="2026.7.0",
        allowed_hosts=("studio.test",),
        allowed_ip_cidrs=("127.0.0.0/8",),
        local_network_enabled=True,
        allow_plaintext_internal=True,
        connect_timeout_seconds=0.05,
        total_timeout_seconds=0.05,
    )
    transport = UnslothStudioTransport(
        config=config,
        resolver=resolver,
        secret_resolver=OpaqueSecretReferenceService(
            {"UNSLOTH_TEST_PASSWORD": _PASSWORD}
        ),
    )
    started = time.monotonic()
    try:
        with pytest.raises(
            UnslothStudioTransportError,
            match="unsloth_studio_endpoint_dns_timeout",
        ):
            transport.request_json(method="GET", path="/api/health")
        assert time.monotonic() - started < 0.5
    finally:
        release_lookup.set()
