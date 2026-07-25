from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.services.jmap_auth_service import (
    JmapAuthService,
    JmapOAuthAccessToken,
)
from agent.services.jmap_discovery_service import (
    JmapDiscoveryCache,
    JmapDiscoveryService,
    JmapLifecycleService,
    JmapSessionRegistry,
)
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyConfig
from agent.services.jmap_http_transport import (
    JmapHttpResponse,
    JmapHttpTransport,
    JmapTransportError,
)
from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_feature_policy import MailFeaturePolicy
from agent.services.mail_provider_ports import MailAuthMaterial, MailProviderResult


class _RedirectHandler(BaseHTTPRequestHandler):
    redirect_port = 0
    hosts: list[str] = []

    def do_GET(self) -> None:
        type(self).hosts.append(str(self.headers.get("Host") or ""))
        if self.path == "/start":
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://redirect.test:{type(self).redirect_port}/final",
            )
            self.end_headers()
            return
        body = b'{"pinned":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_transport_connects_to_validated_ip_and_revalidates_redirect_hops() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    _RedirectHandler.redirect_port = server.server_port
    _RedirectHandler.hosts = []
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    resolver_calls: list[tuple[str, int]] = []

    def resolver(host: str, port: int):
        resolver_calls.append((host, port))
        return ("127.0.0.1",)

    try:
        policy = JmapEndpointPolicy(
            config=JmapEndpointPolicyConfig(
                local_endpoints_enabled=True,
                allowed_local_hosts=("rebind.test", "redirect.test"),
                allowed_local_cidrs=("127.0.0.0/8",),
                allowed_related_origins=(f"http://redirect.test:{server.server_port}",),
            ),
            resolver=resolver,
        )
        transport = JmapHttpTransport(endpoint_policy=policy)
        payload, response = transport.request_json(
            method="GET",
            url=f"http://rebind.test:{server.server_port}/start",
            purpose="session",
            allow_redirects=True,
        )
        assert payload == {"pinned": True}
        assert response.final_url == f"http://redirect.test:{server.server_port}/final"
        assert {host for host, _port in resolver_calls} == {"rebind.test", "redirect.test"}
        assert resolver_calls.count(("rebind.test", server.server_port)) >= 2
        assert resolver_calls.count(("redirect.test", server.server_port)) >= 2
        assert _RedirectHandler.hosts == [
            f"rebind.test:{server.server_port}",
            f"redirect.test:{server.server_port}",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _NeverAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, _request):
        self.calls += 1
        raise AssertionError("adapter must not receive a rebound endpoint")


def test_dns_change_between_validation_and_connect_fails_closed() -> None:
    answers = deque([("93.184.216.34",), ("127.0.0.1",)])
    adapter = _NeverAdapter()
    policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: answers.popleft(),
    )
    transport = JmapHttpTransport(endpoint_policy=policy, adapter=adapter)
    with pytest.raises(JmapTransportError, match="jmap_endpoint_address_forbidden"):
        transport.request_json(
            method="GET",
            url="https://mail.example.test/.well-known/jmap",
            purpose="session",
        )
    assert adapter.calls == 0


class _OAuthAdapter:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def acquire_access_token(self, **values):
        force = bool(values["force_refresh"])
        self.calls.append(force)
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=JmapOAuthAccessToken(
                access_token="rotated-token" if force else "stale-token"
            ),
        )


class _QueuedAdapter:
    def __init__(self, responses) -> None:
        self.responses = deque(responses)
        self.authorization: list[str] = []

    def send(self, request):
        self.authorization.append(str(request.headers.get("Authorization") or ""))
        response = self.responses.popleft()
        return JmapHttpResponse(
            status_code=response.status_code,
            headers=response.headers,
            body=response.body,
            final_url=request.url,
        )


def _session_response(state: str) -> JmapHttpResponse:
    payload = {
        "capabilities": {
            "urn:ietf:params:jmap:core": {
                "maxSizeRequest": 100000,
                "maxConcurrentRequests": 2,
                "maxCallsInRequest": 16,
                "maxObjectsInGet": 50,
                "maxObjectsInSet": 20,
            },
            "urn:ietf:params:jmap:mail": {},
        },
        "accounts": {
            "A1": {
                "accountCapabilities": {
                    "urn:ietf:params:jmap:mail": {},
                }
            }
        },
        "primaryAccounts": {"urn:ietf:params:jmap:mail": "A1"},
        "apiUrl": "https://mail.example.test/api",
        "downloadUrl": "https://mail.example.test/download/{accountId}/{blobId}/{name}?accept={type}",
        "uploadUrl": "https://mail.example.test/upload/{accountId}",
        "state": state,
    }
    return JmapHttpResponse(
        200,
        {"content-type": "application/json"},
        json.dumps(payload).encode("utf-8"),
        "",
    )


def _oauth_account() -> MailAccountV2:
    return MailAccountV2(
        account_id="acc-1",
        display_name="OAuth",
        requested_protocol="jmap",
        resolved_protocol="jmap",
        username_ref="secret://username",
        credential_ref="secret://oauth-refresh",
        sync_policy="headers_only",
        enabled=True,
        provider_config={
            "session_url": "https://mail.example.test/.well-known/jmap",
            "auth_mode": "oauth2",
        },
    )


def test_oauth_rotation_cache_ttl_and_session_state_invalidation_without_token_storage() -> None:
    adapter = _QueuedAdapter(
        [
            JmapHttpResponse(401, {}, b"", ""),
            _session_response("s1"),
            _session_response("s2"),
        ]
    )
    oauth = _OAuthAdapter()
    auth_service = JmapAuthService(oauth_adapter=oauth)
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    transport = JmapHttpTransport(endpoint_policy=endpoint_policy, adapter=adapter)
    now = [0.0]
    cache = JmapDiscoveryCache(ttl_seconds=10, clock=lambda: now[0])
    registry = JmapSessionRegistry(transport=transport)
    discovery = JmapDiscoveryService(
        transport=transport,
        endpoint_policy=endpoint_policy,
        auth_service=auth_service,
        feature_policy=MailFeaturePolicy(external_network_enabled=True),
        cache=cache,
        session_state_observer=registry.invalidate_account_state,
    )
    lifecycle = JmapLifecycleService(
        discovery=discovery,
        auth_service=auth_service,
        registry=registry,
    )
    auth = MailAuthMaterial(username="alice@example.test", credential="refresh-secret")
    first = lifecycle.connect(_oauth_account(), auth)
    assert first.ok is True
    assert oauth.calls[:2] == [False, True]
    assert adapter.authorization[:2] == ["Bearer stale-token", "Bearer rotated-token"]
    cached = lifecycle.connect(_oauth_account(), auth)
    assert cached.ok is True
    assert len(adapter.authorization) == 2
    now[0] = 11.0
    refreshed = lifecycle.connect(_oauth_account(), auth)
    assert refreshed.ok is True
    assert len(adapter.authorization) == 3
    assert registry.client(first.value).reason_code == "jmap_session_not_found"
    assert registry.client(cached.value).reason_code == "jmap_session_not_found"
    assert registry.client(refreshed.value).ok is True
    stored = repr(cache.__dict__) + repr(registry.__dict__)
    assert "stale-token" not in stored
    assert "rotated-token" not in stored
