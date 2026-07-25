"""Provider-neutral execution of one Hub-authorized mail task."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_contract_service import (
    MailMessageMetadata,
    MailMessageRefV2,
)
from agent.services.mail_metadata_store_service import MailMetadataStore
from agent.services.mail_provider_ports import (
    MailContentAccessRequest,
    MailContentAccessVerifier,
    MailDeleteRequest,
    MailKeywordChange,
    MailMoveRequest,
    MailProviderBinding,
    MailProviderResult,
    MailQuery,
    MailSyncCursor,
)
from agent.services.mail_secret_resolver import MailAccountAuthResolver
from worker.mail_operation_intent_client import (
    HubMailContentAccessPolicy,
    MailOperationIntentClient,
    ResolvedMailOperationIntent,
)
from worker.mail_task_execution import MailTaskExecutionOutcome


class MailProviderRouterFactory(Protocol):
    def __call__(
        self,
        intent: ResolvedMailOperationIntent | None,
    ) -> Any: ...


class MailboxLocatorRecorder(Protocol):
    def remember(self, *, account_id: str, mailboxes: Any) -> MailProviderResult[None]:
        ...


def _reason(reason_code: str) -> str:
    value = str(reason_code or "provider_operation_failed").lower()
    return value if value.startswith("mail_") else f"mail_{value}"


def _failed(
    reason_code: str,
    *,
    provider: str = "",
    retryable: bool = False,
    retry_after_ms: int | None = None,
) -> MailTaskExecutionOutcome:
    return MailTaskExecutionOutcome(
        status="failed",
        reason_code=_reason(reason_code),
        provider=provider,
        retryable=retryable,
        retry_after_ms=retry_after_ms,
    )


class ProviderMailTaskExecution:
    def __init__(
        self,
        *,
        accounts: MailAccountService,
        auth: MailAccountAuthResolver,
        router_factory: MailProviderRouterFactory,
        metadata: MailMetadataStore,
        mailboxes: MailboxLocatorRecorder,
        intents: MailOperationIntentClient | None,
        imap_availability: Any,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._accounts = accounts
        self._auth = auth
        self._router_factory = router_factory
        self._metadata = metadata
        self._mailboxes = mailboxes
        self._intents = intents
        self._imap_availability = imap_availability
        self._clock = clock

    def execute(
        self,
        *,
        job_id: str,
        operation: str,
        account_ref: str,
        workspace_scope: Mapping[str, str],
        idempotency_key: str,
        operation_refs: Mapping[str, str],
        policy_refs: Mapping[str, str],
        deadline_at: float,
        lease_fencing_token: int,
    ) -> MailTaskExecutionOutcome:
        del idempotency_key, policy_refs
        if deadline_at <= self._clock() or lease_fencing_token < 1:
            return _failed("mail_worker_execution_context_expired")
        account_id = (
            account_ref.split(":", 1)[1]
            if account_ref.startswith("mail-account:")
            else account_ref
        )
        account = self._accounts.get_account(account_id)
        if account is None:
            return _failed("mail_account_not_found")
        if not account.enabled:
            return _failed("mail_account_disabled")
        if operation in {"migration", "cutover"}:
            return _failed("mail_hub_operation_required")

        intent: ResolvedMailOperationIntent | None = None
        if operation in {"body", "mutation"}:
            if self._intents is None:
                return _failed("mail_intent_hub_client_unconfigured")
            intent_ref = str(operation_refs.get("intent_ref") or "")
            if not intent_ref:
                return _failed("mail_operation_intent_ref_required")
            resolved = self._intents.resolve(
                intent_ref=intent_ref,
                job_id=job_id,
                operation=operation,
                account_ref=account_ref,
                workspace_scope=workspace_scope,
            )
            if not resolved.ok or resolved.value is None:
                return _failed(
                    resolved.reason_code,
                    retryable=resolved.retryable,
                    retry_after_ms=resolved.retry_after_ms,
                )
            intent = resolved.value

        router = self._router_factory(intent)
        binding_result = router.resolve(account)
        if not binding_result.ok or binding_result.value is None:
            return _failed(
                binding_result.reason_code,
                provider=account.effective_protocol or "",
                retryable=binding_result.retryable,
                retry_after_ms=binding_result.retry_after_ms,
            )
        binding: MailProviderBinding = binding_result.value
        provider = binding.protocol
        if provider == "imap":
            available = self._imap_availability.evaluate(
                account_id=account.account_id,
                provider="imap",
                operation=operation,
            )
            if not available.ok:
                return _failed(
                    available.reason_code,
                    provider=provider,
                    retryable=available.retryable,
                    retry_after_ms=available.retry_after_ms,
                )
        auth_result = self._auth.resolve(account)
        if not auth_result.ok or auth_result.value is None:
            return _failed(
                auth_result.reason_code,
                provider=provider,
                retryable=auth_result.retryable,
            )
        connected = binding.lifecycle.connect(account, auth_result.value)
        if not connected.ok or connected.value is None:
            self._record_imap(provider, account.account_id, operation, connected)
            return _failed(
                connected.reason_code,
                provider=provider,
                retryable=connected.retryable,
                retry_after_ms=connected.retry_after_ms,
            )
        session = connected.value
        try:
            outcome = self._dispatch(
                operation=operation,
                account_id=account.account_id,
                workspace_scope=workspace_scope,
                account_sync_policy=account.sync_policy,
                binding=binding,
                session=session,
                intent=intent,
            )
        finally:
            binding.lifecycle.disconnect(session)
        self._record_imap_result(provider, account.account_id, operation, outcome)
        return outcome

    def _dispatch(
        self,
        *,
        operation: str,
        account_id: str,
        workspace_scope: Mapping[str, str],
        account_sync_policy: str,
        binding: MailProviderBinding,
        session: Any,
        intent: ResolvedMailOperationIntent | None,
    ) -> MailTaskExecutionOutcome:
        if operation in {"discovery", "diagnose"}:
            return self._discover(
                account_id=account_id,
                operation=operation,
                binding=binding,
                session=session,
            )
        if operation == "sync":
            return self._sync(
                account_id=account_id,
                sync_policy=account_sync_policy,
                binding=binding,
                session=session,
            )
        if operation == "body" and intent is not None:
            return self._body(
                account_id=account_id,
                workspace_scope=workspace_scope,
                binding=binding,
                session=session,
                intent=intent,
            )
        if operation == "mutation" and intent is not None:
            return self._mutation(
                account_id=account_id,
                binding=binding,
                session=session,
                intent=intent,
            )
        return _failed("mail_operation_unsupported", provider=binding.protocol)

    def _discover(
        self,
        *,
        account_id: str,
        operation: str,
        binding: MailProviderBinding,
        session: Any,
    ) -> MailTaskExecutionOutcome:
        capabilities = binding.capabilities.capabilities(session)
        if not capabilities.ok or capabilities.value is None:
            return _failed(
                capabilities.reason_code,
                provider=binding.protocol,
                retryable=capabilities.retryable,
            )
        mailboxes = binding.reader.list_mailboxes(session)
        if not mailboxes.ok or mailboxes.value is None:
            return _failed(
                mailboxes.reason_code,
                provider=binding.protocol,
                retryable=mailboxes.retryable,
            )
        stored = self._mailboxes.remember(
            account_id=account_id,
            mailboxes=mailboxes.value,
        )
        if not stored.ok:
            return _failed(
                stored.reason_code,
                provider=binding.protocol,
                retryable=stored.retryable,
            )
        return MailTaskExecutionOutcome(
            status="completed",
            reason_code=f"mail_{operation}_completed",
            provider=binding.protocol,
            result_refs=(f"mail-account:{account_id}",),
            counters={
                "mailbox_count": len(mailboxes.value),
                "capability_count": len(capabilities.value.features),
            },
        )

    def _sync(
        self,
        *,
        account_id: str,
        sync_policy: str,
        binding: MailProviderBinding,
        session: Any,
    ) -> MailTaskExecutionOutcome:
        if binding.sync is None:
            return _failed("mail_sync_unsupported", provider=binding.protocol)
        scope = "default"
        cursor = self._metadata.get_sync_cursor(
            account_id=account_id,
            protocol=binding.protocol,
            scope=scope,
        )
        result = binding.sync.sync(
            session,
            cursor
            or MailSyncCursor(
                account_id=account_id,
                protocol=binding.protocol,
                scope=scope,
            ),
            sync_policy,
        )
        if not result.ok or result.value is None:
            return _failed(
                result.reason_code,
                provider=binding.protocol,
                retryable=result.retryable,
                retry_after_ms=result.retry_after_ms,
            )
        delta = result.value
        cursor_material = (
            f"{account_id}|{delta.cursor.email_state}|"
            f"{delta.cursor.query_state}|{delta.cursor.mailbox_state}"
        )
        cursor_ref = "mail-sync:" + hashlib.sha256(
            cursor_material.encode("utf-8")
        ).hexdigest()
        return MailTaskExecutionOutcome(
            status="completed",
            reason_code="mail_sync_completed",
            provider=binding.protocol,
            result_refs=(cursor_ref,),
            counters={
                "created_count": len(delta.created),
                "updated_count": len(delta.updated),
                "destroyed_count": len(delta.destroyed_mail_ref_ids),
                "rebuild_count": int(delta.rebuild_required),
            },
        )

    def _body(
        self,
        *,
        account_id: str,
        workspace_scope: Mapping[str, str],
        binding: MailProviderBinding,
        session: Any,
        intent: ResolvedMailOperationIntent,
    ) -> MailTaskExecutionOutcome:
        if binding.body is None or self._intents is None:
            return _failed("mail_body_unsupported", provider=binding.protocol)
        payload = dict(intent.payload)
        try:
            message_ref = MailMessageRefV2.from_mapping(
                dict(payload.get("message_ref") or {})
            )
        except (TypeError, ValueError):
            return _failed(
                "mail_operation_intent_message_ref_invalid",
                provider=binding.protocol,
            )
        release_scope = str(payload.get("release_scope") or "")
        artifact_ref = (
            f"mail://{message_ref.mail_ref_id}?scope={release_scope}"
        )
        request = MailContentAccessRequest(
            account_id=account_id,
            workspace_id=str(workspace_scope.get("workspace_id") or ""),
            artifact_ref=artifact_ref,
            mail_ref_id=message_ref.mail_ref_id,
            grant_ref=intent.grant_ref,
            release_scope=release_scope,
        )
        verified = MailContentAccessVerifier(
            policy=HubMailContentAccessPolicy(
                client=self._intents,
                intent_ref=intent.intent_ref,
                job_id=intent.job_id,
                account_ref=f"mail-account:{account_id}",
                workspace_scope=workspace_scope,
            )
        ).verify(request)
        if not verified.ok or verified.value is None:
            return _failed(
                verified.reason_code,
                provider=binding.protocol,
                retryable=verified.retryable,
            )
        if release_scope == "attachment_ref":
            result = binding.body.get_attachments(
                session,
                message_ref,
                access=verified.value,
            )
            if not result.ok or result.value is None:
                return _failed(
                    result.reason_code,
                    provider=binding.protocol,
                    retryable=result.retryable,
                )
            try:
                self._ensure_metadata(message_ref, binding, session)
                self._metadata.store_attachments(
                    mail_ref_id=message_ref.mail_ref_id,
                    attachments=[
                        {
                            "attachment_ref": item.attachment_ref,
                            "filename": item.filename,
                            "content_type": item.content_type,
                            "size": item.size,
                        }
                        for item in result.value
                    ],
                    access=verified.value,
                )
            except (OSError, PermissionError, ValueError):
                return _failed(
                    "mail_content_store_failed",
                    provider=binding.protocol,
                    retryable=True,
                )
            count = len(result.value)
        else:
            result = binding.body.get_body(
                session,
                message_ref,
                access=verified.value,
            )
            if not result.ok or result.value is None:
                return _failed(
                    result.reason_code,
                    provider=binding.protocol,
                    retryable=result.retryable,
                )
            try:
                self._ensure_metadata(message_ref, binding, session)
                self._metadata.store_body(
                    mail_ref_id=message_ref.mail_ref_id,
                    text_body=result.value.text_body,
                    html_body=result.value.html_body,
                    access=verified.value,
                )
            except (OSError, PermissionError, ValueError):
                return _failed(
                    "mail_content_store_failed",
                    provider=binding.protocol,
                    retryable=True,
                )
            count = 1
        return MailTaskExecutionOutcome(
            status="completed",
            reason_code="mail_body_completed",
            provider=binding.protocol,
            result_refs=(message_ref.mail_ref_id,),
            counters={"content_ref_count": count},
        )

    def _ensure_metadata(
        self,
        message_ref: MailMessageRefV2,
        binding: MailProviderBinding,
        session: Any,
    ) -> None:
        if self._metadata.get_by_mail_ref_id(message_ref.mail_ref_id) is not None:
            return
        if binding.protocol == "imap":
            self._metadata.upsert_message(
                message_ref=message_ref,
                metadata=MailMessageMetadata(),
            )
            return
        provider_id = str(
            message_ref.protocol_locator.get("email_id") or ""
        )
        if not provider_id:
            raise ValueError("mail_message_provider_locator_missing")
        fetched = binding.reader.get_messages(
            session,
            (provider_id,),
        )
        if not fetched.ok or not fetched.value:
            raise ValueError("mail_message_not_found")
        message = fetched.value[0]
        self._metadata.upsert_message(
            message_ref=message.message_ref,
            metadata=message.metadata,
        )

    def _mutation(
        self,
        *,
        account_id: str,
        binding: MailProviderBinding,
        session: Any,
        intent: ResolvedMailOperationIntent,
    ) -> MailTaskExecutionOutcome:
        if binding.mutator is None:
            return _failed(
                "mail_mutation_unsupported",
                provider=binding.protocol,
            )
        payload = dict(intent.payload)
        try:
            refs = tuple(
                MailMessageRefV2.from_mapping(dict(item))
                for item in list(payload.get("message_refs") or [])
            )
        except (TypeError, ValueError):
            return _failed(
                "mail_mutation_message_refs_invalid",
                provider=binding.protocol,
            )
        if not refs or any(item.account_id != account_id for item in refs):
            return _failed(
                "mail_mutation_account_mismatch",
                provider=binding.protocol,
            )
        mailboxes = binding.reader.list_mailboxes(session)
        if mailboxes.ok and mailboxes.value is not None:
            stored = self._mailboxes.remember(
                account_id=account_id,
                mailboxes=mailboxes.value,
            )
            if not stored.ok:
                return _failed(
                    stored.reason_code,
                    provider=binding.protocol,
                    retryable=stored.retryable,
                )
        action = str(payload.get("action") or "")
        common = {
            "account_id": account_id,
            "intent_ref": str(payload.get("intent_ref") or ""),
            "audit_ref": str(payload.get("audit_ref") or ""),
        }
        if action == "set_keywords":
            result = binding.mutator.set_keywords(
                session,
                tuple(
                    MailKeywordChange(
                        message_ref=ref,
                        add_keywords=tuple(
                            str(item)
                            for item in list(payload.get("add_keywords") or [])
                        ),
                        remove_keywords=tuple(
                            str(item)
                            for item in list(payload.get("remove_keywords") or [])
                        ),
                        **common,
                    )
                    for ref in refs
                ),
                if_in_state=str(payload.get("if_in_state") or "") or None,
            )
        elif action == "move_messages":
            result = binding.mutator.move_messages(
                session,
                tuple(
                    MailMoveRequest(
                        message_ref=ref,
                        destination_mailbox_ref_ids=tuple(
                            str(item)
                            for item in list(
                                payload.get("destination_mailbox_ref_ids") or []
                            )
                        ),
                        **common,
                    )
                    for ref in refs
                ),
                if_in_state=str(payload.get("if_in_state") or "") or None,
            )
        elif action == "delete_messages":
            result = binding.mutator.delete_messages(
                session,
                tuple(
                    MailDeleteRequest(
                        message_ref=ref,
                        permanent=bool(payload.get("permanent")),
                        confirmation_ref=str(
                            payload.get("confirmation_ref") or ""
                        ),
                        **common,
                    )
                    for ref in refs
                ),
                if_in_state=str(payload.get("if_in_state") or "") or None,
            )
        else:
            return _failed(
                "mail_mutation_action_invalid",
                provider=binding.protocol,
            )
        if not result.ok or result.value is None:
            return _failed(
                result.reason_code,
                provider=binding.protocol,
                retryable=result.retryable,
                retry_after_ms=result.retry_after_ms,
            )
        succeeded = tuple(item.mail_ref_id for item in result.value.items if item.ok)
        return MailTaskExecutionOutcome(
            status="completed",
            reason_code="mail_mutation_completed",
            provider=binding.protocol,
            result_refs=succeeded,
            counters={
                "succeeded_count": len(succeeded),
                "failed_count": len(result.value.items) - len(succeeded),
            },
        )

    def _record_imap(
        self,
        provider: str,
        account_id: str,
        operation: str,
        result: MailProviderResult[Any],
    ) -> None:
        if provider != "imap":
            return
        if result.ok:
            self._imap_availability.record_success(
                account_id=account_id,
                provider=provider,
                operation=operation,
            )
        else:
            self._imap_availability.record_failure(
                account_id=account_id,
                provider=provider,
                operation=operation,
                retryable=result.retryable,
            )

    def _record_imap_result(
        self,
        provider: str,
        account_id: str,
        operation: str,
        outcome: MailTaskExecutionOutcome,
    ) -> None:
        self._record_imap(
            provider,
            account_id,
            operation,
            MailProviderResult(
                ok=outcome.status == "completed",
                reason_code=outcome.reason_code,
                retryable=outcome.retryable,
            ),
        )


__all__ = [
    "MailboxLocatorRecorder",
    "MailProviderRouterFactory",
    "ProviderMailTaskExecution",
]
