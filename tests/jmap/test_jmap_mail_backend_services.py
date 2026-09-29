from __future__ import annotations

from datetime import UTC, datetime

from agent.services.jmap_contract_service import (
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JmapCoreLimits,
    JmapMethodResponse,
    JmapSessionDocument,
)
from agent.services.jmap_blob_service import JmapBlobService
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyConfig
from agent.services.jmap_http_transport import JmapHttpResponse
from agent.services.jmap_mail_mutation_provider import JmapMailMutationProvider
from agent.services.jmap_mail_read_provider import JmapMailReadProvider
from agent.services.jmap_sync_service import JmapSyncService
from agent.services.mail_body_service import MailBodyService
from agent.services.mail_contract_service import MailMessageRefV2, stable_mail_ref_id
from agent.services.mail_domain_mapper import MailDomainMapper
from agent.services.mail_mutation_policy import MailMutationPolicy
from agent.services.mail_provider_ports import (
    MailContentAccessDecision,
    MailContentAccessRequest,
    MailContentAccessVerifier,
    MailAttachment,
    MailMoveRequest,
    MailProviderResult,
    MailProviderSession,
    MailQuery,
)


def _session_document() -> JmapSessionDocument:
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


def _provider_session() -> MailProviderSession:
    return MailProviderSession(
        session_id="session-1",
        account_id="acc-1",
        protocol="jmap",
        provider_account_id="A1",
    )


def _raw_message(email_id: str = "E1") -> dict:
    return {
        "id": email_id,
        "threadId": "T1",
        "mailboxIds": {"M1": True},
        "keywords": {"$seen": True},
        "size": 12,
        "receivedAt": "2026-07-25T10:00:00Z",
        "messageId": ["m1@example.com"],
        "from": [{"email": "alice@example.com"}],
        "to": [{"email": "bob@example.com"}],
        "subject": "Status",
        "bodyStructure": {"type": "text/plain"},
    }


class _Client:
    def __init__(self) -> None:
        self.session = _session_document()
        self.calls = []
        self.get_requests = []
        self.call_responses = {}
        self.objects = ()

    def call(self, name, arguments, **_kwargs):
        self.calls.append((name, arguments))
        response = self.call_responses.get(name)
        if callable(response):
            response = response(arguments)
        if response is None:
            return MailProviderResult(ok=False, reason_code=f"unexpected_{name}")
        return response

    def get_objects(self, **kwargs):
        self.get_requests.append(kwargs)
        return MailProviderResult(ok=True, reason_code="ok", value=tuple(self.objects))


class _MailboxResolver:
    def resolve_mailbox(self, *, account_id: str, mailbox_ref_id: str):
        assert account_id == "acc-1"
        return MailProviderResult(ok=True, reason_code="ok", value={"box-local": "M1"}[mailbox_ref_id])

    def resolve_role(self, *, account_id: str, role: str):
        assert account_id == "acc-1"
        return MailProviderResult(ok=True, reason_code="ok", value={"trash": "MTRASH"}[role])


def test_read_provider_maps_server_filter_and_returns_only_local_refs() -> None:
    client = _Client()
    client.call_responses["Email/query"] = MailProviderResult(
        ok=True,
        reason_code="ok",
        value=JmapMethodResponse(
            "Email/query",
            {"ids": ["E1"], "queryState": "q1", "total": 1},
            "c1",
        ),
    )
    client.objects = (_raw_message(),)
    provider = JmapMailReadProvider(
        client=client,
        local_account_id="acc-1",
        mailbox_locator_resolver=_MailboxResolver(),
    )
    result = provider.query_messages(
        _provider_session(),
        MailQuery(filters={"mailbox_ref_id": "box-local", "from": "alice", "unread": False}),
    )
    assert result.ok is True
    assert result.value.message_ref_ids[0].startswith("mailref-")
    sent_filter = client.calls[0][1]["filter"]
    assert sent_filter == {"inMailbox": "M1", "from": "alice", "hasKeyword": "$seen"}
    assert client.get_requests[0]["extra_arguments"]["fetchTextBodyValues"] is False
    combined = provider.query_messages(
        _provider_session(),
        MailQuery(filters={"unread": True, "flagged": True}),
    )
    assert combined.ok is True
    assert client.calls[1][1]["filter"] == {
        "operator": "AND",
        "conditions": [{"notKeyword": "$seen"}, {"hasKeyword": "$flagged"}],
    }


class _ContentPolicy:
    def authorize(self, request):
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=MailContentAccessDecision(
                allowed=True,
                reason_code="allowed",
                policy_decision_ref="policy-1",
                expires_at="2099-01-01T00:00:00Z",
                nonce="nonce-1",
            ),
        )


def _message_ref(email_id: str = "E1") -> MailMessageRefV2:
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


def _access(message_ref: MailMessageRefV2):
    verifier = MailContentAccessVerifier(
        policy=_ContentPolicy(),
        now=lambda: datetime(2026, 7, 25, tzinfo=UTC),
    )
    result = verifier.verify(
        MailContentAccessRequest(
            account_id="acc-1",
            workspace_id="workspace-1",
            mail_ref_id=message_ref.mail_ref_id,
            artifact_ref=f"mail://{message_ref.mail_ref_id}/body",
            grant_ref="grant-1",
            release_scope="full_body",
        )
    )
    assert result.ok is True
    return result.value


