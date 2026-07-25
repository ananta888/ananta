from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping, Sequence

from agent.services.jmap_client_service import JmapClient
from agent.services.jmap_mutation_reconciliation import (
    JmapMutationReconciler,
    JmapMutationReconciliationTarget,
)
from agent.services.mail_domain_mapper import MailboxLocatorResolver
from agent.services.mail_mutation_policy import MailMutationPolicy
from agent.services.mail_provider_ports import (
    MailDeleteRequest,
    MailKeywordChange,
    MailMoveRequest,
    MailMutationItem,
    MailMutationReport,
    MailProviderResult,
    MailProviderSession,
)


class JmapMailMutationProvider:
    def __init__(
        self,
        *,
        client: JmapClient,
        local_account_id: str,
        policy: MailMutationPolicy,
        mailbox_locator_resolver: MailboxLocatorResolver,
        reconciler: JmapMutationReconciler | None = None,
    ) -> None:
        self._client = client
        self._local_account_id = str(local_account_id)
        self._policy = policy
        self._mailboxes = mailbox_locator_resolver
        self._reconciler = reconciler or JmapMutationReconciler(client=client)

    def set_keywords(
        self,
        session: MailProviderSession,
        changes: Sequence[MailKeywordChange],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        return self._update(
            session=session,
            requests=changes,
            operation="set_keywords",
            if_in_state=if_in_state,
            patch_builder=_keyword_patch,
        )

    def move_messages(
        self,
        session: MailProviderSession,
        moves: Sequence[MailMoveRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        return self._update(
            session=session,
            requests=moves,
            operation="move_messages",
            if_in_state=if_in_state,
            patch_builder=self._move_patch,
        )

    def delete_messages(
        self,
        session: MailProviderSession,
        deletes: Sequence[MailDeleteRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        invalid = self._validate_session(session)
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        if not str(if_in_state or ""):
            return MailProviderResult(ok=False, reason_code="mail_mutation_state_required")
        if not deletes:
            return MailProviderResult(ok=True, reason_code="ok", value=_report((), if_in_state or ""))
        permanent = {bool(item.permanent) for item in deletes}
        if len(permanent) != 1:
            return MailProviderResult(ok=False, reason_code="mail_delete_modes_must_be_separate")
        if False in permanent:
            trash = self._mailboxes.resolve_role(account_id=self._local_account_id, role="trash")
            if not trash.ok or not trash.value:
                return MailProviderResult(ok=False, reason_code=trash.reason_code or "mail_trash_mailbox_required")
            return self._update(
                session=session,
                requests=deletes,
                operation="move_to_trash",
                if_in_state=if_in_state,
                patch_builder=lambda _item: {"mailboxIds": {trash.value: True}},
            )
        if len(deletes) > self._client.session.limits.maximum_objects_per_set:
            return MailProviderResult(ok=False, reason_code="jmap_max_objects_in_set_exceeded")
        for item in deletes:
            authorized = self._authorize_request(item, operation="permanent_delete")
            if not authorized.ok:
                return MailProviderResult(ok=False, reason_code=authorized.reason_code)
        ids = tuple(_email_id(item.message_ref) for item in deletes)
        if any(not value for value in ids):
            return MailProviderResult(ok=False, reason_code="jmap_email_locator_missing")
        if len(set(ids)) != len(ids):
            return MailProviderResult(ok=False, reason_code="jmap_duplicate_mutation_target")
        response = self._client.call(
            "Email/set",
            {
                "accountId": self._client.session.provider_account_id,
                "ifInState": if_in_state,
                "destroy": list(ids),
            },
        )
        if not response.ok or response.value is None:
            if _ambiguous_failure(response):
                reconciled = self._reconciler.reconcile(
                    tuple(
                        JmapMutationReconciliationTarget(
                            email_id=email_id,
                            mail_ref_id=item.message_ref.mail_ref_id,
                            destroyed=True,
                        )
                        for email_id, item in zip(ids, deletes)
                    ),
                    original_reason_code=response.reason_code,
                )
                self._audit_reconciliation(deletes, "permanent_delete", reconciled)
                return reconciled
            self._audit_requests(deletes, "permanent_delete", False, response.reason_code)
            return _failure_from(response)
        report = _mutation_report(
            response.value.arguments,
            requested={email_id: item.message_ref.mail_ref_id for email_id, item in zip(ids, deletes)},
            action="destroy",
        )
        self._audit_report(deletes, "permanent_delete", report)
        return report

    def _update(
        self,
        *,
        session: MailProviderSession,
        requests: Sequence[Any],
        operation: str,
        if_in_state: str | None,
        patch_builder: Any,
    ) -> MailProviderResult[MailMutationReport]:
        invalid = self._validate_session(session)
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        if not str(if_in_state or ""):
            return MailProviderResult(ok=False, reason_code="mail_mutation_state_required")
        if not requests:
            return MailProviderResult(ok=True, reason_code="ok", value=_report((), if_in_state or ""))
        if len(requests) > self._client.session.limits.maximum_objects_per_set:
            return MailProviderResult(ok=False, reason_code="jmap_max_objects_in_set_exceeded")
        updates: dict[str, Mapping[str, Any]] = {}
        for item in requests:
            authorized = self._authorize_request(item, operation=operation)
            if not authorized.ok:
                return MailProviderResult(ok=False, reason_code=authorized.reason_code)
            email_id = _email_id(item.message_ref)
            if not email_id:
                return MailProviderResult(ok=False, reason_code="jmap_email_locator_missing")
            try:
                updates[email_id] = patch_builder(item)
            except ValueError as exc:
                return MailProviderResult(ok=False, reason_code=str(exc))
        if len(updates) != len(requests):
            return MailProviderResult(ok=False, reason_code="jmap_duplicate_mutation_target")
        response = self._client.call(
            "Email/set",
            {
                "accountId": self._client.session.provider_account_id,
                "ifInState": if_in_state,
                "update": updates,
            },
        )
        if not response.ok or response.value is None:
            if _ambiguous_failure(response):
                reconciled = self._reconciler.reconcile(
                    tuple(
                        JmapMutationReconciliationTarget(
                            email_id=_email_id(item.message_ref),
                            mail_ref_id=item.message_ref.mail_ref_id,
                            expected_patch=updates[_email_id(item.message_ref)],
                        )
                        for item in requests
                    ),
                    original_reason_code=response.reason_code,
                )
                self._audit_reconciliation(requests, operation, reconciled)
                return reconciled
            self._audit_requests(requests, operation, False, response.reason_code)
            return _failure_from(response)
        report = _mutation_report(
            response.value.arguments,
            requested={
                _email_id(item.message_ref): item.message_ref.mail_ref_id
                for item in requests
            },
            action="update",
        )
        self._audit_report(requests, operation, report)
        return report

    def _authorize_request(self, item: Any, *, operation: str) -> MailProviderResult[None]:
        if str(item.account_id) != self._local_account_id or item.message_ref.account_id != self._local_account_id:
            return MailProviderResult(ok=False, reason_code="mail_mutation_account_mismatch")
        return self._policy.authorize(
            account_id=self._local_account_id,
            operation=operation,
            intent_ref=str(item.intent_ref),
            audit_ref=str(item.audit_ref),
            confirmation_ref=str(getattr(item, "confirmation_ref", "") or ""),
        )

    def _audit_requests(
        self,
        requests: Sequence[Any],
        operation: str,
        ok: bool,
        reason_code: str,
    ) -> None:
        for item in requests:
            self._policy.record_outcome(
                account_id=self._local_account_id,
                operation=operation,
                intent_ref=str(item.intent_ref),
                audit_ref=str(item.audit_ref),
                ok=ok,
                reason_code=reason_code,
            )

    def _audit_reconciliation(
        self,
        requests: Sequence[Any],
        operation: str,
        result: MailProviderResult[MailMutationReport],
    ) -> None:
        by_ref = {
            item.mail_ref_id: item
            for item in tuple(result.value.items if result.value is not None else ())
        }
        for request in requests:
            item = by_ref.get(request.message_ref.mail_ref_id)
            reconciled = bool(
                item is not None
                and item.ok
                and item.reason_code == "jmap_mutation_reconciled"
            )
            self._policy.record_reconciliation(
                account_id=self._local_account_id,
                operation=operation,
                intent_ref=str(request.intent_ref),
                audit_ref=str(request.audit_ref),
                reconciled=reconciled,
                reason_code=(
                    item.reason_code
                    if item is not None
                    else "jmap_mutation_outcome_unknown"
                ),
            )

    def _audit_report(
        self,
        requests: Sequence[Any],
        operation: str,
        result: MailProviderResult[MailMutationReport],
    ) -> None:
        by_ref = {
            item.mail_ref_id: item
            for item in tuple(result.value.items if result.value is not None else ())
        }
        for request in requests:
            item = by_ref.get(request.message_ref.mail_ref_id)
            self._policy.record_outcome(
                account_id=self._local_account_id,
                operation=operation,
                intent_ref=str(request.intent_ref),
                audit_ref=str(request.audit_ref),
                ok=bool(item is not None and item.ok),
                reason_code=(
                    item.reason_code
                    if item is not None
                    else "jmap_set_result_missing"
                ),
            )

    def _validate_session(self, session: MailProviderSession) -> str:
        if session.protocol != "jmap":
            return "mail_provider_session_protocol_mismatch"
        if session.account_id != self._local_account_id:
            return "mail_provider_session_account_mismatch"
        if session.provider_account_id != self._client.session.provider_account_id:
            return "mail_provider_session_remote_account_mismatch"
        return ""

    def _move_patch(self, item: MailMoveRequest) -> dict[str, Any]:
        destinations: list[str] = []
        for mailbox_ref_id in item.destination_mailbox_ref_ids:
            resolved = self._mailboxes.resolve_mailbox(
                account_id=self._local_account_id,
                mailbox_ref_id=str(mailbox_ref_id),
            )
            if not resolved.ok or not resolved.value:
                raise ValueError(resolved.reason_code or "mail_move_destination_not_found")
            destinations.append(resolved.value)
        if not destinations:
            raise ValueError("mail_move_destination_required")
        return {"mailboxIds": {value: True for value in dict.fromkeys(destinations)}}


def _keyword_patch(item: MailKeywordChange) -> dict[str, Any]:
    add = {str(value) for value in item.add_keywords}
    remove = {str(value) for value in item.remove_keywords}
    if not add.isdisjoint(remove):
        raise ValueError("mail_keyword_change_conflict")
    return {
        **{f"keywords/{keyword}": True for keyword in sorted(add)},
        **{f"keywords/{keyword}": None for keyword in sorted(remove)},
    }


def _email_id(message_ref: Any) -> str:
    return str(message_ref.protocol_locator.get("email_id") or "")


def _mutation_report(
    arguments: Mapping[str, Any],
    *,
    requested: Mapping[str, str],
    action: str,
) -> MailProviderResult[MailMutationReport]:
    success_key = "destroyed" if action == "destroy" else "updated"
    failure_key = "notDestroyed" if action == "destroy" else "notUpdated"
    succeeded_raw = arguments.get(success_key) or {}
    failed_raw = arguments.get(failure_key) or {}
    succeeded = set(succeeded_raw if isinstance(succeeded_raw, list) else succeeded_raw.keys())
    failures = failed_raw if isinstance(failed_raw, Mapping) else {}
    items: list[MailMutationItem] = []
    for email_id, mail_ref_id in requested.items():
        if email_id in failures:
            failure = failures[email_id] if isinstance(failures[email_id], Mapping) else {}
            error_type = str(failure.get("type") or "unknown")
            items.append(
                _construct(
                    MailMutationItem,
                    {
                        "mail_ref_id": mail_ref_id,
                        "ok": False,
                        "reason_code": f"jmap_set_{error_type.lower()}",
                    },
                )
            )
        else:
            items.append(
                _construct(
                    MailMutationItem,
                    {
                        "mail_ref_id": mail_ref_id,
                        "ok": email_id in succeeded,
                        "reason_code": "ok" if email_id in succeeded else "jmap_set_result_missing",
                    },
                )
            )
    report = _report(
        tuple(items),
        str(arguments.get("newState") or ""),
        old_state=str(arguments.get("oldState") or ""),
    )
    ok = all(bool(getattr(item, "ok", False)) for item in items)
    return MailProviderResult(
        ok=ok,
        reason_code="ok" if ok else "jmap_mutation_partial_failure",
        value=report,
    )


def _report(
    items: Sequence[MailMutationItem],
    new_state: str,
    *,
    old_state: str = "",
) -> MailMutationReport:
    return MailMutationReport(
        items=tuple(items),
        old_state=str(old_state),
        new_state=str(new_state),
    )


def _construct(dto_type: type[Any], values: Mapping[str, Any]) -> Any:
    accepted = {field.name for field in fields(dto_type)}
    return dto_type(**{key: value for key, value in values.items() if key in accepted})


def _failure_from(result: MailProviderResult[Any]) -> MailProviderResult[Any]:
    return MailProviderResult(
        ok=False,
        reason_code=result.reason_code,
        retryable=result.retryable,
        retry_after_ms=result.retry_after_ms,
        details=result.details,
    )


def _ambiguous_failure(result: MailProviderResult[Any]) -> bool:
    return result.reason_code in {
        "jmap_connection_failed",
        "jmap_request_timeout",
        "jmap_http_server_error",
        "jmap_service_unavailable",
    }


__all__ = ["JmapMailMutationProvider"]
