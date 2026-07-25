from __future__ import annotations

from collections import deque

from agent.services.jmap_client_service import JmapClient
from agent.services.jmap_contract_service import (
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JmapCoreLimits,
    JmapMethodCall,
    JmapSessionDocument,
)
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyConfig
from agent.services.jmap_http_transport import JmapHttpResponse, JmapHttpTransport
from agent.services.mail_html_sanitizer import MailHtmlSanitizer


class _Adapter:
    def __init__(self, body: bytes) -> None:
        self.body = deque([body])

    def send(self, request):
        return JmapHttpResponse(
            200,
            {"content-type": "application/json"},
            self.body.popleft(),
            request.url,
        )


class _BodiesAdapter:
    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = deque(bodies)
        self.calls = 0

    def send(self, request):
        self.calls += 1
        return JmapHttpResponse(
            200,
            {"content-type": "application/json"},
            self.bodies.popleft(),
            request.url,
        )


def _session() -> JmapSessionDocument:
    return JmapSessionDocument(
        session_url="https://mail.example.com/.well-known/jmap",
        api_url="https://mail.example.com/api",
        download_url_template="https://mail.example.com/d/{accountId}/{blobId}/{name}?accept={type}",
        upload_url_template="https://mail.example.com/u/{accountId}",
        event_source_url_template="",
        provider_account_id="A1",
        server_capabilities=frozenset({JMAP_CORE_CAPABILITY, JMAP_MAIL_CAPABILITY}),
        account_capabilities=frozenset({JMAP_MAIL_CAPABILITY}),
        limits=JmapCoreLimits(100000, 4, 16, 50, 20),
        state="s1",
        trusted_origin="https://mail.example.com:443",
    )


def test_client_correlates_responses_by_call_id_not_position() -> None:
    adapter = _Adapter(
        b'{"methodResponses":[["Email/get",{"list":[]},"b"],["Mailbox/get",{"list":[]},"a"]]}'
    )
    policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    client = JmapClient(
        session=_session(),
        transport=JmapHttpTransport(endpoint_policy=policy, adapter=adapter),
        authorization_headers={"Authorization": "Bearer secret"},
    )
    result = client.call_many(
        (
            JmapMethodCall("Mailbox/get", {"accountId": "A1"}, "a"),
            JmapMethodCall("Email/get", {"accountId": "A1"}, "b"),
        )
    )
    assert result.ok is True
    assert [item.call_id for item in result.value] == ["a", "b"]


def test_html_sanitizer_removes_active_and_remote_content() -> None:
    sanitizer = MailHtmlSanitizer()
    result = sanitizer.sanitize(
        '<p onclick="steal()">hello<script>alert(1)</script>'
        '<img src="https://tracker.example/pixel"><a href="https://tracker.example">x</a>'
        '<a href="mailto:safe@example.com">safe</a></p>'
    )
    assert "onclick" not in result
    assert "script" not in result
    assert "tracker.example" not in result
    assert "mailto:safe@example.com" in result


def test_method_rate_limit_retries_reads_but_never_mutations() -> None:
    policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    read_adapter = _BodiesAdapter(
        [
            b'{"methodResponses":[["error",{"type":"rateLimit"},"r"]]}',
            b'{"methodResponses":[["Email/get",{"list":[]},"r"]]}',
        ]
    )
    read_client = JmapClient(
        session=_session(),
        transport=JmapHttpTransport(endpoint_policy=policy, adapter=read_adapter),
        authorization_headers={"Authorization": "Bearer secret"},
        sleep=lambda _seconds: None,
        jitter=lambda: 0.5,
    )
    read = read_client.call("Email/get", {"accountId": "A1"}, call_id="r")
    assert read.ok is True
    assert read_adapter.calls == 2

    mutation_adapter = _BodiesAdapter(
        [b'{"methodResponses":[["error",{"type":"rateLimit"},"m"]]}']
    )
    mutation_client = JmapClient(
        session=_session(),
        transport=JmapHttpTransport(endpoint_policy=policy, adapter=mutation_adapter),
        authorization_headers={"Authorization": "Bearer secret"},
        sleep=lambda _seconds: None,
    )
    mutation = mutation_client.call("Email/set", {"accountId": "A1"}, call_id="m")
    assert mutation.reason_code == "jmap_method_ratelimit"
    assert mutation.retryable is False
    assert mutation_adapter.calls == 1