def test_body_service_requires_message_bound_access_and_sanitizes_html() -> None:
    client = _Client()
    client.objects = (
        {
            "id": "E1",
            "textBody": [{"partId": "p1"}],
            "htmlBody": [{"partId": "p2"}],
            "bodyValues": {
                "p1": {"value": "hello", "isTruncated": False},
                "p2": {"value": '<p>hello<script>bad()</script></p>', "isTruncated": False},
            },
        },
    )
    service = MailBodyService(
        client=client,
        local_account_id="acc-1",
        now=lambda: datetime(2026, 7, 25, tzinfo=UTC),
    )
    message_ref = _message_ref()
    result = service.get_body(_provider_session(), message_ref, access=_access(message_ref))
    assert result.ok is True
    assert result.value.text_body == "hello"
    assert "script" not in result.value.html_body
    mismatch = service.get_body(_provider_session(), _message_ref("E2"), access=_access(message_ref))
    assert mismatch.reason_code == "mail_content_access_message_mismatch"


class _BlobTransport:
    def request_bytes(self, **kwargs):
        assert kwargs["maximum_response_bytes"] == 100
        return JmapHttpResponse(
            status_code=200,
            headers={"content-type": "application/pdf"},
            body=b"%PDF",
            final_url=kwargs["url"],
        )


def test_blob_download_validates_grant_account_template_size_and_content_type() -> None:
    message_ref = _message_ref()
    attachment = MailAttachment(
        attachment_ref="attachment-1",
        mail_ref_id=message_ref.mail_ref_id,
        filename="report.pdf",
        content_type="application/pdf",
        size=4,
        blob_locator={
            "account_id": "acc-1",
            "provider_account_id": "A1",
            "blob_id": "B1",
        },
    )
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    service = JmapBlobService(
        session=_session_document(),
        transport=_BlobTransport(),
        endpoint_policy=endpoint_policy,
        authorization_headers={"Authorization": "Bearer secret"},
        maximum_blob_bytes=100,
    )
    result = service.download(attachment, access=_access(message_ref))
    assert result.ok is True
    assert result.value.content == b"%PDF"


class _StateStore:
    def __init__(self) -> None:
        self.committed = None

    def load(self, **_scope):
        return None

    def apply_and_commit(self, **kwargs):
        self.committed = kwargs
        return True


def test_initial_sync_is_bounded_metadata_only_and_commits_cursor_atomically() -> None:
    client = _Client()
    client.objects = (_raw_message(),)
    client.call_responses["Email/query"] = MailProviderResult(
        ok=True,
        reason_code="ok",
        value=JmapMethodResponse(
            "Email/query",
            {"ids": ["E1"], "queryState": "q1", "total": 1},
            "q",
        ),
    )
    client.call_responses["Email/get"] = MailProviderResult(
        ok=True,
        reason_code="ok",
        value=JmapMethodResponse("Email/get", {"state": "e1", "list": []}, "g"),
    )
    client.call_responses["Mailbox/get"] = MailProviderResult(
        ok=True,
        reason_code="ok",
        value=JmapMethodResponse("Mailbox/get", {"state": "m1", "list": []}, "m"),
    )
    store = _StateStore()
    service = JmapSyncService(
        client=client,
        local_account_id="acc-1",
        state_store=store,
    )
    result = service.sync(_provider_session(), None, "headers_only")
    assert result.ok is True
    assert len(result.value.created) == 1
    assert result.value.cursor.email_state == "e1"
    assert result.value.cursor.mailbox_state == "m1"
    assert result.value.rebuild_required is False
    assert client.get_requests[0]["extra_arguments"]["fetchAllBodyValues"] is False
    assert store.committed["replace_scope"] is True


class _Authorizer:
    def authorize(self, request):
        return MailProviderResult(ok=True, reason_code="authorized")


class _Audit:
    def record(self, **_event):
        pass


def test_move_resolves_local_mailbox_refs_before_email_set() -> None:
    client = _Client()
    client.call_responses["Email/set"] = MailProviderResult(
        ok=True,
        reason_code="ok",
        value=JmapMethodResponse(
            "Email/set",
            {"oldState": "e1", "newState": "e2", "updated": {"E1": None}},
            "s",
        ),
    )
    provider = JmapMailMutationProvider(
        client=client,
        local_account_id="acc-1",
        policy=MailMutationPolicy(authorizer=_Authorizer(), audit_sink=_Audit()),
        mailbox_locator_resolver=_MailboxResolver(),
    )
    result = provider.move_messages(
        _provider_session(),
        (
            MailMoveRequest(
                account_id="acc-1",
                message_ref=_message_ref(),
                destination_mailbox_ref_ids=("box-local",),
                intent_ref="intent-1",
                audit_ref="audit-1",
            ),
        ),
        if_in_state="e1",
    )
    assert result.ok is True
    assert client.calls[0][1]["update"]["E1"]["mailboxIds"] == {"M1": True}
    assert result.value.items[0].mail_ref_id == _message_ref().mail_ref_id
    denied = provider.move_messages(
        _provider_session(),
        (
            MailMoveRequest(
                account_id="acc-1",
                message_ref=_message_ref(),
                destination_mailbox_ref_ids=("box-local",),
                intent_ref="intent-2",
                audit_ref="audit-2",
            ),
        ),
    )
    assert denied.reason_code == "mail_mutation_state_required"
