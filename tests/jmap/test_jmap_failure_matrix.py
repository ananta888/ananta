from __future__ import annotations

import json
from collections import deque

import pytest

from agent.services.jmap_auth_service import JmapAuthService
from agent.services.jmap_client_service import JmapClient
from agent.services.jmap_contract_service import (
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JmapCoreLimits,
    JmapMethodCall,
    JmapSessionDocument,
)
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyConfig
from agent.services.jmap_http_transport import (
    JmapHttpResponse,
    JmapHttpTransport,
    JmapTransportError,
)
from agent.services.jmap_mail_mutation_provider import JmapMailMutationProvider
from agent.services.jmap_provider_factory import JmapProviderDependencies, JmapProviderFactory
from agent.services.mail_contract_service import (
    MailAccountV2,
    MailMessageRefV2,
    stable_mail_ref_id,
)
from agent.services.mail_feature_policy import JmapRuntimeLimits, MailFeaturePolicy
from agent.services.mail_mutation_policy import MailMutationPolicy
from agent.services.mail_provider_ports import (
    MailAuthMaterial,
    MailDeleteRequest,
    MailKeywordChange,
    MailProviderResult,
    MailProviderSession,
)


_SECRET = "SECRET-TOKEN-T19"
_BODY_MARKER = "PRIVATE-BODY-T19"


class _ScriptedAdapter:
    def __init__(self, events) -> None:
        self.events = deque(events)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        event = self.events.popleft()
        if isinstance(event, Exception):
            raise event
        return JmapHttpResponse(
            status_code=event.status_code,
            headers=event.headers,
            body=event.body,
            final_url=request.url,
        )


class _Availability:
    def evaluate(self, **_values):
        return MailProviderResult(ok=True, reason_code="mail_provider_available")

    def record_success(self, **_values):
        return None

    def record_failure(self, **_values):
        return None


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
    def __init__(self) -> None:
        self.events = []

    def record(self, **event):
        self.events.append(event)


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
            "session_url": "https://mail.example.test/.well-known/jmap",
            "auth_mode": "bearer",
        },
    )


def _session_response(*, state: str = "s1") -> JmapHttpResponse:
    payload = {
        "capabilities": {
            JMAP_CORE_CAPABILITY: {
                "maxSizeRequest": 100000,
                "maxConcurrentRequests": 2,
                "maxCallsInRequest": 16,
                "maxObjectsInGet": 50,
                "maxObjectsInSet": 20,
            },
            JMAP_MAIL_CAPABILITY: {},
        },
        "accounts": {
            "A1": {
                "accountCapabilities": {
                    JMAP_MAIL_CAPABILITY: {},
                }
            }
        },
        "primaryAccounts": {JMAP_MAIL_CAPABILITY: "A1"},
        "apiUrl": "https://mail.example.test/api",
        "downloadUrl": "https://mail.example.test/download/{accountId}/{blobId}/{name}?accept={type}",
        "uploadUrl": "https://mail.example.test/upload/{accountId}",
        "state": state,
    }
    return JmapHttpResponse(
        200,
        {"content-type": "application/json"},
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        "",
    )


def _response(
    status: int,
    *,
    body: bytes = b"{}",
    headers=None,
) -> JmapHttpResponse:
    return JmapHttpResponse(
        status,
        dict(headers or {"content-type": "application/json"}),
        body,
        "",
    )


def _factory_binding(adapter, *, limits: JmapRuntimeLimits):
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    transport = JmapHttpTransport(
        endpoint_policy=endpoint_policy,
        adapter=adapter,
        limits=limits,
        sleep=getattr(adapter, "sleep", lambda _seconds: None),
    )
    factory = JmapProviderFactory.from_dependencies(
        JmapProviderDependencies(
            transport=transport,
            endpoint_policy=endpoint_policy,
            auth_service=JmapAuthService(),
            feature_policy=MailFeaturePolicy(
                external_network_enabled=True,
                limits=limits,
            ),
            sync_state_store=_StateStore(),
            mutation_policy=MailMutationPolicy(
                authorizer=_Authorizer(),
                audit_sink=_Audit(),
            ),
            mailbox_locator_resolver=_Mailboxes(),
            availability_policy=_Availability(),
        )
    )
    created = factory.create(_account())
    assert created.ok is True
    return created.value


def _connect(binding):
    return binding.lifecycle.connect(
        _account(),
        MailAuthMaterial(username="alice@example.test", credential=_SECRET),
    )


def _assert_no_result_leak(*values) -> None:
    rendered = repr(values)
    assert _SECRET not in rendered
    assert _BODY_MARKER not in rendered


@pytest.mark.parametrize(
    ("status", "reason_code", "retryable"),
    [
        (401, "jmap_authentication_failed", False),
        (403, "jmap_authorization_failed", False),
        (503, "jmap_service_unavailable", True),
    ],
)
def test_factory_discovery_http_failure_matrix(status, reason_code, retryable) -> None:
    adapter = _ScriptedAdapter([_response(status)])
    binding = _factory_binding(
        adapter,
        limits=JmapRuntimeLimits(maximum_safe_retries=0),
    )
    result = _connect(binding)
    assert result.reason_code == reason_code
    assert result.retryable is retryable
    assert len(adapter.requests) == 1
    _assert_no_result_leak(result)


