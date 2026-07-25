from __future__ import annotations

import uuid
from typing import Any, Mapping, Protocol, Sequence

from agent.services.mail_contract_service import MailMessageRefV2, stable_mail_ref_id, subject_hash
from agent.services.mail_provider_ports import MailMailbox, MailMessage, MailMessageMetadata, MailProviderResult


_MAIL_NAMESPACE = uuid.UUID("b1207256-7902-4a24-a946-e979e2894d98")


class MailboxLocatorResolver(Protocol):
    def resolve_mailbox(self, *, account_id: str, mailbox_ref_id: str) -> MailProviderResult[str]:
        ...

    def resolve_role(self, *, account_id: str, role: str) -> MailProviderResult[str]:
        ...


class MailDomainMapper:
    def map_mailbox(
        self,
        raw: Mapping[str, Any],
        *,
        local_account_id: str = "",
        provider_account_id: str = "",
    ) -> MailMailbox:
        provider_id = _required_id(raw.get("id"), "jmap_mailbox_id_missing")
        parent_id = str(raw.get("parentId") or "")
        return MailMailbox(
            mailbox_ref_id=_mailbox_ref_id(local_account_id, provider_account_id, provider_id),
            name=str(raw.get("name") or ""),
            role=str(raw.get("role") or ""),
            parent_ref_id=_mailbox_ref_id(local_account_id, provider_account_id, parent_id) if parent_id else "",
            sort_order=max(0, int(raw.get("sortOrder") or 0)),
            total_emails=max(0, int(raw.get("totalEmails") or 0)),
            unread_emails=max(0, int(raw.get("unreadEmails") or 0)),
            provider_locator={"mailbox_id": provider_id},
        )

    def map_message(
        self,
        raw: Mapping[str, Any],
        *,
        local_account_id: str,
        provider_account_id: str,
    ) -> MailMessage:
        email_id = _required_id(raw.get("id"), "jmap_email_id_missing")
        thread_id = str(raw.get("threadId") or "")
        subject = str(raw.get("subject") or "")
        from_address = _first_address(raw.get("from"))
        to_addresses = _addresses(raw.get("to"))
        message_ids = raw.get("messageId")
        message_id_header = ""
        if isinstance(message_ids, list) and message_ids:
            message_id_header = str(message_ids[0] or "")
        mail_ref_id = stable_mail_ref_id(
            account_id=local_account_id,
            protocol="jmap",
            stable_identity={
                "provider_account_id": provider_account_id,
                "email_id": email_id,
            },
        )
        thread_ref_id = (
            f"threadref-{uuid.uuid5(_MAIL_NAMESPACE, f'{local_account_id}:{provider_account_id}:{thread_id}').hex}"
            if thread_id
            else ""
        )
        message_ref = MailMessageRefV2(
            mail_ref_id=mail_ref_id,
            account_id=local_account_id,
            protocol="jmap",
            protocol_locator={
                "provider_account_id": provider_account_id,
                "email_id": email_id,
                "thread_id": thread_id,
            },
            locator_version=1,
            thread_ref_id=thread_ref_id,
        )
        metadata = MailMessageMetadata(
            message_id_header=message_id_header,
            date=str(raw.get("receivedAt") or raw.get("sentAt") or ""),
            from_address=from_address,
            to_addresses=to_addresses,
            subject=subject,
            subject_hash=subject_hash(subject),
            size=max(0, int(raw.get("size") or 0)),
            mailbox_ref_ids=tuple(
                sorted(
                    _mailbox_ref_id(local_account_id, provider_account_id, str(key))
                    for key, value in _as_mapping(raw.get("mailboxIds")).items()
                    if value
                )
            ),
            keywords=tuple(sorted(str(key) for key, value in _as_mapping(raw.get("keywords")).items() if value)),
            body_structure=dict(raw.get("bodyStructure") or {}),
        )
        return MailMessage(message_ref=message_ref, metadata=metadata)


def _required_id(value: Any, reason: str) -> str:
    clean = str(value or "")
    if not clean:
        raise ValueError(reason)
    return clean


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _addresses(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            address = str(item.get("email") or "").strip()
            if address:
                result.append(address)
    return tuple(result)


def _first_address(value: Any) -> str:
    values = _addresses(value)
    return values[0] if values else ""


def _mailbox_ref_id(
    local_account_id: str,
    provider_account_id: str,
    provider_mailbox_id: str,
) -> str:
    identity = f"{local_account_id}:{provider_account_id}:{provider_mailbox_id}"
    return f"mailboxref-{uuid.uuid5(_MAIL_NAMESPACE, identity).hex}"


__all__ = ["MailDomainMapper", "MailboxLocatorResolver"]
