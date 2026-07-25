from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_provider_ports import MailProviderResult
from agent.services.mail_legacy_mapper import MailDedupeSummary


@dataclass(frozen=True, slots=True)
class MailProtocolCutoverCommand:
    command_id: str
    account_id: str
    target_protocol: str
    operator_approval_ref: str = ""
    auto_policy_decision_ref: str = ""
    expected_resolved_protocol: str | None = None
    allow_unmatched: bool = False
    rollback: bool = False


@dataclass(frozen=True, slots=True)
class MailProtocolCutoverReport:
    command_id: str
    account: MailAccountV2
    previous_protocol: str | None
    target_protocol: str
    discovery_reason_code: str
    authentication_reason_code: str
    sync_reason_code: str
    dedupe: MailDedupeSummary
    rollback: bool


class MailCutoverDiscoveryPreflight(Protocol):
    def discover(self, account: MailAccountV2, target_protocol: str) -> MailProviderResult[Mapping[str, Any]]:
        ...


class MailCutoverAuthenticationPreflight(Protocol):
    def authenticate(
        self,
        account: MailAccountV2,
        target_protocol: str,
        discovery: Mapping[str, Any],
    ) -> MailProviderResult[None]:
        ...


class MailCutoverSyncPreflight(Protocol):
    def probe(self, account: MailAccountV2, target_protocol: str) -> MailProviderResult[None]:
        ...


class MailCutoverMatcher(Protocol):
    def match(self, account: MailAccountV2, target_protocol: str) -> MailProviderResult[MailDedupeSummary]:
        ...


class MailCutoverAuditSink(Protocol):
    def append(self, event: Mapping[str, Any]) -> None:
        ...


class MailProtocolCutoverService:
    def __init__(
        self,
        *,
        accounts: MailAccountService,
        audit_sink: MailCutoverAuditSink,
        discovery: MailCutoverDiscoveryPreflight,
        authentication: MailCutoverAuthenticationPreflight,
        sync_preflight: MailCutoverSyncPreflight,
        matcher: MailCutoverMatcher,
    ) -> None:
        self._accounts = accounts
        self._audit = audit_sink
        self._discovery = discovery
        self._authentication = authentication
        self._sync = sync_preflight
        self._matcher = matcher

    def execute(self, command: MailProtocolCutoverCommand) -> MailProviderResult[MailProtocolCutoverReport]:
        if command.target_protocol not in {"jmap", "imap"}:
            return MailProviderResult.failure("mail_cutover_protocol_invalid")
        account = self._accounts.get_account(command.account_id)
        if account is None:
            return MailProviderResult.failure("mail_account_not_found")
        if account.resolved_protocol != command.expected_resolved_protocol:
            return MailProviderResult.failure("mail_cutover_state_conflict")
        if not (command.operator_approval_ref or command.auto_policy_decision_ref):
            return MailProviderResult.failure("mail_cutover_approval_required")
        discovery = self._discovery.discover(account, command.target_protocol)
        if not discovery.ok or discovery.value is None:
            return MailProviderResult.failure(discovery.reason_code)
        authentication = self._authentication.authenticate(account, command.target_protocol, discovery.value)
        if not authentication.ok:
            return MailProviderResult.failure(authentication.reason_code)
        sync = self._sync.probe(account, command.target_protocol)
        if not sync.ok:
            return MailProviderResult.failure(sync.reason_code)
        match = self._matcher.match(account, command.target_protocol)
        if not match.ok or match.value is None:
            return MailProviderResult.failure(match.reason_code)
        if match.value.ambiguous:
            return MailProviderResult.failure("mail_cutover_dedupe_ambiguous")
        if match.value.unmatched and not command.allow_unmatched:
            return MailProviderResult.failure("mail_cutover_unmatched_requires_approval")
        updated = MailAccountV2(
            account_id=account.account_id,
            display_name=account.display_name,
            requested_protocol=account.requested_protocol,
            resolved_protocol=command.target_protocol,
            username_ref=account.username_ref,
            credential_ref=account.credential_ref,
            sync_policy=account.sync_policy,
            enabled=account.enabled,
            provider_config=account.provider_config,
        )
        self._accounts.upsert_account(updated)
        self._audit.append(
            {
                "event": "mail_protocol_cutover",
                "command_id": command.command_id,
                "account_id": command.account_id,
                "from_protocol": account.resolved_protocol,
                "to_protocol": command.target_protocol,
                "approval": "operator" if command.operator_approval_ref else "auto_policy",
                "approval_ref": command.operator_approval_ref or command.auto_policy_decision_ref,
                "rollback": command.rollback,
                "matched": match.value.matched,
                "ambiguous": match.value.ambiguous,
                "unmatched": match.value.unmatched,
            }
        )
        return MailProviderResult.success(
            MailProtocolCutoverReport(
                command_id=command.command_id,
                account=updated,
                previous_protocol=account.resolved_protocol,
                target_protocol=command.target_protocol,
                discovery_reason_code=discovery.reason_code,
                authentication_reason_code=authentication.reason_code,
                sync_reason_code=sync.reason_code,
                dedupe=match.value,
                rollback=command.rollback,
            ),
            reason_code="mail_protocol_rollback_complete" if command.rollback else "mail_protocol_cutover_complete",
        )