def test_factory_429_retry_after_is_capped_and_retry_count_is_bounded() -> None:
    adapter = _ScriptedAdapter(
        [
            _response(429, headers={"retry-after": "999"}),
            _response(429, headers={"retry-after": "999"}),
        ]
    )
    delays = []
    adapter.sleep = delays.append
    binding = _factory_binding(
        adapter,
        limits=JmapRuntimeLimits(
            maximum_safe_retries=1,
            maximum_retry_after_seconds=0.2,
        ),
    )
    result = _connect(binding)
    assert result.reason_code == "jmap_rate_limited"
    assert result.retryable is True
    assert result.retry_after_ms == 200
    assert len(adapter.requests) == 2
    assert delays == [0.2]
    _assert_no_result_leak(result)


def test_factory_rejects_malformed_and_oversized_session_json() -> None:
    malformed_adapter = _ScriptedAdapter(
        [_response(200, body=b'{"broken"', headers={"content-type": "application/json"})]
    )
    malformed = _connect(
        _factory_binding(
            malformed_adapter,
            limits=JmapRuntimeLimits(maximum_safe_retries=0),
        )
    )
    assert malformed.reason_code == "jmap_response_json_invalid"

    oversized_adapter = _ScriptedAdapter(
        [
            _response(
                200,
                body=b"{" + (b"x" * 1024),
                headers={"content-type": "application/json"},
            )
        ]
    )
    oversized = _connect(
        _factory_binding(
            oversized_adapter,
            limits=JmapRuntimeLimits(
                maximum_safe_retries=0,
                maximum_json_response_bytes=512,
            ),
        )
    )
    assert oversized.reason_code == "jmap_response_too_large"
    _assert_no_result_leak(malformed, oversized)


def test_factory_rejects_oversized_api_json_after_valid_session() -> None:
    adapter = _ScriptedAdapter(
        [
            _session_response(),
            _response(
                200,
                body=b"{" + (b"x" * 4096),
                headers={"content-type": "application/json"},
            ),
        ]
    )
    binding = _factory_binding(
        adapter,
        limits=JmapRuntimeLimits(
            maximum_safe_retries=0,
            maximum_json_response_bytes=2048,
        ),
    )
    connected = _connect(binding)
    assert connected.ok is True
    result = binding.reader.list_mailboxes(connected.value)
    assert result.reason_code == "jmap_response_too_large"
    assert len(adapter.requests) == 2
    _assert_no_result_leak(result)


def _session_document() -> JmapSessionDocument:
    return JmapSessionDocument(
        session_url="https://mail.example.test/.well-known/jmap",
        api_url="https://mail.example.test/api",
        download_url_template="",
        upload_url_template="",
        event_source_url_template="",
        provider_account_id="A1",
        server_capabilities=frozenset({JMAP_CORE_CAPABILITY, JMAP_MAIL_CAPABILITY}),
        account_capabilities=frozenset({JMAP_MAIL_CAPABILITY}),
        limits=JmapCoreLimits(
            maximum_request_bytes=100000,
            maximum_concurrent_requests=2,
            maximum_calls_per_request=16,
            maximum_objects_per_get=50,
            maximum_objects_per_set=20,
        ),
        state="s1",
        trusted_origin="https://mail.example.test:443",
    )


def _client(adapter: _ScriptedAdapter) -> JmapClient:
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    return JmapClient(
        session=_session_document(),
        transport=JmapHttpTransport(
            endpoint_policy=endpoint_policy,
            adapter=adapter,
            limits=JmapRuntimeLimits(maximum_safe_retries=0),
        ),
        authorization_headers={"Authorization": f"Bearer {_SECRET}"},
    )


@pytest.mark.parametrize(
    ("body", "calls", "reason_code"),
    [
        (
            b'{"methodResponses":[["Email/get",{"list":[]},"unknown"]]}',
            (JmapMethodCall("Email/get", {"accountId": "A1"}, "expected"),),
            "jmap_method_response_unknown_call_id",
        ),
        (
            b'{"methodResponses":[["Email/get",{"list":[]},"a"]]}',
            (
                JmapMethodCall("Email/get", {"accountId": "A1"}, "a"),
                JmapMethodCall("Mailbox/get", {"accountId": "A1"}, "b"),
            ),
            "jmap_method_response_missing",
        ),
    ],
)
def test_client_rejects_unknown_and_partial_method_responses(body, calls, reason_code) -> None:
    adapter = _ScriptedAdapter([_response(200, body=body)])
    result = _client(adapter).call_many(calls)
    assert result.reason_code == reason_code
    assert len(adapter.requests) == 1
    _assert_no_result_leak(result)


def _message_ref(email_id: str) -> MailMessageRefV2:
    return MailMessageRefV2(
        mail_ref_id=stable_mail_ref_id(
            account_id="acc-1",
            protocol="jmap",
            stable_identity={"provider_account_id": "A1", "email_id": email_id},
        ),
        account_id="acc-1",
        protocol="jmap",
        protocol_locator={"provider_account_id": "A1", "email_id": email_id},
        locator_version=1,
    )


