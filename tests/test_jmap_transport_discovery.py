from __future__ import annotations

from collections import deque

from agent.services.jmap_auth_service import JmapAuthService
from agent.services.jmap_discovery_service import (
    JmapCapabilitiesService,
    JmapDiscoveryService,
    JmapSessionRegistry,
)
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyConfig
from agent.services.jmap_http_transport import (
    JmapHttpRequest,
    JmapHttpResponse,
    JmapHttpTransport,
)
from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_feature_policy import JmapRuntimeLimits, MailFeaturePolicy
from agent.services.mail_provider_ports import MailAuthMaterial
from agent.services.mail_provider_ports import MailProviderSession


class _QueueAdapter:
    def __init__(self, responses: list[JmapHttpResponse]) -> None:
        self.responses = deque(responses)
        self.requests: list[JmapHttpRequest] = []

    def send(self, request: JmapHttpRequest) -> JmapHttpResponse:
        self.requests.append(request)
        return self.responses.popleft()


def _account() -> MailAccountV2:
    return MailAccountV2(
        account_id="acc-1",
        display_name="Mail",
        requested_protocol="jmap",
        resolved_protocol="jmap",
        username_ref="secret://username",
        credential_ref="secret://credential",
        sync_policy="headers_only",
        enabled=True,
        provider_config={
            "session_url": "https://mail.example.com/.well-known/jmap",
            "auth_mode": "bearer",
        },
    )


def _session_payload() -> bytes:
    return (
        b'{"capabilities":{"urn:ietf:params:jmap:core":{"maxSizeRequest":100000,'
        b'"maxConcurrentRequests":4,"maxCallsInRequest":16,"maxObjectsInGet":50,'
        b'"maxObjectsInSet":20},"urn:ietf:params:jmap:mail":{}},'
        b'"accounts":{"A1":{"accountCapabilities":{"urn:ietf:params:jmap:mail":{}}}},'
        b'"primaryAccounts":{"urn:ietf:params:jmap:mail":"A1"},'
        b'"apiUrl":"https://mail.example.com/api",'
        b'"downloadUrl":"https://mail.example.com/download/{accountId}/{blobId}/{name}?accept={type}",'
        b'"uploadUrl":"https://mail.example.com/upload/{accountId}",'
        b'"eventSourceUrl":"https://mail.example.com/events?types={types}&closeafter={closeafter}&ping={ping}",'
        b'"state":"s1"}'
    )


def test_discovery_validates_capabilities_and_never_persists_auth_header() -> None:
    adapter = _QueueAdapter(
        [
            JmapHttpResponse(
                200,
                {"content-type": "application/json"},
                _session_payload(),
                "https://mail.example.com/.well-known/jmap",
            )
        ]
    )
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    limits = JmapRuntimeLimits(maximum_safe_retries=0)
    transport = JmapHttpTransport(endpoint_policy=endpoint_policy, adapter=adapter, limits=limits)
    service = JmapDiscoveryService(
        transport=transport,
        endpoint_policy=endpoint_policy,
        auth_service=JmapAuthService(),
        feature_policy=MailFeaturePolicy(external_network_enabled=True, limits=limits),
    )
    result = service.discover(
        account=_account(),
        auth=MailAuthMaterial(username="alice@example.com", credential="token-value"),
    )
    assert result.ok is True
    assert result.value.provider_account_id == "A1"
    assert not hasattr(result.value, "authorization")
    assert adapter.requests[0].headers["Authorization"] == "Bearer token-value"
    registry = JmapSessionRegistry(transport=transport)
    public_session = registry.register(
        account_id="acc-1",
        document=result.value,
        authorization_headers={"Authorization": "Bearer token-value"},
    )
    assert not hasattr(public_session, "api_url")
    assert JmapCapabilitiesService(registry=registry).capabilities(public_session).ok is True
    forged = MailProviderSession(
        session_id=public_session.session_id,
        account_id="other-account",
        protocol="jmap",
        provider_account_id="A1",
    )
    assert registry.client(forged).reason_code == "jmap_session_mismatch"


def test_transport_retries_safe_rate_limit_but_never_mutation_post() -> None:
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    response_429 = JmapHttpResponse(
        429,
        {"content-type": "application/problem+json", "retry-after": "0"},
        b'{"type":"rate"}',
        "https://mail.example.com/api",
    )
    response_200 = JmapHttpResponse(
        200,
        {"content-type": "application/json"},
        b'{"methodResponses":[]}',
        "https://mail.example.com/api",
    )
    adapter = _QueueAdapter([response_429, response_200])
    transport = JmapHttpTransport(
        endpoint_policy=endpoint_policy,
        adapter=adapter,
        limits=JmapRuntimeLimits(maximum_safe_retries=1),
        sleep=lambda _seconds: None,
    )
    payload, _ = transport.request_json(
        method="POST",
        url="https://mail.example.com/api",
        payload={"using": [], "methodCalls": []},
        purpose="api",
        trusted_origin="https://mail.example.com:443",
        retry_safe=True,
    )
    assert payload["methodResponses"] == []
    assert len(adapter.requests) == 2
