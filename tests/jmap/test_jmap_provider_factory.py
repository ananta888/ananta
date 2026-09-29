from __future__ import annotations

from collections import deque

from agent.services.jmap_auth_service import JmapAuthService
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyConfig
from agent.services.jmap_http_transport import JmapHttpResponse, JmapHttpTransport
from agent.services.jmap_provider_factory import JmapProviderDependencies, JmapProviderFactory
from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_feature_policy import MailFeaturePolicy
from agent.services.mail_mutation_policy import MailMutationPolicy
from agent.services.mail_provider_ports import MailAuthMaterial, MailProviderResult


class _Adapter:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = 0

    def send(self, request):
        self.calls += 1
        response = self.responses.popleft()
        return JmapHttpResponse(
            response.status_code,
            response.headers,
            response.body,
            request.url,
        )


class _Availability:
    def __init__(self) -> None:
        self.denied_operation = ""
        self.events = []

    def evaluate(self, *, account_id: str, provider: str, operation: str):
        assert account_id == "acc-1"
        assert provider == "jmap"
        if operation == self.denied_operation:
            return MailProviderResult(ok=False, reason_code="mail_provider_circuit_open")
        return MailProviderResult(ok=True, reason_code="mail_provider_available")

    def record_success(self, **event):
        self.events.append(("success", event))

    def record_failure(self, **event):
        self.events.append(("failure", event))


class _StateStore:
    def load(self, **_scope):
        return None

    def apply_and_commit(self, **_values):
        return True


class _Mailboxes:
    def resolve_mailbox(self, **_values):
        return MailProviderResult(ok=True, reason_code="ok", value="M1")

    def resolve_role(self, **_values):
        return MailProviderResult(ok=True, reason_code="ok", value="MTRASH")


class _Authorizer:
    def authorize(self, _request):
        return MailProviderResult(ok=True, reason_code="authorized")


class _Audit:
    def record(self, **_event):
        pass


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


def _session_response() -> JmapHttpResponse:
    body = (
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
    return JmapHttpResponse(200, {"content-type": "application/json"}, body, "")


def test_factory_builds_complete_binding_and_availability_is_fail_closed() -> None:
    mailbox_response = JmapHttpResponse(
        200,
        {"content-type": "application/json"},
        b'{"methodResponses":[["Mailbox/get",{"list":[],"state":"m1"},"c1"]]}',
        "",
    )
    adapter = _Adapter([_session_response(), mailbox_response])
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    availability = _Availability()
    transport = JmapHttpTransport(endpoint_policy=endpoint_policy, adapter=adapter)
    factory = JmapProviderFactory.from_dependencies(
        JmapProviderDependencies(
            transport=transport,
            endpoint_policy=endpoint_policy,
            auth_service=JmapAuthService(),
            feature_policy=MailFeaturePolicy(external_network_enabled=True),
            sync_state_store=_StateStore(),
            mutation_policy=MailMutationPolicy(
                authorizer=_Authorizer(),
                audit_sink=_Audit(),
            ),
            mailbox_locator_resolver=_Mailboxes(),
            availability_policy=availability,
        )
    )
    created = factory.create(_account())
    assert created.ok is True
    binding = created.value
    assert binding.body is not None
    assert binding.mutator is not None
    assert binding.sync is not None
    connected = binding.lifecycle.connect(
        _account(),
        MailAuthMaterial(username="alice@example.com", credential="token"),
    )
    assert connected.ok is True
    listed = binding.reader.list_mailboxes(connected.value)
    assert listed.ok is True
    availability.denied_operation = "read"
    denied = binding.reader.list_mailboxes(connected.value)
    assert denied.reason_code == "mail_provider_circuit_open"
    assert adapter.calls == 2
