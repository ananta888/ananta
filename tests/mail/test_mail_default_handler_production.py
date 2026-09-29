from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent.services.mail_contract_service import (
    MailAccountV2,
    MailMessageMetadata,
    MailMessageRefV2,
)
from agent.services.mail_provider_ports import (
    MailAuthMaterial,
    MailBody,
    MailContentAccessDecision,
    MailMailbox,
    MailMessage,
    MailMutationItem,
    MailMutationReport,
    MailProviderBinding,
    MailProviderCapabilities,
    MailProviderResult,
    MailProviderSession,
    MailSyncCursor,
    MailSyncDelta,
)
from worker.mail_operation_intent_client import ResolvedMailOperationIntent
from worker.mail_provider_task_execution import ProviderMailTaskExecution
from worker.mail_task_execution import build_mail_task_handler


def _account() -> MailAccountV2:
    return MailAccountV2(
        account_id="acc-1",
        display_name="Mail",
        requested_protocol="jmap",
        resolved_protocol="jmap",
        username_ref="env://MAIL_USER",
        credential_ref="env://MAIL_PASSWORD",
        sync_policy="headers_only",
        enabled=True,
        provider_config={"session_url": "https://mail.example.test/jmap"},
    )


def _message_ref() -> MailMessageRefV2:
    return MailMessageRefV2(
        mail_ref_id="mailref-11111111111111111111111111111111",
        account_id="acc-1",
        protocol="jmap",
        protocol_locator={
            "provider_account_id": "A1",
            "email_id": "e1",
        },
        locator_version=1,
    )


class _Accounts:
    def get_account(self, account_id: str) -> MailAccountV2 | None:
        return _account() if account_id == "acc-1" else None


class _Auth:
    def resolve(self, account: MailAccountV2) -> MailProviderResult[MailAuthMaterial]:
        assert account.account_id == "acc-1"
        return MailProviderResult.success(MailAuthMaterial("user", "token"))


class _Lifecycle:
    def connect(self, account: Any, auth: Any) -> MailProviderResult[MailProviderSession]:
        return MailProviderResult.success(
            MailProviderSession("session-1", account.account_id, "jmap", "A1")
        )

    def disconnect(self, session: Any) -> MailProviderResult[None]:
        return MailProviderResult.success()


class _Capabilities:
    def capabilities(self, session: Any) -> MailProviderResult[MailProviderCapabilities]:
        return MailProviderResult.success(
            MailProviderCapabilities("jmap", features=frozenset({"mail"}))
        )


class _Reader:
    def list_mailboxes(self, session: Any) -> MailProviderResult[tuple[MailMailbox, ...]]:
        return MailProviderResult.success(
            (
                MailMailbox(
                    "mailboxref-inbox",
                    "Inbox",
                    role="inbox",
                    provider_locator={"mailbox_id": "m1"},
                ),
            )
        )

    def get_messages(self, session: Any, ids: Any, properties: Any = ()) -> Any:
        return MailProviderResult.success(
            (MailMessage(_message_ref(), MailMessageMetadata(subject="Hello")),)
        )


class _Sync:
    def sync(self, session: Any, cursor: Any, policy: str) -> Any:
        return MailProviderResult.success(
            MailSyncDelta(
                cursor=MailSyncCursor(
                    "acc-1",
                    "jmap",
                    email_state="s2",
                    query_state="q2",
                ),
                created=(
                    MailMessage(
                        _message_ref(),
                        MailMessageMetadata(subject="Hello"),
                    ),
                ),
            )
        )


class _Body:
    def get_body(self, session: Any, message_ref: Any, *, access: Any) -> Any:
        return MailProviderResult.success(MailBody(message_ref.mail_ref_id, "secret"))

    def get_attachments(self, session: Any, message_ref: Any, *, access: Any) -> Any:
        return MailProviderResult.success(())


class _Mutator:
    def set_keywords(self, session: Any, changes: Any, *, if_in_state: Any = None) -> Any:
        return MailProviderResult.success(
            MailMutationReport(
                (MailMutationItem(_message_ref().mail_ref_id, True, "ok"),)
            )
        )


class _Router:
    def __init__(self, binding: MailProviderBinding) -> None:
        self._binding = binding

    def resolve(self, account: Any) -> MailProviderResult[MailProviderBinding]:
        return MailProviderResult.success(self._binding)


