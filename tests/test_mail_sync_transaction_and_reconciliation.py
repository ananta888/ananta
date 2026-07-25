from __future__ import annotations

import pytest

from agent.services.jmap_contract_service import (
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JmapCoreLimits,
    JmapMethodResponse,
    JmapSessionDocument,
)
from agent.services.jmap_mail_mutation_provider import JmapMailMutationProvider
from agent.services.mail_contract_service import MailMessageRefV2, stable_mail_ref_id
from agent.services.mail_mutation_policy import MailMutationPolicy
from agent.services.mail_provider_ports import (
    MailKeywordChange,
    MailProviderResult,
    MailProviderSession,
    MailSyncCursor,
    MailSyncDelta,
)
from agent.services.mail_sync_state_store import JmapSyncCheckpoint, MailSyncStateStoreError
from agent.services.mail_sync_transaction_service import TransactionalMailSyncStateStore


def _checkpoint() -> JmapSyncCheckpoint:
    return JmapSyncCheckpoint(
        account_id="acc-1",
        provider_account_id="A1",
        scope="default",
        email_state="e2",
        query_fingerprint="fingerprint",
        revision=1,
    )


def _delta() -> MailSyncDelta:
    return MailSyncDelta(
        cursor=MailSyncCursor(
            account_id="acc-1",
            protocol="jmap",
            email_state="e2",
        )
    )


class _UnitOfWork:
    def __init__(self, port, request) -> None:
        self.port = port
        self.request = request
        self.metadata_staged = False
        self.checkpoint_staged = None

    def apply_metadata(self, **_values):
        if self.port.fail_metadata:
            return MailProviderResult(ok=False, reason_code="mail_metadata_apply_failed")
        self.metadata_staged = True
        return MailProviderResult(ok=True, reason_code="ok")

    def stage_checkpoint(self, *, checkpoint):
        self.checkpoint_staged = checkpoint
        return MailProviderResult(ok=True, reason_code="ok")

    def commit(self):
        self.port.metadata_applied = self.metadata_staged
        self.port.checkpoint = self.checkpoint_staged
        return MailProviderResult(ok=True, reason_code="ok")

    def rollback(self):
        self.port.rollbacks += 1
        self.metadata_staged = False
        self.checkpoint_staged = None


class _TransactionPort:
    def __init__(self, *, fail_metadata: bool = False) -> None:
        self.fail_metadata = fail_metadata
        self.metadata_applied = False
        self.checkpoint = None
        self.rollbacks = 0
        self.transaction_ids = []

    def load_checkpoint(self, **_scope):
        return MailProviderResult(ok=True, reason_code="ok", value=self.checkpoint)

    def begin(self, request):
        self.transaction_ids.append(request.transaction_id)
        current_revision = self.checkpoint.revision if self.checkpoint is not None else 0
        if request.expected_revision != current_revision:
            return MailProviderResult(ok=False, reason_code="mail_sync_concurrent_update")
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=_UnitOfWork(self, request),
        )


def test_sync_transaction_rolls_back_metadata_and_checkpoint_together() -> None:
    failing_port = _TransactionPort(fail_metadata=True)
    failing = TransactionalMailSyncStateStore(transaction_port=failing_port)
    with pytest.raises(MailSyncStateStoreError, match="mail_metadata_apply_failed"):
        failing.apply_and_commit(
            expected_revision=0,
            checkpoint=_checkpoint(),
            delta=_delta(),
        )
    assert failing_port.rollbacks == 1
    assert failing_port.metadata_applied is False
    assert failing_port.checkpoint is None

    port = _TransactionPort()
    store = TransactionalMailSyncStateStore(transaction_port=port)
    assert store.apply_and_commit(
        expected_revision=0,
        checkpoint=_checkpoint(),
        delta=_delta(),
        replace_scope=True,
    )
    assert port.metadata_applied is True
    assert port.checkpoint == _checkpoint()
    assert port.transaction_ids[0].startswith("mailsync-")
    assert store.apply_and_commit(
        expected_revision=0,
        checkpoint=_checkpoint(),
        delta=_delta(),
    ) is False


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
            maximum_concurrent_requests=1,
            maximum_calls_per_request=10,
            maximum_objects_per_get=10,
            maximum_objects_per_set=10,
        ),
        state="s1",
        trusted_origin="https://mail.example.test:443",
    )


class _AmbiguousClient:
    def __init__(self) -> None:
        self.session = _session_document()
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "Email/set":
            return MailProviderResult(
                ok=False,
                reason_code="jmap_request_timeout",
                retryable=True,
            )
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=JmapMethodResponse(
                "Email/get",
                {
                    "state": "e2",
                    "list": [
                        {"id": "E1", "keywords": {"$seen": True}},
                        {"id": "E2", "keywords": {}},
                    ],
                    "notFound": [],
                },
                "c2",
            ),
        )


class _Authorizer:
    def authorize(self, _request):
        return MailProviderResult(ok=True, reason_code="authorized")


class _Audit:
    def __init__(self) -> None:
        self.events = []

    def record(self, **event):
        self.events.append(event)


class _Mailboxes:
    def resolve_mailbox(self, **_values):
        return MailProviderResult(ok=True, reason_code="ok", value="M1")

    def resolve_role(self, **_values):
        return MailProviderResult(ok=True, reason_code="ok", value="TRASH")


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


def test_ambiguous_mutation_is_read_reconciled_per_object_and_never_retried() -> None:
    client = _AmbiguousClient()
    audit = _Audit()
    provider = JmapMailMutationProvider(
        client=client,
        local_account_id="acc-1",
        policy=MailMutationPolicy(authorizer=_Authorizer(), audit_sink=audit),
        mailbox_locator_resolver=_Mailboxes(),
    )
    requests = tuple(
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
    result = provider.set_keywords(
        MailProviderSession(
            session_id="session",
            account_id="acc-1",
            protocol="jmap",
            provider_account_id="A1",
        ),
        requests,
        if_in_state="e1",
    )
    assert result.ok is False
    assert result.reason_code == "jmap_mutation_outcome_unknown"
    assert [item.ok for item in result.value.items] == [True, False]
    assert [name for name, _arguments in client.calls] == ["Email/set", "Email/get"]
    outcomes = [
        event["outcome"]
        for event in audit.events
        if event["outcome"] in {"reconciled", "indeterminate"}
    ]
    assert outcomes == ["reconciled", "indeterminate"]
