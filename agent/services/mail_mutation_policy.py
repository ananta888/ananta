from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.mail_provider_ports import MailProviderResult


@dataclass(frozen=True, slots=True)
class MailMutationAuthorization:
    account_id: str
    operation: str
    intent_ref: str
    audit_ref: str
    confirmation_ref: str = ""


class MailMutationAuthorizer(Protocol):
    def authorize(self, request: MailMutationAuthorization) -> MailProviderResult[None]:
        ...


class MailMutationIntentVerifier(Protocol):
    """Hub intent client; no authorization policy is duplicated in the backend."""

    def verify(
        self,
        request: MailMutationAuthorization,
    ) -> MailProviderResult[None]:
        ...


class VerifiedIntentMutationAuthorizer:
    def __init__(self, *, verifier: MailMutationIntentVerifier) -> None:
        self._verifier = verifier

    def authorize(self, request: MailMutationAuthorization) -> MailProviderResult[None]:
        return self._verifier.verify(request)


class MailMutationAuditSink(Protocol):
    def record(
        self,
        *,
        account_id: str,
        operation: str,
        intent_ref: str,
        audit_ref: str,
        outcome: str,
        reason_code: str,
    ) -> None:
        ...


class MailMutationPolicy:
    def __init__(self, *, authorizer: MailMutationAuthorizer, audit_sink: MailMutationAuditSink) -> None:
        self._authorizer = authorizer
        self._audit_sink = audit_sink

    def authorize(
        self,
        *,
        account_id: str,
        operation: str,
        intent_ref: str,
        audit_ref: str,
        confirmation_ref: str = "",
    ) -> MailProviderResult[None]:
        if not account_id or not intent_ref or not audit_ref:
            result: MailProviderResult[None] = MailProviderResult(
                ok=False,
                reason_code="mail_mutation_context_invalid",
            )
        elif operation == "permanent_delete" and not confirmation_ref:
            result = MailProviderResult(ok=False, reason_code="mail_permanent_delete_confirmation_required")
        else:
            result = self._authorizer.authorize(
                MailMutationAuthorization(
                    account_id=account_id,
                    operation=operation,
                    intent_ref=intent_ref,
                    audit_ref=audit_ref,
                    confirmation_ref=confirmation_ref,
                )
            )
        self._audit_sink.record(
            account_id=account_id,
            operation=operation,
            intent_ref=intent_ref,
            audit_ref=audit_ref,
            outcome="authorized" if result.ok else "denied",
            reason_code=result.reason_code,
        )
        return result

    def record_outcome(
        self,
        *,
        account_id: str,
        operation: str,
        intent_ref: str,
        audit_ref: str,
        ok: bool,
        reason_code: str,
    ) -> None:
        self._audit_sink.record(
            account_id=account_id,
            operation=operation,
            intent_ref=intent_ref,
            audit_ref=audit_ref,
            outcome="succeeded" if ok else "failed",
            reason_code=reason_code,
        )

    def record_reconciliation(
        self,
        *,
        account_id: str,
        operation: str,
        intent_ref: str,
        audit_ref: str,
        reconciled: bool,
        reason_code: str,
    ) -> None:
        self._audit_sink.record(
            account_id=account_id,
            operation=operation,
            intent_ref=intent_ref,
            audit_ref=audit_ref,
            outcome="reconciled" if reconciled else "indeterminate",
            reason_code=reason_code,
        )


__all__ = [
    "MailMutationAuditSink",
    "MailMutationAuthorization",
    "MailMutationAuthorizer",
    "MailMutationIntentVerifier",
    "MailMutationPolicy",
    "VerifiedIntentMutationAuthorizer",
]
