from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent.services.jmap_client_service import JmapClient
from agent.services.mail_provider_ports import (
    MailMutationItem,
    MailMutationReport,
    MailProviderResult,
)


@dataclass(frozen=True, slots=True)
class JmapMutationReconciliationTarget:
    email_id: str
    mail_ref_id: str
    expected_patch: Mapping[str, Any] | None = None
    destroyed: bool = False


class JmapMutationReconciler:
    """Determines ambiguous Email/set outcomes through a read, never a retry."""

    def __init__(self, *, client: JmapClient) -> None:
        self._client = client

    def reconcile(
        self,
        targets: Sequence[JmapMutationReconciliationTarget],
        *,
        original_reason_code: str,
    ) -> MailProviderResult[MailMutationReport]:
        normalized = tuple(targets)
        if not normalized:
            return MailProviderResult(
                ok=True,
                reason_code="jmap_mutation_reconciled",
                value=MailMutationReport(items=()),
            )
        response = self._client.call(
            "Email/get",
            {
                "accountId": self._client.session.provider_account_id,
                "ids": [target.email_id for target in normalized],
                "properties": ["id", "keywords", "mailboxIds"],
            },
        )
        if not response.ok or response.value is None:
            return _unknown_report(normalized, original_reason_code)
        rows = response.value.arguments.get("list")
        not_found = response.value.arguments.get("notFound") or []
        if (
            not isinstance(rows, list)
            or not isinstance(not_found, list)
            or len(rows) > len(normalized)
            or any(not isinstance(row, Mapping) for row in rows)
            or any(not isinstance(value, str) for value in not_found)
        ):
            return _unknown_report(normalized, original_reason_code)
        by_id = {
            str(row.get("id")): dict(row)
            for row in rows
            if isinstance(row.get("id"), str)
        }
        missing = set(not_found)
        items: list[MailMutationItem] = []
        for target in normalized:
            observed = (
                target.email_id in missing
                if target.destroyed
                else _patch_matches(by_id.get(target.email_id), target.expected_patch or {})
            )
            items.append(
                MailMutationItem(
                    mail_ref_id=target.mail_ref_id,
                    ok=observed,
                    reason_code=(
                        "jmap_mutation_reconciled"
                        if observed
                        else "jmap_mutation_outcome_unknown"
                    ),
                )
            )
        all_reconciled = all(item.ok for item in items)
        return MailProviderResult(
            ok=all_reconciled,
            reason_code=(
                "jmap_mutation_reconciled"
                if all_reconciled
                else "jmap_mutation_outcome_unknown"
            ),
            value=MailMutationReport(
                items=tuple(items),
                new_state=str(response.value.arguments.get("state") or ""),
            ),
            retryable=False,
            details={"original_reason_code": original_reason_code},
        )


def _patch_matches(row: Mapping[str, Any] | None, patch: Mapping[str, Any]) -> bool:
    if row is None:
        return False
    for path, expected in patch.items():
        if path == "mailboxIds":
            actual = row.get("mailboxIds")
            if not isinstance(actual, Mapping) or {
                str(key) for key, value in actual.items() if bool(value)
            } != {
                str(key) for key, value in dict(expected or {}).items() if bool(value)
            }:
                return False
        elif path.startswith("keywords/"):
            keyword = path.split("/", 1)[1]
            actual_keywords = row.get("keywords")
            if not isinstance(actual_keywords, Mapping):
                return False
            if (keyword in actual_keywords and bool(actual_keywords[keyword])) != (expected is True):
                return False
        else:
            return False
    return True


def _unknown_report(
    targets: Sequence[JmapMutationReconciliationTarget],
    original_reason_code: str,
) -> MailProviderResult[MailMutationReport]:
    return MailProviderResult(
        ok=False,
        reason_code="jmap_mutation_outcome_unknown",
        value=MailMutationReport(
            items=tuple(
                MailMutationItem(
                    mail_ref_id=target.mail_ref_id,
                    ok=False,
                    reason_code="jmap_mutation_outcome_unknown",
                )
                for target in targets
            )
        ),
        retryable=False,
        details={"original_reason_code": original_reason_code},
    )


__all__ = [
    "JmapMutationReconciler",
    "JmapMutationReconciliationTarget",
]