def _provider_session() -> MailProviderSession:
    return MailProviderSession(
        session_id="session",
        account_id="acc-1",
        protocol="jmap",
        provider_account_id="A1",
    )


def _mutation_provider(events):
    adapter = _ScriptedAdapter(events)
    audit = _Audit()
    provider = JmapMailMutationProvider(
        client=_client(adapter),
        local_account_id="acc-1",
        policy=MailMutationPolicy(authorizer=_Authorizer(), audit_sink=audit),
        mailbox_locator_resolver=_Mailboxes(),
    )
    return provider, adapter, audit


def test_partial_not_updated_and_not_destroyed_preserve_per_object_results_and_audit() -> None:
    update_body = (
        b'{"methodResponses":[["Email/set",{"oldState":"e1","newState":"e2",'
        b'"updated":{"E1":null},"notUpdated":{"E2":{"type":"forbidden"}}},"c1"]]}'
    )
    update_provider, update_adapter, update_audit = _mutation_provider(
        [_response(200, body=update_body)]
    )
    changes = tuple(
        MailKeywordChange(
            account_id="acc-1",
            message_ref=_message_ref(email_id),
            add_keywords=("$seen",),
            remove_keywords=(),
            intent_ref=f"intent-{email_id}",
            audit_ref=f"audit-{email_id}",
        )
        for email_id in ("E1", "E2")
    )
    updated = update_provider.set_keywords(
        _provider_session(),
        changes,
        if_in_state="e1",
    )
    assert updated.reason_code == "jmap_mutation_partial_failure"
    assert [(item.ok, item.reason_code) for item in updated.value.items] == [
        (True, "ok"),
        (False, "jmap_set_forbidden"),
    ]
    outcomes = [
        (event["intent_ref"], event["outcome"], event["reason_code"])
        for event in update_audit.events
        if event["outcome"] in {"succeeded", "failed"}
    ]
    assert outcomes == [
        ("intent-E1", "succeeded", "ok"),
        ("intent-E2", "failed", "jmap_set_forbidden"),
    ]

    destroy_body = (
        b'{"methodResponses":[["Email/set",{"oldState":"e2","newState":"e3",'
        b'"destroyed":["E1"],"notDestroyed":{"E2":{"type":"forbidden"}}},"c1"]]}'
    )
    delete_provider, delete_adapter, delete_audit = _mutation_provider(
        [_response(200, body=destroy_body)]
    )
    deletes = tuple(
        MailDeleteRequest(
            account_id="acc-1",
            message_ref=_message_ref(email_id),
            permanent=True,
            intent_ref=f"delete-intent-{email_id}",
            audit_ref=f"delete-audit-{email_id}",
            confirmation_ref=f"confirmation-{email_id}",
        )
        for email_id in ("E1", "E2")
    )
    destroyed = delete_provider.delete_messages(
        _provider_session(),
        deletes,
        if_in_state="e2",
    )
    assert destroyed.reason_code == "jmap_mutation_partial_failure"
    assert [(item.ok, item.reason_code) for item in destroyed.value.items] == [
        (True, "ok"),
        (False, "jmap_set_forbidden"),
    ]
    assert [
        event["reason_code"]
        for event in delete_audit.events
        if event["outcome"] in {"succeeded", "failed"}
    ] == ["ok", "jmap_set_forbidden"]
    _assert_no_result_leak(
        updated,
        destroyed,
        update_audit.events,
        delete_audit.events,
    )
    for request in update_adapter.requests + delete_adapter.requests:
        assert _SECRET.encode() not in (request.body or b"")
        assert _BODY_MARKER.encode() not in (request.body or b"")


def test_ambiguous_mutation_reconciles_with_get_and_never_sends_second_set() -> None:
    reconcile_body = (
        b'{"methodResponses":[["Email/get",{"state":"e2",'
        b'"list":[{"id":"E1","keywords":{"$seen":true}}],"notFound":[]},"c2"]]}'
    )
    provider, adapter, audit = _mutation_provider(
        [
            JmapTransportError("jmap_request_timeout", retryable=True),
            _response(200, body=reconcile_body),
        ]
    )
    result = provider.set_keywords(
        _provider_session(),
        (
            MailKeywordChange(
                account_id="acc-1",
                message_ref=_message_ref("E1"),
                add_keywords=("$seen",),
                remove_keywords=(),
                intent_ref="intent-reconcile",
                audit_ref="audit-reconcile",
            ),
        ),
        if_in_state="e1",
    )
    assert result.ok is True
    assert result.reason_code == "jmap_mutation_reconciled"
    method_names = [
        json.loads(request.body)["methodCalls"][0][0]
        for request in adapter.requests
    ]
    assert method_names == ["Email/set", "Email/get"]
    assert [event["outcome"] for event in audit.events][-1] == "reconciled"
    _assert_no_result_leak(result, audit.events)
    for request in adapter.requests:
        assert _SECRET.encode() not in (request.body or b"")
        assert _BODY_MARKER.encode() not in (request.body or b"")
