from __future__ import annotations

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_protocol_cutover_service import MailProtocolCutoverCommand, MailProtocolCutoverService
from agent.services.mail_legacy_mapper import MailDedupeSummary
from agent.services.mail_provider_ports import MailProviderBinding, MailProviderResult
from agent.services.mail_provider_router import MailProviderRouter


class _Factory:
    def __init__(self, binding):
        self.binding = binding

    def create(self, account):
        return MailProviderResult.success(self.binding)


class _Audit:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(dict(event))


class _Discovery:
    def discover(self, account, target_protocol):
        return MailProviderResult.success({"provider": target_protocol}, reason_code="discovery_ok")


class _Authentication:
    def authenticate(self, account, target_protocol, discovery):
        return MailProviderResult.success(reason_code="authentication_ok")


class _Sync:
    def probe(self, account, target_protocol):
        return MailProviderResult.success(reason_code="sync_ok")


class _Matcher:
    def match(self, account, target_protocol):
        return MailProviderResult.success(MailDedupeSummary(1, 0, 0))


class _ConfiguredMatcher:
    def __init__(self, summary):
        self.summary = summary

    def match(self, account, target_protocol):
        return MailProviderResult.success(self.summary)


class _FailingDiscovery:
    def discover(self, account, target_protocol):
        return MailProviderResult.failure("jmap_discovery_failed")


def _account() -> MailAccountV2:
    return MailAccountV2(
        account_id="a",
        display_name="A",
        requested_protocol="auto",
        resolved_protocol=None,
        username_ref="user://a",
        credential_ref="secret://a",
        sync_policy="manual",
        enabled=True,
        provider_config={},
    )


def test_auto_router_requires_explicit_hub_resolution() -> None:
    account = _account()
    assert MailProviderRouter().resolve(account).reason_code == "mail_protocol_unresolved"


def test_cutover_requires_checks_and_approval_then_router_uses_resolution(tmp_path) -> None:
    accounts = MailAccountService(store_path=tmp_path / "accounts.json")
    accounts.create_account(_account())
    audit = _Audit()
    service = MailProtocolCutoverService(
        accounts=accounts,
        audit_sink=audit,
        discovery=_Discovery(),
        authentication=_Authentication(),
        sync_preflight=_Sync(),
        matcher=_Matcher(),
    )
    denied = service.execute(
        MailProtocolCutoverCommand("cmd", "a", "jmap", expected_resolved_protocol=None)
    )
    assert denied.reason_code == "mail_cutover_approval_required"
    allowed = service.execute(
        MailProtocolCutoverCommand(
            "cmd",
            "a",
            "jmap",
            operator_approval_ref="approval-1",
            expected_resolved_protocol=None,
        )
    )
    assert allowed.ok is True
    binding = MailProviderBinding("jmap", object(), object(), object())  # type: ignore[arg-type]
    router = MailProviderRouter({"jmap": _Factory(binding)})
    assert router.resolve(allowed.value.account).value is binding
    assert audit.events[0]["to_protocol"] == "jmap"


def test_cutover_fails_closed_for_preflight_cas_and_dedupe_and_reports_rollback(tmp_path) -> None:
    accounts = MailAccountService(store_path=tmp_path / "accounts.json")
    accounts.create_account(_account())
    audit = _Audit()

    def service(discovery=_Discovery(), matcher=_Matcher()):
        return MailProtocolCutoverService(
            accounts=accounts,
            audit_sink=audit,
            discovery=discovery,
            authentication=_Authentication(),
            sync_preflight=_Sync(),
            matcher=matcher,
        )

    approval = {
        "command_id": "cmd",
        "account_id": "a",
        "target_protocol": "jmap",
        "operator_approval_ref": "approval",
        "expected_resolved_protocol": None,
    }
    assert service(discovery=_FailingDiscovery()).execute(
        MailProtocolCutoverCommand(**approval)
    ).reason_code == "jmap_discovery_failed"
    assert service().execute(
        MailProtocolCutoverCommand(**(approval | {"expected_resolved_protocol": "imap"}))
    ).reason_code == "mail_cutover_state_conflict"
    assert service(matcher=_ConfiguredMatcher(MailDedupeSummary(0, 1, 0))).execute(
        MailProtocolCutoverCommand(**approval)
    ).reason_code == "mail_cutover_dedupe_ambiguous"
    unmatched_service = service(matcher=_ConfiguredMatcher(MailDedupeSummary(0, 0, 1)))
    assert unmatched_service.execute(
        MailProtocolCutoverCommand(**approval)
    ).reason_code == "mail_cutover_unmatched_requires_approval"
    cutover = unmatched_service.execute(
        MailProtocolCutoverCommand(**approval, allow_unmatched=True)
    )
    assert cutover.ok is True
    rollback = service().execute(
        MailProtocolCutoverCommand(
            command_id="rollback",
            account_id="a",
            target_protocol="imap",
            operator_approval_ref="approval-rollback",
            expected_resolved_protocol="jmap",
            rollback=True,
        )
    )
    assert rollback.reason_code == "mail_protocol_rollback_complete"
    assert rollback.value.rollback is True
    assert rollback.value.previous_protocol == "jmap"
    assert rollback.value.account.resolved_protocol == "imap"
