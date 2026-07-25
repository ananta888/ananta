from __future__ import annotations

from agent.services.mail_mutation_policy import MailMutationPolicy
from agent.services.mail_provider_ports import MailProviderResult


class _Authorizer:
    def authorize(self, request):
        del request
        return MailProviderResult(ok=True, reason_code="authorized")


class _Audit:
    def __init__(self) -> None:
        self.events = []

    def record(self, **event) -> None:
        self.events.append(event)


def test_permanent_delete_requires_confirmation_before_authorizer() -> None:
    audit = _Audit()
    policy = MailMutationPolicy(authorizer=_Authorizer(), audit_sink=audit)
    result = policy.authorize(
        account_id="acc-1",
        operation="permanent_delete",
        intent_ref="intent-1",
        audit_ref="audit-1",
    )
    assert result.ok is False
    assert result.reason_code == "mail_permanent_delete_confirmation_required"
    assert audit.events[0]["outcome"] == "denied"
