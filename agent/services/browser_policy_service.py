from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.services.browser_navigation_target import BrowserNavigationTarget, matches_host, restricted_address
from agent.services.browser_task_contract import BrowserTaskContract


@dataclass(frozen=True)
class BrowserPolicyDecision:
    allow: bool
    reason_code: str


class BrowserPolicyService:
    def enforce_domain(self, *, url: str, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        blocked = self.enforce_blocked_hosts(url=url, contract=contract)
        if not blocked.allow:
            return blocked
        host = BrowserNavigationTarget.parse(url).hostname
        if not contract.allowed_domains:
            return BrowserPolicyDecision(False, "browser_policy_allowed_domains_missing")
        for pattern in contract.allowed_domains:
            if matches_host(host, pattern):
                return BrowserPolicyDecision(True, "ok")
        return BrowserPolicyDecision(False, "browser_policy_domain_not_allowed")

    def enforce_action_budget(self, *, action_count: int, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        if action_count > contract.max_actions:
            return BrowserPolicyDecision(False, "browser_policy_action_budget_exceeded")
        return BrowserPolicyDecision(True, "ok")

    def enforce_download_policy(
        self, *, download_url: str, output_path: str, contract: BrowserTaskContract
    ) -> BrowserPolicyDecision:
        if contract.download_policy == "deny":
            return BrowserPolicyDecision(False, "browser_policy_download_denied")

        blocked = self.enforce_blocked_hosts(url=download_url, contract=contract)
        if not blocked.allow:
            return blocked
        host = BrowserNavigationTarget.parse(download_url).hostname
        if contract.download_policy == "whitelist":
            if not any(matches_host(host, item, subdomains=False) for item in contract.download_allowlist):
                return BrowserPolicyDecision(False, "browser_policy_download_host_not_whitelisted")

        if contract.download_policy == "bounded_output_dir":
            if not contract.output_dir:
                return BrowserPolicyDecision(False, "browser_policy_output_dir_missing")
            out = Path(output_path).resolve()
            base = Path(contract.output_dir).resolve()
            try:
                within_base = out.is_relative_to(base)
            except Exception:
                within_base = str(out).startswith(f"{str(base).rstrip('/')}/")
            if not within_base:
                return BrowserPolicyDecision(False, "browser_policy_download_outside_output_dir")

        return BrowserPolicyDecision(True, "ok")

    def enforce_auth_usage(self, *, requested: bool, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        if requested and contract.auth_policy != "explicit_opt_in":
            return BrowserPolicyDecision(False, "browser_policy_auth_not_allowed")
        return BrowserPolicyDecision(True, "ok")

    def enforce_blocked_hosts(self, *, url: str, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        """Admit literal targets only; DNS/redirect egress needs worker enforcement."""
        try:
            target = BrowserNavigationTarget.parse(url)
        except ValueError:
            return BrowserPolicyDecision(False, "browser_policy_invalid_url")
        # A custom denylist must never remove the local/metadata baseline.
        for blocked in (*contract.blocked_domains, "localhost", "metadata.google.internal"):
            if matches_host(target.hostname, blocked):
                return BrowserPolicyDecision(False, "browser_policy_blocked_domain")
        if target.address is not None and restricted_address(target.address):
            return BrowserPolicyDecision(False, "browser_policy_private_ip_blocked")
        return BrowserPolicyDecision(True, "ok")

    def enforce_session_persistence(self, *, requested: bool, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        """Prüft ob Session-Persistierung erlaubt ist."""
        if requested and not contract.persist_session:
            return BrowserPolicyDecision(False, "browser_policy_session_persistence_not_allowed")
        return BrowserPolicyDecision(True, "ok")


_SERVICE = BrowserPolicyService()


def get_browser_policy_service() -> BrowserPolicyService:
    return _SERVICE