class _Metadata:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    def get_sync_cursor(self, **kwargs: Any) -> None:
        return None

    def get_by_mail_ref_id(self, mail_ref_id: str) -> Any:
        return self.rows.get(mail_ref_id)

    def upsert_message(self, *, message_ref: Any, metadata: Any) -> None:
        self.rows[message_ref.mail_ref_id] = {
            "message_ref": message_ref,
            "metadata": metadata,
        }

    def store_body(self, *, mail_ref_id: str, text_body: str, html_body: str, access: Any) -> None:
        self.rows[mail_ref_id]["stored"] = bool(text_body)


class _Mailboxes:
    def remember(self, **kwargs: Any) -> MailProviderResult[None]:
        return MailProviderResult.success()


class _Availability:
    def evaluate(self, **kwargs: Any) -> MailProviderResult[None]:
        return MailProviderResult.success()

    def record_success(self, **kwargs: Any) -> None:
        pass

    def record_failure(self, **kwargs: Any) -> None:
        pass


class _Intents:
    def __init__(self, operation: str) -> None:
        self.operation = operation

    def resolve(self, **kwargs: Any) -> Any:
        payload = (
            {
                "message_ref": _message_ref().to_dict(),
                "release_scope": "full_body",
            }
            if self.operation == "body"
            else {
                "action": "set_keywords",
                "message_refs": [_message_ref().to_dict()],
                "add_keywords": ["$seen"],
                "remove_keywords": [],
                "destination_mailbox_ref_ids": [],
                "if_in_state": "s1",
                "permanent": False,
                "intent_ref": "mutation-intent-1",
                "audit_ref": "audit-1",
                "confirmation_ref": "",
            }
        )
        return MailProviderResult.success(
            ResolvedMailOperationIntent(
                intent_ref="mail-intent:one",
                operation=self.operation,
                account_id="acc-1",
                workspace_id="repo",
                grant_ref="grant-1",
                payload=payload,
                expires_at=time.time() + 60,
                job_id=kwargs["job_id"],
            )
        )

    def authorize_content(self, **kwargs: Any) -> Any:
        return MailProviderResult.success(
            MailContentAccessDecision(
                allowed=True,
                reason_code="ok",
                policy_decision_ref="policy-1",
                expires_at=(
                    datetime.now(UTC) + timedelta(minutes=1)
                ).isoformat(),
                nonce="nonce-1",
            )
        )


def _task(operation: str) -> dict[str, Any]:
    job_id = f"mail-job-{operation}"
    return {
        "tid": job_id,
        "task": {
            "worker_execution_context": {
                "mail_task": {
                    "schema": "ananta.mail_task.v1",
                    "job_id": job_id,
                    "operation": operation,
                    "account_ref": "mail-account:acc-1",
                    "workspace_scope": {"workspace_id": "repo", "tenant_id": ""},
                    "idempotency_key": f"mail-{operation}-1234",
                    "request_fingerprint": "fingerprint",
                    "operation_refs": (
                        {"intent_ref": "mail-intent:one"}
                        if operation in {"body", "mutation"}
                        else {}
                    ),
                    "policy_refs": {},
                    "deadline_at": time.time() + 60,
                    "max_attempts": 1,
                    "created_at": time.time(),
                },
                "mail_task_control": {
                    "lease": {
                        "job_id": job_id,
                        "fencing_token": 1,
                        "expires_at": time.time() + 60,
                    }
                },
            }
        },
    }


def _execution(operation: str) -> ProviderMailTaskExecution:
    binding = MailProviderBinding(
        protocol="jmap",
        lifecycle=_Lifecycle(),
        capabilities=_Capabilities(),
        reader=_Reader(),
        body=_Body(),
        mutator=_Mutator(),
        sync=_Sync(),
    )
    return ProviderMailTaskExecution(
        accounts=_Accounts(),  # type: ignore[arg-type]
        auth=_Auth(),  # type: ignore[arg-type]
        router_factory=lambda intent: _Router(binding),
        metadata=_Metadata(),  # type: ignore[arg-type]
        mailboxes=_Mailboxes(),
        intents=_Intents(operation) if operation in {"body", "mutation"} else None,
        imap_availability=_Availability(),
    )


@pytest.mark.parametrize("operation", ["discovery", "sync", "body", "mutation"])
def test_default_handler_uses_production_execution_for_all_core_operations(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    import worker.mail_task_composition as composition

    monkeypatch.setattr(
        composition,
        "build_production_mail_task_execution",
        lambda: _execution(operation),
    )
    result = build_mail_task_handler().execute(**_task(operation))

    assert result["status"] == "completed"
    assert result["provider"] == "jmap"
    assert result["reason_code"] == f"mail_{operation}_completed"
