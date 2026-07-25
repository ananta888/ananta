from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from agent.services.jmap_client_service import JmapClient
from agent.services.jmap_contract_service import JmapMethodResponse
from agent.services.mail_contract_service import stable_mail_ref_id
from agent.services.mail_domain_mapper import MailDomainMapper
from agent.services.mail_feature_policy import JmapRuntimeLimits
from agent.services.mail_provider_ports import (
    MailMessage,
    MailProviderResult,
    MailProviderSession,
    MailSyncCursor,
    MailSyncDelta,
)
from agent.services.mail_sync_state_store import (
    JmapSyncCheckpoint,
    MailSyncStateStore,
    MailSyncStateStoreError,
    query_fingerprint,
)


_SYNC_PROPERTIES = (
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


class JmapSyncService:
    def __init__(
        self,
        *,
        client: JmapClient,
        local_account_id: str,
        state_store: MailSyncStateStore,
        mapper: MailDomainMapper | None = None,
        limits: JmapRuntimeLimits = JmapRuntimeLimits(),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._local_account_id = str(local_account_id)
        self._state_store = state_store
        self._mapper = mapper or MailDomainMapper()
        self._limits = limits
        self._now = now or (lambda: datetime.now(UTC))

    def sync(
        self,
        session: MailProviderSession,
        cursor: MailSyncCursor | None,
        policy: str,
    ) -> MailProviderResult[MailSyncDelta]:
        invalid = self._validate_session(session)
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        mode = str(policy or "manual")
        if mode not in {"manual", "headers_only", "limited_recent"}:
            return MailProviderResult(ok=False, reason_code="mail_sync_policy_unknown")
        scope = str(getattr(cursor, "scope", "") or "default")
        filters: dict[str, Any] = {}
        if mode == "limited_recent":
            filters["after"] = (self._now() - timedelta(days=14)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        sort = ({"property": "receivedAt", "isAscending": False},)
        policy_config: dict[str, Any] = {
            "sync_policy": mode,
            "allow_rebuild": True,
        }
        fingerprint = query_fingerprint(filters=filters, sort=sort)
        try:
            checkpoint = self._state_store.load(
                account_id=self._local_account_id,
                provider_account_id=self._client.session.provider_account_id,
                scope=scope,
                query_fingerprint=fingerprint,
            )
        except MailSyncStateStoreError as exc:
            return MailProviderResult(ok=False, reason_code=exc.reason_code)
        if checkpoint is None:
            checkpoint = JmapSyncCheckpoint(
                account_id=self._local_account_id,
                provider_account_id=self._client.session.provider_account_id,
                scope=scope,
                mailbox_state=str(getattr(cursor, "mailbox_state", "") or ""),
                email_state=str(getattr(cursor, "email_state", "") or ""),
                query_state=str(getattr(cursor, "query_state", "") or ""),
                query_fingerprint=fingerprint,
            )
        elif checkpoint.query_fingerprint and checkpoint.query_fingerprint != fingerprint:
            return self._rebuild(checkpoint=checkpoint, filters=filters, sort=sort, policy=policy_config)
        email_state = str(getattr(cursor, "email_state", "") or checkpoint.email_state)
        if not email_state:
            return self._rebuild(checkpoint=checkpoint, filters=filters, sort=sort, policy=policy_config)
        if email_state != checkpoint.email_state:
            checkpoint = replace(checkpoint, email_state=email_state)
        return self._incremental(
            checkpoint=checkpoint,
            filters=filters,
            sort=sort,
            policy=policy_config,
        )

    def _incremental(
        self,
        *,
        checkpoint: JmapSyncCheckpoint,
        filters: Mapping[str, Any],
        sort: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
    ) -> MailProviderResult[MailSyncDelta]:
        created: set[str] = set()
        updated: set[str] = set()
        destroyed: set[str] = set()
        state = checkpoint.email_state
        for _page in range(self._limits.maximum_change_pages):
            result = self._client.call(
                "Email/changes",
                {
                    "accountId": self._client.session.provider_account_id,
                    "sinceState": state,
                    "maxChanges": min(
                        self._limits.maximum_query_page_size,
                        max(1, int(policy.get("maximum_changes") or self._limits.maximum_query_page_size)),
                    ),
                },
            )
            if not result.ok:
                if result.reason_code.endswith("cannotcalculatechanges"):
                    if not bool(policy.get("allow_rebuild", True)):
                        return MailProviderResult(ok=False, reason_code="jmap_rebuild_policy_denied")
                    return self._rebuild(checkpoint=checkpoint, filters=filters, sort=sort, policy=policy)
                return _failure_from(result)
            response = result.value
            if response is None:
                return MailProviderResult(ok=False, reason_code="jmap_changes_response_missing")
            parsed = _parse_changes(response)
            if parsed is None:
                return MailProviderResult(ok=False, reason_code="jmap_changes_response_invalid")
            created.update(parsed["created"])
            updated.update(parsed["updated"])
            destroyed.update(parsed["destroyed"])
            if len(created | updated | destroyed) > self._limits.maximum_rebuild_objects:
                return MailProviderResult(ok=False, reason_code="jmap_sync_object_limit_exceeded")
            state = parsed["new_state"]
            if not parsed["has_more"]:
                break
        else:
            return MailProviderResult(ok=False, reason_code="jmap_sync_page_limit_exceeded")
        updated.difference_update(created)
        destroyed.difference_update(created)
        query_state_result = self._advance_query_state(
            checkpoint.query_state,
            filters=filters,
            sort=sort,
        )
        if not query_state_result.ok or query_state_result.value is None:
            if query_state_result.reason_code == "jmap_query_changes_rebuild_required":
                return self._rebuild(checkpoint=checkpoint, filters=filters, sort=sort, policy=policy)
            return _failure_from(query_state_result)
        next_query_state, query_added, query_removed = query_state_result.value
        updated.update(query_added)
        destroyed.update(query_removed)
        updated.difference_update(created)
        fetched = self._fetch_messages(tuple(sorted(created | updated)))
        if not fetched.ok or fetched.value is None:
            return _failure_from(fetched)
        mailbox_state_result = self._advance_mailbox_state(checkpoint.mailbox_state)
        if not mailbox_state_result.ok or mailbox_state_result.value is None:
            return _failure_from(mailbox_state_result)
        next_checkpoint = replace(
            checkpoint,
            mailbox_state=mailbox_state_result.value,
            email_state=state,
            query_state=next_query_state,
            query_fingerprint=query_fingerprint(filters=filters, sort=tuple(sort)),
            revision=checkpoint.revision + 1,
        )
        delta = _delta(
            created=tuple(message for message in fetched.value if _email_id(message) in created),
            updated=tuple(message for message in fetched.value if _email_id(message) in updated),
            destroyed=tuple(sorted(destroyed)),
            cursor=_cursor(next_checkpoint),
            rebuilt=False,
            provider_account_id=self._client.session.provider_account_id,
        )
        try:
            committed = self._state_store.apply_and_commit(
                expected_revision=checkpoint.revision,
                checkpoint=next_checkpoint,
                delta=delta,
                replace_scope=False,
            )
        except MailSyncStateStoreError as exc:
            return MailProviderResult(ok=False, reason_code=exc.reason_code, retryable=True)
        if not committed:
            return MailProviderResult(ok=False, reason_code="mail_sync_concurrent_update", retryable=True)
        return MailProviderResult(ok=True, reason_code="ok", value=delta)

    def _advance_query_state(
        self,
        current_state: str,
        *,
        filters: Mapping[str, Any],
        sort: Sequence[Mapping[str, Any]],
    ) -> MailProviderResult[tuple[str, set[str], set[str]]]:
        if not current_state:
            return MailProviderResult(
                ok=False,
                reason_code="jmap_query_changes_rebuild_required",
            )
        state = current_state
        added: set[str] = set()
        removed: set[str] = set()
        for _page in range(self._limits.maximum_change_pages):
            result = self._client.call(
                "Email/queryChanges",
                {
                    "accountId": self._client.session.provider_account_id,
                    "filter": dict(filters),
                    "sort": [dict(item) for item in sort],
                    "sinceQueryState": state,
                    "maxChanges": self._limits.maximum_query_page_size,
                    "calculateTotal": True,
                },
            )
            if not result.ok or result.value is None:
                if result.reason_code.endswith("cannotcalculatechanges"):
                    return MailProviderResult(
                        ok=False,
                        reason_code="jmap_query_changes_rebuild_required",
                    )
                return _failure_from(result)
            raw_added = result.value.arguments.get("added") or []
            raw_removed = result.value.arguments.get("removed") or []
            if (
                not isinstance(raw_added, list)
                or not isinstance(raw_removed, list)
                or any(not isinstance(item, Mapping) or not isinstance(item.get("id"), str) for item in raw_added)
                or any(not isinstance(item, str) for item in raw_removed)
            ):
                return MailProviderResult(ok=False, reason_code="jmap_query_changes_invalid")
            added.update(str(item["id"]) for item in raw_added)
            removed.update(raw_removed)
            if len(added | removed) > self._limits.maximum_rebuild_objects:
                return MailProviderResult(ok=False, reason_code="jmap_sync_object_limit_exceeded")
            state = str(result.value.arguments.get("newQueryState") or "")
            if not state:
                return MailProviderResult(ok=False, reason_code="jmap_query_changes_state_missing")
            if not bool(result.value.arguments.get("hasMoreChanges")):
                return MailProviderResult(ok=True, reason_code="ok", value=(state, added, removed))
        return MailProviderResult(ok=False, reason_code="jmap_query_change_page_limit_exceeded")

    def _advance_mailbox_state(self, current_state: str) -> MailProviderResult[str]:
        if not current_state:
            result = self._client.call(
                "Mailbox/get",
                {
                    "accountId": self._client.session.provider_account_id,
                    "ids": [],
                    "properties": ["id"],
                },
            )
            if not result.ok or result.value is None:
                return _failure_from(result)
            state = str(result.value.arguments.get("state") or "")
            return (
                MailProviderResult(ok=True, reason_code="ok", value=state)
                if state
                else MailProviderResult(ok=False, reason_code="jmap_mailbox_state_missing")
            )
        state = current_state
        for _page in range(self._limits.maximum_change_pages):
            result = self._client.call(
                "Mailbox/changes",
                {
                    "accountId": self._client.session.provider_account_id,
                    "sinceState": state,
                    "maxChanges": self._limits.maximum_query_page_size,
                },
            )
            if not result.ok or result.value is None:
                if result.reason_code.endswith("cannotcalculatechanges"):
                    return self._advance_mailbox_state("")
                return _failure_from(result)
            state = str(result.value.arguments.get("newState") or "")
            if not state:
                return MailProviderResult(ok=False, reason_code="jmap_mailbox_changes_invalid")
            if not bool(result.value.arguments.get("hasMoreChanges")):
                return MailProviderResult(ok=True, reason_code="ok", value=state)
        return MailProviderResult(ok=False, reason_code="jmap_mailbox_change_page_limit_exceeded")

    def _rebuild(
        self,
        *,
        checkpoint: JmapSyncCheckpoint,
        filters: Mapping[str, Any],
        sort: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
    ) -> MailProviderResult[MailSyncDelta]:
        ids: list[str] = []
        query_state = ""
        total = 0
        page_size = min(
            self._limits.maximum_query_page_size,
            max(1, int(policy.get("rebuild_page_size") or self._limits.maximum_query_page_size)),
        )
        for page in range(self._limits.maximum_change_pages):
            queried = self._client.call(
                "Email/query",
                {
                    "accountId": self._client.session.provider_account_id,
                    "filter": dict(filters),
                    "sort": [dict(item) for item in sort],
                    "position": page * page_size,
                    "limit": page_size,
                    "calculateTotal": True,
                },
            )
            if not queried.ok or queried.value is None:
                return _failure_from(queried)
            raw_ids = queried.value.arguments.get("ids")
            if not isinstance(raw_ids, list) or any(not isinstance(value, str) for value in raw_ids):
                return MailProviderResult(ok=False, reason_code="jmap_rebuild_query_invalid")
            query_state = str(queried.value.arguments.get("queryState") or "")
            total = max(0, int(queried.value.arguments.get("total") or len(raw_ids)))
            ids.extend(raw_ids)
            if len(ids) > self._limits.maximum_rebuild_objects:
                return MailProviderResult(ok=False, reason_code="jmap_rebuild_object_limit_exceeded")
            if len(raw_ids) < page_size or len(ids) >= total:
                break
        else:
            return MailProviderResult(ok=False, reason_code="jmap_rebuild_page_limit_exceeded")
        fetched = self._fetch_messages(tuple(ids))
        if not fetched.ok or fetched.value is None:
            return _failure_from(fetched)
        state_result = self._client.call(
            "Email/get",
            {
                "accountId": self._client.session.provider_account_id,
                "ids": [],
                "properties": ["id"],
            },
        )
        if not state_result.ok or state_result.value is None:
            return _failure_from(state_result)
        email_state = str(state_result.value.arguments.get("state") or "")
        if not email_state or not query_state:
            return MailProviderResult(ok=False, reason_code="jmap_rebuild_state_missing")
        mailbox_state_result = self._advance_mailbox_state("")
        if not mailbox_state_result.ok or mailbox_state_result.value is None:
            return _failure_from(mailbox_state_result)
        next_checkpoint = replace(
            checkpoint,
            mailbox_state=mailbox_state_result.value,
            email_state=email_state,
            query_state=query_state,
            query_fingerprint=query_fingerprint(filters=filters, sort=tuple(sort)),
            revision=checkpoint.revision + 1,
        )
        delta = _delta(
            created=fetched.value,
            updated=(),
            destroyed=(),
            cursor=_cursor(next_checkpoint),
            rebuilt=True,
            provider_account_id=self._client.session.provider_account_id,
        )
        try:
            committed = self._state_store.apply_and_commit(
                expected_revision=checkpoint.revision,
                checkpoint=next_checkpoint,
                delta=delta,
                replace_scope=True,
            )
        except MailSyncStateStoreError as exc:
            return MailProviderResult(ok=False, reason_code=exc.reason_code, retryable=True)
        if not committed:
            return MailProviderResult(ok=False, reason_code="mail_sync_concurrent_update", retryable=True)
        return MailProviderResult(ok=True, reason_code="jmap_sync_rebuilt", value=delta)

    def _fetch_messages(self, ids: Sequence[str]) -> MailProviderResult[tuple[MailMessage, ...]]:
        fetched = self._client.get_objects(
            object_type="Email",
            provider_account_id=self._client.session.provider_account_id,
            ids=ids,
            properties=_SYNC_PROPERTIES,
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
                    raw,
                    local_account_id=self._local_account_id,
                    provider_account_id=self._client.session.provider_account_id,
                )
                for raw in fetched.value
            )
        except (TypeError, ValueError):
            return MailProviderResult(ok=False, reason_code="jmap_message_invalid")
        return MailProviderResult(ok=True, reason_code="ok", value=messages)

    def _validate_session(self, session: MailProviderSession) -> str:
        if session.protocol != "jmap":
            return "mail_provider_session_protocol_mismatch"
        if session.account_id != self._local_account_id:
            return "mail_provider_session_account_mismatch"
        if session.provider_account_id != self._client.session.provider_account_id:
            return "mail_provider_session_remote_account_mismatch"
        return ""


def _parse_changes(response: JmapMethodResponse) -> dict[str, Any] | None:
    values = response.arguments
    created = values.get("created")
    updated = values.get("updated")
    destroyed = values.get("destroyed")
    state = values.get("newState")
    if (
        not isinstance(created, list)
        or not isinstance(updated, list)
        or not isinstance(destroyed, list)
        or any(not isinstance(value, str) for value in created + updated + destroyed)
        or not isinstance(state, str)
        or not state
    ):
        return None
    return {
        "created": created,
        "updated": updated,
        "destroyed": destroyed,
        "new_state": state,
        "has_more": bool(values.get("hasMoreChanges")),
    }


def _email_id(message: MailMessage) -> str:
    return str(message.message_ref.protocol_locator.get("email_id") or "")


def _construct(dto_type: type[Any], values: Mapping[str, Any]) -> Any:
    accepted = {field.name for field in fields(dto_type)}
    return dto_type(**{key: value for key, value in values.items() if key in accepted})


def _cursor(checkpoint: JmapSyncCheckpoint) -> MailSyncCursor:
    return MailSyncCursor(
        account_id=checkpoint.account_id,
        protocol="jmap",
        scope=checkpoint.scope,
        mailbox_state=checkpoint.mailbox_state,
        email_state=checkpoint.email_state,
        query_state=checkpoint.query_state,
    )


def _delta(
    *,
    created: Sequence[MailMessage],
    updated: Sequence[MailMessage],
    destroyed: Sequence[str],
    cursor: MailSyncCursor,
    rebuilt: bool,
    provider_account_id: str,
) -> MailSyncDelta:
    destroyed_refs = tuple(
        stable_mail_ref_id(
            account_id=cursor.account_id,
            protocol="jmap",
            stable_identity={"provider_account_id": provider_account_id, "email_id": value},
        )
        for value in destroyed
    )
    return MailSyncDelta(
        cursor=cursor,
        created=tuple(created),
        updated=tuple(updated),
        destroyed_mail_ref_ids=destroyed_refs,
        rebuild_required=False,
    )


def _failure_from(result: MailProviderResult[Any]) -> MailProviderResult[Any]:
    return MailProviderResult(
        ok=False,
        reason_code=result.reason_code,
        retryable=result.retryable,
        retry_after_ms=result.retry_after_ms,
        details=result.details,
    )


__all__ = ["JmapSyncService"]
