from __future__ import annotations

from collections.abc import Mapping

from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_provider_ports import MailProviderBinding, MailProviderFactory, MailProviderResult


class MailProviderRouter:
    def __init__(self, factories: Mapping[str, MailProviderFactory] | None = None) -> None:
        self._factories: dict[str, MailProviderFactory] = {}
        for protocol, factory in dict(factories or {}).items():
            self.register(protocol, factory)

    def register(self, protocol: str, factory: MailProviderFactory) -> None:
        key = str(protocol).strip().lower()
        if key not in {"jmap", "imap"}:
            raise ValueError("mail_provider_protocol_invalid")
        self._factories[key] = factory

    def resolve(self, account: MailAccountV2) -> MailProviderResult[MailProviderBinding]:
        if not account.enabled:
            return MailProviderResult.failure("mail_account_disabled")
        protocol = account.effective_protocol
        if protocol is None:
            return MailProviderResult.failure("mail_protocol_unresolved")
        factory = self._factories.get(protocol)
        if factory is None:
            return MailProviderResult.failure("mail_provider_unavailable", details={"protocol": protocol})
        result = factory.create(account)
        if result.ok and result.value is not None and result.value.protocol != protocol:
            return MailProviderResult.failure("mail_provider_protocol_mismatch")
        return result
