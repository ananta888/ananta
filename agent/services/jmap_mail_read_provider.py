from __future__ import annotations

from typing import Any, Mapping, Sequence

from agent.services.jmap_client_service import JmapClient
from agent.services.mail_domain_mapper import MailboxLocatorResolver, MailDomainMapper
from agent.services.mail_provider_ports import (
    MailMailbox,
    MailMessage,
    MailProviderResult,
    MailProviderSession,
    MailQuery,
    MailQueryPage,
)


_METADATA_PROPERTIES = (
    "id",
    "threadId",
    "mailboxIds",
    "keywords",
    "size",
    "receivedAt",
    "sentAt",
    "messageId",
    "from",
    "to",
    "subject",
    "hasAttachment",
    "bodyStructure",
    "textBody",
    "htmlBody",
    "attachments",
)


class JmapMailReadProvider:
    def __init__(
        self,
        *,
        client: JmapClient,
        local_account_id: str,
        mapper: MailDomainMapper | None = None,
        mailbox_locator_resolver: MailboxLocatorResolver | None = None,
    ) -> None:
        self._client = client
        self._local_account_id = str(local_account_id)
        self._mapper = mapper or MailDomainMapper()
        self._mailboxes = mailbox_locator_resolver

    def list_mailboxes(
        self,
        session: MailProviderSession,
    ) -> MailProviderResult[tuple[MailMailbox, ...]]:
        invalid = self._validate_session(session)
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        response = self._client.call(
            "Mailbox/get",
            {
                "accountId": self._client.session.provider_account_id,
                "ids": None,
                "properties": [
                    "id",
                    "name",
                    "role",
                    "parentId",
                    "sortOrder",
                    "totalEmails",
                    "unreadEmails",
                ],
            },
        )
        if not response.ok or response.value is None:
            return _failure_from(response)
        rows = response.value.arguments.get("list")
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            return MailProviderResult(ok=False, reason_code="jmap_mailbox_list_invalid")
        try:
            mailboxes = tuple(
                self._mapper.map_mailbox(
                    row,
                    local_account_id=self._local_account_id,
                    provider_account_id=self._client.session.provider_account_id,
                )
                for row in rows
            )
        except (TypeError, ValueError):
            return MailProviderResult(ok=False, reason_code="jmap_mailbox_invalid")
        return MailProviderResult(ok=True, reason_code="ok", value=mailboxes)

    def query_messages(
        self,
        session: MailProviderSession,
        query: MailQuery,
    ) -> MailProviderResult[MailQueryPage]:
        invalid = self._validate_session(session)
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        limit = min(max(1, int(getattr(query, "limit", 50))), self._client.session.limits.maximum_objects_per_get)
        position = max(0, int(getattr(query, "position", 0)))
        filter_result = self._build_filter(query)
        if not filter_result.ok or filter_result.value is None:
            return MailProviderResult(ok=False, reason_code=filter_result.reason_code)
        arguments = {
            "accountId": self._client.session.provider_account_id,
            "filter": filter_result.value,
            "sort": _build_sort(query),
            "position": position,
            "limit": limit,
            "calculateTotal": True,
        }
        queried = self._client.call("Email/query", arguments)
        if not queried.ok or queried.value is None:
            return _failure_from(queried)
        values = queried.value.arguments
        ids = values.get("ids")
        if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
            return MailProviderResult(ok=False, reason_code="jmap_query_ids_invalid")
        fetched = self._client.get_objects(
            object_type="Email",
            provider_account_id=self._client.session.provider_account_id,
            ids=ids,
            properties=_METADATA_PROPERTIES,
            extra_arguments={
                "fetchTextBodyValues": False,
                "fetchHTMLBodyValues": False,
                "fetchAllBodyValues": False,
            },
        )
        if not fetched.ok or fetched.value is None:
            return _failure_from(fetched)
        try:
            messages = tuple(
                self._mapper.map_message(
                    row,
                    local_account_id=self._local_account_id,
                    provider_account_id=self._client.session.provider_account_id,
                )
                for row in fetched.value
            )
        except (TypeError, ValueError):
            return MailProviderResult(ok=False, reason_code="jmap_message_invalid")
        total = max(0, int(values.get("total") or len(ids)))
        page = MailQueryPage(
            message_ref_ids=tuple(message.message_ref.mail_ref_id for message in messages),
            query_state=str(values.get("queryState") or ""),
            total=total,
            next_position=position + len(ids) if position + len(ids) < total else None,
        )
        return MailProviderResult(ok=True, reason_code="ok", value=page)

    def get_messages(
        self,
        session: MailProviderSession,
        ids: Sequence[str],
        properties: Sequence[str] = (),
    ) -> MailProviderResult[tuple[MailMessage, ...]]:
        invalid = self._validate_session(session)
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        allowed = set(_METADATA_PROPERTIES)
        requested = tuple(properties) if properties else _METADATA_PROPERTIES
        if any(value not in allowed for value in requested):
            return MailProviderResult(ok=False, reason_code="jmap_metadata_property_forbidden")
        fetched = self._client.get_objects(
            object_type="Email",
            provider_account_id=self._client.session.provider_account_id,
            ids=ids,
            properties=requested,
            extra_arguments={
                "fetchTextBodyValues": False,
                "fetchHTMLBodyValues": False,
                "fetchAllBodyValues": False,
            },
        )
        if not fetched.ok or fetched.value is None:
            return _failure_from(fetched)
        try:
            messages = tuple(
                self._mapper.map_message(
                    row,
                    local_account_id=self._local_account_id,
                    provider_account_id=self._client.session.provider_account_id,
                )
                for row in fetched.value
            )
        except (TypeError, ValueError):
            return MailProviderResult(ok=False, reason_code="jmap_message_invalid")
        return MailProviderResult(ok=True, reason_code="ok", value=messages)

    def _build_filter(self, query: MailQuery) -> MailProviderResult[dict[str, Any]]:
        result: dict[str, Any] = {}
        filters = dict(query.filters or {})
        aliases = {
            "from": "from",
            "from_address": "from",
            "to": "to",
            "to_address": "to",
            "subject": "subject",
            "after": "after",
            "before": "before",
        }
        allowed = set(aliases) | {"mailbox_ref_id", "unread", "flagged"}
        if set(filters) - allowed:
            return MailProviderResult(ok=False, reason_code="mail_query_filter_unsupported")
        for field, target in aliases.items():
            value = filters.get(field)
            if value not in {None, ""}:
                result[target] = value
        mailbox_ref_id = str(filters.get("mailbox_ref_id") or "")
        if mailbox_ref_id:
            if self._mailboxes is None:
                return MailProviderResult(ok=False, reason_code="mailbox_locator_resolver_required")
            resolved = self._mailboxes.resolve_mailbox(
                account_id=self._local_account_id,
                mailbox_ref_id=mailbox_ref_id,
            )
            if not resolved.ok or not resolved.value:
                return MailProviderResult(ok=False, reason_code=resolved.reason_code)
            result["inMailbox"] = resolved.value
        unread = filters.get("unread")
        keyword_conditions: list[dict[str, str]] = []
        if unread is True:
            keyword_conditions.append({"notKeyword": "$seen"})
        elif unread is False:
            keyword_conditions.append({"hasKeyword": "$seen"})
        flagged = filters.get("flagged")
        if flagged is not None:
            keyword_conditions.append(
                {"hasKeyword" if flagged else "notKeyword": "$flagged"}
            )
        if not keyword_conditions:
            value = result
        elif len(keyword_conditions) == 1:
            value = {**result, **keyword_conditions[0]}
        else:
            conditions = ([result] if result else []) + keyword_conditions
            value = {"operator": "AND", "conditions": conditions}
        return MailProviderResult(ok=True, reason_code="ok", value=value)

    def _validate_session(self, session: MailProviderSession) -> str:
        if session.protocol != "jmap":
            return "mail_provider_session_protocol_mismatch"
        if session.account_id != self._local_account_id:
            return "mail_provider_session_account_mismatch"
        if session.provider_account_id != self._client.session.provider_account_id:
            return "mail_provider_session_remote_account_mismatch"
        return ""


def _build_sort(query: MailQuery) -> list[dict[str, Any]]:
    allowed = {"receivedAt", "sentAt", "size", "from", "to", "subject"}
    result: list[dict[str, Any]] = []
    for raw in query.sort:
        value = str(raw)
        ascending = value.startswith("+")
        property_name = value[1:] if value[:1] in {"+", "-"} else value
        if property_name not in allowed:
            continue
        result.append({"property": property_name, "isAscending": ascending})
    return result or [{"property": "receivedAt", "isAscending": False}]


def _failure_from(result: MailProviderResult[Any]) -> MailProviderResult[Any]:
    return MailProviderResult(
        ok=False,
        reason_code=result.reason_code,
        retryable=result.retryable,
        retry_after_ms=result.retry_after_ms,
        details=result.details,
    )


__all__ = ["JmapMailReadProvider"]
