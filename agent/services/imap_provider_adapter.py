from __future__ import annotations

import hashlib
import uuid
from typing import Sequence

from agent.services.imap_connector_service import ImapConnectorService, ImapSession
from agent.services.mail_account_mapper import MailAccountMapper
from agent.services.mail_contract_service import MailAccountV2, MailMessageMetadata, MailMessageRefV2, stable_mail_ref_id
from agent.services.mail_legacy_mapper import MailLegacyMapper
from agent.services.mail_provider_ports import (
    MailAttachment,
    MailAuthMaterial,
    MailBody,
    MailContentAccessRequest,
    MailContentAccessVerifier,
    MailDeleteRequest,
    MailKeywordChange,
    MailMailbox,
    MailMessage,
    MailMoveRequest,
    MailMutationReport,
    MailProviderBinding,
    MailProviderCapabilities,
    MailProviderResult,
    MailProviderSession,
    MailQuery,
    MailQueryPage,
    VerifiedMailContentAccess,
)


class ImapProviderAdapter:
    def __init__(self, *, connector: ImapConnectorService) -> None:
        self._connector = connector
        self._sessions: dict[str, ImapSession] = {}
        self._messages: dict[str, MailMessage] = {}

    def create(self, account: MailAccountV2) -> MailProviderResult[MailProviderBinding]:
        if account.effective_protocol != "imap":
            return MailProviderResult.failure("mail_provider_protocol_mismatch")
        return MailProviderResult.success(
            MailProviderBinding(
                protocol="imap",
                lifecycle=self,
                capabilities=self,
                reader=self,
                body=self,
                mutator=self,
            )
        )

    def connect(self, account: MailAccountV2, auth: MailAuthMaterial) -> MailProviderResult[MailProviderSession]:
        legacy = MailAccountMapper.to_legacy_imap_runtime_config(account)
        result = self._connector.connect_account(account=legacy, credential=auth.credential, username=auth.username)
        if not bool(result.get("ok")):
            reason = str(result.get("reason_code") or "imap_connect_failed")
            return MailProviderResult.failure(reason, retryable=reason == "imap_connect_timeout")
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = result["session"]
        return MailProviderResult.success(
            MailProviderSession(session_id=session_id, account_id=account.account_id, protocol="imap")
        )

    def _session(self, session: MailProviderSession) -> ImapSession | None:
        if session.protocol != "imap":
            return None
        return self._sessions.get(session.session_id)

    def disconnect(self, session: MailProviderSession) -> MailProviderResult[None]:
        internal = self._session(session)
        if internal is None:
            return MailProviderResult.failure("mail_session_not_found")
        result = self._connector.disconnect(internal)
        self._sessions.pop(session.session_id, None)
        return (
            MailProviderResult.success(reason_code="disconnected")
            if bool(result.get("ok"))
            else MailProviderResult.failure(str(result.get("reason_code") or "imap_disconnect_failed"))
        )

    def capabilities(self, session: MailProviderSession) -> MailProviderResult[MailProviderCapabilities]:
        if self._session(session) is None:
            return MailProviderResult.failure("mail_session_not_found")
        return MailProviderResult.success(
            MailProviderCapabilities(
                provider="imap",
                features=frozenset({"mailbox_list", "metadata_query", "body_explicit"}),
            )
        )

    def list_mailboxes(self, session: MailProviderSession) -> MailProviderResult[tuple[MailMailbox, ...]]:
        internal = self._session(session)
        if internal is None:
            return MailProviderResult.failure("mail_session_not_found")
        result = self._connector.list_mailboxes(internal)
        if not bool(result.get("ok")):
            return MailProviderResult.failure(str(result.get("reason_code") or "imap_list_mailboxes_failed"))
        rows = tuple(
            MailMailbox(
                mailbox_ref_id=f"mailbox-{hashlib.sha256(f'{session.account_id}|{name}'.encode()).hexdigest()[:24]}",
                name=str(name),
                provider_locator={"mailbox": str(name)},
            )
            for name in list(result.get("mailboxes") or [])
        )
        return MailProviderResult.success(rows)

    def query_messages(self, session: MailProviderSession, query: MailQuery) -> MailProviderResult[MailQueryPage]:
        internal = self._session(session)
        if internal is None:
            return MailProviderResult.failure("mail_session_not_found")
        mailbox = str(query.filters.get("mailbox") or "INBOX")
        result = self._connector.fetch_headers(internal, mailbox=mailbox, limit=max(1, int(query.limit)))
        if not bool(result.get("ok")):
            return MailProviderResult.failure(str(result.get("reason_code") or "imap_query_failed"))
        ids: list[str] = []
        for header in list(result.get("headers") or []):
            if not isinstance(header, dict):
                continue
            legacy_ref = {
                "account_id": session.account_id,
                "mailbox": mailbox,
                "uid": int(header.get("uid") or 0),
                "uidvalidity": header.get("uidvalidity"),
                "message_id": str(header.get("message_id") or ""),
                "date": str(header.get("date") or ""),
                "from": str(header.get("from") or ""),
                "to": header.get("to") or "",
                "subject_hash": str(header.get("subject_hash") or ""),
                "content_hash": str(header.get("content_hash") or ""),
                "size": header.get("size"),
            }
            mapped = MailLegacyMapper.message_from_v1(legacy_ref, header)
            message = MailMessage(mapped.message_ref, mapped.metadata)
            self._messages[mapped.message_ref.mail_ref_id] = message
            ids.append(mapped.message_ref.mail_ref_id)
        return MailProviderResult.success(
            MailQueryPage(message_ref_ids=tuple(ids), total=len(ids), next_position=None)
        )

    def get_messages(
        self,
        session: MailProviderSession,
        ids: Sequence[str],
        properties: Sequence[str] = (),
    ) -> MailProviderResult[tuple[MailMessage, ...]]:
        if self._session(session) is None:
            return MailProviderResult.failure("mail_session_not_found")
        return MailProviderResult.success(
            tuple(self._messages[item] for item in ids if item in self._messages)
        )

    def get_body(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[MailBody]:
        internal = self._session(session)
        if internal is None:
            return MailProviderResult.failure("mail_session_not_found")
        if not isinstance(access, VerifiedMailContentAccess):
            return MailProviderResult.failure("verified_mail_content_access_required")
        if access.account_id != session.account_id or message_ref.account_id != session.account_id:
            return MailProviderResult.failure("mail_content_account_mismatch")
        if access.mail_ref_id != message_ref.mail_ref_id:
            return MailProviderResult.failure("mail_content_message_mismatch")
        if access.release_scope not in {"body_excerpt", "full_body"}:
            return MailProviderResult.failure("mail_content_scope_denied")
        if message_ref.mail_ref_id not in access.artifact_ref:
            return MailProviderResult.failure("mail_content_artifact_mismatch")
        locator = dict(message_ref.protocol_locator)
        result = self._connector.fetch_body(
            internal,
            mailbox=str(locator.get("mailbox") or ""),
            uid=int(locator.get("uid") or 0),
        )
        if not bool(result.get("ok")):
            return MailProviderResult.failure(str(result.get("reason_code") or "imap_body_not_found"))
        return MailProviderResult.success(
            MailBody(mail_ref_id=message_ref.mail_ref_id, text_body=str(result.get("body") or ""))
        )

    def get_attachments(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[tuple[MailAttachment, ...]]:
        if not isinstance(access, VerifiedMailContentAccess):
            return MailProviderResult.failure("verified_mail_content_access_required")
        return MailProviderResult.failure("unsupported_operation")

    def set_keywords(
        self,
        session: MailProviderSession,
        changes: Sequence[MailKeywordChange],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        return MailProviderResult.failure("unsupported_operation")

    def move_messages(
        self,
        session: MailProviderSession,
        moves: Sequence[MailMoveRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        return MailProviderResult.failure("unsupported_operation")

    def delete_messages(
        self,
        session: MailProviderSession,
        deletes: Sequence[MailDeleteRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        if any(item.permanent and not item.confirmation_ref for item in deletes):
            return MailProviderResult.failure("mail_delete_confirmation_required")
        return MailProviderResult.failure("unsupported_operation")
